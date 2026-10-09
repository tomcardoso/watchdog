"""An entity's facts, read from the stored extractions (D280).

Every fact lives once, in the document's staged extraction (`.watchdog/extracted/<sha>.json`,
`document.key_facts`), tagged with the ids of the entities it is about (D26). An entity's facts are
the facts, across every committed document it appears in, whose tags name it. This module reads
them; `entity_notes` renders an entity note from them, and synthesis and the contradiction check
read them instead of an earlier note's prose.

A tag names an entity by the id it had when the document was committed. Merges since D280 rewrite
the tags in every extraction they touch, so a tag is normally a current id. A merge made before
that (D279, `merge_entities.run` over two committed records) left older extractions naming the
merged-away id; `resolve` follows the merge log from that id to the record that absorbed it, and
only to a record that lists the document. A merge marked undone is not followed.

Each fact carries its D271 id (`verification.fact_ids`, over the same filtered list the app and
the ledger use), its page and flags, its D270 passage when the document's passages are readable,
the document it came from, and the reporter's current mark. Nothing here writes anything.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from watchdog.pipeline.json_io import _read_json_or

# Parsed extractions, keyed by path and invalidated by size and modification time. Only the fields
# this module needs are kept, so a vault of thousands of documents stays small in memory.
_CACHE: dict[str, tuple[tuple[int, int], dict | None]] = {}
_CACHE_MAX = 4096


def _registry(vault: Path) -> Path:
    return Path(vault) / ".watchdog" / "registry"


def _slim(artifact: dict | None) -> dict | None:
    """The parts of an extraction an entity's facts need: the document's facts (empty ones dropped,
    as `verification.document_facts` drops them, so ids number duplicates the same way) and the
    entity records' ids."""
    if not isinstance(artifact, dict):
        return None
    doc = artifact.get("document")
    if not isinstance(doc, dict) or not isinstance(doc.get("key_facts"), list):
        return None
    from watchdog.pipeline import passages
    keep_passages = passages.readable(doc)
    facts = []
    for f in doc["key_facts"]:
        if not isinstance(f, dict) or not (f.get("fact") or "").strip():
            continue
        f = dict(f)
        if not keep_passages:
            for k in passages.PASSAGE_FIELDS:
                f.pop(k, None)
        facts.append(f)
    return {"facts": facts,
            "date": doc.get("date_of_document"),
            "entities": [e.get("id") for e in artifact.get("entities") or [] if isinstance(e, dict)]}


def _load(path: Path) -> dict | None:
    key = str(path)
    try:
        st = os.stat(path)
    except OSError:
        _CACHE.pop(key, None)
        return None
    stamp = (st.st_size, st.st_mtime_ns)
    hit = _CACHE.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        data = _slim(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        data = None
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = (stamp, data)
    return data


def clear_cache() -> None:
    _CACHE.clear()


def _date_key(date: str | None) -> str:
    """A sortable form of YYYY, YYYY-MM or YYYY-MM-DD; anything else sorts after every date."""
    d = (date or "").strip()
    if len(d) == 4 and d.isdigit():
        return d + "-00-00"
    if len(d) == 7 and d[:4].isdigit():
        return d + "-00"
    if len(d) == 10 and d[:4].isdigit():
        return d
    return "9999-99-99"


class FactIndex:
    """The facts of a vault's committed documents, by entity.

    `entities`/`documents` are the registries as the caller holds them (a commit pass passes its
    in-memory copies, so documents not yet flushed count). `overrides` maps a sha to an extraction
    already in memory, which wins over the file. `marks` is the verification ledger's current marks
    (read once when omitted)."""

    def __init__(self, vault: Path, entities: dict | None = None, documents: dict | None = None,
                 overrides: dict[str, dict] | None = None, marks: dict | None = None,
                 merges: dict | None = None):
        self.vault = Path(vault)
        reg = _registry(self.vault)
        self.entities = entities if entities is not None else _read_json_or(reg / "entities.json", {})
        self.documents = documents if documents is not None else _read_json_or(reg / "documents.json", {})
        self.overrides = overrides if overrides is not None else {}
        self._marks = marks
        self._merges = merges
        self._absorbed: dict[str, list[str]] | None = None
        self._appears: dict[str, set[str]] = {}
        self._ids: dict[str, list[str]] = {}

    # ── documents ────────────────────────────────────────────────────────────────

    def document(self, sha: str) -> dict | None:
        """The slimmed extraction for `sha`, or None when it has none."""
        if sha in self.overrides:
            return _slim(self.overrides[sha])
        return _load(self.vault / ".watchdog" / "extracted" / f"{sha}.json")

    def fact_ids(self, sha: str) -> list[str]:
        if sha not in self._ids:
            from watchdog.pipeline.verification import fact_ids
            doc = self.document(sha)
            self._ids[sha] = fact_ids(sha, doc["facts"]) if doc else []
        return self._ids[sha]

    @property
    def marks(self) -> dict:
        if self._marks is None:
            from watchdog.pipeline import verification
            self._marks = verification.marks(self.vault)
        return self._marks

    # ── which record a tag names ─────────────────────────────────────────────────

    def _appears_in(self, eid: str) -> set[str]:
        if eid not in self._appears:
            self._appears[eid] = set((self.entities.get(eid) or {}).get("appears_in") or [])
        return self._appears[eid]

    def _absorbed_by(self) -> dict[str, list[str]]:
        """Merged id -> the ids that absorbed it, from every merge not undone."""
        if self._absorbed is None:
            from watchdog.pipeline import merge_log
            data = self._merges if self._merges is not None else merge_log.load(self.vault)
            out: dict[str, list[str]] = {}
            for m in data.get("merges") or []:
                if not isinstance(m, dict) or m.get("undone"):
                    continue
                keep, merged = (m.get("keep") or {}).get("id"), (m.get("merged") or {}).get("id")
                if keep and merged and keep != merged and keep not in out.get(merged, []):
                    out.setdefault(merged, []).append(keep)
            self._absorbed = out
        return self._absorbed

    def resolve(self, sha: str, tag: str) -> str | None:
        """The current entity a fact tag in document `sha` names, or None."""
        if tag in self.entities and sha in self._appears_in(tag):
            return tag
        absorbed = self._absorbed_by()
        seen, queue = {tag}, list(absorbed.get(tag, ()))
        while queue:
            eid = queue.pop(0)
            if eid in seen:
                continue
            seen.add(eid)
            if eid in self.entities and sha in self._appears_in(eid):
                return eid
            queue.extend(absorbed.get(eid, ()))
        return tag if tag in self.entities else None

    # ── an entity's facts ────────────────────────────────────────────────────────

    def facts_for(self, eid: str, shas=None) -> list[dict]:
        """Every fact tagged to `eid` in the documents it appears in (or `shas`), each a dict:
        `id`, `fact`, `page`, `basis`, `date`, `quote`, the figure and quote flags, the passage
        fields, `sha`, `index` (reading order in its document), `doc_date`, `title`, `note`,
        `morgue`, `entities` (current ids) and `mark` (the reporter's current mark, or None)."""
        from watchdog.pipeline.verification import attach
        entry = self.entities.get(eid) or {}
        shas = list(shas) if shas is not None else list(entry.get("appears_in") or [])
        out = []
        for sha in dict.fromkeys(shas):
            doc = self.document(sha)
            if not doc:
                continue
            rec = self.documents.get(sha) or {}
            ids = self.fact_ids(sha)
            for i, (fid, f) in enumerate(zip(ids, doc["facts"])):
                tags = [t for t in (f.get("entities") or []) if isinstance(t, str) and t]
                current = []
                for t in tags:
                    r = self.resolve(sha, t)
                    if r and r not in current:
                        current.append(r)
                if eid not in current:
                    continue
                page = f.get("page") if isinstance(f.get("page"), int) and not isinstance(f.get("page"), bool) else None
                out.append({
                    **{k: f.get(k) for k in ("fact", "basis", "date", "quote", "quote_verified",
                                             "quote_found_page", "quote_spans_pages",
                                             "figures_unverified", "figures_off_page", "passage",
                                             "passage_page", "passage_method", "passage_score",
                                             "added_by")},
                    "id": fid, "page": page, "sha": sha, "index": i,
                    "doc_date": doc.get("date") or rec.get("date_of_document"),
                    "title": rec.get("title") or rec.get("filename") or sha[:12],
                    "note": rec.get("document_note"), "morgue": rec.get("morgue_path"),
                    "entities": current,
                    "mark": attach(self.marks.get(fid), f),
                })
        return out


def chronological(facts: list[dict]) -> list[dict]:
    """Facts in the order a reporter reads an entity's story: by the fact's own date, or its
    document's date when it has none; undated facts from undated documents last. Ties keep a
    document's facts together, in reading order."""
    def key(f):
        return (_date_key(f.get("date") or f.get("doc_date")), _date_key(f.get("doc_date")),
                (f.get("title") or "").casefold(), f.get("sha") or "", f.get("index", 0))
    return sorted(facts, key=key)


def short_refs(facts: list[dict]) -> dict[str, str]:
    """A short citation for each fact id, unique within `facts`: `f:` plus the shortest prefix (at
    least four characters) of the id's hash that no other fact in the list shares, and the id's
    duplicate suffix (`:2`) when it has one. Returns {fact id: short ref}. The short ref is a
    window onto the D271 id, not a new id: the stored map takes it back to the full id."""
    def parts(fid: str) -> tuple[str, str]:
        bits = fid.split(":")
        h = bits[3] if len(bits) > 3 else fid
        dup = f":{bits[4]}" if len(bits) > 4 else ""
        return h, dup
    hashes = {fid: parts(fid) for fid in (f["id"] for f in facts)}
    out: dict[str, str] = {}
    for fid, (h, dup) in hashes.items():
        n = 4
        while n < len(h) and any(o != fid and oh[:n] == h[:n] and od == dup
                                 for o, (oh, od) in hashes.items()):
            n += 1
        out[fid] = f"f:{h[:n]}{dup}"
    return out
