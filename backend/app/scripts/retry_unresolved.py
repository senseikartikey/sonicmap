"""One-off cleanup script: retries catalog songs stuck at resolution_status="unresolved"
whose stored `title` is actually a raw, never-cleaned-up pasted line (artist="Unknown" is the
tell — see app/routers/ingest.py's _create_pending_placeholder/_resolve_and_extract). Found
live: a batch of Bollywood songs pasted as "Arijit Singh, Amaal Mallik, Kumaar - Roke Na Ruke
Naina" (a multi-artist Spotify-style credit line) got used *verbatim* as the iTunes search
query and failed — confirmed directly that the raw query returns nothing while a cleaned
"Arijit Singh - Roke Na Ruke Naina" resolves immediately. These are real, resolvable songs;
they just predate app/services/paste_parser.py's cleanup pass, and "unresolved" is a terminal
status _resolve_and_extract never retries on its own.

Reuses paste_parser.py's same LLM cleanup to re-derive a clean query per song. That turned out
not to be the actual problem for most of these, though: "Arijit Singh, Amaal Mallik, Kumaar -
Roke Na Ruke Naina" is already well-formed "Artist - Title" — the parser correctly leaves it
alone — the real issue is iTunes' search choking on a 3-name comma-separated artist credit as
a single query. Confirmed directly: the full query returns nothing, but "Arijit Singh - Roke Na
Ruke Naina" (first artist only) resolves immediately. So there's a second fallback here: if the
full query still fails and the artist portion has multiple comma-separated names, retry once
more with just the first name — multi-artist Bollywood/soundtrack credits (3-4 collaborators is
common) hit this constantly.

Mirrors _resolve_and_extract's exact merge behavior if a query resolves to a song that already
exists in the catalog under a different row (repoint UserSong/HiddenSong links, drop
collisions, delete the now-redundant placeholder) rather than update-in-place, which would
violate the case-insensitive uniqueness constraint.

The first-artist fallback caught a real mismatch live, worth recording: for "Rochak Kohli,
Arijit Singh, Kumaar - Tera Yaar Hoon Main", the first credited name is the *composer*, not the
performer — Bollywood credits commonly list composer before singer — so "Rochak Kohli - Tera
Yaar Hoon Main" matched iTunes to a *different* song Rochak Kohli also composed ("Dekhte
Dekhte"), not the one actually being searched for. Caught and fixed by hand once, then patched
properly: _looks_like_same_song gates every fallback-driven resolution behind a real title
check before accepting it — silently mislabeling a song is worse than leaving it unresolved.

Run inside the backend container:
    docker compose exec api python -m app.scripts.retry_unresolved
"""

import asyncio
import difflib
import re

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import HiddenSong, Song, UserSong
from app.services import itunes
from app.services.extraction import apply_extracted_features, extract_features
from app.services.language import detect_language
from app.services.paste_parser import parse_pasted_songs

_SUFFIX_RE = re.compile(r"\s*[\(\[][^)\]]*[\)\]]\s*$")


def _bare_title(title: str) -> str:
    prev = None
    while prev != title:
        prev = title
        title = _SUFFIX_RE.sub("", title).strip()
    return title.lower()


def _looks_like_same_song(query: str, resolved_title: str) -> bool:
    """Sanity check before trusting a resolution — see the module docstring for the real
    mismatch this caught. Compares bare titles (trailing "(From ...)"/"[...]" suffixes
    stripped from both sides): accepts if one contains the other, or they're a close textual
    match; rejects a resolved title that doesn't resemble what was actually searched for."""
    _, _, title_part = query.partition(" - ")
    a = _bare_title(title_part.strip() or query)
    b = _bare_title(resolved_title)
    if not a or not b:
        return True  # nothing usable to compare — don't block on it
    if a in b or b in a:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.6


def _first_artist_fallback(query: str) -> str | None:
    """"Arijit Singh, Amaal Mallik, Kumaar - Roke Na Ruke Naina" -> "Arijit Singh - Roke Na Ruke
    Naina". Returns None if `query` doesn't look like "artist(s) - title" with a multi-name
    artist portion, so callers know there's no fallback to try."""
    if " - " not in query:
        return None
    artist_part, _, title_part = query.partition(" - ")
    if "," not in artist_part:
        return None
    first_artist = artist_part.split(",", 1)[0].strip()
    if not first_artist or not title_part.strip():
        return None
    return f"{first_artist} - {title_part.strip()}"


def _merge_into(db, keep: Song, merge_away: Song) -> None:
    for link_model in (UserSong, HiddenSong):
        links = db.execute(select(link_model).where(link_model.song_id == merge_away.id)).scalars().all()
        for link in links:
            conditions = [link_model.user_id == link.user_id, link_model.song_id == keep.id]
            if link_model is UserSong:
                conditions.append(UserSong.source == link.source)
            collision = db.execute(select(link_model).where(*conditions)).scalar_one_or_none()
            if collision is not None:
                db.delete(link)
            else:
                link.song = keep
    db.delete(merge_away)


async def run() -> None:
    db = SessionLocal()
    try:
        songs = db.execute(
            select(Song).where(Song.resolution_status == "unresolved", Song.artist == "Unknown")
        ).scalars().all()
        # Real placeholders only — skip empty/garbage titles left over from manual testing.
        songs = [s for s in songs if s.title.strip(" -")]
        print(f"Retrying {len(songs)} unresolved placeholder(s)...")
        if not songs:
            return

        resolved_count = 0
        still_unresolved = 0
        for song in songs:
            # One cleanup call per song, not one batch call for all of them — a batch call has
            # no reliable way to map cleaned results back to which input they came from once
            # Gemini legitimately drops an entry (a real garbage/test title, correctly excluded
            # as "not actually a song"), and Gemini genuinely did drop two of the 15 real rows
            # here on the first attempt, which a batch+1:1-position-match approach can't
            # distinguish from Gemini losing track of a real song.
            cleaned = await parse_pasted_songs(song.title)
            query = cleaned[0] if cleaned else song.title
            resolved = await itunes.resolve_track(query)
            if resolved is not None and not _looks_like_same_song(query, resolved.title):
                resolved = None

            if resolved is None:
                fallback_query = _first_artist_fallback(query)
                if fallback_query is not None:
                    fallback_resolved = await itunes.resolve_track(fallback_query)
                    if fallback_resolved is not None and _looks_like_same_song(fallback_query, fallback_resolved.title):
                        resolved = fallback_resolved
                        query = fallback_query

            if resolved is None:
                print(f'  still unresolved: {query!r} (was {song.title!r})')
                still_unresolved += 1
                continue

            canonical = db.execute(
                select(Song).where(
                    func.lower(Song.title) == resolved.title.lower(),
                    func.lower(Song.artist) == resolved.artist.lower(),
                    Song.id != song.id,
                )
            ).scalar_one_or_none()

            if canonical is not None:
                print(f'  resolved (merged into existing): {resolved.artist} - {resolved.title}')
                _merge_into(db, canonical, song)
                song = canonical
            else:
                print(f'  resolved: {resolved.artist} - {resolved.title}')
                song.title = resolved.title
                song.artist = resolved.artist
                song.preview_url = resolved.preview_url
                song.duration_ms = resolved.duration_ms or song.duration_ms
                song.resolution_status = "resolved" if resolved.preview_url else "unresolved"
                song.language = detect_language(resolved.title, resolved.artist)
            db.commit()
            resolved_count += 1

            if song.preview_url and song.extraction_status == "pending":
                try:
                    features = await extract_features(song.preview_url)
                    apply_extracted_features(song, features)
                except Exception:
                    song.extraction_status = "failed"
                db.commit()

        print(f"\nResolved {resolved_count}/{len(songs)}; still unresolved: {still_unresolved}.")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(run())
