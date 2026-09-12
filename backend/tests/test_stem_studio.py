import json
import uuid
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from app.routers.stems import _valid_youtube_url
from app.schemas import StemJobIn
from app.services import stem_jobs
from app.services.stem_intelligence import MIN_THREAD_SAMPLE, build_xray, interpret_mix_prompt, safe_mix_weights


def test_youtube_allowlist_blocks_http_subdomains_and_arbitrary_fetches():
    assert _valid_youtube_url("https://www.youtube.com/watch?v=abc")
    assert _valid_youtube_url("https://youtu.be/abc")
    assert not _valid_youtube_url("http://youtube.com/watch?v=abc")
    assert not _valid_youtube_url("https://youtube.com.evil.test/watch?v=abc")
    assert not _valid_youtube_url("file:///etc/passwd")


def test_stem_job_schema_rejects_unknown_source_and_model():
    with pytest.raises(ValidationError):
        StemJobIn(source_type="url", youtube_url="https://youtu.be/a", model="4stem", rights_confirmed=True)
    with pytest.raises(ValidationError):
        StemJobIn(source_type="upload", upload_key="x", model="12stem", rights_confirmed=True)


def test_waveform_accepts_float_demucs_output(monkeypatch, tmp_path):
    samples = np.array([0.0, -0.25, 0.5, -1.2, 0.1], dtype="<f4")
    monkeypatch.setattr(
        stem_jobs.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=samples.tobytes()),
    )
    output = tmp_path / "peaks.json"
    stem_jobs._waveform(tmp_path / "stem.wav", output, points=3)
    peaks = json.loads(output.read_text())["peaks"]
    assert peaks
    assert all(0 <= peak <= 1 for peak in peaks)
    assert max(peaks) == 1


def test_probe_enforces_duration_limit(monkeypatch):
    monkeypatch.setattr(
        stem_jobs.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps({"format": {"duration": "901"}})),
    )
    with pytest.raises(stem_jobs.PermanentJobError) as caught:
        stem_jobs._probe(stem_jobs.Path("too-long.wav"))
    assert caught.value.code == "too_long"


def test_youtube_source_name_prefers_music_metadata():
    assert stem_jobs._youtube_source_name({
        "title": "Official Video Title",
        "track": "Midnight City",
        "artist": "M83",
        "uploader": "Some Channel",
    }) == "Midnight City — M83"


def test_youtube_source_name_avoids_duplicate_creator_and_is_bounded():
    assert stem_jobs._youtube_source_name({"title": "M83 - Midnight City", "artist": "M83"}) == "M83 - Midnight City"
    assert len(stem_jobs._youtube_source_name({"title": "x" * 600}) or "") == 500


def test_mix_weights_ignore_unknown_stems_and_normalize():
    analysis = {"stems": {"vocals": {}, "drums": {}}}
    assert safe_mix_weights(analysis, {"vocals": 0.25, "drums": 0.75, "hacker": 99}) == {
        "vocals": 0.25, "drums": 0.75,
    }
    assert safe_mix_weights(analysis, {"vocals": 0, "drums": 0}) == {"vocals": 0.5, "drums": 0.5}


def test_xray_is_honest_when_brain_has_no_analyzed_songs():
    analysis = {"stems": {"vocals": {"feature_vector": [0.0] * 32, "metrics": {"prominence": 1.0}}}}
    result = build_xray(analysis, [])
    assert result["ready"] is False
    assert result["overall_match"] is None
    assert result["stems"][0]["match"] is None


def _stub_song(index: int, vector: list[float]):
    return SimpleNamespace(
        id=uuid.UUID(int=index), title=f"Song {index}", artist=f"Artist {index}", feature_vector=vector
    )


def _stem_scale_map(rng, count: int = 40):
    """A map whose songs all sit at stem-vs-song range from the probe stem — the situation that
    silently produced zero threads when the scoring constants were fitted to whole-song pairs."""
    return [_stub_song(i + 1, list(rng.uniform(0.7, 1.0, 32))) for i in range(count)]


def test_threads_survive_stem_scale_distances():
    """Regression: an isolated stem sits far from every whole song, so any absolute distance
    cutoff fitted to whole-song pairs makes Resonance Threads unreachable for everyone. A song
    that genuinely stands out from the rest of the map must still surface as a thread."""
    rng = np.random.default_rng(7)
    # A tight bulk of unremarkable songs, plus a small handful that genuinely sit closer to
    # this layer — every one of them still at stem-vs-song range (~0.7+), never whole-song range.
    songs = [_stub_song(i + 1, list(rng.normal(0.95, 0.01, 32))) for i in range(60)]
    songs += [_stub_song(900 + i, list(rng.normal(0.86, 0.005, 32))) for i in range(4)]

    result = build_xray(
        {"stems": {"bass": {"feature_vector": [0.0] * 32, "metrics": {"prominence": 0.5}}}},
        songs,
    )
    row = result["stems"][0]
    # The standouts surface as threads, and nothing from the unremarkable bulk sneaks in.
    assert row["threads"], "a genuinely closer set of songs must produce threads"
    assert all(thread["title"].startswith("Song 9") for thread in row["threads"])
    # And the two scales stay independent: this whole fixture sits near the catalog median, so
    # the absolute match is honestly mediocre even though these songs are real outliers *for
    # this layer*. A thread means "unusually close here", never "a great match".
    assert row["match"] is not None and row["match"] < 50


def test_threads_stay_empty_when_no_song_actually_stands_out():
    """The honest half of the same rule: a layer whose nearest neighbours aren't separated from
    the pack must return nothing, rather than promoting whatever happens to sort first."""
    rng = np.random.default_rng(11)
    songs = [_stub_song(i + 1, list(rng.normal(0.85, 0.005, 32))) for i in range(40)]
    result = build_xray(
        {"stems": {"other": {"feature_vector": [0.0] * 32, "metrics": {"prominence": 0.4}}}},
        songs,
    )
    assert result["stems"][0]["threads"] == []


def test_threads_are_withheld_on_a_small_map_but_the_headline_match_is_not():
    """Threads need a distribution to call something an outlier; the headline match doesn't —
    it's an absolute measurement, so it stays valid (and populated) on a tiny map."""
    rng = np.random.default_rng(3)
    songs = [_stub_song(i + 1, list(rng.uniform(0.7, 1.0, 32))) for i in range(MIN_THREAD_SAMPLE - 1)]
    result = build_xray(
        {"stems": {"vocals": {"feature_vector": [0.0] * 32, "metrics": {"prominence": 1.0}}}},
        songs,
    )
    assert result["stems"][0]["threads"] == []
    assert result["stems"][0]["match"] is not None


def test_match_is_an_absolute_scale_not_a_rank_within_the_asking_map():
    """The regression that made overall_match a vanity metric: scoring the headline against the
    asking map's own spread makes the nearest song an outlier by construction, so every map
    scores ~90 regardless of whether it actually contains anything similar. A map full of close
    songs must outscore a map of distant ones — and /compare depends on exactly that."""
    stem = {"stems": {"bass": {"feature_vector": [0.0] * 32, "metrics": {"prominence": 1.0}}}}
    rng = np.random.default_rng(5)
    close = [_stub_song(i + 1, list(rng.normal(0.72, 0.01, 32))) for i in range(30)]
    distant = [_stub_song(i + 1, list(rng.normal(1.30, 0.01, 32))) for i in range(30)]

    close_match = build_xray(stem, close)["overall_match"]
    distant_match = build_xray(stem, distant)["overall_match"]
    assert close_match > distant_match + 25, (close_match, distant_match)
    # The scale never renormalizes to whoever is asking: a map with the same distance profile
    # scores the same whether it holds 10 songs or 30. (A larger *varied* map does legitimately
    # score higher — its closest song really is closer — which is the statistic's meaning, not
    # a leak of map size into the yardstick.)
    assert build_xray(stem, close[:10])["overall_match"] == pytest.approx(close_match, abs=3)


def test_mix_prompt_is_bounded_to_real_available_stems():
    analysis = {"stems": {"vocals": {}, "drums": {}, "bass": {}}}
    weights, explanation = interpret_mix_prompt(
        analysis, "Keep the drums and bass but remove vocals", {"vocals": 1, "drums": 0.4, "bass": 0.4},
    )
    assert weights == {"vocals": 0.0, "drums": 1.0, "bass": 1.0}
    assert "vocals 0%" in explanation


def test_mix_prompt_rejects_non_audio_instructions():
    with pytest.raises(ValueError):
        interpret_mix_prompt({"stems": {"vocals": {}}}, "make it amazing", {"vocals": 1})
