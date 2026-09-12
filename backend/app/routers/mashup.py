"""Set planner API: build, save, edit and export a DJ-playable running order."""
import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, defer

from app.db import SessionLocal, get_db
from app.dependencies import get_current_user
from app.models import MashupSet, Song, User, UserSong
from app.schemas import MAX_SET_TRACKS, MashupPlanIn, MashupRenameIn, MashupReorderIn
from app.services.beat_jobs import enqueue_many
from app.services.brain import recommend_for_user
from app.services.mashup import SetPlan, TrackAnalysis, plan_set
from app.services.mashup_export import EXPORTERS, IncompleteSetError

router = APIRouter(prefix="/mashup", tags=["mashup"])

# Enough of the shape to rebuild a SetPlan from a saved document without re-reading the songs.
PLAN_VERSION = 1


def _measured_bpm(song: Song) -> float | None:
    """Prefer the tempo the beat tracker measured over the one extraction estimated.

    They usually agree to about 2%, but the grid's figure is the one the player actually mixes
    on — so using anything else means the plan can print "pull it 4.9%" while the audio does
    something slightly different. Falls back to the stored BPM until a song has been gridded.
    """
    grid = song.beat_grid or {}
    measured = grid.get("bpm")
    return float(measured) if isinstance(measured, (int, float)) and measured > 0 else song.bpm


def _to_analysis(song: Song) -> TrackAnalysis:
    return TrackAnalysis(
        id=str(song.id), title=song.title, artist=song.artist, bpm=_measured_bpm(song), key=song.key,
        energy=song.energy, duration_ms=song.duration_ms, genre=song.genre,
        preview_url=song.preview_url, beat_grid=song.beat_grid,
        feature_vector=song.feature_vector, genre_vector=song.genre_vector,
    )


def _serialize(plan: SetPlan) -> dict:
    return {
        "version": PLAN_VERSION,
        "shape": plan.shape,
        "quality": plan.quality,
        "total_ms": plan.total_ms,
        "tracks": [
            {
                "id": track.id, "title": track.title, "artist": track.artist,
                "bpm": track.tempo, "raw_bpm": track.bpm, "key": track.key,
                "camelot": track.camelot, "energy": track.energy,
                "duration_ms": track.duration_ms, "genre": track.genre,
                # The 30-second provider clip. Carried so the browser can *audition* a planned
                # transition — it is a preview, never the render: a full mashup needs full audio,
                # which only ever arrives through Stem Studio's rights-confirmed path.
                "preview_url": track.preview_url,
                # The bar grid the player aligns on. Absent until the beat worker has analysed
                # this song; the player degrades to its own beat-only estimate meanwhile.
                "beat_grid": track.beat_grid,
            }
            for track in plan.order
        ],
        "transitions": [
            {
                "from_id": transition.from_id, "to_id": transition.to_id,
                "kind": transition.kind, "bars": transition.bars, "beats": transition.beats,
                "seconds": transition.seconds_at(
                    next((t.bpm for t in plan.order if t.id == transition.from_id), None)
                ),
                "tempo": None if transition.tempo is None else {
                    "stretch_pct": transition.tempo.stretch_pct,
                    "ratio": transition.tempo.ratio,
                    "within_comfort": transition.tempo.within_comfort,
                },
                "camelot_from": transition.camelot_from, "camelot_to": transition.camelot_to,
                "camelot_steps": transition.camelot_steps,
                "score": transition.score.as_dict,
                "notes": transition.notes, "warnings": transition.warnings,
            }
            for transition in plan.transitions
        ],
    }


def _load_songs(db: Session, song_ids: list[uuid.UUID]) -> list[Song]:
    """Fetch in the caller's order, not the database's — a reorder is meaningless otherwise."""
    rows = db.execute(
        select(Song).options(defer(Song.retrieval_vector)).where(Song.id.in_(song_ids))
    ).scalars().all()
    by_id = {song.id: song for song in rows}
    return [by_id[song_id] for song_id in song_ids if song_id in by_id]


def _recommendation_pool(user_id: uuid.UUID, limit: int) -> list[uuid.UUID]:
    """Own-session worker: recommend_for_user does blocking vector work and can commit
    (catalog enrichment), which detaches ORM instances — so only ids escape this session."""
    worker = SessionLocal()
    try:
        results = asyncio.run(recommend_for_user(worker, user_id, limit))
        return [candidate.id for candidate, *_ in results]
    finally:
        worker.close()


def _map_pool(db: Session, user_id: uuid.UUID, limit: int) -> list[uuid.UUID]:
    return list(db.execute(
        select(Song.id).join(UserSong, UserSong.song_id == Song.id)
        .where(UserSong.user_id == user_id, Song.bpm.is_not(None))
        .limit(limit)
    ).scalars().all())


def _owned_set(db: Session, user_id: uuid.UUID, set_id: uuid.UUID) -> MashupSet:
    row = db.get(MashupSet, set_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(404, "That set does not exist")
    return row


# Per-track fields that describe the *song*, not the plan. These are re-read from the catalog
# every time a set is returned; everything else in the document (order, transitions, techniques)
# is the saved decision and is never touched.
_SONG_FACTS = ("title", "artist", "key", "camelot", "energy", "genre", "duration_ms",
                "preview_url", "beat_grid")


def _rehydrate(db: Session, plan: dict) -> dict:
    """Refresh the song facts inside a saved plan from the catalog.

    A plan is stored as a snapshot, and song rows keep changing underneath it — a preview link
    gets repaired, a duration is backfilled, a new field is added to the serializer. Without
    this, a set saved yesterday is judged on yesterday's facts: the set that prompted this had
    no `preview_url` key at all, so every transition's play button sat disabled behind "no
    preview clip", even though every track in it was perfectly playable.

    Only the facts are refreshed. The running order and the planned blends are the user's
    decision and stay exactly as saved.
    """
    tracks = plan.get("tracks") or []
    if not tracks:
        return plan
    ids = []
    for track in tracks:
        try:
            ids.append(uuid.UUID(track["id"]))
        except (KeyError, ValueError, TypeError):
            continue
    if not ids:
        return plan
    songs = {
        song.id: song
        for song in db.execute(
            select(Song).options(defer(Song.retrieval_vector)).where(Song.id.in_(ids))
        ).scalars().all()
    }

    refreshed = []
    for track in tracks:
        merged = dict(track)
        song = songs.get(uuid.UUID(track["id"])) if track.get("id") else None
        if song is not None:
            fresh = _to_analysis(song)
            merged.update({
                "title": fresh.title, "artist": fresh.artist, "key": fresh.key,
                "camelot": fresh.camelot, "energy": fresh.energy, "genre": fresh.genre,
                "duration_ms": fresh.duration_ms, "preview_url": fresh.preview_url,
                "beat_grid": fresh.beat_grid,
                "bpm": fresh.tempo, "raw_bpm": fresh.bpm,
            })
        else:
            # Song removed from the catalog: keep the snapshot, but make sure the keys exist so
            # the client is not reading `undefined` and calling it "no preview".
            for field in _SONG_FACTS:
                merged.setdefault(field, None)
        refreshed.append(merged)

    plan = dict(plan)
    plan["tracks"] = refreshed
    # Recompute the running time too — durations backfilled since the set was saved are exactly
    # the case where a stored `null` here is stale rather than true.
    durations = [track.get("duration_ms") for track in refreshed]
    transitions = plan.get("transitions") or []
    if durations and all(durations):
        overlap = sum((transition.get("seconds") or 0) for transition in transitions)
        plan["total_ms"] = int(sum(durations) - overlap * 1000)
    return plan


def _out(db: Session, row: MashupSet) -> dict:
    return {
        "id": str(row.id), "name": row.name, "shape": row.shape, "source": row.source,
        "quality": row.quality, "plan": _rehydrate(db, row.plan or {}),
        "created_at": row.created_at, "updated_at": row.updated_at,
    }


@router.post("/plan", status_code=201)
async def create_plan(
    payload: MashupPlanIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.source == "recommendations":
        song_ids = await asyncio.to_thread(_recommendation_pool, user.id, payload.limit)
    elif payload.source == "map":
        song_ids = _map_pool(db, user.id, payload.limit)
    else:
        song_ids = list(dict.fromkeys(payload.song_ids))

    if len(song_ids) < 2:
        raise HTTPException(
            422,
            "A set needs at least two tracks. Add more songs to your map, or pick them yourself."
        )
    songs = _load_songs(db, song_ids[:MAX_SET_TRACKS])
    if len(songs) < 2:
        raise HTTPException(422, "Those tracks are not in the catalog yet.")

    plan = await asyncio.to_thread(
        plan_set,
        [_to_analysis(song) for song in songs],
        shape=payload.shape,
        open_with=str(payload.open_with) if payload.open_with else None,
    )
    document = _serialize(plan)
    if not payload.save:
        return {"id": None, "name": payload.name or "Untitled set", "shape": payload.shape,
                "source": payload.source, "quality": plan.quality, "plan": document,
                "created_at": None, "updated_at": None}

    # Grid the tracks in the background so the player can align bars rather than guess at them.
    # Priority follows set order, so the opening transitions become auditionable first.
    enqueue_many(db, [song.id for song in songs])

    row = MashupSet(
        user_id=user.id,
        name=payload.name or f"{len(plan.order)}-track set",
        shape=payload.shape, source=payload.source, plan=document, quality=plan.quality,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _out(db, row)


@router.get("/sets")
def list_sets(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(MashupSet).where(MashupSet.user_id == user.id)
        .order_by(MashupSet.created_at.desc()).limit(50)
    ).scalars().all()
    return [_out(db, row) for row in rows]


@router.get("/sets/{set_id}")
def get_set(set_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _out(_owned_set(db, user.id, set_id))


@router.patch("/sets/{set_id}/order")
async def reorder_set(
    set_id: uuid.UUID,
    payload: MashupReorderIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Accept a DJ's own running order and replan every blend against it.

    The order is taken exactly as given — this is the override, so the sequencer does not get
    to second-guess it — but the transitions are recomputed, because a technique planned for a
    pair that no longer sits next to each other is worse than no plan at all.
    """
    row = _owned_set(db, user.id, set_id)
    songs = _load_songs(db, payload.song_ids)
    if len(songs) < 2:
        raise HTTPException(422, "A set needs at least two tracks.")
    tracks = [_to_analysis(song) for song in songs]
    from app.services.mashup import SetPlan as _SetPlan, plan_transition

    transitions = [plan_transition(tracks[i], tracks[i + 1]) for i in range(len(tracks) - 1)]
    quality = round(
        sum(t.score.total for t in transitions) / len(transitions) if transitions else 1.0, 4
    )
    plan = _SetPlan(order=tracks, transitions=transitions, shape=row.shape, quality=quality)
    row.plan = _serialize(plan)
    row.quality = quality
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return _out(db, row)


@router.patch("/sets/{set_id}")
def rename_set(
    set_id: uuid.UUID,
    payload: MashupRenameIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _owned_set(db, user.id, set_id)
    row.name = payload.name
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return _out(db, row)


@router.delete("/sets/{set_id}", status_code=204)
def delete_set(set_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.delete(_owned_set(db, user.id, set_id))
    db.commit()
    return Response(status_code=204)


@router.get("/sets/{set_id}/export/{fmt}")
def export_set(
    set_id: uuid.UUID,
    fmt: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _owned_set(db, user.id, set_id)
    exporter = EXPORTERS.get(fmt)
    if exporter is None:
        raise HTTPException(404, f"Unknown format. Available: {', '.join(sorted(EXPORTERS))}")
    songs = _load_songs(db, [uuid.UUID(t["id"]) for t in row.plan.get("tracks", [])])
    if len(songs) < 2:
        raise HTTPException(409, "This set's tracks are no longer available to export.")
    from app.services.mashup import plan_transition

    tracks = [_to_analysis(song) for song in songs]
    transitions = [plan_transition(tracks[i], tracks[i + 1]) for i in range(len(tracks) - 1)]
    plan = SetPlan(order=tracks, transitions=transitions, shape=row.shape,
                   quality=row.quality or 0.0)
    try:
        export = exporter(plan, row.name)
    except IncompleteSetError as exc:
        raise HTTPException(409, str(exc)) from exc
    return Response(
        content=export.body,
        media_type=export.media_type,
        headers={"Content-Disposition": f'attachment; filename="{export.filename}"'},
    )
