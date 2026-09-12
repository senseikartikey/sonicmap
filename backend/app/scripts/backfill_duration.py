"""Backfill songs.duration_ms for rows resolved before migration 0017 added the column.

Uses the iTunes *lookup* endpoint rather than re-running a search: every one of these songs
already has its provider track id in `external_track_refs`, lookup accepts them in batches,
and the result is exact rather than a fresh best-guess match. That turns ~18k songs into
~120 requests instead of ~18k, which is the difference between a script that can be run and
one that gets rate-limited into uselessness.

    docker compose exec api python -m app.scripts.backfill_duration [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import logging

import httpx
from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import ExternalTrackRef, Song, UserSong

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("backfill_duration")

LOOKUP_URL = "https://itunes.apple.com/lookup"
BATCH = 150
"""iTunes accepts a comma-separated id list; 150 stays well inside the URL length limit while
keeping the request count low."""


async def _fetch(client: httpx.AsyncClient, ids: list[str]) -> dict[str, int]:
    try:
        response = await client.get(LOOKUP_URL, params={"id": ",".join(ids), "entity": "song"})
        response.raise_for_status()
        results = response.json().get("results", [])
    except (httpx.HTTPError, ValueError) as exc:
        # One bad batch must not abort a backfill over thousands of rows.
        logger.warning("batch of %d failed (%s) — skipped", len(ids), exc)
        return {}
    durations: dict[str, int] = {}
    for row in results:
        track_id, millis = row.get("trackId"), row.get("trackTimeMillis")
        if track_id is None or not isinstance(millis, int) or millis <= 0:
            continue
        durations[str(track_id)] = millis
    return durations


async def _by_search(song: Song) -> int | None:
    """Fallback for songs with no stored provider id. resolve_track already refuses a result
    whose title/artist don't plausibly match, so this writes a duration only when the search
    landed on the same recording — a wrong length is worse than a missing one."""
    from app.services.itunes import resolve_track

    resolved = await resolve_track(f"{song.title} {song.artist}")
    return resolved.duration_ms if resolved else None


async def backfill_by_search(limit: int | None, dry_run: bool) -> None:
    db = SessionLocal()
    try:
        # Songs someone actually has on a map come first. Oldest-first spends the whole run on
        # the obscure bootlegs that were imported earliest and mostly cannot be resolved at all,
        # so the tracks a user is looking at never get reached.
        on_a_map = select(UserSong.song_id).where(UserSong.song_id == Song.id).exists()
        query = (
            select(Song)
            .where(Song.duration_ms.is_(None), Song.preview_url.is_not(None))
            .order_by(on_a_map.desc(), Song.popularity_score.desc().nullslast(), Song.created_at)
        )
        if limit:
            query = query.limit(limit)
        songs = db.execute(query).scalars().all()
        logger.info("%d song(s) need a duration by search", len(songs))
        updated = 0
        for index, song in enumerate(songs, start=1):
            millis = await _by_search(song)
            if millis:
                updated += 1
                if not dry_run:
                    song.duration_ms = millis
            if index % 50 == 0:
                if not dry_run:
                    db.commit()
                logger.info("  %d/%d searched, %d found", index, len(songs), updated)
            await asyncio.sleep(0.35)
        if not dry_run:
            db.commit()
        logger.info("%s %d duration(s) by search", "would write" if dry_run else "wrote", updated)
    finally:
        db.close()


async def backfill(limit: int | None, dry_run: bool) -> None:
    db = SessionLocal()
    try:
        query = (
            select(ExternalTrackRef.external_id, ExternalTrackRef.song_id)
            .join(Song, Song.id == ExternalTrackRef.song_id)
            .where(ExternalTrackRef.provider == "itunes", Song.duration_ms.is_(None))
        )
        if limit:
            query = query.limit(limit)
        pairs = db.execute(query).all()
        logger.info("%d song(s) need a duration", len(pairs))
        if not pairs:
            return

        song_by_track = {external_id: song_id for external_id, song_id in pairs}
        ids = list(song_by_track)
        updated = 0
        async with httpx.AsyncClient(timeout=20) as client:
            for start in range(0, len(ids), BATCH):
                chunk = ids[start : start + BATCH]
                durations = await _fetch(client, chunk)
                if not dry_run:
                    for track_id, millis in durations.items():
                        db.execute(
                            update(Song)
                            .where(Song.id == song_by_track[track_id])
                            .values(duration_ms=millis)
                        )
                    db.commit()
                updated += len(durations)
                logger.info("  %d/%d looked up, %d durations so far", min(start + BATCH, len(ids)), len(ids), updated)
                # Apple rate-limits the public endpoint; a short pause keeps a long run alive.
                await asyncio.sleep(1.0)
        logger.info("%s %d duration(s)", "would write" if dry_run else "wrote", updated)
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="only process this many songs")
    parser.add_argument("--dry-run", action="store_true", help="look up but do not write")
    parser.add_argument(
        "--search", action="store_true",
        help="resolve by search instead of stored ids — slower, for songs with no provider id",
    )
    args = parser.parse_args()
    runner = backfill_by_search if args.search else backfill
    asyncio.run(runner(args.limit, args.dry_run))


if __name__ == "__main__":
    main()
