"""Offline holdout evaluation: hides some of each real user's songs, asks the recommender to
guess them back, and reports R-Precision / Clicks — with and without the Last.fm collaborative
blend, so the comparison actually says whether that channel helps or hurts, rather than
guessing. See app/services/evaluation.py for the methodology and its sourcing (RecSys Challenge
2018 / recsys-smpd).

Run inside the backend container:
    docker compose exec api python -m app.scripts.evaluate_recommendations
"""

import asyncio

from sqlalchemy import select

from app.db import SessionLocal
from app.models import User
from app.services.evaluation import EvaluationResult, holdout_evaluate

HOLD_OUT_COUNT = 2
TRIALS = 5
SEED = 42  # fixed so back-to-back runs (e.g. before/after a weight change) are comparable,
# and so the WITH/WITHOUT Last.fm runs below hold out the identical songs each trial


def _print_result(label: str, result: EvaluationResult) -> None:
    print(f"  --- {label} ---")
    if result.skipped_reason:
        print(f"  skipped: {result.skipped_reason}")
        return
    print(f"  mean R-Precision: {result.mean_r_precision:.2f}   mean Clicks: {result.mean_clicks:.2f}")
    for i, trial in enumerate(result.trials, 1):
        hits = ", ".join(trial.top_k_hits) or "none"
        print(f"    trial {i}: held out [{', '.join(trial.held_out)}] -> top-10 hits: {hits}")


async def _run() -> None:
    db = SessionLocal()
    try:
        users = db.execute(select(User)).scalars().all()
        for user in users:
            label = user.display_name or str(user.id)
            print(f"\n=== {label} ===")

            without_lastfm = await holdout_evaluate(
                db, user.id, label, hold_out_count=HOLD_OUT_COUNT, trials=TRIALS,
                lastfm_active=False, seed=SEED,
            )
            _print_result("pure SonicDistance (no Last.fm)", without_lastfm)

            weighted = await holdout_evaluate(
                db, user.id, label, hold_out_count=HOLD_OUT_COUNT, trials=TRIALS,
                lastfm_active=True, blend_mode="weighted", seed=SEED,
            )
            _print_result("weighted blend (production default)", weighted)

            cascade = await holdout_evaluate(
                db, user.id, label, hold_out_count=HOLD_OUT_COUNT, trials=TRIALS,
                lastfm_active=True, blend_mode="cascade", seed=SEED,
            )
            _print_result("cascade (Last.fm reranks acoustic shortlist)", cascade)
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(_run())
