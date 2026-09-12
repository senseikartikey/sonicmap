"""Fire-and-forget background work that's actually decoupled from the request lifecycle.

FastAPI's own BackgroundTasks run *before* yield-dependencies are torn down (this is
documented, intentional behavior — it's what lets a background task reuse a request's `db`
session). The catalog-expansion and bulk-resolve tasks in this codebase don't want or need
that: each already opens its own short-lived SessionLocal() internally. But because they were
being registered via `background_tasks.add_task(...)`, the *request's* `db: Session =
Depends(get_db)` stayed checked out from the connection pool for the entire duration of
whatever background work that request queued — which, for a bulk playlist import or a
recommendations call queuing four expansion strategies, can be minutes. With a 15-connection
pool, a handful of ordinary actions in a short session was enough to exhaust it entirely,
leaving unrelated requests (including a plain "Rebuild map" click) queued for up to the pool's
30s checkout timeout before failing.

fire_and_forget schedules a coroutine on the event loop directly via asyncio.create_task,
which has no relationship to any request's dependency-injection teardown at all — the
request's own db session closes normally right after its response is built, regardless of how
long the scheduled work takes.
"""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)

# asyncio.create_task() returns a Task that can be garbage-collected mid-run if nothing holds
# a reference to it — a well-known footgun. Keeping every in-flight task in this set (and
# discarding it on completion via the done-callback below) is the standard fix.
_background_tasks: set[asyncio.Task] = set()


def fire_and_forget(coro: Coroutine[Any, Any, Any]) -> None:
    async def _run() -> None:
        try:
            await coro
        except Exception:
            # Best-effort catalog growth — a failure here should never be able to affect a
            # user-facing request, but it also shouldn't vanish silently.
            logger.exception("Background task failed")

    task = asyncio.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
