"""Synthetic taste profiles built from real acoustic-feature proximity (timbre/rhythm/tonal/
energy — explicitly excluding genre) rather than any external metadata graph.

Why this exists: app/services/synthetic_taste.py's Last.fm-artist-graph profiles turned out to
carry a real bias, found by actually running app/scripts/learn_weights.py against them — an
artist-similarity-derived ground truth is structurally correlated with genre (songs by similar
artists tend to share catalog genre tags), so a weight search against it kept converging on
"genre≈1.0, ignore everything else," which is recovering that correlation, not evidence genre
alone actually predicts real taste. Swapping the *source* of the metadata graph (e.g. asking an
LLM to build profiles instead) doesn't fix this: an LLM has no audio access either, so
genre/artist text is still its most legible signal, and it would plausibly reconverge on the
identical bias through a different door.

This is the actual fix: ground the synthetic profile in feature_vector data itself — the same
real Essentia-extracted numbers SonicDistance's non-genre components are built from — with
genre's weight forced to zero while building each profile, so genre gets *no* credit for
membership by construction. A weight search evaluated against these profiles can only earn
credit for timbre/rhythm/tonal/energy actually predicting which songs are acoustically close;
it has no metadata shortcut available here. Meant as a complement to the Last.fm-graph
profiles, not a replacement — real listener taste is presumably shaped by both community/
artist context *and* raw sonic similarity, and a weight search should be honest about being
tested against both kinds of ground truth, not just one.
"""

import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Song
from app.services.brain import SonicDistanceWeights, _pairwise_distance_matrix, _song_vectors

MIN_PROFILE_SONGS = 6  # same floor as synthetic_taste.py — see evaluate_song_rows
MAX_PROFILE_SONGS = 30
NEIGHBORS_PER_SEED = MAX_PROFILE_SONGS - 1

# Genre forced to zero; the remaining four components keep their *relative* weight from the
# production defaults (W_TIMBRE=0.25, W_RHYTHM=0.15, W_TONAL=0.10, W_ENERGY=0.05, summing to
# 0.55) rather than an arbitrary equal split, so this still reflects "how much each component
# matters relative to the others today," just without genre's vote at all.
_NON_GENRE_WEIGHTS = SonicDistanceWeights(
    genre=0.0, timbre=0.25 / 0.55, rhythm=0.15 / 0.55, tonal=0.10 / 0.55, energy=0.05 / 0.55
)


def _all_songs(db: Session) -> list[Song]:
    return list(db.execute(select(Song).where(Song.feature_vector.is_not(None))).scalars().all())


def generate_acoustic_profiles(
    db: Session, count: int, seed: int | None = None
) -> list[tuple[str, list[Song]]]:
    """Returns up to `count` (label, songs) profiles. Each profile: pick a real seed song and
    take its NEIGHBORS_PER_SEED nearest neighbors by non-genre SonicDistance across the whole
    catalog. Deterministic given `seed`, same fairness rationale as synthetic_taste.py's
    artist-graph profiles (a fixed seed reproduces the same profiles run to run, so re-running
    the search later is a fair comparison). A song already used as a seed (or pulled in as a
    neighbor) isn't picked as a *fresh* seed again, so profiles don't trivially duplicate each
    other around the same cluster."""
    songs = _all_songs(db)
    if len(songs) < MIN_PROFILE_SONGS:
        return []

    rng = random.Random(seed)
    vectors = [_song_vectors(s) for s in songs]
    distance_matrix = _pairwise_distance_matrix(vectors, _NON_GENRE_WEIGHTS)

    seed_order = list(range(len(songs)))
    rng.shuffle(seed_order)

    profiles: list[tuple[str, list[Song]]] = []
    used: set[int] = set()
    for idx in seed_order:
        if len(profiles) >= count:
            break
        if idx in used:
            continue
        distances = distance_matrix[idx]
        neighbor_order = sorted((j for j in range(len(songs)) if j != idx), key=lambda j: distances[j])
        neighbors = neighbor_order[:NEIGHBORS_PER_SEED]
        if len(neighbors) + 1 < MIN_PROFILE_SONGS:
            continue
        profile_indices = [idx, *neighbors]
        used.update(profile_indices)
        seed_song = songs[idx]
        profiles.append((f"acoustic:{seed_song.artist} - {seed_song.title}", [songs[i] for i in profile_indices]))

    return profiles
