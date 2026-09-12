"""Read-only latency/concurrency smoke test for production recommendation and map endpoints."""
import asyncio
import statistics
import time

import httpx
from sqlalchemy import select

from app.db import SessionLocal
from app.models import User
from app.services.brain import recommend_for_user
from app.services.jwt_auth import create_session_token


def _percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * fraction))]


async def main():
    db = SessionLocal()
    try:
        users = db.execute(select(User).order_by(User.created_at)).scalars().all()
        for user in users:
            timings = []
            for _ in range(5):
                started = time.perf_counter()
                results = await recommend_for_user(db, user.id, limit=10)
                timings.append((time.perf_counter() - started) * 1000)
            print(f"direct user={user.id} results={len(results)} median_ms={statistics.median(timings):.1f} max_ms={max(timings):.1f}")
        if not users:
            print("no users; HTTP smoke skipped")
            return
        token = create_session_token(users[0].id)
    finally:
        db.close()

    headers = {"Authorization": f"Bearer {token}"}
    semaphore = asyncio.Semaphore(5)
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", headers=headers, timeout=30) as client:
        async def one(path):
            async with semaphore:
                started = time.perf_counter()
                response = await client.get(path)
                return response.status_code, (time.perf_counter() - started) * 1000

        results = await asyncio.gather(*[one("/recommendations?limit=10") for _ in range(20)])
        latencies = [elapsed for status, elapsed in results if status == 200]
        print(f"http recommendations ok={len(latencies)}/20 p50_ms={_percentile(latencies, .5):.1f} p95_ms={_percentile(latencies, .95):.1f} max_ms={max(latencies):.1f}")
        map_results = await asyncio.gather(*[one("/map") for _ in range(20)])
        map_latencies = [elapsed for status, elapsed in map_results if status == 200]
        print(f"http map ok={len(map_latencies)}/20 p50_ms={_percentile(map_latencies, .5):.1f} p95_ms={_percentile(map_latencies, .95):.1f} max_ms={max(map_latencies):.1f}")
        bad_search = await client.post("/ingest/search", json={"query": "   "})
        bad_paste = await client.post("/ingest/paste", json={"raw_text": "   "})
        print(f"validation whitespace_search={bad_search.status_code} whitespace_paste={bad_paste.status_code}")


if __name__ == "__main__":
    asyncio.run(main())
