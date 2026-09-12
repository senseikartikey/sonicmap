"""One-off/periodic maintenance script: removes catalog songs' `styles` sub-tags that Gemini
flags as an obvious cultural/linguistic mismatch (app/services/genre_sanity.py) — e.g. Greek
folk sub-genres on a Hindi devotional song, the real case that motivated this. Only ever
*removes* specific flagged entries via exact substring match; never invents a replacement. It
does reconcile the coarser top-level `genre` field when a removal strips away the entry `genre`
was derived from (see genre_sanity.reconcile_genre) — found live after this script's first real
run: songs ended up with e.g. genre="Latin" while every remaining style read Hip Hop, because
the Latin-labeled entry had been the one removed. `genre`/`styles` are both display-only —
nothing in ranking reads them — so this is a data-quality fix, not something that can affect
recommendations.

Run inside the backend container:
    docker compose exec api python -m app.scripts.backfill_genre_sanity
"""

import asyncio

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Song
from app.services.genre_sanity import find_mismatched_styles, reconcile_genre, remove_mismatched_entries


async def run() -> None:
    db = SessionLocal()
    try:
        songs = db.execute(
            select(Song).where(Song.feature_vector.is_not(None), Song.styles.is_not(None))
        ).scalars().all()
        print(f"Checking style-tag plausibility for {len(songs)} songs (batched via Gemini)...")

        batch = [(str(s.id), s.title, s.artist, s.styles) for s in songs]
        flagged = await find_mismatched_styles(batch)
        print(f"Gemini flagged {len(flagged)}/{len(songs)} songs with a confident mismatch.")

        by_id = {str(s.id): s for s in songs}
        changed = 0
        for song_id, mismatched in flagged.items():
            song = by_id.get(song_id)
            if song is None or not song.styles:
                continue
            new_styles = remove_mismatched_entries(song.styles, mismatched)
            if new_styles is None or new_styles == song.styles:
                continue
            print(f"  {song.artist} - {song.title}")
            print(f"    removed: {mismatched}")
            print(f"    {song.styles!r} -> {new_styles!r}")
            song.styles = new_styles
            new_genre = reconcile_genre(song.genre, new_styles)
            if new_genre != song.genre:
                print(f"    genre {song.genre!r} -> {new_genre!r} (its supporting style was removed)")
                song.genre = new_genre
            changed += 1
        db.commit()
        print(f"Changed: {changed}/{len(songs)}")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(run())
