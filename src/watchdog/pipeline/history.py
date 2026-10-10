"""Version history for every file Watchdog generates or the app edits (D286).

A purpose-built store under ``.watchdog/history/`` rather than git: the vault is not a repository,
a reporter never sees a commit, and the only operations needed are "record what changed, and why",
"show one file's versions" and "put an old version back".

Layout::

    .watchdog/history/
      objects/ab/cdef…       one zlib-compressed blob per distinct file content (SHA-256 of the
                             raw bytes), so an unchanged file costs nothing and a file that
                             returns to an earlier content reuses its blob
      log.jsonl              one line per version: {schema_version, version, at, cause,
                             changes: [[path, blob or null (deleted)], …]}, appended to by every
                             snapshot and rewritten only when the reporter removes a version
                             (`remove`), which may add `removed` (how many of the version's
                             changes were taken out) and `gaps` ({path: how many of that path's
                             versions just before this one were removed})
      index.json             derived from the log, rebuilt from it when missing or behind: per
                             path its [version, blob] list, its gaps and the size and mtime it
                             was last seen with (so a snapshot re-reads only files whose stat
                             changed), and per version its time, cause and change count
      size.json              the objects' total size as of a log length, so the projects list
                             can show a history's size without walking it
      .lock                  serializes snapshots and removals across processes and threads

What is tracked (`tracked`): Markdown files outside ``morgue/``, ``incoming/``, ``context/`` and
hidden folders, plus the registry files that hold the investigation's data (`REGISTRY_FILES`).
Original documents, staging, extractions, search indexes, caches and telemetry are not.

When: every operation that writes the vault wraps itself in `recording(vault, cause)` under its own
lock. Entering records anything changed since the last version as "changed since the last
version" (an Obsidian edit, a Claude session in a terminal, a command that records no history), so
an operation never overwrites a version history did not have; leaving records what the operation
changed with its cause. Recordings nest: a merge or a notes rebuild inside a processing run belongs
to the run's one version (I7). History never fails the operation it records: an error is reported
on stderr and the operation goes on.
"""

from __future__ import annotations

import contextvars
import datetime
import difflib
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import zlib
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1

HISTORY_DIR = (".watchdog", "history")
REGISTRY_DIR = ".watchdog/registry/"
REGISTRY_FILES = ("entities.json", "documents.json", "merges.json", "verification.json",
                  "resolutions.json", "requests.json", "relationships.json")
# Top-level folders whose files are never versioned: the originals and their full text, the drop
# zone, background material (and their pre-D266 names). Hidden folders are skipped everywhere.
EXCLUDED_TOP = frozenset({"morgue", "incoming", "context", "_INCOMING", "_CONTEXT"})
MAX_FILE_BYTES = 32 * 1024 * 1024
# A file whose mtime is this close to the snapshot is re-read next time even if its stat matches:
# a second write within the filesystem's timestamp resolution would otherwise go unseen.
_RACY_SECONDS = 2.0

# Reporter-written pages are restored whole; entity and document notes are views rebuilt from data
# at every run, so only their Notes section (the reporter's own words) can be put back; everything
# else is generated from the registries and can be viewed or copied, not restored (D286).
_PAGE_FILES = frozenset({"context.md", "watchlist.md"})
_PAGE_DIRS = ("queries/", "wiki/", "briefings/")
_NOTE_DIRS = ("entities/", "documents/")

# The vaults whose operation is being recorded in this context (a thread, or a task and the
# threads it hands work to with `asyncio.to_thread`, which copy the context).
_ACTIVE: contextvars.ContextVar[frozenset] = contextvars.ContextVar("history_active",
                                                                    default=frozenset())
_thread_lock = threading.Lock()


class HistoryTooNew(Exception):
    """The store was written by a newer Watchdog; it is read-only to this one."""


class HistoryBusy(Exception):
    """A processing run holds the vault; the history cannot be changed by hand meanwhile."""


# ── paths ─────────────────────────────────────────────────────────────────────────────

def history_dir(vault: Path) -> Path:
    return Path(vault).joinpath(*HISTORY_DIR)


def tracked(rel: str) -> bool:
    """Whether a vault-relative POSIX path is versioned."""
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        return False
    if rel.startswith(REGISTRY_DIR):
        return rel[len(REGISTRY_DIR):] in REGISTRY_FILES
    parts = rel.split("/")
    if parts[0] in EXCLUDED_TOP or any(p.startswith(".") for p in parts):
        return False
    return rel.lower().endswith(".md")


def restore_kind(rel: str) -> str:
    """What "Restore this version" means for this path: `page` (the whole file), `notes` (the
    note's Notes section only) or `none` (view and copy only)."""
    if rel in _PAGE_FILES or rel.startswith(_PAGE_DIRS):
        return "page"
    if rel.startswith(_NOTE_DIRS):
        return "notes"
    return "none"


def _in_scope(rel: str, scope) -> bool:
    if scope is None:
        return True
    return any(rel == s or (s.endswith("/") and rel.startswith(s)) for s in scope)


def _scan(vault: Path, scope) -> dict[str, tuple[int, int]]:
    """{rel: (size, mtime_ns)} for every tracked file in `scope` (None = the whole vault)."""
    out: dict[str, tuple[int, int]] = {}

    def add(path: str, rel: str) -> None:
        try:
            st = os.stat(path)
        except OSError:
            return
        if st.st_size <= MAX_FILE_BYTES:
            out[rel] = (st.st_size, st.st_mtime_ns)

    def walk(directory: str, prefix: str) -> None:
        try:
            entries = list(os.scandir(directory))
        except OSError:
            return
        for e in entries:
            rel = prefix + e.name
            if e.name.startswith("."):
                continue
            try:
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if not prefix and e.name in EXCLUDED_TOP:
                    continue
                d = rel + "/"
                if scope is None or any(s.startswith(d) or (s.endswith("/") and d.startswith(s))
                                        for s in scope):
                    walk(e.path, d)
            elif e.name.lower().endswith(".md") and _in_scope(rel, scope) and tracked(rel):
                add(e.path, rel)

    root = str(vault)
    walk(root, "")
    reg = os.path.join(root, ".watchdog", "registry")
    for name in REGISTRY_FILES:
        rel = REGISTRY_DIR + name
        if _in_scope(rel, scope) and os.path.isfile(os.path.join(reg, name)):
            add(os.path.join(reg, name), rel)
    return out


# ── the store ─────────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _blob_path(hdir: Path, blob: str) -> Path:
    return hdir / "objects" / blob[:2] / blob[2:]


def _put_blob(hdir: Path, data: bytes) -> int:
    """Store `data`'s blob if it is not stored yet; returns the bytes written (0 when it was)."""
    blob = hashlib.sha256(data).hexdigest()
    path = _blob_path(hdir, blob)
    if path.exists():
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    packed = zlib.compress(data, 6)
    fd, tmp = tempfile.mkstemp(prefix=".blob.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(packed)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return len(packed)


def read_blob(vault: Path, blob: str) -> bytes:
    if not re.fullmatch(r"[0-9a-f]{64}", blob or ""):
        raise ValueError("Not a version id.")
    return zlib.decompress(_blob_path(history_dir(vault), blob).read_bytes())


def _write_json_atomic(path: Path, data) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ── size (shown in the projects list, so it must not walk the store) ─────────────────

def _walk_bytes(directory: Path) -> int:
    total = 0
    for root, _dirs, names in os.walk(directory):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(root, n))
            except OSError:
                pass
    return total


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _update_size_cache(hdir: Path, prev_log_bytes: int | None, added: int) -> None:
    """Record the objects' total size for the log's current length (under the store lock).
    `added` bytes were written since the log was `prev_log_bytes` long; without a cache for that
    length (or with `prev_log_bytes` None) the objects folder is walked once."""
    path = hdir / "size.json"
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = None
    if (prev_log_bytes is not None and isinstance(cache, dict)
            and cache.get("log_bytes") == prev_log_bytes
            and isinstance(cache.get("objects_bytes"), int)):
        objects = cache["objects_bytes"] + added
    else:
        objects = _walk_bytes(hdir / "objects")
    try:
        _write_json_atomic(path, {"objects_bytes": objects,
                                  "log_bytes": _file_size(hdir / "log.jsonl")})
    except OSError:
        path.unlink(missing_ok=True)


def history_bytes(vault: Path) -> int | None:
    """The history's size on disk, or None when the investigation has none. Cheap: three stats
    and a small file read, falling back to walking the store when its size cache is behind (a
    history written before the cache existed, or after a crash between the log and the cache)."""
    hdir = history_dir(vault)
    log = hdir / "log.jsonl"
    try:
        log_bytes = log.stat().st_size
    except OSError:
        return None
    try:
        cache = json.loads((hdir / "size.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = None
    if (isinstance(cache, dict) and cache.get("log_bytes") == log_bytes
            and isinstance(cache.get("objects_bytes"), int)):
        return (cache["objects_bytes"] + log_bytes + _file_size(hdir / "index.json")
                + _file_size(hdir / "size.json"))
    return _walk_bytes(hdir)


@contextmanager
def _store_lock(hdir: Path):
    from watchdog.pipeline.write_vault import _registry_lock
    hdir.mkdir(parents=True, exist_ok=True)
    with _thread_lock, _registry_lock(hdir, ".lock"):
        yield


def _empty_index() -> dict:
    return {"schema_version": SCHEMA_VERSION, "last": 0, "log_bytes": 0, "versions": {},
            "files": {}}


def _repair_log_tail(log: Path) -> None:
    """Cut a half-written last line (a crash mid-append) so the next append starts clean."""
    try:
        size = log.stat().st_size
    except OSError:
        return
    if not size:
        return
    with open(log, "rb+") as f:
        f.seek(max(0, size - 1))
        if f.read(1) == b"\n":
            return
        f.seek(0)
        data = f.read()
        f.truncate(data.rfind(b"\n") + 1)


def _changes(rec: dict) -> list[tuple[str, str | None]]:
    return [(c[0], c[1]) for c in rec.get("changes") or [] if isinstance(c, list) and len(c) >= 2]


def _apply(index: dict, rec: dict, offset: int) -> None:
    v = int(rec["version"])
    changes = _changes(rec)
    index["versions"][str(v)] = {"at": rec.get("at"), "cause": rec.get("cause") or {},
                                 "n": len(changes), "off": offset,
                                 "removed": int(rec.get("removed") or 0)}
    gaps = rec.get("gaps") if isinstance(rec.get("gaps"), dict) else {}
    for path, blob in changes:
        entry = index["files"].setdefault(path, {"h": [], "st": None})
        entry["h"].append([v, blob])
        entry["st"] = None
        if gaps.get(path):
            entry.setdefault("g", {})[str(v)] = int(gaps[path])
    index["last"] = max(index["last"], v)


def _rebuild_index(hdir: Path) -> dict:
    index = _empty_index()
    log = hdir / "log.jsonl"
    if not log.exists():
        return index
    offset = 0
    with open(log, "rb") as f:
        for raw in f:
            if raw.endswith(b"\n"):
                try:
                    rec = json.loads(raw)
                except ValueError:
                    rec = None
                if isinstance(rec, dict) and "version" in rec:
                    if int(rec.get("schema_version") or 1) > SCHEMA_VERSION:
                        index["too_new"] = True
                    _apply(index, rec, offset)
            offset += len(raw)
    index["log_bytes"] = offset
    return index


def _load_index(hdir: Path) -> dict:
    """The index, rebuilt from the log when it is missing, unreadable or behind the log."""
    log = hdir / "log.jsonl"
    log_bytes = log.stat().st_size if log.exists() else 0
    try:
        index = json.loads((hdir / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        index = None
    if isinstance(index, dict) and int(index.get("schema_version") or 0) > SCHEMA_VERSION:
        index["too_new"] = True
        return index
    if (not isinstance(index, dict) or index.get("schema_version") != SCHEMA_VERSION
            or index.get("log_bytes") != log_bytes):
        index = _rebuild_index(hdir)
    return index


def too_new(vault: Path) -> bool:
    return bool(_load_index(history_dir(vault)).get("too_new"))


def _current_blob(entry: dict | None) -> str | None:
    return entry["h"][-1][1] if entry and entry.get("h") else None


def snapshot(vault: Path, cause: dict, scope=None) -> int | None:
    """Record every tracked file in `scope` (None = the whole vault) whose content differs from
    its last recorded version, and every recorded file in scope that is now gone, as one new
    version with `cause`. Returns the version number, or None when nothing changed."""
    vault = Path(vault)
    hdir = history_dir(vault)
    if not (vault / ".watchdog").is_dir():
        return None
    scope = list(scope) if scope is not None else None
    with _store_lock(hdir):
        log = hdir / "log.jsonl"
        _repair_log_tail(log)
        index = _load_index(hdir)
        if index.get("too_new"):
            raise HistoryTooNew("This investigation's history was written by a newer version of "
                                "Watchdog.")
        started = datetime.datetime.now().timestamp()
        seen = _scan(vault, scope)
        files = index["files"]
        changes: list[list] = []
        stats: dict[str, list | None] = {}
        added = 0
        for rel in sorted(seen):
            st = seen[rel]
            entry = files.get(rel)
            current = _current_blob(entry)
            if entry and current is not None and entry.get("st") == list(st):
                continue
            try:
                data = (vault / rel).read_bytes()
            except OSError:
                continue
            blob = hashlib.sha256(data).hexdigest()
            if blob != current:
                added += _put_blob(hdir, data)
                changes.append([rel, blob])
            stats[rel] = list(st) if st[1] / 1e9 < started - _RACY_SECONDS else None
        for rel, entry in files.items():
            if rel not in seen and _in_scope(rel, scope) and _current_blob(entry) is not None:
                changes.append([rel, None])
        version = None
        if changes:
            if not index["last"]:
                cause = {**cause, "first": True}
            version = index["last"] + 1
            rec = {"schema_version": SCHEMA_VERSION, "version": version, "at": _now(),
                   "cause": cause, "changes": changes}
            line = (json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            offset = log.stat().st_size if log.exists() else 0
            with open(log, "ab") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
            _apply(index, rec, offset)
            index["log_bytes"] = offset + len(line)
            _update_size_cache(hdir, offset, added)
        for rel, st in stats.items():
            if rel in files:
                files[rel]["st"] = st
        if changes or stats:
            _write_json_atomic(hdir / "index.json", index)
        return version


def _warn(e: Exception) -> None:
    print(f"  Warning: version history not recorded: {e}", file=sys.stderr)


def safe_snapshot(vault: Path, cause: dict, scope=None) -> int | None:
    """`snapshot` that reports a failure instead of raising."""
    try:
        return snapshot(vault, cause, scope)
    except Exception as e:  # noqa: BLE001 — history must never fail the operation it records
        _warn(e)
        return None


@contextmanager
def recording(vault: Path, cause: dict, scope=None):
    """Record the operation in the block as one version with `cause` (see the module docstring).
    Inside another recording of the same vault in this thread, it does nothing: the outer
    operation's version holds the change."""
    key = str(Path(vault).resolve())
    active = _ACTIVE.get()
    if key in active:
        yield
        return
    token = _ACTIVE.set(active | {key})
    try:
        safe_snapshot(vault, {"kind": "found"}, scope)
        ok = False
        try:
            yield
            ok = True
        finally:
            safe_snapshot(vault, cause if ok else {**cause, "incomplete": True}, scope)
    finally:
        _ACTIVE.reset(token)


def run_in_progress(vault: Path) -> bool:
    """True while a processing run holds the vault (its files are changing under it). A lock its
    run left behind when it died holds nothing (D293)."""
    from watchdog.pipeline.locks import held
    from watchdog.vault_paths import processing_lock
    return held(processing_lock(Path(vault))) is not None


def note_seen(vault: Path, rel: str) -> None:
    """Record a tracked file's current content if it changed outside any recorded operation (a
    page a Claude session wrote, an Obsidian edit), so its history is complete when it is shown.
    Skipped while a run is writing the vault, whose own version will hold the change."""
    if tracked(rel) and not run_in_progress(vault):
        safe_snapshot(vault, {"kind": "found"}, [rel])


# ── reading it back ───────────────────────────────────────────────────────────────────

def label(cause: dict | None) -> str:
    """The plain-language line the app shows for a version's cause."""
    c = cause or {}
    kind = c.get("kind")
    if c.get("first") and kind in ("found", "baseline"):
        text = "Version when history began"
    elif kind == "run":
        docs = [d for d in c.get("documents") or [] if isinstance(d, str)]
        total = int(c.get("document_count") or len(docs))
        if not total:
            text = "Post-processing run"
        else:
            shown = ", ".join(docs[:3])
            more = total - min(3, len(docs))
            text = (f"Documents added: {shown}" + (f" and {more} more" if more > 0 else "")
                    if shown else f"{total} document{'s' if total != 1 else ''} added")
    elif kind == "merge":
        text = f"Merged {c.get('merged') or 'an entity'} into {c.get('keep') or 'another'}"
        if c.get("by") == "reporter":
            text += " (by you)"
    elif kind == "undo_merge":
        text = f"Undid the merge of {c.get('split') or 'an entity'} into {c.get('keep') or 'another'}"
    elif kind == "mark":
        status = c.get("status")
        text = {"verified": "Fact marked verified", "disputed": "Fact marked disputed",
                "unverifiable": "Fact marked unverifiable"}.get(status, "Fact mark cleared")
    elif kind == "recheck":
        names = [n for n in c.get("names") or [] if isinstance(n, str)]
        more = int(c.get("count") or len(names)) - len(names)
        if c.get("all"):
            text = "Contradictions re-checked: the whole investigation"
        elif names:
            text = f"Contradictions re-checked: {', '.join(names)}" + (f" and {more} more" if more > 0 else "")
        else:
            text = "Contradictions re-checked"
    elif kind == "relationship_split":
        text = "Relationship wordings shown apart (by you)"
    elif kind == "rebuild":
        text = "Notes rebuilt from stored data"
    elif kind == "notes":
        text = "Your notes edited in the app"
    elif kind == "edit":
        text = f"{c.get('file') or 'File'} edited in the app"
    elif kind == "review":
        n = int(c.get("count") or 0)
        verb = "marked handled" if c.get("resolved", True) else "reopened"
        text = f"Review: {n} item{'s' if n != 1 else ''} {verb}"
    elif kind == "session":
        text = "Written in an Ask Claude session"
    elif kind == "restore":
        when = c.get("from_at") or ""
        day = _day(when)
        part = " (your notes)" if c.get("part") == "notes" else ""
        text = f"Restored by you{part}" + (f", from the version of {day}" if day else "")
    elif kind == "cleared":
        text = "History cleared; the current version was kept"
    elif kind == "found":
        text = "Changed since the last recorded version"
    else:
        text = "Changed"
    if c.get("incomplete"):
        text += " (stopped before it finished)"
    return text


def _day(iso: str) -> str | None:
    try:
        d = datetime.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    return f"{d.day} {d.strftime('%B %Y')}, {d.strftime('%H:%M')}"


def _version_row(index: dict, v: int) -> dict:
    meta = index["versions"].get(str(v)) or {}
    cause = meta.get("cause") or {}
    return {"version": v, "at": meta.get("at"), "cause": cause, "label": label(cause),
            "files": meta.get("n", 0), "removed": int(meta.get("removed") or 0)}


def file_history(vault: Path, rel: str) -> dict:
    """One path's versions, newest first, each with whether it is the file's content now."""
    vault = Path(vault)
    note_seen(vault, rel)
    index = _load_index(history_dir(vault))
    entry = index["files"].get(rel) or {"h": []}
    current = None
    try:
        current = hashlib.sha256((vault / rel).read_bytes()).hexdigest()
    except OSError:
        pass
    gaps = entry.get("g") or {}
    rows = []
    latest = entry["h"][-1][0] if entry["h"] else None
    for v, blob in reversed(entry["h"]):
        row = _version_row(index, v)
        row.update({"deleted": blob is None, "current": blob is not None and blob == current,
                    "latest": v == latest, "removed_before": int(gaps.get(str(v)) or 0)})
        rows.append(row)
    return {"path": rel, "tracked": tracked(rel), "restore": restore_kind(rel),
            "exists": current is not None, "versions": rows,
            "too_new": bool(index.get("too_new"))}


def _blob_at(index: dict, rel: str, version: int) -> tuple[str | None, int | None]:
    """(blob, previous version) for `rel` at `version`. KeyError when the path has no such
    version."""
    hist = (index["files"].get(rel) or {}).get("h") or []
    for i, (v, blob) in enumerate(hist):
        if v == version:
            return blob, (hist[i - 1][0] if i else None)
    raise KeyError(version)


def _text(vault: Path, blob: str | None) -> str | None:
    if blob is None:
        return None
    return read_blob(vault, blob).decode("utf-8", errors="replace")


def read_version(vault: Path, rel: str, version: int) -> dict:
    index = _load_index(history_dir(vault))
    blob, _ = _blob_at(index, rel, int(version))
    return {"path": rel, "version": int(version), "exists": blob is not None,
            "text": _text(vault, blob) or ""}


def versions(vault: Path, limit: int = 100, before: int | None = None) -> dict:
    """The vault's versions, newest first, with the paths each changed."""
    hdir = history_dir(vault)
    index = _load_index(hdir)
    nums = sorted((int(v) for v in index["versions"]), reverse=True)
    if before is not None:
        nums = [v for v in nums if v < before]
    page = nums[:max(1, min(int(limit), 500))]
    rows = []
    log = hdir / "log.jsonl"
    with (open(log, "rb") if log.exists() else open(os.devnull, "rb")) as f:
        for v in page:
            row = _version_row(index, v)
            off = (index["versions"].get(str(v)) or {}).get("off")
            changes = []
            if off is not None:
                f.seek(off)
                try:
                    changes = json.loads(f.readline()).get("changes") or []
                except ValueError:
                    changes = []
            row["changes"] = [{"path": p, "deleted": b is None}
                              for p, b in _changes({"changes": changes})]
            rows.append(row)
    return {"versions": rows, "total": len(index["versions"]),
            "more": len(nums) > len(page), "too_new": bool(index.get("too_new"))}


def stats(vault: Path) -> dict:
    hdir = history_dir(vault)
    index = _load_index(hdir)
    size, objects = 0, 0
    for root, _dirs, names in os.walk(hdir):
        for n in names:
            try:
                size += os.path.getsize(os.path.join(root, n))
            except OSError:
                pass
            if os.path.basename(root) != "history" and not n.startswith("."):
                objects += 1
    first = index["versions"].get("1") or {}
    earliest = min((m.get("at") for m in index["versions"].values() if m.get("at")), default=None)
    return {"versions": len(index["versions"]), "files": len(index["files"]), "objects": objects,
            "bytes": size, "since": earliest or first.get("at"),
            "too_new": bool(index.get("too_new"))}


# ── diff ──────────────────────────────────────────────────────────────────────────────

_TOKEN = re.compile(r"\s+|\w+|[^\w\s]")
_CONTEXT = 3
_MAX_LINES = 4000
_WORD_DIFF_MAX = 2000      # characters per line pair above which a line is shown whole


def _words(a: str, b: str) -> tuple[list, list]:
    """Word-level segments for one changed line pair: (old segments, new segments)."""
    if len(a) + len(b) > _WORD_DIFF_MAX:
        return [{"t": "del", "s": a}], [{"t": "ins", "s": b}]
    ta, tb = _TOKEN.findall(a), _TOKEN.findall(b)
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    old, new = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            s = "".join(ta[i1:i2])
            old.append({"t": "eq", "s": s})
            new.append({"t": "eq", "s": s})
        else:
            if i2 > i1:
                old.append({"t": "del", "s": "".join(ta[i1:i2])})
            if j2 > j1:
                new.append({"t": "ins", "s": "".join(tb[j1:j2])})
    return old, new


def diff_text(before: str | None, after: str | None) -> dict:
    """A line diff with word-level detail inside changed lines, in hunks with three lines of
    context. Each line is {op: " " | "-" | "+", old, new, segments}."""
    a = (before or "").splitlines()
    b = (after or "").splitlines()
    big = len(a) + len(b) > 40000
    sm = difflib.SequenceMatcher(None, a, b, autojunk=big)
    added = removed = 0
    hunks: list[dict] = []
    shown = 0
    truncated = False
    for group in sm.get_grouped_opcodes(_CONTEXT):
        lines: list[dict] = []
        for op, i1, i2, j1, j2 in group:
            if op == "equal":
                for k in range(i2 - i1):
                    lines.append({"op": " ", "old": i1 + k + 1, "new": j1 + k + 1,
                                  "segments": [{"t": "eq", "s": a[i1 + k]}]})
                continue
            removed += i2 - i1
            added += j2 - j1
            pairs = min(i2 - i1, j2 - j1) if op == "replace" and not big else 0
            dels, ins = [], []
            for k in range(i2 - i1):
                seg = [{"t": "del", "s": a[i1 + k]}]
                if k < pairs:
                    seg, other = _words(a[i1 + k], b[j1 + k])
                    ins.append({"op": "+", "old": None, "new": j1 + k + 1, "segments": other})
                dels.append({"op": "-", "old": i1 + k + 1, "new": None, "segments": seg})
            for k in range(pairs, j2 - j1):
                ins.append({"op": "+", "old": None, "new": j1 + k + 1,
                            "segments": [{"t": "ins", "s": b[j1 + k]}]})
            lines.extend(dels + ins)
        if shown + len(lines) > _MAX_LINES:
            truncated = True
            break
        shown += len(lines)
        first = group[0]
        hunks.append({"old_start": first[1] + 1, "new_start": first[3] + 1, "lines": lines})
    if truncated:
        # Count the rest so the totals stay true even when not every line is shown.
        added = removed = 0
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op != "equal":
                removed += i2 - i1
                added += j2 - j1
    return {"hunks": hunks, "added": added, "removed": removed, "truncated": truncated,
            "identical": not hunks and (before or "") == (after or "")}


def diff(vault: Path, rel: str, version: int, against: str = "previous") -> dict:
    """`rel` at `version` compared with its version before (`against="previous"`) or with the
    file as it is now (`against="current"`)."""
    vault = Path(vault)
    index = _load_index(history_dir(vault))
    blob, prev = _blob_at(index, rel, int(version))
    if against == "current":
        try:
            now = (vault / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            now = None
        before, after = _text(vault, blob), now
        base = {"before": {"version": int(version), "exists": blob is not None},
                "after": {"version": None, "exists": now is not None},
                "removed_between": 0}
    else:
        prev_blob = _blob_at(index, rel, prev)[0] if prev is not None else None
        before, after = _text(vault, prev_blob), _text(vault, blob)
        gap = int(((index["files"].get(rel) or {}).get("g") or {}).get(str(int(version))) or 0)
        # `removed_between`: versions of this file removed between the two compared, whose
        # changes the diff therefore includes (D288).
        base = {"before": {"version": prev, "exists": prev_blob is not None},
                "after": {"version": int(version), "exists": blob is not None},
                "removed_between": gap}
    return {"path": rel, **base, **diff_text(before, after)}


# ── restore and clear ─────────────────────────────────────────────────────────────────

class CannotRestore(Exception):
    pass


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def notes_section(text: str) -> str:
    """The body of a note's `## Notes` section (to the end of the file), or ''."""
    from watchdog.gui.vaultio import split_sections
    from watchdog.gui.vaultio import split_frontmatter
    _fm, body = split_frontmatter(text)
    return (split_sections(body).get("notes") or "").strip("\n")


def restore(vault: Path, rel: str, version: int) -> dict:
    """Put `rel`'s content at `version` back and record it as "Restored by you". A page is written
    whole; an entity or document note gets back only its Notes section (the rest is rebuilt from
    data at every run); other files are refused (`CannotRestore`)."""
    vault = Path(vault)
    kind = restore_kind(rel)
    if kind == "none":
        raise CannotRestore("This file is generated from the investigation's data and is rebuilt "
                            "after every change, so an older version can be viewed or copied but "
                            "not restored.")
    index = _load_index(history_dir(vault))
    if index.get("too_new"):
        raise HistoryTooNew("This investigation's history was written by a newer version of "
                            "Watchdog.")
    try:
        blob, _ = _blob_at(index, rel, int(version))
    except KeyError:
        raise LookupError(f"{rel} has no version {version}.") from None
    if blob is None:
        raise CannotRestore("That version records the file being deleted; there is nothing to "
                            "restore.")
    old = _text(vault, blob) or ""
    at = (index["versions"].get(str(int(version))) or {}).get("at")
    cause = {"kind": "restore", "from_version": int(version), "from_at": at, "part": kind}
    target = vault / rel
    with recording(vault, cause, [rel]):
        if kind == "page":
            _write_text_atomic(target, old)
        else:
            if not target.is_file():
                raise CannotRestore("This note no longer exists, so its notes cannot be put back. "
                                    "Copy them from the version shown instead.")
            from watchdog.gui.api.vault import (
                _NOTES_PLACEHOLDER, notes_write_lock, replace_notes_body,
            )
            with notes_write_lock(vault, rel):
                current = target.read_text(encoding="utf-8")
                placeholder = _NOTES_PLACEHOLDER[rel.split("/", 1)[0]]
                _write_text_atomic(target, replace_notes_body(current, notes_section(old),
                                                              placeholder))
    new = _load_index(history_dir(vault))
    hist = (new["files"].get(rel) or {}).get("h") or []
    return {"path": rel, "part": kind, "version": hist[-1][0] if hist else None}


def clear(vault: Path) -> dict:
    """Remove every past version and keep the current files, which become the first version of a
    fresh history. Irreversible."""
    import shutil
    vault = Path(vault)
    hdir = history_dir(vault)
    before = stats(vault) if hdir.exists() else {"versions": 0, "bytes": 0}
    with _store_lock(hdir):
        index = _load_index(hdir)
        if index.get("too_new"):
            raise HistoryTooNew("This investigation's history was written by a newer version of "
                                "Watchdog.")
        for name in ("objects",):
            shutil.rmtree(hdir / name, ignore_errors=True)
        for name in ("log.jsonl", "index.json", "size.json"):
            (hdir / name).unlink(missing_ok=True)
    snapshot(vault, {"kind": "cleared"})
    after = stats(vault)
    return {"removed_versions": before["versions"],
            "freed_bytes": max(0, before["bytes"] - after["bytes"]),
            "versions": after["versions"], "bytes": after["bytes"]}


# ── removing versions (D288) ──────────────────────────────────────────────────────────

class CannotRemove(Exception):
    pass


def _read_log(hdir: Path) -> list[dict]:
    out = []
    log = hdir / "log.jsonl"
    if not log.exists():
        return out
    with open(log, "rb") as f:
        for raw in f:
            if not raw.endswith(b"\n"):
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if isinstance(rec, dict) and "version" in rec:
                out.append(rec)
    return out


def _plan(index: dict, version: int, path: str | None, older: bool) -> tuple[set, list[str]]:
    """({(path, version)} to remove, paths kept because this version is what they hold now)."""
    files = index["files"]
    if path is not None:
        hist = (files.get(path) or {}).get("h") or []
        at = next((i for i, (v, _b) in enumerate(hist) if v == version), None)
        if at is None:
            raise LookupError(f"{path} has no version {version}.")
        if at == len(hist) - 1:
            raise CannotRemove("This is the file as it is now, so it cannot be removed from the "
                               "history. Change or delete the text in the file itself; the "
                               "version it replaces can then be removed.")
        chosen = hist[:at + 1] if older else [hist[at]]
        return {(path, v) for v, _b in chosen}, []
    if str(version) not in index["versions"]:
        raise LookupError(f"There is no version {version}.")
    targets, kept = set(), []
    for p, entry in files.items():
        hist = entry.get("h") or []
        for i, (v, _b) in enumerate(hist):
            if v == version:
                (kept.append(p) if i == len(hist) - 1 else targets.add((p, v)))
    return targets, sorted(kept)


def remove(vault: Path, version: int, path: str | None = None, older: bool = False,
           dry_run: bool = False) -> dict:
    """Remove versions from the history for good (D288): `path`'s entry at `version` (with
    `older`, also every earlier one of that file), or, with no `path`, every file's entry in
    `version` except those that are still the file's latest version, which describe the file as
    it is now and are kept. The log is rewritten without them, atomically and under the store
    lock; a blob no remaining version names is deleted; the next remaining version of each file
    carries how many of its versions before it were removed, so its diff is labelled as spanning
    them. Nothing about the removed content is kept. `dry_run` reports what would happen."""
    vault = Path(vault)
    hdir = history_dir(vault)
    version = int(version)
    if run_in_progress(vault):
        raise HistoryBusy("Documents are being added to this investigation. Remove versions when "
                          "that has finished.")
    # Record any change made outside a recorded operation first, so a file's latest version is
    # what it holds now and is never removed (it would be recorded again at the next snapshot).
    safe_snapshot(vault, {"kind": "found"}, [path] if path is not None else None)
    if not (hdir / "log.jsonl").exists():
        raise LookupError("This investigation has no history.")
    with _store_lock(hdir):
        log = hdir / "log.jsonl"
        _repair_log_tail(log)
        index = _load_index(hdir)
        if index.get("too_new"):
            raise HistoryTooNew("This investigation's history was written by a newer version of "
                                "Watchdog.")
        targets, kept = _plan(index, version, path, older)
        files = index["files"]
        # Where each removed entry's gap goes: the next entry of the same file that stays.
        moved: dict[tuple[str, int], int] = {}
        for p in {p for p, _v in targets}:
            entry = files[p]
            gaps = entry.get("g") or {}
            carry = 0
            for v, _b in entry["h"]:
                if (p, v) in targets:
                    carry += 1 + int(gaps.get(str(v)) or 0)
                elif carry:
                    moved[(p, v)] = int(gaps.get(str(v)) or 0) + carry
                    carry = 0
        removed_blobs = {b for p, v in targets for vv, b in files[p]["h"] if vv == v and b}
        records, kept_records = _read_log(hdir), []
        for rec in records:
            v = int(rec["version"])
            changes = _changes(rec)
            keep = [[p, b] for p, b in changes if (p, v) not in targets]
            if not keep:
                continue
            out = {**rec, "changes": keep}
            dropped = len(changes) - len(keep)
            if dropped:
                out["removed"] = int(rec.get("removed") or 0) + dropped
            gaps = {p: n for p, n in (rec.get("gaps") or {}).items() if any(k[0] == p for k in keep)}
            for p, _b in keep:
                if (p, v) in moved:
                    gaps[p] = moved[(p, v)]
            if gaps:
                out["gaps"] = gaps
            else:
                out.pop("gaps", None)
            kept_records.append(out)
        still = {b for rec in kept_records for _p, b in _changes(rec) if b}
        purge = sorted(removed_blobs - still)
        shared = sorted({(p, v) for p, v in targets
                         for vv, b in files[p]["h"] if vv == v and b in still})
        freed = sum(_file_size(_blob_path(hdir, b)) for b in purge)
        result = {"removed": [{"path": p, "version": v} for p, v in sorted(targets)],
                  "kept_current": kept, "purged": len(purge),
                  "shared": [{"path": p, "version": v} for p, v in shared],
                  "freed_bytes": freed, "dry_run": bool(dry_run)}
        if dry_run or not targets:
            return result
        # 1. The log without the removed entries, replacing the old one in one step.
        fd, tmp = tempfile.mkstemp(prefix=".log.", suffix=".tmp", dir=hdir)
        try:
            with os.fdopen(fd, "wb") as f:
                for rec in kept_records:
                    f.write((json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
                             + "\n").encode("utf-8"))
                f.flush()
                os.fsync(f.fileno())
            _retry(lambda: os.replace(tmp, log))
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        # 2. The index rebuilt from it; each file's latest version is unchanged, so the stat
        # cache that lets a snapshot skip unchanged files carries over.
        new = _rebuild_index(hdir)
        for p, entry in new["files"].items():
            if p in files:
                entry["st"] = files[p].get("st")
        _write_json_atomic(hdir / "index.json", new)
        # 3. Every blob no version names: the removed content, and any left by an earlier
        # removal that stopped between its log and its blobs.
        _sweep(hdir, still)
        _update_size_cache(hdir, None, 0)
        return result


def _retry(fn, attempts: int = 10):
    """Windows refuses to replace or delete a file another reader has open for a moment."""
    import time
    for i in range(attempts):
        try:
            return fn()
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.1)


def _sweep(hdir: Path, referenced: set) -> None:
    objects = hdir / "objects"
    if not objects.is_dir():
        return
    for sub in objects.iterdir():
        if not sub.is_dir():
            continue
        for f in sub.iterdir():
            if (sub.name + f.name) not in referenced:
                _retry(lambda f=f: f.unlink(missing_ok=True))
        try:
            sub.rmdir()
        except OSError:
            pass
