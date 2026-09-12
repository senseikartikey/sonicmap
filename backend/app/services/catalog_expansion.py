"""Grows the shared catalog in the background so recommendations draw from a genuinely broad,
comprehensive pool — the way a real streaming service's recommendations work isn't "look
something up live when you ask," it's "already have virtually everything indexed, so nothing
ever feels limited." Sonicmap can't pre-index the whole music industry, but it can keep
deepening its own catalog continuously, driven by real signal instead of an editorial
top-charts list, so normal usage builds toward the same effect over time. Two complementary
expansion strategies, both triggered as background tasks from GET /recommendations (see
app/routers/recommend.py) so neither ever adds latency to a request:

  - expand_catalog_for_genres: broad coverage. Checks whether the catalog has enough depth in
    the genres already on the requesting user's map (coarse-grained — "Electronic", "Rock") and
    tops it up from iTunes Search if not.
  - expand_catalog_via_similar_artists: targeted, personal discovery. Uses Last.fm's community
    similarity graph to find artists related to the user's *specific* artists (not just their
    genre bucket) that aren't in the catalog yet, and pulls their tracks in — this is what
    makes recommendations feel like "you might also like [artist]" instead of "here's something
    broadly similar-genre." Requires LASTFM_API_KEY (see app/services/lastfm.py); a no-op
    without one, same degrade-gracefully contract as the rest of that module.

  - expand_catalog_via_charts: what's actually new. Apple's own unauthenticated top-songs
    chart (rss.marketingtools.apple.com), refreshed daily — the one strategy not keyed to any
    specific user's taste, so the catalog keeps picking up current/popular releases even for
    users whose own genres and artists never change. Runs at most once per
    CHART_PULL_INTERVAL_HOURS, since the chart itself only updates once a day.
  - expand_catalog_via_lastfm_charts: a second, independent "what's popular right now" signal
    — Last.fm's own global scrobble chart, which is aggregated from actual listening behavior
    rather than Apple's sales/streams, so the two charts rarely agree on the exact same songs.
    Not a replacement for expand_catalog_via_charts; each covers what the other misses. Same
    once-a-day gate, same LASTFM_API_KEY requirement as the similar-artist strategy above.
  - expand_catalog_via_llm: cluster-aware, open-ended discovery. Seeds an LLM with real songs
    from the user's own largest taste-cluster and asks for more that would sonically fit —
    the LLM only ever proposes what to look up, same as any other strategy here; every
    suggestion still goes through the real resolve+extract pipeline. Requires
    GEMINI_API_KEY (see app/services/llm_expansion.py); a no-op without one.

Either way, a song only earns a permanent place in the catalog because it got resolved via
iTunes/Deezer and classified by the same pipeline every other song goes through —
SonicDistance ranking (and the Last.fm blend), not search relevance or which strategy found
it, is what actually surfaces it to any user afterward.
"""

import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import Song
from app.services import itunes, lastfm, llm_expansion
from app.services.extraction import apply_extracted_features, extract_features
from app.services.language import detect_language

MIN_SONGS_PER_GENRE = 30
MAX_NEW_PER_GENRE_PER_CALL = 12

GENRE_SEARCH_TERMS: dict[str, list[str]] = {
    "Blues": ["Blues", "Rhythm and Blues", "Delta Blues"],
    "Brass & Military": ["Brass Band", "Military Band"],
    "Children's": ["Kids Songs", "Nursery Rhymes"],
    "Classical": ["Classical", "Orchestral", "Opera"],
    "Electronic": ["Electronic", "House Music", "Techno", "Drum and Bass", "Dubstep", "Synth-pop"],
    "Folk, World, & Country": ["Folk Music", "Country Music", "World Music", "Celtic Music", "African Music"],
    "Funk / Soul": ["Funk", "Soul Music", "Neo Soul", "Afrobeat", "Rhythm and Blues"],
    "Hip Hop": ["Hip Hop", "Rap", "Trap Music", "Boom Bap", "Conscious Hip Hop"],
    "Jazz": ["Jazz", "Bebop", "Smooth Jazz", "Bossa Nova"],
    "Latin": ["Latin Music", "Reggaeton", "Salsa", "Cumbia", "Bachata"],
    "Non-Music": ["Spoken Word", "Comedy"],
    "Pop": ["Pop Music", "K-pop", "J-pop", "Indie Pop", "Europop"],
    "Reggae": ["Reggae", "Dancehall", "Ska Music", "Dub"],
    "Rock": ["Rock", "Indie Rock", "Punk Rock", "Metal", "Alternative Rock"],
    "Stage & Screen": ["Movie Soundtrack", "Musical Theatre", "Film Score"],
}


async def _add_candidate(db: Session, resolved: itunes.ResolvedTrack) -> bool:
    # Case-insensitive — see the matching comment in app/routers/ingest.py's
    # _get_or_create_song for why an exact-case match isn't enough here.
    existing = db.execute(
        select(Song).where(
            func.lower(Song.title) == resolved.title.lower(),
            func.lower(Song.artist) == resolved.artist.lower(),
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False

    song = Song(
        title=resolved.title,
        artist=resolved.artist,
        preview_url=resolved.preview_url,
        resolution_status="resolved" if resolved.preview_url else "unresolved",
        language=detect_language(resolved.title, resolved.artist),
    )
    db.add(song)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False  # Lost a race with another expansion call resolving the same track.

    if not song.preview_url:
        return True

    try:
        features = await extract_features(song.preview_url)
    except Exception:
        song.extraction_status = "failed"
        db.commit()
        return True

    apply_extracted_features(song, features)
    db.commit()
    # Keep it even if the brain's own classification landed in a different genre than the
    # search term implied — that's real signal (search relevance != Discogs classification),
    # not a reason to throw the track away. It's just already correctly filed for ranking.
    return True


async def expand_catalog_for_genres(genres: set[str]) -> None:
    db = SessionLocal()
    try:
        for genre in genres:
            terms = GENRE_SEARCH_TERMS.get(genre)
            if not terms:
                continue

            count = db.execute(
                select(func.count(Song.id)).where(Song.genre == genre, Song.extraction_status == "extracted")
            ).scalar_one()
            if count >= MIN_SONGS_PER_GENRE:
                continue

            term = random.choice(terms)
            candidates = await itunes.search_tracks(term, limit=25)
            random.shuffle(candidates)

            added = 0
            for candidate in candidates:
                if added >= MAX_NEW_PER_GENRE_PER_CALL:
                    break
                if await _add_candidate(db, candidate):
                    added += 1
    finally:
        db.close()


# Bounds per-trigger work: a request-driven background task, so this stays modest even though
# it runs on essentially every recommendations call.
MAX_NEW_ARTISTS_PER_CALL = 5
SONGS_PER_NEW_ARTIST = 5


async def expand_catalog_via_similar_artists(user_artists: set[str]) -> None:
    """Finds artists related to the user's *specific* artists via Last.fm's community
    similarity graph — not a genre bucket — and pulls a handful of their tracks into the
    catalog if they aren't represented yet. This is the targeted-discovery half of catalog
    growth; expand_catalog_for_genres above stays useful for broad coverage, but genre alone
    can't tell "similar to Bon Iver" from "similar to Nickelback" — both are just "Rock"."""
    if not settings.lastfm_api_key or not user_artists:
        return

    similar_lists = await asyncio.gather(*[lastfm.get_similar_artists(a) for a in user_artists])
    candidate_scores: dict[str, float] = {}
    for similar in similar_lists:
        for name, score in similar.items():
            candidate_scores[name] = max(candidate_scores.get(name, 0.0), score)

    # Strongest community-similarity matches first — those are the artists most likely to
    # actually belong in this user's recommendations once they're in the catalog at all.
    ranked_artists = sorted(candidate_scores.items(), key=lambda kv: kv[1], reverse=True)
    user_artists_lower = {a.lower() for a in user_artists}

    db = SessionLocal()
    try:
        added_artists = 0
        for artist_name, _score in ranked_artists:
            if added_artists >= MAX_NEW_ARTISTS_PER_CALL:
                break
            if artist_name.lower() in user_artists_lower:
                continue  # the user already has this artist directly

            already_have = db.execute(
                select(func.count(Song.id)).where(func.lower(Song.artist) == artist_name.lower())
            ).scalar_one()
            if already_have > 0:
                continue  # already represented — don't keep re-fetching the same artist

            candidates = await itunes.search_tracks(artist_name, limit=SONGS_PER_NEW_ARTIST)
            added_any = False
            for candidate in candidates:
                # iTunes' free-text search can return loosely-related results for an
                # artist-name query — only trust ones actually credited to this artist.
                if artist_name.lower() not in candidate.artist.lower():
                    continue
                if await _add_candidate(db, candidate):
                    added_any = True
            if added_any:
                added_artists += 1
    finally:
        db.close()


# Apple's official, unauthenticated chart feed — no API key, refreshed daily. Verified
# directly against the live endpoint: returns artistName/name/releaseDate/genres per track.
APPLE_TOP_SONGS_URL = "https://rss.marketingtools.apple.com/api/v2/us/music/most-played/100/songs.json"
CHART_PULL_INTERVAL_HOURS = 20  # the feed itself only refreshes once a day
MAX_NEW_FROM_CHART_PER_CALL = 25

# Module-level, not persisted — acceptable at this project's documented single-process scale
# (same assumption app/routers/ingest.py's background tasks already make). A restart just
# means the next /recommendations call re-pulls once more than strictly necessary.
_last_chart_pull: datetime | None = None


async def _fetch_apple_chart_queries() -> list[str]:
    """Returns 'Artist - Title' strings in the same free-text format every other ingestion
    path already uses, ready for itunes.resolve_track. Any failure (network, unexpected feed
    shape) degrades to an empty list rather than raising — this is background, best-effort
    catalog growth, never something a request depends on."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(APPLE_TOP_SONGS_URL)
            resp.raise_for_status()
            results = resp.json()["feed"]["results"]
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        return []
    return [f"{r['artistName']} - {r['name']}" for r in results if r.get("artistName") and r.get("name")]


async def expand_catalog_via_charts() -> None:
    """The one expansion strategy not keyed to any specific user's genres or artists — pulls
    from what's actually new and popular right now, so the catalog keeps growing even for
    users whose own taste signal never changes. Gated to run at most once per
    CHART_PULL_INTERVAL_HOURS regardless of how many /recommendations calls trigger it, since
    re-fetching an unchanged daily chart on every request would be pure waste."""
    global _last_chart_pull
    now = datetime.now(timezone.utc)
    if _last_chart_pull is not None and now - _last_chart_pull < timedelta(hours=CHART_PULL_INTERVAL_HOURS):
        return
    _last_chart_pull = now

    queries = await _fetch_apple_chart_queries()
    if not queries:
        return
    # The chart is pre-ranked by popularity, not by "not yet in our catalog" — sampling
    # instead of always taking the top N means repeated daily pulls eventually cover the
    # whole chart rather than re-checking (and skipping, since _add_candidate dedupes) the
    # same top few tracks forever.
    sample = random.sample(queries, min(MAX_NEW_FROM_CHART_PER_CALL, len(queries)))

    db = SessionLocal()
    try:
        for query in sample:
            resolved = await itunes.resolve_track(query)
            if resolved is not None:
                await _add_candidate(db, resolved)
    finally:
        db.close()


LASTFM_CHART_PULL_INTERVAL_HOURS = 20  # same rationale as CHART_PULL_INTERVAL_HOURS above
MAX_NEW_FROM_LASTFM_CHART_PER_CALL = 25

_last_lastfm_chart_pull: datetime | None = None


async def expand_catalog_via_lastfm_charts() -> None:
    """Last.fm's global scrobble chart — a second, independently-aggregated "what's popular
    right now" signal alongside expand_catalog_via_charts' Apple data. Same shape, same
    once-a-day gate, same sample-not-top-N rationale; kept as a fully separate function (not a
    parameterized variant of expand_catalog_via_charts) since the two pull from different
    services with different response shapes and failure modes — collapsing them would mean one
    source's outage silently starving the other's schedule too."""
    global _last_lastfm_chart_pull
    now = datetime.now(timezone.utc)
    if (
        _last_lastfm_chart_pull is not None
        and now - _last_lastfm_chart_pull < timedelta(hours=LASTFM_CHART_PULL_INTERVAL_HOURS)
    ):
        return
    _last_lastfm_chart_pull = now

    top_tracks = await lastfm.get_global_top_tracks(limit=100)
    if not top_tracks:
        return
    sample = random.sample(top_tracks, min(MAX_NEW_FROM_LASTFM_CHART_PER_CALL, len(top_tracks)))

    db = SessionLocal()
    try:
        for artist, title in sample:
            resolved = await itunes.resolve_track(f"{artist} - {title}")
            if resolved is not None:
                await _add_candidate(db, resolved)
    finally:
        db.close()


MAX_NEW_FROM_LLM_PER_CALL = 12
LLM_EXPANSION_INTERVAL_HOURS = 6  # bounds API spend for a single user hammering /recommendations

# Per-user, not global (unlike _last_chart_pull) — this strategy is personalized, so throttling
# has to be per user too. Module-level and not persisted, same single-process assumption as
# _last_chart_pull above.
_last_llm_pull: dict[uuid.UUID, datetime] = {}


async def expand_catalog_via_llm(user_id: uuid.UUID, cluster_seed_songs: list[tuple[str, str]]) -> None:
    """cluster_seed_songs: (title, artist) pairs from the user's own largest real taste-
    cluster (see app/routers/recommend.py) — the only expansion strategy that can target
    'more like *this specific corner* of this user's map' instead of a genre bucket or a
    community-wide similarity graph. See llm_expansion.py for how suggestions are generated
    and why they're never trusted as feature data."""
    if not settings.gemini_api_key or not cluster_seed_songs:
        return

    now = datetime.now(timezone.utc)
    last = _last_llm_pull.get(user_id)
    if last is not None and now - last < timedelta(hours=LLM_EXPANSION_INTERVAL_HOURS):
        return
    _last_llm_pull[user_id] = now

    suggestions = await llm_expansion.suggest_similar_songs(cluster_seed_songs)
    if not suggestions:
        return

    db = SessionLocal()
    try:
        for artist, title in suggestions[:MAX_NEW_FROM_LLM_PER_CALL]:
            resolved = await itunes.resolve_track(f"{artist} - {title}")
            if resolved is not None:
                await _add_candidate(db, resolved)
    finally:
        db.close()
