"""Stem-aware analysis and discovery layered on top of the existing SonicDistance brain.

build_xray's `threads` field (Resonance Threads) is the deliberate reason Stem Studio exists
alongside the brain rather than as a standalone toy: per-instrument-layer feature vectors are
something no whole-track recommender has, and the actual "wow" is surfacing a real connection
whole-track clustering can't — a song with nothing in common genre- or cluster-wise that still
shares this one isolated layer's texture. Rendered as glowing cross-cluster lines in
TasteBrain.tsx, not just printed as text."""
from __future__ import annotations

import re
import statistics
from typing import Iterable

import numpy as np

from app.models import Song
from app.services.brain import sonic_distance

# An isolated stem's feature vector sits systematically further from any *whole song's* vector
# than two whole songs sit from each other — a drum track's MFCC/energy profile simply isn't
# shaped like a full mix's, whatever the music. Measured on a real 175-song map: whole-song
# pairs have a median SonicDistance of 0.35 (94% below 0.52), while stem-to-song distances run
# 0.70-1.84 with a median near 1.1. Scoring stems on the whole-song scale therefore deflated
# every match into the 30s-40s and put the thread cutoff permanently out of reach — the feature
# returned zero threads for every layer. The ranking was never the problem (each layer's nearest
# neighbours land in a coherent, and different, cluster) — only the constants were.
#
# The fix uses two scales deliberately, because the panel asks two different questions:
#   "how well does my map match this layer?"  -> _absolute_match, a fixed catalog-wide yardstick
#   "which songs are surprisingly close here?" -> THREAD_Z, relative to this layer's own spread
# Selecting threads is inherently a relative question (an outlier *for this layer*), but every
# percentage the product ever shows comes from the absolute scale, so one number means one thing
# everywhere. Neither touches sonic_distance/stem_distance/mix_distance, so recommendations
# (`/discover`, brain.recommend_for_user) are unaffected by any of this.
# --- Absolute scale: what a match *is*, independent of whose map is asking ---------------
# Anchored on the real pooled stem->song distance distribution, measured over 93,424 pairs
# (every stem of a separated track against all 23,356 catalog songs with feature vectors):
#
#     p0.1  0.685   p1  0.739   p5  0.860   p50  1.155   p95  1.436   p99  1.696
#
# So an exceptional pairing lives near 0.66-0.70 and an unrelated one near 1.45, and a match
# percentage is where a distance falls between those two. This is what makes `match` and
# `overall_match` mean the same thing for every track, every layer and every user's map — the
# claim is "how close is this, really", not "how close relative to your other songs", which is
# why it must NOT be scored against the asking map's own spread: on any map the nearest song is
# an outlier by construction, so a relative headline reads ~90 for everything and tells nobody
# anything. /compare depends on this too — it scores two maps against each other as
# `100 - abs(mine - theirs)`, which is only meaningful while both sides are on an absolute scale.
_STEM_MATCH_NEAR = 0.66
_STEM_MATCH_FAR = 1.45
# Known residual, deliberately not "fixed" by fitting: the pooled anchors sit between the
# per-layer distributions (isolated vocals run further from any whole song, p50 1.267, than a
# full instrumental bed does, p50 1.068), so vocals score a little low and 'other' a little high
# against a common yardstick. Calibrating four per-layer anchors from the single separated track
# available here would be overfitting a real bias into a fake precision; it wants more tracks.


def _absolute_match(distance: float) -> int:
    """Stem->song SonicDistance -> a 0-100 readout on the fixed scale documented above."""
    span = _STEM_MATCH_FAR - _STEM_MATCH_NEAR
    return round(100 * min(1.0, max(0.0, (_STEM_MATCH_FAR - distance) / span)))


# --- Relative scale: which songs are *unusually* close for one particular layer -----------
THREAD_Z = -2.0
"""A thread must be this many standard deviations below its own layer's mean distance. Chosen
against real data rather than picked: at -2.0 a 175-song map yields 7/5/2 threads for bass/
drums/vocals and *zero* for the 'other' layer, whose nearest neighbours aren't actually
separated from the pack. That last case is the point — a layer with nothing standout must be
able to come back empty, which a plain percentile cutoff could never do."""

MIN_THREAD_SAMPLE = 8
"""Below this many analyzed songs the mean/spread aren't a distribution worth calling one, so
threads are withheld rather than invented from three data points. The headline `match` has no
such requirement — it's an absolute measurement, so it's just as valid on a three-song map."""


STEM_COPY = {
    "vocals": ("Vocal character", "voice and timbral texture"),
    "drums": ("Rhythmic instinct", "pulse, movement and percussion"),
    "bass": ("Low-end affinity", "bass movement and weight"),
    "guitar": ("Guitar texture", "harmonic and instrumental texture"),
    "piano": ("Harmonic color", "piano tone and harmony"),
    "other": ("Production atmosphere", "instrumentation and sonic space"),
}


def safe_mix_weights(analysis: dict, requested: dict[str, float]) -> dict[str, float]:
    available = set((analysis.get("stems") or {}).keys())
    values = {
        name: min(1.0, max(0.0, float(value)))
        for name, value in requested.items()
        if name in available
    }
    if not values or sum(values.values()) <= 0:
        values = {name: 1.0 for name in available}
    total = sum(values.values()) or 1.0
    return {name: value / total for name, value in values.items() if value > 0}


def interpret_mix_prompt(analysis: dict, prompt: str, current: dict[str, float]) -> tuple[dict[str, float], str]:
    """Deterministic, auditable stem-language controls; unknown prose never invents weights."""
    available = set((analysis.get("stems") or {}).keys())
    weights = {name: min(1.0, max(0.0, float(current.get(name, 1.0)))) for name in available}
    text = prompt.casefold()
    aliases = {
        "vocals": ("vocal", "voice", "singer", "acapella"),
        "drums": ("drum", "beat", "percussion", "rhythm"),
        "bass": ("bass", "low end", "low-end", "groove"),
        "guitar": ("guitar",), "piano": ("piano", "keys"),
        "other": ("instrumental", "production", "atmosphere", "synth"),
    }
    touched = []
    for name in available:
        words = aliases.get(name, (name,))
        if not any(word in text for word in words):
            continue
        touched.append(name)
        alternatives = "|".join(re.escape(word) for word in words)
        remove = bool(re.search(rf"(?:without|remove|mute|no)\s+(?:the\s+)?(?:{alternatives})", text))
        reduce = bool(re.search(rf"(?:less|lower|quieter|subtle)\s+(?:the\s+)?(?:{alternatives})", text))
        weights[name] = 0.0 if remove else 0.35 if reduce else 1.0
    if "acapella" in text and "vocals" in available:
        weights = {name: (1.0 if name == "vocals" else 0.0) for name in available}
        touched = ["vocals"]
    elif "instrumental" in text and "vocals" in available and any(token in text for token in ("only", "version", "without")):
        weights["vocals"] = 0.0
        touched.append("vocals")
    if not touched:
        raise ValueError("Mention a layer such as vocals, drums, bass, piano, guitar, or instrumentation")
    summary = ", ".join(f"{name} {round(weights[name] * 100)}%" for name in sorted(set(touched)))
    return weights, f"Applied: {summary}"


def stem_distance(stem: dict, song: Song) -> float:
    vector = stem.get("feature_vector")
    if not vector or song.feature_vector is None:
        return 2.0
    return sonic_distance(
        np.asarray(vector, dtype=float), None,
        np.asarray(song.feature_vector, dtype=float), None,
    )


def mix_distance(analysis: dict, weights: dict[str, float], song: Song) -> float:
    stems = analysis.get("stems") or {}
    return sum(
        weight * stem_distance(stems[name], song)
        for name, weight in weights.items()
        if name in stems
    )


def build_xray(
    analysis: dict,
    taste_songs: Iterable[Song],
    *,
    cluster_by_id: dict[str, int | None] | None = None,
    source_cluster: int | None = None,
    thread_limit: int = 4,
) -> dict:
    songs = [song for song in taste_songs if song.feature_vector is not None]
    cluster_by_id = cluster_by_id or {}
    rows = []
    for name, stem in (analysis.get("stems") or {}).items():
        distances = sorted(
            ((stem_distance(stem, song), song) for song in songs),
            key=lambda row: row[0],
        )
        best_distance, best_song = distances[0] if distances else (None, None)
        # Headline match: the absolute scale, so it stays comparable across tracks and users.
        score = _absolute_match(best_distance) if best_distance is not None else None
        # Thread *selection* only: this layer's own spread across the map — see THREAD_Z.
        values = [distance for distance, _song in distances]
        mean = statistics.fmean(values) if values else 0.0
        # Population, not sample, stdev: `values` is the entire map, not a draw from it.
        spread = statistics.pstdev(values) if len(values) > 1 else 0.0
        scorable = spread > 0 and len(values) >= MIN_THREAD_SAMPLE
        label, dimension = STEM_COPY.get(name, (name.title(), "sonic texture"))
        # Resonance threads: real cross-cluster matches on this one isolated layer — songs
        # whose *overall* SonicDistance puts them nowhere near the song being separated (a
        # different cluster entirely) but whose feature vector still lands close to this one
        # stem alone. This is the actual point of feeding Stem Studio into the brain: surfacing
        # a connection the whole-track clustering would never show, not restating it — so a
        # song sharing the source's own cluster is deliberately excluded here even if its
        # stem-distance is small, and `distances` is already sorted ascending, so the first
        # candidate above THREAD_Z means everything after it is weaker still.
        threads = []
        for distance, song in distances if scorable else []:
            if song is best_song:
                continue
            if source_cluster is not None and cluster_by_id.get(str(song.id)) == source_cluster:
                continue
            if (distance - mean) / spread > THREAD_Z:
                break
            threads.append({
                "song_id": str(song.id),
                "title": song.title,
                "artist": song.artist,
                # Selected relatively, reported absolutely — same yardstick as every other
                # percentage in the panel.
                "match": _absolute_match(distance),
            })
            if len(threads) >= thread_limit:
                break
        rows.append({
            "name": name,
            "label": label,
            "dimension": dimension,
            "match": score,
            "prominence": round(100 * float(stem.get("metrics", {}).get("prominence", 0.0))),
            "best_match": (
                {"id": str(best_song.id), "title": best_song.title, "artist": best_song.artist}
                if best_song
                else None
            ),
            "threads": threads,
            "metrics": stem.get("metrics", {}),
        })
    scored = [row for row in rows if row["match"] is not None]
    overall = (
        round(
            sum(row["match"] * max(1, row["prominence"]) for row in scored)
            / sum(max(1, row["prominence"]) for row in scored)
        )
        if scored else None
    )
    dominant = max(rows, key=lambda row: row["prominence"], default=None)
    return {
        "overall_match": overall,
        "dominant": dominant["label"] if dominant else None,
        "stems": rows,
        "ready": bool(scored),
    }
