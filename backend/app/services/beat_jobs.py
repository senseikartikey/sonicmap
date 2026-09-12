"""Queue and worker for beat-grid analysis.

Deliberately reuses `catalog_jobs` rather than adding a table: that queue is already durable,
leased with FOR UPDATE SKIP LOCKED, and has retry/backoff and a max-attempts rule that has been
exercised in production. It is keyed on (song_id, job_type), so a second job type costs nothing
but a string.

It cannot reuse the catalog *worker*, though. That runs in the api image, which carries Essentia
and TensorFlow but no torch; beat_this needs torch, which only the stem image has. Hence a
separate worker process over the same queue — see app/scripts/beat_worker.py.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import CatalogJob, Song
from app.services.beat_grid import BeatGridError, analyze_preview

logger = logging.getLogger(__name__)

JOB_TYPE = "beat_grid"
LEASE_TIMEOUT = timedelta(minutes=10)
QUEUE_HIGH_WATER = 2000


def enqueue_beat_grid(db: Session, song_id: uuid.UUID, priority: int = 0) -> bool:
    """Queue one song for analysis. Idempotent: a song already gridded, already queued, or
    already permanently failed is left alone."""
    song = db.get(Song, song_id)
    if song is None or not song.preview_url or song.beat_grid:
        return False

    stmt = insert(CatalogJob).values(
        id=uuid.uuid4(), song_id=song_id, job_type=JOB_TYPE, status="queued",
        priority=priority, attempts=0, available_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    ).on_conflict_do_update(
        constraint="uq_catalog_job_song_type",
        set_={"priority": func.greatest(CatalogJob.priority, priority)},
    )
    db.execute(stmt)
    job = db.execute(
        select(CatalogJob).where(CatalogJob.song_id == song_id, CatalogJob.job_type == JOB_TYPE)
    ).scalar_one()
    if job.status in ("completed", "queued", "leased"):
        db.commit()
        return job.status == "queued"
    # Failed jobs are retried only when a person asks for this song again, which is exactly what
    # an enqueue is — but attempts reset, so the same permanent failure cannot loop forever the
    # way it did on the catalog queue (see catalog_jobs.PERMANENT_FAILURE_COOLDOWN).
    if job.attempts >= settings.catalog_job_max_attempts:
        db.commit()
        return False
    job.status = "queued"
    job.available_at = datetime.now(timezone.utc)
    db.commit()
    return True


def enqueue_many(db: Session, song_ids: list[uuid.UUID]) -> int:
    """Queue a whole set at once, highest priority to the earliest tracks so a listener can
    audition the opening transitions while the rest are still being analysed."""
    depth = db.execute(
        select(func.count()).select_from(CatalogJob).where(
            CatalogJob.job_type == JOB_TYPE, CatalogJob.status.in_(("queued", "leased"))
        )
    ).scalar_one()
    if depth >= QUEUE_HIGH_WATER:
        return 0
    return sum(
        int(enqueue_beat_grid(db, song_id, priority=1000 - index))
        for index, song_id in enumerate(song_ids)
    )


def claim_one(db: Session) -> CatalogJob | None:
    now = datetime.now(timezone.utc)
    stale = now - LEASE_TIMEOUT
    job = db.execute(
        select(CatalogJob)
        .where(
            CatalogJob.job_type == JOB_TYPE,
            CatalogJob.attempts < settings.catalog_job_max_attempts,
            or_(
                (CatalogJob.status == "queued") & (CatalogJob.available_at <= now),
                # Recover work abandoned by a worker that died mid-job.
                (CatalogJob.status == "leased") & (CatalogJob.leased_at < stale),
            ),
        )
        .order_by(CatalogJob.priority.desc(), CatalogJob.available_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalars().first()
    if job is None:
        return None
    job.status = "leased"
    job.leased_at = now
    job.heartbeat_at = now
    job.attempts += 1
    db.commit()
    return job


def process(job_id: uuid.UUID) -> None:
    db = SessionLocal()
    try:
        job = db.get(CatalogJob, job_id)
        if job is None:
            return
        song = db.get(Song, job.song_id)
        try:
            if song is None:
                raise BeatGridError("song was deleted")
            if not song.preview_url:
                raise BeatGridError("song has no preview clip to analyse")
            grid = analyze_preview(song.preview_url)
        except BeatGridError as exc:
            _fail(db, job, str(exc))
            return
        except Exception as exc:  # unexpected: still must not kill the worker
            logger.exception("Beat grid job %s crashed", job_id)
            _fail(db, job, f"unexpected error: {type(exc).__name__}: {exc}"[:2000])
            return

        song.beat_grid = grid
        job.status = "completed"
        job.finished_at = datetime.now(timezone.utc)
        job.last_error = None
        db.commit()
        logger.info(
            "Gridded %s - %s (%d beats, %d downbeats, %s/bar)",
            song.artist, song.title, len(grid["beats"]), len(grid["downbeats"]),
            grid.get("beats_per_bar"),
        )
    finally:
        db.close()


def _fail(db: Session, job: CatalogJob, message: str) -> None:
    db.rollback()
    job = db.get(CatalogJob, job.id)
    if job is None:
        return
    job.last_error = message[:2000]
    if job.attempts >= settings.catalog_job_max_attempts:
        job.status = "failed"
        job.finished_at = datetime.now(timezone.utc)
    else:
        job.status = "queued"
        job.available_at = datetime.now(timezone.utc) + timedelta(
            seconds=min(600, 20 * 2 ** job.attempts)
        )
    db.commit()
    logger.warning("Beat grid job %s: %s", job.id, message)
