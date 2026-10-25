"""Fact tags that name no entity of their document (D333), the third housekeeping chore.

A fact's `entities` tags name the entities it concerns, by the ids its own document defines. A tag
naming an id the document never defined is dropped by `postflight.explode_key_facts` with a warning
(D211): the fact stays in the document note but never reaches the entity's page or its timeline.
Post-flight already points a near miss (spacing, a shorter or longer form) at the id it meant
(`postflight._resolve_near_miss_tags`, #757). This pass takes the rest, those that need judgement —
a company tagged by an abbreviation the document defines under its full name:

- **When.** First in finalize, before the exact-name fold, so a re-tagged fact takes part in the
  identity evidence, reconciliation and contradiction check like any other, and the commit writes
  it to the entity's page and timeline (I7).
- **The model's input.** One item per document and dangling tag: the tag, up to three facts carrying
  it, and the document's entities (ids, names, aliases and types).
- **The model's answer.** The defined id the tag means, or nothing.
- **Code applies it** (`validate`, `apply`). The id must be one the document defines. Every fact of
  the document carrying the tag is re-tagged (its original tags kept in `extracted_entities`, as
  the fold keeps them), the re-tagged id recorded in `entities_inferred` so the link shows as
  inferred on the entity's page (I1), and the document's entity views and raw timeline files are
  rebuilt. Nothing, or a rejected answer, leaves the tag to be dropped as before, recorded in
  `tags_unmatched` so the next finalize of the same batch does not ask again.

`stats` reports the dangling-tag rate of the batch for the run's log.
"""

from __future__ import annotations

import json
from pathlib import Path

from watchdog.pipeline import housekeeping
from watchdog.pipeline.json_io import _read_json_or

DOC_ENTITIES_MAX = 60
EXAMPLE_FACTS = 3


def _defined(art: dict) -> list[dict]:
    return [e for e in art.get("entities") or [] if isinstance(e, dict) and e.get("id")]


def _facts(art: dict) -> list:
    return (art.get("document") or {}).get("key_facts") or []


def dangling_tags(art: dict) -> dict[str, list[int]]:
    """Each tag of a fact that names no entity of the document → the indices of the facts carrying
    it. A tag the pass already looked at for that fact is left out."""
    ids = {e["id"] for e in _defined(art)}
    out: dict[str, list[int]] = {}
    for i, f in enumerate(_facts(art)):
        if not isinstance(f, dict) or not (f.get("fact") or "").strip():
            continue
        seen = set(f.get("tags_unmatched") or [])
        for t in f.get("entities") or []:
            if isinstance(t, str) and t and t not in ids and t not in seen:
                out.setdefault(t, [])
                if i not in out[t]:
                    out[t].append(i)
    return out


def stats(vault: Path, shas: list[str]) -> dict:
    """The batch's fact tags: ``{"tags", "dangling"}``, every tag on a fact with text, and those
    naming no entity of the document (whether or not the pass looked at them)."""
    tags = dangling = 0
    for sha in shas:
        art = _read_json_or(vault / ".watchdog" / "extracted" / f"{sha}.json", None)
        if not isinstance(art, dict):
            continue
        ids = {e["id"] for e in _defined(art)}
        for f in _facts(art):
            if isinstance(f, dict) and (f.get("fact") or "").strip():
                for t in f.get("entities") or []:
                    if isinstance(t, str) and t:
                        tags += 1
                        dangling += t not in ids
    return {"tags": tags, "dangling": dangling}


def pending_items(vault: Path, shas: list[str]) -> list[dict]:
    """One item per staged document and dangling tag: ``{"sha", "tag", "facts": [index…],
    "examples": [text…], "entities": [entity…], "offered": [ids]}``."""
    from watchdog.pipeline.role_targets import similarity
    items = []
    for sha in shas:
        art = _read_json_or(vault / ".watchdog" / "extracted" / f"{sha}.json", None)
        if not isinstance(art, dict):
            continue
        defined = _defined(art)
        if not defined:
            continue
        facts = _facts(art)
        for tag, idxs in dangling_tags(art).items():
            ents = sorted(defined, key=lambda e: -similarity(tag, e))[:DOC_ENTITIES_MAX]
            items.append({
                "sha": sha, "tag": tag, "facts": idxs,
                "examples": [(facts[i].get("fact") or "").strip() for i in idxs[:EXAMPLE_FACTS]],
                "entities": [{"id": e["id"], "name": e.get("name") or e["id"], "type": e.get("type"),
                              "aliases": list(e.get("aliases") or [])} for e in ents],
                "offered": [e["id"] for e in ents]})
    return items


def build_prompt(items: list[dict]) -> str:
    from watchdog.pipeline import prompts
    from watchdog.pipeline.role_targets import _entity_line
    blocks = []
    for n, it in enumerate(items, 1):
        lines = [f"{n}. tag: {json.dumps(it['tag'], ensure_ascii=False)}"]
        lines += [f"   fact: {json.dumps(t, ensure_ascii=False)}" for t in it["examples"]]
        lines.append("   entities in this document:")
        lines += [f"     - {_entity_line(e)}" for e in it["entities"]]
        blocks.append("\n".join(lines))
    return prompts.build_fact_tags_prompt("\n\n".join(blocks))


def validate(item: dict, answer: dict):
    """Code's check on one answer: an id the document defines, ``""`` for none, or None to reject."""
    match = answer.get("match")
    if not isinstance(match, str):
        return None
    match = match.strip()
    if not match:
        return ""
    return match if match in item["offered"] else None


SCHEMA = housekeeping.answers_schema({"match": {"type": "string"}}, ["match"])

CHORE = housekeeping.Chore(task="fact-tags", schema=SCHEMA, build_prompt=build_prompt,
                           validate=validate, max_items=40)


def apply(vault: Path, items: list[dict], result: "housekeeping.ChoreResult") -> dict:
    """Write what the pass settled into the staged extractions (I7) and rebuild each changed
    document's entity views and raw timeline files. Items no call covered are left for the next
    finalize. Returns ``{"retagged": tags matched, "facts": facts changed, "unmatched"}``."""
    from watchdog.pipeline import timeline
    from watchdog.pipeline.postflight import explode_key_facts
    by_sha: dict[str, list[int]] = {}
    for idx, it in enumerate(items):
        if idx not in result.unanswered:
            by_sha.setdefault(it["sha"], []).append(idx)
    retagged = changed_facts = unmatched = 0
    for sha, idxs in by_sha.items():
        path = vault / ".watchdog" / "extracted" / f"{sha}.json"
        art = _read_json_or(path, None)
        if not isinstance(art, dict):
            continue
        facts = _facts(art)
        ids = {e["id"] for e in _defined(art)}
        moved = False
        for idx in idxs:
            it = items[idx]
            value = result.settled.get(idx) or ""
            if value and value not in ids:
                value = ""
            retagged += bool(value)
            unmatched += not value
            for i in it["facts"]:
                f = facts[i] if 0 <= i < len(facts) and isinstance(facts[i], dict) else None
                if f is None or it["tag"] not in (f.get("entities") or []):
                    continue
                if value:
                    f.setdefault("extracted_entities", list(f["entities"]))
                    f["entities"] = list(dict.fromkeys(value if t == it["tag"] else t
                                                       for t in f["entities"]))
                    inferred = f.setdefault("entities_inferred", [])
                    if value not in inferred:
                        inferred.append(value)
                    changed_facts += 1
                    moved = True
                else:
                    unseen = f.setdefault("tags_unmatched", [])
                    if it["tag"] not in unseen:
                        unseen.append(it["tag"])
        if moved:
            for e in art.get("entities") or []:
                if isinstance(e, dict):
                    e.pop("evidence_fragments", None)
                    e.pop("timeline_events", None)
            explode_key_facts(art)
            timeline.restage_document(vault, art)
        path.write_text(json.dumps(art, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"retagged": retagged, "facts": changed_facts, "unmatched": unmatched}


def inferred_for(fact: dict, eid: str) -> bool:
    """Whether `fact` reached `eid` through a tag the model matched (D333). `eid` is the record's
    current id; a merge since keeps the mark through `resolve` (an `entity_facts.FactIndex`)."""
    return eid in (fact.get("entities_inferred") or [])
