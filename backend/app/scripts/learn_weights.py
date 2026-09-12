"""Searches for a SonicDistance weight vector (genre/timbre/rhythm/tonal/energy) that scores
better on the real holdout-evaluation harness than the current hand-picked constants — the
first time anything in sonicmap's ranking pipeline is actually *fit* to data rather than
guessed. See app/services/brain.py's SonicDistanceWeights/DEFAULT_WEIGHTS and
app/services/evaluation.py's holdout_evaluate for the machinery this drives.

Method: random search over the weight simplex (Dirichlet-sampled, so every candidate's five
weights are non-negative and sum to 1, same shape as the hand-picked constants), scored by mean
R-Precision, tie-broken by mean Clicks — plus a short local hill-climb around whichever
candidate (random or the current baseline) scores best, since blind random search alone can
miss a nearby improvement.

sonicmap's real user base is tiny (currently 2 users), which is nowhere near enough holdout
judgments on its own for this search to say anything trustworthy — see the honesty check at the
end. To fix that without waiting on real user growth, every run also builds synthetic taste
profiles from two independent sources: Last.fm's real artist-similarity graph
(app/services/synthetic_taste.py) and real acoustic-feature proximity with genre forced to
zero (app/services/acoustic_synthetic_taste.py). Both exist for the same reason (give the
search enough signal to run at all), but a first run against only the Last.fm-graph profiles
found a real bias worth recording: artist-similarity is structurally correlated with catalog
genre tags, so the search kept converging on "genre≈1.0, ignore everything else" — recovering
that correlation, not evidence genre alone predicts real taste. The acoustic profiles are
immune to that specific shortcut by construction (genre earns zero credit for membership in
them), so mixing both sources in gives the search honest pressure to value the non-genre
components too, not just whichever ground truth happens to reward genre. Real users and
synthetic profiles (of either source) are scored separately and reported separately (real-user
numbers are the ones that actually matter; synthetic is what makes the search itself have
enough signal to run) but combined for the search decision.

Every candidate — including the current production baseline, always evaluated first — is
scored against the *identical* holdout splits (one fixed SEED, reused for every candidate, both
real users, and every synthetic profile) so the comparison is paired: a difference in score
reflects the weight vector, not which songs happened to get hidden that trial. lastfm_active is
always False here deliberately: this tunes the acoustic SonicDistance metric specifically, and
mixing in the Last.fm collaborative blend would muddy whether an improvement came from the
weight change or from how it happened to interact with that separate signal.

This does NOT touch production by default — it only prints what it found. Pass --apply to
write the winner to app/sonic_distance_weights.json, which app/services/brain.py loads at
import time (see _load_default_weights); that file lives in the bind-mounted app/ directory, so
applying a result just needs a container restart, not a rebuild.

Run inside the backend container:
    docker compose exec api python -m app.scripts.learn_weights
    docker compose exec api python -m app.scripts.learn_weights --candidates 80 --synthetic 40 --apply
"""

import argparse
import asyncio
import dataclasses
import json
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Song, User
from app.services.acoustic_synthetic_taste import generate_acoustic_profiles
from app.services.brain import DEFAULT_WEIGHTS, _WEIGHTS_FILE, SonicDistanceWeights
from app.services.evaluation import EvaluationResult, _FakeUserSong, evaluate_song_rows, holdout_evaluate
from app.services.synthetic_taste import generate_synthetic_profiles

HOLD_OUT_COUNT = 2
TRIALS = 5
SEED = 42  # fixed across every candidate/user/profile so holdout splits are paired, not just similar
LOCAL_REFINE_STEPS = 15  # small hill-climb around the best candidate random search finds
LOCAL_REFINE_SIGMA = 0.08  # stdev of the per-dimension perturbation, before renormalizing


def _to_weights(vec: np.ndarray) -> SonicDistanceWeights:
    genre, timbre, rhythm, tonal, energy = vec.tolist()
    return SonicDistanceWeights(genre=genre, timbre=timbre, rhythm=rhythm, tonal=tonal, energy=energy)


def _to_vec(weights: SonicDistanceWeights) -> np.ndarray:
    return np.array([weights.genre, weights.timbre, weights.rhythm, weights.tonal, weights.energy])


async def _score_synthetic_profile(
    profile_songs: list[Song], full_catalog: list[Song], weights: SonicDistanceWeights
) -> EvaluationResult:
    now = datetime.now(timezone.utc)
    rows = [(_FakeUserSong(-1, now), song) for song in profile_songs]
    profile_ids = {song.id for song in profile_songs}
    catalog_pool = [song for song in full_catalog if song.id not in profile_ids]
    return await evaluate_song_rows(
        rows, catalog_pool, "synthetic",
        hold_out_count=HOLD_OUT_COUNT, trials=TRIALS,
        lastfm_active=False, seed=SEED, sonic_weights=weights,
    )


async def _score_candidate(
    db: Session,
    users: list[User],
    synthetic_profiles: list[tuple[str, list[Song]]],
    full_catalog: list[Song],
    weights: SonicDistanceWeights,
) -> dict:
    """Scores one weight vector against real users and synthetic profiles separately, then
    combined. All three means are unweighted averages *per profile* (not per trial/judgment),
    so neither a big real catalog nor a big synthetic profile can dominate just by contributing
    more trials than everyone else."""
    real_r: list[float] = []
    real_c: list[float] = []
    for user in users:
        result = await holdout_evaluate(
            db, user.id, str(user.id),
            hold_out_count=HOLD_OUT_COUNT, trials=TRIALS,
            lastfm_active=False, seed=SEED, sonic_weights=weights,
        )
        if result.mean_r_precision is not None:
            real_r.append(result.mean_r_precision)
        if result.mean_clicks is not None:
            real_c.append(result.mean_clicks)

    synth_r: list[float] = []
    synth_c: list[float] = []
    for _label, songs in synthetic_profiles:
        result = await _score_synthetic_profile(songs, full_catalog, weights)
        if result.mean_r_precision is not None:
            synth_r.append(result.mean_r_precision)
        if result.mean_clicks is not None:
            synth_c.append(result.mean_clicks)

    all_r = real_r + synth_r
    all_c = real_c + synth_c
    return {
        "real_r": sum(real_r) / len(real_r) if real_r else None,
        "real_c": sum(real_c) / len(real_c) if real_c else None,
        "synth_r": sum(synth_r) / len(synth_r) if synth_r else None,
        "synth_c": sum(synth_c) / len(synth_c) if synth_c else None,
        "combined_r": sum(all_r) / len(all_r) if all_r else None,
        "combined_c": sum(all_c) / len(all_c) if all_c else None,
        "real_judgments": len(real_r) * TRIALS * HOLD_OUT_COUNT,
        "combined_judgments": len(all_r) * TRIALS * HOLD_OUT_COUNT,
    }


async def _run(num_candidates: int, num_synthetic: int, num_acoustic: int, apply: bool) -> None:
    db = SessionLocal()
    try:
        users = db.execute(select(User)).scalars().all()
        full_catalog = list(db.execute(select(Song).where(Song.feature_vector.is_not(None))).scalars().all())

        synthetic_profiles: list[tuple[str, list[Song]]] = []
        if num_synthetic > 0:
            print(f"Building up to {num_synthetic} synthetic taste profiles from Last.fm's "
                  "artist-similarity graph...")
            lastfm_profiles = await generate_synthetic_profiles(db, num_synthetic, seed=SEED)
            print(f"Built {len(lastfm_profiles)} usable Last.fm-graph profiles "
                  f"({sum(len(s) for _, s in lastfm_profiles)} songs total).")
            synthetic_profiles.extend(lastfm_profiles)
        if num_acoustic > 0:
            print(f"Building up to {num_acoustic} synthetic taste profiles from real acoustic "
                  "proximity (genre excluded by construction)...")
            acoustic_profiles = generate_acoustic_profiles(db, num_acoustic, seed=SEED)
            print(f"Built {len(acoustic_profiles)} usable acoustic profiles "
                  f"({sum(len(s) for _, s in acoustic_profiles)} songs total).\n")
            synthetic_profiles.extend(acoustic_profiles)

        if not users and not synthetic_profiles:
            print("No real users and no synthetic profiles could be built — nothing to evaluate against.")
            return

        rng = np.random.default_rng(SEED)

        candidates: list[tuple[str, SonicDistanceWeights]] = [("current production", DEFAULT_WEIGHTS)]
        for i in range(num_candidates):
            vec = rng.dirichlet(np.ones(5))
            candidates.append((f"random #{i + 1}", _to_weights(vec)))

        print(f"Evaluating {len(candidates)} candidates against {len(users)} real user(s) + "
              f"{len(synthetic_profiles)} synthetic profile(s), {TRIALS} paired holdout trials "
              f"each (seed={SEED})...\n")

        scored: list[tuple[str, SonicDistanceWeights, dict]] = []
        for label, weights in candidates:
            stats = await _score_candidate(db, users, synthetic_profiles, full_catalog, weights)
            if stats["combined_r"] is None:
                print(f"  {label}: skipped (nothing had enough data)")
                continue
            scored.append((label, weights, stats))
            real_str = f"{stats['real_r']:.3f}" if stats["real_r"] is not None else "n/a"
            print(f"  {label}: combined R-Precision={stats['combined_r']:.3f} "
                  f"(real-only={real_str})  combined Clicks={stats['combined_c']:.2f}  "
                  f"(genre={weights.genre:.2f} timbre={weights.timbre:.2f} rhythm={weights.rhythm:.2f} "
                  f"tonal={weights.tonal:.2f} energy={weights.energy:.2f})")

        if not scored:
            print("\nNothing had enough holdout data to evaluate any candidate — nothing to report.")
            return

        # Best-so-far by combined mean R-Precision, ties broken by lower combined Clicks.
        scored.sort(key=lambda row: (row[2]["combined_r"], -row[2]["combined_c"]), reverse=True)
        best_label, best_weights, best_stats = scored[0]

        print(f"\nBest of random search: {best_label} (combined R-Precision={best_stats['combined_r']:.3f}) "
              f"— refining locally ({LOCAL_REFINE_STEPS} steps)...")
        cur_vec, cur_stats = _to_vec(best_weights), best_stats
        for step in range(LOCAL_REFINE_STEPS):
            perturbed = np.clip(cur_vec + rng.normal(0, LOCAL_REFINE_SIGMA, size=5), 1e-6, None)
            perturbed = perturbed / perturbed.sum()
            candidate_weights = _to_weights(perturbed)
            stats = await _score_candidate(db, users, synthetic_profiles, full_catalog, candidate_weights)
            if stats["combined_r"] is None:
                continue
            if (stats["combined_r"], -stats["combined_c"]) > (cur_stats["combined_r"], -cur_stats["combined_c"]):
                cur_vec, cur_stats = perturbed, stats
                print(f"  step {step + 1}: improved to combined R-Precision={cur_stats['combined_r']:.3f}  "
                      f"Clicks={cur_stats['combined_c']:.2f}")

        final_weights = _to_weights(cur_vec)
        baseline_stats = next((s for label, _, s in scored if label == "current production"), None)

        # Sample size the *combined* score is built from — this is what determines whether a
        # delta means anything: one extra hit swings mean R-Precision by 1/judgments.
        judgments = cur_stats["combined_judgments"]
        one_hit_swing = 1.0 / judgments if judgments else 1.0
        real_judgments = cur_stats["real_judgments"]

        print("\n=== Result ===")
        print(f"Current production: {DEFAULT_WEIGHTS}")
        print(f"Best found:          {final_weights}")
        print(f"  combined R-Precision={cur_stats['combined_r']:.3f}  Clicks={cur_stats['combined_c']:.2f}  "
              f"({judgments} judgments: {real_judgments} real + {judgments - real_judgments} synthetic)")
        if cur_stats["real_r"] is not None:
            print(f"  real-users-only R-Precision={cur_stats['real_r']:.3f}  Clicks={cur_stats['real_c']:.2f}  "
                  f"({real_judgments} judgments) — this is the number that actually matters; "
                  "synthetic profiles exist to give the search enough signal to run, not to "
                  "replace real-user validation.")
        else:
            print("  no real user had enough data this run — every judgment above is synthetic.")

        if baseline_stats is not None:
            delta = cur_stats["combined_r"] - baseline_stats["combined_r"]
            if judgments < 100 or delta <= 2 * one_hit_swing:
                print(
                    f"\nDelta vs. production is {delta:+.3f} combined R-Precision, on a sample "
                    f"this small ({judgments} judgments) — that's within the range one or two "
                    "lucky/unlucky holdout draws could produce on their own, not evidence the "
                    "weight vector itself is actually better. Not recommending applying this yet."
                )
            else:
                print(f"\nDelta vs. production: {delta:+.3f} combined R-Precision — bigger than "
                      "one lucky draw could produce. Still worth treating as a lead to re-verify "
                      "against real-user growth rather than a settled result, especially if the "
                      "real-users-only number above didn't move the same direction.")

        if apply:
            _WEIGHTS_FILE.write_text(json.dumps(dataclasses.asdict(final_weights), indent=2) + "\n")
            print(f"\nWrote {final_weights} to {_WEIGHTS_FILE}")
            print("Restart the api container to pick it up (no rebuild needed — this file is bind-mounted).")
        else:
            print("\nNot applied (pass --apply to write this to app/sonic_distance_weights.json).")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=int, default=40, help="number of random-search candidates to try")
    parser.add_argument("--synthetic", type=int, default=30, help="number of Last.fm-graph synthetic profiles to build (0 to disable)")
    parser.add_argument("--acoustic-synthetic", type=int, default=20, dest="num_acoustic", help="number of acoustic-proximity synthetic profiles to build (0 to disable)")
    parser.add_argument("--apply", action="store_true", help="write the best result to app/sonic_distance_weights.json")
    args = parser.parse_args()
    asyncio.run(_run(args.candidates, args.synthetic, args.num_acoustic, args.apply))
