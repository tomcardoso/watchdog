"""The verification ledger (D271): a reporter's own check of each fact.

A reporter marks a fact **Verified**, **Disputed** or **Can't verify**, with an optional note;
Watchdog records who and when. Nothing here calls a model, and nothing here changes a fact: the
ledger is the journalist's record about the pipeline's output, kept beside it.

Fact identity. Facts have no stored id, so one is derived: ``fact:1:<sha12>:<hash10>``, where
``1`` is the id scheme (`ID_SCHEME`), ``sha12`` opens the document's SHA-256 and ``hash10`` is a
SHA-1 of that full SHA-256, the fact's cited page and its text normalized only for case, Unicode
form and spacing (never punctuation or digits, so "$1.2 million" and "$12 million" stay different
facts). An exact duplicate within one document (same text, same page) gets ``:2``, ``:3`` in
reading order. Each mark also keeps the fact's text and page as they were when it was made, and a
mark attaches to a current fact only when that snapshot still matches (`attach`): so neither a hash
collision nor a later change to how ids or facts are produced can re-point a mark at different
words. A future id scheme gets a new number, and marks made under scheme 1 keep resolving through
it. The id survives anything
that leaves the fact's words and page alone: an entity merge (entities are not part of it), a
reindex, a re-run of post-flight, and re-processing that extracts the same fact again. When
re-processing changes a fact's wording or page, the id changes with it, and the old mark is shown
as no longer matching a current fact, with the fact as it read when marked. It is never moved to
another fact, because a mark attached to words the reporter didn't check would be worse than none.

Storage. ``.watchdog/registry/verification.json`` is the source of truth (``schema_version``
1; unknown fields are kept on rewrite and ignored on read; a ledger with a newer version than this
code knows is read for display but never written, `LedgerTooNew`)::

    {"schema_version": 1,
     "marks": {"<fact id>": {"status": "verified"|"disputed"|"unverifiable"|null, "note",
                             "by", "at", "sha256", "filename", "page", "fact",
                             "history": [{"status", "note", "by", "at"}, …]}}}

Clearing a mark sets ``status`` to null and keeps the history. Every write takes the ledger's own
lock (``.verification-lock``), is atomic, and regenerates ``verification.md`` at the vault root, a
readable list of every marked fact, so the vault stays a folder of Markdown files.
"""

from __future__ import annotations

import datetime
import getpass
import hashlib
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

_SCHEMA_VERSION = 1
ID_SCHEME = 1
NOTE_FORMAT = 1
STATUSES = ("verified", "disputed", "unverifiable")
LABELS = {"verified": "Verified", "disputed": "Disputed", "unverifiable": "Can't verify"}
# What the CLI accepts for each status, besides its own name.
STATUS_ALIASES = {"cant-verify": "unverifiable", "can't-verify": "unverifiable",
                  "cannot-verify": "unverifiable", "clear": None, "none": None}
NOTE_CAP = 2000
NOTE_FILE = "verification.md"
_LOCK = ".verification-lock"
_WS_RE = re.compile(r"\s+")


# ── identity ──────────────────────────────────────────────────────────────────────────

def normalize_fact_text(text: str) -> str:
    """Case, Unicode form and whitespace only: a punctuation or digit change is a different fact."""
    text = unicodedata.normalize("NFKC", text or "").casefold()
    return _WS_RE.sub(" ", text).strip()


def _page(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def fact_ids(sha256: str, facts: list[dict]) -> list[str]:
    """The id of each fact in `facts` (dicts with `fact` and `page`), in order."""
    seen: dict[str, int] = {}
    out = []
    for f in facts:
        page = _page(f.get("page"))
        key = f"{sha256}\n{'' if page is None else page}\n{normalize_fact_text(f.get('fact') or '')}"
        base = f"fact:{ID_SCHEME}:{sha256[:12]}:{hashlib.sha1(key.encode('utf-8')).hexdigest()[:10]}"
        n = seen.get(base, 0) + 1
        seen[base] = n
        out.append(base if n == 1 else f"{base}:{n}")
    return out


def sha_prefix(fid: str) -> str:
    """The document SHA-256 prefix inside a fact id, or "" for an id this code can't read."""
    parts = fid.split(":")
    if len(parts) >= 4 and parts[0] == "fact" and parts[1] == str(ID_SCHEME):
        return parts[2]
    return ""


def attach(mark: dict | None, fact: dict | None) -> dict | None:
    """`mark` if it is a current mark made on exactly this fact's words and page, else None. The
    id already says so; this re-checks against the snapshot the mark was made on."""
    if not isinstance(mark, dict) or mark.get("status") not in STATUSES or not isinstance(fact, dict):
        return None
    if "fact" in mark and normalize_fact_text(mark.get("fact") or "") != normalize_fact_text(fact.get("fact") or ""):
        return None
    if "page" in mark and _page(mark.get("page")) != _page(fact.get("page")):
        return None
    return mark


class LedgerTooNew(RuntimeError):
    """The ledger was written by a newer Watchdog; this one can show it but must not rewrite it."""


# ── a vault's facts ───────────────────────────────────────────────────────────────────

def _registry(vault: Path) -> Path:
    return vault / ".watchdog" / "registry"


def _load_documents(vault: Path) -> dict:
    try:
        data = json.loads((_registry(vault) / "documents.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


_NOTE_PAGE_RE = re.compile(r"\[\[[^\]]*#page=(\d+)[^\]]*\]\]|p\. (\d+)")


def facts_from_note(section: str) -> list[dict]:
    """Key facts read back from a document note's `## Key facts` bullets, for a document with no
    saved extraction (a vault older than the staged artifacts)."""
    out: list[dict] = []
    for line in (section or "").splitlines():
        if not line.startswith("- "):
            if line.startswith(">") and out and not out[-1]["quote"]:
                out[-1]["quote"] = line.lstrip("> ").strip() or None
            continue
        text = re.sub(r"\s+\^f-[0-9a-f]+(?:-\d+)?$", "", line[2:].strip())
        text = re.sub(r" · ✗ disputed$", "", text)
        m = _NOTE_PAGE_RE.search(text)
        page = int(m.group(1) or m.group(2)) if m else None
        inferred = "*(inferred)*" in text
        text = re.sub(r"\s*\(\[\[[^\]]*\|p\. \d+\]\]\)", "", text)
        text = re.sub(r"\s*\*\(.*?\)\*", "", text).strip()
        out.append({"fact": text, "page": page, "basis": "inferred" if inferred else "stated",
                    "date": None, "quote": None, "entities": [], "from_note": True})
    return out


def document_facts(vault: Path, sha256: str, record: dict | None = None) -> list[dict]:
    """A committed document's facts as the app shows them: the saved extraction's `key_facts`,
    or the note's bullets when there is none. Facts with no text are dropped."""
    try:
        ex = json.loads((vault / ".watchdog" / "extracted" / f"{sha256}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ex = None
    doc = ex.get("document") if isinstance(ex, dict) else None
    if isinstance(doc, dict) and isinstance(doc.get("key_facts"), list):
        facts = [f for f in doc["key_facts"] if isinstance(f, dict) and (f.get("fact") or "").strip()]
        from watchdog.pipeline import passages
        if not passages.readable(doc):   # written by a newer Watchdog: show facts, not passages
            facts = [{k: v for k, v in f.items() if k not in passages.PASSAGE_FIELDS} for f in facts]
        return facts
    record = record if record is not None else _load_documents(vault).get(sha256) or {}
    note = record.get("document_note") if isinstance(record, dict) else None
    if not note:
        return []
    try:
        text = (vault / f"{note}.md").read_text(encoding="utf-8")
    except OSError:
        return []
    from watchdog.pipeline.write_vault import _extract_section
    return [f for f in facts_from_note(_extract_section(text, "Key facts")) if f["fact"]]


def all_facts(vault: Path, docs: dict | None = None) -> dict[str, dict]:
    """Every committed fact by id: `{id: {fact, page, sha256, filename, title, note, passage_method}}`."""
    docs = docs if docs is not None else _load_documents(vault)
    out: dict[str, dict] = {}
    for sha, rec in docs.items():
        if not isinstance(rec, dict):
            continue
        facts = document_facts(vault, sha, rec)
        for fid, f in zip(fact_ids(sha, facts), facts):
            out[fid] = {"fact": f["fact"], "page": _page(f.get("page")), "sha256": sha,
                        "filename": rec.get("filename") or sha[:12],
                        "title": rec.get("title") or rec.get("filename") or sha[:12],
                        "note": rec.get("document_note"),
                        "passage_method": f.get("passage_method")}
    return out


# ── the store ─────────────────────────────────────────────────────────────────────────

def _path(vault: Path) -> Path:
    return _registry(vault) / "verification.json"


def load(vault: Path) -> dict:
    """The ledger, or an empty one when the file is missing or unreadable."""
    try:
        data = json.loads(_path(vault).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema_version": _SCHEMA_VERSION, "marks": {}}
    if not isinstance(data, dict) or not isinstance(data.get("marks"), dict):
        return {"schema_version": _SCHEMA_VERSION, "marks": {}}
    return data


def too_new(data: dict) -> bool:
    v = data.get("schema_version", _SCHEMA_VERSION)
    return not isinstance(v, int) or isinstance(v, bool) or v > _SCHEMA_VERSION


def marks(vault: Path) -> dict[str, dict]:
    """Current marks (status set) by fact id."""
    return {fid: m for fid, m in load(vault)["marks"].items()
            if isinstance(m, dict) and m.get("status") in STATUSES}


def _save(vault: Path, data: dict) -> None:
    path = _path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def parse_status(value: str | None) -> str | None:
    """A status from user input: one of `STATUSES`, an alias, or empty for clear. Raises
    ValueError on anything else."""
    v = (value or "").strip().lower().replace("_", "-").replace(" ", "-")
    if not v:
        return None
    if v in STATUSES:
        return v
    if v in STATUS_ALIASES:
        return STATUS_ALIASES[v]
    raise ValueError(f"Unknown status '{value}'. Use verified, disputed, cant-verify or clear.")


def _os_full_name() -> str | None:
    if sys.platform == "win32":
        try:
            import ctypes
            size = ctypes.c_ulong(0)
            fn = ctypes.windll.secur32.GetUserNameExW   # type: ignore[attr-defined]
            fn(3, None, ctypes.byref(size))             # 3 = NameDisplay
            buf = ctypes.create_unicode_buffer(size.value)
            if size.value and fn(3, buf, ctypes.byref(size)):
                return buf.value.strip() or None
        except Exception:  # noqa: BLE001 — a display name is a nicety
            return None
        return None
    try:
        import pwd
        gecos = pwd.getpwuid(os.getuid()).pw_gecos
    except (ImportError, KeyError, OSError):
        return None
    name = (gecos or "").split(",")[0].strip()
    return name or None


def default_reporter_name() -> str:
    """The OS account's full name where the system has one, else the login name."""
    name = _os_full_name()
    if name:
        return name
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 — no login name in some sandboxes
        return "Unknown"


def reporter_name() -> str:
    """The `reporter_name` setting ("Your name"), or the OS default."""
    from watchdog import config as user_config
    value = user_config.get("reporter_name", None)
    return value.strip() if isinstance(value, str) and value.strip() else default_reporter_name()


def mark(vault: Path, fid: str, status: str | None, note: str | None = None,
         by: str | None = None) -> dict:
    """Set (or, with `status` None, clear) the mark on one current fact. The fact must exist now:
    a mark is only ever made against words the reporter can see. Returns the stored entry, with
    its id. Raises LookupError for an unknown fact and ValueError for a bad status."""
    from watchdog.pipeline.write_vault import _registry_lock

    if status is not None and status not in STATUSES:
        raise ValueError(f"Unknown status '{status}'.")
    note = (note or "").strip()[:NOTE_CAP]
    docs = _load_documents(vault)
    prefix = sha_prefix(fid)
    sha, current = None, None
    for candidate in (s for s in docs if prefix and s.startswith(prefix)):
        facts = document_facts(vault, candidate, docs[candidate])
        current = next((f for i, f in zip(fact_ids(candidate, facts), facts) if i == fid), None)
        if current is not None:
            sha = candidate
            break
    registry = _registry(vault)
    registry.mkdir(parents=True, exist_ok=True)
    with _registry_lock(registry, _LOCK):
        data = load(vault)
        if too_new(data):
            raise LedgerTooNew("This investigation's verification ledger was written by a newer "
                               "version of Watchdog. Update Watchdog to change marks.")
        entry = data["marks"].get(fid)
        if current is None and status is not None:
            raise LookupError(f"No current fact has the id {fid}.")
        if current is None and entry is None:
            raise LookupError(f"No current fact has the id {fid}.")
        now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        who = (by or "").strip() or reporter_name()
        if entry is None:
            entry = {"history": []}
        elif entry.get("status") is not None or entry.get("note"):
            entry.setdefault("history", []).append(
                {k: entry.get(k) for k in ("status", "note", "by", "at")})
        if entry.get("status") is not None and current is not None and attach(entry, current) is None:
            # A mark left on this id from different words (a collision, or an older scheme):
            # start afresh, keeping what it said in the history.
            entry["history"][-1]["fact"] = entry.get("fact")
        entry.update({"status": status, "note": note or None, "by": who, "at": now})
        if current is not None:
            rec = docs[sha]
            entry.update({"sha256": sha, "filename": rec.get("filename"),
                          "page": _page(current.get("page")), "fact": current["fact"]})
        data["marks"][fid] = entry
        data["schema_version"] = _SCHEMA_VERSION
        _save(vault, data)
        render(vault, data=data, docs=docs)
    # Entity notes show each fact's mark (D280), and its document's note and the timeline label a
    # disputed one (D285): refresh the notes of the entities this fact is about and of its
    # document, now if no commit holds the registry, else at the next commit, and re-render the
    # timeline when the fact is dated (it reads only the stored events, so it needs no lock).
    if current is not None and sha is not None:
        from watchdog.pipeline import entity_notes
        try:
            entity_notes.refresh(vault, entity_notes.fact_entities(vault, sha, current), docs={sha})
        except OSError as e:
            print(f"  Warning: notes not refreshed after the mark: {e}", file=sys.stderr)
        if (current.get("date") or "").strip() and (vault / "timeline.md").exists():
            from watchdog.pipeline import timeline
            try:
                timeline.cmd_rebuild_timeline(vault, quiet=True)
            except OSError as e:
                print(f"  Warning: timeline not refreshed after the mark: {e}", file=sys.stderr)
    return {"id": fid, **entry}


# ── reading it back ───────────────────────────────────────────────────────────────────

def summary(vault: Path, facts: dict[str, dict] | None = None, data: dict | None = None) -> dict:
    """Progress over the vault's current facts, and the marks that no longer match one."""
    facts = facts if facts is not None else all_facts(vault)
    data = data or load(vault)
    current = {fid: m for fid, m in data["marks"].items()
               if isinstance(m, dict) and m.get("status") in STATUSES}
    counts = {s: 0 for s in STATUSES}
    orphaned = 0
    for fid, m in current.items():
        if attach(m, facts.get(fid)):
            counts[m["status"]] += 1
        else:
            orphaned += 1
    unlocated = sum(1 for f in facts.values() if f.get("passage_method") == "unlocated")
    return {"facts": len(facts), **counts,
            "unmarked": len(facts) - sum(counts.values()),
            "unlocated": unlocated, "orphaned": orphaned, "read_only": too_new(data)}


def entries(vault: Path, facts: dict[str, dict] | None = None, data: dict | None = None) -> list[dict]:
    """Every current mark, newest first, each with its fact as it reads now (or, for an orphan,
    as it read when marked) and `orphaned`."""
    facts = facts if facts is not None else all_facts(vault)
    out = []
    for fid, m in (data or load(vault))["marks"].items():
        if not isinstance(m, dict) or m.get("status") not in STATUSES:
            continue
        cur = facts.get(fid) if attach(m, facts.get(fid)) else None
        out.append({
            "id": fid, "status": m["status"], "note": m.get("note") or None,
            "by": m.get("by") or None, "at": m.get("at") or None,
            "orphaned": cur is None,
            "fact": (cur or m).get("fact") or "", "page": (cur or m).get("page"),
            "sha256": (cur or m).get("sha256"),
            "filename": (cur or m).get("filename"),
            "title": (cur or {}).get("title") or m.get("filename"),
            "note_path": (cur or {}).get("note"),
            "history": [h for h in m.get("history") or [] if isinstance(h, dict)],
        })
    out.sort(key=lambda e: e["at"] or "", reverse=True)
    return out


def _md(text: str) -> str:
    from watchdog.pipeline.write_vault import _defang
    return _defang(_WS_RE.sub(" ", text or "").strip())


def render(vault: Path, data: dict | None = None, docs: dict | None = None) -> Path:
    """Regenerate `verification.md` from the ledger: progress, then every marked fact grouped by
    status and document, then marks whose fact has changed since."""
    docs = docs if docs is not None else _load_documents(vault)
    facts = all_facts(vault, docs)
    data = data or load(vault)
    s = summary(vault, facts, data)
    rows = entries(vault, facts, data)
    lines = [
        "---", "title: Fact verification", "type: Verification", f"format_version: {NOTE_FORMAT}",
        "---", "",
        "# Fact verification", "",
        "<!-- Generated by Watchdog from .watchdog/registry/verification.json and rewritten on "
        "every change. Mark facts in the Watchdog app; edits made here are not kept. -->", "",
        f"**{s['verified']} of {s['facts']} facts verified** · {s['disputed']} disputed · "
        f"{s['unverifiable']} can't verify · {s['unmarked']} not yet checked", "",
    ]
    if not rows:
        lines += ["No facts have been marked yet.", ""]
    for status in ("disputed", "unverifiable", "verified"):
        group = [r for r in rows if r["status"] == status and not r["orphaned"]]
        if not group:
            continue
        lines += [f"## {LABELS[status]} ({len(group)})", ""]
        by_doc: dict[str, list[dict]] = {}
        for r in group:
            by_doc.setdefault(r["sha256"] or "", []).append(r)
        for sha, items in sorted(by_doc.items(), key=lambda kv: (kv[1][0]["title"] or "").lower()):
            first = items[0]
            title = _md(first["title"] or first["filename"] or sha[:12])
            link = f"[[{first['note_path']}|{title}]]" if first["note_path"] else title
            lines += [f"### {link}", ""]
            for r in sorted(items, key=lambda r: (r["page"] or 0, r["fact"])):
                page = f"p. {r['page']}: " if r["page"] else ""
                lines.append(f"- {page}{_md(r['fact'])}")
                when = (r["at"] or "")[:16].replace("T", " ")
                lines.append(f"  - {LABELS[status]} by {_md(r['by'] or 'unknown')}, {when}"
                             + (f": {_md(r['note'])}" if r["note"] else ""))
            lines.append("")
    orphans = [r for r in rows if r["orphaned"]]
    if orphans:
        lines += [f"## No longer matching a current fact ({len(orphans)})", "",
                  "These marks were made on facts whose wording or page has changed since, usually "
                  "because the document was processed again. They are kept as a record and are not "
                  "carried over to the new wording.", ""]
        for r in orphans:
            page = f"p. {r['page']}: " if r["page"] else ""
            lines.append(f"- {_md(r['filename'] or '')}, {page}{_md(r['fact'])}")
            lines.append(f"  - {LABELS[r['status']]} by {_md(r['by'] or 'unknown')}, "
                         f"{(r['at'] or '')[:16].replace('T', ' ')}" + (f": {_md(r['note'])}" if r["note"] else ""))
        lines.append("")
    path = vault / NOTE_FILE
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path
