import uuid
import math
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.dependencies import get_current_user
from app.models import HiddenSong, RecommendationFeedback, Song, User, UserSong, UserTasteWeights
from app.schemas import FeedbackIn, RecommendationOut, SongOut
from app.services.brain import DEFAULT_WEIGHTS, SonicDistanceWeights, recommend_for_user, sonic_distance_breakdown
from app.services.catalog_jobs import enqueue_enrichment_for_user
from app.services import recommendation_cache

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


def _recommend_blocking(user_id: uuid.UUID, limit: int, exclude: list[uuid.UUID], english_only: bool):
    """Own-session worker for blocking SQL/vector decoding and exact CPU scoring.

    Serialize ORM rows before closing this worker-owned session. Returning Song instances to
    the async route used to work only while none of the worker operations committed/expired
    them; catalog enrichment can commit, making every attribute access raise
    DetachedInstanceError after this function's finally block closes the session.
    """
    worker_db = SessionLocal()
    try:
        results = asyncio.run(recommend_for_user(worker_db, user_id, limit, exclude, english_only))
        response = [
            RecommendationOut(
                song=SongOut.model_validate(candidate),
                distance=distance,
                reason=_build_reason(candidate, distance, best_match, cluster_size),
                best_match_song_id=best_match.id,
                xray=sonic_distance_breakdown(
                    candidate.feature_vector,
                    candidate.genre_vector,
                    best_match.feature_vector,
                    best_match.genre_vector,
                ),
            )
            for candidate, distance, best_match, cluster_size in results
        ]
        enqueue_enrichment_for_user(worker_db, user_id)
        return response
    finally:
        worker_db.close()


def _adapt_weight_values(current: dict[str, float], parts: dict[str, float], action: str) -> dict[str, float]:
    """Small bounded online update; pure so adversarial sequences can be regression-tested."""
    direction = 1 if action in {"more_like", "save"} else -1
    values = current.copy()
    for key in values:
        closeness = 1.0 - min(1.0, parts.get(key, 0.0) / max(getattr(DEFAULT_WEIGHTS, key), 0.01))
        values[key] = max(0.03, values[key] * (1 + direction * 0.035 * closeness))
    total = sum(values.values()) or 1.0
    return {key: value / total for key, value in values.items()}


def _build_reason(candidate: Song, distance: float, best_match: Song, cluster_size: int) -> str:
    """cluster_size distinguishes a genuine multi-song cluster-consensus match (the score
    reflects agreement across cluster_size songs, not just best_match alone) from the
    degenerate case — a song HDBSCAN never clustered, or a map too small for HDBSCAN to
    attempt clustering at all (fewer than 5 songs) — where the "cluster" is just best_match
    itself and the phrasing needs to say that honestly rather than implying a richer match
    than actually exists."""
    match_pct = round(100 * math.exp(-max(0.0, distance) * 1.15))

    if cluster_size > 1:
        cluster_note = f" (matched against {cluster_size} songs in that neighborhood, not just this one)"
    else:
        cluster_note = " (only song in that neighborhood so far — add more like it to sharpen this)"

    if candidate.genre and best_match.genre and candidate.genre == best_match.genre:
        return (
            f"Same {candidate.genre} neighborhood as \"{best_match.title}\" by {best_match.artist}"
            f"{cluster_note} — {match_pct}% SonicDistance match."
        )
    if candidate.genre:
        return (
            f"Closest to \"{best_match.title}\" by {best_match.artist} across tempo, genre, "
            f"and timbre{cluster_note} — {match_pct}% SonicDistance match."
        )
    return (
        f"Closest to \"{best_match.title}\" by {best_match.artist} on tempo, energy, and timbre"
        f"{cluster_note} — {match_pct}% SonicDistance match. (Genre not yet analyzed for this track.)"
    )


@router.get("", response_model=list[RecommendationOut])
async def get_recommendations(
    limit: int = Query(default=10, ge=1, le=50),
    exclude: list[uuid.UUID] = Query(default=[]),
    english_only: bool = False,
    user: User = Depends(get_current_user),
) -> list[RecommendationOut]:
    """`exclude` lets the client page past songs it's already been shown this session —
    SonicDistance ranking is otherwise deterministic for a given user/catalog, so without
    this, asking again just returns the identical list. `english_only` restricts candidates
    to Song.language == "en" (see app/services/language.py)."""
    if len(exclude) > 500:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Too many excluded songs in one request")
    cache_key = (user.id, limit, tuple(sorted(str(song_id) for song_id in exclude)), english_only)
    cached = recommendation_cache.get(cache_key)
    if cached is not None:
        return cached
    async with recommendation_cache.lock_for(cache_key):
        cached = recommendation_cache.get(cache_key)
        if cached is not None:
            return cached
        results = await asyncio.to_thread(_recommend_blocking, user.id, limit, exclude, english_only)

    # Grow the catalog in the background — never slows this request down, just makes the pool
    # deeper for next time. Five complementary strategies (see catalog_expansion.py): broad
    # genre coverage, targeted discovery of artists related to this user's *specific* artists
    # via Last.fm's similarity graph (makes future recommendations feel like "you might also
    # like [artist]" rather than "something in your genre bucket"), two independent "what's
    # actually popular right now" charts (Apple's + Last.fm's — different aggregation, rarely
    # agree on the same songs, each covers what the other misses), and LLM-assisted expansion
    # around the user's own largest taste-cluster (the only strategy that can target "more
    # like *this specific corner* of this map").
    #
    # fire_and_forget, not FastAPI's BackgroundTasks: BackgroundTasks run *before* this
    # request's `db` (a yield-dependency) is closed, so scheduling minutes of work this way
    # kept the request's own pooled connection checked out the whole time — on an endpoint
    # called on nearly every user action, that alone was enough to exhaust the pool and make
    # unrelated requests (like clicking "Rebuild map") hang waiting for a connection.
    response = results
    recommendation_cache.put(cache_key, response)
    return response


@router.post("/feedback", status_code=status.HTTP_204_NO_CONTENT)
def recommendation_feedback(payload: FeedbackIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    allowed = {"more_like", "less_like", "save", "wrong_genre", "wrong_language"}
    if payload.action not in allowed:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown feedback action")
    song = db.get(Song, payload.song_id)
    match = db.get(Song, payload.best_match_song_id) if payload.best_match_song_id else None
    if song is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Song not found")
    db.add(RecommendationFeedback(user_id=user.id, song_id=song.id, best_match_song_id=payload.best_match_song_id, action=payload.action))
    learned = db.get(UserTasteWeights, user.id) or UserTasteWeights(user_id=user.id)
    if payload.action in {"more_like", "less_like", "save"} and match and song.feature_vector is not None and match.feature_vector is not None:
        parts = sonic_distance_breakdown(song.feature_vector, song.genre_vector, match.feature_vector, match.genre_vector)
        values = {k: getattr(learned, k) for k in ("genre", "timbre", "rhythm", "tonal", "energy")}
        values = _adapt_weight_values(values, parts, payload.action)
        for key, value in values.items():
            setattr(learned, key, value)
    learned.feedback_count += 1
    db.add(learned)
    db.commit()
    recommendation_cache.invalidate_user(user.id)


@router.post("/{song_id}/hide", status_code=status.HTTP_204_NO_CONTENT)
def hide_recommendation(
    song_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> None:
    """Permanent dismiss — unlike `exclude`, this survives across sessions. recommend_for_user
    filters every HiddenSong out of the candidate pool for good."""
    db.add(HiddenSong(user_id=user.id, song_id=song_id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # already hidden — idempotent, not an error
    recommendation_cache.invalidate_user(user.id)


@router.delete("/{song_id}/hide", status_code=status.HTTP_204_NO_CONTENT)
def unhide_recommendation(
    song_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> None:
    result = db.execute(
        select(HiddenSong).where(HiddenSong.user_id == user.id, HiddenSong.song_id == song_id)
    ).scalar_one_or_none()
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not hidden")
    db.delete(result)
    db.commit()
    recommendation_cache.invalidate_user(user.id)
