"""Set planning: turn an unordered pile of tracks into a DJ-playable running order.

This is the *planner* half of the mashup engine. It decides what plays after what, where the
blend starts, how long it runs and which technique it uses. It never touches audio — every
input here is already on the `songs` row (bpm, key, energy, feature/genre vectors), which is
what lets it run over a whole recommendations playlist that Sonicmap has no audio rights to.
Rendering real audio is a separate, narrower path (see app/services/stem_jobs.py) that only
ever runs on tracks the user supplied themselves.

The rules encoded here are the ones DJ practice and the automatic-DJ literature agree on:

  * Blends resolve on phrase boundaries, and phrases are 8/16/32 bars. A 32-bar blend reads
    as confident; an 8-bar one reads as panicked. So transition length is quantised to whole
    phrases, never to seconds.
  * Harmonic mixing works on the Camelot wheel: same key, one step around the wheel, or the
    relative major/minor at the same number. Everything else is a clash you should hear coming.
  * Tempo can be pulled about 6% before time-stretching becomes audible. Past that you either
    accept a double/half-time relationship or you stop blending and cut.
  * The listening test in DJtransGAN (ICASSP 2022, 136 listeners incl. 46 working DJs) found
    no significant quality difference between a linear crossfade, a rule-based blend and a
    learned one — but experienced listeners reliably flagged *bad pairings and wrong cue
    points*. That is why the effort here goes into pair scoring and ordering rather than into
    modelling fader curves.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Iterable, Literal, Sequence

import numpy as np

from app.services.brain import sonic_distance

# --- Camelot wheel -----------------------------------------------------------------------
# 8B is C major by convention; majors run clockwise around the circle of fifths, and each
# minor sits at the same number as its relative major (8A = A minor = relative of 8B).
_MAJOR_ORDER = ["C", "G", "D", "A", "E", "B", "F#", "C#", "G#", "D#", "A#", "F"]
_MINOR_ORDER = ["A", "E", "B", "F#", "C#", "G#", "D#", "A#", "F", "C", "G", "D"]

# Essentia writes keys as "C major" / "F# minor" / "Ab major" / "Eb minor" — sharps for some
# pitch classes and flats for others — so both spellings have to resolve to one pitch class.
_ENHARMONIC = {
    "DB": "C#", "EB": "D#", "GB": "F#", "AB": "G#", "BB": "A#",
    "CB": "B", "FB": "E", "E#": "F", "B#": "C",
}

CAMELOT_BY_KEY: dict[str, str] = {}
for _i, _root in enumerate(_MAJOR_ORDER):
    CAMELOT_BY_KEY[f"{_root} major"] = f"{(_i + 7) % 12 + 1}B"
for _i, _root in enumerate(_MINOR_ORDER):
    CAMELOT_BY_KEY[f"{_root} minor"] = f"{(_i + 7) % 12 + 1}A"


def _normalize_root(root: str) -> str:
    root = root.strip()
    upper = root.upper()
    return _ENHARMONIC.get(upper, upper.capitalize() if len(upper) == 1 else upper[0] + upper[1:])


def to_camelot(key: str | None) -> str | None:
    """"F# minor" / "Gb minor" -> "11A". None for anything unparseable, never a guess."""
    if not key:
        return None
    parts = key.strip().split()
    if len(parts) != 2:
        return None
    root, mode = _normalize_root(parts[0]), parts[1].lower()
    if mode not in ("major", "minor"):
        return None
    return CAMELOT_BY_KEY.get(f"{root} {mode}")


def camelot_distance(a: str | None, b: str | None) -> int | None:
    """Moves around the Camelot wheel, where one move is ±1 hour or a relative major/minor
    flip at the same hour. 0 is the same key, 1 is any of the four classic "safe" mixes,
    and anything ≥2 is a clash a listener will notice."""
    if not a or not b:
        return None
    try:
        na, la = int(a[:-1]), a[-1]
        nb, lb = int(b[:-1]), b[-1]
    except (ValueError, IndexError):
        return None
    around = abs(na - nb)
    steps = min(around, 12 - around)
    return steps + (0 if la == lb else 1)


# --- Tempo -------------------------------------------------------------------------------
# Essentia's BPM estimate lands outside any danceable range on a meaningful slice of the
# catalog (the live table runs 57.7 to 738.3), almost always as an octave error rather than
# genuinely fast music, so tempo is folded into a DJ range before anything is compared.
TEMPO_FLOOR = 70.0
TEMPO_CEILING = 180.0
COMFORTABLE_STRETCH_PCT = 6.0
"""Rubber-band territory: past roughly ±6% a time-stretch stops being transparent, so beyond
this the planner stops proposing a blend and proposes a cut instead."""


def fold_tempo(bpm: float | None) -> float | None:
    """Halve or double until the tempo sits in a range someone could actually dance to."""
    if not bpm or bpm <= 0 or not math.isfinite(bpm):
        return None
    folded = float(bpm)
    for _ in range(6):
        if folded > TEMPO_CEILING:
            folded /= 2
        elif folded < TEMPO_FLOOR:
            folded *= 2
        else:
            return round(folded, 2)
    return None


@dataclass(frozen=True)
class TempoMove:
    """How the incoming track has to be pulled to sit on the outgoing track's grid."""

    stretch_pct: float
    """Signed. +3.1 means the incoming track is sped up 3.1% to match."""
    ratio: Literal["same", "double", "half"]
    """`double`/`half` mean the two tracks lock at a 2:1 pulse — a legitimate DJ move
    (half-time rolls under a double-time top line), not a failed match."""
    within_comfort: bool


def tempo_move(from_bpm: float | None, to_bpm: float | None) -> TempoMove | None:
    """Best way to get `to_bpm` onto `from_bpm`'s grid, considering the 2:1 relationships."""
    a, b = fold_tempo(from_bpm), fold_tempo(to_bpm)
    if a is None or b is None:
        return None
    options: list[tuple[float, Literal["same", "double", "half"]]] = [
        (a / b, "same"), (a / (b * 2), "double"), (a / (b / 2), "half"),
    ]
    ratio, kind = min(options, key=lambda option: abs(math.log(option[0])))
    stretch = (ratio - 1.0) * 100.0
    return TempoMove(
        stretch_pct=round(stretch, 2),
        ratio=kind,
        within_comfort=abs(stretch) <= COMFORTABLE_STRETCH_PCT,
    )


# --- Track view --------------------------------------------------------------------------
@dataclass
class TrackAnalysis:
    """Everything the planner needs about one track, decoupled from the ORM so the whole
    engine is testable without a database (and so a future analysis pass can supply real
    beat grids here without changing any of the logic below)."""

    id: str
    title: str
    artist: str
    bpm: float | None = None
    key: str | None = None
    energy: float | None = None
    duration_ms: int | None = None
    genre: str | None = None
    preview_url: str | None = None
    beat_grid: dict | None = None
    feature_vector: Sequence[float] | None = None
    genre_vector: Sequence[float] | None = None

    @property
    def camelot(self) -> str | None:
        return to_camelot(self.key)

    @property
    def tempo(self) -> float | None:
        return fold_tempo(self.bpm)


# --- Pair scoring ------------------------------------------------------------------------
@dataclass(frozen=True)
class PairScore:
    total: float
    tempo: float
    harmonic: float
    sonic: float
    energy: float

    @property
    def as_dict(self) -> dict[str, float]:
        return {
            "total": round(self.total, 4), "tempo": round(self.tempo, 4),
            "harmonic": round(self.harmonic, 4), "sonic": round(self.sonic, 4),
            "energy": round(self.energy, 4),
        }


# Tempo dominates because it is the one incompatibility no technique hides: a 12% mismatch is
# audible through any blend, whereas a key clash can be EQ'd around and a genre jump can be
# made a feature. Harmonic and sonic coherence then carry equal weight — the Camelot rule is
# what DJs actually mix on, and sonic_distance is the thing Sonicmap has that no DJ tool does.
TEMPO_WEIGHT = 0.40
HARMONIC_WEIGHT = 0.25
SONIC_WEIGHT = 0.25
ENERGY_WEIGHT = 0.10

# A missing measurement scores as mediocre rather than as either a match or a clash — the
# planner must not reward a track for having no data, nor bury a real track it can't measure.
UNKNOWN_SCORE = 0.5


# Every component below decays smoothly and never reaches a flat floor inside the range real
# tracks occupy. That is deliberate and it is load-bearing: the sequencer picks an order by
# comparing pair scores, so the instant two bad pairs score identically it loses the gradient
# it needs to sort by — and it loses it exactly where ordering matters most. An earlier
# version clipped tempo to zero at 12%, which made a 13% gap and a 30% gap indistinguishable
# and let the solver scatter a 84-to-144 BPM pool instead of walking up it.


def _tempo_component(move: TempoMove | None) -> float:
    if move is None:
        return UNKNOWN_SCORE
    # ~0.95 at 2%, ~0.77 at the 6% comfort edge, ~0.46 at 12%, ~0.08 at 25% — still ordered.
    fit = math.exp(-((abs(move.stretch_pct) / 14.0) ** 1.6))
    # A 2:1 lock is a real technique but a more demanding one, so it scores slightly under an
    # equally-tight same-tempo match rather than tying with it.
    return fit * (1.0 if move.ratio == "same" else 0.85)


def _harmonic_component(distance: int | None) -> float:
    if distance is None:
        return UNKNOWN_SCORE
    # 0 and 1 are both "correct" harmonic mixes; the cliff is at 2. Past that it keeps
    # descending rather than plateauing, so "nearly opposite" still beats "opposite".
    return {0: 1.0, 1: 0.92, 2: 0.55, 3: 0.38, 4: 0.28, 5: 0.20, 6: 0.14}.get(distance, 0.10)


def _sonic_component(a: TrackAnalysis, b: TrackAnalysis) -> float:
    if a.feature_vector is None or b.feature_vector is None:
        return UNKNOWN_SCORE
    distance = sonic_distance(
        np.asarray(a.feature_vector, dtype=float),
        np.asarray(a.genre_vector, dtype=float) if a.genre_vector is not None else None,
        np.asarray(b.feature_vector, dtype=float),
        np.asarray(b.genre_vector, dtype=float) if b.genre_vector is not None else None,
    )
    # SonicDistance is ~[0,1] for whole-song pairs (measured median 0.35), but it is not bounded
    # there, so decay rather than clamp — see the note above _tempo_component.
    return math.exp(-distance)


def _energy_component(a: TrackAnalysis, b: TrackAnalysis) -> float:
    if a.energy is None or b.energy is None:
        return UNKNOWN_SCORE
    # Pairwise, only the size of the jump matters; whether the set should be rising or falling
    # at this point is a whole-set question, handled by the energy curve in `plan_set`.
    return math.exp(-abs(a.energy - b.energy) * 3.0)


def score_pair(a: TrackAnalysis, b: TrackAnalysis) -> PairScore:
    """How well b can follow a. Asymmetric in principle (tempo pull direction, energy rise)
    though currently symmetric in every component — kept directional so the sequencer can
    stay an asymmetric solver as the rules get richer."""
    tempo = _tempo_component(tempo_move(a.bpm, b.bpm))
    harmonic = _harmonic_component(camelot_distance(a.camelot, b.camelot))
    sonic = _sonic_component(a, b)
    energy = _energy_component(a, b)
    total = (
        TEMPO_WEIGHT * tempo + HARMONIC_WEIGHT * harmonic
        + SONIC_WEIGHT * sonic + ENERGY_WEIGHT * energy
    )
    return PairScore(total=total, tempo=tempo, harmonic=harmonic, sonic=sonic, energy=energy)


# --- Energy curve ------------------------------------------------------------------------
EnergyShape = Literal["arc", "build", "peak", "wind_down", "wave"]


def energy_targets(shape: EnergyShape, count: int) -> list[float]:
    """Normalised 0..1 target energy for each slot in the set.

    `arc` is the default because it is what a party actually wants: open below the room's
    energy, climb, peak around four fifths of the way in, then ease off rather than dropping
    people off a cliff at the end.
    """
    if count <= 1:
        return [0.5] * max(count, 0)
    positions = [i / (count - 1) for i in range(count)]
    if shape == "build":
        return positions
    if shape == "peak":
        return [0.7 + 0.3 * p for p in positions]
    if shape == "wind_down":
        return [1.0 - p for p in positions]
    if shape == "wave":
        return [0.5 + 0.5 * math.sin(2 * math.pi * p - math.pi / 2) for p in positions]
    # arc: rise to a peak at p=0.8, then a controlled descent to ~0.75 of the peak.
    peak = 0.8
    return [
        (0.35 + 0.65 * (p / peak)) if p <= peak else (1.0 - 0.25 * ((p - peak) / (1 - peak)))
        for p in positions
    ]


def _energy_penalties(tracks: Sequence[TrackAnalysis], shape: EnergyShape) -> list[list[float]]:
    """penalty[position][track] — how wrong it is to place this track at this point in the arc.

    Targets are rescaled onto the energy range actually present in the pool. A playlist whose
    tracks all sit between 0.55 and 0.65 has no 0.0 or 1.0 to offer, and scoring it against an
    absolute curve would just penalise every arrangement equally while adding noise.
    """
    known = [t.energy for t in tracks if t.energy is not None]
    count = len(tracks)
    if not known or count == 0:
        return [[0.0] * count for _ in range(count)]
    low, high = min(known), max(known)
    span = high - low
    targets = energy_targets(shape, count)
    if span < 1e-6:
        return [[0.0] * count for _ in range(count)]
    return [
        [
            0.0 if track.energy is None else abs(track.energy - (low + target * span)) / span
            for track in tracks
        ]
        for target in targets
    ]


# --- Sequencing --------------------------------------------------------------------------
EXACT_SOLVE_LIMIT = 11
"""Above this many tracks Held-Karp's 2^n table stops being worth the wall time in Python, so
the sequencer switches to a greedy construction refined by 2-opt and Or-opt. The exact solver
covers a short warm-up set; a full night is handled by the heuristic."""

ENERGY_CURVE_WEIGHT = 0.35
"""How hard the arc pulls against pure pair quality. High enough that the set actually shapes,
low enough that it will not force a jarring transition just to hit an energy target."""


def _sequence_cost(
    order: Sequence[int],
    pair_cost: list[list[float]],
    energy_penalty: list[list[float]],
) -> float:
    cost = sum(pair_cost[order[i]][order[i + 1]] for i in range(len(order) - 1))
    cost += ENERGY_CURVE_WEIGHT * sum(energy_penalty[position][track] for position, track in enumerate(order))
    return cost


def _solve_exact(
    count: int, pair_cost: list[list[float]], energy_penalty: list[list[float]], start: int | None
) -> list[int]:
    """Held-Karp over subsets. The energy term is position-dependent, which normally breaks a
    TSP formulation — but the DP state already encodes position as the popcount of the visited
    mask, so the penalty can be charged exactly as each track is added."""
    full = 1 << count
    best: list[dict[int, float]] = [{} for _ in range(full)]
    back: list[dict[int, int]] = [{} for _ in range(full)]
    starts = [start] if start is not None else range(count)
    for first in starts:
        mask = 1 << first
        best[mask][first] = ENERGY_CURVE_WEIGHT * energy_penalty[0][first]
    for mask in range(full):
        if not best[mask]:
            continue
        position = bin(mask).count("1")
        if position >= count:
            continue
        for last, cost_so_far in list(best[mask].items()):
            for nxt in range(count):
                if mask & (1 << nxt):
                    continue
                new_mask = mask | (1 << nxt)
                cost = (
                    cost_so_far + pair_cost[last][nxt]
                    + ENERGY_CURVE_WEIGHT * energy_penalty[position][nxt]
                )
                if cost < best[new_mask].get(nxt, math.inf):
                    best[new_mask][nxt] = cost
                    back[new_mask][nxt] = last
    final = full - 1
    last = min(best[final], key=lambda node: best[final][node])
    order = [last]
    mask = final
    while len(order) < count:
        previous = back[mask][last]
        mask ^= 1 << last
        last = previous
        order.append(last)
    return order[::-1]


def _solve_heuristic(
    count: int, pair_cost: list[list[float]], energy_penalty: list[list[float]], start: int | None
) -> list[int]:
    """Greedy nearest-neighbour from every plausible opener, then 2-opt and Or-opt until no
    single move improves the full objective (pair costs *and* the energy arc, recomputed —
    a segment reversal moves every track in it to a new slot, so the arc term genuinely
    changes and cannot be evaluated by the usual delta shortcut)."""
    seeds = [start] if start is not None else range(count)
    best_order: list[int] = []
    best_cost = math.inf
    for seed in seeds:
        order = [seed]
        unused = set(range(count)) - {seed}
        while unused:
            last = order[-1]
            position = len(order)
            nxt = min(
                unused,
                key=lambda node: pair_cost[last][node] + ENERGY_CURVE_WEIGHT * energy_penalty[position][node],
            )
            order.append(nxt)
            unused.discard(nxt)
        cost = _sequence_cost(order, pair_cost, energy_penalty)
        if cost < best_cost:
            best_order, best_cost = order, cost

    improved = True
    while improved:
        improved = False
        for i, j in combinations(range(1 if start is not None else 0, count), 2):
            if j - i < 1:
                continue
            candidate = best_order[:i] + best_order[i : j + 1][::-1] + best_order[j + 1 :]
            cost = _sequence_cost(candidate, pair_cost, energy_penalty)
            if cost < best_cost - 1e-9:
                best_order, best_cost, improved = candidate, cost, True
        for i in range(1 if start is not None else 0, count):
            for j in range(1 if start is not None else 0, count):
                if i == j:
                    continue
                moved = best_order[:i] + best_order[i + 1 :]
                candidate = moved[:j] + [best_order[i]] + moved[j:]
                if len(candidate) != count:
                    continue
                cost = _sequence_cost(candidate, pair_cost, energy_penalty)
                if cost < best_cost - 1e-9:
                    best_order, best_cost, improved = candidate, cost, True
    return best_order


def order_tracks(
    tracks: Sequence[TrackAnalysis],
    *,
    shape: EnergyShape = "arc",
    open_with: str | None = None,
) -> list[int]:
    """Indices of `tracks` in the order they should be played."""
    count = len(tracks)
    if count <= 2:
        return list(range(count))
    pair_cost = [[0.0] * count for _ in range(count)]
    for i, a in enumerate(tracks):
        for j, b in enumerate(tracks):
            pair_cost[i][j] = 0.0 if i == j else 1.0 - score_pair(a, b).total
    energy_penalty = _energy_penalties(tracks, shape)
    start = next((i for i, t in enumerate(tracks) if t.id == open_with), None) if open_with else None
    solver = _solve_exact if count <= EXACT_SOLVE_LIMIT else _solve_heuristic
    return solver(count, pair_cost, energy_penalty, start)


# --- Transitions -------------------------------------------------------------------------
TransitionKind = Literal["bass_swap", "long_blend", "double_drop", "rolling", "echo_out", "cut"]

PHRASE_BARS = (8, 16, 32, 64)
"""Every blend length the planner will ever emit. Transitions resolve on phrase boundaries,
and a phrase is a power-of-two bar count — quantising here is what keeps a generated set from
landing mid-phrase, which is the single most-reported tell of an automated mix."""


@dataclass
class Transition:
    from_id: str
    to_id: str
    kind: TransitionKind
    bars: int
    tempo: TempoMove | None
    camelot_from: str | None
    camelot_to: str | None
    camelot_steps: int | None
    score: PairScore
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def beats(self) -> int:
        return self.bars * 4

    def seconds_at(self, bpm: float | None) -> float | None:
        tempo = fold_tempo(bpm)
        return None if tempo is None else round(self.beats * 60.0 / tempo, 1)


def _transition_kind(
    a: TrackAnalysis, b: TrackAnalysis, move: TempoMove | None, steps: int | None
) -> tuple[TransitionKind, int]:
    """Pick the technique and its length. Order matters: the hard blockers (untempo'd,
    over-stretched, clashing keys) are checked before the stylistic choices, because a
    double-drop over a 10% tempo gap is not a double drop, it is a train wreck."""
    if move is None or not move.within_comfort:
        # Nothing to beatmatch onto, or too far to stretch: land it on a downbeat and move on.
        return "cut", 8
    if steps is not None and steps >= 3:
        # Too far around the wheel to hold two tonalities together for a whole phrase; get out
        # of the first track before the second establishes its key.
        return "echo_out", 16
    energy_a = a.energy if a.energy is not None else 0.5
    energy_b = b.energy if b.energy is not None else 0.5
    if energy_a >= 0.7 and energy_b >= 0.7:
        # Both tracks are already at the top: hold them together and land the drops as one.
        return "double_drop", 16
    if energy_a < 0.45 and energy_b < 0.45:
        # Nothing is fighting for attention, so there's room for a long, unhurried blend.
        return "long_blend", 64
    if energy_b > energy_a + 0.15:
        # Lifting the room: ride the outgoing track's tail into the incoming track's drop.
        return "rolling", 32
    return "bass_swap", 32


def plan_transition(a: TrackAnalysis, b: TrackAnalysis) -> Transition:
    move = tempo_move(a.bpm, b.bpm)
    steps = camelot_distance(a.camelot, b.camelot)
    kind, bars = _transition_kind(a, b, move, steps)
    notes: list[str] = []
    warnings: list[str] = []

    if move is None:
        warnings.append("No usable tempo for one of these tracks — this one has to be cut in by ear.")
    else:
        if move.ratio != "same":
            notes.append(
                f"Lock at {'double' if move.ratio == 'double' else 'half'} time — "
                f"{a.tempo:g} against {b.tempo:g} BPM."
            )
        if move.within_comfort:
            direction = "up" if move.stretch_pct >= 0 else "down"
            notes.append(f"Pull {b.title} {direction} {abs(move.stretch_pct):.1f}% to sit on {a.tempo:g} BPM.")
        else:
            warnings.append(
                f"{abs(move.stretch_pct):.1f}% tempo change needed — past the ±{COMFORTABLE_STRETCH_PCT:g}% "
                "that time-stretches cleanly, so cut rather than blend."
            )

    if steps is None:
        warnings.append("Key unknown for one of these tracks — check the blend by ear before trusting it.")
    elif steps == 0:
        notes.append(f"Same key ({a.camelot}) — the two will sit on top of each other.")
    elif steps == 1:
        notes.append(f"{a.camelot} into {b.camelot} — one step on the wheel, a clean harmonic move.")
    else:
        warnings.append(f"{a.camelot} into {b.camelot} is {steps} steps — expect a clash if you hold them together.")

    if kind == "bass_swap":
        notes.append("Bring the incoming low end in only on the downbeat, and take the outgoing one out on the same beat.")
    elif kind == "double_drop":
        notes.append("Both tracks are peak-energy — align the drops and let them land together.")
    elif kind == "long_blend":
        notes.append("Low energy on both sides — there is room to let this one breathe over 64 bars.")
    elif kind == "rolling":
        notes.append("Energy lifts here — ride the outgoing tail into the incoming drop.")
    elif kind == "echo_out":
        notes.append("Echo the outgoing track out rather than holding two keys against each other.")
    elif kind == "cut":
        notes.append("Cut on the downbeat — do not attempt to hold these two together.")

    return Transition(
        from_id=a.id, to_id=b.id, kind=kind, bars=bars, tempo=move,
        camelot_from=a.camelot, camelot_to=b.camelot, camelot_steps=steps,
        score=score_pair(a, b), notes=notes, warnings=warnings,
    )


# --- Whole set ---------------------------------------------------------------------------
@dataclass
class SetPlan:
    order: list[TrackAnalysis]
    transitions: list[Transition]
    shape: EnergyShape
    quality: float
    """Mean transition score across the set, 0..1. This is the number to watch when tuning —
    it says how well the *pairings* worked, independently of the arc."""

    @property
    def total_ms(self) -> int | None:
        durations = [t.duration_ms for t in self.order if t.duration_ms]
        if len(durations) != len(self.order) or not durations:
            return None
        # Every blend overlaps two tracks, so the set is shorter than the sum of its parts.
        overlap = 0.0
        for transition, track in zip(self.transitions, self.order):
            seconds = transition.seconds_at(track.bpm)
            if seconds:
                overlap += seconds * 1000
        return int(sum(durations) - overlap)

    @property
    def warnings(self) -> list[str]:
        return [warning for transition in self.transitions for warning in transition.warnings]


def plan_set(
    tracks: Iterable[TrackAnalysis],
    *,
    shape: EnergyShape = "arc",
    open_with: str | None = None,
) -> SetPlan:
    """Order a pile of tracks and plan every blend between them."""
    pool = list(tracks)
    if not pool:
        return SetPlan(order=[], transitions=[], shape=shape, quality=0.0)
    ordered = [pool[i] for i in order_tracks(pool, shape=shape, open_with=open_with)]
    transitions = [plan_transition(ordered[i], ordered[i + 1]) for i in range(len(ordered) - 1)]
    quality = (
        sum(transition.score.total for transition in transitions) / len(transitions)
        if transitions else 1.0
    )
    return SetPlan(order=ordered, transitions=transitions, shape=shape, quality=round(quality, 4))
