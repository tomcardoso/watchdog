"""A committed document's records brought in line with its stored extraction after a migration
changed it (D336).

The pipeline's own steps change a document's extraction before its commit, and the commit writes
everything derived from it (I7). "Bring this investigation up to date" runs the same steps over
documents committed long ago, so what the commit derived has to follow. Most of it is read from the
extraction at display time and needs nothing: a fact's tags, page, date and passage reach the
notes, Review, the app and every model input through `entity_facts.FactIndex`. Four things were
written at commit time and are brought in line here:

- **Entity timelines** (`timeline_events` in `registry/entities.json`, the entity page's timeline):
  a fact whose date, page or tags changed has its event removed from the records it left and added
  to the records it now reaches.
- **The timeline files** (`.watchdog/timeline/`): the same change, through
  `timeline.sync_document_events`, which updates a record in place when its date and text are
  unchanged and stages a new one otherwise.
- **Relationships** (`roles` in the registry): a relationship whose target was re-pointed is
  re-pointed on the record, with its reverse on the new target (`write_vault._add_reverse_role`).
- **Fact ids** (D271 hashes the fact's page): a corrected page moves the reporter's mark with it
  (`verification.move_marks`), and the links that name the old id (a contradiction's fact links,
  an entity summary's short refs) are rewritten. A contradiction's resolution id ignores fact
  links, so a handled one stays handled.

Usage: take a `Tracker` over the documents before a step changes them, then `sync(vault,
tracker.changed())` after it. Nothing here calls a model.
"""

from __future__ import annotations

import json
from pathlib import Path

from watchdog.pipeline.json_io import _read_json_or

_FACT_FIELDS = ("fact", "page", "date", "date_end", "date_text", "date_basis", "basis", "entities")


def _path(vault: Path, sha: str) -> Path:
    return Path(vault) / ".watchdog" / "extracted" / f"{sha}.json"


def load(vault: Path, sha: str) -> dict | None:
    art = _read_json_or(_path(vault, sha), None)
    return art if isinstance(art, dict) else None


def save(vault: Path, sha: str, art: dict) -> None:
    _path(vault, sha).write_text(json.dumps(art, ensure_ascii=False, indent=2), encoding="utf-8")


def _facts(art: dict) -> list:
    facts = (art.get("document") or {}).get("key_facts")
    return facts if isinstance(facts, list) else []


def snapshot(art: dict | None) -> dict:
    """The parts of an extraction whose change `sync` carries into the committed records."""
    if not isinstance(art, dict):
        return {"facts": [], "roles": {}}
    facts = []
    for f in _facts(art):
        if isinstance(f, dict):
            facts.append({k: (list(f[k]) if isinstance(f.get(k), list) else f.get(k))
                          for k in _FACT_FIELDS})
        else:
            facts.append(None)
    roles = {}
    for i, e in enumerate(art.get("entities") or []):
        if not isinstance(e, dict):
            continue
        for j, r in enumerate(e.get("roles") or []):
            if isinstance(r, dict):
                roles[(i, j)] = r.get("target_id")
    return {"facts": facts, "roles": roles}


class Tracker:
    """Snapshots of committed documents before a step, to find what it changed."""

    def __init__(self, vault: Path, shas):
        self.vault = Path(vault)
        self.before = {sha: snapshot(load(self.vault, sha)) for sha in shas}

    def changed(self) -> list[tuple[str, dict, dict]]:
        """``(sha, snapshot before, extraction now)`` for every document the step changed."""
        out = []
        for sha, before in self.before.items():
            art = load(self.vault, sha)
            if art is not None and snapshot(art) != before:
                out.append((sha, before, art))
        return out


def _live(facts: list) -> list[tuple[int, dict]]:
    """The facts with text, as `entity_facts`/`verification` number them."""
    return [(i, f) for i, f in enumerate(facts) if isinstance(f, dict) and (f.get("fact") or "").strip()]


def _event_key(f: dict) -> str:
    from watchdog.pipeline.write_vault import _timeline_dedup_key
    return _timeline_dedup_key({"date": (f.get("date") or "").strip(), "event": (f.get("fact") or "").strip(),
                                "date_end": f.get("date_end") or ""})


def _resolved(index, sha: str, tags) -> list[str]:
    out: list[str] = []
    for t in tags or []:
        if isinstance(t, str) and t:
            r = index.resolve(sha, t)
            if r and r not in out:
                out.append(r)
    return out


def _sync_facts(sha: str, before: dict, art: dict, entities: dict, index, timeline_changes: dict,
                moves: dict, counts: dict) -> None:
    from watchdog.pipeline import timeline, verification
    from watchdog.pipeline.postflight import _timeline_event
    from watchdog.pipeline.write_vault import _merge_timeline_events, _timeline_dedup_key
    facts_now = _facts(art)
    b_live, a_live = _live(before["facts"]), _live(facts_now)
    # A fact's D271 id hashes its page: a corrected page is a new id for the same words.
    if [i for i, _ in b_live] == [i for i, _ in a_live]:
        ids_b = verification.fact_ids(sha, [f for _, f in b_live])
        ids_a = verification.fact_ids(sha, [f for _, f in a_live])
        for (i, f), old, new in zip(a_live, ids_b, ids_a):
            if old != new:
                moves[old] = (new, f.get("page") if isinstance(f.get("page"), int) else None)
    for i, b in enumerate(before["facts"]):
        a = facts_now[i] if i < len(facts_now) else None
        if not isinstance(a, dict) or not isinstance(b, dict):
            continue
        if {k: a.get(k) for k in _FACT_FIELDS} == b:
            continue
        text = (a.get("fact") or "").strip()
        if not text:
            continue
        old_dated, new_dated = bool((b.get("date") or "").strip()), bool((a.get("date") or "").strip())
        if not (old_dated or new_dated):
            continue
        old_ents, new_ents = _resolved(index, sha, b.get("entities")), _resolved(index, sha, a.get("entities"))
        if old_dated:
            key = _event_key(b)
            for eid in old_ents:
                entry = entities.get(eid)
                if isinstance(entry, dict):
                    events = entry.get("timeline_events") or []
                    kept = [ev for ev in events if not (ev.get("source_sha256") == sha
                                                       and _timeline_dedup_key(ev) == key)]
                    if len(kept) != len(events):
                        entry["timeline_events"] = kept
                        counts["events_removed"] += len(events) - len(kept)
        record = None
        if new_dated:
            event = _timeline_event(a, text)
            for eid in new_ents:
                entry = entities.get(eid)
                if isinstance(entry, dict):
                    before_n = len(entry.get("timeline_events") or [])
                    entry["timeline_events"] = _merge_timeline_events(entry.get("timeline_events") or [],
                                                                      [event], sha)
                    counts["events_added"] += len(entry["timeline_events"]) - before_n
            record = {"date": event["date"], "event": text, "page": a.get("page"),
                      "entity_ids": new_ents, "basis": a.get("basis") or "stated",
                      **{k: a[k] for k in ("date_end", "date_text", "date_basis") if a.get(k)}}
        old_key = timeline.event_key(b.get("date"), b.get("fact"), b.get("date_end") or "") if old_dated else None
        timeline_changes.setdefault(sha, []).append((old_key, record))


def _sync_roles(sha: str, before: dict, art: dict, entities: dict, index, counts: dict) -> None:
    from watchdog.pipeline.write_vault import _add_reverse_role
    ents = art.get("entities") or []
    for (i, j), old in before["roles"].items():
        if i >= len(ents) or not isinstance(ents[i], dict):
            continue
        roles = ents[i].get("roles") or []
        role = roles[j] if j < len(roles) and isinstance(roles[j], dict) else None
        new = role.get("target_id") if role else None
        if not role or not new or new == old:
            continue
        eid = index.resolve(sha, ents[i].get("id") or "") or ents[i].get("id")
        entry = entities.get(eid)
        if not isinstance(entry, dict):
            continue
        new_t = index.resolve(sha, new) or new
        rel = (role.get("relationship") or "").strip()
        target = entities.get(new_t) or {}
        mine = entry.setdefault("roles", [])
        fwd = [r for r in mine if not r.get("is_reverse") and r.get("source_sha256") == sha
               and (r.get("relationship") or "").lower() == rel.lower() and r.get("target_id") == old]
        if any(not r.get("is_reverse") and (r.get("relationship") or "").lower() == rel.lower()
               and r.get("target_id") == new_t for r in mine):
            entry["roles"] = [r for r in mine if not any(r is f for f in fwd)]
        else:
            fields = {"target_id": new_t, "target_name": target.get("name") or new_t,
                      "target_type": target.get("type") or role.get("target_type") or "Unknown",
                      **{k: role[k] for k in ("target_basis", "extracted_target_id") if role.get(k)}}
            if fwd:
                fwd[0].update(fields)
                entry["roles"] = [r for r in mine if not any(r is f for f in fwd[1:])]
            else:
                mine.append({**{k: v for k, v in role.items() if k not in ("target_unmatched",)},
                             **fields, "source_sha256": sha, "is_reverse": False})
        old_entry = entities.get(old)
        if isinstance(old_entry, dict):
            old_entry["roles"] = [r for r in old_entry.get("roles") or []
                                  if not (r.get("is_reverse") and r.get("target_id") == eid
                                          and r.get("source_sha256") == sha
                                          and (r.get("relationship") or "").lower() == rel.lower())]
        _add_reverse_role(entities, entry, {**role, "target_id": new_t}, sha, set())
        counts["roles"] += 1


def _move_links(entities: dict, moves: dict) -> int:
    """Rewrite the stored links that name a moved fact id: a contradiction side's block link and
    an entity summary's short-ref map. Returns the records changed."""
    from watchdog.pipeline.citations import block_id
    blocks = {block_id(old): block_id(new) for old, (new, _page) in moves.items()}
    ids = {old: new for old, (new, _page) in moves.items()}
    changed = 0
    for entry in entities.values():
        if not isinstance(entry, dict):
            continue
        touched = False
        callouts = entry.get("contradictions")
        if isinstance(callouts, list):
            out = []
            for c in callouts:
                if isinstance(c, str):
                    new_c = c
                    for old_b, new_b in blocks.items():
                        new_c = new_c.replace(f"#^{old_b}|", f"#^{new_b}|")
                    touched |= new_c != c
                    out.append(new_c)
                else:
                    out.append(c)
            entry["contradictions"] = out
        refs = (entry.get("synthesis") or {}).get("fact_refs") if isinstance(entry.get("synthesis"), dict) else None
        if isinstance(refs, dict):
            for k, v in list(refs.items()):
                if v in ids:
                    refs[k] = ids[v]
                    touched = True
        changed += touched
    return changed


def sync(vault: Path, changed: list[tuple[str, dict, dict]]) -> dict:
    """Carry each changed extraction into the committed records (see the module docstring), under
    the registry lock. Only committed documents are touched. Returns counts."""
    from watchdog.pipeline import entity_facts, timeline, verification
    from watchdog.pipeline.write_vault import _registry_lock, _write_json_atomic
    vault = Path(vault)
    counts = {"documents": 0, "events_added": 0, "events_removed": 0, "roles": 0, "marks_moved": 0,
              "links": 0}
    if not changed:
        return counts
    reg = vault / ".watchdog" / "registry"
    timeline_changes: dict[str, list] = {}
    moves: dict[str, tuple[str, int | None]] = {}
    with _registry_lock(reg):
        entities = _read_json_or(reg / "entities.json", {})
        documents = _read_json_or(reg / "documents.json", {})
        index = entity_facts.FactIndex(vault, entities, documents)
        for sha, before, art in changed:
            if sha not in documents:
                continue
            counts["documents"] += 1
            _sync_facts(sha, before, art, entities, index, timeline_changes, moves, counts)
            _sync_roles(sha, before, art, entities, index, counts)
        if moves:
            counts["links"] = _move_links(entities, moves)
        _write_json_atomic(reg / "entities.json", entities)
    counts["marks_moved"] = verification.move_marks(vault, moves)
    timeline.sync_document_events(vault, timeline_changes)
    return counts
