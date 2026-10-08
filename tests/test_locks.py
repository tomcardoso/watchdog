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


def test_heartbeat_keeps_a_long_run_lock_fresh(tmp_path):
    """#696: a long active run re-stamps its lock, so a second invocation never sees it as stale
    while the first is still working. The holder's other lines (its pid label) survive."""
    import time
    lock = tmp_path / ".lock"
    old = _iso(datetime.now(timezone.utc) - timedelta(seconds=3600))
    lock.write_text(f"pid: 4242\nstarted_at: {old}\n")
    with locks.heartbeat(lock, interval=0.02):
        deadline = time.monotonic() + 5
        while locks.lock_age_seconds(lock) > 5 and time.monotonic() < deadline:
            time.sleep(0.01)
    assert locks.lock_age_seconds(lock) < 5
    assert "pid: 4242" in lock.read_text()
    assert locks.acquire_or_take_stale(lock, "started_at: x\n", 1800) is False


def test_heartbeat_never_recreates_a_released_lock(tmp_path):
    import time
    lock = tmp_path / ".lock"
    lock.write_text("started_at: 2020-01-01T00:00:00Z\n")
    with locks.heartbeat(lock, interval=0.02):
        lock.unlink()
        time.sleep(0.1)
    assert not lock.exists()


def test_heartbeat_stops_when_the_block_exits(tmp_path):
    """The beat thread is joined before the block exits, so the caller's release always comes
    after the last beat."""
    import threading
    lock = tmp_path / ".lock"
    lock.write_text("started_at: 2020-01-01T00:00:00Z\n")
    with locks.heartbeat(lock, interval=0.02):
        pass
    assert not any(t.name.startswith("lock-heartbeat") for t in threading.enumerate())


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


def test_concurrent_lock_writers_in_one_process_never_collide(tmp_path):
    """The heartbeat thread and `dig --wait`'s main thread both rewrite the lock (D258)."""
    import threading
    from watchdog.pipeline.locks import refresh_lock
    lock = tmp_path / ".processing-lock"
    lock.write_text("pid: cli\nstarted_at: 2026-01-01T00:00:00Z\n")
    errors = []

    def hammer():
        try:
            for _ in range(500):
                refresh_lock(lock)
        except Exception as e:      # noqa: BLE001 — any error is the failure
            errors.append(e)

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert lock.read_text().startswith("pid: cli")
    assert not list(tmp_path.glob("*.tmp"))
