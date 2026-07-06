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
from datetime import datetime, timezone
from pathlib import Path


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


def refresh_lock(lock_file: Path) -> None:
    """Rewrite an already-held lock's ``started_at`` to now.

    For a long-lived holder (e.g. `watchdog ingest --wait` sleeping through a rate limit) so
    the staleness heuristic never mistakes a live-but-sleeping run for an abandoned one.
    """
    lock_file.write_text(
        f"pid: cli\nstarted_at: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n",
        encoding="utf-8")


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
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
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
