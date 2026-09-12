from app.services.beat_grid import (
    TRUSTED_METERS,
    _infer_beats_per_bar,
    first_downbeat_after,
    is_usable,
    last_phrase_start_before,
    phrase_downbeats,
    seconds_per_bar,
)


def grid(bpm=120.0, bars=8, beats_per_bar=4, start=0.5):
    """A synthetic 4/4 grid at a known tempo, so every assertion below is exact arithmetic."""
    spb = 60 / bpm
    beats = [round(start + i * spb, 4) for i in range(bars * beats_per_bar)]
    downbeats = beats[::beats_per_bar]
    return {"bpm": bpm, "beats": beats, "downbeats": downbeats, "beats_per_bar": beats_per_bar}


def test_beats_per_bar_is_measured_not_assumed():
    assert _infer_beats_per_bar(grid()["beats"], grid()["downbeats"]) == 4
    waltz = grid(beats_per_bar=3)
    assert _infer_beats_per_bar(waltz["beats"], waltz["downbeats"]) == 3
    # Downbeats that do not sit at a whole number of beats are noise, not a meter.
    assert _infer_beats_per_bar([0, 0.5, 1.0, 1.5, 2.0], [0, 0.7]) is None


def test_a_two_beat_bar_is_not_trusted():
    """A 2-beat "bar" is almost always the tracker reading 4/4 at half speed. Aligning on it puts
    the incoming track on beat 3 as often as beat 1 — the exact error downbeats exist to remove."""
    assert 2 not in TRUSTED_METERS
    assert not is_usable(grid(beats_per_bar=2))
    assert is_usable(grid(beats_per_bar=4))
    assert is_usable(grid(beats_per_bar=3))


def test_a_grid_without_bars_is_not_usable():
    assert not is_usable(None)
    assert not is_usable({"beats": [0, 1, 2], "downbeats": [], "beats_per_bar": 4})
    assert not is_usable({"beats": [0, 1], "downbeats": [0, 1], "beats_per_bar": 4})


def test_phrase_starts_are_every_fourth_bar():
    g = grid(bars=12)
    starts = phrase_downbeats(g, phrase_bars=4)
    assert starts == [g["downbeats"][0], g["downbeats"][4], g["downbeats"][8]]


def test_last_phrase_start_before_never_overshoots():
    g = grid(bars=12)          # 120 BPM, 2s per bar, phrase every 8s from 0.5
    assert last_phrase_start_before(g, 20.0) == 16.5
    assert last_phrase_start_before(g, 16.5) == 16.5, "a phrase start exactly at the limit still fits"
    assert last_phrase_start_before(g, 0.0) is None, "no phrase starts that early"


def test_first_downbeat_after_lands_on_a_bar_line():
    g = grid(bars=8)
    assert first_downbeat_after(g, 0.0) == 0.5
    # Mid-bar: must move forward to the next bar line, never back to the one just missed.
    assert first_downbeat_after(g, 3.1) == 4.5
    assert first_downbeat_after(g, 999) is None


def test_seconds_per_bar_comes_from_the_downbeats():
    assert seconds_per_bar(grid(bpm=120)) == 2.0          # 4 beats at 0.5s
    assert abs(seconds_per_bar(grid(bpm=90, beats_per_bar=3)) - 2.0) < 1e-6
    assert seconds_per_bar({"downbeats": [1.0]}) is None
