"""Seed a globally broad analyzed subset without mistaking MusicBrainz row score for popularity."""
import argparse

from sqlalchemy import String, cast, delete, func, select, update

from app.db import SessionLocal
from app.models import CatalogJob, Song, UserSong
from app.services.catalog_jobs import enqueue_many

BAD_ARTISTS = ("various artists", "unknown artist", "[unknown]", "[no artist]")
BAD_VARIANT_PATTERN = (
    r"(rehears|commentary|karaoke|monitor mix|practice|unused mix|studio chat|"
    r"instrumental take|demo|soundcheck|acapella|a cappella|isolated vocal)"
)


def _eligible():
    return (
        Song.feature_vector.is_(None),
        Song.catalog_status == "metadata",
        Song.musicbrainz_id.isnot(None),
        func.lower(Song.artist).notin_(BAD_ARTISTS),
        func.length(Song.title) <= 120,
        Song.title.op("!~*")(BAD_VARIANT_PATTERN),
    )


def run(limit: int, replace_queued: bool) -> None:
    db = SessionLocal()
    try:
        if replace_queued:
            queued_ids = select(CatalogJob.song_id).where(CatalogJob.status == "queued")
            db.execute(
                update(Song)
                .where(Song.id.in_(queued_ids), Song.catalog_status == "queued")
                .values(catalog_status="metadata")
            )
            removed = db.execute(delete(CatalogJob).where(CatalogJob.status == "queued")).rowcount
            db.commit()
            print(f"Replaced {removed:,} previously queued jobs; completed/leased jobs were preserved")

        # Highest priority: deeper coverage around artists real users have already supplied.
        user_artists = set(
            db.execute(
                select(func.lower(Song.artist)).join(UserSong, UserSong.song_id == Song.id).distinct()
            ).scalars()
        )
        demand_limit = min(limit // 5, 5000)
        if user_artists:
            demand_ranked = (
                select(
                    Song.id.label("song_id"),
                    func.row_number().over(
                        partition_by=func.lower(Song.artist),
                        order_by=func.md5(Song.musicbrainz_id),
                    ).label("artist_rank"),
                )
                .where(*_eligible(), func.lower(Song.artist).in_(user_artists))
                .subquery()
            )
            demanded = db.execute(
                select(demand_ranked.c.song_id)
                .where(demand_ranked.c.artist_rank <= 10)
                .order_by(func.md5(cast(demand_ranked.c.song_id, String)))
                .limit(demand_limit)
            ).scalars().all()
        else:
            demanded = []

        # Global breadth: at most two recordings per raw artist, deterministically sampled.
        # This prevents a prolific catalog or compilation credit from consuming the seed and
        # yields roughly 12.5k distinct artists at the 25k milestone.
        ranked = (
            select(
                Song.id.label("song_id"),
                func.row_number().over(
                    partition_by=func.lower(Song.artist),
                    order_by=func.md5(Song.musicbrainz_id),
                ).label("artist_rank"),
            )
            .where(*_eligible(), Song.id.notin_(demanded))
            .subquery()
        )
        broad = db.execute(
            select(ranked.c.song_id)
            .where(ranked.c.artist_rank <= 2)
            .order_by(func.md5(cast(ranked.c.song_id, String)))
            .limit(limit - len(demanded))
        ).scalars().all()

        selected = demanded + broad
        submitted = enqueue_many(
            db, [(song_id, limit - rank) for rank, song_id in enumerate(selected)]
        )
        print(
            f"Submitted {submitted:,} tracks: {len(demanded):,} user-demand and "
            f"{len(broad):,} globally artist-diverse"
        )
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=25_000)
    parser.add_argument("--replace-queued", action="store_true")
    args = parser.parse_args()
    run(args.limit, args.replace_queued)
