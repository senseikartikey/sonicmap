from sqlalchemy import select

from app.db import SessionLocal
from app.models import Song
from app.services.catalog_vectors import apply_retrieval_vector


def run() -> None:
    db = SessionLocal()
    try:
        songs = db.execute(
            select(Song).where(
                Song.feature_vector.isnot(None), Song.genre_vector.isnot(None), Song.retrieval_vector.is_(None)
            )
        ).scalars().yield_per(250)
        count = 0
        for song in songs:
            apply_retrieval_vector(song)
            count += 1
            if count % 250 == 0:
                db.commit()
        db.commit()
        print(f"Backfilled {count:,} retrieval vectors")
    finally:
        db.close()


if __name__ == "__main__":
    run()
