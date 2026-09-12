import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, defer

from app.db import get_db
from app.dependencies import get_current_user
from app.models import Song, User, UserSong
from app.schemas import MapPoint, SongOut
from app.services.brain import schedule_rebuild
from app.services import recommendation_cache

router = APIRouter(prefix="/map", tags=["map"])


@router.post("/rebuild", status_code=status.HTTP_202_ACCEPTED)
async def rebuild(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, str]:
    """Non-blocking: kicks the rebuild off in the background and returns immediately with a
    status the client polls via GET /map/status, instead of holding the request (and its
    pooled DB connection) open for however long UMAP/HDBSCAN take. A no-op if a rebuild is
    already in flight for this user, and self-healing if scheduling itself fails — see
    schedule_rebuild.

    Must be `async def`, not `def` — schedule_rebuild calls asyncio.create_task() under the
    hood, which needs a running event loop *in the calling thread*. FastAPI runs sync `def`
    route handlers in a threadpool (no event loop of their own), so calling it from one raises
    "no running event loop" — confirmed live during testing, from a `def` version of exactly
    this route."""
    schedule_rebuild(db, user)
    return {"status": user.map_rebuild_status}


@router.get("/status")
def get_rebuild_status(user: User = Depends(get_current_user)) -> dict[str, str]:
    return {"status": user.map_rebuild_status}


@router.delete("/{song_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_song(
    song_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> None:
    """Removes the song from this user's map only (deletes their UserSong rows for it,
    every source it was added through). The shared Song catalog entry is untouched, since
    other users' brains may still reference it."""
    result = db.execute(
        delete(UserSong).where(UserSong.user_id == user.id, UserSong.song_id == song_id)
    )
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Song not found on your map")
    recommendation_cache.invalidate_user(user.id)
    schedule_rebuild(db, user)


@router.delete("")
async def clear_map(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, int]:
    """Removes every song from this user's map at once — a full reset, not a per-song
    removal (see remove_song above for that). Shared Song catalog entries are untouched, same
    as remove_song; only this user's UserSong rows (and therefore their map/cluster placement)
    are deleted. Hidden-song dismissals are separate user state and are left alone."""
    result = db.execute(delete(UserSong).where(UserSong.user_id == user.id))
    db.commit()
    recommendation_cache.invalidate_user(user.id)
    user.map_rebuild_status = "idle"
    db.commit()
    return {"removed": result.rowcount}


@router.get("", response_model=list[MapPoint])
def get_map(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[MapPoint]:
    rows = (
        db.execute(
            select(UserSong, Song)
            .options(defer(Song.feature_vector), defer(Song.genre_vector), defer(Song.retrieval_vector))
            .join(Song, UserSong.song_id == Song.id).where(UserSong.user_id == user.id)
        )
        .tuples()
        .all()
    )
    return [
        MapPoint(
            song=SongOut.model_validate(song),
            x=user_song.map_x,
            y=user_song.map_y,
            cluster_label=user_song.cluster_label,
            added_at=user_song.added_at.isoformat(),
            source=user_song.source,
        )
        for user_song, song in rows
    ]
