"""The persistent per-user 'brain': recomputes a user's 2D taste map over their *entire*
accumulated song history (app.models.UserSong), and finds catalog-wide recommendations near
that history. Nothing here is scoped to a single import/session — every call reads the full
UserSong history for the user, which is what makes old context never get forgotten.

Both the map (rebuild_user_map) and the recommender (recommend_for_user) are driven by one
shared, named similarity metric — SonicDistance (see sonic_distance() below) — instead of a
single flat cosine distance over one blended vector. A single vector treats "sounds similar"
as one number and has no way to express "same genre neighborhood" separately from "similar
production texture", which is exactly what let acoustically-textured-alike but
genre-unrelated tracks get recommended to each other. SonicDistance is a named, weighted
combination of five interpretable components — see the docstring on sonic_distance().
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session, defer

from app.config import settings
from app.db import SessionLocal
from app.models import HiddenSong, Song, User, UserClusterProfile, UserSong
from app.services import lastfm
from app.services.background import fire_and_forget
from app.services.language import is_english_recommendation_eligible

logger = logging.getLogger(__name__)

try:
    import hdbscan
    import umap
except ImportError:  # pragma: no cover - only available in the Linux backend container
    hdbscan = None
    umap = None

# SonicDistance component weights. Genre carries the most weight deliberately — it's the
# dimension the old single-vector cosine distance had zero representation of at all, and the
# one whose absence produced recommendations that felt wrong despite being "close" by pure
# timbral/production similarity (e.g. a mellow Radiohead track landing next to a mellow Drake
# or Shakira track). The rest still matter — two songs in the same genre with wildly
# different tempo or energy shouldn't be treated as identical either.
W_GENRE = 0.45
W_TIMBRE = 0.25
W_RHYTHM = 0.15
W_TONAL = 0.10
W_ENERGY = 0.05


@dataclass(frozen=True)
class SonicDistanceWeights:
    """Everything below (sonic_distance, _pairwise_distance_matrix, score_candidates) takes
    weights as data instead of reading W_GENRE etc. directly, specifically so
    app/scripts/learn_weights.py can hold-out-evaluate many candidate weight vectors against
    the *exact* production ranking code, rather than a parallel reimplementation that could
    drift out of sync. Everyday production code never needs to construct one of these — it
    just uses DEFAULT_WEIGHTS."""

    genre: float = W_GENRE
    timbre: float = W_TIMBRE
    rhythm: float = W_RHYTHM
    tonal: float = W_TONAL
    energy: float = W_ENERGY


_WEIGHTS_FILE = Path(__file__).resolve().parent.parent / "sonic_distance_weights.json"


def _load_default_weights() -> SonicDistanceWeights:
    """sonic_distance_weights.json is an optional, git-tracked, human-reviewed artifact —
    app/scripts/learn_weights.py can write a candidate there (only with --apply; by default it
    just reports its findings), but nothing here ever adopts a new weight vector silently.
    Falls back to the original hand-picked constants above whenever the file is absent,
    unreadable, or missing a field — a bad or partial file must never leave production without
    a valid set of weights."""
    if _WEIGHTS_FILE.exists():
        try:
            data = json.loads(_WEIGHTS_FILE.read_text())
            return SonicDistanceWeights(**data)
        except Exception:
            logger.exception("Failed to load %s, falling back to built-in defaults", _WEIGHTS_FILE)
    return SonicDistanceWeights()


DEFAULT_WEIGHTS = _load_default_weights()

# feature_vector slice layout — see app/services/extraction.py for how each slot is computed.
_BPM_DANCE_IDX = slice(0, 2)
_ENERGY_IDX = 2
_MFCC_IDX = slice(3, 29)  # mean (13) + std (13) — timbral/production texture
_TONAL_IDX = slice(29, 32)  # key sin, key cos, mode


def _cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 1.0
    return float(1.0 - np.dot(a, b) / denom)


def sonic_distance(
    feature_vector_a: np.ndarray,
    genre_vector_a: np.ndarray | None,
    feature_vector_b: np.ndarray,
    genre_vector_b: np.ndarray | None,
    weights: SonicDistanceWeights = DEFAULT_WEIGHTS,
) -> float:
    """SonicDistance: a named, weighted combination of five components, each measuring a
    materially different kind of similarity between two songs' extracted audio features —
    not a single blind cosine distance over one concatenated vector.

      genre    — cosine distance between Discogs-EffNet genre/style embeddings (weights.genre)
      timbre   — cosine distance between MFCC mean+std, i.e. production/instrumentation
                 texture (weights.timbre)
      rhythm   — Euclidean distance between (normalized bpm, danceability) (weights.rhythm)
      tonal    — Euclidean distance between (key sin, key cos, mode) (weights.tonal)
      energy   — absolute difference in normalized loudness/energy (weights.energy)

    Weights sum to 1, so the result is roughly in [0, 1] (cosine and Euclidean components
    aren't perfectly bounded the same way, but stay close in practice given how the source
    features are normalized). If either song has no genre_vector yet (not every song in the
    catalog has been through the genre-embedding pipeline), the genre term is dropped and the
    remaining weights are renormalized proportionally, rather than silently treating "unknown
    genre" as "zero genre distance". `weights` defaults to DEFAULT_WEIGHTS (production) — only
    app/services/evaluation.py's holdout harness ever passes something else, to score a
    candidate weight vector from app/scripts/learn_weights.py.
    """
    timbre = _cosine_distance(feature_vector_a[_MFCC_IDX], feature_vector_b[_MFCC_IDX])
    rhythm = float(np.linalg.norm(feature_vector_a[_BPM_DANCE_IDX] - feature_vector_b[_BPM_DANCE_IDX]))
    tonal = float(np.linalg.norm(feature_vector_a[_TONAL_IDX] - feature_vector_b[_TONAL_IDX]))
    energy = float(abs(feature_vector_a[_ENERGY_IDX] - feature_vector_b[_ENERGY_IDX]))

    if genre_vector_a is not None and genre_vector_b is not None:
        genre = _cosine_distance(genre_vector_a, genre_vector_b)
        distance = (
            weights.genre * genre
            + weights.timbre * timbre
            + weights.rhythm * rhythm
            + weights.tonal * tonal
            + weights.energy * energy
        )
    else:
        remaining = weights.timbre + weights.rhythm + weights.tonal + weights.energy
        distance = (
            weights.timbre * timbre + weights.rhythm * rhythm + weights.tonal * tonal + weights.energy * energy
        ) / remaining

    # Every component above is provably non-negative (cosine distance of two unit-normalized
    # vectors, a Euclidean norm, an absolute difference), so the true value can never be below
    # zero — but two songs with near-identical (or, as here, literally duplicate — the same
    # canonical song linked twice via different ingest sources) feature vectors can push the
    # cosine terms a hair past 1.0 in floating point, landing distance at e.g. -4.4e-17 instead
    # of 0.0. UMAP's metric="precomputed" path validates strict non-negativity and rejects the
    # whole matrix over noise this small, so clamp rather than let one duplicate pair 500 an
    # entire rebuild.
    return max(0.0, distance)


def sonic_distance_breakdown(feature_vector_a, genre_vector_a, feature_vector_b, genre_vector_b,
                             weights: SonicDistanceWeights = DEFAULT_WEIGHTS) -> dict[str, float]:
    """Return each normalized component's actual contribution to SonicDistance."""
    a, b = np.asarray(feature_vector_a), np.asarray(feature_vector_b)
    raw = {
        "timbre": _cosine_distance(a[_MFCC_IDX], b[_MFCC_IDX]),
        "rhythm": float(np.linalg.norm(a[_BPM_DANCE_IDX] - b[_BPM_DANCE_IDX])),
        "tonal": float(np.linalg.norm(a[_TONAL_IDX] - b[_TONAL_IDX])),
        "energy": float(abs(a[_ENERGY_IDX] - b[_ENERGY_IDX])),
    }
    active = {key: getattr(weights, key) for key in raw}
    if genre_vector_a is not None and genre_vector_b is not None:
        raw["genre"] = _cosine_distance(np.asarray(genre_vector_a), np.asarray(genre_vector_b))
        active["genre"] = weights.genre
    total = sum(active.values()) or 1.0
    return {key: max(0.0, float(value * active[key] / total)) for key, value in raw.items()}


def _cosine_distance_matrix(X: np.ndarray) -> np.ndarray:
    """All-pairs cosine distance via one normalize + matmul instead of n² individual dot
    products — the same floating-point operations sonic_distance's _cosine_distance does per
    pair, just batched through BLAS. A zero-norm row normalizes to the zero vector, which has
    zero similarity (distance 1.0) against everything including itself — the same fallback
    _cosine_distance returns explicitly when denom == 0."""
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    Xn = X / norms
    return 1.0 - Xn @ Xn.T


def _pairwise_distance_matrix(
    rows: list[tuple[np.ndarray, np.ndarray | None]], weights: SonicDistanceWeights = DEFAULT_WEIGHTS
) -> np.ndarray:
    """Vectorized equivalent of calling sonic_distance() for every pair — mathematically the
    same weighted combination, computed as a handful of BLAS matrix operations instead of n²
    individual Python-level calls. For a genre-embedding dimension of 1280, that difference is
    not cosmetic: 500 songs is 124,750 pairs, each previously doing its own 1280-dim dot
    product in a Python loop; here it's one (n × 1280) @ (1280 × n) matmul. Verified to match
    sonic_distance()'s per-pair output on real data (see the assertion in rebuild_user_map).
    `weights` defaults to production (DEFAULT_WEIGHTS) — evaluation.py's holdout harness is the
    only caller that ever passes a candidate vector, to rebuild simulated cluster structure
    under weights other than production's."""
    n = len(rows)
    feature_vectors = np.stack([fv for fv, _ in rows])

    timbre = _cosine_distance_matrix(feature_vectors[:, _MFCC_IDX])

    rhythm_vals = feature_vectors[:, _BPM_DANCE_IDX]
    rhythm = np.linalg.norm(rhythm_vals[:, None, :] - rhythm_vals[None, :, :], axis=-1)

    tonal_vals = feature_vectors[:, _TONAL_IDX]
    tonal = np.linalg.norm(tonal_vals[:, None, :] - tonal_vals[None, :, :], axis=-1)

    energy_vals = feature_vectors[:, _ENERGY_IDX]
    energy = np.abs(energy_vals[:, None] - energy_vals[None, :])

    has_genre = np.array([gv is not None for _, gv in rows])
    both_have_genre = has_genre[:, None] & has_genre[None, :]
    genre_vectors = np.zeros((n, settings.genre_vector_dim))
    for i, (_, gv) in enumerate(rows):
        if gv is not None:
            genre_vectors[i] = gv
    genre = _cosine_distance_matrix(genre_vectors)

    with_genre = (
        weights.genre * genre
        + weights.timbre * timbre
        + weights.rhythm * rhythm
        + weights.tonal * tonal
        + weights.energy * energy
    )
    remaining = weights.timbre + weights.rhythm + weights.tonal + weights.energy
    without_genre = (
        weights.timbre * timbre + weights.rhythm * rhythm + weights.tonal * tonal + weights.energy * energy
    ) / remaining

    matrix = np.where(both_have_genre, with_genre, without_genre)
    np.fill_diagonal(matrix, 0.0)
    # Same floating-point-noise rationale as sonic_distance()'s own clamp.
    return np.maximum(matrix, 0.0)


def _cross_distance_matrix(
    left: list[tuple[np.ndarray, np.ndarray | None]],
    right: list[tuple[np.ndarray, np.ndarray | None]],
    weights: SonicDistanceWeights = DEFAULT_WEIGHTS,
) -> np.ndarray:
    """Vectorized exact SonicDistance for every left/right pair (shape L x R).

    Unlike _pairwise_distance_matrix this avoids computing candidate-to-candidate distances,
    which would be quadratic in the ANN shortlist and are never used by recommendation
    ranking. The component formula and missing-genre renormalization are identical to
    sonic_distance().
    """
    lf = np.stack([fv for fv, _ in left])
    rf = np.stack([fv for fv, _ in right])

    def cosine_cross(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        an = np.linalg.norm(a, axis=1, keepdims=True)
        bn = np.linalg.norm(b, axis=1, keepdims=True)
        an[an == 0] = 1.0
        bn[bn == 0] = 1.0
        return 1.0 - (a / an) @ (b / bn).T

    timbre = cosine_cross(lf[:, _MFCC_IDX], rf[:, _MFCC_IDX])
    rhythm = np.linalg.norm(lf[:, None, _BPM_DANCE_IDX] - rf[None, :, _BPM_DANCE_IDX], axis=-1)
    tonal = np.linalg.norm(lf[:, None, _TONAL_IDX] - rf[None, :, _TONAL_IDX], axis=-1)
    energy = np.abs(lf[:, None, _ENERGY_IDX] - rf[None, :, _ENERGY_IDX])

    left_has = np.array([gv is not None for _, gv in left])
    right_has = np.array([gv is not None for _, gv in right])
    lg = np.zeros((len(left), settings.genre_vector_dim))
    rg = np.zeros((len(right), settings.genre_vector_dim))
    for i, (_, vector) in enumerate(left):
        if vector is not None:
            lg[i] = vector
    for i, (_, vector) in enumerate(right):
        if vector is not None:
            rg[i] = vector
    genre = cosine_cross(lg, rg)
    with_genre = (
        weights.genre * genre + weights.timbre * timbre + weights.rhythm * rhythm
        + weights.tonal * tonal + weights.energy * energy
    )
    remaining = weights.timbre + weights.rhythm + weights.tonal + weights.energy
    without_genre = (
        weights.timbre * timbre + weights.rhythm * rhythm + weights.tonal * tonal
        + weights.energy * energy
    ) / remaining
    return np.maximum(np.where(left_has[:, None] & right_has[None, :], with_genre, without_genre), 0.0)


def _song_vectors(song: Song) -> tuple[np.ndarray, np.ndarray | None]:
    return (
        np.nan_to_num(np.array(song.feature_vector, dtype=float), nan=0.0, posinf=1.0, neginf=-1.0),
        np.nan_to_num(np.array(song.genre_vector, dtype=float), nan=0.0, posinf=1.0, neginf=-1.0) if song.genre_vector is not None else None,
    )


def _compute_coords_and_labels(distance_matrix: np.ndarray, n: int) -> tuple[np.ndarray, list[int]]:
    """The clustering half of rebuild_user_map, factored out so app/services/evaluation.py can
    recompute cluster structure over a *simulated* subset of a user's songs (for offline
    holdout testing) without touching the database — evaluating against a real rebuild_user_map
    run would leak the held-out songs into the very clusters being tested against them."""
    if n < 5:
        # UMAP needs enough points to build a non-degenerate neighbor graph - below that it
        # throws (empty graph -> "zero-size array to reduction operation maximum"). Fall back
        # to classical MDS (the precomputed-distance-matrix equivalent of PCA) until there's
        # enough data for a real embedding — still driven by the same SonicDistance matrix,
        # not raw feature vectors, so it's genre-aware too.
        from sklearn.manifold import MDS

        coords = MDS(n_components=2, random_state=42, dissimilarity="precomputed", normalized_stress="auto").fit_transform(
            distance_matrix
        )
        return coords, [-1] * n

    n_neighbors = max(2, min(15, n - 1))
    reducer = umap.UMAP(n_neighbors=n_neighbors, n_components=2, metric="precomputed", random_state=42)
    coords = reducer.fit_transform(distance_matrix)

    # Cluster from a higher-dimensional taste embedding, not the lossy two-dimensional UMAP
    # projection used for drawing. Direct density clustering of SonicDistance was evaluated
    # too, but the real catalog's high-dimensional distance concentration made every song
    # noise. Eight dimensions preserve useful neighbourhood structure without making visual
    # overlap decide membership.
    #
    # n//10 previously made a 178-song library require 17 members before a taste community
    # was allowed to exist, collapsing the whole map into two broad mixed buckets. A bounded
    # sqrt rule grows gently with the library while still allowing meaningful niches. `leaf`
    # selection retains stable sub-communities instead of merging them into their broadest
    # parent cluster. HDBSCAN is still free to mark ambiguous songs as noise.
    cluster_dimensions = min(8, n - 2)
    cluster_space = umap.UMAP(
        n_neighbors=n_neighbors,
        n_components=cluster_dimensions,
        metric="precomputed",
        random_state=42,
    ).fit_transform(distance_matrix)
    min_cluster_size = max(4, min(10, round(np.sqrt(n) / 1.7)))
    min_samples = max(2, min_cluster_size // 3)
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        cluster_selection_method="leaf",
    )
    labels = clusterer.fit_predict(cluster_space)
    return coords, [int(label) for label in labels]


def warm_up_clustering() -> None:
    """UMAP/HDBSCAN's core routines are Numba @njit-compiled lazily on first real
    invocation — a one-time cost, empirically measured at ~17s, that's completely
    independent of data size: every call after the first, in the same process, takes tens
    of milliseconds regardless of how many songs are involved. Called once at server
    startup (see app/main.py's lifespan) so that cost lands during deploy/restart, never on
    whichever real user happens to trigger the first rebuild afterward — which is what was
    actually behind a chunk of "rebuild feels stuck," especially right after any of the
    restarts a normal deploy cycle involves."""
    if umap is None or hdbscan is None:
        return
    rng = np.random.default_rng(0)
    n = 6
    matrix = rng.random((n, n))
    matrix = (matrix + matrix.T) / 2
    np.fill_diagonal(matrix, 0.0)
    _compute_coords_and_labels(matrix, n)


def rebuild_user_map(db: Session, user_id: uuid.UUID) -> int:
    """Recomputes 2D coordinates + cluster labels for every song the user has ever submitted.
    Returns the number of songs placed. Synchronous and CPU-bound (the distance matrix is
    vectorized numpy/BLAS, but UMAP/HDBSCAN are still real compute) — see
    rebuild_user_map_background below for how this actually gets called: off the event loop,
    off the request path, with a pollable status instead of a blocking response."""

    rows: list[tuple[UserSong, Song]] = (
        db.execute(
            select(UserSong, Song).options(defer(Song.retrieval_vector))
            .join(Song, UserSong.song_id == Song.id)
            .where(UserSong.user_id == user_id, Song.feature_vector.is_not(None))
        )
        .tuples()
        .all()
    )

    if len(rows) < 2:
        db.execute(delete(UserClusterProfile).where(UserClusterProfile.user_id == user_id))
        for user_song, _ in rows:
            user_song.map_x, user_song.map_y, user_song.cluster_label = 0.0, 0.0, -1
        db.commit()
        return len(rows)

    song_vectors = [_song_vectors(song) for _, song in rows]
    distance_matrix = _pairwise_distance_matrix(song_vectors)
    coords, labels = _compute_coords_and_labels(distance_matrix, len(rows))

    for (user_song, _), (x, y), label in zip(rows, coords, labels):
        user_song.map_x, user_song.map_y = float(x), float(y)
        user_song.cluster_label = label

    # Persist one actual-song medoid per real cluster for ANN seed queries. Unlike a centroid,
    # this cannot manufacture a blended genre that no song in the cluster represents.
    db.execute(delete(UserClusterProfile).where(UserClusterProfile.user_id == user_id))
    for label in sorted(set(labels)):
        if label < 0:
            continue
        indices = [i for i, value in enumerate(labels) if value == label]
        medoid_idx = min(indices, key=lambda i: float(distance_matrix[i, indices].mean()))
        db.add(
            UserClusterProfile(
                user_id=user_id,
                cluster_label=label,
                medoid_song_id=rows[medoid_idx][1].id,
                updated_at=datetime.now(timezone.utc),
            )
        )

    db.commit()
    return len(rows)


def _rebuild_user_map_sync(user_id: uuid.UUID) -> None:
    """Owns its own session end-to-end (open, use, commit/rollback, close) so it's safe to run
    on a worker thread via asyncio.to_thread — SQLAlchemy Sessions aren't safe to share across
    threads, so this never touches a session created anywhere else."""
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user is None:
            return
        try:
            rebuild_user_map(db, user_id)
            user.map_rebuild_status = "idle"
        except Exception:
            logger.exception("Map rebuild failed for user %s", user_id)
            db.rollback()
            user.map_rebuild_status = "failed"
        db.commit()
    finally:
        db.close()


def trigger_rebuild(db: Session, user: User) -> bool:
    """Idempotent: a no-op if a rebuild is already in flight for this user, so an explicit
    "Rebuild map" click racing an auto-trigger (or two ingests finishing close together, see
    app/routers/ingest.py) can't queue redundant overlapping work. Callers should only
    schedule rebuild_user_map_background when this returns True:

        if trigger_rebuild(db, user):
            fire_and_forget(rebuild_user_map_background(user.id))
    """
    if user.map_rebuild_status == "processing":
        return False
    user.map_rebuild_status = "processing"
    db.commit()
    return True


async def rebuild_user_map_background(user_id: uuid.UUID) -> None:
    """The real entry point for triggering a rebuild — everywhere in the app that used to call
    rebuild_user_map directly (or block a request on it) should call this instead.

    Two things matter here, both learned from the same class of bug: UMAP/HDBSCAN and the
    distance-matrix numpy work are CPU-bound, so running them directly in an async function
    would block the single-threaded event loop for every other concurrent request/background
    task on the whole server for the duration of one user's rebuild — asyncio.to_thread offloads
    that to a worker thread instead. And this must be scheduled via fire_and_forget
    (app/services/background.py), never FastAPI's BackgroundTasks — that runs before a
    request's own DB session closes, which is exactly what caused connections to sit checked
    out of the pool for minutes at a time."""
    await asyncio.to_thread(_rebuild_user_map_sync, user_id)


def schedule_rebuild(db: Session, user: User) -> None:
    """trigger_rebuild + fire_and_forget, with a safety net most callers actually want: if
    *scheduling* the background job itself raises (as opposed to a failure during the rebuild,
    which rebuild_user_map_background's own try/except already handles — this caught a real
    bug during testing, a route declared `def` instead of `async def` breaking
    asyncio.create_task with no event loop in that thread), map_rebuild_status is reset
    instead of being left stuck at "processing" forever with nothing actually running. That
    stuck-forever state is exactly the "keeps on rebuilding" symptom this whole async path
    exists to prevent, so it can't be allowed to have its own silent failure mode."""
    if not trigger_rebuild(db, user):
        return
    try:
        fire_and_forget(rebuild_user_map_background(user.id))
    except Exception:
        logger.exception("Failed to schedule map rebuild for user %s", user.id)
        user.map_rebuild_status = "failed"
        db.commit()


def reset_stuck_rebuilds(db: Session) -> int:
    """Self-healing for the one scenario schedule_rebuild's safety net can't catch: a server
    restart or crash while a rebuild was genuinely in flight. Nothing is actually running
    anymore after a restart, but the DB would say "processing" forever without this — called
    once at startup (see app/main.py's lifespan), before any real request could ever observe
    a stale status left over from before the restart."""
    result = db.execute(update(User).where(User.map_rebuild_status == "processing").values(map_rebuild_status="idle"))
    db.commit()
    return result.rowcount


# Multi-channel blend weights: acoustic/SonicDistance stays dominant since it's sonicmap's real
# differentiator and the only channel guaranteed to be populated; the two Last.fm channels are
# a boost on top, not a replacement. Track-level gets more weight than artist-level since "this
# specific song resonates with fans of your specific songs" is a more precise signal than
# "similar artist" — both only apply when settings.lastfm_api_key is configured, otherwise
# ranking is identical to pure-SonicDistance, unchanged from before either channel existed.
ACOUSTIC_WEIGHT = 0.6
ARTIST_AFFINITY_WEIGHT = 0.15
TRACK_AFFINITY_WEIGHT = 0.25

# blend_mode="cascade" only: how many of the top-acoustic candidates are eligible for
# collaborative reranking at all. See score_candidates()'s docstring.
CASCADE_SHORTLIST_SIZE = 40

# No more than this many results from one artist, so one artist's chart-heavy catalog
# presence can't fill the whole recommendation list on its own. 1 == every recommended
# artist is unique.
MAX_PER_ARTIST = 1

# A song added this many days ago counts for half the ranking weight of one added today —
# see _recency_weight().
RECENCY_HALF_LIFE_DAYS = 90.0


def _recency_weight(added_at: datetime) -> float:
    """Exponential decay applied to *how much each cluster member's distance counts toward
    that cluster's average* when ranking recommendations — never to cluster membership or map
    placement. Every song a user has ever added stays on their map and in their brain forever
    (see the module docstring: "old context never get forgotten") — this only lets the
    recommender lean toward what they've been adding lately, the way real taste drifts,
    without needing a manual "clear everything and start over" to get there. Inspired by the
    engagement-decay pattern in Flow's FlowNeuroEngine (github.com/A-EDev/Flow), adapted to
    what sonicmap actually has: UserSong.added_at, not per-listen engagement signals."""
    now = datetime.now(timezone.utc)
    days = max(0.0, (now - added_at).total_seconds() / 86400)
    return 0.5 ** (days / RECENCY_HALF_LIFE_DAYS)


def _canonical_artist_names(song: Song) -> list[str]:
    """The real individual artist(s) behind this song's credit string, lowercased for
    case-insensitive comparison — see app/services/artist_identity.py for how
    artist_canonical gets populated (Gemini-resolved, " | "-joined) and why raw-string
    matching alone misses same-artist duo/group aliases. Falls back to the raw `artist`
    string as a single-element list wherever artist_canonical hasn't been backfilled yet."""
    if song.artist_canonical:
        names = [n.strip().lower() for n in song.artist_canonical.split(" | ") if n.strip()]
        if names:
            return names
    return [song.artist.strip().lower()]


def _apply_diversity_cap(
    scored: list[tuple], limit: int
) -> list[tuple]:
    """Greedily fills `limit` slots respecting MAX_PER_ARTIST, then backfills any remaining
    slots from the overflow (still best-first) rather than returning short — a soft cap, not a
    hard exclusion, so a user with a narrow catalog still gets a full list. Works on any
    row shape as long as the candidate Song is element 0 — callers vary in how many scoring
    fields they carry alongside it.

    Counts against *every* real artist behind a multi-credit song (see
    _canonical_artist_names), not just its raw credit string — a song wrongly slipping past
    the cap just because a duo happened to be credited under a different name/order than
    another selected song is exactly the gap this closes. A candidate is capped if *any* of
    its artists is already at the limit, so a collaboration can't be used to smuggle a
    third appearance of an already-capped artist through either."""
    selected: list[tuple] = []
    overflow: list[tuple] = []
    artist_counts: dict[str, int] = {}

    for row in scored:
        candidate = row[0]
        names = _canonical_artist_names(candidate)
        if any(artist_counts.get(name, 0) >= MAX_PER_ARTIST for name in names):
            overflow.append(row)
        else:
            selected.append(row)
            for name in names:
                artist_counts[name] = artist_counts.get(name, 0) + 1
        if len(selected) >= limit:
            return selected

    selected.extend(overflow[: limit - len(selected)])
    return selected


async def recommend_for_user(
    db: Session,
    user_id: uuid.UUID,
    limit: int = 10,
    exclude_song_ids: list[uuid.UUID] | None = None,
    english_only: bool = False,
) -> list[tuple[Song, float, Song, int]]:
    """Thin DB-querying wrapper around score_candidates(): fetches the user's full song
    history and the eligible catalog candidates, then ranks them against the user's *brain* —
    the taste-clusters rebuild_user_map already computed via HDBSCAN over the SonicDistance
    matrix (UserSong.cluster_label). `exclude_song_ids` additionally excludes songs the caller
    has already been shown this session; hidden_songs (HiddenSong, a *permanent* per-user
    dismiss list — see routers/recommend.py's hide/unhide endpoints) are always excluded,
    session or not. `english_only` restricts candidates to Song.language == "en" (see
    app/services/language.py) — a text-based proxy detected from each song's title, not a
    guarantee about the actual vocal language. See score_candidates() for the ranking algorithm
    itself."""

    user_song_rows: list[tuple[UserSong, Song]] = (
        db.execute(
            select(UserSong, Song)
            .join(Song, UserSong.song_id == Song.id)
            .where(UserSong.user_id == user_id, Song.feature_vector.is_not(None))
        )
        .tuples()
        .all()
    )
    if not user_song_rows:
        return []

    already_have_ids = set(db.execute(select(UserSong.song_id).where(UserSong.user_id == user_id)).scalars())
    hidden_ids = set(db.execute(select(HiddenSong.song_id).where(HiddenSong.user_id == user_id)).scalars())
    excluded_ids = already_have_ids | hidden_ids | set(exclude_song_ids or [])
    conditions = [Song.feature_vector.is_not(None)]
    if english_only:
        conditions.append(Song.language == "en")

    candidates: list[Song]
    if settings.ann_recommendations_enabled:
        seeds = db.execute(
            select(Song)
            .join(UserClusterProfile, UserClusterProfile.medoid_song_id == Song.id)
            .where(UserClusterProfile.user_id == user_id, Song.retrieval_vector.isnot(None))
        ).scalars().all()
        noise_seeds = db.execute(
            select(Song)
            .join(UserSong, UserSong.song_id == Song.id)
            .where(
                UserSong.user_id == user_id,
                (UserSong.cluster_label.is_(None)) | (UserSong.cluster_label == -1),
                Song.retrieval_vector.isnot(None),
            )
            .order_by(UserSong.added_at.desc())
            .limit(10)
        ).scalars().all()
        if not seeds:
            seeds = db.execute(
                select(Song)
                .join(UserSong, UserSong.song_id == Song.id)
                .where(UserSong.user_id == user_id, Song.retrieval_vector.isnot(None))
                .order_by(UserSong.added_at.desc())
                .limit(10)
            ).scalars().all()
        seed_ids = {seed.id for seed in seeds}
        seeds.extend(seed for seed in noise_seeds if seed.id not in seed_ids)

        shortlisted_ids: set[uuid.UUID] = set()
        if seeds:
            db.execute(text("SET LOCAL hnsw.ef_search = 100"))
            per_seed_fetch = settings.ann_candidates_per_seed * (3 if english_only else 1)
            for seed in seeds:
                nearest = db.execute(
                    select(Song.id, Song.language)
                    .where(Song.feature_vector.is_not(None), Song.retrieval_vector.isnot(None))
                    .order_by(Song.retrieval_vector.l2_distance(seed.retrieval_vector))
                    .limit(per_seed_fetch)
                ).tuples().all()
                for song_id, language in nearest:
                    if song_id in excluded_ids or (english_only and language != "en"):
                        continue
                    shortlisted_ids.add(song_id)
                    if len(shortlisted_ids) >= settings.ann_candidate_cap:
                        break
                if len(shortlisted_ids) >= settings.ann_candidate_cap:
                    break
        candidates = (
            db.execute(
                select(Song)
                .options(defer(Song.retrieval_vector))
                .where(Song.id.in_(shortlisted_ids))
            ).scalars().all()
            if shortlisted_ids else []
        )
        # Migration-safe fallback while old rows are being backfilled.
        if len(candidates) < limit:
            candidates = db.execute(select(Song).options(defer(Song.retrieval_vector)).where(*conditions, Song.id.not_in(excluded_ids)).limit(settings.ann_candidate_cap)).scalars().all()
    else:
        candidates = db.execute(select(Song).options(defer(Song.retrieval_vector)).where(*conditions, Song.id.not_in(excluded_ids)).limit(settings.ann_candidate_cap)).scalars().all()

    if english_only:
        candidates = [
            song
            for song in candidates
            if is_english_recommendation_eligible(song.title, song.artist, song.language)
        ]
        # Legacy rows can have `language='en'` from the old lenient classifier. If the ANN
        # shortlist contained too many of them, refill from all SQL-eligible candidates before
        # exact SonicDistance ranking so strict mode still returns a full page.
        if len(candidates) < limit:
            candidates = [
                song
                for song in db.execute(select(Song).options(defer(Song.retrieval_vector)).where(*conditions, Song.id.not_in(excluded_ids)).limit(settings.ann_candidate_cap)).scalars().all()
                if is_english_recommendation_eligible(song.title, song.artist, song.language)
            ]
    if not candidates:
        return []

    from app.models import UserTasteWeights
    learned = db.get(UserTasteWeights, user_id)
    weights = DEFAULT_WEIGHTS if learned is None else SonicDistanceWeights(
        genre=learned.genre, timbre=learned.timbre, rhythm=learned.rhythm,
        tonal=learned.tonal, energy=learned.energy,
    )
    return await score_candidates(user_song_rows, candidates, limit=limit, sonic_weights=weights)


async def score_candidates(
    user_song_rows: list[tuple[UserSong, Song]],
    candidates: list[Song],
    limit: int = 10,
    lastfm_active: bool | None = None,
    blend_mode: str = "weighted",
    sonic_weights: SonicDistanceWeights = DEFAULT_WEIGHTS,
) -> list[tuple[Song, float, Song, int]]:
    """The actual ranking algorithm — acoustic cluster-consensus scoring, the optional Last.fm
    collaborative blend, and the diversity cap — independent of the database. Factored out of
    recommend_for_user so app/services/evaluation.py's offline holdout testing can run the
    *exact* production algorithm against a simulated (visible-songs-only, held-out-songs-as-
    candidates) split, rather than a reimplementation that could quietly drift out of sync.
    `sonic_weights` defaults to production (DEFAULT_WEIGHTS) — app/scripts/learn_weights.py is
    the only caller that passes a candidate vector, to score it via holdout_evaluate.

    Acoustic scoring: a candidate scores against a cluster by its *average* SonicDistance to
    every song in that cluster (recency-weighted — see _recency_weight), and its overall
    acoustic score is the best (lowest) average across all of the user's clusters. This is
    deliberately not the same as either extreme this project already tried and rejected:
      - a single centroid over the user's entire history blurs together unrelated clusters
        (the mathematical average of Drake, Kanye West, Shakira, and Radiohead isn't "a song
        like any of those").
      - nearest-neighbor-to-any-single-song lets one outlier song vouch for a candidate that
        has nothing to do with the rest of the user's taste — a candidate that's only near one
        member of a 4-song cluster, and far from the other three, should lose to a candidate
        that's moderately close to all four.
    Songs HDBSCAN left unclustered (cluster_label -1 or None) each become their own cluster of
    one, so the formula degrades to plain nearest-neighbor for exactly the songs that have no
    real neighborhood to be judged against.

    Collaborative scoring: for each of the user's artists/tracks, app/services/lastfm.py gives
    a [0,1] community-similarity score to each candidate, derived from real listener/tag
    co-occurrence across Last.fm's user base — the "people with taste like yours also like
    this" signal pure audio content can't see. Only applied when `lastfm_active` (defaults to
    whether LASTFM_API_KEY is configured); with it off, ranking is untouched pure-SonicDistance.
    Two ways to combine it with the acoustic score, per `blend_mode`:
      - "weighted" (default, production): ACOUSTIC_WEIGHT * (1 - sonic_distance) +
        ARTIST_AFFINITY_WEIGHT * artist_match + TRACK_AFFINITY_WEIGHT * track_match, over every
        candidate. Simple, but a strong collaborative match can drag an acoustically-mediocre
        candidate above a much closer one, since the two scores were never on the same scale.
      - "cascade" (Burke's hybrid taxonomy, Recommender Systems Handbook): acoustic ranking
        first decides the CASCADE_SHORTLIST_SIZE eligible candidates, and the collaborative
        score only *reorders within* that shortlist — a candidate can never outrank one that
        didn't even make the acoustic cut, no matter how strong its Last.fm match. Better suited
        to sonicmap's very sparse collaborative data (currently 2 users) per the Handbook's
        sparse-data guidance. See app/scripts/evaluate_recommendations.py for a measured
        comparison between the two rather than a guess.

    Results are then passed through a per-artist diversity cap (MAX_PER_ARTIST) so one artist
    with heavy catalog presence can't dominate the list."""
    if not user_song_rows or not candidates:
        return []
    if lastfm_active is None:
        lastfm_active = bool(settings.lastfm_api_key)

    clusters: dict[int, list[tuple[int, Song, float]]] = {}
    for idx, (user_song, song) in enumerate(user_song_rows):
        label = user_song.cluster_label
        key = label if label is not None and label != -1 else -(idx + 1)  # unique solo cluster
        clusters.setdefault(key, []).append((idx, song, _recency_weight(user_song.added_at)))

    user_vectors = [_song_vectors(song) for _, song in user_song_rows]
    candidate_vectors = [_song_vectors(song) for song in candidates]
    exact_distances = _cross_distance_matrix(user_vectors, candidate_vectors, sonic_weights)

    # cluster_size lets the caller (recommend.py's _build_reason) tell a genuine multi-song
    # cluster-consensus match apart from the degenerate case — a lone/unclustered song, whose
    # "cluster" is just itself, where the formula correctly falls back to nearest-neighbor but
    # the explanation text needs to say something different than it would for a real cluster.
    acoustic: list[tuple[Song, float, Song, int]] = []
    for candidate_idx, candidate in enumerate(candidates):
        best_cluster_distance = float("inf")
        best_match = user_song_rows[0][1]
        best_cluster_size = 1
        for key, members in clusters.items():
            indices = [idx for idx, _song, _weight in members]
            distances = exact_distances[indices, candidate_idx]
            recency_weights = np.asarray([weight for _idx, _song, weight in members])
            avg_distance = float(np.average(distances, weights=recency_weights))
            if avg_distance < best_cluster_distance:
                best_cluster_distance = avg_distance
                # Closest individual member for the reason text — plain audio proximity, not
                # recency-weighted, since "best match" should describe why it sounds similar,
                # not why it ranked where it did.
                songs = [song for _idx, song, _weight in members]
                best_match = songs[int(np.argmin(distances))]
                best_cluster_size = len(members)
        acoustic.append((candidate, best_cluster_distance, best_match, best_cluster_size))

    if not lastfm_active:
        blended = [(c, d, m, cs, max(0.0, 1.0 - d)) for c, d, m, cs in acoustic]
        blended.sort(key=lambda row: row[4], reverse=True)
        diversified = _apply_diversity_cap(blended, limit)
        return [(c, d, m, cs) for c, d, m, cs, _score in diversified]

    user_artists = {song.artist for _, song in user_song_rows}
    user_tracks = [(song.artist, song.title) for _, song in user_song_rows]

    if blend_mode == "cascade":
        # Burke's cascade hybrid pattern (Recommender Systems Handbook): content-based first
        # decides who's even eligible, collaborative signal only reorders within that eligible
        # set — rather than the weighted mode's risk of a strong collaborative match dragging
        # an acoustically-mediocre candidate above a much closer one. Only makes sense with a
        # real collaborative channel active; falls through to "weighted" behavior above when
        # lastfm_active is False, same acoustic-only ranking either way in that case.
        acoustic.sort(key=lambda row: row[1])
        shortlist = acoustic[:CASCADE_SHORTLIST_SIZE]
        shortlist_artists = {c.artist for c, _, _, _ in shortlist}
        shortlist_tracks = [(c.artist, c.title) for c, _, _, _ in shortlist]
        artist_affinity, track_affinity = await asyncio.gather(
            lastfm.artist_affinity_scores(user_artists, shortlist_artists),
            lastfm.track_affinity_scores(user_tracks, shortlist_tracks),
        )
        blended = [
            (
                c,
                d,
                m,
                cs,
                ARTIST_AFFINITY_WEIGHT * artist_affinity.get(c.artist, 0.0)
                + TRACK_AFFINITY_WEIGHT * track_affinity.get(lastfm.track_key(c.artist, c.title), 0.0),
            )
            for c, d, m, cs in shortlist
        ]
        # Collaborative score descending; acoustic distance ascending as the tiebreak for the
        # (common) case where neither Last.fm channel matched at all.
        blended.sort(key=lambda row: (row[4], -row[1]), reverse=True)
    else:
        candidate_artists = {c.artist for c in candidates}
        candidate_tracks = [(c.artist, c.title) for c in candidates]
        artist_affinity, track_affinity = await asyncio.gather(
            lastfm.artist_affinity_scores(user_artists, candidate_artists),
            lastfm.track_affinity_scores(user_tracks, candidate_tracks),
        )
        blended = [
            (
                c,
                d,
                m,
                cs,
                ACOUSTIC_WEIGHT * max(0.0, 1.0 - d)
                + ARTIST_AFFINITY_WEIGHT * artist_affinity.get(c.artist, 0.0)
                + TRACK_AFFINITY_WEIGHT * track_affinity.get(lastfm.track_key(c.artist, c.title), 0.0),
            )
            for c, d, m, cs in acoustic
        ]
        blended.sort(key=lambda row: row[4], reverse=True)

    diversified = _apply_diversity_cap(blended, limit)
    return [(c, d, m, cs) for c, d, m, cs, _score in diversified]
