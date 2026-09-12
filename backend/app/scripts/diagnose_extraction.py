"""Run one failed song through extraction without swallowing the real exception."""
import asyncio
import sys
import traceback
from app.db import SessionLocal
from app.models import Song
from app.services.extraction import apply_extracted_features, extract_features

async def main(song_id):
    db = SessionLocal()
    try:
        song = db.get(Song, song_id)
        print(f"song={song.title} preview={bool(song.preview_url)}")
        try:
            features = await extract_features(song.preview_url)
            print(f"bpm={features.bpm} energy={features.energy} danceability={features.danceability} vector={len(features.vector)} genre_vector={len(features.genre_vector)}")
            apply_extracted_features(song, features)
            print("validation=passed")
        except Exception:
            traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__": asyncio.run(main(sys.argv[1]))
