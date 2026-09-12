"""Regression tests for app/services/genre_sanity.py's pure, LLM-free helper functions.

reconcile_genre in particular is here because it broke real, live catalog data: a first version
split `styles` on ", " to find the first entry's genre half, which works for every Discogs
top-level genre except "Folk, World, & Country" — the one genre name that itself contains a
comma. That silently truncated 203 real songs' genre to "Folk" before it was caught by manually
inspecting the script's output. A test asserting the exact case below would have caught it
before it ever touched the database.
"""

from app.services.genre_sanity import reconcile_genre, remove_mismatched_entries


def test_reconcile_genre_handles_comma_containing_genre_name():
    # The exact bug: "Folk, World, & Country" contains its own ", ", which a naive
    # styles.split(", ", 1)[0] mistakes for the separator between style entries.
    assert (
        reconcile_genre("Blues", "Folk, World, & Country---Folk, Rock---Pop Rock")
        == "Folk, World, & Country"
    )


def test_reconcile_genre_fixes_orphaned_genre():
    # The real bug this whole module exists to fix: genre pointed at a style entry that was
    # since removed as an implausible cultural mismatch (see backfill_genre_sanity.py).
    assert reconcile_genre("Latin", "Hip Hop---Trap, Hip Hop---Cloud Rap") == "Hip Hop"


def test_reconcile_genre_is_a_noop_when_already_consistent():
    assert reconcile_genre("Pop", "Pop---Vocal, Rock---Pop Rock") == "Pop"


def test_reconcile_genre_uses_majority_of_top_styles():
    assert reconcile_genre("Latin", "Latin---Reggaeton, Pop---Ballad, Pop---Vocal") == "Pop"


def test_reconcile_genre_leaves_genre_alone_with_no_styles():
    assert reconcile_genre("Rock", None) is None or reconcile_genre("Rock", None) == "Rock"
    assert reconcile_genre("Rock", "") == "Rock"


def test_reconcile_genre_leaves_genre_alone_when_styles_dont_match_any_known_genre():
    # Defensive case — shouldn't happen with real Discogs-EffNet output, but reconcile_genre
    # must not crash or invent something if it ever does.
    assert reconcile_genre("Rock", "Not A Real Genre---Whatever") == "Rock"


def test_remove_mismatched_entries_strips_named_entries_only():
    styles = "Folk, World, & Country---Laïkó, Folk, World, & Country---Éntekhno, Pop---Ballad"
    result = remove_mismatched_entries(
        styles, ["Folk, World, & Country---Laïkó", "Folk, World, & Country---Éntekhno"]
    )
    assert result == "Pop---Ballad"


def test_remove_mismatched_entries_returns_none_when_nothing_remains():
    assert remove_mismatched_entries("Pop---Ballad", ["Pop---Ballad"]) is None


def test_reconcile_after_remove_matches_manual_backfill_behavior():
    # End-to-end of the exact sequence backfill_genre_sanity.py and quality_sweep.py both run:
    # remove a flagged entry, then reconcile genre against whatever's left.
    styles = "Latin---Reggaeton, Hip Hop---Trap, Hip Hop---Cloud Rap"
    new_styles = remove_mismatched_entries(styles, ["Latin---Reggaeton"])
    assert new_styles == "Hip Hop---Trap, Hip Hop---Cloud Rap"
    assert reconcile_genre("Latin", new_styles) == "Hip Hop"
