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
    """A lock with a missing or garbage started_at takes its age from the file's modification
    time (D293): a freshly written one is kept, never deleted on sight."""
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


# ── D293: a lock records its holder, and a dead holder's lock is taken over at once ──────────

def _dead_pid() -> int:
    """The pid of a process that has exited (and been reaped)."""
    import subprocess
    import sys
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _lock_of(pid: int, *, start: str | None = None, machine: str | None = "",
             host: str | None = None, age: float = 0, label: str = "cli") -> str:
    lines = [f"pid: {pid}"]
    if start:
        lines.append(f"pid_start: {start}")
    lines.append(f"host: {host if host is not None else locks.hostname()}")
    mid = locks.machine_id() if machine == "" else machine
    if mid:
        lines.append(f"machine: {mid}")
    when = _iso(datetime.now(timezone.utc) - timedelta(seconds=age))
    lines += [f"label: {label}", f"started_at: {when}"]
    return "\n".join(lines) + "\n"


import pytest  # noqa: E402

needs_proc = pytest.mark.skipif(locks.machine_id() is None or locks.process_status(1)[0] is None,
                                reason="this platform can't identify the machine or its processes")


def test_lock_contents_names_the_holder():
    f = locks._fields(locks.lock_contents("chew"))
    import os
    assert f["pid"] == str(os.getpid())
    assert f["label"] == "chew"
    assert f["host"] == locks.hostname()
    assert locks._parse_iso_age(f["started_at"]) < 5
    if locks.machine_id():
        assert f["machine"] == locks.machine_id()


@needs_proc
def test_dead_pid_on_this_computer_is_taken_over_at_once(tmp_path):
    lock = tmp_path / ".lock"
    lock.write_text(_lock_of(_dead_pid()))            # written a moment ago, holder gone
    assert locks.inspect(lock)["state"] == locks.DEAD
    assert locks.held(lock) is None
    mine = locks.lock_contents("cli")
    assert locks.acquire_or_take_stale(lock, mine) is True
    assert lock.read_text() == mine


@needs_proc
def test_live_pid_is_not_taken_over_however_old(tmp_path):
    import os
    lock = tmp_path / ".lock"
    start = locks.process_status(os.getpid())[1]
    lock.write_text(_lock_of(os.getpid(), start=start, age=10 * 3600))   # no beat for ten hours
    info = locks.inspect(lock)
    assert info["state"] == locks.LIVE and info["where"] == "here"
    assert locks.acquire_or_take_stale(lock, "started_at: x\n") is False
    assert locks.clear_abandoned(lock) is None
    assert lock.exists()


@needs_proc
def test_reused_pid_with_another_start_time_is_dead(tmp_path):
    """The pid is running, but it is not the process that took the lock: it was reused."""
    import os
    lock = tmp_path / ".lock"
    real = locks.process_status(os.getpid())[1]
    if real is None:
        pytest.skip("no start time on this platform")
    lock.write_text(_lock_of(os.getpid(), start=real + "-earlier"))
    assert locks.inspect(lock)["state"] == locks.DEAD
    assert locks.acquire_or_take_stale(lock, "started_at: new\n") is True


@needs_proc
def test_running_pid_without_a_comparable_start_time_falls_back_to_age(tmp_path):
    import os
    lock = tmp_path / ".lock"
    lock.write_text(_lock_of(os.getpid(), start=None, age=60))
    assert locks.inspect(lock)["state"] == locks.LIVE
    lock.write_text(_lock_of(os.getpid(), start=None, age=locks.STALE_SECONDS + 60))
    assert locks.inspect(lock)["state"] == locks.STALE


def test_another_computers_lock_follows_the_age_rule(tmp_path):
    """Its process can't be checked from here, even when the pid happens to be dead here."""
    lock = tmp_path / ".lock"
    lock.write_text(_lock_of(_dead_pid(), machine="0123456789abcdef", host="other-laptop", age=60))
    info = locks.inspect(lock)
    assert info["state"] == locks.REMOTE
    assert info["where"] == "elsewhere" and info["host"] == "other-laptop"
    assert locks.acquire_or_take_stale(lock, "started_at: new\n") is False

    lock.write_text(_lock_of(_dead_pid(), machine="0123456789abcdef", host="other-laptop",
                             age=locks.STALE_SECONDS + 60))
    assert locks.inspect(lock)["state"] == locks.STALE
    assert locks.acquire_or_take_stale(lock, "started_at: new\n") is True


def test_same_host_name_without_the_same_machine_is_another_computer(tmp_path):
    """Two laptops can share a host name; only the machine id proves the lock is ours."""
    lock = tmp_path / ".lock"
    lock.write_text(_lock_of(_dead_pid(), machine="fedcba9876543210", host=locks.hostname()))
    assert locks.inspect(lock)["state"] == locks.REMOTE


def test_unknown_machine_id_never_judges_a_lock_dead(tmp_path, monkeypatch):
    """Fail safe: a platform that gives no machine id treats every lock by its age."""
    lock = tmp_path / ".lock"
    lock.write_text(_lock_of(_dead_pid()))
    monkeypatch.setattr(locks, "machine_id", lambda: None)
    assert locks.inspect(lock)["state"] != locks.DEAD
    assert locks.acquire_or_take_stale(lock, "started_at: new\n") is False


def test_unknown_process_status_falls_back_to_age(tmp_path, monkeypatch):
    lock = tmp_path / ".lock"
    lock.write_text(_lock_of(_dead_pid()))
    monkeypatch.setattr(locks, "process_status", lambda pid: (None, None))
    assert locks.inspect(lock)["state"] == locks.LIVE
    assert locks.acquire_or_take_stale(lock, "started_at: new\n") is False


def test_old_format_lock_follows_the_age_rule(tmp_path):
    """A `pid: cli` lock from an older Watchdog names no process or computer."""
    lock = tmp_path / ".lock"
    lock.write_text(f"pid: cli\nstarted_at: {_iso(datetime.now(timezone.utc))}\n")
    info = locks.inspect(lock)
    assert info["state"] == locks.LIVE and info["where"] == "unknown"
    assert locks.acquire_or_take_stale(lock, "started_at: new\n") is False
    old = _iso(datetime.now(timezone.utc) - timedelta(seconds=locks.STALE_SECONDS + 60))
    lock.write_text(f"pid: cli\nstarted_at: {old}\n")
    assert locks.inspect(lock)["state"] == locks.STALE
    assert locks.acquire_or_take_stale(lock, "started_at: new\n") is True


def test_unparseable_lock_ages_by_modification_time(tmp_path):
    import os
    import time
    lock = tmp_path / ".lock"
    lock.write_text("pid: cli\n")
    assert locks.inspect(lock)["state"] == locks.LIVE
    past = time.time() - locks.STALE_SECONDS - 60
    os.utime(lock, (past, past))
    assert locks.inspect(lock)["state"] == locks.STALE


def test_heartbeat_keeps_a_new_format_lock_fresh_and_its_holder(tmp_path):
    import time
    lock = tmp_path / ".lock"
    assert locks.acquire_lock(lock, locks.lock_contents("cli"))
    holder = {k: v for k, v in locks._fields(lock.read_text()).items() if k != "started_at"}
    with locks.heartbeat(lock, interval=0.02):
        lock.write_text(lock.read_text().replace("started_at: ", "started_at: 2000"))
        deadline = time.monotonic() + 5
        while (locks.lock_age_seconds(lock) or 1e9) > 5 and time.monotonic() < deadline:
            time.sleep(0.01)
    after = locks._fields(lock.read_text())
    assert locks.lock_age_seconds(lock) < 5
    assert {k: v for k, v in after.items() if k != "started_at"} == holder


def test_heartbeat_and_release_leave_another_holders_lock_alone(tmp_path):
    """A run judged abandoned (on another computer, say) must not re-stamp or delete the lock of
    the run that took over from it."""
    import time
    lock = tmp_path / ".lock"
    assert locks.acquire_lock(lock, locks.lock_contents("cli"))
    theirs = _lock_of(1, machine="0123456789abcdef", host="other-laptop", age=600)
    with locks.heartbeat(lock, interval=0.02):
        lock.write_text(theirs)
        time.sleep(0.1)
    assert lock.read_text() == theirs
    locks.release_lock(lock)
    assert lock.read_text() == theirs
    lock.write_text(locks.lock_contents("cli"))
    locks.release_lock(lock)
    assert not lock.exists()


def test_clear_abandoned_removes_only_an_abandoned_lock(tmp_path):
    lock = tmp_path / ".lock"
    lock.write_text(f"pid: cli\nstarted_at: {_iso(datetime.now(timezone.utc))}\n")
    assert locks.clear_abandoned(lock) is None and lock.exists()
    old = _iso(datetime.now(timezone.utc) - timedelta(seconds=locks.STALE_SECONDS + 60))
    lock.write_text(f"pid: cli\nstarted_at: {old}\n")
    assert locks.clear_abandoned(lock)["state"] == locks.STALE
    assert not lock.exists()
    assert locks.clear_abandoned(lock) is None


def _taker(arg):
    lock_path, i, go = arg
    import time
    while time.time() < go:
        pass
    return locks.acquire_or_take_stale(Path(lock_path), f"pid: cli\nlabel: taker-{i}\n"
                                       f"started_at: {_iso(datetime.now(timezone.utc))}\n")


def test_only_one_of_many_takers_of_an_abandoned_lock_wins(tmp_path):
    """Two processes that both judge a lock abandoned must not both take it: an unlink followed
    by O_EXCL let the second delete the first's new lock. The takeover claim admits one."""
    import time
    lock = tmp_path / ".lock"
    old = _iso(datetime.now(timezone.utc) - timedelta(seconds=locks.STALE_SECONDS + 60))
    n = 8
    for _ in range(5):                  # several rounds: the race window is narrow
        lock.write_text(f"pid: cli\nstarted_at: {old}\n")
        go = time.time() + 0.5
        with multiprocessing.Pool(n) as pool:
            results = pool.map(_taker, [(str(lock), i, go) for i in range(n)])
        winners = [i for i, r in enumerate(results) if r]
        assert len(winners) == 1, winners
        assert f"label: taker-{winners[0]}" in lock.read_text()
        assert not locks._claim_path(lock).exists()


def test_a_claim_left_by_a_crashed_taker_expires(tmp_path):
    import os
    import time
    lock = tmp_path / ".lock"
    old = _iso(datetime.now(timezone.utc) - timedelta(seconds=locks.STALE_SECONDS + 60))
    lock.write_text(f"pid: cli\nstarted_at: {old}\n")
    claim = locks._claim_path(lock)
    claim.write_text("")
    assert locks.acquire_or_take_stale(lock, "started_at: new\n") is False   # a taker is busy
    past = time.time() - 120
    os.utime(claim, (past, past))
    locks.acquire_or_take_stale(lock, "started_at: new\n")   # clears the abandoned claim
    assert locks.acquire_or_take_stale(lock, "started_at: new\n") is True


@pytest.mark.skipif(not __import__("shutil").which("ps"), reason="no ps")
def test_ps_start_time_is_stable_and_differs_between_processes():
    """The macOS path (`ps -o lstart=`), exercised on any Unix that has ps."""
    import os
    a = locks._ps_proc(os.getpid())
    assert a[0] is True and a[1] and a[1] == locks._ps_proc(os.getpid())[1]
    assert locks._ps_proc(_dead_pid())[0] is False


def test_a_taker_that_arrives_after_a_completed_takeover_backs_off(tmp_path, monkeypatch):
    """The interleaving the takeover claim exists for, made deterministic: A judges the lock
    abandoned, B takes it over completely, then A acts. A must find B's lock and back off, not
    delete it (which an unlink-then-O_EXCL takeover did, leaving two runs that both held it)."""
    lock = tmp_path / ".lock"
    old = _iso(datetime.now(timezone.utc) - timedelta(seconds=locks.STALE_SECONDS + 60))
    lock.write_text(f"pid: cli\nstarted_at: {old}\n")
    real = locks.inspect

    def b_overtakes(lock_file, stale_seconds=locks.STALE_SECONDS):
        info = real(lock_file, stale_seconds)
        monkeypatch.setattr(locks, "inspect", real)
        assert locks.acquire_or_take_stale(lock_file, "label: B\n") is True
        return info

    monkeypatch.setattr(locks, "inspect", b_overtakes)
    assert locks.acquire_or_take_stale(lock, "label: A\n") is False
    assert lock.read_text() == "label: B\n"
