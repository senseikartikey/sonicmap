"""Community listening-behavior signal via Last.fm's artist.getSimilar / track.getSimilar —
the collaborative "people with taste like yours also like this" signal sonicmap otherwise has
no way to get: Spotify locked down related-artists/audio-features access for new apps, and
sonicmap doesn't have anywhere near the multi-user listener base a first-party collaborative
signal would need. Last.fm's similarity comes from aggregate listener/tag co-occurrence across
its own large user base — free, and (unlike Spotify) needs only a static app API key, no
OAuth/user login.

Originally this project also planned a Song2Vec embedding trained on Spotify's Million
Playlist Dataset for a track-co-occurrence signal — that dataset turned out to no longer be
freely downloadable (AIcrowd now requires a binding agreement directly with Spotify Research
that explicitly forbids redistribution, so the unofficial Kaggle/GitHub mirrors floating
around aren't a legitimate source). track_affinity_scores() below is the substitute: Last.fm's
track.getSimilar gives a comparable track-to-track "listeners of X also listen to Y" signal
using infrastructure already wired up here, no new data acquisition required.

Every function here degrades to a no-op when LASTFM_API_KEY isn't configured (see
app/config.py) — this is optional infrastructure the recommender blends in when present, not a
hard dependency. See app/services/brain.py for how the resulting scores get combined with
SonicDistance.
"""

import asyncio
import re

import httpx

from app.config import settings

LASTFM_BASE_URL = "https://ws.audioscrobbler.com/2.0/"

_VARIANT_SUFFIX = re.compile(r"\s*[\(\[][^)\]]*[\)\]]\s*$")


def _strip_variant_suffix(title: str) -> str:
    """Strips a single trailing parenthetical/bracketed suffix — "(Taylor's Version)", "(feat.
    X)", "(On Vacation Version)", "[Remix]" — so the Last.fm query and the local-catalog match
    key both use each song's canonical title. iTunes titles carry these suffixes far more
    aggressively than Last.fm's own naming does, which silently kills real matches: querying
    sonicmap's exact catalog title "Espresso (On Vacation Version)" returns nothing, while
    plain "Espresso" correctly surfaces "The Fate of Ophelia" at 0.54 similarity — a track
    already sitting in sonicmap's own catalog."""
    stripped = _VARIANT_SUFFIX.sub("", title).strip()
    return stripped if stripped else title  # never return an empty title

# Process-lifetime cache, no TTL: artist-similarity relationships don't meaningfully change
# within a dev session, and this is what keeps a request with N distinct user-artists from
# re-hitting Last.fm on every single recommendation call.
_similar_artists_cache: dict[str, dict[str, float]] = {}


async def get_similar_artists(artist: str, limit: int = 20) -> dict[str, float]:
    """Returns {artist_name: match_score}, match_score being Last.fm's own [0, 1] similarity
    score. Empty dict on a missing key, an unknown artist, or any request failure — callers
    treat "no data" and "no similarity" identically, which is the correct degrade-gracefully
    behavior for an optional signal."""
    if not settings.lastfm_api_key:
        return {}
    if artist in _similar_artists_cache:
        return _similar_artists_cache[artist]

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(
                LASTFM_BASE_URL,
                params={
                    "method": "artist.getSimilar",
                    "artist": artist,
                    "api_key": settings.lastfm_api_key,
                    "format": "json",
                    "limit": limit,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return {}

    similar = data.get("similarartists", {}).get("artist", [])
    result = {a["name"]: float(a.get("match", 0)) for a in similar if a.get("name")}
    _similar_artists_cache[artist] = result
    return result


# Bounds worst-case request latency: querying Last.fm for every distinct artist on a large
# map would be a lot of sequential-feeling network round trips even parallelized.
MAX_ARTISTS_PER_REQUEST = 15


async def artist_affinity_scores(user_artists: set[str], candidate_artists: set[str]) -> dict[str, float]:
    """Best similarity from any of the user's own artists to each candidate artist. A
    candidate by an artist the user already has scores 1.0 (maximal affinity) without needing
    a network call — the same-artist case is trivially "similar". Returns {} untouched
    (meaning: contribute nothing to blended ranking) when no API key is configured."""
    if not settings.lastfm_api_key:
        return {}

    query_artists = list(user_artists)[:MAX_ARTISTS_PER_REQUEST]
    similar_lists = await asyncio.gather(*[get_similar_artists(a) for a in query_artists])

    scores: dict[str, float] = {}
    for candidate_artist in candidate_artists:
        if candidate_artist in user_artists:
            scores[candidate_artist] = 1.0

    for similar in similar_lists:
        for candidate_artist in candidate_artists:
            if candidate_artist in scores and scores[candidate_artist] == 1.0:
                continue
            match = similar.get(candidate_artist)
            if match is not None:
                scores[candidate_artist] = max(scores.get(candidate_artist, 0.0), match)

    return scores


def track_key(artist: str, title: str) -> tuple[str, str]:
    return artist.strip().lower(), _strip_variant_suffix(title).lower()


_similar_tracks_cache: dict[tuple[str, str], dict[tuple[str, str], float]] = {}


async def get_similar_tracks(artist: str, title: str, limit: int = 20) -> dict[tuple[str, str], float]:
    """Returns {(artist_lower, title_lower): match_score}. Same degrade-gracefully contract as
    get_similar_artists: empty dict on a missing key, an unresolved track, or any failure."""
    if not settings.lastfm_api_key:
        return {}
    key = track_key(artist, title)
    if key in _similar_tracks_cache:
        return _similar_tracks_cache[key]

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(
                LASTFM_BASE_URL,
                params={
                    "method": "track.getSimilar",
                    "artist": artist,
                    "track": _strip_variant_suffix(title),
                    "api_key": settings.lastfm_api_key,
                    "format": "json",
                    "limit": limit,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return {}

    tracks = data.get("similartracks", {}).get("track", [])
    result: dict[tuple[str, str], float] = {}
    for t in tracks:
        name = t.get("name")
        artist_name = (t.get("artist") or {}).get("name")
        if name and artist_name:
            result[track_key(artist_name, name)] = float(t.get("match", 0))
    _similar_tracks_cache[key] = result
    return result


MAX_TRACKS_PER_REQUEST = 15


async def track_affinity_scores(
    user_songs: list[tuple[str, str]], candidate_songs: list[tuple[str, str]]
) -> dict[tuple[str, str], float]:
    """Same shape as artist_affinity_scores, one level more specific: best track-to-track
    Last.fm similarity from any of the user's own (artist, title) pairs to each candidate's.
    Matching is on lowercased exact (artist, title) — a real limit (a remix suffix or "feat."
    formatting difference won't match), but this is a supplementary signal layered on top of
    SonicDistance, not the primary ranking; a missed match just means that candidate doesn't
    get this particular boost, same "no data == no signal" degrade-gracefully contract as
    everywhere else in this module."""
    if not settings.lastfm_api_key:
        return {}

    query_songs = user_songs[:MAX_TRACKS_PER_REQUEST]
    similar_lists = await asyncio.gather(*[get_similar_tracks(a, t) for a, t in query_songs])

    candidate_keys = {track_key(a, t) for a, t in candidate_songs}
    user_keys = {track_key(a, t) for a, t in user_songs}

    scores: dict[tuple[str, str], float] = {}
    for key in candidate_keys:
        if key in user_keys:
            scores[key] = 1.0

    for similar in similar_lists:
        for key in candidate_keys:
            if key in scores and scores[key] == 1.0:
                continue
            match = similar.get(key)
            if match is not None:
                scores[key] = max(scores.get(key, 0.0), match)

    return scores


async def get_global_top_tracks(limit: int = 50) -> list[tuple[str, str]]:
    """chart.getTopTracks — Last.fm's own global "what's actually playing right now" signal,
    a second independent source alongside Apple's charts (app/services/catalog_expansion.py's
    expand_catalog_via_charts) rather than a replacement for it: different aggregation (Last.fm
    scrobbles vs. Apple's sales/streams), so the two rarely agree on the exact same songs,
    which is the point — each one covers what the other misses. Returns (artist, title) pairs,
    ready for itunes.resolve_track. Verified directly against the live endpoint: no auth beyond
    the existing static app key, real current chart data."""
    if not settings.lastfm_api_key:
        return []

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(
                LASTFM_BASE_URL,
                params={
                    "method": "chart.gettoptracks",
                    "api_key": settings.lastfm_api_key,
                    "format": "json",
                    "limit": limit,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

    tracks = data.get("tracks", {}).get("track", [])
    return [
        (t["artist"]["name"], t["name"])
        for t in tracks
        if t.get("name") and (t.get("artist") or {}).get("name")
    ]
