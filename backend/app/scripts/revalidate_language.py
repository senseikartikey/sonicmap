"""One-off maintenance script: re-validates EVERY catalog song's language against Gemini,
catching the residual cases fastText answers *confidently but wrong* on — the class of bug
backfill_language.py's normal fastText-then-escalate-on-None flow structurally can't reach
(see app/services/language.py's module docstring: fastText sometimes returns "en" with real
confidence on a title it was never trained to read correctly, so there's no None to escalate
on). This is the wider, costlier "double-check everything" pass — run only after
backfill_language.py's normal escalate-on-None pass already handled the cheap majority of the
problem.

Only overwrites Song.language when Gemini gives a *specific, different* answer. Gemini saying
"unknown" on a song that already has a value never downgrades it — Gemini's own abstention
isn't evidence the existing value is wrong, and Gemini isn't a ground-truth oracle either (a
prior run already caught it getting a real Hindi title wrong as "en" too) — this is a
best-effort improvement over fastText alone, not a claim of perfect accuracy.

Run inside the backend container:
    docker compose exec api python -m app.scripts.revalidate_language
"""

import asyncio

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Song
from app.services.language import classify_languages_via_llm


async def run() -> None:
    db = SessionLocal()
    try:
        songs = db.execute(select(Song).where(Song.feature_vector.is_not(None))).scalars().all()
        print(f"Re-validating language for {len(songs)} catalog songs via Gemini (batched)...")

        batch = [(str(s.id), s.title, s.artist) for s in songs]
        results = await classify_languages_via_llm(batch)
        print(f"Gemini answered for {len(results)}/{len(songs)} songs.")

        by_id = {str(s.id): s for s in songs}
        changed = 0
        for song_id, lang in results.items():
            if lang is None:
                continue  # Gemini's own "unknown" never downgrades an existing value
            song = by_id.get(song_id)
            if song is None or song.language == lang:
                continue
            print(f"  {song.language!r:>8} -> {lang!r:8} | {song.artist} - {song.title}")
            song.language = lang
            changed += 1
        db.commit()
        print(f"Changed: {changed}/{len(songs)}")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(run())
