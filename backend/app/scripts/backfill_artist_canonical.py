"""One-off/periodic maintenance script: resolves every catalog song's `artist` credit string
into its real individual artist(s) via Gemini (app/services/artist_identity.py), so the
recommendation diversity cap (app/services/brain.py's _apply_diversity_cap) recognizes the same
real artist across differently-formatted credits — e.g. a duo/group stage name ("Vishal &
Shekhar") and the full names behind it ("Vishal Dadlani & Shekhar Ravjiani") no longer count as
two different artists just because a release credited them differently.

Run inside the backend container:
    docker compose exec api python -m app.scripts.backfill_artist_canonical
"""

import asyncio

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Song
from app.services.artist_identity import canonicalize_artists


async def run() -> None:
    db = SessionLocal()
    try:
        songs = db.execute(
            select(Song).where(Song.feature_vector.is_not(None), Song.artist_canonical.is_(None))
        ).scalars().all()
        print(f"Resolving canonical artist identity for {len(songs)} songs (batched via Gemini)...")

        batch = [(str(s.id), s.artist) for s in songs]
        results = await canonicalize_artists(batch)
        print(f"Gemini answered for {len(results)}/{len(songs)} songs.")

        by_id = {str(s.id): s for s in songs}
        changed = 0
        for song_id, names in results.items():
            song = by_id.get(song_id)
            if song is None or not names:
                continue
            song.artist_canonical = " | ".join(names)
            changed += 1
        db.commit()
        print(f"Set artist_canonical on {changed}/{len(songs)} songs.")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(run())
