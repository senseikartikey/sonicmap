from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

# pool_size/max_overflow bumped from SQLAlchemy's defaults (5/10) for headroom against the
# frontend's own concurrency (the pipeline rail polls GET /map every 3s while anything's
# processing) stacked on top of whatever background catalog-expansion work is in flight — the
# real fix for connections being held for minutes at a time was decoupling background work
# from request-scoped sessions entirely (see app/services/background.py), this is just margin.
engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=20)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
