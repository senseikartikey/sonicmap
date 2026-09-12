"""Worker process for beat-grid analysis.

Separate from the catalog worker because it needs torch, which only the stem image carries.
Single-threaded on purpose: beat_this holds a 77 MB model in memory and analysis is CPU-bound,
so a second concurrent job on this hardware would slow both rather than finish sooner.
"""
import logging
import time

from app.db import SessionLocal
from app.services.beat_jobs import claim_one, process

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("beat_worker")

POLL_SECONDS = 3.0


def main() -> None:
    logger.info("Beat grid worker started")
    while True:
        db = SessionLocal()
        job_id = None
        try:
            job = claim_one(db)
            if job is not None:
                job_id = job.id
        except Exception:
            logger.exception("Failed to claim a beat grid job")
        finally:
            db.close()

        if job_id is None:
            time.sleep(POLL_SECONDS)
            continue
        process(job_id)


if __name__ == "__main__":
    main()
