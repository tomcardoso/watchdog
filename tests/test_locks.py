"""Tests for the shared atomic lock primitives (pipeline/locks.py, #257)."""

import multiprocessing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from watchdog.pipeline import locks


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_acquire_lock_is_exclusive(tmp_path):
    """O_CREAT|O_EXCL: the first caller creates the file and writes its contents; a second
    attempt while it exists fails and leaves the incumbent untouched — the property the old
    check-then-write lacked."""
    lock = tmp_path / ".lock"
    assert locks.acquire_lock(lock, "started_at: first\n") is True
    assert lock.read_text() == "started_at: first\n"
    assert locks.acquire_lock(lock, "started_at: second\n") is False
    assert lock.read_text() == "started_at: first\n"


def test_acquire_or_take_stale_refuses_fresh(tmp_path):
    lock = tmp_path / ".lock"
    fresh = _iso(datetime.now(timezone.utc))
    lock.write_text(f"started_at: {fresh}\n")
    assert locks.acquire_or_take_stale(lock, "started_at: new\n", 1800) is False
    assert f"started_at: {fresh}" in lock.read_text()   # not taken over


def test_acquire_or_take_stale_takes_over_stale(tmp_path):
    lock = tmp_path / ".lock"
    old = _iso(datetime.now(timezone.utc) - timedelta(seconds=3600))
    lock.write_text(f"started_at: {old}\n")
    assert locks.acquire_or_take_stale(lock, "started_at: new\n", 1800) is True
    assert lock.read_text() == "started_at: new\n"


def test_acquire_or_take_stale_refuses_unparseable(tmp_path):
    """A lock with a missing or garbage started_at is of unknown age — refuse and preserve it for
    `watchdog unlock`, rather than delete it regardless of age (what the replaced check-then-
    unlink did)."""
    lock = tmp_path / ".lock"
    lock.write_text("pid: 123\n")   # no started_at line at all
    assert locks.acquire_or_take_stale(lock, "started_at: new\n", 1800) is False
    assert lock.read_text() == "pid: 123\n"

    lock.write_text("started_at: not-a-timestamp\n")
    assert locks.acquire_or_take_stale(lock, "started_at: new\n", 1800) is False
    assert lock.read_text() == "started_at: not-a-timestamp\n"


def test_refresh_lock_resets_age(tmp_path):
    """refresh_lock (#271, for `watchdog ingest --wait`) rewrites started_at to now, so a lock
    held through a long sleep never crosses the staleness threshold."""
    lock = tmp_path / ".lock"
    old = _iso(datetime.now(timezone.utc) - timedelta(seconds=3600))
    lock.write_text(f"pid: cli\nstarted_at: {old}\n")
    locks.refresh_lock(lock)
    age = locks.lock_age_seconds(lock)
    assert age is not None and age < 5
    assert "pid: cli" in lock.read_text()


def _racer(arg):
    lock_path, contents = arg
    return locks.acquire_lock(Path(lock_path), contents)


def test_only_one_of_many_racers_wins(tmp_path):
    """Cross-process contention: N workers race the same lock; exactly one acquires it. Deleting
    the flock/atomic acquisition (mutation) left the whole suite green before this existed."""
    lock = tmp_path / ".lock"
    n = 6
    with multiprocessing.Pool(n) as pool:
        results = pool.map(_racer, [(str(lock), f"started_at: p{i}\n") for i in range(n)])
    assert sum(1 for r in results if r) == 1
    assert lock.exists()
