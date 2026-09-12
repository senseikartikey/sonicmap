"""Runs real audio feature extraction (Essentia) against a preview clip and produces:

  1. The fixed-dimension low-level vector stored on Song.feature_vector (unchanged from the
     original pipeline: rhythm, loudness, timbral texture, key/mode).
  2. A genre/style-aware embedding (Song.genre_vector) from Essentia's Discogs-EffNet model —
     trained on 2M+ real Discogs-tagged tracks (CC BY-NC-SA 4.0, non-commercial), this is what
     gives the recommender an actual sense of genre neighborhood instead of only timbral
     texture, which is what let acoustically-similar-but-genre-unrelated tracks (e.g. a mellow
     Radiohead cut next to a mellow Drake cut) end up recommended to each other.
  3. Human-readable genre/styles labels for display, from the same model's 400-class Discogs
     genre/style classifier head.

Only the preview clip is ever touched, and only derived numeric features + text labels are
persisted — the audio itself is never stored or re-served. Essentia has no Windows wheels,
which is why this whole backend runs in the Linux container defined by backend/Dockerfile
rather than natively on Windows.

feature_vector layout (feature_vector_dim = 32):
  [0]      bpm (normalized /200)
  [1]      danceability
  [2]      loudness/energy (normalized)
  [3:16]   MFCC mean (13 coeffs)
  [16:29]  MFCC std (13 coeffs)
  [29]     key pitch-class sin
  [30]     key pitch-class cos
  [31]     mode (1.0 major / 0.0 minor)

genre_vector: the 1280-dim Discogs-EffNet embedding, mean-pooled across the track's frames.
"""

import json
import tempfile
import threading
from dataclasses import dataclass
from math import cos, pi, sin
from typing import TYPE_CHECKING

import httpx
import numpy as np

if TYPE_CHECKING:
    from app.models import Song

PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

MODEL_DIR = "/app/models"
EFFNET_EMBEDDING_MODEL = f"{MODEL_DIR}/discogs-effnet-bs64-1.pb"
GENRE_CLASSIFIER_MODEL = f"{MODEL_DIR}/genre_discogs400-discogs-effnet-1.pb"
GENRE_CLASSIFIER_METADATA = f"{MODEL_DIR}/genre_discogs400-discogs-effnet-1.json"

# How many of the top-scoring Discogs styles to keep for the human-readable `styles` field.
TOP_STYLES = 3


@dataclass
class ExtractedFeatures:
    vector: list[float]
    bpm: float
    key: str
    energy: float
    danceability: float
    genre_vector: list[float]
    genre: str | None
    styles: str | None


async def _download_preview(preview_url: str) -> bytes:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(preview_url)
        resp.raise_for_status()
        return resp.content


def _extract_low_level(es, audio: np.ndarray) -> tuple[list[float], float, str, float, float]:
    bpm, _, _, _, _ = es.RhythmExtractor2013(method="multifeature")(audio)

    key, scale, _strength = es.KeyExtractor()(audio)
    pitch_class = PITCH_CLASSES.index(key) if key in PITCH_CLASSES else 0
    angle = 2 * pi * pitch_class / 12
    mode = 1.0 if scale == "major" else 0.0

    danceability, _ = es.Danceability()(audio)

    # RMS in dBFS, mapped from a typical commercial-master range (-40..0 dB) to 0..1.
    # (Essentia's raw Loudness() output isn't dB-scaled and saturated to 1.0 for nearly
    # every track when used directly here - this replaced it.)
    rms = float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0
    rms_db = 20 * np.log10(rms + 1e-9)
    energy = float(np.clip((rms_db + 40.0) / 40.0, 0.0, 1.0))

    frame_gen = es.FrameGenerator(audio, frameSize=2048, hopSize=1024, startFromZero=True)
    windowing = es.Windowing(type="hann")
    spectrum = es.Spectrum()
    mfcc = es.MFCC(numberCoefficients=13)

    mfcc_frames = []
    for frame in frame_gen:
        spec = spectrum(windowing(frame))
        _, coeffs = mfcc(spec)
        mfcc_frames.append(coeffs)

    if mfcc_frames:
        mfcc_arr = np.array(mfcc_frames)
        mfcc_mean = mfcc_arr.mean(axis=0)
        mfcc_std = mfcc_arr.std(axis=0)
    else:
        mfcc_mean = np.zeros(13)
        mfcc_std = np.zeros(13)

    vector = (
        [float(bpm) / 200.0, float(danceability), energy]
        + mfcc_mean.tolist()
        + mfcc_std.tolist()
        + [sin(angle), cos(angle), mode]
    )
    return vector, float(bpm), f"{key} {scale}", energy, float(danceability)


_genre_classes: list[str] | None = None


def _genre_class_names() -> list[str]:
    global _genre_classes
    if _genre_classes is None:
        with open(GENRE_CLASSIFIER_METADATA) as f:
            _genre_classes = json.load(f)["classes"]
    return _genre_classes


def _coarse_genre_from_predictions(predictions: np.ndarray, classes: list[str]) -> str | None:
    """Derive the parent from the strongest style evidence without taxonomy-size bias.

    Discogs400 is a multi-label style classifier, not a mutually-exclusive hierarchy. Its
    parents have radically different numbers of children (Electronic=106, Rock=91, Pop=16),
    so summing all 400 sigmoid outputs systematically promoted the largest parents even when
    none of their styles was a strong prediction. Vote among the same top styles shown to the
    user instead; ties use their combined confidence and then classifier rank.
    """
    if len(predictions) == 0 or not classes:
        return None
    ranked = sorted(
        zip(predictions, classes, strict=True), key=lambda item: float(item[0]), reverse=True
    )[:TOP_STYLES]
    counts: dict[str, int] = {}
    confidence: dict[str, float] = {}
    first_rank: dict[str, int] = {}
    for rank, (probability, label) in enumerate(ranked):
        genre = label.split("---", 1)[0]
        counts[genre] = counts.get(genre, 0) + 1
        confidence[genre] = confidence.get(genre, 0.0) + float(probability)
        first_rank.setdefault(genre, rank)
    return max(counts, key=lambda genre: (counts[genre], confidence[genre], -first_rank[genre]))


_genre_models: tuple[object, object] | None = None
# Reentrant because _extract_genre acquires this itself around the actual inference calls,
# then calls _get_genre_models() which also acquires it for the lazy-init — a plain Lock would
# deadlock on that nested acquisition from the same thread.
_genre_model_lock = threading.RLock()


def _get_genre_models(es) -> tuple[object, object]:
    """Builds Essentia's TensorflowPredictEffnetDiscogs + TensorflowPredict2D models once per
    process and reuses them for every song, instead of reconstructing both from their .pb
    graph files on disk on every single extraction call — confirmed via container logs
    showing "Successfully loaded graph file" repeated for every song, which was almost
    certainly the dominant cost of extraction (graph parsing + TF session setup dwarfs the
    actual inference time for one ~30s audio clip). This is what made bulk imports (see
    ingest.py's BULK_EXTRACTION_CONCURRENCY) so slow — the concurrency fix alone didn't help
    much when the bottleneck was redundant model loading, not lack of parallelism."""
    global _genre_models
    with _genre_model_lock:
        if _genre_models is None:
            embedding_model = es.TensorflowPredictEffnetDiscogs(
                graphFilename=EFFNET_EMBEDDING_MODEL, output="PartitionedCall:1"
            )
            classifier = es.TensorflowPredict2D(
                graphFilename=GENRE_CLASSIFIER_MODEL,
                input="serving_default_model_Placeholder",
                output="PartitionedCall:0",
            )
            _genre_models = (embedding_model, classifier)
        return _genre_models


def _extract_genre(es, audio_16k: np.ndarray) -> tuple[list[float], str | None, str | None]:
    # Essentia's TensorFlow wrapper isn't documented as safe for concurrent inference from
    # multiple threads against the same graph/session — now that extraction genuinely runs
    # concurrently (BULK_EXTRACTION_CONCURRENCY), serialize just the inference calls
    # themselves; audio download, decoding, and low-level feature extraction still run fully
    # in parallel across threads.
    with _genre_model_lock:
        embedding_model, classifier = _get_genre_models(es)
        # One embedding row per ~patch of audio (EffNet processes the track in chunks, not a
        # single pass) — mean-pool across time for one fixed-length vector summarizing the
        # track.
        frame_embeddings = embedding_model(audio_16k)
        genre_vector = np.mean(frame_embeddings, axis=0)
        frame_predictions = classifier(frame_embeddings)

    mean_predictions = np.mean(frame_predictions, axis=0)
    classes = _genre_class_names()
    top_indices = np.argsort(mean_predictions)[::-1][:TOP_STYLES]
    top_labels = [classes[i] for i in top_indices]

    # Styles remain the strongest individual labels. The parent genre is aggregated across
    # all 400 probabilities so one noisy style cannot dictate the coarse bucket.
    genre = _coarse_genre_from_predictions(mean_predictions, classes)
    styles = ", ".join(top_labels) if top_labels else None

    return genre_vector.tolist(), genre, styles


def _extract_sync(audio_bytes: bytes) -> ExtractedFeatures:
    # Imported lazily: essentia is only installable on Linux, and importing it eagerly would
    # break any tooling that imports this module outside the backend container (e.g. tests
    # run on a Windows host without Docker).
    import essentia.standard as es

    with tempfile.NamedTemporaryFile(suffix=".m4a") as tmp:
        tmp.write(audio_bytes)
        tmp.flush()
        audio = es.MonoLoader(filename=tmp.name)()
        # Discogs-EffNet expects 16kHz input, separate from the native-rate load above used
        # for the low-level rhythm/spectral features.
        audio_16k = es.MonoLoader(filename=tmp.name, sampleRate=16000, resampleQuality=4)()

    vector, bpm, key, energy, danceability = _extract_low_level(es, audio)
    genre_vector, genre, styles = _extract_genre(es, audio_16k)

    return ExtractedFeatures(
        vector=vector,
        bpm=bpm,
        key=key,
        energy=energy,
        danceability=danceability,
        genre_vector=genre_vector,
        genre=genre,
        styles=styles,
    )


async def extract_features(preview_url: str) -> ExtractedFeatures:
    audio_bytes = await _download_preview(preview_url)
    # Essentia's C-extension calls are blocking/CPU-bound; run them off the event loop.
    import anyio

    return await anyio.to_thread.run_sync(_extract_sync, audio_bytes)


def apply_extracted_features(song: "Song", features: ExtractedFeatures) -> None:
    """Shared by every path that persists an extraction result onto a Song row (single-song
    ingest, background bulk ingest, catalog expansion) so they can't drift out of sync."""
    vector = np.asarray(features.vector, dtype=float)
    genre_vector = np.asarray(features.genre_vector, dtype=float)
    scalars = np.asarray([features.bpm, features.energy, features.danceability], dtype=float)
    if vector.shape != (32,) or genre_vector.shape != (1280,):
        raise ValueError("Extractor returned an invalid vector dimension")
    if not np.isfinite(vector).all() or not np.isfinite(genre_vector).all() or not np.isfinite(scalars).all():
        raise ValueError("Extractor returned NaN or infinite features")
    # Essentia Danceability is *not* a probability: its documented normal range is roughly
    # 0..3. Keep a generous finite sanity ceiling for corrupt output without rejecting valid
    # values such as 1.49 (the regression that exposed this incorrect 0..1 assumption).
    if features.bpm <= 0 or not 0.0 <= features.energy <= 1.0 or not 0.0 <= features.danceability <= 4.0:
        raise ValueError("Extractor returned out-of-range audio features")
    song.feature_vector = features.vector
    song.bpm = features.bpm
    song.key = features.key
    song.energy = features.energy
    song.danceability = features.danceability
    song.genre_vector = features.genre_vector
    song.genre = features.genre
    song.styles = features.styles
    song.extraction_status = "extracted"
    from app.services.catalog_vectors import apply_retrieval_vector

    apply_retrieval_vector(song)
