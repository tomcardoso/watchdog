"""Relationships between entities, one row per pair and meaning (D291).

Documents describe the same relationship in different words: one calls a person "lawyer at" a
firm, the next "counsel with". Each document's own wording stays where it was recorded (the
registry's `roles`, the stored extraction); nothing here rewrites it. This module adds two things on
top:

- **A view.** `View` gathers every stated relationship (`statements`) from the registry's forward
  roles and from every committed document's stored extraction, so a relationship stated in four
  documents counts four, and groups them per ordered pair of entities and per *label key*. A key is
  `normalize(wording)`: case, spacing, punctuation, "&", articles and a plain plural `-s` only, so
  "Director of" and "the director of" share a key and "counsel to" and "counsel for" do not.
  The graph draws one edge per pair, entity notes and the app list one row per counterpart and
  meaning, and the export carries the canonical label with the wording.
- **A grouping log.** ``.watchdog/registry/relationships.json``::

      {"schema_version": 1,
       "groups": [{"id", "from", "to", "keys": [...], "canonical", "wordings": [...],
                   "decided_by": "model", "model", "reason", "at", "run",
                   "status": "active" | "superseded" | "split",
                   "superseded_at", "split_by", "split_at"}],
       "evaluated": {"<from>|<to>": [key, ...]}}

  After a run, every ordered pair a committed document of the batch touched that holds two or more
  keys with at least one not yet `evaluated` is sent to the finalizer model, which proposes groups
  of keys that name the same relationship and picks one member wording as the canonical label. Code
  checks each group (two or more keys of that pair, a canonical that is one of them, nothing the
  reporter split apart) and records it; anything else stays apart. A new decision on a pair
  supersedes the model's earlier groups for it. The reporter can split a group (`split`), which
  keeps its keys apart for good. Ids follow the merge log to the record that absorbed them.

Writers take the registry lock (I7); `View` only reads.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from watchdog.pipeline.json_io import _read_json_or

SCHEMA_VERSION = 1
LOG_FILE = "relationships.json"

# Most label keys one relationship-labels call is shown; a larger set is split across calls.
MAX_ITEMS_PER_CALL = 120
# Most distinct wordings one pair sends; a pair with more keeps its most-documented ones.
MAX_KEYS_PER_PAIR = 12

_ARTICLES = frozenset({"a", "an", "the"})
_LEADING = frozenset({"is", "are"})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def path(vault: Path) -> Path:
    return Path(vault) / ".watchdog" / "registry" / LOG_FILE


# ── normalizing a wording ───────────────────────────────────────────────────────────────

def _singular(word: str) -> str:
    """A plain plural `-s` dropped ("partners" → "partner"), leaving "-ss", "-us", "-is" and
    short words ("has", "owns") alone. Only for comparing keys; a wording is never shown altered."""
    if len(word) >= 5 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def normalize(label: str | None) -> str:
    """The key two wordings share when they differ only in case, spacing, punctuation, "&" for
    "and", an article, a leading "is"/"are", or a plain plural. Prepositions, tense and every other
    word are kept: "counsel to" and "counsel for", "director of" and "former director of" are
    different keys, and only the model may group them."""
    text = unicodedata.normalize("NFKC", label or "").casefold().replace("&", " and ")
    words = [w for w in re.split(r"[^\w]+", text) if w and w != "_"]
    words = [_singular(w) for w in words if w not in _ARTICLES]
    while len(words) > 1 and words[0] in _LEADING:
        words = words[1:]
    return " ".join(words)


# ── the log ───────────────────────────────────────────────────────────────────────────────

def _empty() -> dict:
    return {"schema_version": SCHEMA_VERSION, "groups": [], "evaluated": {}}


def load(vault: Path | None) -> dict:
    """The log, or an empty one when the file is missing or unreadable."""
    if vault is None:
        return _empty()
    data = _read_json_or(path(vault), None)
    if not isinstance(data, dict):
        return _empty()
    data.setdefault("schema_version", SCHEMA_VERSION)
    if not isinstance(data.get("groups"), list):
        data["groups"] = []
    if not isinstance(data.get("evaluated"), dict):
        data["evaluated"] = {}
    return data


def too_new(data: dict) -> bool:
    v = data.get("schema_version")
    return isinstance(v, int) and v > SCHEMA_VERSION


def _save(vault: Path, data: dict) -> None:
    from watchdog.pipeline.write_vault import _write_json_atomic
    p = path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(p, data)


def group_id(frm: str, to: str, keys) -> str:
    key = f"{frm}|{to}|{'|'.join(sorted(keys))}"
    return "rel:" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _pair_key(frm: str, to: str) -> str:
    return f"{frm}|{to}"


# ── the view ──────────────────────────────────────────────────────────────────────────────

def _page(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


class View:
    """Every stated relationship of a vault, grouped. `index` is an `entity_facts.FactIndex` (a
    commit pass passes its in-memory one); `log` the grouping log (read when omitted)."""

    def __init__(self, vault: Path, index=None, log: dict | None = None):
        from watchdog.pipeline import entity_facts
        self.vault = Path(vault)
        self.index = index if index is not None else entity_facts.FactIndex(self.vault, marks={})
        self.entities = self.index.entities
        self.documents = self.index.documents
        self.log = log if log is not None else load(self.vault)
        self._statements: list[dict] | None = None
        self._groups: dict[tuple, list[dict]] | None = None
        self._active: dict[tuple, dict] | None = None

    # which record an id names now
    def current(self, eid: str | None) -> str | None:
        """`eid`, or the record that absorbed it by a merge not undone, or None."""
        if not eid:
            return None
        if eid in self.entities:
            return eid
        absorbed = self.index._absorbed_by()
        seen, queue = {eid}, list(absorbed.get(eid, ()))
        while queue:
            nxt = queue.pop(0)
            if nxt in seen:
                continue
            seen.add(nxt)
            if nxt in self.entities:
                return nxt
            queue.extend(absorbed.get(nxt, ()))
        return None

    # every statement
    def statements(self) -> list[dict]:
        """One dict per (from, to, wording, document): `from`, `to`, `label` (the document's
        wording), `key`, `sha`, `page`, `basis`, `date_range`, and `to_name`/`to_type` for a target
        never profiled as its own entity. Registry roles first (they cover documents with no stored
        extraction), then each stored extraction's roles."""
        if self._statements is not None:
            return self._statements
        out: list[dict] = []
        seen: set[tuple] = set()

        def add(frm, to, label, sha, page, basis, date_range, to_name=None, to_type=None):
            label = (label or "").strip()
            key = normalize(label)
            if not frm or not to or frm == to or not key:
                return
            mark = (frm, to, label.casefold(), sha or "")
            if mark in seen:
                return
            seen.add(mark)
            out.append({"from": frm, "to": to, "label": label, "key": key, "sha": sha or None,
                        "page": _page(page), "basis": "inferred" if basis == "inferred" else "stated",
                        "date_range": (date_range or "").strip() or None,
                        "to_name": to_name, "to_type": to_type})

        for eid in sorted(self.entities):
            ent = self.entities[eid]
            if not isinstance(ent, dict):
                continue
            for r in ent.get("roles") or []:
                if not isinstance(r, dict) or r.get("is_reverse"):
                    continue
                tid = r.get("target_id")
                to = self.current(tid) or tid
                add(eid, to, r.get("relationship"), r.get("source_sha256"), r.get("page"),
                    r.get("basis"), r.get("date_range"),
                    None if to in self.entities else (r.get("target_name") or tid),
                    None if to in self.entities else r.get("target_type"))
        for sha in sorted(self.documents):
            doc = self.index.document(sha)
            for eid, label, tid, page, basis, date_range in (doc or {}).get("roles") or []:
                frm = self.index.resolve(sha, eid)
                to = self.index.resolve(sha, tid)
                if frm and to:
                    add(frm, to, label, sha, page, basis, date_range)
        self._statements = out
        return out

    # the log, by current ids
    def active_groups(self) -> dict[tuple, dict]:
        """(from, to, key) → the active group holding that key, ids followed through merges."""
        if self._active is not None:
            return self._active
        out: dict[tuple, dict] = {}
        for g in sorted((g for g in self.log.get("groups") or []
                         if isinstance(g, dict) and g.get("status", "active") == "active"),
                        key=lambda g: g.get("at") or ""):
            frm, to = self.current(g.get("from")), self.current(g.get("to"))
            if not frm or not to or frm == to:
                continue
            for k in g.get("keys") or []:
                out.setdefault((frm, to, k), g)
        self._active = out
        return out

    def split_keys(self, frm: str, to: str) -> list[set[str]]:
        """The key sets the reporter split apart on this ordered pair."""
        out = []
        for g in self.log.get("groups") or []:
            if (isinstance(g, dict) and g.get("status") == "split"
                    and self.current(g.get("from")) == frm and self.current(g.get("to")) == to):
                out.append(set(g.get("keys") or []))
        return out

    def evaluated(self, frm: str, to: str) -> set[str]:
        out: set[str] = set()
        for pk, keys in (self.log.get("evaluated") or {}).items():
            a, _, b = pk.partition("|")
            if self.current(a) == frm and self.current(b) == to:
                out |= set(keys or [])
        return out

    # grouped
    def groups(self) -> dict[tuple, list[dict]]:
        """(from, to) → its relationships, one per canonical group (see `group_statements`)."""
        if self._groups is None:
            self._groups = group_statements(self.statements(), self.active_groups(), self._doc_order)
        return self._groups

    def _doc_order(self, sha: str | None) -> tuple:
        rec = self.documents.get(sha or "") or {}
        return (str(rec.get("date_of_document") or "9999"), str(rec.get("title") or rec.get("filename") or ""),
                sha or "")

    def edges(self, profiled_only: bool = True) -> list[dict]:
        """One edge per unordered pair, for the network: `a`/`b` (the pair, `a` the source of its
        best-documented relationship), `relationships` (both directions' rows), `docs` (distinct
        shas across them), `directed` (every row runs a → b)."""
        pairs: dict[tuple, list[dict]] = {}
        for (frm, to), rows in self.groups().items():
            if profiled_only and (frm not in self.entities or to not in self.entities):
                continue
            pairs.setdefault(tuple(sorted((frm, to))), []).extend(rows)
        out = []
        for (lo, hi), rows in sorted(pairs.items()):
            rows = sorted(rows, key=lambda r: (-len(r["docs"]), r["label"].casefold(), r["from"]))
            a = rows[0]["from"]
            b = hi if a == lo else lo
            docs: list[str] = []
            for r in rows:
                docs += [d for d in r["docs"] if d not in docs]
            out.append({"a": a, "b": b, "relationships": rows, "docs": docs,
                        "directed": all(r["from"] == a for r in rows)})
        return out

    def for_entity(self, eid: str) -> list[dict]:
        """The entity's relationships, one row per counterpart and meaning, with `direction`
        (`out` when the entity is the subject) and `other` (the counterpart's id). Sorted by
        counterpart name, outgoing first."""
        rows = []
        for (frm, to), group in self.groups().items():
            if eid not in (frm, to):
                continue
            for r in group:
                out = frm == eid
                other = to if out else frm
                rec = self.entities.get(other) or {}
                rows.append({**r, "direction": "out" if out else "in", "other": other,
                             "other_name": rec.get("name") or r.get("to_name") or other,
                             "other_type": rec.get("type") or r.get("to_type") or "Unknown",
                             "profiled": other in self.entities})
        rows.sort(key=lambda r: (r["other_name"].casefold(), r["direction"] != "out",
                                 r["label"].casefold()))
        return rows

    def canonical(self, frm: str, to: str, label: str) -> str:
        """The label a wording on (from, to) is shown under."""
        key = normalize(label)
        g = self.active_groups().get((self.current(frm) or frm, self.current(to) or to, key))
        if g and (g.get("canonical") or "").strip():
            return g["canonical"].strip()
        for r in self.groups().get((self.current(frm) or frm, self.current(to) or to)) or []:
            if key in r["keys"]:
                return r["label"]
        return (label or "").strip()

    # what the model is asked
    def pending(self, shas) -> list[dict]:
        """Ordered pairs a document in `shas` states a relationship on, holding two or more keys at
        least one of which was never put to the model: `from`, `to`, `labels` (each key's most
        used wording and document count, most-documented first, at most `MAX_KEYS_PER_PAIR`)."""
        shas = set(shas or ())
        touched: dict[tuple, dict[str, list[dict]]] = {}
        hit: set[tuple] = set()
        for s in self.statements():
            pair = (s["from"], s["to"])
            if pair[0] not in self.entities or pair[1] not in self.entities:
                continue
            touched.setdefault(pair, {}).setdefault(s["key"], []).append(s)
            if s["sha"] in shas:
                hit.add(pair)
        out = []
        for pair in sorted(hit):
            by_key = touched[pair]
            if len(by_key) < 2 or set(by_key) <= self.evaluated(*pair):
                continue
            labels = []
            for key, stmts in by_key.items():
                counts = Counter(s["label"] for s in stmts)
                docs = {s["sha"] for s in stmts if s["sha"]}
                labels.append({"key": key, "label": sorted(counts, key=lambda w: (-counts[w], w))[0],
                               "docs": len(docs) or 1})
            labels.sort(key=lambda x: (-x["docs"], x["label"].casefold()))
            out.append({"from": pair[0], "to": pair[1], "labels": labels[:MAX_KEYS_PER_PAIR]})
        return out


def group_statements(statements: list[dict], active: dict[tuple, dict],
                     doc_order=lambda sha: (sha or "",)) -> dict[tuple, list[dict]]:
    """(from, to) → its relationships, one per canonical group: `from`, `to`, `label` (the
    canonical label, else the wording most documents use), `keys`, `group` (the log entry's id,
    when grouped), `wordings` (each distinct wording with its sources), `sources` (sha, page,
    wording, basis, date_range), `docs` (distinct shas), `basis` (inferred only when every source
    is), `date_ranges`, `to_name`, `to_type`. `active` maps (from, to, key) to a log group."""
    buckets: dict[tuple, dict[str, list[dict]]] = {}
    for s in statements:
        g = active.get((s["from"], s["to"], s["key"]))
        bucket = g["id"] if g else "key:" + s["key"]
        buckets.setdefault((s["from"], s["to"]), {}).setdefault(bucket, []).append(s)
    out: dict[tuple, list[dict]] = {}
    for pair, by_bucket in buckets.items():
        rows = []
        for bucket, stmts in by_bucket.items():
            stmts.sort(key=lambda s: (doc_order(s["sha"]), s["page"] or 0, s["label"]))
            g = active.get((pair[0], pair[1], stmts[0]["key"])) if not bucket.startswith("key:") else None
            wordings: dict[str, dict] = {}
            for s in stmts:
                w = wordings.setdefault(s["label"].casefold(), {"text": s["label"], "sources": []})
                w["sources"].append({"sha": s["sha"], "page": s["page"]})
            counts = Counter(s["label"] for s in stmts)
            common = sorted(counts, key=lambda w: (-counts[w], w))[0]
            label = (g.get("canonical") or "").strip() if g else ""
            docs: list[str] = []
            ranges: list[str] = []
            for s in stmts:
                if s["sha"] and s["sha"] not in docs:
                    docs.append(s["sha"])
                if s["date_range"] and s["date_range"] not in ranges:
                    ranges.append(s["date_range"])
            rows.append({
                "from": pair[0], "to": pair[1], "label": label or common,
                "keys": sorted({s["key"] for s in stmts}),
                "group": g.get("id") if g else None,
                "wordings": list(wordings.values()),
                "sources": [{k: s[k] for k in ("sha", "page", "label", "basis", "date_range")}
                            for s in stmts],
                "docs": docs,
                "basis": "inferred" if all(s["basis"] == "inferred" for s in stmts) else "stated",
                "date_ranges": ranges,
                "to_name": next((s["to_name"] for s in stmts if s["to_name"]), None),
                "to_type": next((s["to_type"] for s in stmts if s["to_type"]), None),
            })
        rows.sort(key=lambda r: (-len(r["docs"]), r["label"].casefold()))
        out[pair] = rows
    return out


def rows_for_entry(entry: dict) -> list[dict]:
    """`View.for_entity` rows from one registry entry's own roles (forward and reverse), grouped
    by normalized wording only: for rendering a note with no vault to read."""
    eid = entry.get("id") or ""
    stmts, seen = [], set()
    for r in entry.get("roles") or []:
        if not isinstance(r, dict):
            continue
        label = (r.get("relationship") or "").strip()
        other = r.get("target_id")
        if not label or not other or other == eid:
            continue
        frm, to = (other, eid) if r.get("is_reverse") else (eid, other)
        mark = (frm, to, label.casefold(), r.get("source_sha256") or "")
        if mark in seen:
            continue
        seen.add(mark)
        stmts.append({"from": frm, "to": to, "label": label, "key": normalize(label),
                      "sha": r.get("source_sha256"), "page": _page(r.get("page")),
                      "basis": "inferred" if r.get("basis") == "inferred" else "stated",
                      "date_range": (r.get("date_range") or "").strip() or None,
                      "to_name": r.get("target_name"), "to_type": r.get("target_type")})
    rows = []
    for (frm, to), group in group_statements(stmts, {}).items():
        for r in group:
            out = frm == eid
            other = to if out else frm
            rows.append({**r, "direction": "out" if out else "in", "other": other,
                         "other_name": r.get("to_name") or other,
                         "other_type": r.get("to_type") or "Unknown", "profiled": True})
    rows.sort(key=lambda r: (r["other_name"].casefold(), r["direction"] != "out", r["label"].casefold()))
    return rows


# ── the model's answer, applied by code ─────────────────────────────────────────────────

def checked_groups(item: dict, answer: dict | None, split: list[set[str]]) -> tuple[list[dict], list[str]]:
    """The groups of one pair's answer that code accepts, and why each other one was dropped. A
    group must name two or more of the pair's labels (by number, each at most once across the
    answer), its canonical must be one of them, and it must not join two keys the reporter split."""
    labels = item["labels"]
    accepted, dropped, used = [], [], set()
    for g in (answer or {}).get("groups") or []:
        if not isinstance(g, dict):
            continue
        nums = g.get("labels")
        if not isinstance(nums, list) or not all(isinstance(n, int) and not isinstance(n, bool) for n in nums):
            dropped.append("labels not numbers")
            continue
        nums = sorted(set(nums))
        if len(nums) < 2 or any(n < 1 or n > len(labels) for n in nums):
            dropped.append("fewer than two of the pair's labels")
            continue
        if used & set(nums):
            dropped.append("a label in two groups")
            continue
        canon = g.get("canonical")
        if not isinstance(canon, int) or isinstance(canon, bool) or canon not in nums:
            dropped.append("canonical label not one of the group's")
            continue
        keys = {labels[n - 1]["key"] for n in nums}
        if any(len(keys & s) >= 2 for s in split):
            dropped.append("joins labels the reporter split")
            continue
        used |= set(nums)
        accepted.append({"keys": sorted(keys), "canonical": labels[canon - 1]["label"],
                         "wordings": [labels[n - 1]["label"] for n in nums],
                         "reason": (g.get("reason") or "").strip()[:300] or None})
    return accepted, dropped


def apply(vault: Path, items: list[dict], answers: dict[int, dict], *, model: str | None = None,
          run: str | None = None) -> dict:
    """Record the model's decisions for `items` (each pair `pending` returned; `answers` maps an
    item's index to its answer, `{}` for "nothing to group"). Under the registry lock. Returns
    `{"grouped": [...], "dropped": n, "entities": {ids whose rows changed}}`. An item with no
    answer (its call failed) is left unevaluated, for the next run that touches it."""
    from watchdog.pipeline.write_vault import _registry_lock
    vault = Path(vault)
    registry = vault / ".watchdog" / "registry"
    if not registry.is_dir():
        return {"grouped": [], "dropped": 0, "entities": set()}
    with _registry_lock(registry):
        data = load(vault)
        if too_new(data):
            return {"grouped": [], "dropped": 0, "entities": set(), "too_new": True}
        view = View(vault, log=data)
        at = _now()
        grouped, dropped, touched = [], 0, set()
        for i, item in enumerate(items):
            if i not in answers:
                continue
            frm, to = item["from"], item["to"]
            accepted, why = checked_groups(item, answers[i], view.split_keys(frm, to))
            dropped += len(why)
            item_keys = {lab["key"] for lab in item["labels"]}
            before = {g["id"] for g in data["groups"]
                      if g.get("status", "active") == "active" and g.get("decided_by") == "model"
                      and view.current(g.get("from")) == frm and view.current(g.get("to")) == to
                      and set(g.get("keys") or []) & item_keys}
            new_ids = {group_id(frm, to, g["keys"]) for g in accepted}
            for g in data["groups"]:
                if g.get("id") in before - new_ids and g.get("status", "active") == "active":
                    g["status"], g["superseded_at"] = "superseded", at
                    touched |= {frm, to}
            for g in accepted:
                gid = group_id(frm, to, g["keys"])
                existing = next((x for x in data["groups"] if x.get("id") == gid
                                 and x.get("status", "active") == "active"), None)
                if existing:
                    if existing.get("canonical") != g["canonical"]:
                        existing.update({"canonical": g["canonical"], "at": at, "model": model})
                        touched |= {frm, to}
                    continue
                entry = {"id": gid, "from": frm, "to": to, **g, "decided_by": "model",
                         "model": model, "at": at, "run": run, "status": "active"}
                data["groups"].append(entry)
                grouped.append(entry)
                touched |= {frm, to}
            pk = _pair_key(frm, to)
            data["evaluated"][pk] = sorted(set(data["evaluated"].get(pk) or []) | item_keys)
        data["schema_version"] = SCHEMA_VERSION
        _save(vault, data)
    return {"grouped": grouped, "dropped": dropped, "entities": touched}


def split(vault: Path, gid: str, by: str | None = None) -> dict:
    """Undo one grouping: its wordings are shown apart again, and no later run may group any two of
    them on that pair. Recorded as a version of the vault's history; the two entities' notes are
    refreshed. Returns the log entry. Raises LookupError for an unknown or inactive group."""
    from watchdog.pipeline import entity_notes, history
    from watchdog.pipeline.verification import reporter_name
    from watchdog.pipeline.write_vault import _registry_lock
    vault = Path(vault)
    registry = vault / ".watchdog" / "registry"
    scope = [f".watchdog/registry/{LOG_FILE}"]
    if not history.run_in_progress(vault):
        scope += ["entities/"]
    with history.recording(vault, {"kind": "relationship_split", "group": gid}, scope):
        with _registry_lock(registry):
            data = load(vault)
            if too_new(data):
                raise ValueError("This investigation's relationship log was written by a newer "
                                 "version of Watchdog. Update Watchdog to change it.")
            entry = next((g for g in data["groups"] if isinstance(g, dict) and g.get("id") == gid
                          and g.get("status", "active") == "active"), None)
            if entry is None:
                raise LookupError(f"No current grouping has the id {gid}.")
            entry.update({"status": "split", "split_by": (by or "").strip() or reporter_name(),
                          "split_at": _now()})
            _save(vault, data)
        view = View(vault, log=data)
        ids = {i for i in (view.current(entry.get("from")), view.current(entry.get("to"))) if i}
        try:
            entity_notes.refresh(vault, ids)
        except OSError:
            pass
    return entry


def prompt_items(items: list[dict], entities: dict) -> str:
    """The pairs as the relationship-labels prompt lists them: `P<n>: <from> (<type>) → <to>
    (<type>)`, then each wording numbered with its document count."""
    lines = []
    for n, item in enumerate(items, 1):
        a, b = entities.get(item["from"]) or {}, entities.get(item["to"]) or {}
        lines.append(f"P{n}: {a.get('name') or item['from']} ({a.get('type') or 'unknown'}) → "
                     f"{b.get('name') or item['to']} ({b.get('type') or 'unknown'})")
        for i, lab in enumerate(item["labels"], 1):
            docs = lab["docs"]
            lines.append(f"  {i}. \"{lab['label']}\" ({docs} document{'s' if docs != 1 else ''})")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def chunks(items: list[dict]) -> list[list[int]]:
    """Item indices split so no call lists more than `MAX_ITEMS_PER_CALL` labels."""
    out, cur, n = [], [], 0
    for i, item in enumerate(items):
        size = len(item["labels"])
        if cur and n + size > MAX_ITEMS_PER_CALL:
            out.append(cur)
            cur, n = [], 0
        cur.append(i)
        n += size
    if cur:
        out.append(cur)
    return out
