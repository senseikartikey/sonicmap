from __future__ import annotations

import asyncio
import math
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, defer

from app.config import settings
from app.db import SessionLocal, get_db
from app.dependencies import get_current_user
from app.models import Song, StemArtifact, StemJob, TasteShare, User, UserSong
from app.schemas import IngestSearchRequest, RecommendationOut, ShareCompareIn, SongOut, StemJobIn, StemMixIn, StemPromptIn, StemUploadIn
from app.services import stem_storage
from app.services.brain import recommend_for_user, sonic_distance_breakdown
from app.services.stem_intelligence import build_xray, interpret_mix_prompt, mix_distance, safe_mix_weights

router = APIRouter(prefix="/stems", tags=["stem-studio"])
ACTIVE = ("queued", "acquiring", "transcoding", "separating", "packaging")
ALLOWED_TYPES = {
    "audio/mpeg", "audio/mp3", "audio/mp4", "audio/x-m4a", "audio/aac", "audio/wav",
    "audio/x-wav", "audio/flac", "audio/ogg", "audio/opus", "application/ogg",
}
SAFE_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus"}
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}


def _enabled() -> None:
    if not settings.stem_studio_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stem Studio is not enabled")


def _valid_youtube_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and (parsed.hostname or "").lower() in YOUTUBE_HOSTS


def _job_out(job: StemJob) -> dict:
    return {
        "id": str(job.id), "source_type": job.source_type, "source_name": job.source_name,
        "model": job.model, "status": job.status, "stage": job.stage,
        "progress": job.progress, "error_code": job.error_code,
        "error_message": job.error_message, "duration_seconds": job.duration_seconds,
        "analysis_ready": bool(job.analysis_json and job.analysis_json.get("stems")),
        "mapped_song_id": str(job.mapped_song_id) if job.mapped_song_id else None,
        "reused": bool(job.reused_from_job_id),
        "created_at": job.created_at.isoformat(),
        "expires_at": job.expires_at.isoformat() if job.expires_at else None,
    }


def _owned_job(db: Session, user_id: uuid.UUID, job_id: uuid.UUID) -> StemJob:
    job = db.execute(select(StemJob).where(StemJob.id == job_id, StemJob.user_id == user_id)).scalar_one_or_none()
    if job is None:
        raise HTTPException(404, "Stem job not found")
    return job


def _discover_blocking(user_id: uuid.UUID, analysis: dict, weights: dict[str, float], limit: int) -> list[RecommendationOut]:
    worker_db = SessionLocal()
    try:
        core = asyncio.run(recommend_for_user(worker_db, user_id, limit=100))
        reranked = []
        for candidate, brain_distance, best_match, cluster_size in core:
            audible_distance = mix_distance(analysis, weights, candidate)
            final_distance = 0.45 * brain_distance + 0.55 * audible_distance
            reranked.append((final_distance, candidate, best_match, cluster_size, audible_distance))
        reranked.sort(key=lambda row: row[0])
        return [
            RecommendationOut(
                song=SongOut.model_validate(candidate),
                distance=distance,
                reason=f"Your live stem balance is {round(100 * math.exp(-max(0.0, audible) * 1.15))}% compatible; your existing brain remains part of the rank.",
                best_match_song_id=best_match.id,
                xray=sonic_distance_breakdown(candidate.feature_vector, candidate.genre_vector, best_match.feature_vector, best_match.genre_vector),
            )
            for distance, candidate, best_match, _cluster_size, audible in reranked[:limit]
        ]
    finally:
        worker_db.close()


@router.post("/uploads", status_code=201)
def create_upload(payload: StemUploadIn, user: User = Depends(get_current_user)):
    _enabled()
    suffix = Path(payload.filename).suffix.lower()
    if payload.content_type.lower() not in ALLOWED_TYPES or suffix not in SAFE_EXTENSIONS:
        raise HTTPException(415, "Use an MP3, M4A, AAC, WAV, FLAC, OGG, or Opus audio file")
    if payload.size_bytes > settings.stem_max_upload_bytes:
        raise HTTPException(413, "Audio files must be 250 MB or smaller")
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", Path(payload.filename).name).strip(".-") or f"audio{suffix}"
    key = f"users/{user.id}/uploads/{uuid.uuid4()}/{safe_name}"
    signed = stem_storage.presigned_upload(key, payload.content_type.lower(), settings.stem_max_upload_bytes)
    return {
        "upload_key": key,
        "upload_url": signed["url"],
        "upload_fields": signed["fields"],
        "expires_in": 900,
    }


@router.post("/jobs", status_code=202)
def create_job(payload: StemJobIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _enabled()
    if not payload.rights_confirmed:
        raise HTTPException(422, "Confirm that you have permission to process this audio")
    now = datetime.now(timezone.utc)
    active = db.execute(
        select(func.count()).select_from(StemJob).where(StemJob.user_id == user.id, StemJob.status.in_(ACTIVE))
    ).scalar_one()
    if active >= settings.stem_max_active_jobs:
        raise HTTPException(409, "Finish or cancel your active stem job before starting another")
    recent = db.execute(
        select(func.count()).select_from(StemJob).where(
            StemJob.user_id == user.id, StemJob.created_at >= now - timedelta(hours=24)
        )
    ).scalar_one()
    if recent >= settings.stem_daily_job_limit:
        raise HTTPException(429, "You have reached the three-job daily Stem Studio limit")

    if payload.source_type == "upload":
        prefix = f"users/{user.id}/uploads/"
        if not payload.upload_key or not payload.upload_key.startswith(prefix) or ".." in payload.upload_key:
            raise HTTPException(422, "Upload this audio through Stem Studio first")
        source_ref = payload.upload_key
        source_name = payload.source_name or Path(payload.upload_key).name
    else:
        if not settings.stem_youtube_enabled:
            raise HTTPException(403, "YouTube import is not enabled for this deployment")
        if not _valid_youtube_url(payload.youtube_url or ""):
            raise HTTPException(422, "Enter a valid HTTPS YouTube URL")
        source_ref = payload.youtube_url or ""
        source_name = payload.source_name or "YouTube audio"

    job = StemJob(
        user_id=user.id, source_type=payload.source_type, source_ref=source_ref,
        source_name=source_name, model=payload.model, status="queued", stage="queued", progress=0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return _job_out(job)


@router.get("/jobs")
def list_jobs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _enabled()
    jobs = db.execute(
        select(StemJob).where(StemJob.user_id == user.id).order_by(StemJob.created_at.desc()).limit(20)
    ).scalars().all()
    return [_job_out(job) for job in jobs]


@router.get("/jobs/{job_id}")
def get_job(job_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _enabled()
    return _job_out(_owned_job(db, user.id, job_id))


@router.get("/jobs/{job_id}/xray")
def get_xray(job_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _enabled()
    job = _owned_job(db, user.id, job_id)
    if not job.analysis_json or not job.analysis_json.get("stems"):
        raise HTTPException(409, "Stem intelligence is not ready yet")
    # Cluster labels ride along so build_xray can tell a genuine cross-cluster Resonance Thread
    # (the whole point) from a match that's just restating the song's own existing cluster.
    rows = db.execute(
        select(Song, UserSong.cluster_label).options(defer(Song.retrieval_vector))
        .join(UserSong, UserSong.song_id == Song.id)
        .where(UserSong.user_id == user.id, Song.feature_vector.is_not(None))
    ).all()
    cluster_by_id = {str(song.id): cluster for song, cluster in rows}
    songs = [song for song, _cluster in rows if song.id != job.mapped_song_id]
    source_cluster = cluster_by_id.get(str(job.mapped_song_id)) if job.mapped_song_id else None
    return {
        "job": _job_out(job),
        **build_xray(job.analysis_json, songs, cluster_by_id=cluster_by_id, source_cluster=source_cluster),
    }


@router.post("/jobs/{job_id}/add-to-map")
async def add_job_to_map(job_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _enabled()
    job = _owned_job(db, user.id, job_id)
    if job.status != "ready":
        raise HTTPException(409, "Finish separating this song before adding it to your map")
    if job.mapped_song_id:
        song = db.get(Song, job.mapped_song_id)
        if song:
            return {"song": SongOut.model_validate(song), "was_new": False}
    query = re.sub(r"\.(mp3|m4a|aac|wav|flac|ogg|opus)$", "", job.source_name or "", flags=re.I)
    query = query.replace("_", " ").strip()
    if not query or query.startswith("Fetching YouTube"):
        raise HTTPException(409, "Song details are not ready yet")
    # Reuse the canonical ingestion path so resolution, extraction, language/genre checks,
    # cache invalidation and map rebuilding cannot drift into a Stem-Studio-only fork.
    from app.routers.ingest import ingest_search
    result = await ingest_search(IngestSearchRequest(query=query), user, db)
    job.mapped_song_id = result.song.id
    db.commit()
    return result


@router.post("/jobs/{job_id}/discover", response_model=list[RecommendationOut])
async def discover_from_mix(
    job_id: uuid.UUID,
    payload: StemMixIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[RecommendationOut]:
    _enabled()
    job = _owned_job(db, user.id, job_id)
    if not job.analysis_json or not job.analysis_json.get("stems"):
        raise HTTPException(409, "Stem intelligence is not ready yet")
    weights = safe_mix_weights(job.analysis_json, payload.weights)
    # Start with the exact production brain shortlist (cluster consensus + learned taste),
    # then let the audible mix rerank it. This keeps the user's brain as a 45% guardrail.
    return await asyncio.to_thread(_discover_blocking, user.id, job.analysis_json, weights, payload.limit)


@router.post("/jobs/{job_id}/interpret-mix")
def interpret_mix(
    job_id: uuid.UUID,
    payload: StemPromptIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _enabled()
    job = _owned_job(db, user.id, job_id)
    if not job.analysis_json or not job.analysis_json.get("stems"):
        raise HTTPException(409, "Stem intelligence is not ready yet")
    try:
        weights, explanation = interpret_mix_prompt(job.analysis_json, payload.prompt, payload.weights)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"weights": weights, "explanation": explanation}


@router.post("/jobs/{job_id}/compare")
def compare_stem_brains(
    job_id: uuid.UUID,
    payload: ShareCompareIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _enabled()
    job = _owned_job(db, user.id, job_id)
    if not job.analysis_json or not job.analysis_json.get("stems"):
        raise HTTPException(409, "Stem intelligence is not ready yet")
    share = db.execute(
        select(TasteShare).where(TasteShare.token == payload.token, TasteShare.active.is_(True))
    ).scalar_one_or_none()
    if share is None:
        raise HTTPException(404, "That private brain code is invalid or inactive")

    def taste_songs(user_id: uuid.UUID) -> list[Song]:
        return db.execute(
            select(Song).options(defer(Song.retrieval_vector))
            .join(UserSong, UserSong.song_id == Song.id)
            .where(UserSong.user_id == user_id, Song.feature_vector.is_not(None))
            .limit(500)
        ).scalars().all()

    mine = build_xray(job.analysis_json, taste_songs(user.id))
    theirs = build_xray(job.analysis_json, taste_songs(share.user_id))
    their_by_name = {row["name"]: row for row in theirs["stems"]}
    overlaps = []
    layers = []
    for row in mine["stems"]:
        other = their_by_name.get(row["name"])
        connection = None
        if row["match"] is not None and other and other["match"] is not None:
            connection = 100 - abs(row["match"] - other["match"])
            overlaps.append(connection)
        layers.append({"name": row["name"], "label": row["label"], "mine": row["match"], "theirs": other["match"] if other else None, "connection": connection})
    return {"compatibility": round(sum(overlaps) / len(overlaps)) if overlaps else None, "layers": layers}


@router.get("/jobs/{job_id}/manifest")
def get_manifest(job_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _enabled()
    job = _owned_job(db, user.id, job_id)
    if job.status == "expired" or (job.expires_at and job.expires_at <= datetime.now(timezone.utc)):
        raise HTTPException(410, "These stem files have expired")
    if job.status != "ready":
        raise HTTPException(409, "Stem files are not ready yet")
    artifacts = db.execute(
        select(StemArtifact).where(StemArtifact.job_id == job.id).order_by(StemArtifact.kind, StemArtifact.name)
    ).scalars().all()
    by_stem: dict[str, dict] = {}
    archive = None
    for artifact in artifacts:
        if artifact.kind == "archive":
            archive_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", job.source_name or "sonicmap-stems").strip(".-")
            archive = stem_storage.presigned_download(artifact.object_key, f"{archive_name or 'sonicmap-stems'}.zip")
            continue
        item = by_stem.setdefault(artifact.name, {"name": artifact.name})
        if artifact.kind == "stem":
            item["download_url"] = stem_storage.presigned_download(artifact.object_key, f"{artifact.name}.wav")
        elif artifact.kind == "preview":
            item["preview_url"] = stem_storage.presigned_download(artifact.object_key)
        elif artifact.kind == "waveform":
            item["waveform_url"] = stem_storage.presigned_download(artifact.object_key)
    return {"job": _job_out(job), "stems": list(by_stem.values()), "archive_url": archive}


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _enabled()
    job = _owned_job(db, user.id, job_id)
    job.cancel_requested = True
    if job.status == "queued":
        job.status, job.stage, job.finished_at = "cancelled", "cancelled", datetime.now(timezone.utc)
    if job.status in ("ready", "failed", "cancelled", "expired"):
        artifacts = db.execute(select(StemArtifact).where(StemArtifact.job_id == job.id)).scalars().all()
        stem_storage.delete_keys([a.object_key for a in artifacts] + ([job.source_ref] if job.source_type == "upload" else []))
        for artifact in artifacts:
            db.delete(artifact)
        job.status, job.stage, job.expires_at = "cancelled", "cancelled", datetime.now(timezone.utc)
    db.commit()
    return Response(status_code=204)
