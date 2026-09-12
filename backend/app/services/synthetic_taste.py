"""Synthetic taste profiles for holdout evaluation, built from Last.fm's real artist-similarity
graph — a signal sourced from actual listener scrobbling behavior, completely independent of
sonicmap's own genre tags and audio embeddings (Discogs-EffNet genre_vector, MFCC-derived
timbre — see app/services/brain.py). That independence is what makes this a legitimate
technique rather than a circular one: if a SonicDistance weight vector, using only audio
content, recovers relationships Last.fm's community independently agrees on, that's real
evidence the weighting is picking up something true — the same "weak supervision from tags"
methodology music-information-retrieval research uses to validate audio-feature models, not
something engineered to pass its own test.

This exists because sonicmap's real holdout evaluation (app/services/evaluation.py) currently
has only ~2 real users to draw on — nowhere near enough judgments for
app/scripts/learn_weights.py's weight search to say anything trustworthy (see its own honesty
check). Synthetic profiles are NOT fake accounts: nothing here is ever written to the
users/user_songs tables. Each profile is a plain list[Song] built and scored in memory for one
evaluation run and then discarded — the same technique evaluation.py's _FakeUserSong already
uses to simulate cluster structure without touching real data.

Each profile: pick a real catalog artist as a seed, pull Last.fm's similar artists for it, and
collect every catalog song by the seed or any of those similar artists. That set stands in for
"an imaginary listener into this corner of music" — not a real person's taste, but a real,
externally-validated cluster of related songs, which is exactly what holdout evaluation needs
to test whether SonicDistance ranks a held-out member of that cluster back near the rest.
"""

import random

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.models import Song
from app.services import lastfm

MIN_PROFILE_SONGS = 6  # need >=1 visible song per hold-out slot, same floor as evaluate_song_rows
MAX_PROFILE_SONGS = 30  # keeps a profile roughly map-sized rather than "half the catalog"
MAX_ARTIST_OVERLAP = 0.6  # skip a profile that's mostly the same artists as one already kept


def _catalog_artists(db: Session) -> list[str]:
    return list(
        db.execute(
            select(distinct(Song.artist)).where(Song.feature_vector.is_not(None))
        ).scalars().all()
    )


def _songs_by_artists(db: Session, artists: set[str]) -> list[Song]:
    if not artists:
        return []
    return list(
        db.execute(
            select(Song).where(Song.feature_vector.is_not(None), func.lower(Song.artist).in_({a.lower() for a in artists}))
        ).scalars().all()
    )


async def _build_one_profile(db: Session, seed_artist: str, rng: random.Random) -> tuple[set[str], list[Song]] | None:
    similar = await lastfm.get_similar_artists(seed_artist, limit=20)
    artist_pool = {seed_artist} | set(similar.keys())
    songs = _songs_by_artists(db, artist_pool)
    if len(songs) < MIN_PROFILE_SONGS:
        return None
    if len(songs) > MAX_PROFILE_SONGS:
        songs = rng.sample(songs, MAX_PROFILE_SONGS)
    actual_artists = {s.artist.lower() for s in songs}
    return actual_artists, songs


async def generate_synthetic_profiles(
    db: Session, count: int, seed: int | None = None, max_seed_attempts: int | None = None
) -> list[tuple[str, list[Song]]]:
    """Returns up to `count` (label, songs) profiles, each built from a different seed artist.
    Tries seed artists in a fixed random order (so a fixed `seed` reproduces the same profiles
    run to run — same fairness rationale as evaluation.py's holdout seed) until `count` valid,
    sufficiently-distinct profiles are found or seeds run out. A profile is skipped, not
    padded — a run against fewer than `count` real profiles is still valid, just smaller."""
    rng = random.Random(seed)
    artists = _catalog_artists(db)
    rng.shuffle(artists)
    if max_seed_attempts is None:
        max_seed_attempts = len(artists)

    profiles: list[tuple[str, list[Song]]] = []
    accepted_artist_sets: list[set[str]] = []

    for seed_artist in artists[:max_seed_attempts]:
        if len(profiles) >= count:
            break
        built = await _build_one_profile(db, seed_artist, rng)
        if built is None:
            continue
        artist_set, songs = built
        too_similar = any(
            len(artist_set & existing) / len(artist_set | existing) > MAX_ARTIST_OVERLAP
            for existing in accepted_artist_sets
        )
        if too_similar:
            continue
        accepted_artist_sets.append(artist_set)
        profiles.append((f"synthetic:{seed_artist}", songs))

    return profiles
