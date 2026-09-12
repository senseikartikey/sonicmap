"""Beat and downbeat grids: where the bars actually are.

Why this exists, precisely. The mix player used to find beat *phase* in the browser with a comb
filter over an onset envelope, using the tempo already on the song row. Measured against
Essentia's own beat tracker that landed within 12% of a beat on 5 of 10 real tracks, and — more
damagingly — it only ever found *beats*, never *bars*. Two tracks aligned on a beat but not on a
bar are as likely as not a half-bar apart, so one track's snare lands on the other's kick. That
is what "no rhythm, sounds random" is: not a bad crossfade, a bar-phase error.

So grids are computed once, server-side, by a model that predicts beats and downbeats jointly
(Beat This!, ISMIR 2024, MIT), and stored on the song. The browser stops guessing and just reads
them. Measured on this catalog's 30-second previews: ~4s per clip on CPU, beats-per-bar comes
back 3.9-4.1 (i.e. it really is finding 4/4 bars), and the tempo implied by the grid agrees with
Essentia's stored BPM to within about 2%.

The pure functions at the bottom take a grid and answer the questions a DJ actually asks — where
does a phrase start, which downbeat should a blend begin on — and are unit-tested without audio
or a model.
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import httpx

logger = logging.getLogger(__name__)

GRID_SOURCE = "beat_this:final0"
"""Stamped into every grid. If the model is ever swapped, this is what tells you which rows were
produced by which analyser without having to guess from the numbers."""

BEATS_PER_BAR = 4
PHRASE_BARS = 4
"""A phrase for cueing purposes. DJ practice talks in 8/16/32-bar phrases, but those are built
from 4-bar groups, and a 30-second preview only holds ~15 bars — so 4 is the largest unit that
actually fits inside the audio available here. Blends still resolve on a multiple of it."""

_SAMPLE_RATE = 22050


class BeatGridError(RuntimeError):
    """Analysis could not produce a usable grid."""


def _decode_to_wav(source: Path, destination: Path) -> None:
    """beat_this reads through soundfile, which cannot open the AAC/m4a that both providers
    serve, so everything goes through ffmpeg first. Mono at 22.05 kHz is what the model wants
    anyway, so this is a conversion rather than a loss."""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(source),
         "-ar", str(_SAMPLE_RATE), "-ac", "1", str(destination)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not destination.exists():
        raise BeatGridError(f"could not decode preview audio: {result.stderr.strip()[:200]}")


_model = None


def _load_model():
    """Loaded once per process and kept. The checkpoint is 77 MB and takes ~24s to pull the
    first time, so re-instantiating per track would dominate a 4s analysis."""
    global _model
    if _model is None:
        from beat_this.inference import File2Beats

        _model = File2Beats(checkpoint_path="final0", device="cpu", dbn=False)
    return _model


def analyze_preview(preview_url: str, timeout: float = 30.0) -> dict:
    """Download one preview clip and return its beat grid.

    Raises BeatGridError for anything recoverable — a dead link, undecodable audio, a clip with
    too little rhythm to grid — so the caller can mark the job failed rather than crash a worker.
    """
    try:
        response = httpx.get(preview_url, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise BeatGridError(f"preview clip could not be fetched: {exc}") from exc

    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "clip.audio"
        wav = Path(directory) / "clip.wav"
        source.write_bytes(response.content)
        _decode_to_wav(source, wav)
        try:
            beats, downbeats = _load_model()(str(wav))
        except Exception as exc:  # the model raises bare RuntimeErrors for bad audio
            raise BeatGridError(f"beat tracking failed: {exc}") from exc

    beats = [round(float(t), 4) for t in beats]
    downbeats = [round(float(t), 4) for t in downbeats]
    if len(beats) < 8 or len(downbeats) < 2:
        raise BeatGridError("clip has too little steady rhythm to grid")

    intervals = [b - a for a, b in zip(beats, beats[1:]) if b > a]
    median = sorted(intervals)[len(intervals) // 2] if intervals else 0.0
    return {
        "source": GRID_SOURCE,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "bpm": round(60.0 / median, 2) if median > 0 else None,
        "beats": beats,
        "downbeats": downbeats,
        "beats_per_bar": _infer_beats_per_bar(beats, downbeats),
    }


def _infer_beats_per_bar(beats: Sequence[float], downbeats: Sequence[float]) -> int | None:
    """How many beats the model actually put between downbeats.

    Reported rather than assumed: it is the cheapest available check that the grid is real. A
    value of 4 (or 3 for a waltz) means the model found consistent bars; anything else means the
    downbeats are noise and the caller should treat the grid as beat-only.
    """
    if len(downbeats) < 2 or len(beats) < 2:
        return None
    bar_lengths = [b - a for a, b in zip(downbeats, downbeats[1:]) if b > a]
    beat_lengths = [b - a for a, b in zip(beats, beats[1:]) if b > a]
    if not bar_lengths or not beat_lengths:
        return None
    bar = sorted(bar_lengths)[len(bar_lengths) // 2]
    beat = sorted(beat_lengths)[len(beat_lengths) // 2]
    if beat <= 0:
        return None
    ratio = bar / beat
    rounded = round(ratio)
    # Only trust it when the ratio is genuinely close to a whole number of beats.
    return rounded if rounded in (2, 3, 4, 6) and abs(ratio - rounded) < 0.25 else None


# --- Pure grid questions ------------------------------------------------------------------
# Everything below is arithmetic over a stored grid: no audio, no model, no network. This is the
# part the player's musical decisions actually rest on, so it is kept separable and tested.


TRUSTED_METERS = (3, 4, 6)
"""Meters whose downbeats are worth aligning bars to.

2 is excluded on purpose even though `_infer_beats_per_bar` can report it. Genuine 2/4 is rare
in this catalog, and a 2-beat "bar" is the classic signature of the tracker reading a 4/4 song at
half speed. Aligning on it would place the incoming track on beat 3 as often as beat 1 — the
exact half-bar error this whole grid exists to remove — so a 2 falls back to beat-only handling
instead of being trusted."""


def is_usable(grid: dict | None) -> bool:
    """A grid worth aligning bars to, as opposed to one that only knows about beats."""
    if not grid:
        return False
    return (
        len(grid.get("downbeats") or []) >= 2
        and len(grid.get("beats") or []) >= 8
        and grid.get("beats_per_bar") in TRUSTED_METERS
    )


def phrase_downbeats(grid: dict, phrase_bars: int = PHRASE_BARS) -> list[float]:
    """Downbeats that begin a phrase, i.e. every `phrase_bars`-th bar.

    A blend that starts here lands on the "1" of a musical section rather than merely on a bar
    line, which is the difference between a mix sounding deliberate and sounding like a fade at
    an arbitrary moment.
    """
    downbeats = grid.get("downbeats") or []
    return [time for index, time in enumerate(downbeats) if index % phrase_bars == 0]


def last_phrase_start_before(grid: dict, limit: float, phrase_bars: int = PHRASE_BARS) -> float | None:
    """The latest phrase start at or before `limit` — where to drop the needle so a blend still
    fits in the clip while beginning on a phrase."""
    candidates = [time for time in phrase_downbeats(grid, phrase_bars) if time <= limit]
    return candidates[-1] if candidates else None


def first_downbeat_after(grid: dict, start: float) -> float | None:
    """The first bar line at or after `start`. This is what the incoming deck starts on, so its
    bar 1 coincides with a bar 1 of the outgoing deck."""
    for time in grid.get("downbeats") or []:
        if time >= start:
            return time
    return None


def seconds_per_bar(grid: dict) -> float | None:
    downbeats = grid.get("downbeats") or []
    if len(downbeats) < 2:
        return None
    gaps = [b - a for a, b in zip(downbeats, downbeats[1:]) if b > a]
    if not gaps:
        return None
    return sorted(gaps)[len(gaps) // 2]
