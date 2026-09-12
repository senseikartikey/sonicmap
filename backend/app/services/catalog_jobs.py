"""Durable catalog enrichment queue and worker implementation."""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import CatalogJob, Song, UserSong
from app.services import itunes
from app.services.catalog_identity import record_external_ref
from app.services.extraction import apply_extracted_features, extract_features
from app.services.language import detect_language
from app.services.quality_sweep import run_quality_sweep

logger = logging.getLogger(__name__)
LEASE_TIMEOUT = timedelta(minutes=20)
GLOBAL_QUEUE_HIGH_WATER = 5000

PERMANENT_FAILURE_COOLDOWN = timedelta(days=7)
"""How long a song that exhausted its retries is left alone before it may be queued again.

Without this the pipeline deadlocks, and it did: `enqueue_enrichment_for_user` ranks candidates
by the requesting user's own artists first, and a user whose map is full of Beatles and Radiohead
surfaces bootlegs, rehearsal chatter and radio sessions that exist on no provider. Those songs
failed, `enqueue_song` reset them to attempts=0 on the very next call, and the worker spent every
cycle re-failing the same two dozen tracks while 1.76 million never-attempted songs waited behind
them. The pool sat at exactly 23,356 for hours with the LIVE indicator flickering as those jobs
cycled. A song can still come back — a provider may add it later — just not every few seconds.
"""

GLOBAL_BACKFILL_BATCH = 200
"""How many songs the worker pulls in for itself when it runs out of work.

Enrichment used to be purely demand-driven: nothing was queued unless someone loaded
recommendations. That is fine while the queue has depth and useless once it drains, which is
the state the catalog reached — a worker sitting idle beside 1.76M unanalysed songs.
"""


def is_exhausted(status: str | None, attempts: int) -> bool:
    """Has this job given up, either by being marked failed or by burning its attempts?"""
    return status == "failed" or attempts >= settings.catalog_job_max_attempts


def may_retry_after_failure(finished_at: datetime | None, now: datetime) -> bool:
    """Whether a job that gave up is allowed back into the queue yet.

    `finished_at` is stamped when a job gives up, so this measures "how long since it last
    failed". A missing timestamp predates the cooldown (migration 0018 stamps the rows that
    existed) and is read as recent: refusing one retry is recoverable, whereas reviving on
    every call is the deadlock this exists to prevent — see PERMANENT_FAILURE_COOLDOWN.
    """
    if finished_at is None:
        return False
    return now - finished_at >= PERMANENT_FAILURE_COOLDOWN


def enqueue_song(db: Session, song_id: uuid.UUID, priority: int = 0) -> bool:
    stmt = insert(CatalogJob).values(
        id=uuid.uuid4(), song_id=song_id, job_type="resolve_extract", status="queued",
        priority=priority, attempts=0, available_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_catalog_job_song_type",
        set_={"priority": func.greatest(CatalogJob.priority, priority)},
    )
    # completed jobs stay completed; failed/queued jobs are re-queued below explicitly.
    db.execute(stmt)
    job = db.execute(
        select(CatalogJob).where(CatalogJob.song_id == song_id, CatalogJob.job_type == "resolve_extract")
    ).scalar_one()
    if job.status == "completed":
        db.commit()
        return False
    now = datetime.now(timezone.utc)
    if is_exhausted(job.status, job.attempts):
        if not may_retry_after_failure(job.finished_at, now):
            db.commit()
            return False
        job.status = "queued"
        job.attempts = 0
        job.available_at = now
        job.last_error = None
        job.finished_at = None
    song = db.get(Song, song_id)
    if song is not None and song.catalog_status not in ("analyzed", "analyzing"):
        song.catalog_status = "queued"
    db.commit()
    return True


def enqueue_many(db: Session, songs: list[tuple[uuid.UUID, int]], batch_size: int = 1000) -> int:
    """Bulk form used by million-catalog seeders; one transaction per batch, not per song."""
    queued = 0
    now = datetime.now(timezone.utc)
    for start in range(0, len(songs), batch_size):
        batch = songs[start : start + batch_size]
        values = [
            {
                "id": uuid.uuid4(), "song_id": song_id, "job_type": "resolve_extract",
                "status": "queued", "priority": priority, "attempts": 0,
                "available_at": now, "created_at": now,
            }
            for song_id, priority in batch
        ]
        stmt = insert(CatalogJob).values(values)
        result = db.execute(
            stmt.on_conflict_do_update(
                constraint="uq_catalog_job_song_type",
                set_={
                    "priority": func.greatest(CatalogJob.priority, stmt.excluded.priority),
                    "status": "queued",
                    "attempts": 0,
                    "available_at": now,
                    "last_error": None,
                },
                where=CatalogJob.status != "completed",
            )
        )
        ids = [song_id for song_id, _priority in batch]
        db.execute(
            Song.__table__.update()
            .where(Song.id.in_(ids), Song.catalog_status.notin_(("analyzed", "analyzing")))
            .values(catalog_status="queued")
        )
        db.commit()
        # psycopg reports -1 for INSERT .. ON CONFLICT executemany rowcount even when the
        # operation succeeds; this is a submitted-item count, with idempotency enforced by DB.
        queued += len(batch)
    return queued


def enqueue_enrichment_for_user(db: Session, user_id: uuid.UUID, limit: int = 24) -> int:
    """Queue metadata-only tracks around represented artists, then globally strong gaps."""
    queue_depth = db.execute(
        select(func.count()).select_from(CatalogJob).where(
            CatalogJob.status.in_(("queued", "leased")),
            CatalogJob.attempts < settings.catalog_job_max_attempts,
        )
    ).scalar_one()
    if queue_depth >= GLOBAL_QUEUE_HIGH_WATER:
        return 0

    artists = select(func.lower(Song.artist)).join(UserSong).where(UserSong.user_id == user_id)
    candidates = db.execute(
        select(Song)
        .where(
            Song.feature_vector.is_(None),
            Song.catalog_status.in_(("metadata", "failed", "unavailable")),
            _not_in_failure_cooldown(),
        )
        .order_by(
            func.lower(Song.artist).in_(artists).desc(),
            Song.popularity_score.desc().nullslast(),
            Song.canonical_score.desc().nullslast(),
        )
        .limit(limit)
    ).scalars().all()
    queued = 0
    for rank, song in enumerate(candidates):
        queued += int(enqueue_song(db, song.id, priority=1000 - rank))
    return queued


def _not_in_failure_cooldown():
    """Filter out songs whose job gave up recently.

    Selecting these is not merely wasteful — because they sort to the top of the candidate
    ordering, they crowd out every song that has never been tried, so the analysed count stops
    moving entirely. `enqueue_song` would refuse them anyway; excluding them here is what makes
    room for real candidates in the same `limit`.
    """
    cutoff = datetime.now(timezone.utc) - PERMANENT_FAILURE_COOLDOWN
    return ~select(CatalogJob.id).where(
        CatalogJob.song_id == Song.id,
        CatalogJob.job_type == "resolve_extract",
        CatalogJob.status == "failed",
        or_(CatalogJob.finished_at.is_(None), CatalogJob.finished_at >= cutoff),
    ).exists()


def enqueue_global_backfill(db: Session, limit: int = GLOBAL_BACKFILL_BATCH) -> int:
    """Top the queue up from the catalog at large, with no user to aim at.

    Ordered by popularity so the pool grows in the direction most likely to be useful to
    somebody, rather than by insertion order. Deliberately separate from
    enqueue_enrichment_for_user: that one is a user asking for their own gaps to be filled,
    this one is the worker refusing to idle while 1.76M songs sit unanalysed.
    """
    queue_depth = db.execute(
        select(func.count()).select_from(CatalogJob).where(
            CatalogJob.status.in_(("queued", "leased")),
            CatalogJob.attempts < settings.catalog_job_max_attempts,
        )
    ).scalar_one()
    if queue_depth >= GLOBAL_QUEUE_HIGH_WATER:
        return 0

    candidates = db.execute(
        select(Song)
        .where(
            Song.feature_vector.is_(None),
            Song.catalog_status.in_(("metadata", "failed", "unavailable")),
            _not_in_failure_cooldown(),
        )
        .order_by(Song.popularity_score.desc().nullslast(), Song.canonical_score.desc().nullslast())
        .limit(limit)
    ).scalars().all()
    return sum(int(enqueue_song(db, song.id)) for song in candidates)


def _claim_one(db: Session) -> CatalogJob | None:
    now = datetime.now(timezone.utc)
    stale_before = now - LEASE_TIMEOUT
    job = db.execute(
        select(CatalogJob)
        .where(
            CatalogJob.attempts < settings.catalog_job_max_attempts,
            CatalogJob.available_at <= now,
            or_(CatalogJob.status == "queued", (CatalogJob.status == "leased") & (CatalogJob.heartbeat_at < stale_before)),
        )
        .order_by(CatalogJob.priority.desc(), CatalogJob.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    ).scalar_one_or_none()
    if job is None:
        return None
    job.status = "leased"
    job.leased_at = now
    job.heartbeat_at = now
    job.attempts += 1
    song = db.get(Song, job.song_id)
    if song is not None:
        song.catalog_status = "analyzing"
    db.commit()
    return job


async def _process(job_id: uuid.UUID) -> None:
    db = SessionLocal()
    try:
        job = db.get(CatalogJob, job_id)
        if job is None:
            return
        song = db.get(Song, job.song_id)
        if song is None:
            raise RuntimeError("catalog song was deleted")
        song_id, query = song.id, f"{song.artist} - {song.title}"

        # Release the read transaction before any network or extraction work.
        #
        # This is not a tidiness point. Reading job and song opens a transaction, and it used to
        # stay open across resolution *and* Essentia extraction (audio download plus a
        # Discogs-EffNet inference). Every in-flight job therefore sat "idle in transaction"
        # holding a lock on `songs` — one was observed stuck for 84 minutes. That alone is
        # survivable, but the next migration's ALTER TABLE queued behind it for an
        # ACCESS EXCLUSIVE lock, and once DDL is queued Postgres makes every later query on the
        # table queue behind *it*. The whole catalog stalled: plain SELECTs timing out, the
        # migration never completing, no error anywhere. Do the slow work unlocked, then
        # re-read to write.
        db.commit()

        resolved = await itunes.resolve_track(query)
        features = None
        if resolved is not None and resolved.preview_url:
            features = await extract_features(resolved.preview_url)

        song = db.get(Song, song_id)
        if song is None:
            raise RuntimeError("catalog song was deleted")
        if resolved is None or not resolved.preview_url:
            song.catalog_status = "unavailable"
            song.resolution_status = "unresolved"
            raise RuntimeError("no rights-compatible preview source resolved")
        song.preview_url = resolved.preview_url
        song.duration_ms = resolved.duration_ms or song.duration_ms
        record_external_ref(db, song.id, resolved)
        song.resolution_status = "resolved"
        song.language = song.language or detect_language(song.title, song.artist)
        apply_extracted_features(song, features)
        db.commit()
        await run_quality_sweep(db, [song.id])
        job.status = "completed"
        job.finished_at = datetime.now(timezone.utc)
        job.last_error = None
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.get(CatalogJob, job_id)
        if job is None:
            return
        song = db.get(Song, job.song_id)
        message = str(exc)[:2000]
        if job.attempts >= settings.catalog_job_max_attempts:
            job.status = "failed"
            # Stamped on failure as well as success: PERMANENT_FAILURE_COOLDOWN measures from here.
            job.finished_at = datetime.now(timezone.utc)
            if song is not None:
                song.catalog_status = "failed"
        else:
            job.status = "queued"
            job.available_at = datetime.now(timezone.utc) + timedelta(seconds=min(3600, 30 * 2 ** job.attempts))
            if song is not None:
                song.catalog_status = "queued"
        job.last_error = message
        if song is not None:
            song.last_catalog_error = message
        db.commit()
        logger.warning("Catalog job %s failed: %s", job_id, message)
    finally:
        db.close()


async def worker_loop(poll_seconds: float = 2.0) -> None:
    semaphore = asyncio.Semaphore(settings.catalog_worker_concurrency)
    running: set[asyncio.Task] = set()
    while True:
        running = {task for task in running if not task.done()}
        if len(running) >= settings.catalog_worker_concurrency:
            await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            continue
        db = SessionLocal()
        job_id: uuid.UUID | None = None
        try:
            job = _claim_one(db)
            if job is not None:
                job_id = job.id
        finally:
            db.close()
        if job_id is None:
            # Nothing to claim: pull in more work rather than sleeping beside an unanalysed
            # catalog. If that also comes back empty there is genuinely nothing left to do.
            db = SessionLocal()
            try:
                added = enqueue_global_backfill(db)
            except Exception:
                logger.exception("Catalog backfill top-up failed")
                added = 0
            finally:
                db.close()
            if added:
                logger.info("Queued %d more song(s) for analysis", added)
                continue
            await asyncio.sleep(poll_seconds)
            continue
        await semaphore.acquire()
        task = asyncio.create_task(_process(job_id))
        task.add_done_callback(lambda _task: semaphore.release())
        running.add(task)


def catalog_counts(db: Session) -> dict[str, int | bool]:
    rows = dict(db.execute(select(Song.catalog_status, func.count()).group_by(Song.catalog_status)).all())
    queued = db.execute(
        select(func.count()).select_from(CatalogJob).where(
            CatalogJob.status.in_(("queued", "leased")),
            CatalogJob.attempts < settings.catalog_job_max_attempts,
        )
    ).scalar_one()
    return {
        "metadata": int(sum(rows.values())),
        "analyzed": int(rows.get("analyzed", 0)),
        "queued": int(queued),
        "failed": int(rows.get("failed", 0)),
        "enrichment_active": queued > 0,
    }
