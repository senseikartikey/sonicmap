"""Create-and-clean a uniquely named test row to verify concurrent ingest deduplication."""
import concurrent.futures
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import delete, select
from app.db import SessionLocal
from app.models import Song, User, UserSong
from app.routers.ingest import _create_pending_placeholder, _link_user_song

marker = f"__sonicmap_race_{uuid.uuid4()}__"
db = SessionLocal()
user_id = uuid.uuid4()
try:
    db.add(User(id=user_id, spotify_id=marker, spotify_access_token="test", spotify_refresh_token="test", spotify_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
    db.commit()

    def create_one(_):
        session = SessionLocal()
        try: return _create_pending_placeholder(session, marker)[0].id
        finally: session.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        song_ids = list(pool.map(create_one, range(48)))
    assert len(set(song_ids)) == 1, set(song_ids)
    song_id = song_ids[0]

    def link_one(_):
        session = SessionLocal()
        try: return _link_user_song(session, user_id, song_id, "search", None)
        finally: session.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        created = list(pool.map(link_one, range(48)))
    check = SessionLocal()
    try:
        count = len(check.execute(select(UserSong).where(UserSong.user_id == user_id, UserSong.song_id == song_id)).scalars().all())
    finally: check.close()
    assert count == 1 and sum(created) == 1, (count, sum(created))
    print("placeholder_requests=48 unique_songs=1 link_requests=48 unique_links=1")
finally:
    db.rollback()
    db.execute(delete(UserSong).where(UserSong.user_id == user_id))
    db.execute(delete(Song).where(Song.title == marker))
    db.execute(delete(User).where(User.id == user_id))
    db.commit()
    db.close()
