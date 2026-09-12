"""Print a cumulative profile for one real recommendation request without mutating state."""
import asyncio
import cProfile
import io
import pstats
from sqlalchemy import select
from app.db import SessionLocal
from app.models import User
from app.services.brain import recommend_for_user

db = SessionLocal()
try:
    user = db.execute(select(User).order_by(User.created_at.desc())).scalars().first()
    profiler = cProfile.Profile()
    profiler.enable()
    asyncio.run(recommend_for_user(db, user.id, limit=10))
    profiler.disable()
    stream = io.StringIO()
    pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(30)
    print(stream.getvalue())
finally:
    db.close()
