"""One-off/periodic maintenance script: seeds the shared catalog with real, currently-popular
songs pulled from Apple's public top-charts feed across many countries — independent of any
user ever typing them in.

Why this exists: the shared catalog (app/models.py Song table) only grew from what individual
users explicitly searched, pasted, or imported. Recommendations rank the best-available match
in that catalog — correct, but with only a few dozen songs (mostly one person's test data)
"best available" is nowhere close to "the whole music industry", which is what a recommender
is supposed to feel like. This is the fix: proactively populate the catalog from real charts,
so recommend_for_user (app/services/brain.py) has hundreds of genuinely diverse songs to
choose from, not just whatever a handful of users happened to add.

Genre/language diversity comes from *which countries'* charts get pulled, not a genre filter —
Apple retired the old per-genre chart endpoint (rss.itunes.apple.com is dead, DNS failure as of
2026); the current endpoint (rss.applemarketingtools.com) only serves an overall "most played"
chart per country, but different countries' overall charts are naturally genre-distinct: Korea
skews K-pop, Mexico/Brazil skew Latin, Nigeria skews Afrobeats, Japan skews J-pop/J-rock, India
skews Bollywood/Punjabi, Sweden skews the pop-songwriting/electronic axis, and so on.

Run inside the backend container:
    docker compose exec api python -m app.scripts.seed_catalog
"""

import asyncio

import anyio
import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.models import Song
from app.services import itunes
from app.services.extraction import extract_features

# Chosen for genre/language spread, not population size — see module docstring.
COUNTRIES = ["us", "gb", "kr", "mx", "br", "in", "jp", "ng", "de", "se", "fr", "it", "es", "za", "ca"]
SONGS_PER_COUNTRY = 15
# Concurrent extractions — each spins up its own Essentia/TensorFlow model instances and DB
# session (no sharing across tasks), so this trades memory for wall-clock time. Kept modest
# for a single dev container, not tuned for a beefier host.
CONCURRENCY = 3


async def _fetch_chart(client: httpx.AsyncClient, country: str) -> list[tuple[str, str]]:
    url = f"https://rss.applemarketingtools.com/api/v2/{country}/music/most-played/{SONGS_PER_COUNTRY}/songs.json"
    try:
        resp = await client.get(url, follow_redirects=True, timeout=15)
        resp.raise_for_status()
        results = resp.json()["feed"]["results"]
        return [(r["artistName"], r["name"]) for r in results]
    except Exception as e:
        print(f"  chart fetch failed for {country}: {e}")
        return []


async def _seed_one(semaphore: asyncio.Semaphore, artist: str, title: str, country: str) -> str:
    async with semaphore:
        db = SessionLocal()
        try:
            query = f"{artist} - {title}"
            resolved = await itunes.resolve_track(query, country=country.upper())
            if resolved is None:
                return f"unresolved: {query}"

            existing = db.execute(
                select(Song).where(
                    func.lower(Song.title) == resolved.title.lower(),
                    func.lower(Song.artist) == resolved.artist.lower(),
                )
            ).scalar_one_or_none()
            if existing is not None:
                return f"already in catalog: {resolved.artist} - {resolved.title}"

            song = Song(
                title=resolved.title,
                artist=resolved.artist,
                preview_url=resolved.preview_url,
                duration_ms=resolved.duration_ms,
                resolution_status="resolved" if resolved.preview_url else "unresolved",
            )
            db.add(song)
            try:
                db.commit()
            except IntegrityError:
                # Two different countries' charts resolved to the same canonical track and
                # both got past the existence check before either committed (e.g. a globally
                # charting song like "Sunflower" showing up in multiple countries' top 15).
                # Not an error — just treat it the same as "already in catalog".
                db.rollback()
                canonical = db.execute(
                    select(Song).where(
                    func.lower(Song.title) == resolved.title.lower(),
                    func.lower(Song.artist) == resolved.artist.lower(),
                )
                ).scalar_one_or_none()
                return f"already in catalog (race): {canonical.artist} - {canonical.title}" if canonical else f"failed: {query}"
            db.refresh(song)

            if song.preview_url:
                try:
                    features = await extract_features(song.preview_url)
                    song.feature_vector = features.vector
                    song.bpm = features.bpm
                    song.key = features.key
                    song.energy = features.energy
                    song.danceability = features.danceability
                    song.genre_vector = features.genre_vector
                    song.genre = features.genre
                    song.styles = features.styles
                    song.extraction_status = "extracted"
                except Exception as e:
                    song.extraction_status = "failed"
                    db.commit()
                    return f"extraction failed: {song.artist} - {song.title} ({e})"
                db.commit()

            return f"seeded: {song.artist} - {song.title} [{song.genre}]"
        finally:
            db.close()


async def _run() -> None:
    async with httpx.AsyncClient() as client:
        chart_lists = await asyncio.gather(*[_fetch_chart(client, c) for c in COUNTRIES])

    seen: set[tuple[str, str]] = set()
    tracks: list[tuple[str, str, str]] = []
    for country, songs in zip(COUNTRIES, chart_lists):
        for artist, title in songs:
            key = (artist.strip().lower(), title.strip().lower())
            if key in seen:
                continue
            seen.add(key)
            tracks.append((artist, title, country))

    print(f"Pulled {len(tracks)} unique tracks from {len(COUNTRIES)} countries' charts. Seeding...")
    semaphore = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(
        *[_seed_one(semaphore, artist, title, country) for artist, title, country in tracks]
    )
    for i, r in enumerate(results, 1):
        print(f"[{i}/{len(tracks)}] {r}")

    seeded = sum(1 for r in results if r.startswith("seeded"))
    print(f"\nDone. {seeded}/{len(tracks)} newly seeded with full extraction.")


if __name__ == "__main__":
    anyio.run(_run)
