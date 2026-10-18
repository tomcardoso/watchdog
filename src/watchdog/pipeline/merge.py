"""
Watchdog section merge — deterministically combine per-section extraction JSONs
into one document-level extraction JSON.

Sectioned extraction carries a running scratchpad forward, so each section
reuses the entity ids found by earlier sections. That makes this merge a pure
set-union — no LLM reasoning:

  * entities grouped by id; aliases / roles unioned (the graph layer)
  * a normalized-name pass folds id drift (OCR variance) onto one id — same type only, a person
    only on a full name — and logs each fold as a merge (D280)
  * document key_facts concatenated and deduped (the fact layer, including each
    fact's date / entity tags — postflight fans these back out per entity)
  * document_requests unioned across sections, deduped by normalized `what` text (#365)

The output is shape-identical to a single-document extraction JSON, so it feeds
straight into postflight (`pipeline/postflight.py`) unchanged.
"""

import json
from pathlib import Path

from watchdog.pipeline.entity_norm import normalize_entity_name
from watchdog.pipeline.entity_type import canonical_type
from watchdog.pipeline.json_io import _read_json


def _role_key(role: dict) -> tuple:
    return ((role.get("relationship") or "").lower(), role.get("target_id"))


def _merge_into(acc: list, incoming: list, key_fn) -> None:
    seen = {key_fn(x) for x in acc}
    for item in incoming:
        k = key_fn(item)
        if k not in seen:
            acc.append(item)
            seen.add(k)


def _dedup_key_facts(facts: list) -> list:
    """Dedup by full fact text (not a prefix — a truncated key can conflate two distinct facts
    that merely share a long opening clause); union entity tags and keep a date if any
    duplicate carries one."""
    out: list = []
    by_key: dict[str, dict] = {}
    for f in facts:
        k = (f.get("fact") or "").lower()
        if not k:
            continue
        if k not in by_key:
            kept = dict(f)
            kept["entities"] = list(f.get("entities") or [])
            by_key[k] = kept
            out.append(kept)
        else:
            kept = by_key[k]
            for eid in f.get("entities") or []:
                if eid not in kept["entities"]:
                    kept["entities"].append(eid)
            if not kept.get("date") and f.get("date"):
                kept["date"] = f["date"]
    for kept in out:
        if not kept.get("entities"):
            kept.pop("entities", None)
    return out


def _dedup_document_requests(requests: list) -> list:
    """Dedup by normalized `what` (whitespace-collapsed, lowercased), keeping first-seen order
    and first-seen wording — same idea as `_dedup_key_facts`, one field instead of the whole
    fact text."""
    seen: set[str] = set()
    out: list = []
    for r in requests:
        if not isinstance(r, dict):
            continue
        what = (r.get("what") or "").strip()
        if not what:
            continue
        key = " ".join(what.split()).lower()
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _may_fold(ent: dict, cur: dict, surface: str) -> bool:
    """Whether a later section's entity may fold onto an earlier one by a shared name (D280): only
    when both are the same canonical type, and for a person only on a full name — an initialled
    or partial name is never merged automatically (D279), even within one document."""
    if canonical_type(ent.get("type", "")) != canonical_type(cur.get("type", "")):
        return False
    if canonical_type(ent.get("type", "")) == "person":
        from watchdog.pipeline.identity import is_full_person_name
        return is_full_person_name(surface)
    return True


def merge_extractions(sections: list[dict], sha: str | None = None) -> dict:
    """Merge a list of partial extraction dicts into one extraction dict.

    A section that coins a new id for an entity an earlier section already named (id drift) is
    folded onto the earlier id when `_may_fold` allows it; that section's fact tags and role
    targets follow, and the fold is logged as a merge on the extraction's `identity` block, written
    to the merge log when the document commits (D279, D280). With `sha`, the log records the
    document and the moved facts' ids."""
    docs = [s.get("document") or {} for s in sections]
    document = next((dict(d) for d in docs if d), {})

    document_requests: list = []
    for sec in sections:
        document_requests.extend(sec.get("document_requests") or [])
    document_requests = _dedup_document_requests(document_requests)

    by_id: dict[str, dict] = {}
    norm_index: dict[str, str] = {}   # normalized surface form -> canonical id
    morgue_entity_id = None
    remaps: list[dict[str, str]] = []                 # per section: drifted id -> kept id
    folds: list[dict] = []

    for n, sec in enumerate(sections, 1):
        morgue_entity_id = morgue_entity_id or sec.get("morgue_entity_id")
        remap: dict[str, str] = {}

        for ent in sec.get("entities", []):
            eid = ent.get("id")
            if not eid:
                continue
            surfaces = [ent.get("name", "")] + list(ent.get("aliases", []))

            # Fold id drift: reuse an existing id that shares a surface form, when allowed.
            if eid not in by_id:
                for nm in surfaces:
                    k = normalize_entity_name(nm)
                    if k and k in norm_index and _may_fold(ent, by_id[norm_index[k]], nm):
                        target = norm_index[k]
                        remap[eid] = target
                        folds.append({"section": n, "merged": {"id": eid, "name": ent.get("name", ""),
                                                               "type": ent.get("type", "")},
                                      "keep": target, "surface": nm})
                        eid = target
                        break

            cur = by_id.get(eid)
            if cur is None:
                cur = {
                    "id": eid,
                    "name": ent.get("name", ""),
                    "type": ent.get("type", ""),
                    "_surfaces": set(),
                    "roles": [],
                }
                by_id[eid] = cur

            if len(ent.get("name", "")) > len(cur["name"]):
                cur["name"] = ent["name"]
            cur["type"] = cur["type"] or ent.get("type", "")

            for nm in surfaces:
                if nm:
                    cur["_surfaces"].add(nm)
            _merge_into(cur["roles"], ent.get("roles", []), _role_key)

            for nm in surfaces:
                k = normalize_entity_name(nm)
                if k:
                    norm_index.setdefault(k, eid)
        remaps.append(remap)

    # Each section's facts and role targets follow that section's folds.
    key_facts: list = []
    for d, remap in zip(docs, remaps):
        for f in d.get("key_facts", []):
            tags = f.get("entities")
            if remap and tags and any(t in remap for t in tags):
                f = {**f, "extracted_entities": list(tags),
                     "entities": list(dict.fromkeys(remap.get(t, t) for t in tags))}
            key_facts.append(f)
    all_remaps = {k: v for r in remaps for k, v in r.items()}
    for cur in by_id.values():
        for role in cur["roles"]:
            if role.get("target_id") in all_remaps:
                role.setdefault("extracted_target_id", role["target_id"])
                role["target_id"] = all_remaps[role["target_id"]]

    entities = []
    for cur in by_id.values():
        surfaces = cur.pop("_surfaces")
        name_lower = cur["name"].lower()
        aliases, seen = [], set()
        for nm in surfaces:
            low = nm.lower()
            if low == name_lower or low in seen:
                continue
            seen.add(low)
            aliases.append(nm)
        cur["aliases"] = aliases
        entities.append(cur)

    document.pop("key_facts", None)
    document["key_facts"] = _dedup_key_facts(key_facts)

    # Fallback if no section ever supplied morgue_entity_id (#505) — section 1 is the only
    # section ever asked for it (prompts.py), so a single miss there is otherwise a single point
    # of failure for the whole document. Deterministic, not a model call: the entity mentioned
    # in the most key_facts is the closest available proxy for "primarily about", falling back to
    # the first entity encountered when no fact carries an entity tag. Stays None (still a
    # post-flight failure) when there are no entities at all — nothing to derive from. A later
    # pre-commit reconciliation pass (#513) keeps this value in sync if the entity it names gets
    # folded into a different canonical id.
    if not morgue_entity_id and entities:
        mention_counts: dict[str, int] = {}
        for fact in document["key_facts"]:
            for eid in fact.get("entities") or []:
                mention_counts[eid] = mention_counts.get(eid, 0) + 1
        morgue_entity_id = (
            max(mention_counts, key=mention_counts.get) if mention_counts else entities[0]["id"]
        )

    merged = {
        "document": document,
        "entities": entities,
        "morgue_entity_id": morgue_entity_id,
    }
    if document_requests:
        merged["document_requests"] = document_requests
    if folds:
        merged["identity"] = {"version": 1, "log": _fold_log(folds, by_id, document["key_facts"], sha)}
    return merged


def _fold_log(folds: list[dict], by_id: dict, facts: list[dict], sha: str | None) -> list[dict]:
    """A merge-log entry for each cross-section fold: decided by rule, high tier, with the shared
    name and the section as evidence. Undo is not offered: the split would have to re-run the
    section that coined the second id."""
    from watchdog.pipeline import merge_log
    from watchdog.pipeline.verification import fact_ids
    ids = fact_ids(sha, [f for f in facts if (f.get("fact") or "").strip()]) if sha else []
    moved = [f for f in facts if (f.get("fact") or "").strip()]
    out = []
    for fold in folds:
        keep = by_id.get(fold["keep"], {})
        mine = [fid for fid, f in zip(ids, moved)
                if fold["merged"]["id"] in (f.get("extracted_entities") or [])]
        out.append(merge_log.merge_entry(
            keep={"id": keep.get("id"), "name": keep.get("name"), "type": canonical_type(keep.get("type", ""))},
            merged={**fold["merged"], "type": canonical_type(fold["merged"]["type"])},
            tier="high", decided_by="rule", rule="same-document-section",
            reason=(f"The same name, “{fold['surface']}”, in two sections of one document that was "
                    f"read in sections (section {fold['section']} gave it a second id)."),
            occurrence={"sha": sha, "documents": [sha] if sha else [], "facts": mine[:5],
                        "identifier": None, "shared": [], "section": fold["section"]},
            undo={"version": 2, "available": False,
                  "reason": "Merged inside one document while it was read in sections."}))
    return out


def run(vault: Path, sha256: str) -> dict:
    tmp = vault / ".watchdog" / "tmp"
    section_files = sorted(tmp.glob(f"section_ex_{sha256}_*.json"))
    if not section_files:
        return {"error": f"no section extraction files found for sha256 {sha256}"}

    sections = []
    for f in section_files:
        try:
            sections.append(_read_json(f))
        except json.JSONDecodeError as e:
            return {"error": f"invalid section JSON {f.name}: {e}"}

    merged = merge_extractions(sections, sha=sha256)
    out = tmp / f"wdg_ex_{sha256}.json"
    out.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    # New-vs-updated split, computed against the registry *before* post-flight writes.
    registry: dict = {}
    reg_path = vault / ".watchdog" / "registry" / "entities.json"
    if reg_path.exists():
        try:
            registry = json.loads(reg_path.read_text(encoding="utf-8"))
        except Exception:
            registry = {}
    new_entities = {e["id"]: e["name"] for e in merged["entities"] if e["id"] not in registry}
    updated_entities = {e["id"]: e["name"] for e in merged["entities"] if e["id"] in registry}

    return {
        "ok": True,
        "extraction_path": str(out.relative_to(vault)),
        "entity_count": len(merged["entities"]),
        "new_entities": new_entities,
        "updated_entities": updated_entities,
        "sections_merged": len(sections),
    }
