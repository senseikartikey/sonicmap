import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from app.models import Song
from app.schemas import IngestPasteRequest, IngestSearchRequest, IngestSpotifyPlaylistRequest
from app.services.brain import SonicDistanceWeights, _apply_diversity_cap, _song_vectors, score_candidates
from app.services.extraction import ExtractedFeatures, apply_extracted_features
from app.services.itunes import _split_query
from app.services import recommendation_cache


def _vector(timbre):
    value = np.zeros(32)
    value[3:5] = timbre
    value[29:32] = [0, 1, 1]
    return value.tolist()


def _song(title, artist, timbre):
    return Song(id=uuid.uuid4(), title=title, artist=artist, feature_vector=_vector(timbre), genre_vector=None)


def _row(song, cluster=0):
    return SimpleNamespace(cluster_label=cluster, added_at=datetime.now(timezone.utc)), song


def test_ingest_contracts_reject_empty_and_oversized_values():
    with pytest.raises(ValidationError): IngestSearchRequest(query="")
    with pytest.raises(ValidationError): IngestPasteRequest(raw_text="")
    with pytest.raises(ValidationError): IngestSpotifyPlaylistRequest(playlist_id_or_url="x")
    with pytest.raises(ValidationError): IngestSearchRequest(query="x" * 301)


@pytest.mark.parametrize("query", ["Artist - Title", "Artist – Title", "Artist: Title"])
def test_query_split_accepts_common_separators(query):
    assert _split_query(query) == ("Title", "Artist")


def test_cluster_consensus_beats_one_outlier_neighbor():
    history = [_row(_song("u1", "a", [1, 0])), _row(_song("u2", "b", [0, 1]))]
    outlier = _song("outlier", "c", [1, 0])
    consensus = _song("consensus", "d", [2**-0.5, 2**-0.5])
    ranked = asyncio.run(score_candidates(history, [outlier, consensus], limit=2, lastfm_active=False))
    assert ranked[0][0].id == consensus.id


def test_metric_weights_can_change_exact_ranking_deterministically():
    base = _song("base", "a", [1, 0])
    timbre_close = _song("timbre", "b", [1, 0]); timbre_close.feature_vector[2] = 1
    energy_close = _song("energy", "c", [0, 1]); energy_close.feature_vector[2] = 0
    energy_heavy = SonicDistanceWeights(genre=0, timbre=.05, rhythm=.05, tonal=.05, energy=.85)
    timbre_heavy = SonicDistanceWeights(genre=0, timbre=.85, rhythm=.05, tonal=.05, energy=.05)
    first = asyncio.run(score_candidates([_row(base)], [timbre_close, energy_close], 2, False, sonic_weights=energy_heavy))
    second = asyncio.run(score_candidates([_row(base)], [timbre_close, energy_close], 2, False, sonic_weights=timbre_heavy))
    assert first[0][0].id == energy_close.id
    assert second[0][0].id == timbre_close.id


def test_diversity_cap_prefers_distinct_artists_and_backfills():
    rows = [(_song("a1", "Same", [1, 0]), 1), (_song("a2", "Same", [1, 0]), 2), (_song("b", "Other", [1, 0]), 3)]
    selected = _apply_diversity_cap(rows, 3)
    assert [row[0].artist for row in selected] == ["Same", "Other", "Same"]


def test_corrupt_legacy_vectors_are_sanitized_before_ranking():
    song = _song("bad", "artist", [1, 0]); song.feature_vector[4] = float("nan")
    feature, _ = _song_vectors(song)
    assert np.isfinite(feature).all()


def test_extracted_features_reject_nan_wrong_dimensions_and_bad_ranges():
    song = Song(id=uuid.uuid4(), title="x", artist="y")
    valid = dict(vector=[0.0] * 32, bpm=120, key="C major", energy=.5, danceability=.5, genre_vector=[0.0] * 1280, genre="Pop", styles="Pop")
    # 1.495 is a real observed Essentia result and must remain valid (normal range is ~0..3).
    apply_extracted_features(song, ExtractedFeatures(**(valid | {"danceability": 1.495})))
    for changes in ({"vector": [0.0] * 31}, {"energy": float("nan")}, {"danceability": 5.0}, {"bpm": 0}):
        with pytest.raises(ValueError): apply_extracted_features(song, ExtractedFeatures(**(valid | changes)))


def test_recommendation_cache_is_scoped_and_explicitly_invalidated():
    user_id = uuid.uuid4()
    key = (user_id, 10, (), False)
    recommendation_cache.put(key, ["result"])
    assert recommendation_cache.get(key) == ["result"]
    recommendation_cache.invalidate_user(user_id)
    assert recommendation_cache.get(key) is None
