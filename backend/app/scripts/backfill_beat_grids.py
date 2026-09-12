"""Queue songs for beat-grid analysis, or analyse them here and now.

Grids are normally produced on demand — planning a set enqueues its tracks — but a catalog with
grids already in place is what makes the mix player feel instant, so this exists to work through
the backlog.

    # queue everything on somebody's map (the songs people will actually mix), highest value first
    docker compose exec beat-worker python -m app.scripts.backfill_beat_grids --limit 500

    # analyse inline instead of queueing, e.g. to watch it work
    docker compose exec beat-worker python -m app.scripts.backfill_beat_grids --now --limit 5

Must run in the stem/beat image: `--now` loads beat_this, which needs torch.
"""
from __future__ import annotations

import argparse
import logging
import time

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Song, UserSong
from app.services.beat_grid import BeatGridError, analyze_preview
from app.services.beat_jobs import enqueue_many

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("backfill_beat_grids")


_NEEDS_GRID = (Song.preview_url.is_not(None), Song.bpm.is_not(None), Song.beat_grid.is_(None))


def _candidates(db, limit: int, only_mapped: bool):
    """Songs with a playable clip and no grid yet, songs on somebody's map first.

    Two queries rather than one sorted query, deliberately. Expressing the priority as
    `ORDER BY EXISTS (...)` makes Postgres run that subquery once per candidate row, and
    `beat_grid IS NULL` matches ~1.8M rows, so the sort alone ran past a 60-second statement
    timeout. A join against user_songs answers the same question through an index in
    milliseconds, and the second pass only runs if the first did not fill the batch.
    """
    mapped = db.execute(
        select(Song)
        .join(UserSong, UserSong.song_id == Song.id)
        .where(*_NEEDS_GRID)
        .order_by(Song.popularity_score.desc().nullslast())
        .limit(limit)
    ).scalars().unique().all()
    if only_mapped or len(mapped) >= limit:
        return mapped[:limit]

    seen = {song.id for song in mapped}
    rest = db.execute(
        select(Song)
        .where(*_NEEDS_GRID, Song.id.not_in(seen) if seen else True)
        .order_by(Song.popularity_score.desc().nullslast())
        .limit(limit - len(mapped))
    ).scalars().all()
    return mapped + rest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--now", action="store_true", help="analyse inline instead of queueing")
    parser.add_argument("--mapped-only", action="store_true", help="skip songs on nobody's map")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        songs = _candidates(db, args.limit, args.mapped_only)
        logger.info("%d song(s) need a beat grid", len(songs))
        if not songs:
            return

        if not args.now:
            logger.info("queued %d", enqueue_many(db, [song.id for song in songs]))
            return

        done = failed = 0
        for index, song in enumerate(songs, start=1):
            started = time.time()
            try:
                song.beat_grid = analyze_preview(song.preview_url)
                db.commit()
                done += 1
                grid = song.beat_grid
                logger.info(
                    "  [%d/%d] %.1fs  %s - %s  (%d beats, %d bars, %s/bar, %.1f BPM)",
                    index, len(songs), time.time() - started, song.artist[:28], song.title[:34],
                    len(grid["beats"]), len(grid["downbeats"]), grid.get("beats_per_bar"),
                    grid.get("bpm") or 0,
                )
            except BeatGridError as exc:
                db.rollback()
                failed += 1
                logger.warning("  [%d/%d] %s - %s: %s", index, len(songs), song.artist[:20], song.title[:28], exc)
        logger.info("gridded %d, failed %d", done, failed)
    finally:
        db.close()


if __name__ == "__main__":
    main()
