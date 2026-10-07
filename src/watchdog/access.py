"""Folder access: Watchdog may change files only in folders the user has allowed.

The desktop app keeps a list of folders the user granted — by choosing them in a folder dialog or
by approving them in a prompt — in `~/.watchdog/access.json`. The app's main process is the only
writer of that file. Python only reads it.

When the app runs Watchdog it sets `WATCHDOG_ENFORCE_ACCESS=1`, and `install()` (called when the
package is imported, see `watchdog/__init__.py`) adds an audit hook that refuses any write, rename,
delete or database connection that would change a file outside the granted folders. Audit hooks see
every file operation in the interpreter, so the rule covers the whole pipeline — every write site,
every library it calls — and the variable carries it into child processes, including the `watchdog`
commands a Claude session runs. Without the variable (the terminal CLI, the test suite) nothing is
enforced.

Some places are always writable, because Watchdog or its libraries need them and they hold no one's
documents: Watchdog's own settings folder (except the access list itself), the system temporary
folder, the Python environment, and model caches. Outside the user's home folder and removable
volumes, the operating system's own permissions apply unchanged.

This guards against Watchdog's own mistakes and against a document that tries to steer a Claude
session into writing elsewhere. It is not an operating-system sandbox: code that doesn't import
`watchdog` is not covered.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path

ENFORCE_ENV = "WATCHDOG_ENFORCE_ACCESS"
ACCESS_FILE_ENV = "WATCHDOG_ACCESS_FILE"
EXTRA_ROOTS_ENV = "WATCHDOG_EXTRA_WRITE_ROOTS"

_installed = False
_local = threading.local()


def access_file() -> Path:
    override = os.environ.get(ACCESS_FILE_ENV)
    return Path(override) if override else Path.home() / ".watchdog" / "access.json"


def enforced() -> bool:
    return os.environ.get(ENFORCE_ENV) == "1"


def _resolve(p) -> Path:
    """Absolute, symlink-resolved path, even when the file doesn't exist yet."""
    if isinstance(p, bytes):
        p = os.fsdecode(p)
    return Path(os.path.realpath(os.path.abspath(os.fspath(p))))


def _within(p: Path, root: Path) -> bool:
    return p == root or root in p.parents


def granted_folders() -> list[Path]:
    """The folders the user has allowed, resolved. A missing or unreadable list grants nothing."""
    try:
        data = json.loads(access_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for entry in data.get("folders", []) if isinstance(data, dict) else []:
        path = entry.get("path") if isinstance(entry, dict) else None
        if isinstance(path, str) and path:
            out.append(_resolve(path))
    return out


def is_granted(path) -> bool:
    p = _resolve(path)
    return any(_within(p, root) for root in granted_folders())


def _protected_roots() -> list[Path]:
    """Where people keep files: the home folder and removable or network volumes."""
    roots = [Path.home()]
    if os.name == "nt":
        system = (os.environ.get("SystemDrive") or "C:").upper()
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            drive = Path(f"{letter}:\\")
            if f"{letter}:" != system and drive.exists():
                roots.append(drive)
    else:
        roots += [Path(p) for p in ("/Volumes", "/media", "/mnt", "/run/media") if Path(p).exists()]
    return [_resolve(r) for r in roots]


def _exempt_roots() -> list[Path]:
    """Always writable: Watchdog's settings folder, temporary files, the Python environment and
    model caches."""
    home = Path.home()
    candidates = [
        home / ".watchdog",
        tempfile.gettempdir(),
        sys.prefix, sys.base_prefix, sys.exec_prefix,
        os.environ.get("XDG_CACHE_HOME") or home / ".cache",
        home / "Library" / "Caches",
        home / ".EasyOCR",
        os.environ.get("LOCALAPPDATA"),
    ]
    for var in ("HF_HOME", "TORCH_HOME", "FASTEMBED_CACHE_PATH", "UV_CACHE_DIR", "TMPDIR",
                "PLAYWRIGHT_BROWSERS_PATH"):
        candidates.append(os.environ.get(var))
    candidates += [p for p in os.environ.get(EXTRA_ROOTS_ENV, "").split(os.pathsep)]
    return [_resolve(c) for c in candidates if c]


def write_allowed(path) -> bool:
    p = _resolve(path)
    if p == _resolve(access_file()):
        return False  # only the app's main process changes the list of granted folders
    if any(_within(p, root) for root in _exempt_roots()):
        return True
    if any(_within(p, root) for root in granted_folders()):
        return True
    return not any(_within(p, root) for root in _protected_roots())


class AccessDenied(PermissionError):
    pass


def _deny(path) -> None:
    raise AccessDenied(
        f"Watchdog does not have permission to change {_resolve(path)}. "
        "Allow access to that folder in the Watchdog app (Settings → Folder access), then try again.")


def check(path) -> None:
    """Raise `AccessDenied` when enforcement is on and `path` may not be changed."""
    if enforced() and not write_allowed(path):
        _deny(path)


_WRITE_FLAGS = (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC)

# Audit events that change the file system, mapped to the argument positions holding paths.
_PATH_EVENTS = {
    "os.remove": (0,), "os.rmdir": (0,), "os.mkdir": (0,), "os.truncate": (0,),
    "os.chmod": (0,), "os.chown": (0,), "os.utime": (0,),
    "os.rename": (0, 1), "os.replace": (0, 1), "os.link": (1,), "os.symlink": (1,),
    "shutil.rmtree": (0,), "shutil.copyfile": (1,), "shutil.copytree": (1,),
    "shutil.move": (0, 1), "shutil.copymode": (1,), "shutil.copystat": (1,),
}


def _is_path(v) -> bool:
    return isinstance(v, (str, bytes, os.PathLike))


def _hook(event: str, args) -> None:
    if getattr(_local, "busy", False):
        return
    if event == "open":
        path, mode, flags = (tuple(args) + (None, None, None))[:3]
        if not _is_path(path):
            return
        writes = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            mode is None and isinstance(flags, int) and flags & _WRITE_FLAGS)
        if not writes:
            return
        paths = [path]
    elif event == "sqlite3.connect":
        db = args[0] if args else None
        if not _is_path(db) or os.fspath(db) in (":memory:", b":memory:", ""):
            return
        paths = [db]
    elif event in _PATH_EVENTS:
        paths = [args[i] for i in _PATH_EVENTS[event] if i < len(args) and _is_path(args[i])]
    else:
        return
    _local.busy = True
    try:
        for p in paths:
            if not write_allowed(p):
                _deny(p)
    finally:
        _local.busy = False


def install() -> None:
    """Add the enforcement hook when the app asked for it. Audit hooks can't be removed, so this
    is a one-way switch for the life of the process."""
    global _installed
    if _installed or not enforced():
        return
    sys.addaudithook(_hook)
    _installed = True
