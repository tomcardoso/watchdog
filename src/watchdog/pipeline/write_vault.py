#!/usr/bin/env python3
"""Write every vault artifact for one staged extraction: the deterministic writer the finalize
commit pass replays (D126, I7).

Extraction JSON as consumed here, after `postflight.explode_key_facts` has fanned `key_facts` out
into per-entity `evidence_fragments`/`timeline_events` (D26). The model itself emits only
`key_facts` and the entity graph:
{
  "document": {
    "sha256": str, "filename": str, "original_path": str,
    "title": str, "document_type": str, "date_of_document": str|null,
    "page_count": int, "source": str|null, "obtained": str|null,
    "sidecar": str|null,  // filtered/allowlisted .yml text (D121) — re-written into morgue
    "near_duplicate_of": str|null, "shingles": [],
    "summary": str,
    "key_facts": [{"fact": str, "page": int|null, "basis": "stated"|"inferred",
                   "date": str|null, "entities": [str], "quote": str|null}]
  },
  "entities": [
    {
      "id": str, "name": str, "type": str, "aliases": [],
      "evidence_fragments": [          // reconstructed by postflight from facts tagged to this id
        {"claim": str, "page": int|null, "basis": "stated"|"inferred", "quote": str|null}
      ],
      "contradictions": [str]|null,    // each a `> [!contradiction]` callout block
      "timeline_events": [             // reconstructed by postflight from this id's dated facts
        {
          "date": str,   // YYYY-MM-DD, YYYY-MM, or YYYY
          "event": str,
          "page": int|null,
          "basis": "stated"|"inferred"
        }
      ],
      "roles": [
        {
          "relationship": str, "target_id": str,   // target_name/target_type resolved from id
          "page": int|null, "basis": "stated"|"inferred",
          "date_range": str|null
        }
      ]
    }
  ],
  "morgue_entity_id": str,
  "morgue_document_type": str
}"""

import hashlib
import json
import os
import re
import shutil
import sys
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import yaml

from watchdog.pipeline.entity_norm import normalize_entity_name
from watchdog.pipeline.entity_type import canonical_type
from watchdog.pipeline.json_io import _read_json_or
from watchdog.vault_paths import incoming_dir, modernize_path

try:
    from fcntl import flock as _flock, LOCK_EX as _LOCK_EX, LOCK_UN as _LOCK_UN
    _HAS_FLOCK = True
except ImportError:
    _HAS_FLOCK = False  # Windows

try:
    import msvcrt as _msvcrt  # Windows-only stdlib module
except ImportError:
    _msvcrt = None  # macOS/Linux


# ── Helpers ───────────────────────────────────────────────────────────────────

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(text: str) -> str:
    """Convert arbitrary text to a URL-safe kebab-case slug."""
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


def _type_dir(entity_type: str) -> str:
    """Directory name for an entity type. Entity ``id`` is slugified upstream in postflight
    (#303), but ``type`` is not — it stays a display value (e.g. "Person"), so this is the
    one place it becomes a path segment; route it through the full ``slugify`` (not just
    ``.lower()``) so a hostile value (e.g. containing ``../``) can't traverse out of the
    entities directory."""
    return slugify(entity_type) or "entity"


def _doc_slug(filename: str) -> str:
    return slugify(Path(filename).stem) or "document"


def _unique_doc_slug(vault_path: Path, slug: str, sha256: str, filename: str,
                     documents_reg: dict) -> str:
    """The document-note slug for `sha256` — `slug` unless another document already owns it.

    Ownership is decided by the registry, not by the note's `file:` line: two different documents
    can share a filename (every production has its `Order.pdf`), and keying on the filename let
    the second silently overwrite the first's note (D241). A document already in the registry keeps
    the note it has, so a `--force` re-commit replaces its own note rather than forking a new one.
    A note on disk that no registry entry claims (hand-made, or left by a crash) is still respected
    when it names a different file, as before."""
    own = (documents_reg.get(sha256) or {}).get("document_note")
    if own:
        return own.removeprefix("documents/")
    taken = {e.get("document_note") for s, e in documents_reg.items() if s != sha256}
    candidate = vault_path / "documents" / f"{slug}.md"
    clash = f"documents/{slug}" in taken
    if not clash and candidate.exists():
        try:
            head = candidate.read_text(encoding="utf-8", errors="replace")
            clash = f"file: {filename}" not in head
        except OSError:
            pass
    return f"{slug}-{sha256[:6]}" if clash else slug


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _unique_morgue_path(vault_path: Path, morgue_dir: str, filename: str, sha256: str,
                        documents_reg: dict) -> str:
    """`<morgue_dir>/<filename>`, or `<stem>-<sha6><suffix>` there when another document already
    holds that path — the original is moved, not copied, so a clash would replace another
    document's only copy of its source file (D241)."""
    relative = f"{morgue_dir}/{filename}"
    if (documents_reg.get(sha256) or {}).get("morgue_path") == relative:
        return relative
    taken = {e.get("morgue_path") for s, e in documents_reg.items() if s != sha256}
    existing = vault_path / relative
    if relative in taken or (existing.is_file() and _file_sha256(existing) != sha256):
        p = Path(filename)
        relative = f"{morgue_dir}/{p.stem}-{sha256[:6]}{p.suffix}"
    return relative


def _defang(text: str) -> str:
    """Defang ``[[``/``]]`` in model-supplied text before it is written into a vault note (#305,
    #508) — otherwise a hostile value can close a wikilink early and forge a second one pointing
    elsewhere in the vault. Covers wikilink display text/headings (name, title) as well as free
    text bodies (claim, reason, quote, relationship) since a quote in particular is resolved
    verbatim from the page text at post-flight (#529), giving a document author direct control
    over its contents."""
    from watchdog.pipeline.research import neutralize
    return neutralize(text or "")


def _defang_links(text: str) -> str:
    """`_defang` without its whitespace collapse — for multi-line bodies (a document summary)
    whose paragraph breaks must survive. Breaks `[[`/`]]` so model- or document-supplied text can
    never forge a wikilink."""
    return (text or "").replace("[[", "[ [").replace("]]", "] ]")


def _assert_in_vault(path: Path, vault_path: Path, label: str) -> Path:
    """Refuse to write outside the vault (#303) — the resolve-based backstop behind entity
    id/type slugification, in case a malicious value slips past it. Matches the existing
    ``--extraction``/``--neardup-file`` containment guards in ``main()``, below."""
    resolved = path.resolve()
    if not resolved.is_relative_to(vault_path.resolve()):
        sys.exit(f"Error: refusing to write outside the vault ({label}): {resolved}")
    return path


class NameIndex:
    """(normalized name, canonical type) → entity id, over every name and alias in a registry.

    Built once and kept current with `add` (#696), rather than rebuilt from the whole registry for
    every document — on a batch of thousands that rebuild was O(documents × entities). The lookup
    matches a fresh rebuild exactly: a key shared by several entities resolves to the one inserted
    into the registry first, which is what `dict.setdefault` over the registry's insertion order
    gave. Valid while entities are only added or gain aliases — their name and type never change
    in `_merge_entity`, and nothing is removed during a fold."""

    def __init__(self, entities_reg: dict):
        self._pos: dict[str, int] = {}
        self._index: dict[tuple[str, str], str] = {}
        self._all: dict[tuple[str, str], set[str]] = {}
        for eid, entry in entities_reg.items():
            self.add(eid, entry)

    def add(self, eid: str, entry: dict) -> None:
        pos = self._pos.setdefault(eid, len(self._pos))
        etype = canonical_type(entry["type"])
        for n in [entry["name"], *entry.get("aliases", [])]:
            key = (normalize_entity_name(n), etype)
            self._all.setdefault(key, set()).add(eid)
            current = self._index.get(key)
            if current is None or self._pos[current] > pos:
                self._index[key] = eid

    def get(self, key: tuple[str, str]) -> str | None:
        return self._index.get(key)

    def get_all(self, key: tuple[str, str]) -> list[str]:
        """Every entity carrying this (name, type), in registry order — several records can share
        a name now that a same-name person is no longer folded on name alone (D279)."""
        return sorted(self._all.get(key, ()), key=lambda eid: self._pos[eid])


def _unique_entity_id(eid: str, taken) -> str:
    """`eid`, or `eid-2`, `eid-3`, … — the first not in `taken`."""
    n = 2
    while f"{eid}-{n}" in taken:
        n += 1
    return f"{eid}-{n}"


def _names_only_judge(entity: dict, existing_id: str, entities_reg: dict) -> bool:
    """`identity.classify` on names and aliases alone — the judge when no vault evidence is at
    hand. Only a high-confidence verdict folds."""
    from watchdog.pipeline import identity
    entry = entities_reg[existing_id]
    a = identity.Profile(entity["id"], entity["name"], entity["type"])
    for alias in entity.get("aliases") or []:
        a.add_surface(alias)
    b = identity.Profile(existing_id, entry["name"], entry["type"])
    for alias in entry.get("aliases") or []:
        b.add_surface(alias)
    verdict = identity.classify(a, b)
    return bool(verdict and verdict["tier"] == "high")


def _reconcile_entity_ids(incoming_entities: list[dict], entities_reg: dict,
                          name_index: NameIndex | None = None, judge=None) -> dict[str, str]:
    """Fold incoming entities onto existing ones only when `judge` says the match is high
    confidence (D279); otherwise keep them apart, so a medium or low match reaches the reconcile
    model or the reporter instead of being merged here.

    Two ways an incoming entity meets an existing one: the same slug (two documents both coin
    `john-smith`), or the same normalized (name, canonical type) under another slug, including an
    existing alias. `judge(entity, existing_id)` decides each; it is `identity.classify` with the
    documents' evidence when called from `orchestrate._batch_exact_fold`, and on names alone
    otherwise. A same-slug match that is not high gets a fresh slug (`john-smith-2`) so the two
    records stay separate. Non-person entities are judged first, so a person's roles already point
    at reconciled ids when shared employers and addresses are compared.

    Each entity that moves keeps the id it was extracted under in `extracted_id`. Returns the remap
    (old id -> new id) so the caller can rewrite the other fields that name entity ids —
    `morgue_entity_id`, `document.key_facts[].entities`."""
    norm_index = name_index if name_index is not None else NameIndex(entities_reg)
    if judge is None:
        def judge(entity, existing_id):
            return _names_only_judge(entity, existing_id, entities_reg)

    remap: dict[str, str] = {}
    taken = set(entities_reg) | {e["id"] for e in incoming_entities}
    # The id each entity had when this call began: the key the caller's other fields still use.
    start = {id(e): e["id"] for e in incoming_entities}

    def _move(entity: dict, new_id: str) -> None:
        old = entity["id"]
        entity.setdefault("extracted_id", old)
        entity["id"] = new_id
        remap[start[id(entity)]] = new_id
        for other in incoming_entities:
            for role in other.get("roles", []):
                if role.get("target_id") == old:
                    role["target_id"] = new_id

    ordered = sorted(incoming_entities, key=lambda e: canonical_type(e.get("type", "")) == "person")
    for entity in ordered:
        if entity["id"] in entities_reg:
            if judge(entity, entity["id"]):
                continue
            new_id = _unique_entity_id(entity["id"], taken)
            taken.add(new_id)
            _move(entity, new_id)
        key = (normalize_entity_name(entity["name"]), canonical_type(entity["type"]))
        for existing_id in norm_index.get_all(key):
            if existing_id == entity["id"] or existing_id not in entities_reg:
                continue
            if judge(entity, existing_id):
                _move(entity, existing_id)
                # Preserve the variant spelling so the entity stays findable next time.
                entity.setdefault("aliases", []).append(entity["name"])
                break

    return {old: new for old, new in remap.items() if old != new}


def _resolve_role_targets(incoming_entities: list[dict], entities_reg: dict) -> None:
    """Re-inflate the role fields the extractor no longer emits.

    Extraction identifies each role's target by ``target_id`` only; ``target_name`` and
    ``target_type`` are derivable from the target entity (this batch's entities first, then
    the registry). Filling them in here — after id reconciliation, before anything reads
    roles — keeps the slim extraction wire format while leaving every downstream consumer
    (note rendering, pre-flight context, the synthesis digest) unchanged. A dangling target
    falls back to the id as name and ``Unknown`` as type.
    """
    lookup: dict[str, tuple] = {e["id"]: (e.get("name"), e.get("type")) for e in incoming_entities}
    for entity in incoming_entities:
        for role in entity.get("roles", []):
            tid = role.get("target_id")
            # Looked up per target rather than by copying the whole registry into `lookup` for
            # every document (#696); this batch's entities still take precedence.
            if tid not in lookup and tid in entities_reg:
                entry = entities_reg[tid]
                lookup[tid] = (entry.get("name"), entry.get("type"))
            name, typ = lookup.get(tid, (None, None))
            if not role.get("target_name"):
                role["target_name"] = name or tid or ""
            if not role.get("target_type"):
                role["target_type"] = typ or "Unknown"


def _frontmatter(data: dict) -> str:
    return "---\n" + yaml.dump(
        data, default_flow_style=False, allow_unicode=True, sort_keys=False
    ) + "---\n"


def _extract_section(content: str, section_name: str) -> str:
    """Return the body of a named ## section, stripped, or empty string."""
    header = f"## {section_name}"
    idx = content.find(header)
    if idx == -1:
        return ""
    start = idx + len(header)
    next_section = content.find("\n## ", start)
    body = content[start:next_section] if next_section != -1 else content[start:]
    return body.strip()


def _extract_notes_section(note_path: Path) -> str:
    """Return the ## Notes section and everything after it, or a default stub."""
    default = "\n## Notes\n\n<!-- Journalist annotations — never overwritten by ingestion. -->\n"
    if not note_path.exists():
        return default
    content = note_path.read_text(encoding="utf-8")
    idx = content.find("## Notes")
    return "\n" + content[idx:] if idx != -1 else default


def _extract_analysis(note_path: Path) -> str:
    """Return the existing ## Analysis body, or empty string."""
    if not note_path.exists():
        return ""
    return _extract_section(note_path.read_text(encoding="utf-8"), "Analysis")


def _extract_summary(note_path: Path) -> str | None:
    """Return the existing ## Summary body, or None if absent."""
    if not note_path.exists():
        return None
    text = _extract_section(note_path.read_text(encoding="utf-8"), "Summary")
    return text or None


def _extract_contradictions(note_path: Path) -> str:
    """Return the existing ## Contradictions body, or empty string."""
    if not note_path.exists():
        return ""
    return _extract_section(note_path.read_text(encoding="utf-8"), "Contradictions")


# Header a document contributes to an entity note's ## Analysis section, e.g.
# ``*3 May 2026, via [[documents/acme-annual-report|Acme Annual Report]]:*``. The doc-note
# link is stable per document (1:1 with its sha via the slug), so it keys the block for
# replace-not-append rewrites (#259).
_ANALYSIS_HEADER_RE = re.compile(r"^\*[^\n]*?via \[\[([^\]|]+)[|\]]", re.MULTILINE)


def _drop_analysis_entry(analysis: str, doc_note: str) -> str:
    """Remove any prior ## Analysis block this document contributed, so re-running write_vault
    for the same document replaces its entry instead of appending a duplicate (#259). Blocks are
    delimited by their ``*…via [[doc_note|…]]:*`` header; everything from one header up to the
    next is one document's contribution."""
    if not analysis:
        return analysis
    matches = list(_ANALYSIS_HEADER_RE.finditer(analysis))
    if not matches:
        return analysis
    kept = analysis[: matches[0].start()]  # preamble before the first header (normally empty)
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(analysis)
        if m.group(1) != doc_note:
            kept += analysis[m.start():end]
    return kept.strip()


# ── Timeline helpers ──────────────────────────────────────────────────────────

def _date_sort_key(date_str: str) -> str:
    """Pad date string for correct lexicographic chronological sorting."""
    if len(date_str) == 4:    # YYYY
        return date_str + "-00-00"
    if len(date_str) == 7:    # YYYY-MM
        return date_str + "-00"
    return date_str            # YYYY-MM-DD


def _render_date(date_str: str) -> str:
    """Format a date string for human-readable display."""
    try:
        if len(date_str) == 10:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return f"{dt.day} {dt.strftime('%b %Y')}"
        if len(date_str) == 7:
            return datetime.strptime(date_str, "%Y-%m").strftime("%B %Y")
    except ValueError:
        pass
    return date_str  # YYYY or unparseable — return as-is


def _timeline_dedup_key(event: dict) -> str:
    # Full text, not a prefix — a truncated key can collide on a long fact's shared opening
    # clause (e.g. "On March 3, 2019, Acme Corp transferred...") while the divergent, material
    # part of the sentence lands past the cutoff, silently losing the second event.
    return f"{event.get('date', '')}|{event.get('event', '').lower()}"


def _merge_timeline_events(existing: list[dict], incoming: list[dict], doc_sha256: str) -> list[dict]:
    """Merge new timeline events into existing list, deduplicating by (date, event text)."""
    existing_keys = {_timeline_dedup_key(e) for e in existing}
    result = list(existing)
    for event in incoming:
        key = _timeline_dedup_key(event)
        if key not in existing_keys:
            result.append({**event, "source_sha256": doc_sha256})
            existing_keys.add(key)
    return result


def _build_timeline_section(events: list[dict], docs_reg: dict) -> str:
    """Render an entity note's ``## Timeline`` section from its dated tagged facts, year-grouped
    and attributed to each event's source document. The *global* timeline is rendered separately
    from the cross-document-deduped NDJSON (`timeline.cmd_rebuild_timeline`, #237)."""
    if not events:
        return ""

    sorted_events = sorted(events, key=lambda e: _date_sort_key(e.get("date", "")))

    lines_by_year: dict[str, list[str]] = {}
    for ev in sorted_events:
        date_str = ev.get("date", "")
        year = date_str[:4] if date_str else "Unknown"
        rendered_date = _render_date(date_str)
        basis_note = " *(inferred)*" if ev.get("basis") == "inferred" else ""

        doc_entry = docs_reg.get(ev.get("source_sha256", ""), {})
        doc_note = doc_entry.get("document_note", "")
        doc_title = doc_entry.get("title") or doc_entry.get("filename", "")
        if doc_note and doc_title:
            pg = _page_link(doc_entry.get("morgue_path", ""), ev.get("page"))
            page_part = f", {pg}" if pg else ""
            source_part = f" — *[[{doc_note}|{_defang(doc_title)}]]{page_part}*"
        else:
            source_part = ""

        line = f"- **{rendered_date}** — {_defang(ev['event'])}{source_part}{basis_note}"
        lines_by_year.setdefault(year, []).append(line)

    sections = [f"### {year}\n" + "\n".join(lines_by_year[year]) for year in sorted(lines_by_year)]
    return "\n## Timeline\n\n" + "\n\n".join(sections) + "\n"


@contextmanager
def _registry_lock(registry_dir: Path, name: str = ".write-lock"):
    """Exclusive per-vault lock so concurrent write-vault calls serialize safely. `name` picks a
    different lock file for a store only its own commands write (the verification ledger, D271),
    so those writes don't wait on a long commit pass.

    Uses `fcntl.flock` on macOS/Linux (blocks indefinitely until acquired) and
    `msvcrt.locking` on Windows (locks a 1-byte region; blocks in ~1s retries, raising
    OSError after ~10s of contention rather than waiting indefinitely — a real
    behavioural difference from flock, not just a different API). If neither is
    available, this is a no-op and callers rely on in-process serialization only
    (D18) — cross-process writers are not locked out."""
    lock_path = registry_dir / name
    with open(lock_path, "w") as fh:
        if _HAS_FLOCK:
            _flock(fh, _LOCK_EX)
        elif _msvcrt is not None:
            fh.write(" ")
            fh.flush()
            fh.seek(0)
            _msvcrt.locking(fh.fileno(), _msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if _HAS_FLOCK:
                _flock(fh, _LOCK_UN)
            elif _msvcrt is not None:
                fh.seek(0)
                _msvcrt.locking(fh.fileno(), _msvcrt.LK_UNLCK, 1)


@contextmanager
def _try_registry_lock(registry_dir: Path, name: str = ".write-lock"):
    """`_registry_lock` without waiting: yields True holding the lock, or False at once when
    another writer holds it (or the platform has no lock to take)."""
    lock_path = registry_dir / name
    with open(lock_path, "w") as fh:
        got = False
        try:
            if _HAS_FLOCK:
                from fcntl import LOCK_NB
                _flock(fh, _LOCK_EX | LOCK_NB)
                got = True
            elif _msvcrt is not None:
                fh.write(" ")
                fh.flush()
                fh.seek(0)
                _msvcrt.locking(fh.fileno(), _msvcrt.LK_NBLCK, 1)
                got = True
        except OSError:
            got = False
        try:
            yield got
        finally:
            if got and _HAS_FLOCK:
                _flock(fh, _LOCK_UN)
            elif got and _msvcrt is not None:
                fh.seek(0)
                _msvcrt.locking(fh.fileno(), _msvcrt.LK_UNLCK, 1)


def _write_json_atomic(path: Path, data) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)   # rename() fails on Windows when the target exists


class RegistryBatch:
    """Hold the registries in memory across a commit pass and persist them every `flush_every`
    documents (D239), instead of re-reading and rewriting every registry file per document.

    The persist is still the commit point (D67): a document counts as committed only after a flush
    writes it, and `after_flush` callbacks (removing its queue file) run only then. A crash between
    flushes leaves those documents uncommitted and replayable; everything `run()` writes before the
    persist is replace-not-append. The registry lock is held for the whole batch."""

    def __init__(self, vault_path: Path, flush_every: int = 50):
        self.vault_path = vault_path
        self.registry_dir = vault_path / ".watchdog" / "registry"
        self.flush_every = flush_every
        self.entities: dict = {}
        self.documents: dict = {}
        self._pending = 0
        self._after_flush: list = []
        self._lock = None
        self._undo: tuple | None = None
        self._merge_log: list[dict] = []     # merge-log entries of the documents written so far
        self._merge_log_mark = 0
        self._note_ids: set[str] = set()     # entities whose notes the next flush renders (D280)
        self._overrides: dict[str, dict] = {}
        self._pending_notes: tuple | None = None

    def __enter__(self) -> "RegistryBatch":
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        self._lock = _registry_lock(self.registry_dir)
        self._lock.__enter__()
        try:
            for attr, name in (("entities", "entities.json"), ("documents", "documents.json")):
                path = self.registry_dir / name
                setattr(self, attr, json.loads(path.read_text()) if path.exists() else {})
        except BaseException:
            self._lock.__exit__(None, None, None)
            raise
        return self

    def begin(self, entity_ids: set[str], doc_sha256: str) -> None:
        """Snapshot the registry entries one document may change, so `rollback` can undo a write
        that fails partway. Without a batch a failed write never persisted anything — its edits
        lived in a registry copy it then discarded — and a batch must not let the next flush write
        them either. Only the entries a write can touch are copied: the document's own entities,
        the targets its roles point at (`_add_reverse_role`), and its own documents entry."""
        missing = object()
        self._merge_log_mark = len(self._merge_log)
        self._undo = (
            {eid: deepcopy(self.entities[eid]) if eid in self.entities else missing
             for eid in entity_ids},
            doc_sha256,
            deepcopy(self.documents[doc_sha256]) if doc_sha256 in self.documents else missing,
            missing,
        )

    def rollback(self) -> None:
        """Restore the entries `begin` snapshotted; a no-op when nothing is open."""
        if self._undo is None:
            return
        entities, doc_sha256, document, missing = self._undo
        del self._merge_log[self._merge_log_mark:]
        self._pending_notes = None
        for eid, entry in entities.items():
            if entry is missing:
                self.entities.pop(eid, None)
            else:
                self.entities[eid] = entry
        if document is missing:
            self.documents.pop(doc_sha256, None)
        else:
            self.documents[doc_sha256] = document
        self._undo = None

    def committed(self, after_flush=None) -> None:
        """Record one document written; flush once `flush_every` have accumulated. `after_flush`
        runs once that document's registry entries are on disk."""
        self._undo = None
        if self._pending_notes is not None:
            ids, sha, extraction = self._pending_notes
            self._note_ids |= ids
            self._overrides[sha] = extraction
            self._pending_notes = None
        self._pending += 1
        if after_flush is not None:
            self._after_flush.append(after_flush)
        if self._pending >= self.flush_every:
            self.flush()

    def notes_for(self, entity_ids: set[str], doc_sha256: str, extraction: dict) -> None:
        """Queue the notes one document's write touched, rendered at the flush that commits it."""
        self._pending_notes = (set(entity_ids), doc_sha256, extraction)

    def render_notes(self) -> list[tuple[str, str, str]]:
        """Render every queued entity note from the batch's in-memory registries (D280)."""
        from watchdog.pipeline import entity_facts, entity_notes
        self._note_ids |= {e for e in entity_notes.take_stale(self.vault_path) if e in self.entities}
        if not self._note_ids:
            return []
        index = entity_facts.FactIndex(self.vault_path, self.entities, self.documents,
                                       overrides=self._overrides)
        written = entity_notes.write_entities(self.vault_path, self._note_ids, self.entities,
                                              self.documents, index=index)
        self._note_ids, self._overrides = set(), {}
        return written

    def log_merges(self, entries: list[dict]) -> None:
        """Queue a document's merge-log entries (D279) for the flush that commits it."""
        self._merge_log.extend(entries)

    def flush(self) -> None:
        if not self._pending:
            return
        # Before the registries: a crash in between leaves entries for documents a replay commits
        # again, and `merge_log.record` is idempotent, so the log never misses a committed merge.
        written = self.render_notes()
        logged = bool(self._merge_log)
        if logged:
            from watchdog.pipeline import merge_log
            merge_log.record(self.vault_path, self._merge_log, render_note=False)
            self._merge_log = []
        _persist_registries(self.vault_path, self.entities, self.documents)
        if logged:
            merge_log.render(self.vault_path)     # after the registries, so it names the documents
        if written:
            from watchdog.pipeline import entity_notes
            entity_notes.index_notes(self.vault_path, written)
        self._pending = 0
        callbacks, self._after_flush = self._after_flush, []
        for callback in callbacks:
            callback()

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self.flush()
        finally:
            self._lock.__exit__(exc_type, exc, tb)


def _persist_registries(vault_path: Path, entities_reg: dict, documents_reg: dict) -> None:
    """The commit point (D67): every registry file, each atomically, entities and documents first."""
    registry_dir = vault_path / ".watchdog" / "registry"
    registry_path = registry_dir / "registry.json"
    _write_json_atomic(registry_dir / "entities.json", entities_reg)
    _write_json_atomic(registry_dir / "documents.json", documents_reg)
    existing_registry = (
        _read_json_or(registry_path, {}, catch=(json.JSONDecodeError,))
        if registry_path.exists() else {}
    )
    existing_registry.update({
        "last_updated":   _now_iso(),
        "document_count": len(documents_reg),
        "entity_count":   len(entities_reg),
    })
    _write_json_atomic(registry_path, existing_registry)
    _update_manifest(vault_path, entities_reg)


def _update_manifest(vault_path: Path, entities_reg: dict) -> None:
    """Write a lightweight lookup index: id → name, type, aliases, note_path only."""
    manifest = {
        eid: {
            "name":      entry["name"],
            "type":      entry["type"],
            "aliases":   entry.get("aliases", []),
            "note_path": entry["note_path"],
        }
        for eid, entry in entities_reg.items()
    }
    manifest_path = vault_path / ".watchdog" / "registry" / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ── Relationship helpers ──────────────────────────────────────────────────────

def _page_link(morgue_path: str, page: int | None) -> str:
    """Return a clickable page link if both path and page are known, else plain text."""
    if morgue_path and page:
        return f"[[{morgue_path}#page={page}|p. {page}]]"
    if page:
        return f"p. {page}"
    return ""


def _role_line(role: dict, docs_reg: dict) -> str:
    """Format a role dict as a Markdown relationship line with pretty links."""
    date_part = f" — {role['date_range']}" if role.get("date_range") else ""
    basis_part = " *(inferred)*" if role.get("basis") == "inferred" else ""

    # target_id sits in the wikilink *target* position. It's postflight-slugified for a profiled
    # target, but a role can point at an unprofiled entity (leads.py: "named but never profiled"),
    # whose target_id never passed through that pass — slugify here so a hostile dangling id can't
    # close the wikilink early and forge a second one (#305, target side).
    target_link = f"[[entities/{_type_dir(role['target_type'])}/{slugify(role['target_id'])}|{_defang(role['target_name'])}]]"

    source_sha = role.get("source_sha256", "")
    doc_entry = docs_reg.get(source_sha, {})
    doc_note = doc_entry.get("document_note", "")
    doc_title = doc_entry.get("title") or doc_entry.get("filename", "")

    if doc_note and doc_title:
        pg = _page_link(doc_entry.get("morgue_path", ""), role.get("page"))
        page_part = f", {pg}" if pg else ""
        source_part = f" — via [[{doc_note}|{_defang(doc_title)}]]{page_part}"
    else:
        pg = _page_link("", role.get("page"))
        source_part = f" — {pg}" if pg else ""

    relationship = _defang(role["relationship"])
    if role.get("is_reverse"):
        return f"- {target_link} — {relationship}{date_part}{basis_part}{source_part}"
    else:
        return f"- {relationship} {target_link}{date_part}{basis_part}{source_part}"


# ── Entity registry operations ────────────────────────────────────────────────

def _new_entity(entity: dict, doc_sha256: str) -> dict:
    roles = [
        {**r, "source_sha256": doc_sha256, "is_reverse": False}
        for r in entity.get("roles", [])
    ]
    events = [
        {**e, "source_sha256": doc_sha256}
        for e in entity.get("timeline_events", [])
    ]
    return {
        "id":               entity["id"],
        "name":             entity["name"],
        "type":             entity["type"],
        "aliases":          list(entity.get("aliases", [])),
        "appears_in":       [doc_sha256],
        "note_path":        f"entities/{_type_dir(entity['type'])}/{entity['id']}",
        "roles":            roles,
        "timeline_events":  events,
        "contradictions":   [c.strip() for c in entity.get("contradictions") or [] if c.strip()],
        "date_first_seen":  _today(),
        "date_last_updated": _today(),
    }


def _merge_entity(existing: dict, incoming: dict, doc_sha256: str) -> None:
    """Mutate existing registry entry with data from incoming extraction entity."""
    known_lower = {a.lower() for a in existing.get("aliases", [])}
    for alias in incoming.get("aliases", []):
        if alias.lower() not in known_lower and alias.lower() != existing["name"].lower():
            existing.setdefault("aliases", []).append(alias)
            known_lower.add(alias.lower())

    if doc_sha256 not in existing.get("appears_in", []):
        existing.setdefault("appears_in", []).append(doc_sha256)

    existing_role_keys = {
        (r["relationship"].lower(), r["target_id"])
        for r in existing.get("roles", [])
    }
    for role in incoming.get("roles", []):
        key = (role["relationship"].lower(), role["target_id"])
        if key not in existing_role_keys:
            existing.setdefault("roles", []).append(
                {**role, "source_sha256": doc_sha256, "is_reverse": False}
            )
            existing_role_keys.add(key)

    existing["timeline_events"] = _merge_timeline_events(
        existing.get("timeline_events", []),
        incoming.get("timeline_events", []),
        doc_sha256,
    )

    existing_contradictions = existing.setdefault("contradictions", [])
    for callout in incoming.get("contradictions") or []:
        callout = callout.strip()
        if callout and callout not in existing_contradictions:
            existing_contradictions.append(callout)

    existing["date_last_updated"] = _today()


def _add_reverse_role(
    entities_reg: dict,
    from_entity: dict,
    role: dict,
    doc_sha256: str,
    modified: set,
) -> None:
    target_id = role.get("target_id")
    if not target_id or target_id not in entities_reg:
        return

    target = entities_reg[target_id]
    reverse_key = (role["relationship"].lower(), from_entity["id"])
    existing_keys = {
        (r["relationship"].lower(), r["target_id"])
        for r in target.get("roles", [])
    }
    if reverse_key in existing_keys:
        return

    target.setdefault("roles", []).append({
        "relationship":  role["relationship"],
        "target_id":     from_entity["id"],
        "target_type":   from_entity["type"],
        "target_name":   from_entity["name"],
        "page":          role.get("page"),
        "basis":         role.get("basis", "stated"),
        "date_range":    role.get("date_range"),
        "source_sha256": doc_sha256,
        "is_reverse":    True,
    })
    target["date_last_updated"] = _today()
    modified.add(target_id)


# ── Note builders ─────────────────────────────────────────────────────────────

def _write_morgue_markdown(vault_path: Path, sha256: str, morgue_dir: Path, stem: str) -> None:
    """Write the Docling per-page markdown next to the original in the morgue (#140).

    Best-effort: the page markdown lives in the chew-time queue descriptor, which is present during
    ingest but gone on a re-run from disk (`watchdog bark`); skip silently if unavailable.
    Pages are joined with `<!-- PAGE N -->` markers so the file is both greppable and page-aligned.
    """
    queue_file = vault_path / ".watchdog" / "queue" / f"{sha256}.json"
    pages = _read_json_or(queue_file, {}).get("pages", [])
    if not pages:
        return
    body = "\n\n".join(
        f"<!-- PAGE {p.get('page')} -->\n\n{p.get('markdown', '')}".rstrip() for p in pages
    )
    try:
        (morgue_dir / f"{stem}.md").write_text(body + "\n", encoding="utf-8")
    except OSError:
        pass


def _index_corpus_passages(vault_path: Path, doc: dict, entity_entries: list[dict],
                            morgue_path: str = "", filename_shared: bool = False) -> None:
    """Embed this document's source passages into the semantic index, with a contextual
    prefix built from what extraction produced — the document's title, type, and the
    entities it names. The prefix anchors a passage that lacks the document's who/what,
    improving retrieval (Anthropic contextual-retrieval). Also indexes the same raw pages
    into the full-text (exact-term) index (#109) — the two lanes are built from the same
    page text but serve different recall needs. Best-effort: the page text lives in the
    chew-time queue descriptor, gone on a finalize re-run from disk; skip if absent.
    """
    sha256 = doc["sha256"]
    queue_file = vault_path / ".watchdog" / "queue" / f"{sha256}.json"
    if not queue_file.exists():
        return
    pages = json.loads(queue_file.read_text(encoding="utf-8")).get("pages", [])
    if not pages:
        return
    names = [e.get("name", "") for e in entity_entries if e.get("name")][:20]
    title = doc.get("title") or doc.get("filename", "")
    dtype = doc.get("document_type") or "document"
    context = f"{title} — {dtype}."
    if names:
        context += " Mentions: " + ", ".join(names) + "."
    from watchdog.pipeline.embed import add_document
    add_document(vault_path, doc["filename"], pages, context=context, sha256=sha256,
                 drop_legacy=not filename_shared)
    from watchdog.pipeline.fulltext import add_document as fts_add_document
    fts_add_document(vault_path, doc["filename"], sha256, pages, morgue_path=morgue_path)


def _quote_verification_note(f: dict) -> str:
    """Suffix for a rendered quote, from the deterministic post-flight check (#267).

    ``quote_verified is False`` means the quote couldn't be matched on or near its cited
    page. ``quote_found_page`` means it was only found (via a normalized match) on a
    different page than cited — checked against `f["page"]` itself, not assumed to still
    differ, since post-flight corrects the citation to `quote_found_page` when that page is
    unique across the whole document (#560); after a correction the two agree and there is
    nothing left to flag. ``quote_spans_pages`` means the source sentence crosses a page break
    and no single page contains it, so the citation is left as the model wrote it. None of these
    keys present means either verification wasn't run (e.g. no page text available) or the quote
    matched exactly — nothing to flag.
    """
    if f.get("quote_verified") is False:
        return " *(quote not found on cited page — verify against source)*"
    spans_pages = f.get("quote_spans_pages")
    if spans_pages:
        return f" *(quote spans pages {spans_pages[0]}–{spans_pages[1]})*"
    found_page = f.get("quote_found_page")
    if found_page is not None and found_page != f.get("page"):
        return f" *(found on p. {found_page}, not the cited page)*"
    return ""


def _group_digits(token: str) -> str:
    """Re-comma a normalized figure token for display — the check strips grouping to compare,
    but a reporter reads 173,471, not 173471. Left alone if it isn't a plain integer."""
    int_part, _, dec_part = token.partition(".")
    if not int_part.isdigit():
        return token
    grouped = f"{int(int_part):,}"
    return f"{grouped}.{dec_part}" if dec_part else grouped


def _figure_verification_note(f: dict) -> str:
    """Suffix for a rendered fact, from the deterministic post-flight figure check (#363/#623).

    ``figures_unverified`` are figures found nowhere in the document — computed or garbled,
    and the reporter should check the source. ``figures_off_page`` are figures that are real
    but sit on a page other than the one cited; unlike a quote locator, a single off-page
    figure isn't strong enough evidence to correct the citation (a fact may legitimately draw
    figures from several pages), so it is flagged and the citation left alone. Neither key
    present means the fact's figures checked out, or that there was nothing to check.
    """
    parts = []
    missing = f.get("figures_unverified")
    if missing:
        figs = ", ".join(_group_digits(t) for t in missing)
        parts.append(f"figure{'s' if len(missing) > 1 else ''} {figs} not found in the document "
                     "— may be derived; verify against source")
    off_page = f.get("figures_off_page")
    if off_page:
        detail = ", ".join(
            f"{_group_digits(t)} (p. {'/'.join(str(p) for p in pages)})"
            for t, pages in off_page.items()
        )
        parts.append(f"figure{'s' if len(off_page) > 1 else ''} {detail} found on another page, "
                     "not the one cited")
    return f" *({'; '.join(parts)})*" if parts else ""


def _render_evidence_fragments(fragments: list, morgue_path: str = "") -> str:
    """Render evidence-fragment claims as Markdown bullets.

    Each claim becomes a bullet with an optional page link and `— reason`; an optional quote —
    resolved from the page text at post-flight (#529), not model-supplied — renders as a
    blockquote beneath it. With no morgue_path, pages render as plain "p. N" (used for the
    finalizer digest, which needs no clickable links).
    """
    lines = []
    for f in fragments:
        claim = _defang((f.get("claim") or "").strip())
        if not claim:
            continue
        pg = _page_link(morgue_path, f.get("page"))
        page = f" ({pg})" if pg else ""
        reason = f" — {_defang(f['reason'].strip())}" if f.get("reason") else ""
        basis_note = " *(inferred)*" if f.get("basis") == "inferred" else ""
        line = f"- {claim}{page}{reason}{basis_note}{_figure_verification_note(f)}"
        quote = _defang((f.get("quote") or "").strip())
        if quote:
            line += f"\n  > {quote}{_quote_verification_note(f)}"
        lines.append(line)
    return "\n".join(lines)


def build_entity_note(
    entry: dict,
    notes_section: str,
    docs_reg: dict,
    summary: str | None,
    accumulated_analysis: str,
    contradictions: str = "",
) -> str:
    appears_in_links = []
    for sha in entry.get("appears_in", []):
        doc_entry = docs_reg.get(sha, {})
        note = doc_entry.get("document_note")
        title = doc_entry.get("title") or doc_entry.get("filename", "")
        appears_in_links.append(f"[[{note}|{_defang(title)}]]" if note and title else sha[:16] + "…")

    fm = _frontmatter({
        "id":               entry["id"],
        "name":             entry["name"],
        "type":             entry["type"],
        "aliases":          entry.get("aliases", []),
        "appears_in":       appears_in_links,
        "date_first_seen":  entry.get("date_first_seen", _today()),
        "date_last_updated": entry.get("date_last_updated", _today()),
    })

    body = f"\n# {_defang(entry['name'])}\n"

    if summary:
        body += f"\n## Summary\n\n{summary}\n"

    if accumulated_analysis:
        body += f"\n## Analysis\n\n{accumulated_analysis}\n"

    if contradictions:
        body += f"\n## Contradictions\n\n{contradictions}\n"

    timeline_section = _build_timeline_section(entry.get("timeline_events", []), docs_reg)
    if timeline_section:
        body += timeline_section

    roles = entry.get("roles", [])
    if roles:
        lines = "\n".join(_role_line(r, docs_reg) for r in roles)
        body += f"\n## Relationships\n\n{lines}\n"

    return fm + body + notes_section


def _build_document_note(doc: dict, entity_entries: list[dict], morgue_path: str | None = None,
                         rec: dict | None = None, old: str | None = None,
                         sha: str | None = None) -> str:
    """A document note from its extraction. `rec` (the registry entry) supplies the ingestion date,
    so a rebuild gives the same note; `old` (the note on disk) supplies the journalist's Notes,
    which are carried over unchanged (D280). With the document's `sha`, each fact line ends in its
    block id (`^f-<hash>`), the target of every fact citation (D283)."""
    ingested = ((rec or {}).get("ingested_at") or "")[:10] or _today()
    fm = _frontmatter({
        "title":            doc.get("title", doc["filename"]),
        "type":             "Document",
        "document_type":    doc.get("document_type"),
        "file":             doc["filename"],
        "date_of_document": doc.get("date_of_document"),
        "date_ingested":    ingested,
        "source":           doc.get("source"),
        "obtained":         doc.get("obtained"),
        "entities_mentioned": [
            f"[[entities/{_type_dir(e['type'])}/{e['id']}|{_defang(e['name'])}]]"
            for e in entity_entries
        ],
        "page_count":       doc.get("page_count"),
        "near_duplicate_of": doc.get("near_duplicate_of"),
        "record_skill":     doc.get("record_skill"),
        "record_skill_hash": doc.get("record_skill_hash"),
        "extract_model":    doc.get("extract_model"),
        "extract_effort":   doc.get("extract_effort"),
    })

    body = ""
    if morgue_path:
        body += f"\n**Source file:** [[{morgue_path}]]\n"
        md_path = str(Path(morgue_path).with_suffix(".md"))
        body += f"\n**Full text:** [[{md_path}]]\n"

    body += f"\n## Summary\n\n{_defang_links(doc.get('summary', ''))}\n"

    key_facts = doc.get("key_facts", [])
    if key_facts:
        body += "\n## Key facts\n\n"
        ids: list[str | None] = [None] * len(key_facts)
        if sha:
            from watchdog.pipeline.citations import block_id
            from watchdog.pipeline.verification import fact_ids
            kept = [i for i, kf in enumerate(key_facts)
                    if isinstance(kf, dict) and (kf.get("fact") or "").strip()]
            for i, fid in zip(kept, fact_ids(sha, [key_facts[i] for i in kept])):
                ids[i] = f" ^{block_id(fid)}"
        for kf, bid in zip(key_facts, ids):
            pg = _page_link(morgue_path or "", kf.get("page"))
            page = f" ({pg})" if pg else ""
            basis_note = " *(inferred)*" if kf.get("basis") == "inferred" else ""
            body += f"- {_defang(kf['fact'])}{page}{basis_note}{_figure_verification_note(kf)}{bid or ''}\n"
            quote = _defang((kf.get("quote") or "").strip())
            if quote:
                body += f"  > {quote}{_quote_verification_note(kf)}\n"

    if entity_entries:
        body += "\n## Entities mentioned\n\n"
        for e in entity_entries:
            body += f"- [[entities/{_type_dir(e['type'])}/{e['id']}|{_defang(e['name'])}]]\n"

    from watchdog.pipeline.entity_notes import _NOTES_RE
    m = _NOTES_RE.search(old or "")
    if m:
        body += "\n" + old[m.start():]
    else:
        body += "\n## Notes\n\n<!-- Reserved for journalist annotations — never overwritten by ingestion. -->\n"

    return fm + body


# ── Main operation ────────────────────────────────────────────────────────────

def run(extraction_path: Path, vault_path: Path, neardup_file: Path | None = None, neardup_data: dict | None = None, quiet: bool = False,
        batch: RegistryBatch | None = None) -> dict:
    """Commit one staged extraction to the vault. With `batch` (#696), the registries are read
    from and left in the batch's memory — persisting them is the batch's flush, not this call's —
    and the batch already holds the registry lock."""
    raw = extraction_path.read_text(encoding="utf-8")
    extraction = json.loads(raw)
    doc = extraction.get("document")
    if not doc:
        sys.exit(f"Error: extraction JSON missing required 'document' key ({extraction_path.name})")
    # The stored extraction is the record every entity note is rendered from (D280), so a document
    # committed from anywhere else is kept there too, as committed.
    stored = vault_path / ".watchdog" / "extracted" / f"{doc.get('sha256')}.json"
    if doc.get("sha256") and extraction_path.resolve() != stored.resolve():
        stored.parent.mkdir(parents=True, exist_ok=True)
        stored.write_text(raw, encoding="utf-8")
    incoming_entities = extraction.get("entities", [])
    # Collapse each entity's model-invented type onto the closed vocabulary (#335) before it
    # becomes load-bearing — the stored registry type, the `entities/<type>/` folder, the note
    # frontmatter, and the graph colour group all follow from this single canonical value.
    for _entity in incoming_entities:
        _entity["type"] = canonical_type(_entity.get("type", ""))
    doc_sha256 = doc["sha256"]
    slug = _doc_slug(doc["filename"])
    doc_title = doc.get("title", doc["filename"])

    registry_dir = vault_path / ".watchdog" / "registry"
    registry_dir.mkdir(parents=True, exist_ok=True)

    with (nullcontext() if batch is not None else _registry_lock(registry_dir)):
        entities_path  = registry_dir / "entities.json"
        documents_path = registry_dir / "documents.json"
        log_path       = registry_dir / "processing.log"   # vault_paths.processing_log (D276)

        if batch is not None:
            entities_reg, documents_reg = batch.entities, batch.documents
        else:
            entities_reg  = json.loads(entities_path.read_text())  if entities_path.exists()  else {}
            documents_reg = json.loads(documents_path.read_text()) if documents_path.exists() else {}

        base_slug = slug
        slug = _unique_doc_slug(vault_path, slug, doc_sha256, doc["filename"], documents_reg)
        if slug != base_slug and not quiet:
            print(f"WARN  slug collision — using documents/{slug}.md for {doc['filename']}")

        # Resolved-contradiction overlay (#266): callouts the journalist has acknowledged are
        # dropped from the rendered note body. The registry keeps the full list, so unresolving
        # restores them on the next write.
        from watchdog.pipeline import requests, resolutions
        resolved_ids = resolutions.resolved_ids(vault_path)

        morgue_relative = _unique_morgue_path(
            vault_path,
            f"morgue/{extraction.get('morgue_entity_id', 'unknown')}"
            f"/{extraction.get('morgue_document_type', 'document')}",
            doc["filename"], doc_sha256, documents_reg,
        )

        # ── 1. Update entity registry ─────────────────────────────────────────

        # Near-duplicate slugs coined by concurrent extraction tasks are already reconciled by
        # this point — the batch-wide pre-commit fold (#403 phase 2, orchestrate._batch_exact_fold)
        # ran over the staged extraction JSON before any commit began.
        # Roles arrive as target_id only; re-inflate target_name/target_type deterministically.
        _resolve_role_targets(incoming_entities, entities_reg)
        if batch is not None:
            batch.begin(
                {e["id"] for e in incoming_entities}
                | {r["target_id"] for e in incoming_entities for r in e.get("roles", [])
                   if r.get("target_id")},
                doc_sha256,
            )

        modified: set[str] = set()
        # Which entities this document *introduced* vs. added to — decided here, where it is
        # simply a fact about the registry, and reported back for the briefing (#381/D118). This
        # used to be read off the extractor's `match_id`, i.e. the model's belief about what it
        # had seen before, which was wrong whenever the entity it matched had not been written yet.
        new_ids: list[str] = []
        updated_ids: list[str] = []

        for entity in incoming_entities:
            eid = entity["id"]
            if eid in entities_reg:
                _merge_entity(entities_reg[eid], entity, doc_sha256)
                updated_ids.append(eid)
            else:
                entities_reg[eid] = _new_entity(entity, doc_sha256)
                new_ids.append(eid)
            modified.add(eid)

        for entity in incoming_entities:
            reg_entry = entities_reg[entity["id"]]
            for role in entity.get("roles", []):
                _add_reverse_role(entities_reg, reg_entry, role, doc_sha256, modified)

        # ── 2. Update document registry ──────────────────────────────────────

        # Prefer minhash from neardup_data dict, then sidecar file, then extraction field.
        if neardup_data and neardup_data.get("candidate_minhash"):
            sig = neardup_data["candidate_minhash"]
        elif neardup_file and neardup_file.exists():
            try:
                sig = json.loads(neardup_file.read_text()).get("candidate_minhash", [])
            except Exception:
                sig = doc.get("minhash", [])
        else:
            sig = doc.get("minhash", [])

        documents_reg[doc_sha256] = {
            "sha256":           doc_sha256,
            "filename":         doc["filename"],
            "title":            doc_title,
            "original_path":    doc.get("original_path", f"incoming/{doc['filename']}"),
            "document_note":    f"documents/{slug}",
            "ingested_at":      _now_iso(),
            "page_count":       doc.get("page_count"),
            "document_type":    doc.get("document_type"),
            "record_skill":     doc.get("record_skill"),
            "record_skill_hash": doc.get("record_skill_hash"),
            "extract_model":    doc.get("extract_model"),
            "extract_effort":   doc.get("extract_effort"),
            "file_metadata":    doc.get("file_metadata") or {},
            "coverage_gap":     doc.get("coverage_gap"),
            "entities_extracted": [e["id"] for e in incoming_entities],
            "near_duplicate_of": doc.get("near_duplicate_of"),
            "minhash":          sig,
            "morgue_path":      morgue_relative,
        }
        if isinstance(doc.get("media"), dict):
            documents_reg[doc_sha256]["media"] = doc["media"]   # audio/video only (D273)

        # ── 2b. Record document requests ──────────────────────────────────────
        #
        # The model authors what/why/likely_source in the extraction; Python stamps the rid and
        # source provenance (§I1) — the same code/model split as the registries above. `record`
        # dedups by rid, so a repair retry of this document converges instead of duplicating.
        requests.record(
            vault_path, extraction.get("document_requests") or [],
            sha256=doc_sha256, filename=doc["filename"],
            document_note=documents_reg[doc_sha256]["document_note"],
        )

        # ── 3. Entity notes ───────────────────────────────────────────────────
        #
        # An entity note is a view of stored data (D280): its Facts section is rendered from every
        # stored extraction that tags the entity, so it is rebuilt whole, never appended to. In a
        # batch the notes of every entity a flush covers are rendered once, at that flush, before
        # the registries persist; alone, they are rendered here. Either way a repair retry
        # converges, since the render reads only data.

        # (note_path, kind, title, content) tuples replayed into the embed + FTS indexes
        # after the commit point.
        note_index_jobs: list[tuple[str, str, str, str]] = []
        if batch is None:
            from watchdog.pipeline import entity_facts, entity_notes
            index = entity_facts.FactIndex(vault_path, entities_reg, documents_reg,
                                           overrides={doc_sha256: extraction})
            for np_, name, content in entity_notes.write_entities(
                    vault_path, modified, entities_reg, documents_reg, index=index,
                    resolved=resolved_ids):
                note_index_jobs.append((np_, "entity", name, content))

        # ── 4. Write document note ────────────────────────────────────────────

        doc_note_path = _assert_in_vault(
            vault_path / "documents" / f"{slug}.md", vault_path, "document note_path"
        )
        doc_note_path.parent.mkdir(parents=True, exist_ok=True)
        entity_entries_for_note = [entities_reg[e["id"]] for e in incoming_entities if e["id"] in entities_reg]
        old_doc_note = doc_note_path.read_text(encoding="utf-8") if doc_note_path.exists() else None
        doc_note_content = _build_document_note(doc, entity_entries_for_note, morgue_relative,
                                                rec=documents_reg[doc_sha256], old=old_doc_note,
                                                sha=doc_sha256)
        doc_note_path.write_text(doc_note_content, encoding="utf-8")
        note_index_jobs.append((f"documents/{slug}", "document", doc_title, doc_note_content))

        # ── 5. Persist registries (atomic temp-then-rename) ──────────────────
        #
        # Inside a RegistryBatch (#696) this is the batch's flush, every `flush_every` documents.

        identity_log = (extraction.get("identity") or {}).get("log") or []
        if batch is None:
            if identity_log:
                from watchdog.pipeline import merge_log
                merge_log.record(vault_path, identity_log, render_note=False)
            _persist_registries(vault_path, entities_reg, documents_reg)
            if identity_log:
                merge_log.render(vault_path)
        elif identity_log:
            batch.log_merges(identity_log)
        if batch is not None:
            batch.notes_for(modified, doc_sha256, extraction)

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(
                f"[{_now_iso()}] INGEST \"{doc['filename']}\" "
                f"sha256={doc_sha256} "
                f"entities={len(incoming_entities)} "
                f"type={doc.get('document_type', 'unknown')}\n"
            )

        # ── 6. Update derived data (search indexes) ───────────────────────────
        #
        # These are rebuilt-from-source: the embed/FTS indexes are keyed by note_path (upsert),
        # so replaying them after the commit point is idempotent — a repair retry converges
        # instead of doubling (#259). Kept inside the registry lock so writes stay race-free
        # with other write_vault calls.
        for idx_note_path, kind, idx_title, idx_content in note_index_jobs:
            try:
                from watchdog.pipeline.embed import add_note
                add_note(vault_path, idx_note_path, idx_content)
            except Exception as e:
                print(f"  Warning: embed index update failed for {idx_note_path}: {e}", file=sys.stderr)
            try:
                from watchdog.pipeline.fulltext import add_note as fts_add_note
                fts_add_note(vault_path, idx_note_path, kind, idx_title, idx_content)
            except Exception as e:
                print(f"  Warning: full-text index update failed for {idx_note_path}: {e}", file=sys.stderr)

        # Index the source passages (corpus stream) with a contextual prefix — now that
        # extraction has supplied the title, type, and entities the prefix needs (D43).
        try:
            filename_shared = any(e.get("filename") == doc["filename"]
                                  for s, e in documents_reg.items() if s != doc_sha256)
            _index_corpus_passages(vault_path, doc, entity_entries_for_note,
                                   morgue_path=morgue_relative, filename_shared=filename_shared)
        except Exception as e:
            print(f"  Warning: corpus index update failed for {doc['filename']}: {e}", file=sys.stderr)

    # The global timeline is no longer rebuilt per document (#237): it is rendered
    # exclusively from the cross-document-deduped canonical NDJSON, which only exists after
    # `_post_ingest` runs the dedup pass at the end of a batch. A standalone write-vault
    # therefore leaves timeline.md to the next ingest run or explicit `watchdog timeline`.

    # ── 7. Move source file to morgue ─────────────────────────────────────────

    morgue_dir = _assert_in_vault(
        vault_path / Path(morgue_relative).parent, vault_path, "morgue path"
    )
    morgue_dir.mkdir(parents=True, exist_ok=True)

    source = vault_path / modernize_path(doc.get("original_path", f"incoming/{doc['filename']}"))
    if source.exists():
        morgue_name = Path(morgue_relative).name
        shutil.move(str(source), str(morgue_dir / morgue_name))
        # No sidecar file survives past chew (D121) — it's filtered/allowlisted into the queue
        # JSON there and carried onto `doc["sidecar"]` by orchestrate._stamp_document. Re-write it
        # as a .yml here so morgue still keeps a permanent, citable copy alongside the source.
        sidecar_text = doc.get("sidecar")
        if sidecar_text:
            (morgue_dir / f"{morgue_name}.yml").write_text(sidecar_text, encoding="utf-8")
        # Preserve the Docling text alongside the original so the full document stays greppable in
        # the vault — extraction now indexes this substrate rather than restating it (#140).
        _write_morgue_markdown(vault_path, doc_sha256, morgue_dir, Path(morgue_name).stem)

        incoming_root = incoming_dir(vault_path)
        parent = source.parent
        while parent != incoming_root and parent.is_relative_to(incoming_root):
            try:
                parent.rmdir()
                parent = parent.parent
            except OSError:
                break

        staging_dir = vault_path / ".watchdog" / "staging"
        if parent.parent == staging_dir:
            try:
                parent.rmdir()
            except OSError:
                pass

    if not quiet:
        print(
            f"OK  {doc['filename']}  "
            f"entities={len(incoming_entities)}  "
            f"doc=documents/{slug}"
        )

    return {"new_entities": new_ids, "updated_entities": updated_ids}
