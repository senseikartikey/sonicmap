"""One-off maintenance script: re-runs extraction for every catalog song that predates the
genre-aware pipeline (app/services/extraction.py), so SonicDistance (app/services/brain.py)
has genre_vector to work with for the whole catalog, not just newly-ingested songs.

Run inside the backend container:
    docker compose exec api python -m app.scripts.backfill_genre
"""

import anyio
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Song
from app.services.extraction import apply_extracted_features, extract_features


async def _run() -> None:
    db = SessionLocal()
    try:
        songs = db.execute(
            select(Song).where(Song.preview_url.is_not(None), Song.genre_vector.is_(None))
        ).scalars().all()
        print(f"Backfilling genre data for {len(songs)} songs...")

        for i, song in enumerate(songs, 1):
            try:
                features = await extract_features(song.preview_url)
                apply_extracted_features(song, features)
                db.commit()
                print(f"[{i}/{len(songs)}] {song.artist} - {song.title}: {song.genre} ({song.styles})")
            except Exception as e:
                song.extraction_status = "failed"
                db.commit()
                print(f"[{i}/{len(songs)}] {song.artist} - {song.title}: FAILED ({e})")
    finally:
        db.close()


if __name__ == "__main__":
    anyio.run(_run)
