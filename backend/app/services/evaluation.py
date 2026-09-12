"""Offline holdout evaluation for recommend_for_user — the methodology sonicmap otherwise had
no equivalent of: hide some of a real user's songs, recompute taste-clusters on what's left,
ask the recommender to guess the hidden songs back, and score how well it did. Adapted from the
standard playlist-continuation evaluation pattern used by the RecSys Challenge 2018 / Spotify
Million Playlist Dataset research community (recsyschallenge.com/2018,
github.com/anaezquerro/recsys-smpd) to sonicmap's actual data: a per-user song history instead
of anonymous playlists.

Two metrics, matching that community's standard pair (skipping NDCG, which needs graded
relevance sonicmap doesn't have):
  - R-Precision: of the held-out songs, what fraction landed in the top-K recommendations.
    Higher is better, range [0, 1].
  - Clicks: for each held-out song, how many pages of 10 you'd have to click "show more"
    through before reaching it (ceil(rank / 10)) — capped at MAX_CLICKS for a song that never
    surfaces within the scanned window at all. Lower is better.

Every held-out song is scored against the *real production ranking algorithm*
(app.services.brain.score_candidates) run on a *simulated* cluster structure recomputed from
only the visible songs — never against the real UserSong.cluster_label, which would leak the
held-out songs into their own answer key.
"""

import math
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Song, UserSong
from app.services.brain import (
    DEFAULT_WEIGHTS,
    SonicDistanceWeights,
    _compute_coords_and_labels,
    _pairwise_distance_matrix,
    _song_vectors,
    score_candidates,
)

MAX_CLICKS = 20  # Cap for a held-out song that never surfaces within the scanned window.
SCAN_WINDOW = MAX_CLICKS * 10  # How deep into the ranked list to look for each held-out song.


@dataclass
class HoldoutTrial:
    held_out: list[str]  # "Artist - Title" labels, for a human-readable report
    visible_count: int
    r_precision: float
    clicks: list[int]  # one per held-out song
    top_k_hits: list[str]  # held-out songs that landed in the top-K


@dataclass
class EvaluationResult:
    user_display_name: str
    trials: list[HoldoutTrial] = field(default_factory=list)
    skipped_reason: str | None = None

    @property
    def mean_r_precision(self) -> float | None:
        if not self.trials:
            return None
        return sum(t.r_precision for t in self.trials) / len(self.trials)

    @property
    def mean_clicks(self) -> float | None:
        all_clicks = [c for t in self.trials for c in t.clicks]
        if not all_clicks:
            return None
        return sum(all_clicks) / len(all_clicks)


class _FakeUserSong:
    """A non-persisted stand-in for UserSong, carrying only what score_candidates actually
    reads (cluster_label, added_at) — never added to a session, never written to the database.
    Lets holdout_evaluate simulate "what if these songs had never been added" clustering
    without touching a real user's real map."""

    def __init__(self, cluster_label: int, added_at: datetime):
        self.cluster_label = cluster_label
        self.added_at = added_at


async def holdout_evaluate(
    db: Session,
    user_id: uuid.UUID,
    user_display_name: str,
    hold_out_count: int = 2,
    top_k: int = 10,
    trials: int = 5,
    lastfm_active: bool | None = None,
    blend_mode: str = "weighted",
    seed: int | None = None,
    sonic_weights: SonicDistanceWeights = DEFAULT_WEIGHTS,
) -> EvaluationResult:
    """Runs `trials` independent random holdouts for one real user and aggregates them — a thin
    DB-fetching wrapper around evaluate_song_rows (see its docstring for the actual methodology,
    which is shared with synthetic taste profiles — see app/services/synthetic_taste.py)."""
    all_rows: list[tuple[UserSong, Song]] = (
        db.execute(
            select(UserSong, Song)
            .join(Song, UserSong.song_id == Song.id)
            .where(UserSong.user_id == user_id, Song.feature_vector.is_not(None))
            .order_by(UserSong.id)  # deterministic row order, so a fixed seed is reproducible
        )
        .tuples()
        .all()
    )

    result = EvaluationResult(user_display_name=user_display_name)
    min_needed = hold_out_count + 1  # need >=1 song left visible to build any cluster context
    if len(all_rows) < min_needed:
        result.skipped_reason = (
            f"only {len(all_rows)} extracted songs — need at least {min_needed} to hold out "
            f"{hold_out_count} and still have visible context"
        )
        return result

    already_have_ids = {song.id for _, song in all_rows}
    catalog_pool = (
        db.execute(
            select(Song)
            .where(Song.feature_vector.is_not(None), Song.id.not_in(already_have_ids))
            .order_by(Song.id)
        )
        .scalars()
        .all()
    )

    return await evaluate_song_rows(
        all_rows,
        catalog_pool,
        user_display_name,
        hold_out_count=hold_out_count,
        top_k=top_k,
        trials=trials,
        lastfm_active=lastfm_active,
        blend_mode=blend_mode,
        seed=seed,
        sonic_weights=sonic_weights,
    )


async def evaluate_song_rows(
    all_rows: list[tuple[UserSong | _FakeUserSong, Song]],
    catalog_pool: list[Song],
    display_name: str,
    hold_out_count: int = 2,
    top_k: int = 10,
    trials: int = 5,
    lastfm_active: bool | None = None,
    blend_mode: str = "weighted",
    seed: int | None = None,
    sonic_weights: SonicDistanceWeights = DEFAULT_WEIGHTS,
) -> EvaluationResult:
    """The actual holdout methodology (module docstring), independent of where `all_rows` and
    `catalog_pool` came from — a real user's DB rows (holdout_evaluate above) or a synthetic
    taste profile built from Last.fm's artist-similarity graph
    (app/services/synthetic_taste.py), which only needs each row's second element to be a real
    Song and the first element to expose `.added_at` (see _FakeUserSong).

    Runs `trials` independent random holdouts and aggregates them — a single trial is noisy
    (which songs happen to get hidden matters a lot at small map sizes), so this reports the
    mean across several draws rather than one lucky or unlucky split. Pass the same `seed`
    across two calls (e.g. lastfm_active=True vs False) to guarantee both runs hold out the
    identical songs each trial — otherwise the comparison isn't paired and the difference could
    just be which songs got hidden, not the algorithm change being tested."""
    rng = random.Random(seed)
    result = EvaluationResult(user_display_name=display_name)
    min_needed = hold_out_count + 1  # need >=1 song left visible to build any cluster context
    if len(all_rows) < min_needed:
        result.skipped_reason = (
            f"only {len(all_rows)} songs — need at least {min_needed} to hold out "
            f"{hold_out_count} and still have visible context"
        )
        return result

    for _ in range(trials):
        shuffled = list(all_rows)
        rng.shuffle(shuffled)
        held_out_rows = shuffled[:hold_out_count]
        visible_rows = shuffled[hold_out_count:]

        # Recompute cluster structure from scratch over *only* the visible songs — reusing the
        # real UserSong.cluster_label would leak the held-out songs into their own answer key.
        if len(visible_rows) >= 2:
            song_vectors = [_song_vectors(song) for _, song in visible_rows]
            distance_matrix = _pairwise_distance_matrix(song_vectors, sonic_weights)
            _, labels = _compute_coords_and_labels(distance_matrix, len(visible_rows))
        else:
            labels = [-1] * len(visible_rows)

        simulated_rows: list[tuple[UserSong, Song]] = [
            (_FakeUserSong(label, user_song.added_at), song)
            for (user_song, song), label in zip(visible_rows, labels)
        ]

        held_out_songs = [song for _, song in held_out_rows]
        candidates = held_out_songs + list(catalog_pool)

        ranked = await score_candidates(
            simulated_rows,
            candidates,
            limit=SCAN_WINDOW,
            lastfm_active=lastfm_active,
            blend_mode=blend_mode,
            sonic_weights=sonic_weights,
        )
        ranked_ids = [song.id for song, _, _, _ in ranked]

        clicks: list[int] = []
        top_k_hits: list[str] = []
        hit_count = 0
        for song in held_out_songs:
            label = f"{song.artist} - {song.title}"
            if song.id in ranked_ids:
                rank = ranked_ids.index(song.id) + 1  # 1-indexed
                clicks.append(math.ceil(rank / 10))
                if rank <= top_k:
                    hit_count += 1
                    top_k_hits.append(label)
            else:
                clicks.append(MAX_CLICKS)

        result.trials.append(
            HoldoutTrial(
                held_out=[f"{s.artist} - {s.title}" for s in held_out_songs],
                visible_count=len(visible_rows),
                r_precision=hit_count / hold_out_count,
                clicks=clicks,
                top_k_hits=top_k_hits,
            )
        )

    return result
