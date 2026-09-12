import asyncio
import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal, get_db
from app.dependencies import get_current_user, get_valid_spotify_token
from app.models import Song, User, UserSong
from app.schemas import (
    IngestPasteRequest,
    IngestResult,
    IngestSearchRequest,
    IngestSpotifyPlaylistRequest,
    SearchSuggestion,
    SongOut,
)
from app.services import itunes, spotify_auth
from app.services.background import fire_and_forget
from app.services.brain import rebuild_user_map_background, schedule_rebuild, trigger_rebuild
from app.services.catalog_identity import record_external_ref
from app.services.extraction import apply_extracted_features, extract_features
from app.services.language import detect_language
from app.services.paste_parser import parse_pasted_songs
from app.services.quality_sweep import run_quality_sweep
from app.services import recommendation_cache

router = APIRouter(prefix="/ingest", tags=["ingest"])
logger = logging.getLogger("sonicmap.ingest")

# Sanity ceiling on a single bulk ingest (paste or playlist import). Processing itself is
# now backgrounded (see _resolve_and_extract), so this isn't guarding a request timeout
# anymore — it's just a bound on how much work one request can queue up at once.
MAX_BULK_SONGS = 200


async def _get_or_create_song(db: Session, query: str) -> tuple[Song, bool]:
    """Synchronous resolve-and-extract for a single song, used only by /search — one
    iTunes + one Essentia call is fast enough to do inline. Bulk paths use the
    create-placeholder-then-background-process split below instead."""
    resolved = await itunes.resolve_track(query)
    if resolved is None:
        # Unresolved rows share the same (title, artist="Unknown") unique constraint as
        # resolved songs, so retrying an identical failed query must dedupe here too —
        # without this check it hits uq_songs_title_artist and 500s on the second attempt.
        existing = db.execute(
            select(Song).where(func.lower(Song.title) == query.lower(), Song.artist == "Unknown")
        ).scalar_one_or_none()
        if existing is not None:
            return existing, False
        song = Song(title=query, artist="Unknown", resolution_status="unresolved")
        db.add(song)
        db.commit()
        db.refresh(song)
        return song, True

    # Case-insensitive: iTunes/Deezer don't return perfectly consistent capitalization for
    # the same underlying recording across different search queries ("Rhythm and Blues" vs
    # "Rhythm And Blues") — an exact-case match let real duplicates slip past this check and
    # both get independently resolved and extracted.
    existing = db.execute(
        select(Song).where(
            func.lower(Song.title) == resolved.title.lower(),
            func.lower(Song.artist) == resolved.artist.lower(),
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    song = Song(
        title=resolved.title,
        artist=resolved.artist,
        preview_url=resolved.preview_url,
        duration_ms=resolved.duration_ms,
        resolution_status="resolved" if resolved.preview_url else "unresolved",
        language=detect_language(resolved.title, resolved.artist),
    )
    db.add(song)
    db.commit()
    db.refresh(song)
    record_external_ref(db, song.id, resolved)
    db.commit()

    if song.preview_url:
        try:
            features = await extract_features(song.preview_url)
            apply_extracted_features(song, features)
        except Exception:
            song.extraction_status = "failed"
        db.commit()
        db.refresh(song)

    return song, True


def _create_pending_placeholder(db: Session, query: str) -> tuple[Song, bool, bool]:
    """Fast, synchronous half of bulk ingest: reserves a row for `query` (or reuses an
    existing pending/unresolved one for the same raw text) without calling iTunes or
    Essentia, so the request can return immediately. The slow work happens afterward in
    _resolve_and_extract, off the request/response path — the frontend already polls
    GET /map and shows resolution/extraction status per song, so this needs no separate
    job-status API."""
    existing = db.execute(
        select(Song).where(func.lower(Song.title) == query.lower(), Song.artist == "Unknown")
    ).scalar_one_or_none()
    if existing is not None:
        # A provider outage must not make a raw query permanently unresolvable. A later import
        # retries unresolved/failed placeholders while still reporting that the row is not new.
        needs_processing = existing.resolution_status in {"unresolved", "pending"}
        if existing.resolution_status == "unresolved":
            existing.resolution_status = "pending"
            db.commit()
        return existing, False, needs_processing
    song = Song(title=query, artist="Unknown", resolution_status="pending")
    db.add(song)
    try:
        db.commit()
    except IntegrityError:
        # Concurrent identical requests can both pass the pre-check. The unique index is the
        # final authority; recover by loading the winner instead of surfacing a 500.
        db.rollback()
        winner = db.execute(
            select(Song).where(func.lower(Song.title) == query.lower(), Song.artist == "Unknown")
        ).scalar_one()
        return winner, False, winner.resolution_status == "pending"
    db.refresh(song)
    return song, True, True


async def _resolve_and_extract(song_id: uuid.UUID) -> None:
    """Background task: resolves + extracts a placeholder row created by
    _create_pending_placeholder. Runs after the response is sent, so it opens its own DB
    session rather than reusing the request-scoped one, which is already closed by then.

    Still does blocking DB calls inside an async function (same as the rest of this
    router) — fine for this project's single-process scale, and no worse than before;
    the fix here is moving the work off the request path, not making it non-blocking."""
    db = SessionLocal()
    try:
        song = db.get(Song, song_id)
        if song is None or song.resolution_status != "pending":
            return  # Already handled — e.g. a duplicate placeholder from a racing request.

        resolved = await itunes.resolve_track(song.title)
        if resolved is None:
            song.resolution_status = "unresolved"
            db.commit()
            return

        canonical = db.execute(
            select(Song).where(
                func.lower(Song.title) == resolved.title.lower(),
                func.lower(Song.artist) == resolved.artist.lower(),
                Song.id != song.id,
            )
        ).scalar_one_or_none()

        if canonical is not None:
            # A different request already resolved this exact song first. Renaming this
            # placeholder to match would violate uq_songs_title_artist, so merge into the
            # existing canonical row instead: repoint this song's UserSong links, dropping
            # any that would collide with a link the user already has via the same source.
            placeholder_links = db.execute(
                select(UserSong).where(UserSong.song_id == song.id)
            ).scalars().all()
            for link in placeholder_links:
                collision = db.execute(
                    select(UserSong).where(
                        UserSong.user_id == link.user_id,
                        UserSong.song_id == canonical.id,
                        UserSong.source == link.source,
                    )
                ).scalar_one_or_none()
                if collision is not None:
                    db.delete(link)
                else:
                    # Must go through the relationship, not the raw song_id column: Song and
                    # UserSong are back_populates-linked, and without cascade/passive_deletes
                    # configured, SQLAlchemy's default delete behavior reloads song.user_songs
                    # at flush time and nulls out any FK still pointing at the row being
                    # deleted below — silently clobbering a raw column assignment made here.
                    link.song = canonical
            db.delete(song)
            db.commit()
            song = canonical
        else:
            song.title = resolved.title
            song.artist = resolved.artist
            song.preview_url = resolved.preview_url
            song.duration_ms = resolved.duration_ms or song.duration_ms
            song.resolution_status = "resolved" if resolved.preview_url else "unresolved"
            song.language = detect_language(resolved.title, resolved.artist)
            record_external_ref(db, song.id, resolved)
            db.commit()

        if song.preview_url and song.extraction_status in {"pending", "failed"}:
            song.extraction_status = "pending"
            try:
                features = await extract_features(song.preview_url)
                apply_extracted_features(song, features)
            except Exception:
                logger.exception("Extraction failed for %s by %s (%s)", song.title, song.artist, song.id)
                song.extraction_status = "failed"
            db.commit()
    finally:
        db.close()


# Scheduling one _resolve_and_extract call per song (33 separate fire_and_forget calls for a
# 33-song import) would mean 33 songs each paying their own audio download + Essentia
# extraction cost (~15-30s) totally independently of each other — fine for pool-exhaustion
# purposes (fire_and_forget doesn't hold a request connection open either way), but wasteful
# of extraction throughput. This batches a whole bulk import into one background call that
# runs its songs concurrently instead (bounded, so a 100-song import doesn't try to open 100
# simultaneous audio downloads + TensorFlow inference calls at once) — same semaphore-bounded-
# gather pattern already proven in app/scripts/seed_catalog.py and
# app/services/catalog_expansion.py.
BULK_EXTRACTION_CONCURRENCY = 4


async def _resolve_and_extract_many(user_id: uuid.UUID, song_ids: list[uuid.UUID]) -> None:
    semaphore = asyncio.Semaphore(BULK_EXTRACTION_CONCURRENCY)

    async def _one(song_id: uuid.UUID) -> None:
        async with semaphore:
            await _resolve_and_extract(song_id)

    outcomes = await asyncio.gather(*[_one(song_id) for song_id in song_ids], return_exceptions=True)
    failed_ids: list[uuid.UUID] = []
    for song_id, outcome in zip(song_ids, outcomes):
        if isinstance(outcome, Exception):
            logger.error("Background ingest failed for song %s: %r", song_id, outcome)
            failed_ids.append(song_id)

    # Never leave an unexpected failure looking like active work forever. Mark it retryable;
    # a later ingest moves unresolved -> pending and gives the provider another chance.
    if failed_ids:
        failure_db = SessionLocal()
        try:
            for failed in failure_db.execute(select(Song).where(Song.id.in_(failed_ids))).scalars():
                if failed.resolution_status == "pending":
                    failed.resolution_status = "unresolved"
            failure_db.commit()
        finally:
            failure_db.close()

    # Runs the same LLM-assisted language/genre/artist quality passes the one-off backfill
    # scripts apply catalog-wide (see app/services/quality_sweep.py), scoped to just this
    # batch, right here rather than leaving it to whoever next remembers to run a script. This
    # is what stops a newly-ingested song from sitting with an unescalated language guess, an
    # unreconciled genre, or no canonical artist until someone notices it looks wrong.
    db = SessionLocal()
    try:
        try:
            # Every user-ingested track gets metadata-aware language review, even when the
            # cheap title classifier confidently said English. This catches English-looking
            # titles by non-English artists without maintaining an impossible artist list.
            await run_quality_sweep(db, song_ids, review_all_languages=True)
        except Exception:
            # Quality enrichment is valuable but optional; placement must still happen when
            # Gemini/a metadata provider is unavailable.
            logger.exception("Quality sweep failed for ingest batch; continuing to map rebuild")
    finally:
        db.close()

    # Batches the rebuild trigger to fire once after the whole import settles, not once per
    # song — public users shouldn't need to know "click Rebuild after adding songs" is a step
    # at all. Already running off the request path (this whole function is itself scheduled
    # via fire_and_forget), so this can just await the rebuild directly rather than scheduling
    # yet another background task.
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user is not None and trigger_rebuild(db, user):
            await rebuild_user_map_background(user_id)
    finally:
        db.close()


def _link_user_song(
    db: Session, user_id: uuid.UUID, song_id: uuid.UUID, source: str, source_ref: str | None
) -> bool:
    """Returns whether a new link was actually created — a pre-existing catalog song getting
    linked to this user's map for the first time needs a rebuild just as much as a brand-new
    song does, even though no resolution/extraction work is required for it."""
    existing = db.execute(
        select(UserSong).where(
            UserSong.user_id == user_id, UserSong.song_id == song_id, UserSong.source == source
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    db.add(UserSong(user_id=user_id, song_id=song_id, source=source, source_ref=source_ref))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    return True


@router.get("/search/suggestions", response_model=list[SearchSuggestion])
async def ingest_search_suggestions(
    q: str, user: User = Depends(get_current_user)
) -> list[SearchSuggestion]:
    """Typeahead for the search tab — same iTunes source ingest/search itself resolves
    against, just returning the raw candidate list instead of collapsing to one match, so the
    user can see (and pick) the actual title/artist before committing to an ingest call."""
    q = q.strip()
    if len(q) < 2:
        return []
    tracks = await itunes.search_tracks(q, limit=6)
    seen: set[tuple[str, str]] = set()
    suggestions: list[SearchSuggestion] = []
    for t in tracks:
        key = (t.title, t.artist)
        if key in seen:
            continue
        seen.add(key)
        suggestions.append(SearchSuggestion(title=t.title, artist=t.artist))
    return suggestions


async def _quality_sweep_one(song_id: uuid.UUID) -> None:
    """fire_and_forget wrapper for the single-song /search path — opens its own session rather
    than reusing the request's (see app/services/background.py's docstring for why that
    matters), same pattern as _resolve_and_extract above."""
    db = SessionLocal()
    try:
        await run_quality_sweep(db, [song_id], review_all_languages=True)
    finally:
        db.close()


@router.post("/search", response_model=IngestResult)
async def ingest_search(
    payload: IngestSearchRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> IngestResult:
    query = payload.query.strip()
    if len(query) < 2:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter at least two visible characters")
    # Keep the request path provider/CPU-free, exactly like paste/playlist imports. Resolution,
    # extraction, quality checks, and map rebuild happen in the bounded background batch.
    song, was_new, needs_processing = _create_pending_placeholder(db, query)
    _link_user_song(db, user.id, song.id, source="search", source_ref=None)
    recommendation_cache.invalidate_user(user.id)
    if needs_processing:
        fire_and_forget(_resolve_and_extract_many(user.id, [song.id]))
    elif song.feature_vector is not None:
        schedule_rebuild(db, user)
    return IngestResult(song=SongOut.model_validate(song), was_new=was_new)


@router.post("/paste", response_model=list[IngestResult])
async def ingest_paste(
    payload: IngestPasteRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[IngestResult]:
    raw_lines = [line.strip() for line in payload.raw_text.splitlines() if line.strip()]
    lines = list(dict.fromkeys(line.casefold() for line in raw_lines))
    original_by_fold = {line.casefold(): line for line in raw_lines}
    lines = [original_by_fold[line] for line in lines]
    if not lines:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Paste at least one song")
    if len(lines) > MAX_BULK_SONGS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Paste up to {MAX_BULK_SONGS} songs at a time (got {len(lines)}). Split larger lists into batches.",
        )

    # Best-effort cleanup of the raw paste (numbered lists, inconsistent separators, "Title -
    # Artist" order, tracklist noise) into clean query strings — see paste_parser.py. Only
    # trusted when it returns something the same rough size as the naive split; if Gemini
    # dropped, merged, or invented enough entries to meaningfully change the count, the naive
    # per-line split is the safer fallback, not a hallucinated list.
    try:
        # LLM cleanup is optional polish, never permission to hold "Add to map" hostage to a
        # slow provider. The deterministic line parser remains the safe fallback.
        parsed = await asyncio.wait_for(parse_pasted_songs(payload.raw_text), timeout=2.0)
    except (TimeoutError, asyncio.TimeoutError):
        parsed = None
    if parsed is not None and abs(len(parsed) - len(lines)) <= max(2, len(lines) // 10):
        parsed_by_fold = {line.strip().casefold(): line.strip() for line in parsed if line.strip()}
        lines = list(parsed_by_fold.values())

    results: list[IngestResult] = []
    new_song_ids: list[uuid.UUID] = []
    any_new_link = False
    for line in lines:
        song, was_new, needs_processing = _create_pending_placeholder(db, line)
        if _link_user_song(db, user.id, song.id, source="paste", source_ref=payload.source_ref):
            any_new_link = True
        if needs_processing:
            new_song_ids.append(song.id)
        results.append(IngestResult(song=SongOut.model_validate(song), was_new=was_new))
    if new_song_ids:
        # Rebuild is triggered at the end of this, after resolution/extraction finishes.
        fire_and_forget(_resolve_and_extract_many(user.id, new_song_ids))
    elif any_new_link:
        # Nothing needed resolving (every song already existed in the catalog), but at least
        # one was newly linked to this user's map — that still needs a rebuild to place it.
        schedule_rebuild(db, user)
    recommendation_cache.invalidate_user(user.id)
    return results


@router.post("/spotify-playlist", response_model=list[IngestResult])
async def ingest_spotify_playlist(
    payload: IngestSpotifyPlaylistRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[IngestResult]:
    access_token = await get_valid_spotify_token(user, db)
    try:
        tracks = await spotify_auth.fetch_playlist_tracks(access_token, payload.playlist_id_or_url)
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (403, 404):
            # The common real-world cause: a private playlist not owned by this account, a
            # deleted playlist, or (since this app is in Spotify's unapproved Development Mode)
            # a playlist belonging to a Spotify account not added to the app's allowed-users
            # list in the developer dashboard — Spotify returns a bare 403 either way, no
            # detail to distinguish them, so the message covers the whole family of causes.
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Can't access this playlist — it may be private, deleted, or not visible to "
                "this Spotify account. Try a public playlist, or one you own.",
            )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Spotify didn't respond as expected — try again in a moment."
        )

    if len(tracks) > MAX_BULK_SONGS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"This playlist has {len(tracks)} tracks — import up to {MAX_BULK_SONGS} at a time.",
        )

    results: list[IngestResult] = []
    new_song_ids: list[uuid.UUID] = []
    any_new_link = False
    for track in tracks:
        query = f"{track.artist} - {track.title}"
        song, was_new, needs_processing = _create_pending_placeholder(db, query)
        if _link_user_song(
            db, user.id, song.id, source="spotify_playlist", source_ref=payload.playlist_id_or_url
        ):
            any_new_link = True
        if needs_processing:
            new_song_ids.append(song.id)
        results.append(IngestResult(song=SongOut.model_validate(song), was_new=was_new))
    if new_song_ids:
        fire_and_forget(_resolve_and_extract_many(user.id, new_song_ids))
    elif any_new_link:
        schedule_rebuild(db, user)
    recommendation_cache.invalidate_user(user.id)
    return results
