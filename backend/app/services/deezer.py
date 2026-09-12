"""Deezer's public catalog API — a second, differently-shaped window into roughly the same
commercial catalog as iTunes Search, used as app.services.itunes.resolve_track's fallback when
iTunes has no match for a query. No API key or OAuth needed for search/track lookups (verified
directly against the live endpoint). Deezer's own `bpm` field is unreliable in practice
(frequently 0) and isn't used here — this is only ever a source of a real, playable preview
clip for sonicmap's own Essentia extraction to run against, never a shortcut around it.

Preview URLs are signed and short-lived (~1hr) — callers should extract features from them
promptly rather than caching the URL itself for later use. That warning went unheeded: the URL
was stored on `songs.preview_url` anyway, and 2,042 rows (~9% of the analysable catalog) had
quietly rotted to a permanent 403 by the time anyone tried to play one. Anything that needs to
*play* a Deezer-sourced clip must re-resolve it first — see catalog.py's refresh-preview
endpoint, which the mix player calls automatically when a stored link fails.
"""

import httpx

from app.services.resolved_track import ResolvedTrack

DEEZER_SEARCH_URL = "https://api.deezer.com/search"


def _duration_ms(result: dict) -> int | None:
    """Deezer reports track length in whole *seconds* as `duration`; iTunes reports
    milliseconds. Converting here is what keeps `ResolvedTrack.duration_ms` meaning one thing
    whichever provider answered. Parsed defensively — a missing or malformed length must not
    fail an otherwise good resolution."""
    try:
        seconds = int(result.get("duration"))
    except (TypeError, ValueError):
        return None
    return seconds * 1000 if 0 < seconds < 24 * 60 * 60 else None


async def resolve_track(query: str) -> ResolvedTrack | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(DEEZER_SEARCH_URL, params={"q": query, "limit": 1})
            resp.raise_for_status()
            results = resp.json().get("data", [])
    except httpx.HTTPError:
        # This is already the fallback for an iTunes miss — a transient failure here should
        # just mean "not found this time," not crash whatever bulk loop called resolve_track.
        return None

    if not results:
        return None

    best = results[0]
    artist = best.get("artist", {}).get("name")
    title = best.get("title")
    if not artist or not title:
        return None

    return ResolvedTrack(
        title=title,
        artist=artist,
        preview_url=best.get("preview") or None,
        source_track_id=str(best["id"]) if best.get("id") is not None else None,
        source="deezer",
        duration_ms=_duration_ms(best),
    )
