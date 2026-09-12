import asyncio
import re
import secrets
import unicodedata
import uuid
from datetime import datetime, timezone

import httpx
import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user, get_valid_spotify_token
from app.models import Song, TasteShare, User, UserSong, UserTasteWeights
from app.schemas import JourneyIn, PromptDiscoveryIn, ShareCompareIn, SongOut
from app.services import spotify_auth
from app.services.brain import DEFAULT_WEIGHTS, SonicDistanceWeights, score_candidates, sonic_distance, sonic_distance_breakdown

router = APIRouter(prefix="/studio", tags=["product-studio"])
MAX_COMPARE_SONGS = 250


def _normalized_name(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", ascii_value.casefold()).strip()


def _spotify_track_matches(song: Song, item: dict) -> bool:
    wanted_title, got_title = _normalized_name(song.title), _normalized_name(item.get("name", ""))
    wanted_artists = set(_normalized_name(song.artist).split())
    got_artists = set(_normalized_name(" ".join(a.get("name", "") for a in item.get("artists", []))).split())
    title_ok = wanted_title == got_title or wanted_title in got_title or got_title in wanted_title
    return bool(title_ok and wanted_artists.intersection(got_artists))


async def _search_spotify_song(client: httpx.AsyncClient, headers: dict[str, str], song: Song):
    for attempt in range(2):
        result = await client.get(f"{spotify_auth.API_BASE}/search", headers=headers, params={"q": f'track:"{song.title}" artist:"{song.artist}"', "type": "track", "limit": 3})
        if result.status_code == 429 and attempt == 0:
            await asyncio.sleep(min(float(result.headers.get("Retry-After", "1")), 2.0))
            continue
        if not result.is_success:
            return None
        return next((item.get("uri") for item in result.json().get("tracks", {}).get("items", []) if _spotify_track_matches(song, item)), None)
    return None


def _user_songs(db: Session, user_id: uuid.UUID):
    return db.execute(select(UserSong, Song).join(Song).where(UserSong.user_id == user_id, Song.feature_vector.is_not(None))).tuples().all()


def _bounded_rows(rows, maximum=MAX_COMPARE_SONGS):
    """Deterministic coverage of the whole history, not a biased newest/oldest slice."""
    if len(rows) <= maximum:
        return rows
    indices = np.linspace(0, len(rows) - 1, maximum, dtype=int)
    return [rows[i] for i in indices]


def _nearest_pairs(left, right):
    pairs = []
    for _, a in left:
        best = min(
            ((sonic_distance(np.asarray(a.feature_vector), np.asarray(a.genre_vector) if a.genre_vector is not None else None, np.asarray(b.feature_vector), np.asarray(b.genre_vector) if b.genre_vector is not None else None), b) for _, b in right),
            key=lambda row: row[0],
        )
        pairs.append((best[0], a, best[1]))
    return pairs


@router.post("/share")
def create_share(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    share = db.execute(select(TasteShare).where(TasteShare.user_id == user.id)).scalar_one_or_none()
    if share is None:
        share = TasteShare(user_id=user.id, token=secrets.token_urlsafe(24))
        db.add(share)
    share.active = True
    db.commit()
    return {"token": share.token}


@router.post("/compare")
def compare_brains(payload: ShareCompareIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    share = db.execute(select(TasteShare).where(TasteShare.token == payload.token, TasteShare.active.is_(True))).scalar_one_or_none()
    if share is None:
        raise HTTPException(404, "That taste link is unavailable")
    mine, theirs = _bounded_rows(_user_songs(db, user.id)), _bounded_rows(_user_songs(db, share.user_id))
    if not mine or not theirs:
        raise HTTPException(409, "Both brains need analyzed songs before they can merge")
    # Bidirectional nearest-neighbor scoring makes merge(A,B) identical to merge(B,A).
    pairs = _nearest_pairs(mine, theirs) + [(d, b, a) for d, a, b in _nearest_pairs(theirs, mine)]
    totals = {k: 0.0 for k in ("genre", "timbre", "rhythm", "tonal", "energy")}
    for distance, a, best in pairs:
        parts = sonic_distance_breakdown(a.feature_vector, a.genre_vector, best.feature_vector, best.genre_vector)
        for key in totals: totals[key] += parts.get(key, 0.0)
    pairs.sort(key=lambda row: row[0])
    avg = sum(row[0] for row in pairs) / len(pairs)
    return {"compatibility": round(100 * np.exp(-1.15 * avg)), "dimensions": {k: round(100 * np.exp(-totals[k] / len(pairs) / max(getattr(DEFAULT_WEIGHTS, k), .01))) for k in totals}, "bridges": [{"left": SongOut.model_validate(a), "right": SongOut.model_validate(b), "distance": d} for d, a, b in pairs[:5]]}


def _prompt_constraints(prompt: str):
    text = prompt.lower()
    genres = [g for g in ("pop", "rock", "electronic", "hip hop", "jazz", "classical", "folk", "latin", "reggae", "blues", "funk", "soul", "metal") if re.search(rf"\b{re.escape(g)}\b", text)]
    energy = "high" if any(w in text for w in ("energetic", "workout", "intense", "hype")) else "low" if any(w in text for w in ("calm", "chill", "sleep", "soft")) else None
    bpm = re.search(r"(?:under|below|slower than)\s+(\d{2,3})(?:\s*bpm)?\b", text)
    parsed_bpm = int(bpm.group(1)) if bpm else None
    if parsed_bpm is not None and not 40 <= parsed_bpm <= 240:
        parsed_bpm = None
    return genres, energy, parsed_bpm


@router.post("/discover")
async def prompted_discovery(payload: PromptDiscoveryIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    history = _user_songs(db, user.id)
    if not history:
        return {"interpretation": "Add analyzed songs to your brain first.", "songs": []}
    owned = select(UserSong.song_id).where(UserSong.user_id == user.id)
    candidates = db.execute(select(Song).where(Song.feature_vector.is_not(None), Song.id.not_in(owned)).limit(5000)).scalars().all()
    genres, energy, max_bpm = _prompt_constraints(payload.prompt)
    if genres: candidates = [s for s in candidates if s.genre and any(g in s.genre.lower() for g in genres)]
    if energy == "high": candidates = [s for s in candidates if s.energy is not None and s.energy >= .62]
    if energy == "low": candidates = [s for s in candidates if s.energy is not None and s.energy <= .48]
    if max_bpm: candidates = [s for s in candidates if s.bpm is not None and s.bpm <= max_bpm]
    learned = db.get(UserTasteWeights, user.id)
    weights = DEFAULT_WEIGHTS if learned is None else SonicDistanceWeights(learned.genre, learned.timbre, learned.rhythm, learned.tonal, learned.energy)
    ranked = await score_candidates(history, candidates, limit=payload.limit, sonic_weights=weights)
    return {"interpretation": f"Taste-matched with filters: {', '.join(genres) or 'any genre'}{', ' + energy + ' energy' if energy else ''}{', under ' + str(max_bpm) + ' BPM' if max_bpm else ''}.", "songs": [SongOut.model_validate(row[0]) for row in ranked]}


@router.post("/journey")
def build_journey(payload: JourneyIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    unique_ids = list(dict.fromkeys(payload.song_ids))
    found = db.execute(select(Song).where(Song.id.in_(unique_ids), Song.feature_vector.is_not(None))).scalars().all()
    by_id = {song.id: song for song in found}
    songs = [by_id[song_id] for song_id in unique_ids if song_id in by_id]
    if not songs: return {"name": payload.name, "songs": []}
    remaining, ordered = songs[1:], [songs[0]]
    while remaining:
        current = ordered[-1]
        nxt = min(remaining, key=lambda s: sonic_distance(np.asarray(current.feature_vector), np.asarray(current.genre_vector) if current.genre_vector is not None else None, np.asarray(s.feature_vector), np.asarray(s.genre_vector) if s.genre_vector is not None else None) + .12 * abs((current.energy or .5) - (s.energy or .5)))
        ordered.append(nxt); remaining.remove(nxt)
    return {"name": payload.name, "songs": [SongOut.model_validate(s) for s in ordered]}


@router.post("/journey/export")
async def export_journey(payload: JourneyIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    unique_ids = list(dict.fromkeys(payload.song_ids))
    found = db.execute(select(Song).where(Song.id.in_(unique_ids))).scalars().all()
    by_id = {song.id: song for song in found}
    songs = [by_id[song_id] for song_id in unique_ids if song_id in by_id]
    if not songs:
        raise HTTPException(422, "No valid songs were supplied for export")
    token = await get_valid_spotify_token(user, db)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=20) as client:
        resolved = []
        for start in range(0, len(songs), 8):
            resolved.extend(await asyncio.gather(*[_search_spotify_song(client, headers, song) for song in songs[start:start + 8]]))
        uris = [uri for uri in resolved if uri]
        missing = [f"{song.artist} — {song.title}" for song, uri in zip(songs, resolved) if not uri]
        if not uris:
            raise HTTPException(422, "Spotify could not confidently match any songs; no empty playlist was created")

        # Spotify's February 2026 migration removed POST /users/{user_id}/playlists.
        # Playlist creation is now always scoped to the token owner via /me/playlists.
        created = await client.post(
            f"{spotify_auth.API_BASE}/me/playlists",
            headers=headers,
            json={"name": payload.name, "public": False, "description": "A Sonicmap taste journey"},
        )
        if not created.is_success:
            try:
                spotify_message = created.json().get("error", {}).get("message")
            except ValueError:
                spotify_message = None
            if created.status_code in {401, 403}:
                detail = spotify_message or "Spotify did not authorize playlist creation"
                raise HTTPException(created.status_code, f"Spotify export was denied: {detail}")
            created.raise_for_status()
        playlist = created.json()
        if uris:
            added = await client.post(f"{spotify_auth.API_BASE}/playlists/{playlist['id']}/items", headers=headers, json={"uris": uris}); added.raise_for_status()
    return {"url": playlist["external_urls"]["spotify"], "exported": len(uris), "missing": missing}
