"""
Atomic lock-file primitives shared by ingest, finalize, and chew.

Check-then-write — ``if lock.exists(): ...; lock.write_text(...)`` — races: two processes can
both pass the existence check and both proceed, so neither is excluded (#257). ``acquire_lock``
uses ``O_CREAT | O_EXCL``, which is atomic on every platform: exactly one caller creates the
file, and the contents are written to that exclusively-created descriptor so a concurrent reader
never sees an empty lock. Callers own the staleness policy on the failure branch via
``acquire_or_take_stale``.
"""

import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# How often a held lock's `started_at` is refreshed while its run is active — well inside the
# 30-minute staleness window (`ingest_setup.STALE_SECONDS`), so a missed beat or two never lets a
# live run look abandoned.
HEARTBEAT_SECONDS = 300


def acquire_lock(lock_file: Path, contents: str) -> bool:
    """Atomically create ``lock_file`` holding ``contents``.

    Returns ``True`` if we now hold the lock, ``False`` if it already existed.
    """
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(contents)
    return True


def _write_atomic(lock_file: Path, text: str) -> None:
    """Replace the lock's contents in one step. A plain truncate-and-write leaves a window where
    a concurrent reader (another invocation checking staleness) sees an empty file and reads the
    lock's age as unknown."""
    tmp = lock_file.with_name(f"{lock_file.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, lock_file)


def refresh_lock(lock_file: Path) -> None:
    """Rewrite an already-held lock's ``started_at`` to now.

    For a long-lived holder (e.g. `watchdog dig --wait` sleeping through a rate limit) so
    the staleness heuristic never mistakes a live-but-sleeping run for an abandoned one.
    """
    _write_atomic(
        lock_file,
        f"pid: cli\nstarted_at: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n")


def lock_started_at(lock_file: Path) -> str | None:
    """Return the ISO timestamp on the lock's ``started_at:`` line, or ``None`` if absent."""
    try:
        for line in lock_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("started_at:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def lock_age_seconds(lock_file: Path) -> float | None:
    """Age of the lock derived from its ``started_at`` line, or ``None`` if absent/unparseable."""
    ts = lock_started_at(lock_file)
    if ts is None:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds()


def acquire_or_take_stale(lock_file: Path, contents: str, stale_seconds: float) -> bool:
    """Acquire the lock, taking over one that is *provably* older than ``stale_seconds``.

    Returns ``True`` on success. Returns ``False`` when a live lock is held **or** the existing
    lock's age can't be determined (missing/unparseable ``started_at``): the conservative choice
    is to refuse and let the user run ``watchdog unlock``, rather than delete a lock of unknown
    age — the check-then-unlink code this replaces deleted such a lock regardless of age.
    """
    if acquire_lock(lock_file, contents):
        return True
    age = lock_age_seconds(lock_file)
    if age is None or age < stale_seconds:
        return False   # live, or unknown age → refuse
    # Provably stale — take it over. The unlink-then-reacquire window is a tolerable residual for
    # a lock already older than stale_seconds; the common two-fresh-invocation race is fully
    # closed by the O_EXCL create above.
    lock_file.unlink(missing_ok=True)
    return acquire_lock(lock_file, contents)


def _stamp(lock_file: Path) -> bool:
    """Rewrite just the `started_at:` line of an existing lock to now, keeping its other lines
    (the holder's pid label). Returns False — writing nothing — when the lock is gone, so a beat
    racing a release never recreates a lock its holder just dropped."""
    try:
        lines = lock_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    kept = [line for line in lines if not line.startswith("started_at:")]
    try:
        _write_atomic(lock_file, "\n".join([*kept, f"started_at: {now}"]) + "\n")
    except OSError:
        return False
    return True


@contextmanager
def heartbeat(lock_file: Path, interval: float = HEARTBEAT_SECONDS):
    """Keep a held lock fresh for as long as the block runs (#696).

    Staleness was only ever refreshed while sleeping through a rate limit (`refresh_lock`), so a
    run that was simply long — hours of extraction or a finalize over thousands of documents —
    crossed the 30-minute window mid-work, and a second invocation could take the lock over while
    the first was still writing. A daemon thread re-stamps `started_at` every `interval` seconds
    and is stopped and joined before the block exits, so the caller's release always happens after
    the last beat. A run that dies without exiting the block stops beating with it, so a crashed
    run's lock still goes stale on schedule."""
    stop = threading.Event()

    def _beat() -> None:
        while not stop.wait(interval):
            if not _stamp(lock_file):
                return

    thread = threading.Thread(target=_beat, name=f"lock-heartbeat:{lock_file.name}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()

