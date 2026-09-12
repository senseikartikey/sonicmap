import math

import pytest

from app.services.mashup import (
    COMFORTABLE_STRETCH_PCT,
    EXACT_SOLVE_LIMIT,
    TrackAnalysis,
    camelot_distance,
    energy_targets,
    fold_tempo,
    plan_set,
    plan_transition,
    score_pair,
    tempo_move,
    to_camelot,
)
from app.services.mashup_export import IncompleteSetError, to_cue_sheet, to_m3u8, to_rekordbox_xml

# The full Camelot wheel, as printed on every DJ key chart. 8B/8A is the anchor.
WHEEL = {
    "C major": "8B", "G major": "9B", "D major": "10B", "A major": "11B", "E major": "12B",
    "B major": "1B", "F# major": "2B", "Db major": "3B", "Ab major": "4B", "Eb major": "5B",
    "Bb major": "6B", "F major": "7B",
    "A minor": "8A", "E minor": "9A", "B minor": "10A", "F# minor": "11A", "C# minor": "12A",
    "Ab minor": "1A", "Eb minor": "2A", "Bb minor": "3A", "F minor": "4A", "C minor": "5A",
    "G minor": "6A", "D minor": "7A",
}


def track(id_, **kwargs):
    base = dict(title=f"Track {id_}", artist=f"Artist {id_}", bpm=124.0, key="C major", energy=0.6)
    base.update(kwargs)
    return TrackAnalysis(id=str(id_), **base)


def test_every_key_maps_to_the_right_camelot_code():
    assert {key: to_camelot(key) for key in WHEEL} == WHEEL


def test_enharmonic_spellings_are_the_same_key():
    """Essentia emits sharps for some pitch classes and flats for others, so both spellings of
    one pitch class have to land on the same code or the same song reads as two keys."""
    assert to_camelot("Gb minor") == to_camelot("F# minor") == "11A"
    assert to_camelot("Db major") == to_camelot("C# major") == "3B"
    assert to_camelot("nonsense") is None and to_camelot(None) is None


def test_camelot_distance_wraps_and_counts_the_relative_flip():
    assert camelot_distance("8B", "8B") == 0
    assert camelot_distance("8B", "9B") == 1          # one hour around
    assert camelot_distance("8B", "8A") == 1          # relative major/minor
    assert camelot_distance("12B", "1B") == 1         # wraps past 12
    assert camelot_distance("8B", "2B") == 6          # opposite side
    assert camelot_distance("8B", None) is None


def test_tempo_folds_octave_errors_into_a_danceable_range():
    """The live catalog holds BPMs from 57.7 to 738.3; the extremes are octave errors, not
    music, and comparing them unfolded makes every pairing look impossible."""
    assert fold_tempo(738.3) == pytest.approx(92.29, abs=0.01)
    assert fold_tempo(57.7) == pytest.approx(115.4, abs=0.01)
    assert fold_tempo(128) == 128
    assert fold_tempo(None) is None and fold_tempo(0) is None


def test_tempo_move_prefers_a_half_time_lock_over_a_huge_stretch():
    move = tempo_move(85, 170)
    assert move.ratio == "half" and abs(move.stretch_pct) < 0.01 and move.within_comfort
    stretch = tempo_move(124, 128)
    assert stretch.ratio == "same" and stretch.within_comfort
    assert not tempo_move(100, 130).within_comfort


def test_no_scoring_component_flattens_out_inside_a_real_range():
    """Regression for the bug that scattered a real set: every component used to clip to a
    constant past a threshold, so two differently-bad pairs scored identically and the
    sequencer lost the gradient it orders by — exactly where ordering matters most."""
    base = track(1, bpm=100)
    # Held inside one tempo relationship — see the half-time test below for why a plain
    # "higher BPM scores lower" sweep is not the invariant it looks like.
    scores = [score_pair(base, track(2, bpm=bpm)).tempo for bpm in (104, 110, 118, 126, 133)]
    assert all(a > b for a, b in zip(scores, scores[1:])), scores
    assert scores[-1] > 0, "a hopeless tempo pair must still be ranked, not floored to zero"

    harmonic = [score_pair(base, track(2, key=key)).harmonic
                for key in ("C major", "G major", "D major", "A major", "E major")]
    assert all(a >= b for a, b in zip(harmonic, harmonic[1:])) and harmonic[-1] > 0

    energy = [score_pair(base, track(2, energy=e)).energy for e in (0.6, 0.75, 0.9)]
    assert all(a > b for a, b in zip(energy, energy[1:])) and energy[-1] > 0


def test_a_far_faster_track_can_beat_a_nearer_one_by_locking_at_half_time():
    """Counter-intuitive but correct, and worth pinning because it looks like a bug: against a
    100 BPM track, a 180 BPM track scores *higher* than a 160 BPM one. 180 halves to 90, an
    11% pull away, while 160 needs 25% however you slice it. A DJ would make the same call."""
    base = track(1, bpm=100)
    assert score_pair(base, track(2, bpm=180)).tempo > score_pair(base, track(2, bpm=160)).tempo
    assert tempo_move(100, 180).ratio == "half"


def test_a_perfect_pair_outscores_a_clashing_one():
    perfect = score_pair(track(1, bpm=124, key="C major"), track(2, bpm=124, key="C major"))
    clash = score_pair(track(1, bpm=124, key="C major"), track(2, bpm=168, key="F# major"))
    assert perfect.total > clash.total + 0.25


def test_missing_data_scores_as_mediocre_rather_than_perfect():
    """A track with no key must not be rewarded for having nothing to clash with."""
    unknown = score_pair(track(1, key=None), track(2, key=None))
    matched = score_pair(track(1, key="C major"), track(2, key="C major"))
    assert unknown.harmonic < matched.harmonic


def test_sequencer_walks_the_tempo_ladder():
    """Given a pile spanning 96-144 BPM, the order should climb rather than jump around."""
    pool = [track(i, bpm=bpm, energy=0.5) for i, bpm in enumerate([132, 100, 144, 108, 96, 120])]
    plan = plan_set(pool, shape="build")
    tempos = [t.tempo for t in plan.order]
    jumps = [abs(b - a) for a, b in zip(tempos, tempos[1:])]
    assert max(jumps) <= 16, tempos


def test_pinned_opener_is_honoured_by_both_solvers():
    for count in (6, EXACT_SOLVE_LIMIT + 3):
        pool = [track(i, bpm=100 + i * 3) for i in range(count)]
        plan = plan_set(pool, open_with="4")
        assert plan.order[0].id == "4", (count, [t.id for t in plan.order])


def test_energy_shapes_have_the_arcs_they_claim():
    build = energy_targets("build", 8)
    assert build == sorted(build)
    wind = energy_targets("wind_down", 8)
    assert wind == sorted(wind, reverse=True)
    arc = energy_targets("arc", 11)
    peak = arc.index(max(arc))
    assert 0 < peak < len(arc) - 1, "an arc has to come back down, not end at its peak"


def test_transition_technique_follows_the_rules_it_claims():
    calm = plan_transition(track(1, energy=0.3), track(2, energy=0.35))
    assert calm.kind == "long_blend" and calm.bars == 64

    hot = plan_transition(track(1, energy=0.85), track(2, energy=0.8))
    assert hot.kind == "double_drop"

    unmixable = plan_transition(track(1, bpm=100), track(2, bpm=145))
    assert unmixable.kind == "cut"
    assert any("stretch" in warning for warning in unmixable.warnings)

    clash = plan_transition(track(1, key="C major"), track(2, key="F# major", energy=0.6))
    assert clash.kind == "echo_out" and clash.camelot_steps >= 3


def test_every_transition_length_is_a_whole_phrase():
    """Landing mid-phrase is the most-reported tell of an automated mix, so the planner must
    never emit a bar count that isn't one."""
    pool = [track(i, bpm=100 + i * 9, energy=i / 10, key=key)
            for i, key in enumerate(["C major", "G major", "F# minor", "A minor", "Eb major"])]
    for transition in plan_set(pool).transitions:
        assert transition.bars in (8, 16, 32, 64)
        assert transition.beats == transition.bars * 4


def test_comfort_boundary_decides_blend_versus_cut():
    inside = plan_transition(track(1, bpm=124), track(2, bpm=124 * (1 + COMFORTABLE_STRETCH_PCT / 100 - 0.005)))
    assert inside.kind != "cut"
    outside = plan_transition(track(1, bpm=124), track(2, bpm=124 * 1.2))
    assert outside.kind == "cut" and outside.bars == 8


def test_plan_reports_running_time_only_when_every_length_is_known():
    known = [track(i, duration_ms=200_000, bpm=124) for i in range(3)]
    plan = plan_set(known)
    assert plan.total_ms is not None and plan.total_ms < 3 * 200_000, "blends overlap"
    plan_missing = plan_set([track(1, duration_ms=200_000), track(2, duration_ms=None)])
    assert plan_missing.total_ms is None


def test_empty_and_single_track_sets_do_not_explode():
    assert plan_set([]).order == []
    single = plan_set([track(1)])
    assert len(single.order) == 1 and single.transitions == []


def test_exports_carry_the_plan_a_dj_needs():
    pool = [track(i, bpm=120 + i * 2, duration_ms=210_000) for i in range(4)]
    plan = plan_set(pool)

    xml = to_rekordbox_xml(plan, "Friday Warm-Up")
    assert xml.filename == "friday-warm-up.xml" and xml.body.startswith("<?xml")
    assert xml.body.count("<TRACK ") == len(pool) * 2  # collection entries + playlist keys
    assert "POSITION_MARK" in xml.body and 'Tonality="8B"' in xml.body

    m3u = to_m3u8(plan, "Friday Warm-Up")
    assert m3u.body.startswith("#EXTM3U") and m3u.body.count("#EXTINF") == len(pool)

    cue = to_cue_sheet(plan, "Friday Warm-Up")
    assert cue.body.count("TRACK 0") == len(pool) and "INDEX 01" in cue.body


def test_rekordbox_export_escapes_once_and_never_invents_a_length():
    """An ampersand in an artist name came back as '&amp;amp;' when the exporter escaped a
    value ElementTree escapes itself; and an unknown length must be absent, not zero."""
    plan = plan_set([
        TrackAnalysis(id="1", title="Hits & Misses", artist="Above & Beyond", bpm=124, key="C major", energy=0.6),
        TrackAnalysis(id="2", title="Other", artist="Someone", bpm=126, key="G major", energy=0.6),
    ])
    body = to_rekordbox_xml(plan, "Set").body
    assert "&amp;amp;" not in body and "Above &amp; Beyond" in body
    assert 'TotalTime="0"' not in body


def test_cue_sheet_refuses_a_set_with_an_unknown_track_length():
    """Treating a missing length as zero does not degrade gracefully — the running offset
    stops advancing and two tracks get stamped at the same index, which is a broken cue sheet
    rather than an approximate one. rekordbox and M3U8 stay available because neither claims
    a position in a continuous recording."""
    plan = plan_set([
        track(1, duration_ms=200_000), track(2, duration_ms=None), track(3, duration_ms=190_000),
    ])
    with pytest.raises(IncompleteSetError, match="length"):
        to_cue_sheet(plan, "Set")
    assert to_m3u8(plan, "Set").body and to_rekordbox_xml(plan, "Set").body


def test_cue_sheet_offsets_never_run_backwards():
    plan = plan_set([track(i, bpm=124, duration_ms=180_000) for i in range(5)])
    indexes = [line for line in to_cue_sheet(plan, "Set").body.splitlines() if "INDEX 01" in line]
    stamps = [tuple(int(part) for part in line.split()[-1].split(":")) for line in indexes]
    assert stamps == sorted(stamps), stamps


def test_a_short_set_is_solved_exactly_and_a_long_one_still_finishes():
    short = plan_set([track(i, bpm=110 + i * 4) for i in range(EXACT_SOLVE_LIMIT)])
    assert len(short.order) == EXACT_SOLVE_LIMIT
    long_set = plan_set([track(i, bpm=100 + (i * 37) % 60, energy=(i % 7) / 10) for i in range(24)])
    assert len(long_set.order) == 24
    assert len({t.id for t in long_set.order}) == 24, "the heuristic must not drop or duplicate"
    assert math.isfinite(long_set.quality)
