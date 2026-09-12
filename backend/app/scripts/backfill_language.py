"""One-off/periodic maintenance script: detects language for every catalog song that doesn't
have one yet, so the recommendations panel's English/mixed filter works across the whole
catalog, not just newly-ingested songs.

Two-pass hybrid, cheapest-first: fastText (app/services/language.py's detect_language, free,
instant, local) runs on every song first. Only the songs fastText couldn't confidently
classify — genuinely ambiguous titles, and Romanized Hindi/Urdu titles fastText was never
trained on — get escalated to Gemini in batches (classify_languages_via_llm). This keeps the
common case free and only pays for the cases that actually need it.

Run inside the backend container:
    docker compose exec api python -m app.scripts.backfill_language
"""

import asyncio

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Song
from app.services.language import classify_languages_via_llm, detect_language, needs_language_review


async def run() -> None:
    db = SessionLocal()
    try:
        candidates = db.execute(select(Song)).scalars().all()
        songs = [
            song for song in candidates
            if song.language is None or needs_language_review(song.title, song.artist, song.language)
        ]
        print(f"Pass 1 (fastText, free): classifying {len(songs)} songs...")

        unresolved: list[tuple[str, str, str]] = []
        fasttext_hits = 0
        for song in songs:
            # Re-run suspicious stored-English rows through the same conservative gate rather
            # than preserving a historical fastText false positive forever.
            song.language = detect_language(song.title, song.artist)
            if song.language is not None:
                fasttext_hits += 1
            else:
                unresolved.append((str(song.id), song.title, song.artist))
        db.commit()
        print(f"  fastText classified {fasttext_hits}/{len(songs)}; {len(unresolved)} left unresolved.")

        if unresolved:
            print(f"Pass 2 (Gemini, batched): escalating {len(unresolved)} unresolved songs...")
            llm_results = await classify_languages_via_llm(unresolved)
            by_id = {str(song.id): song for song in songs}
            llm_hits = 0
            for song_id, lang in llm_results.items():
                song = by_id.get(song_id)
                if song is None or lang is None:
                    continue
                song.language = lang
                llm_hits += 1
            db.commit()
            print(f"  Gemini classified {llm_hits}/{len(unresolved)}.")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(run())
