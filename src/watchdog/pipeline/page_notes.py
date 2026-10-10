"""The reporter's `## Notes` on saved pages (`queries/`, `wiki/`) survive a session's rewrite (D296).

An entity or document note's Notes section is protected by the registry lock the commit pass
holds. Saved pages are written by Ask Claude sessions, which take no lock, so an app save and a
session's Write could interleave: the session reads the page, the reporter saves a note in the
app, and the session writes the page back with the old Notes. Two halves close that:

- **The app's save is a compare-and-swap** (`app_save`): under this module's lock it reads the
  page, merges only the Notes body into it, re-reads, and writes atomically only if the page is
  unchanged; otherwise it starts again. It records the saved Notes and the time in a small ledger
  (`.watchdog/page-notes.json`).
- **A session's file edits are bracketed by the vault's hooks** (`watchdog page-notes pre|post`,
  installed in `.claude/settings.json` for Write, Edit and MultiEdit). Before the edit, the page's
  Notes as they stand on disk are put aside; after it, the Notes are put back to the reporter's
  latest: the app's saved Notes if saved since the snapshot, else the snapshot. A session never
  writes the Notes section (the vault's instructions say so), so putting it back only ever undoes
  a stale copy, and Claude is told it happened.

Both halves run under one lock, so whichever comes second sees the other's write. What remains
is a session write landing in the instant between the app's re-read and its replace, which loses
that session write (not the Notes); version history (D286) still holds it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

PAGE_DIRS = ("queries", "wiki")
_LEDGER = "page-notes.json"
_PENDING = "page-notes-pending"
_LOCK_NAME = ".notes-lock"
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_HEADING = re.compile(r"^##[ \t]+Notes[ \t]*$")
_RETRIES = 5
_PENDING_MAX_AGE = 24 * 3600

RESTORED = ("Watchdog put back the reporter's ## Notes section on {page}: that section is theirs, "
            "and the copy written had dropped or changed what they last saved. Leave ## Notes as "
            "it is; if the user asked you to change it, tell them to edit their notes in the app.")


# ── the Notes section ───────────────────────────────────────────────────────────────────────

def _heading_index(lines: list[str]) -> int | None:
    in_fence = False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
        if not in_fence and _HEADING.match(line):
            return i
    return None


def section(text: str) -> str | None:
    """Everything after the page's `## Notes` heading (the section runs to the end of the file,
    as the pipeline and the app treat it), or None when it has none."""
    lines = (text or "").split("\n")
    idx = _heading_index(lines)
    return None if idx is None else "\n".join(lines[idx + 1:])


def _same(a: str | None, b: str | None) -> bool:
    return (a or "").strip() == (b or "").strip()


def with_section(text: str, body: str) -> str:
    """`text` with its Notes section's contents replaced by `body` (appended when it has none)."""
    lines = (text or "").split("\n")
    idx = _heading_index(lines)
    head = "\n".join(lines[:idx]) if idx is not None else (text or "")
    return f"{head.rstrip(chr(10))}\n\n## Notes\n\n{body.strip(chr(10))}\n"


# ── storage ─────────────────────────────────────────────────────────────────────────────────

def _state(vault: Path) -> Path:
    return Path(vault) / ".watchdog"


@contextmanager
def lock(vault: Path):
    """The lock an app save and a session hook take around a saved page's Notes. Separate from
    the registry lock, so neither waits on a run's commit pass."""
    from watchdog.pipeline.write_vault import _registry_lock
    reg = _state(vault) / "registry"
    reg.mkdir(parents=True, exist_ok=True)
    with _registry_lock(reg, _LOCK_NAME):
        yield


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        try:
            os.chmod(tmp, path.stat().st_mode & 0o777)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def saved(vault: Path, rel: str) -> dict | None:
    """The reporter's last app-saved Notes for `rel`: `{"notes", "at"}`, or None."""
    entry = _read_json(_state(vault) / _LEDGER).get(rel)
    return entry if isinstance(entry, dict) and isinstance(entry.get("notes"), str) else None


def _record(vault: Path, rel: str, notes: str) -> None:
    path = _state(vault) / _LEDGER
    data = _read_json(path)
    data[rel] = {"notes": notes, "at": time.time()}
    _write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# ── the app's save ──────────────────────────────────────────────────────────────────────────

def is_page(rel: str) -> bool:
    parts = rel.split("/")
    return len(parts) >= 2 and parts[0] in PAGE_DIRS and rel.lower().endswith(".md")


def app_save(vault: Path, rel: str, file: Path, merge) -> bool:
    """Save the reporter's Notes on saved page `rel` with a compare-and-swap: `merge(old_text)`
    returns the page with the new Notes body; the result is written only if the page has not
    changed since it was read, else the merge starts again from the new text. Returns True
    when the file changed. Raises RuntimeError if the page kept changing (`_RETRIES` attempts)."""
    with lock(vault):
        for _ in range(_RETRIES):
            old = _read(file)
            if old is None:
                raise FileNotFoundError(rel)
            new = merge(old)
            if _read(file) != old:
                continue
            if new != old:
                _write_atomic(file, new)
            _record(vault, rel, section(new) or "")
            return new != old
    raise RuntimeError("The page kept changing while the notes were being saved.")


# ── the session hooks ───────────────────────────────────────────────────────────────────────

def _target(vault: Path, payload: dict) -> tuple[str, Path] | None:
    """The saved page a hook payload's edit names, as (vault-relative path, file), or None."""
    tool_input = payload.get("tool_input") or {}
    raw = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(raw, str) or not raw:
        return None
    root = Path(os.path.realpath(vault))
    target = Path(raw) if Path(raw).is_absolute() else root / raw
    real = Path(os.path.realpath(target))
    try:
        rel = real.relative_to(root).as_posix()
    except ValueError:
        return None
    return (rel, real) if is_page(rel) else None


def _pending_path(vault: Path, payload: dict, rel: str) -> Path:
    key = hashlib.sha1(f"{payload.get('session_id') or ''}|{rel}".encode("utf-8")).hexdigest()[:20]
    return _state(vault) / _PENDING / f"{key}.json"


def _prune(folder: Path) -> None:
    cutoff = time.time() - _PENDING_MAX_AGE
    for p in folder.glob("*.json") if folder.is_dir() else []:
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


def before_edit(vault: Path, payload: dict) -> None:
    """PreToolUse: put aside the page's Notes as they stand now."""
    hit = _target(vault, payload)
    if hit is None:
        return
    rel, file = hit
    with lock(vault):
        text = _read(file)
        path = _pending_path(vault, payload, rel)
        _prune(path.parent)
        _write_atomic(path, json.dumps({"rel": rel, "at": time.time(),
                                        "notes": section(text) if text is not None else None}))


def after_edit(vault: Path, payload: dict) -> str | None:
    """PostToolUse: put the reporter's latest Notes back if the edit dropped or changed them.
    Returns the message for Claude when it did, else None."""
    hit = _target(vault, payload)
    if hit is None:
        return None
    rel, file = hit
    with lock(vault):
        path = _pending_path(vault, payload, rel)
        pending = _read_json(path)
        path.unlink(missing_ok=True)
        if not pending:
            return None          # no snapshot (the hook before the edit didn't run): change nothing
        target = pending.get("notes")
        app = saved(vault, rel)
        if app is not None and float(app.get("at") or 0) >= float(pending.get("at") or 0):
            target = app["notes"]
        if target is None:
            return None          # a new page, or one with no Notes before: nothing of theirs to keep
        text = _read(file)
        if text is None or _same(section(text), target):
            return None
        _write_atomic(file, with_section(text, target))
    return RESTORED.format(page=rel)


def run_hook(stage: str, stdin_text: str) -> str | None:
    """`watchdog page-notes pre|post`: read the hook payload, act, and return what to print.
    Never raises: a hook that fails must not fail the session's edit."""
    try:
        payload = json.loads(stdin_text or "{}")
        if not isinstance(payload, dict):
            return None
        vault = Path(payload.get("cwd") or ".").resolve()
        if not (vault / ".watchdog").is_dir():
            return None
        if stage == "pre":
            before_edit(vault, payload)
            return None
        message = after_edit(vault, payload)
    except Exception:  # noqa: BLE001
        return None
    if not message:
        return None
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                              "additionalContext": message}})
