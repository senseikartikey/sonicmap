"""Compare production ANN shortlist top-10 with exhaustive exact ranking, read-only."""
import asyncio
from sqlalchemy import select
from sqlalchemy.orm import defer
from app.config import settings
from app.db import SessionLocal
from app.models import HiddenSong, Song, User, UserSong
from app.services.brain import recommend_for_user, score_candidates

async def main():
    db = SessionLocal()
    try:
        for user in db.execute(select(User)).scalars():
            history = db.execute(select(UserSong, Song).options(defer(Song.retrieval_vector)).join(Song).where(UserSong.user_id == user.id, Song.feature_vector.is_not(None))).tuples().all()
            owned = set(db.execute(select(UserSong.song_id).where(UserSong.user_id == user.id)).scalars())
            hidden = set(db.execute(select(HiddenSong.song_id).where(HiddenSong.user_id == user.id)).scalars())
            candidates = db.execute(select(Song).options(defer(Song.retrieval_vector)).where(Song.feature_vector.is_not(None), Song.id.not_in(owned | hidden))).scalars().all()
            previous = settings.ann_recommendations_enabled
            previous_lastfm = settings.lastfm_api_key
            settings.ann_recommendations_enabled = True
            settings.lastfm_api_key = ""
            try: ann = await recommend_for_user(db, user.id, 10)
            finally:
                settings.ann_recommendations_enabled = previous
                settings.lastfm_api_key = previous_lastfm
            exact = await score_candidates(history, candidates, 10, lastfm_active=False)
            ann_ids, exact_ids = [row[0].id for row in ann], [row[0].id for row in exact]
            overlap = len(set(ann_ids).intersection(exact_ids))
            print(f"user={user.id} catalog_candidates={len(candidates)} recall_at_10={overlap / 10:.2f} overlap={overlap}/10")
    finally: db.close()

if __name__ == "__main__": asyncio.run(main())
