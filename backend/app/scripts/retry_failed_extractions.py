"""Retry user-linked extraction failures from a UTC cutoff, then rebuild affected maps."""
import argparse
import asyncio
import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Song, User, UserSong
from app.services.brain import rebuild_user_map_background, trigger_rebuild
from app.services.extraction import apply_extracted_features, extract_features


async def run(cutoff: datetime, concurrency: int):
    db = SessionLocal()
    try:
        ids = list(db.execute(select(Song.id).join(UserSong).where(Song.extraction_status == "failed", Song.preview_url.is_not(None), Song.created_at >= cutoff).distinct()).scalars())
        user_ids = set(db.execute(select(UserSong.user_id).where(UserSong.song_id.in_(ids))).scalars()) if ids else set()
    finally: db.close()
    semaphore = asyncio.Semaphore(concurrency)

    async def one(song_id):
        async with semaphore:
            session = SessionLocal()
            try:
                song = session.get(Song, song_id)
                try:
                    features = await extract_features(song.preview_url)
                    apply_extracted_features(song, features)
                    session.commit()
                    print(f"ok {song.artist} - {song.title}")
                    return True
                except Exception as exc:
                    session.rollback()
                    print(f"failed {song_id}: {type(exc).__name__}: {exc}")
                    return False
            finally: session.close()

    results = await asyncio.gather(*[one(song_id) for song_id in ids])
    for user_id in user_ids:
        session = SessionLocal()
        try:
            user = session.get(User, user_id)
            if user and trigger_rebuild(session, user):
                await rebuild_user_map_background(user_id)
        finally: session.close()
    print(f"retried={len(ids)} recovered={sum(results)} still_failed={len(ids)-sum(results)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", required=True, help="UTC ISO timestamp, e.g. 2026-08-23T00:00:00+00:00")
    # Recovery is intentionally serial by default. Essentia's reusable TensorFlow graphs are
    # protected during normal ingestion, but bulk repair jobs run for long enough that parallel
    # teardown can flood native "No network created" warnings and obscure genuine failures.
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()
    asyncio.run(run(datetime.fromisoformat(args.since).astimezone(timezone.utc), args.concurrency))
