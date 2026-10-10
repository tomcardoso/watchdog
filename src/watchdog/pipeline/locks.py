"""
Atomic lock-file primitives shared by ingest, finalize, chew and the contradiction re-check.

Check-then-write — ``if lock.exists(): ...; lock.write_text(...)`` — races: two processes can
both pass the existence check and both proceed, so neither is excluded (#257). ``acquire_lock``
uses ``O_CREAT | O_EXCL``, which is atomic on every platform: exactly one caller creates the
file, and the contents are written to that exclusively-created descriptor so a concurrent reader
never sees an empty lock.

A lock records who holds it (D293): the holder's process id and that process's start time, this
computer's name and an anonymous machine id, a label, and ``started_at``, which a heartbeat
re-stamps while the run works. ``inspect`` reads a lock and says whether its holder is alive:

- **dead** — the lock was written on this computer and its process is gone, or the process id
  now belongs to a process started at another time (the id was reused). Taken over at once.
- **live** — the lock was written on this computer and its process is still running (same
  start time). Never taken over, however old.
- **remote** — written on another computer (a synced folder), so its process can't be checked.
  Taken over once ``started_at`` is ``STALE_SECONDS`` old: several missed heartbeats.
- **stale** — any lock whose holder can't be checked (another computer, a lock written by an
  older Watchdog, a platform that can't report a start time) and whose age has passed
  ``STALE_SECONDS``. Taken over.

Anything unknown falls back to the age rule; nothing is ever called dead on a guess.
"""

import functools
import hashlib
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# How often a held lock's `started_at` is re-stamped while its run is active.
HEARTBEAT_SECONDS = 120
# How old a lock whose holder can't be checked must be before it is taken over: seven missed
# heartbeats, so a synced folder that delivers the beats several minutes late never makes a live
# run on another computer look abandoned (D293).
STALE_SECONDS = 15 * 60
# A takeover claim older than this was left by a taker that crashed mid-takeover.
_CLAIM_STALE_SECONDS = 60

LIVE, DEAD, REMOTE, STALE = "live", "dead", "remote", "stale"
# States a lock can be taken over from without waiting.
TAKEABLE = (DEAD, STALE)


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── this computer and its processes ─────────────────────────────────────────────────────────

def hostname() -> str:
    try:
        return socket.gethostname() or ""
    except OSError:
        return ""


@functools.lru_cache(maxsize=1)
def machine_id() -> str | None:
    """An anonymous, stable id for this computer, or None when the platform gives none.

    A lock's process can only be checked on the computer that wrote it, and a host name alone
    can't prove that: two laptops can share one, and a Mac's can change with the network. The raw
    id is hashed, because the lock sits in the investigation's folder and may be synced."""
    raw = None
    try:
        if sys.platform.startswith("linux"):
            for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
                try:
                    raw = Path(p).read_text(encoding="ascii").strip() or None
                except OSError:
                    continue
                if raw:
                    break
        elif sys.platform == "darwin":
            out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                                 capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    raw = line.split("=", 1)[1].strip().strip('"') or None
                    break
        elif sys.platform == "win32":
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                                0, winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)) as k:
                raw = str(winreg.QueryValueEx(k, "MachineGuid")[0]) or None
    except Exception:  # noqa: BLE001 — any failure means "unknown", which is safe
        raw = None
    if not raw:
        return None
    return hashlib.sha256(f"watchdog-lock:{raw}".encode()).hexdigest()[:16]


def _linux_proc(pid: int) -> tuple[bool | None, str | None]:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
    except FileNotFoundError:
        return False, None
    except OSError:
        return None, None
    try:
        fields = stat[stat.rindex(")") + 2:].split()
        state, ticks = fields[0], fields[19]
    except (ValueError, IndexError):
        return None, None
    if state in ("Z", "X", "x"):          # exited, waiting to be reaped: not running
        return False, None
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError:
        boot = ""
    return True, f"linux:{boot}:{ticks}"


def _posix_alive(pid: int) -> bool | None:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                        # exists, owned by another user
    except OSError:
        return None
    return True


def _ps_proc(pid: int) -> tuple[bool | None, str | None]:
    """macOS and other Unix: liveness from `kill(pid, 0)`, start time from `ps`. Fixed locale and
    time zone, so two processes reading the same start time get the same text."""
    alive = _posix_alive(pid)
    if not alive:
        return alive, None
    try:
        out = subprocess.run(["ps", "-o", "stat=,lstart=", "-p", str(pid)], capture_output=True,
                             text=True, timeout=5, env={**os.environ, "LC_ALL": "C", "TZ": "UTC"})
    except (OSError, subprocess.SubprocessError):
        return True, None
    line = out.stdout.strip()
    if out.returncode != 0 or not line:
        return True, None
    stat, _, start = line.partition(" ")
    if stat.startswith("Z"):
        return False, None
    start = " ".join(start.split())
    return True, (f"ps:{start}" if start else None)


def _windows_proc(pid: int) -> tuple[bool | None, str | None]:
    # Never os.kill on Windows: any signal but the console ones terminates the process.
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        h = k32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            err = ctypes.get_last_error()
            return (False, None) if err == 87 else (None, None)   # 87: no such process
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return None, None
            if code.value != 259:                       # STILL_ACTIVE
                return False, None
            times = [wintypes.FILETIME() for _ in range(4)]
            if not k32.GetProcessTimes(h, *(ctypes.byref(t) for t in times)):
                return True, None
            created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            return True, f"win:{created}"
        finally:
            k32.CloseHandle(h)
    except Exception:  # noqa: BLE001
        return None, None


def process_status(pid: int) -> tuple[bool | None, str | None]:
    """``(alive, start)`` for a process on this computer. ``alive`` is None when it can't be
    told; ``start`` is a platform-tagged start time, None when unavailable."""
    if pid <= 0:
        return None, None
    if sys.platform.startswith("linux") and Path("/proc/self/stat").exists():
        return _linux_proc(pid)
    if sys.platform == "win32":
        return _windows_proc(pid)
    if os.name == "posix":
        return _ps_proc(pid)
    return None, None


@functools.lru_cache(maxsize=1)
def _own_start() -> str | None:
    return process_status(os.getpid())[1]


def lock_contents(label: str = "cli") -> str:
    """The text of a new lock held by this process."""
    lines = [f"pid: {os.getpid()}"]
    start = _own_start()
    if start:
        lines.append(f"pid_start: {start}")
    lines.append(f"host: {hostname()}")
    mid = machine_id()
    if mid:
        lines.append(f"machine: {mid}")
    lines += [f"label: {label}", f"started_at: {_iso_now()}"]
    return "\n".join(lines) + "\n"


# ── reading a lock ──────────────────────────────────────────────────────────────────────────

def _fields(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and key.strip() not in out:
            out[key.strip()] = value.strip()
    return out


def _parse_iso_age(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds()


def lock_started_at(lock_file: Path) -> str | None:
    """Return the ISO timestamp on the lock's ``started_at:`` line, or ``None`` if absent."""
    try:
        return _fields(lock_file.read_text(encoding="utf-8")).get("started_at")
    except OSError:
        return None


def lock_age_seconds(lock_file: Path) -> float | None:
    """Age of the lock derived from its ``started_at`` line, or ``None`` if absent/unparseable."""
    return _parse_iso_age(lock_started_at(lock_file))


def _holder_pid(f: dict) -> int | None:
    try:
        return int(f.get("pid", ""))
    except ValueError:
        return None


def _is_local(f: dict) -> bool:
    mid = machine_id()
    return bool(mid) and f.get("machine") == mid


def inspect(lock_file: Path, stale_seconds: float = STALE_SECONDS) -> dict | None:
    """Who holds ``lock_file`` and whether they are still running, or None when it is free.

    Returns ``{"state", "where", "host", "label", "started_at", "age", "raw"}``. ``where`` is
    ``"here"``, ``"elsewhere"`` (another computer's name is on it) or ``"unknown"`` (an older
    lock that names no computer). ``age`` is from ``started_at``, else from the file's
    modification time, which every write and heartbeat updates."""
    try:
        raw = lock_file.read_text(encoding="utf-8")
        mtime = lock_file.stat().st_mtime
    except FileNotFoundError:
        return None
    except OSError:
        raw, mtime = "", time.time()
    f = _fields(raw)
    age = _parse_iso_age(f.get("started_at"))
    if age is None:
        age = max(0.0, time.time() - mtime)
    host = f.get("host") or None
    local = _is_local(f)
    where = "here" if local else ("elsewhere" if f.get("machine") or host else "unknown")
    info = {"where": where, "host": host, "label": f.get("label") or f.get("pid"),
            "started_at": f.get("started_at"), "age": age, "raw": raw}
    pid = _holder_pid(f)
    if local and pid is not None:
        alive, start = process_status(pid)
        recorded = f.get("pid_start")
        if alive is False or (alive and start and recorded and start != recorded):
            return {**info, "state": DEAD}
        if alive and start and recorded and start == recorded:
            return {**info, "state": LIVE}
        # Running but its start time can't be compared, or unknown: the age rule decides.
    if age >= stale_seconds:
        return {**info, "state": STALE}
    return {**info, "state": LIVE if where == "here" else REMOTE if where == "elsewhere" else LIVE}


def held(lock_file: Path, stale_seconds: float = STALE_SECONDS) -> dict | None:
    """The lock's holder when a run really holds it (live here, or fresh on another computer);
    None when it is free or abandoned."""
    info = inspect(lock_file, stale_seconds)
    return info if info and info["state"] not in TAKEABLE else None


# ── taking and releasing ────────────────────────────────────────────────────────────────────

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
    # A unique temp file per call, not per process: `dig --wait` refreshes the lock on the main
    # thread while the heartbeat thread stamps it, and a shared name let one writer's replace
    # move the other's file away mid-write.
    fd, tmp = tempfile.mkstemp(prefix=f".{lock_file.name}.", suffix=".tmp", dir=lock_file.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, lock_file)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _claim_path(lock_file: Path) -> Path:
    return lock_file.parent / f".{lock_file.name}.takeover"


@contextmanager
def _claim(lock_file: Path):
    """Yield True to the one process allowed to replace an abandoned lock right now.

    Two takers that both judged the same lock abandoned must not both win: an unlink followed by
    an ``O_EXCL`` create lets the second taker delete the first one's new lock. The claim file is
    itself created with ``O_EXCL``; whoever creates it re-reads the lock before replacing it, so a
    taker that arrives after a completed takeover sees the new holder and backs off."""
    claim = _claim_path(lock_file)
    try:
        fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        try:
            abandoned = time.time() - claim.stat().st_mtime > _CLAIM_STALE_SECONDS
        except OSError:
            abandoned = False
        if abandoned:
            claim.unlink(missing_ok=True)   # the next attempt can claim it
        yield False
        return
    os.close(fd)
    try:
        yield True
    finally:
        claim.unlink(missing_ok=True)


def acquire_or_take_stale(lock_file: Path, contents: str,
                          stale_seconds: float = STALE_SECONDS) -> bool:
    """Acquire the lock, taking over one whose holder is dead or whose age has passed
    ``stale_seconds`` when its holder can't be checked (see ``inspect``).

    Returns ``True`` on success, ``False`` while a live run (here or on another computer) holds
    it, or while another process is taking it over at this moment."""
    if acquire_lock(lock_file, contents):
        return True
    info = inspect(lock_file, stale_seconds)
    if info is None:
        return acquire_lock(lock_file, contents)      # released between the two calls
    if info["state"] not in TAKEABLE:
        return False
    with _claim(lock_file) as mine:
        if not mine:
            return False
        try:
            now = lock_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return acquire_lock(lock_file, contents)
        if now != info["raw"]:
            return False            # someone else took it over or the holder beat: not ours
        _write_atomic(lock_file, contents)
        return True


def clear_abandoned(lock_file: Path, stale_seconds: float = STALE_SECONDS) -> dict | None:
    """Remove ``lock_file`` if its holder is dead or its age has passed the window; return what
    was removed (``inspect``'s dict), else None. The same rule a run applies when it takes a
    lock over, for a reader that only wants the vault's true state (the app's status calls)."""
    info = inspect(lock_file, stale_seconds)
    if info is None or info["state"] not in TAKEABLE:
        return None
    with _claim(lock_file) as mine:
        if not mine:
            return None
        try:
            if lock_file.read_text(encoding="utf-8") != info["raw"]:
                return None
        except FileNotFoundError:
            return None
        lock_file.unlink(missing_ok=True)
        return info


def _belongs_to_another(text: str) -> bool:
    """True when the lock names a holder other than this process. A lock that names no process
    on this computer (an older format) is treated as ours, as before."""
    f = _fields(text)
    pid = _holder_pid(f)
    if pid is None or not f.get("machine"):
        return False
    return f.get("machine") != machine_id() or pid != os.getpid()


def release_lock(lock_file: Path) -> None:
    """Remove a lock this process holds. A lock another process has since taken over (this run
    was judged abandoned, for instance on another computer whose clock or sync lagged) is left
    alone, so the run that holds it now keeps it."""
    try:
        text = lock_file.read_text(encoding="utf-8")
    except OSError:
        return
    if _belongs_to_another(text):
        return
    lock_file.unlink(missing_ok=True)


def _stamp(lock_file: Path) -> bool:
    """Rewrite just the `started_at:` line of an existing lock to now, keeping its other lines.
    Returns False — writing nothing — when the lock is gone or another process now holds it, so a
    beat racing a release never recreates a lock its holder just dropped."""
    try:
        text = lock_file.read_text(encoding="utf-8")
    except OSError:
        return False
    if _belongs_to_another(text):
        return False
    kept = [line for line in text.splitlines() if not line.startswith("started_at:")]
    try:
        _write_atomic(lock_file, "\n".join([*kept, f"started_at: {_iso_now()}"]) + "\n")
    except OSError:
        return False
    return True


def refresh_lock(lock_file: Path) -> None:
    """Rewrite an already-held lock's ``started_at`` to now, keeping who holds it.

    For a long-lived holder (e.g. `watchdog dig --wait` sleeping through a rate limit) so
    the age rule never mistakes a live-but-sleeping run for an abandoned one."""
    _stamp(lock_file)


@contextmanager
def heartbeat(lock_file: Path, interval: float = HEARTBEAT_SECONDS):
    """Keep a held lock fresh for as long as the block runs (#696).

    A daemon thread re-stamps `started_at` every `interval` seconds and is stopped and joined
    before the block exits, so the caller's release always happens after the last beat. On this
    computer a crashed run's lock is recognised at once by its process (D293); the beat is what
    another computer, which can't see that process, goes by."""
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
