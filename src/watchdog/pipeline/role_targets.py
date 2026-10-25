"""Relationship targets named by an id no entity has (D332), the second housekeeping chore.

Extraction names a relationship's target by id only (D321). When the model points at an id it never
defined, `write_vault._resolve_role_targets` falls back to the id as the target's name, and the
target becomes a "named but never profiled" lead (`leads.py`) whose name is the bare id. Most such
ids are near misses of an entity that exists — a stray space, a shortened company name, a variant of
an id — so in finalize, after reconciliation and before the commit:

1. **Code first** (`resolve_exact`). The id is compared, after folding case, spacing and
   punctuation (`write_vault.slugify`), with the ids, names and aliases of the document's own
   entities, then with the rest of the batch and the registry. One match is applied silently; none,
   or several, leaves the target for the model.
2. **The model for the rest** (`CHORE`, task `role-targets`). Each remaining target is sent with its
   relationships, the entity that names it, the document's entities and a few registry entities
   whose names are like it. The model answers the id it means, and what kind of thing the target
   is, or nothing.
3. **Code applies it** (`validate`, `apply`). The id must be one the item offered, not the naming
   entity itself, and of a type that fits the kind the model gave and any type the extraction
   recorded for the target. The role's `target_id` is rewritten in the staged extraction, its first
   value kept in `extracted_target_id` (as a fold keeps it), so a later merge records and undoes the
   role like any other; a model-made match is marked `target_basis: "inferred"` and shown as
   inferred wherever the relationship is (I1). Nothing, or a rejected answer, leaves the lead, and
   `target_unmatched` keeps the next finalize of the same batch from asking again.

Hidden entities (D326) are never offered: a relationship moved onto one would vanish with it.
"""

from __future__ import annotations

import json
from pathlib import Path

from watchdog.pipeline import housekeeping
from watchdog.pipeline.json_io import _read_json_or

# Entities of the document a call lists for one target, and registry entities found by name.
DOC_ENTITIES_MAX = 60
CANDIDATES_MAX = 8
# Words that say nothing about which entity a name means, left out of the similarity score.
_FILLER = frozenset("""the of and a an for to in on at by de la le du des et inc ltd llc llp corp
co company corporation limited group plc""".split())

KINDS = ("person", "organization", "public-body", "place", "asset", "proceeding")
# Kinds that a document and a registry may record for the same body: a crown corporation, a
# university, a hospital, a board.
_NEAR = {frozenset({"organization", "public-body"})}


def _slug(text) -> str:
    from watchdog.pipeline.write_vault import slugify
    return slugify(text) if isinstance(text, str) else ""


def _canon(t) -> str:
    from watchdog.pipeline.entity_type import canonical_type
    return canonical_type(t or "")


def compatible(a: str, b: str) -> bool:
    """Whether two entity types may name the same thing: equal, or organization and public body."""
    a, b = _canon(a), _canon(b)
    return a == b or frozenset({a, b}) in _NEAR


def _words(text: str) -> list[str]:
    return [w for w in _slug(text).split("-") if w]


def _signature(words: list[str]) -> set[str]:
    return {w for w in words if w not in _FILLER} or set(words)


def similarity(target: str, entity: dict) -> float:
    """How alike a target id and an entity are, 0 to 1: the share of the target's words found in
    the entity's id, name or one alias, and 1 for an abbreviation made of a name's initials."""
    t = _words(target)
    if not t:
        return 0.0
    sig = _signature(t)
    best = 0.0
    for surface in [entity.get("id") or "", entity.get("name") or "", *(entity.get("aliases") or [])]:
        words = _words(surface)
        if not words:
            continue
        if len(t) == 1 and len(t[0]) >= 2 and t[0] == "".join(w[0] for w in words if w not in _FILLER):
            return 1.0
        best = max(best, len(sig & _signature(words)) / len(sig))
    return best


def _keys(entity: dict) -> set[str]:
    return {k for k in (_slug(entity.get("id")), _slug(entity.get("name")),
                        *(_slug(a) for a in entity.get("aliases") or [])) if k}


# ── where the targets are ─────────────────────────────────────────────────────────────────

class Batch:
    """The staged extractions `shas` and the entities a target may mean: each document's own, the
    batch's, and the registry's (less hidden ones)."""

    def __init__(self, vault: Path, shas: list[str]):
        from watchdog.pipeline import hidden
        self.vault = Path(vault)
        self.dir = self.vault / ".watchdog" / "extracted"
        reg = _read_json_or(self.vault / ".watchdog" / "registry" / "entities.json", {})
        self.hidden = hidden.hidden_ids(self.vault)
        self.pool: dict[str, dict] = {}
        for eid, e in (reg if isinstance(reg, dict) else {}).items():
            if isinstance(e, dict) and eid not in self.hidden:
                self.pool[eid] = {"id": eid, "name": e.get("name") or eid, "type": e.get("type"),
                                  "aliases": list(e.get("aliases") or [])}
        self.artifacts: dict[str, dict] = {}
        for sha in shas:
            art = _read_json_or(self.dir / f"{sha}.json", None)
            if not isinstance(art, dict):
                continue
            self.artifacts[sha] = art
            for e in art.get("entities") or []:
                if isinstance(e, dict) and e.get("id") and e["id"] not in self.hidden:
                    self.pool.setdefault(e["id"], {"id": e["id"], "name": e.get("name") or e["id"],
                                                   "type": e.get("type"),
                                                   "aliases": list(e.get("aliases") or [])})

    def save(self, sha: str) -> None:
        (self.dir / f"{sha}.json").write_text(
            json.dumps(self.artifacts[sha], ensure_ascii=False, indent=2), encoding="utf-8")

    def dangling(self) -> list[dict]:
        """Every role of the batch whose target no entity has: ``{"sha", "entity", "role",
        "target"}`` (indices into the extraction). A target the pass already looked at is left out."""
        out = []
        for sha, art in self.artifacts.items():
            ents = art.get("entities") or []
            own = {e.get("id") for e in ents if isinstance(e, dict)}
            for i, e in enumerate(ents):
                if not isinstance(e, dict):
                    continue
                for j, r in enumerate(e.get("roles") or []):
                    tid = r.get("target_id") if isinstance(r, dict) else None
                    if (not isinstance(tid, str) or not tid.strip() or tid in own or tid in self.pool
                            or tid in self.hidden or r.get("target_unmatched")):
                        continue
                    out.append({"sha": sha, "entity": i, "role": j, "target": tid})
        return out


def _unique(key: str, entities) -> str | None:
    hits = {e["id"] for e in entities if key in _keys(e)}
    return hits.pop() if len(hits) == 1 else None


def resolve_exact(batch: Batch) -> int:
    """Point each dangling target whose id matches exactly one entity, once case, spacing and
    punctuation are folded, at that entity: the document's own first, then the batch's and the
    registry's. Silent: a tidy match is not a judgement. Returns the roles re-pointed."""
    fixed, touched = 0, set()
    for ref in batch.dangling():
        art = batch.artifacts[ref["sha"]]
        ents = [e for e in art.get("entities") or [] if isinstance(e, dict) and e.get("id")]
        key = _slug(ref["target"])
        if not key:
            continue
        me = art["entities"][ref["entity"]]
        match = _unique(key, ents) or _unique(key, batch.pool.values())
        if not match or match == me.get("id"):
            continue
        role = me["roles"][ref["role"]]
        role.setdefault("extracted_target_id", role["target_id"])
        role["target_id"] = match
        fixed += 1
        touched.add(ref["sha"])
    for sha in touched:
        batch.save(sha)
    return fixed


def _entity_line(e: dict) -> str:
    aliases = [a for a in e.get("aliases") or [] if a and a != e.get("name")][:4]
    also = f"; also {', '.join(json.dumps(a, ensure_ascii=False) for a in aliases)}" if aliases else ""
    return f"{e['id']} — {json.dumps(e.get('name') or e['id'], ensure_ascii=False)} ({_canon(e.get('type'))}{also})"


def pending_items(batch: Batch) -> list[dict]:
    """The targets code could not settle, one item per document and target id: ``{"sha",
    "target", "refs": [[entity, role], …], "mentions": [{"by", "by_type", "relationship"}],
    "target_type", "document": [entity…], "candidates": [entity…], "offered": [ids]}``."""
    items: dict[tuple, dict] = {}
    for ref in batch.dangling():
        art = batch.artifacts[ref["sha"]]
        me = art["entities"][ref["entity"]]
        role = me["roles"][ref["role"]]
        it = items.get((ref["sha"], ref["target"]))
        if it is None:
            ents = [e for e in art.get("entities") or [] if isinstance(e, dict) and e.get("id")
                    and e["id"] not in batch.hidden]
            ents.sort(key=lambda e: -similarity(ref["target"], e))
            ents = ents[:DOC_ENTITIES_MAX]
            doc = [{"id": e["id"], "name": e.get("name") or e["id"], "type": e.get("type"),
                    "aliases": list(e.get("aliases") or [])} for e in ents]
            own = {e["id"] for e in doc}
            scored = [(similarity(ref["target"], e), e) for eid, e in batch.pool.items() if eid not in own]
            cands = [e for s, e in sorted(scored, key=lambda t: (-t[0], t[1]["id"])) if s > 0][:CANDIDATES_MAX]
            it = items[(ref["sha"], ref["target"])] = {
                "sha": ref["sha"], "target": ref["target"], "refs": [], "mentions": [],
                "target_type": None, "document": doc, "candidates": cands,
                "offered": [e["id"] for e in doc + cands]}
        it["refs"].append([ref["entity"], ref["role"]])
        it["mentions"].append({"id": me.get("id"), "by": me.get("name") or me.get("id"),
                               "by_type": _canon(me.get("type")),
                               "relationship": (role.get("relationship") or "").strip()})
        if role.get("target_type") and not it["target_type"]:
            it["target_type"] = role["target_type"]
    return [it for it in items.values() if it["offered"]]


def build_prompt(items: list[dict]) -> str:
    from watchdog.pipeline import prompts
    blocks = []
    for n, it in enumerate(items, 1):
        lines = [f"{n}. target id: {json.dumps(it['target'], ensure_ascii=False)}"]
        for m in it["mentions"][:6]:
            lines.append(f"   named by: {json.dumps(m['by'], ensure_ascii=False)} ({m['by_type']}), "
                         f"as {json.dumps(m['relationship'], ensure_ascii=False)}")
        if it["document"]:
            lines.append("   entities in this document:")
            lines += [f"     - {_entity_line(e)}" for e in it["document"]]
        if it["candidates"]:
            lines.append("   elsewhere in the investigation:")
            lines += [f"     - {_entity_line(e)}" for e in it["candidates"]]
        blocks.append("\n".join(lines))
    return prompts.build_role_targets_prompt("\n\n".join(blocks))


def validate(item: dict, answer: dict):
    """Code's check on one answer: the matched entity's id, ``""`` for no match, or None to reject.
    A match must be one of the ids the item offered, not the entity that names the target, and its
    type must fit the `kind` the model gave (one of the six) and any type the extraction recorded
    for the target (`compatible`)."""
    match, kind = answer.get("match"), answer.get("kind")
    if not isinstance(match, str) or not isinstance(kind, (str, type(None))):
        return None
    match = match.strip()
    if not match:
        return ""
    if match not in item["offered"] or any(m["id"] == match for m in item["mentions"]):
        return None
    kind = (kind or "").strip().casefold()
    if kind not in KINDS:
        return None
    entity = next(e for e in item["document"] + item["candidates"] if e["id"] == match)
    if not compatible(entity.get("type"), kind):
        return None
    if item.get("target_type") and not compatible(entity.get("type"), item["target_type"]):
        return None
    return match


SCHEMA = housekeeping.answers_schema(
    {"match": {"type": "string"}, "kind": {"type": "string"}}, ["match", "kind"])

CHORE = housekeeping.Chore(task="role-targets", schema=SCHEMA, build_prompt=build_prompt,
                           validate=validate, max_items=40)


def apply(batch: Batch, items: list[dict], result: "housekeeping.ChoreResult") -> dict:
    """Write what the pass settled into the staged extractions, before the commit (I7). A match
    re-points every role of the item, keeping the first id in `extracted_target_id` and marking
    `target_basis: "inferred"`; an item the pass looked at and did not match is marked
    `target_unmatched`. Items no call covered are left for the next finalize. Returns
    ``{"matched", "unmatched", "roles"}``."""
    matched = unmatched = roles = 0
    touched = set()
    for idx, it in enumerate(items):
        if idx in result.unanswered:
            continue
        art = batch.artifacts.get(it["sha"])
        if art is None:
            continue
        value = result.settled.get(idx) or ""
        for i, j in it["refs"]:
            role = art["entities"][i]["roles"][j]
            if role.get("target_id") != it["target"]:
                continue
            if value:
                role.setdefault("extracted_target_id", role["target_id"])
                role["target_id"] = value
                role["target_basis"] = "inferred"
                role.pop("target_unmatched", None)
                roles += 1
            else:
                role["target_unmatched"] = True
            touched.add(it["sha"])
        if value:
            matched += 1
        else:
            unmatched += 1
    for sha in touched:
        batch.save(sha)
    return {"matched": matched, "unmatched": unmatched, "roles": roles}


def estimate_items(vault: Path, shas: list[str]) -> list[dict]:
    """The items the pass would send for `shas`, after code's own matches, without writing them:
    for the finalize estimate."""
    batch = Batch(vault, shas)
    batch.save = lambda sha: None   # an estimate never writes
    resolve_exact(batch)
    return pending_items(batch)


def inferred_note(role: dict) -> str:
    """The note a relationship whose target the model matched carries in markdown."""
    if role.get("target_basis") != "inferred":
        return ""
    from watchdog.pipeline.write_vault import _defang
    text = _defang(str(role.get("extracted_target_id") or "").strip())
    return f" *(target inferred from “{text}”)*" if text else " *(target inferred)*"

