"""Finds and (optionally) merges catalog duplicates the case-insensitive exact-match dedup
already running on every ingest can't catch — see app/services/duplicate_detection.py for the
two-stage cheap-prefilter-then-Gemini-confirms approach.

Report-only by default — prints every Gemini-confirmed duplicate pair without changing
anything. Pass --apply to actually merge: the two rows' UserSong/HiddenSong links get repointed
onto whichever row is kept (preferring the one with more real listeners' data, extracted
features, and an older created_at, in that order — see _pick_canonical), any link that would
collide with one the same user already has via the same source is dropped rather than
duplicated (mirrors the exact merge pattern app/routers/ingest.py's _resolve_and_extract
already uses for the "two requests raced to resolve the same song" case), and the other row is
deleted. This is the first destructive tool this codebase's batch-LLM pattern has produced —
default to report-only for exactly that reason.

Run inside the backend container:
    docker compose exec api python -m app.scripts.find_duplicates
    docker compose exec api python -m app.scripts.find_duplicates --apply
"""

import argparse
import asyncio

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import HiddenSong, Song, UserSong
from app.services.duplicate_detection import DuplicateCandidate, confirm_duplicates, find_candidate_pairs


def _pick_canonical(db, a: Song, b: Song) -> tuple[Song, Song]:
    """Returns (keep, merge_away). Prefers more real usage (UserSong link count), then having
    extracted audio features, then being the older row — all real signals of "which row is
    more load-bearing," never an arbitrary id comparison."""
    links_a = db.execute(select(func.count()).select_from(UserSong).where(UserSong.song_id == a.id)).scalar()
    links_b = db.execute(select(func.count()).select_from(UserSong).where(UserSong.song_id == b.id)).scalar()
    if links_a != links_b:
        return (a, b) if links_a > links_b else (b, a)
    extracted_a = a.extraction_status == "extracted"
    extracted_b = b.extraction_status == "extracted"
    if extracted_a != extracted_b:
        return (a, b) if extracted_a else (b, a)
    return (a, b) if a.created_at <= b.created_at else (b, a)


def _merge(db, keep: Song, merge_away: Song) -> None:
    for link_model, fk in ((UserSong, "user_id"), (HiddenSong, "user_id")):
        links = db.execute(select(link_model).where(link_model.song_id == merge_away.id)).scalars().all()
        for link in links:
            collision_conditions = [
                getattr(link_model, fk) == getattr(link, fk),
                link_model.song_id == keep.id,
            ]
            if link_model is UserSong:
                collision_conditions.append(UserSong.source == link.source)
            collision = db.execute(select(link_model).where(*collision_conditions)).scalar_one_or_none()
            if collision is not None:
                db.delete(link)
            else:
                # Through the relationship, not the raw song_id column — same reason
                # app/routers/ingest.py's _resolve_and_extract does this: SQLAlchemy's default
                # delete cascade behavior can null out a raw FK still pointing at a row that's
                # about to be deleted, if the relationship isn't updated first.
                link.song = keep
    db.delete(merge_away)


async def run(apply: bool) -> None:
    db = SessionLocal()
    try:
        songs = db.execute(select(Song).where(Song.feature_vector.is_not(None))).scalars().all()
        print(f"Prefiltering {len(songs)} songs for likely-duplicate title pairs (same artist, similar title)...")

        candidates = find_candidate_pairs(songs)
        print(f"Found {len(candidates)} candidate pairs — confirming via Gemini...")

        confirmed = await confirm_duplicates(candidates)
        print(f"Gemini confirmed {len(confirmed)} genuine duplicate pairs.\n")

        for c in confirmed:
            keep, merge_away = _pick_canonical(db, c.song_a, c.song_b)
            print(f'  KEEP:  "{keep.title}" by {keep.artist}')
            print(f'  MERGE: "{merge_away.title}" by {merge_away.artist}')
            if apply:
                _merge(db, keep, merge_away)
        if apply:
            db.commit()
            print(f"\nMerged {len(confirmed)} duplicate pairs.")
        else:
            print("\nNot applied (pass --apply to actually merge these).")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="actually merge confirmed duplicates")
    args = parser.parse_args()
    asyncio.run(run(args.apply))
