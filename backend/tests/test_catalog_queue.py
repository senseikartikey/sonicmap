from datetime import datetime, timedelta, timezone

from app.config import settings
from app.services.catalog_jobs import (
    PERMANENT_FAILURE_COOLDOWN,
    is_exhausted,
    may_retry_after_failure,
)

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def test_a_job_that_burned_its_attempts_counts_as_exhausted():
    assert is_exhausted("failed", 0)
    assert is_exhausted("queued", settings.catalog_job_max_attempts)
    assert not is_exhausted("queued", 1)
    assert not is_exhausted("completed", 0)


def test_a_failed_job_is_not_revived_inside_the_cooldown():
    """The deadlock this prevents: enqueue reset every failed job to attempts=0, so a user whose
    map is full of unreleasable bootlegs had those same tracks re-queued on every recommendations
    call. The worker re-failed them forever and 1.76M never-tried songs stayed untouched, with the
    analysed count frozen at 23,356."""
    assert not may_retry_after_failure(NOW - timedelta(minutes=1), NOW)
    assert not may_retry_after_failure(NOW - PERMANENT_FAILURE_COOLDOWN + timedelta(seconds=1), NOW)


def test_a_failed_job_becomes_eligible_once_the_cooldown_passes():
    """Permanently failed must not mean permanently banned — a provider can add a track later."""
    assert may_retry_after_failure(NOW - PERMANENT_FAILURE_COOLDOWN, NOW)
    assert may_retry_after_failure(NOW - timedelta(days=30), NOW)


def test_an_unstamped_failure_is_treated_as_recent():
    """Reading "unknown" as "long ago" would restore the deadlock on exactly the rows that
    caused it, so the safe direction is to wait."""
    assert not may_retry_after_failure(None, NOW)
