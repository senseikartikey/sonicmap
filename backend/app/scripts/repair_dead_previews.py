"""Replace preview URLs that no longer resolve.

Deezer serves previews from signed, time-limited CDN links (`cdnt-preview.dzcdn.net`). Once the
signature expires the URL returns 403 forever, so every row resolved through Deezer eventually
becomes unplayable — 2,042 of them when this was written, about 9% of the analysable catalog.
Nothing warns you, because the URL is still *there*; it just stops working. That silently breaks
preview playback in the brain view and blocks auditioning a transition in Set Studio.

Re-resolving through iTunes gives a stable, unsigned URL. Only `preview_url` is rewritten — the
extracted features stay exactly as they were, because they describe the recording, not the link
the audio was fetched from.

    docker compose exec api python -m app.scripts.repair_dead_previews [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import or_, select

from app.db import SessionLocal
from app.models import Song
from app.services.itunes import resolve_track

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("repair_dead_previews")

DEAD_HOSTS = ("dzcdn.net",)


async def repair(limit: int | None, dry_run: bool) -> None:
    db = SessionLocal()
    try:
        query = select(Song).where(
            Song.preview_url.is_not(None),
            or_(*[Song.preview_url.contains(host) for host in DEAD_HOSTS]),
        )
        if limit:
            query = query.limit(limit)
        songs = db.execute(query).scalars().all()
        logger.info("%d song(s) have an expired preview link", len(songs))

        repaired = 0
        for index, song in enumerate(songs, start=1):
            resolved = await resolve_track(f"{song.title} {song.artist}")
            # resolve_track refuses a match whose title/artist don't plausibly line up, so a
            # None here means "no confident match" — leave the dead link rather than swap in
            # audio from a different recording.
            if resolved and resolved.preview_url:
                repaired += 1
                if not dry_run:
                    song.preview_url = resolved.preview_url
                    if resolved.duration_ms and not song.duration_ms:
                        song.duration_ms = resolved.duration_ms
            if index % 50 == 0:
                if not dry_run:
                    db.commit()
                logger.info("  %d/%d checked, %d repaired", index, len(songs), repaired)
            await asyncio.sleep(0.35)
        if not dry_run:
            db.commit()
        logger.info("%s %d preview link(s)", "would repair" if dry_run else "repaired", repaired)
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(repair(args.limit, args.dry_run))


if __name__ == "__main__":
    main()
