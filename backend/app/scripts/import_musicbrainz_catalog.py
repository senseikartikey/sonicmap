"""Import the compact MusicBrainz canonical metadata CSV without loading it into memory.

Usage inside the API container after placing the CSV under backend/app/data (bind-mounted):
  python -m app.scripts.import_musicbrainz_catalog /app/app/data/canonical_musicbrainz_data.csv --limit 1000000
"""
from __future__ import annotations

import argparse
import bz2
import csv
import gzip
import lzma
import io
import tarfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO

from sqlalchemy import text

from app.db import SessionLocal


class _NonSeekableBinary(io.RawIOBase):
    """Makes tarfile's streaming member compatible with BufferedReader/TextIOWrapper."""

    def __init__(self, source):
        self.source = source

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def readinto(self, buffer) -> int:
        data = self.source.read(len(buffer))
        size = len(data)
        buffer[:size] = data
        return size

    def close(self) -> None:
        self.source.close()
        super().close()


@contextmanager
def _open_text(path: Path) -> Iterator[TextIO]:
    resources: list[object] = []
    if path.name.endswith(".tar.zst"):
        import zstandard

        raw = path.open("rb")
        reader = zstandard.ZstdDecompressor().stream_reader(raw)
        archive = tarfile.open(fileobj=reader, mode="r|")
        resources.extend((archive, reader, raw))
        member_file = None
        for member in archive:
            if member.isfile() and member.name.endswith("canonical_musicbrainz_data.csv"):
                member_file = archive.extractfile(member)
                break
        if member_file is None:
            archive.close()
            reader.close()
            raw.close()
            raise ValueError("archive does not contain canonical_musicbrainz_data.csv")
        handle = io.TextIOWrapper(
            io.BufferedReader(_NonSeekableBinary(member_file)), encoding="utf-8", newline=""
        )
    elif path.suffix == ".zst":
        import zstandard

        raw = path.open("rb")
        reader = zstandard.ZstdDecompressor().stream_reader(raw)
        resources.extend((reader, raw))
        handle = io.TextIOWrapper(reader, encoding="utf-8", newline="")
    elif path.suffix == ".gz":
        handle = gzip.open(path, "rt", encoding="utf-8", newline="")
    elif path.suffix == ".bz2":
        handle = bz2.open(path, "rt", encoding="utf-8", newline="")
    elif path.suffix in (".xz", ".lzma"):
        handle = lzma.open(path, "rt", encoding="utf-8", newline="")
    else:
        handle = path.open("r", encoding="utf-8", newline="")
    try:
        yield handle
    finally:
        handle.close()
        for resource in resources:
            try:
                resource.close()  # type: ignore[attr-defined]
            except Exception:
                pass


def import_catalog(path: Path, limit: int, sample_modulus: int = 1) -> int:
    db = SessionLocal()
    try:
        db.execute(text("""
            CREATE TEMP TABLE catalog_import_stage (
                recording_mbid text, title text, artist text, canonical_score double precision
            ) ON COMMIT DROP
        """))
        raw = db.connection().connection.driver_connection
        copied = 0
        with _open_text(path) as handle, raw.cursor().copy(
            "COPY catalog_import_stage (recording_mbid, title, artist, canonical_score) FROM STDIN"
        ) as copy:
            for row in csv.DictReader(handle):
                mbid = row.get("recording_mbid") or row.get("canonical_recording_mbid")
                title = row.get("recording_name") or row.get("title")
                artist = row.get("artist_credit_name") or row.get("artist")
                if not mbid or not title or not artist:
                    continue
                if sample_modulus > 1:
                    try:
                        if uuid.UUID(mbid).int % sample_modulus != 0:
                            continue
                    except ValueError:
                        continue
                try:
                    score = float(row.get("score") or 0)
                except ValueError:
                    score = 0.0
                copy.write_row((mbid, title, artist, score))
                copied += 1
                if copied >= limit:
                    break

        # Attach canonical IDs to already-extracted local rows before inserting new metadata.
        db.execute(text("""
            UPDATE songs s
            SET musicbrainz_id = st.recording_mbid,
                canonical_score = GREATEST(COALESCE(s.canonical_score, 0), st.canonical_score)
            FROM (
                SELECT DISTINCT ON (lower(title), lower(artist)) *
                FROM catalog_import_stage ORDER BY lower(title), lower(artist), canonical_score DESC
            ) st
            WHERE s.musicbrainz_id IS NULL
              AND lower(s.title) = lower(st.title) AND lower(s.artist) = lower(st.artist)
        """))
        db.execute(text("""
            INSERT INTO songs (
                id, title, artist, musicbrainz_id, resolution_status, extraction_status,
                catalog_status, canonical_score, created_at
            )
            SELECT gen_random_uuid(), st.title, st.artist, st.recording_mbid,
                   'pending', 'pending', 'metadata', st.canonical_score, now()
            FROM (
                SELECT DISTINCT ON (recording_mbid) * FROM catalog_import_stage
                ORDER BY recording_mbid, canonical_score DESC
            ) st
            WHERE NOT EXISTS (SELECT 1 FROM songs s WHERE s.musicbrainz_id = st.recording_mbid)
              AND NOT EXISTS (
                  SELECT 1 FROM songs s
                  WHERE lower(s.title) = lower(st.title) AND lower(s.artist) = lower(st.artist)
              )
            ON CONFLICT DO NOTHING
        """))
        db.commit()
        return copied
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--limit", type=int, default=1_000_000)
    parser.add_argument(
        "--sample-modulus", type=int, default=1,
        help="Keep roughly one in N MBIDs across the whole file for representative sampling",
    )
    args = parser.parse_args()
    count = import_catalog(args.path, args.limit, max(1, args.sample_modulus))
    print(f"Staged {count:,} canonical MusicBrainz rows")
