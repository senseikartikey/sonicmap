"""One-off fix for catalog rows already left inconsistent by backfill_genre_sanity.py's earlier
runs, before this same reconciliation was wired into that script itself (see
app.services.genre_sanity.reconcile_genre). Confirmed live: songs like "Ishq Ka Raja" ended up
genre="Latin" while their remaining styles read entirely "Hip Hop---Trap, Hip Hop---Cloud Rap" —
no Latin anywhere — because the styles entry genre had been derived from was removed as an
implausible cultural mismatch, and genre itself was never touched to match.

No Gemini calls here — this only re-derives `genre` from each song's own already-stored
`styles` string, which is real, already-classified audio-classifier output. A no-op for every
row that was never touched by the earlier removal pass (extraction.py already guarantees
genre == styles[0]'s genre half at write time, so this only changes rows where that's since
drifted).

Run inside the backend container:
    docker compose exec api python -m app.scripts.reconcile_genre
"""

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Song
from app.services.genre_sanity import reconcile_genre


def run() -> None:
    db = SessionLocal()
    try:
        songs = db.execute(select(Song).where(Song.styles.is_not(None))).scalars().all()
        print(f"Checking genre/styles consistency for {len(songs)} songs...")

        changed = 0
        for song in songs:
            new_genre = reconcile_genre(song.genre, song.styles)
            if new_genre == song.genre:
                continue
            print(f"  {song.artist} - {song.title}")
            print(f"    genre {song.genre!r} -> {new_genre!r} (styles: {song.styles!r})")
            song.genre = new_genre
            changed += 1
        db.commit()
        print(f"Changed: {changed}/{len(songs)}")
    finally:
        db.close()


if __name__ == "__main__":
    run()
