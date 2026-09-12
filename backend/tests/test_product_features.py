import uuid

import numpy as np
import pytest
from pydantic import ValidationError

from app.models import Song
from app.routers.product import _bounded_rows, _prompt_constraints, _spotify_track_matches
from app.routers.recommend import _adapt_weight_values
from app.schemas import JourneyIn, PromptDiscoveryIn, ShareCompareIn
from app.services.brain import DEFAULT_WEIGHTS, sonic_distance, sonic_distance_breakdown


def _song(title="Halo", artist="Beyoncé"):
    return Song(id=uuid.uuid4(), title=title, artist=artist)


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("popular songs", ([], None, None)),
        ("calm electronic under 110 BPM", (["electronic"], "low", 110)),
        ("intense hip hop slower than 95", (["hip hop"], "high", 95)),
        ("rock below 999 bpm", (["rock"], None, None)),
        ("soft pop below 39 bpm", (["pop"], "low", None)),
    ],
)
def test_prompt_constraints_are_bounded_and_word_aware(prompt, expected):
    assert _prompt_constraints(prompt) == expected


def test_request_contracts_reject_empty_or_unbounded_input():
    with pytest.raises(ValidationError):
        PromptDiscoveryIn(prompt="")
    with pytest.raises(ValidationError):
        JourneyIn(song_ids=[])
    with pytest.raises(ValidationError):
        JourneyIn(song_ids=[uuid.uuid4()] * 101)
    with pytest.raises(ValidationError):
        ShareCompareIn(token="guessable")


def test_bounded_rows_is_deterministic_and_covers_both_ends():
    rows = list(range(1000))
    selected = _bounded_rows(rows, 25)
    assert selected == _bounded_rows(rows, 25)
    assert len(selected) == 25
    assert selected[0] == 0 and selected[-1] == 999


def test_spotify_match_rejects_wrong_artist_and_handles_accents():
    song = _song()
    assert _spotify_track_matches(song, {"name": "Halo", "artists": [{"name": "Beyonce"}]})
    assert not _spotify_track_matches(song, {"name": "Halo", "artists": [{"name": "Foo Fighters"}]})
    assert not _spotify_track_matches(song, {"name": "Single Ladies", "artists": [{"name": "Beyoncé"}]})


def test_online_weight_updates_remain_positive_normalized_and_bounded():
    values = {k: getattr(DEFAULT_WEIGHTS, k) for k in ("genre", "timbre", "rhythm", "tonal", "energy")}
    parts = {k: 0.0 for k in values}
    for _ in range(10_000):
        values = _adapt_weight_values(values, parts, "less_like")
    assert sum(values.values()) == pytest.approx(1.0)
    assert all(0.0 < value < 1.0 for value in values.values())


def test_xray_contributions_reconstruct_sonic_distance():
    rng = np.random.default_rng(7)
    a, b = rng.normal(size=32), rng.normal(size=32)
    ga, gb = rng.normal(size=1280), rng.normal(size=1280)
    breakdown = sonic_distance_breakdown(a, ga, b, gb)
    assert sum(breakdown.values()) == pytest.approx(sonic_distance(a, ga, b, gb))
