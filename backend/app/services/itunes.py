"""Resolves a free-text or Artist/Title query to a canonical track with a preview clip.

iTunes Search API is free, unauthenticated, and covers the large majority of commercial
releases — used here instead of Spotify because Spotify no longer hands out preview_url or
audio-features to new apps (see plan doc). MusicBrainz ID / ISRC enrichment is not implemented
yet (Phase 1 keeps resolution to iTunes-only); songs without a preview match are marked
'unresolved' rather than silently dropped.
"""

import difflib
import re

import httpx

from app.config import settings
from app.services import deezer
from app.services.resolved_track import ResolvedTrack

__all__ = ["ResolvedTrack", "resolve_track", "search_tracks"]


def _split_query(raw: str) -> tuple[str, str | None]:
    """Best-effort split of 'Artist - Title' style input. Falls back to treating the whole
    string as a search term when there's no clear separator."""
    for sep in (" - ", " – ", ": "):
        if sep in raw:
            artist, title = raw.split(sep, 1)
            return title.strip(), artist.strip()
    return raw.strip(), None


_SUFFIX_RE = re.compile(r"\s*[\(\[][^)\]]*[\)\]]\s*$")


def _bare_title(title: str) -> str:
    """Strips trailing "(...)"/"[...]" suffixes repeatedly (a title can carry more than one,
    e.g. "Title (Remix) [Radio Edit]") so two titles differing only by that kind of noise
    still compare as equal."""
    prev = None
    while prev != title:
        prev = title
        title = _SUFFIX_RE.sub("", title).strip()
    return title.lower()


def _looks_like_same_song(title_hint: str, candidate_title: str) -> bool:
    """Rejects a candidate whose title doesn't actually resemble what was searched for — the
    real failure mode this guards against: a multi-artist credit's first-name-only retry
    (_first_artist_only) can match iTunes to a *different* song that artist is also credited
    on (confirmed live: "Rochak Kohli - Tera Yaar Hoon Main" matched "Dekhte Dekhte", an
    unrelated song Rochak Kohli also composed — see app/scripts/retry_unresolved.py, where
    this was found). _best_title_match already picks the closest of what's available; this is
    the separate check for "even the closest one isn't actually close enough" — accepting
    iTunes' top-ranked result unconditionally is what let the mismatch through before."""
    if not title_hint.strip() or not candidate_title.strip():
        return True  # nothing usable to compare — don't block on it
    a, b = _bare_title(title_hint), _bare_title(candidate_title)
    if a in b or b in a:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.6


_ARTIST_NOISE = {"and", "the", "feat", "featuring", "with", "dj", "music"}


def _artist_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) > 1 and token not in _ARTIST_NOISE
    }


def _looks_like_same_artist(artist_hint: str | None, candidate_artist: str) -> bool:
    """Require at least one meaningful credited-name token to overlap.

    Catalog credits often contain composer, singer, and lyricist together while providers may
    return only one, so exact equality would reject valid matches. Zero overlap, however, is a
    strong signal that a same-titled result is a different recording.
    """
    if not artist_hint or not artist_hint.strip():
        return True
    expected = _artist_tokens(artist_hint)
    actual = _artist_tokens(candidate_artist)
    return bool(expected and actual and expected.intersection(actual))


def _best_title_match(results: list[dict], title_hint: str, artist_hint: str | None = None) -> dict | None:
    """Trusts iTunes' own result ordering first — index 0 already reflects a real relevance
    ranking that weighs artist match too, not just title text, so a naive "pick whichever
    result has the closest title string" would throw that away and risk landing on a
    same-titled track by a *different* artist (this was tried and caused a real regression:
    "Radiohead - Weird Fishes" started resolving to a Lianne La Havas track with a similar
    title, because a title-only re-rank ignored that the correct Radiohead result was already
    sitting at index 0). Only searches the rest of the already-fetched results for a title that
    actually looks right when the top one *doesn't* — the real failure mode this exists for is
    a multi-artist credit's first-name-only retry landing on a different song that artist is
    also credited on (see _looks_like_same_song's docstring). Returns None if nothing in the
    result set looks like the same song at all."""
    named = [
        r
        for r in results
        if r.get("trackName")
        and _looks_like_same_artist(artist_hint, r.get("artistName", ""))
    ]
    if not named:
        return None
    if _looks_like_same_song(title_hint, named[0]["trackName"]):
        return named[0]
    for candidate in named[1:]:
        if _looks_like_same_song(title_hint, candidate["trackName"]):
            return candidate
    return None


def _track_duration_ms(result: dict) -> int | None:
    """iTunes reports full track length as `trackTimeMillis`. Guarded because the field is
    absent on some entries and has been seen as a string; a bad value must not fail a
    resolution that is otherwise fine."""
    raw = result.get("trackTimeMillis")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if 0 < value < 24 * 60 * 60 * 1000 else None


def _first_artist_only(artist_hint: str) -> str | None:
    """"Rochak Kohli, Arijit Singh, Kumaar" -> "Rochak Kohli". Multi-artist Bollywood/
    soundtrack credits (composer, singer, lyricist all in one field) are common enough in this
    catalog that a combined "artist_hint title_hint" search term regularly returns nothing at
    all — worth one retry with just the first name. Returns None when there's nothing to
    shorten."""
    if "," not in artist_hint:
        return None
    first = artist_hint.split(",", 1)[0].strip()
    return first or None


async def resolve_track(query: str, country: str = "US") -> ResolvedTrack | None:
    """`country` is the iTunes storefront to search (ISO 3166-1 alpha-2, e.g. "KR", "JP") —
    matters for tracks that aren't distributed to the US store, which is otherwise the
    default and would silently mark them unresolved. Callers ingesting from a known-region
    source (e.g. app/scripts/seed_catalog.py pulling a country's chart) should pass it;
    user-facing search has no such hint and keeps the US default."""
    title_hint, artist_hint = _split_query(query)
    term = f"{artist_hint} {title_hint}" if artist_hint else title_hint

    async def _search(search_term: str) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    settings.itunes_search_base_url,
                    params={"term": search_term, "media": "music", "entity": "song", "limit": 5, "country": country},
                )
                resp.raise_for_status()
                return resp.json().get("results", [])
        except httpx.HTTPError:
            # A transient network/rate-limit hiccup shouldn't be indistinguishable from "no
            # match" to callers, but it also shouldn't crash whatever background task or bulk
            # loop is mid-batch over many queries — Deezer/the next fallback gets an honest
            # shot at this one query, same as a genuine zero-result search.
            return []

    results = await _search(term)

    if not results and artist_hint:
        # Multi-artist credit ("Composer, Singer, Lyricist") crammed into one search term is a
        # common, real failure mode for this catalog specifically — confirmed live: a batch of
        # Bollywood songs pasted with full multi-artist credits returned nothing until retried
        # with just the first artist name (see app/scripts/retry_unresolved.py, where this was
        # found and fixed). One retry, only on a genuine zero-result search — never touches the
        # already-working single/dual-artist case.
        first_artist = _first_artist_only(artist_hint)
        if first_artist is not None:
            results = await _search(f"{first_artist} {title_hint}")

    best = _best_title_match(results, title_hint, artist_hint) if results else None
    if best is None:
        # Either iTunes had no match at all, or every candidate it returned failed the title
        # sanity check (_looks_like_same_song) — both cases mean "nothing trustworthy found
        # here," so both fall through the same way: try Deezer's differently-shaped catalog
        # before giving up. Both are only ever a source of a title/artist match and a preview
        # clip; sonicmap runs its own Essentia extraction on whichever preview it gets, so this
        # never changes the trust model, just widens what counts as "found."
        fallback = await deezer.resolve_track(term)
        if fallback is None:
            return None
        if not _looks_like_same_song(title_hint, fallback.title):
            return None
        if not _looks_like_same_artist(artist_hint, fallback.artist):
            return None
        return fallback

    return ResolvedTrack(
        title=best.get("trackName", title_hint),
        artist=best.get("artistName", artist_hint or "Unknown Artist"),
        preview_url=best.get("previewUrl"),
        source_track_id=str(best["trackId"]) if best.get("trackId") is not None else None,
        source="itunes",
        duration_ms=_track_duration_ms(best),
    )


async def search_tracks(term: str, country: str = "US", limit: int = 25) -> list[ResolvedTrack]:
    """Bulk variant of resolve_track: returns every match for `term` instead of just the
    best one. Used by app/services/catalog_expansion.py to pull genre-representative
    candidates (e.g. term="Reggaeton") into the shared catalog — the search itself is the
    same iTunes source resolve_track already uses, just not collapsed to a single result."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                settings.itunes_search_base_url,
                params={"term": term, "media": "music", "entity": "song", "limit": limit, "country": country},
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
    except httpx.HTTPError:
        # Same rationale as resolve_track — callers here loop over many search_tracks calls
        # per batch (catalog_expansion.py); one transient failure shouldn't abort the rest.
        return []

    return [
        ResolvedTrack(
            title=r.get("trackName", ""),
            artist=r.get("artistName", "Unknown Artist"),
            preview_url=r.get("previewUrl"),
            source_track_id=str(r["trackId"]) if r.get("trackId") is not None else None,
            source="itunes",
            duration_ms=_track_duration_ms(r),
        )
        for r in results
        if r.get("trackName")
    ]
