"""Short-lived per-process recommendation cache with explicit user invalidation."""
import asyncio
import time
import uuid

TTL_SECONDS = 15.0
_cache: dict[tuple, tuple[float, list]] = {}
_locks: dict[tuple, asyncio.Lock] = {}


def get(key: tuple):
    cached = _cache.get(key)
    if cached is None:
        return None
    created, value = cached
    if time.monotonic() - created > TTL_SECONDS:
        _cache.pop(key, None)
        return None
    return value


def put(key: tuple, value: list) -> None:
    _cache[key] = (time.monotonic(), value)


def lock_for(key: tuple) -> asyncio.Lock:
    return _locks.setdefault(key, asyncio.Lock())


def invalidate_user(user_id: uuid.UUID) -> None:
    for key in [key for key in _cache if key[0] == user_id]:
        _cache.pop(key, None)
