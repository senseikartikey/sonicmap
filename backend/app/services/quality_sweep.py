"""Runs the same LLM-assisted data-quality passes the one-off backfill scripts apply
catalog-wide (app/scripts/backfill_language.py, backfill_genre_sanity.py,
backfill_artist_canonical.py) — but scoped to a specific, just-ingested set of songs, and
triggered automatically right after extraction instead of needing someone to notice something
looks wrong and remember to run a script.

This is the actual fix for a pattern that kept recurring this session: language/genre/artist
bugs weren't happening because the LLM checks don't work — they were happening because those
checks only ever ran when someone manually invoked a script against the whole catalog. A song
ingested five minutes after the last manual run got none of that scrutiny — it sat there with
only fastText's free first-pass language guess, an unreconciled genre, and no canonical artist —
until someone (a user) noticed it looked wrong and asked again. Wiring this sweep into the same
post-extraction background job that already runs after every ingest (see
app/routers/ingest.py's _resolve_and_extract_many, and the fire_and_forget call from /search's
single-song path) closes that gap: every song gets the same treatment the backfill scripts give
the catalog, without a human having to remember to trigger it.

Best-effort throughout, same degrade-gracefully contract as every LLM service this catalog
already relies on: a network hiccup or empty Gemini response for one pass just means that song
keeps whatever it already had (raw fastText language, unreconciled genre, no canonical artist)
until the next sweep — never raises, never undoes the extraction that already succeeded. The
whole-catalog backfill scripts remain the way to sweep up anything ingested *before* this existed,
or any row a sweep call failed on.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Song
from app.services.artist_identity import canonicalize_artists
from app.services.genre_sanity import find_mismatched_styles, reconcile_genre, remove_mismatched_entries
from app.services.language import classify_languages_via_llm, needs_language_review


async def run_quality_sweep(
    db: Session, song_ids: list[uuid.UUID], *, review_all_languages: bool = False
) -> None:
    """Call once per batch of just-ingested songs (after extraction has run, so genre/styles/
    feature_vector are populated) — not once per song, for the same batching-cost reason the
    backfill scripts batch their Gemini calls. Commits its own changes; does not close `db`
    (callers already manage that session's lifecycle)."""
    songs = db.execute(select(Song).where(Song.id.in_(song_ids))).scalars().all()
    if not songs:
        return
    by_id = {str(s.id): s for s in songs}

    # --- Language: escalate whatever fastText's free first pass (already applied at ingest
    # time — see ingest.py's detect_language calls) left unresolved. Same selection criteria as
    # backfill_language.py's pass 2. ---
    unresolved_lang = [
        (str(s.id), s.title, s.artist)
        for s in songs
        if review_all_languages
        or s.language is None
        or needs_language_review(s.title, s.artist, s.language)
    ]
    # Fail closed for strict-English recommendations even if the optional LLM is unavailable.
    for song in songs:
        if review_all_languages or needs_language_review(song.title, song.artist, song.language):
            song.language = None
    if unresolved_lang:
        llm_lang_results = await classify_languages_via_llm(unresolved_lang)
        for song_id, lang in llm_lang_results.items():
            song = by_id.get(song_id)
            if song is not None and lang is not None:
                song.language = lang

    # --- Genre sanity + reconciliation: same as backfill_genre_sanity.py, scoped to this
    # batch. Only songs with real extracted styles have anything to check. ---
    genre_batch = [
        (str(s.id), s.title, s.artist, s.styles)
        for s in songs
        if s.feature_vector is not None and s.styles
    ]
    if genre_batch:
        flagged = await find_mismatched_styles(genre_batch)
        for song_id, mismatched in flagged.items():
            song = by_id.get(song_id)
            if song is None or not song.styles:
                continue
            new_styles = remove_mismatched_entries(song.styles, mismatched)
            if new_styles is None or new_styles == song.styles:
                continue
            song.styles = new_styles
            song.genre = reconcile_genre(song.genre, new_styles)

    # --- Artist canonicalization: same as backfill_artist_canonical.py, scoped to this batch. ---
    artist_batch = [
        (str(s.id), s.artist)
        for s in songs
        if s.feature_vector is not None and s.artist_canonical is None
    ]
    if artist_batch:
        artist_results = await canonicalize_artists(artist_batch)
        for song_id, names in artist_results.items():
            song = by_id.get(song_id)
            if song is not None and names:
                song.artist_canonical = " | ".join(names)

    db.commit()
