"""Lease-safe Stem Studio worker pipeline.

The API only queues work. This module belongs in the dedicated CPU/GPU worker image so a
large Demucs model can never consume the recommendation API's memory or event loop.
"""
from __future__ import annotations

import json
import hashlib
import logging
import math
import os
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import StemArtifact, StemJob
from app.services import stem_storage

logger = logging.getLogger(__name__)
LEASE_TIMEOUT = timedelta(minutes=10)


class PermanentJobError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class CancelledJob(RuntimeError):
    pass


def _claim_one(db: Session) -> uuid.UUID | None:
    now = datetime.now(timezone.utc)
    job = db.execute(
        select(StemJob)
        .where(
            StemJob.attempts < settings.stem_worker_max_attempts,
            or_(
                StemJob.status == "queued",
                (StemJob.status.in_(("acquiring", "transcoding", "separating", "packaging")))
                & (StemJob.heartbeat_at < now - LEASE_TIMEOUT),
            ),
        )
        .order_by(StemJob.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    ).scalar_one_or_none()
    if job is None:
        return None
    if job.cancel_requested:
        job.status, job.stage, job.finished_at = "cancelled", "cancelled", now
        db.commit()
        return None
    job.status = job.stage = "acquiring"
    job.progress = max(job.progress, 2)
    job.started_at = job.started_at or now
    job.heartbeat_at = now
    job.attempts += 1
    job.error_code = job.error_message = None
    db.commit()
    logger.info("Claimed stem job %s model=%s source=%s attempt=%d", job.id, job.model, job.source_type, job.attempts)
    return job.id


def _update(db: Session, job: StemJob, stage: str, progress: int) -> None:
    db.refresh(job)
    if job.cancel_requested:
        raise CancelledJob()
    job.status = job.stage = stage
    job.progress = max(job.progress, min(progress, 99))
    job.heartbeat_at = datetime.now(timezone.utc)
    db.commit()


def _run(command: list[str], db: Session, job: StemJob, stage: str, start: int, end: int) -> None:
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    last_heartbeat = 0.0
    lines: list[str] = []
    assert process.stdout is not None
    output_ready = selectors.DefaultSelector()
    output_ready.register(process.stdout, selectors.EVENT_READ)
    while process.poll() is None:
        for key, _events in output_ready.select(timeout=1):
            line = key.fileobj.readline()
            if line:
                lines.append(line.strip())
                lines = lines[-20:]
        now = time.monotonic()
        if now - last_heartbeat >= 2:
            db.refresh(job)
            if job.cancel_requested:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise CancelledJob()
            # Demucs/ffmpeg do not expose one stable machine-readable percentage across all
            # versions. Advance within the stage without ever claiming completion early.
            job.progress = min(end - 1, max(job.progress, start) + 1)
            job.heartbeat_at = datetime.now(timezone.utc)
            db.commit()
            last_heartbeat = now
    output_ready.close()
    remainder = process.stdout.read()
    if remainder:
        lines.extend(remainder.splitlines()[-20:])
    if process.returncode:
        raise RuntimeError(f"{stage} failed: {' | '.join(lines[-4:])}"[:1800])


def _probe(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode:
        raise PermanentJobError("invalid_audio", "The file is not valid playable audio")
    try:
        duration = float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise PermanentJobError("invalid_audio", "The audio duration could not be read") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise PermanentJobError("invalid_audio", "The audio file is empty")
    if duration > settings.stem_max_duration_seconds:
        raise PermanentJobError("too_long", "Tracks must be 15 minutes or shorter")
    return duration


def _youtube_source_name(info: dict) -> str | None:
    """Build a useful, bounded display name from yt-dlp's trusted metadata fields."""
    title = str(info.get("track") or info.get("title") or "").strip()
    creator = str(info.get("artist") or info.get("uploader") or info.get("channel") or "").strip()
    if not title:
        return None
    if creator and creator.casefold() not in title.casefold():
        title = f"{title} — {creator}"
    return title[:500]


def _waveform(path: Path, output: Path, points: int = 1200) -> None:
    # Demucs may emit float, PCM16, or WAVE_FORMAT_EXTENSIBLE depending on its version and
    # device. Let ffmpeg normalize that variability instead of teaching the worker a partial
    # WAV parser that silently breaks on one GPU image.
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "f32le", "pipe:1"],
        capture_output=True, timeout=120,
    )
    if result.returncode:
        raise RuntimeError("Could not generate waveform peaks")
    mono = np.abs(np.frombuffer(result.stdout, dtype="<f4"))
    if not len(mono):
        raise RuntimeError("Stem waveform is empty")
    window = max(1, math.ceil(len(mono) / points))
    padded = np.pad(mono, (0, (-len(mono)) % window))
    peaks = np.clip(padded.reshape(-1, window).max(axis=1), 0, 1)
    output.write_text(json.dumps({"peaks": np.round(peaks, 4).tolist()}), encoding="utf-8")


def _analyze_stem(path: Path) -> dict:
    """Create a compact SonicDistance-compatible fingerprint without loading API models."""
    import soundfile as sf

    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = audio.mean(axis=1)
    if sample_rate != 16000 and len(mono):
        target_size = max(1, round(len(mono) * 16000 / sample_rate))
        mono = np.interp(
            np.linspace(0, len(mono) - 1, target_size),
            np.arange(len(mono)), mono,
        ).astype(np.float32)
        sample_rate = 16000
    frame_size, hop = 2048, 512
    if len(mono) < frame_size:
        mono = np.pad(mono, (0, frame_size - len(mono)))
    count = 1 + (len(mono) - frame_size) // hop
    analysis_stride = hop * max(1, math.ceil(count / 4000))
    frames = np.lib.stride_tricks.sliding_window_view(mono, frame_size)[::analysis_stride]
    spectrum = np.abs(np.fft.rfft(frames * np.hanning(frame_size), axis=1)) + 1e-9
    power = spectrum ** 2
    frequencies = np.fft.rfftfreq(frame_size, 1 / sample_rate)

    # A deterministic 40-band log spectrum + cosine transform gives the same 13+13
    # mean/std timbre layout used by the core brain, without shipping the large genre model.
    band_edges = np.linspace(0, power.shape[1], 41, dtype=int)
    bands = np.stack([power[:, band_edges[i]:max(band_edges[i] + 1, band_edges[i + 1])].mean(axis=1) for i in range(40)], axis=1)
    log_bands = np.log1p(bands)
    dct = np.cos(np.pi / 40 * (np.arange(40) + 0.5)[None, :] * np.arange(13)[:, None])
    mfcc = log_bands @ dct.T
    scale = np.maximum(np.linalg.norm(mfcc, axis=1, keepdims=True), 1e-9)
    mfcc = mfcc / scale

    rms = float(np.sqrt(np.mean(mono ** 2)))
    energy = float(np.clip((20 * np.log10(rms + 1e-9) + 40) / 40, 0, 1))
    flux = np.maximum(0, np.diff(spectrum, axis=0)).sum(axis=1)
    flux = flux - flux.mean() if len(flux) else flux
    fps = sample_rate / analysis_stride
    best_bpm, pulse = 100.0, 0.0
    if len(flux) > 8 and np.linalg.norm(flux) > 0:
        autocorrelation = np.correlate(flux, flux, mode="full")[len(flux) - 1:]
        bpms = np.arange(60, 201)
        lags = np.clip(np.rint(fps * 60 / bpms).astype(int), 1, len(autocorrelation) - 1)
        strengths = autocorrelation[lags] / (autocorrelation[0] + 1e-9)
        winner = int(np.argmax(strengths))
        best_bpm, pulse = float(bpms[winner]), float(np.clip(strengths[winner], 0, 1))

    chroma = np.zeros(12)
    valid = frequencies > 40
    pitch_classes = np.rint(69 + 12 * np.log2(frequencies[valid] / 440)).astype(int) % 12
    spectral_sum = power[:, valid].sum(axis=0)
    for pitch_class, value in zip(pitch_classes, spectral_sum, strict=True):
        chroma[pitch_class] += value
    key_index = int(np.argmax(chroma)) if chroma.any() else 0
    angle = 2 * math.pi * key_index / 12
    major_template = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    minor_template = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
    mode = 1.0 if np.dot(chroma, np.roll(major_template, key_index)) >= np.dot(chroma, np.roll(minor_template, key_index)) else 0.0
    centroid = float(np.mean((power * frequencies).sum(axis=1) / power.sum(axis=1)))
    low_ratio = float(power[:, frequencies < 250].sum() / power.sum())
    vector = [best_bpm / 200, pulse, energy] + mfcc.mean(axis=0).tolist() + mfcc.std(axis=0).tolist() + [math.sin(angle), math.cos(angle), mode]
    return {
        "feature_vector": [round(float(value), 6) for value in vector],
        "metrics": {
            "bpm": round(best_bpm, 1), "energy": round(energy, 3),
            "pulse": round(pulse, 3), "brightness": round(min(1.0, centroid / 6000), 3),
            "low_end": round(min(1.0, low_ratio), 3), "rms": rms,
        },
    }


def _upload_artifact(client, db: Session, job: StemJob, kind: str, name: str, path: Path, mime: str) -> None:
    key = f"users/{job.user_id}/jobs/{job.id}/{kind}/{path.name}"
    client.upload_file(str(path), settings.stem_s3_bucket, key, ExtraArgs={"ContentType": mime})
    db.add(StemArtifact(
        job_id=job.id, kind=kind, name=name, object_key=key, mime_type=mime,
        size_bytes=path.stat().st_size, metadata_json={},
    ))
    db.commit()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reuse_separation(client, db: Session, job: StemJob) -> bool:
    now = datetime.now(timezone.utc)
    previous = db.execute(
        select(StemJob).where(
            StemJob.id != job.id,
            StemJob.source_hash == job.source_hash,
            StemJob.model == job.model,
            StemJob.status == "ready",
            StemJob.expires_at > now,
        ).order_by(StemJob.finished_at.desc()).limit(1)
    ).scalar_one_or_none()
    if previous is None:
        return False
    artifacts = db.execute(select(StemArtifact).where(StemArtifact.job_id == previous.id)).scalars().all()
    if not artifacts:
        return False
    copied_keys = []
    try:
        for artifact in artifacts:
            filename = artifact.object_key.rsplit("/", 1)[-1]
            key = f"users/{job.user_id}/jobs/{job.id}/{artifact.kind}/{filename}"
            client.copy_object(
                Bucket=settings.stem_s3_bucket,
                Key=key,
                CopySource={"Bucket": settings.stem_s3_bucket, "Key": artifact.object_key},
                ContentType=artifact.mime_type,
                MetadataDirective="REPLACE",
            )
            copied_keys.append(key)
            db.add(StemArtifact(
                job_id=job.id, kind=artifact.kind, name=artifact.name, object_key=key,
                mime_type=artifact.mime_type, size_bytes=artifact.size_bytes,
                metadata_json=artifact.metadata_json or {},
            ))
        job.analysis_json = previous.analysis_json or {}
        job.duration_seconds = previous.duration_seconds
        job.reused_from_job_id = previous.id
        job.status = job.stage = "ready"
        job.progress = 100
        job.finished_at = now
        job.expires_at = now + timedelta(hours=settings.stem_retention_hours)
        job.heartbeat_at = now
        db.commit()
        logger.info("Reused stem job %s for %s hash=%s", previous.id, job.id, job.source_hash)
        return True
    except Exception:
        db.rollback()
        stem_storage.delete_keys(copied_keys)
        logger.warning("Could not reuse stem job %s; falling back to separation", previous.id, exc_info=True)
        return False


def process_job(job_id: uuid.UUID) -> None:
    db = SessionLocal()
    workspace = Path(tempfile.mkdtemp(prefix=f"sonicmap-stems-{job_id}-"))
    job: StemJob | None = None
    try:
        job = db.get(StemJob, job_id)
        if job is None:
            return
        client = stem_storage.internal_client()
        source = workspace / "source"
        if job.source_type == "upload":
            try:
                head = client.head_object(Bucket=settings.stem_s3_bucket, Key=job.source_ref)
            except Exception as exc:
                raise PermanentJobError("upload_missing", "The uploaded audio could not be found") from exc
            if int(head.get("ContentLength", 0)) > settings.stem_max_upload_bytes:
                raise PermanentJobError("too_large", "Audio files must be 250 MB or smaller")
            client.download_file(settings.stem_s3_bucket, job.source_ref, str(source))
        else:
            output = workspace / "youtube.%(ext)s"
            _run(
                ["yt-dlp", "--no-playlist", "--max-filesize", str(settings.stem_max_upload_bytes),
                 "--write-info-json", "--no-clean-info-json",
                 "-f", "bestaudio", "-o", str(output), job.source_ref],
                db, job, "acquiring", 3, 12,
            )
            info_files = list(workspace.glob("youtube.info.json"))
            if info_files:
                try:
                    display_name = _youtube_source_name(json.loads(info_files[0].read_text(encoding="utf-8")))
                    if display_name:
                        job.source_name = display_name
                        db.commit()
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    logger.warning("Could not read YouTube metadata for stem job %s", job.id, exc_info=True)
            matches = [path for path in workspace.glob("youtube.*") if path.suffix != ".json"]
            if not matches:
                raise PermanentJobError("youtube_unavailable", "YouTube audio is unavailable")
            source = matches[0]
        _update(db, job, "transcoding", 14)
        duration = _probe(source)
        job.duration_seconds = duration
        normalized = workspace / "normalized.wav"
        _run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(source), "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(normalized)],
            db, job, "transcoding", 15, 24,
        )
        job.source_hash = _sha256(normalized)
        db.commit()
        if _reuse_separation(client, db, job):
            return
        _update(db, job, "separating", 25)
        model = "htdemucs_6s" if job.model == "6stem" else "htdemucs"
        separated = workspace / "separated"
        _run(
            [sys.executable, "-m", "demucs.separate", "-n", model, "--shifts", "1", "--overlap", "0.25", "-o", str(separated), str(normalized)],
            db, job, "separating", 26, 82,
        )
        stem_dir = separated / model / normalized.stem
        stems = sorted(stem_dir.glob("*.wav"))
        expected = 6 if job.model == "6stem" else 4
        if len(stems) != expected:
            raise RuntimeError(f"Separation returned {len(stems)} of {expected} expected stems")
        _update(db, job, "packaging", 84)
        stem_analysis = {stem.stem: _analyze_stem(stem) for stem in stems}
        total_rms = sum(item["metrics"]["rms"] for item in stem_analysis.values()) or 1.0
        for item in stem_analysis.values():
            item["metrics"]["prominence"] = round(item["metrics"].pop("rms") / total_rms, 4)
        job.analysis_json = {"version": 1, "stems": stem_analysis}
        db.commit()
        archive_path = workspace / "sonicmap-stems.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=4) as archive:
            for index, stem in enumerate(stems):
                name = stem.stem
                preview = workspace / f"{name}.opus"
                peaks = workspace / f"{name}.json"
                subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-i", str(stem), "-c:a", "libopus", "-b:a", "128k", str(preview)],
                    check=True, timeout=max(120, int(duration * 2)),
                )
                _waveform(stem, peaks)
                archive.write(stem, arcname=f"{name}.wav")
                _upload_artifact(client, db, job, "stem", name, stem, "audio/wav")
                _upload_artifact(client, db, job, "preview", name, preview, "audio/ogg")
                _upload_artifact(client, db, job, "waveform", name, peaks, "application/json")
                _update(db, job, "packaging", 85 + int(11 * (index + 1) / len(stems)))
        _upload_artifact(client, db, job, "archive", "all", archive_path, "application/zip")
        job.status = job.stage = "ready"
        job.progress = 100
        job.finished_at = datetime.now(timezone.utc)
        job.expires_at = job.finished_at + timedelta(hours=settings.stem_retention_hours)
        job.heartbeat_at = job.finished_at
        db.commit()
        logger.info("Completed stem job %s model=%s duration=%.2fs", job.id, job.model, duration)
    except CancelledJob:
        db.rollback()
        job = db.get(StemJob, job_id)
        if job:
            job.status, job.stage, job.finished_at = "cancelled", "cancelled", datetime.now(timezone.utc)
            db.commit()
    except Exception as exc:
        db.rollback()
        job = db.get(StemJob, job_id)
        if job:
            permanent = isinstance(exc, PermanentJobError)
            job.error_code = exc.code if permanent else "processing_failed"
            job.error_message = str(exc)[:1000] if permanent else "Stem separation failed. You can retry this job."
            if permanent or job.attempts >= settings.stem_worker_max_attempts:
                job.status = job.stage = "failed"
                job.finished_at = datetime.now(timezone.utc)
                job.expires_at = job.finished_at + timedelta(hours=settings.stem_retention_hours)
            else:
                job.status = job.stage = "queued"
                job.progress = 0
            db.commit()
            if job.status == "queued":
                # A retry starts with an empty manifest. Keep the source object, but remove
                # artifacts from an interrupted packaging/upload attempt so unique keys and
                # stale media can never leak into the next run.
                try:
                    partial = db.execute(select(StemArtifact).where(StemArtifact.job_id == job.id)).scalars().all()
                    stem_storage.delete_keys([artifact.object_key for artifact in partial])
                    db.execute(delete(StemArtifact).where(StemArtifact.job_id == job.id))
                    db.commit()
                except Exception:
                    db.rollback()
                    logger.exception("Could not clean partial artifacts for stem job %s", job_id)
        logger.exception("Stem job %s failed", job_id)
    finally:
        if job and job.source_type == "upload" and job.status != "queued":
            try:
                stem_storage.delete_keys([job.source_ref])
            except Exception:
                logger.exception("Could not delete source for stem job %s", job_id)
        shutil.rmtree(workspace, ignore_errors=True)
        db.close()


def expire_jobs() -> int:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        jobs = db.execute(
            select(StemJob).where(StemJob.expires_at.is_not(None), StemJob.expires_at <= now, StemJob.status != "expired")
        ).scalars().all()
        for job in jobs:
            artifacts = db.execute(select(StemArtifact).where(StemArtifact.job_id == job.id)).scalars().all()
            stem_storage.delete_keys([artifact.object_key for artifact in artifacts])
            db.execute(delete(StemArtifact).where(StemArtifact.job_id == job.id))
            job.status = job.stage = "expired"
        # Presigned uploads can be abandoned before a job is created (closed tab, quota
        # rejection, lost network). Sweep those too so the 24-hour privacy promise covers
        # every uploaded byte, not only successfully submitted jobs.
        client = stem_storage.internal_client()
        stale_upload_keys: list[str] = []
        continuation = None
        cutoff = now - timedelta(hours=settings.stem_retention_hours)
        while True:
            kwargs = {"Bucket": settings.stem_s3_bucket, "Prefix": "users/"}
            if continuation:
                kwargs["ContinuationToken"] = continuation
            page = client.list_objects_v2(**kwargs)
            stale_upload_keys.extend(
                item["Key"] for item in page.get("Contents", [])
                if "/uploads/" in item["Key"] and item["LastModified"] <= cutoff
            )
            if not page.get("IsTruncated"):
                break
            continuation = page.get("NextContinuationToken")
        stem_storage.delete_keys(stale_upload_keys)
        db.commit()
        if jobs:
            logger.info("Expired %d stem job(s) and %d abandoned upload(s)", len(jobs), len(stale_upload_keys))
        return len(jobs)
    finally:
        db.close()


def worker_loop(poll_seconds: float = 2.0) -> None:
    last_cleanup = 0.0
    while True:
        if time.monotonic() - last_cleanup > 300:
            try:
                expire_jobs()
            except Exception:
                logger.exception("Stem expiry sweep failed")
            last_cleanup = time.monotonic()
        db = SessionLocal()
        try:
            job_id = _claim_one(db)
        finally:
            db.close()
        if job_id is None:
            time.sleep(poll_seconds)
        else:
            process_job(job_id)
