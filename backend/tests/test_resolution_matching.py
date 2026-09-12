from app.services.itunes import _best_title_match, _looks_like_same_artist


def test_artist_match_accepts_condensed_multi_artist_credit() -> None:
    assert _looks_like_same_artist("Pritam, Arijit Singh & Amitabh Bhattacharya", "Arijit Singh")
    assert _looks_like_same_artist("The Beatles", "Beatles")


def test_artist_match_rejects_different_artist_with_same_title() -> None:
    results = [{"trackName": "Angel", "artistName": "Massive Attack"}]
    assert _best_title_match(results, "Angel", "Shaggy") is None


def test_best_match_skips_wrong_artist_and_uses_valid_result() -> None:
    results = [
        {"trackName": "Creep", "artistName": "Stone Temple Pilots"},
        {"trackName": "Creep", "artistName": "Radiohead"},
    ]
    assert _best_title_match(results, "Creep", "Radiohead") == results[1]
