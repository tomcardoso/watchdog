#!/usr/bin/env python3
"""Build the entity-synthesis bundle and apply the synthesized prose (D26, D129, D238, D280).

Gathers every entity touched this batch whose `appears_in` reaches `min_docs` across the vault and
hands the model the entity's FACTS — read from the stored extractions (`entity_facts`), each with
its D271 id as a short citation, date, document, page and warnings — not its earlier prose. A large
entity's facts are cut to a budget (this batch's facts, the reporter's verified ones, then the most
recent), and the bundle says so. Facts the reporter marked Disputed are withheld. `apply_bundle`
stores the returned prose in the registry (`synthesis`) with what it was written from, and
re-renders the note. Library functions only, called from `orchestrate`."""

import json
import sys
from pathlib import Path

from watchdog.pipeline import entity_facts
from watchdog.pipeline.finalize_entity import apply_one
from watchdog.pipeline.write_vault import (
    _write_json_atomic,
    _defang,
    _figure_verification_note,
    _update_manifest,
)

# A synthesis call sees at most this many of an entity's facts, and at most this many characters of
# them, so a hub entity named in hundreds of documents stays one bounded call (D280).
FACT_MAX = 120
FACT_BUDGET_CHARS = 24_000
_ROLE_MAX = 15


def _fact_line(f: dict, ref: str, new: bool) -> str:
    bits = [f"[{ref}]"]
    if f.get("date"):
        bits.append(f"({f['date']})")
    bits.append(_defang(f.get("fact") or ""))
    src = _defang(f.get("title") or "")
    if f.get("doc_date"):
        src += f", {f['doc_date']}"
    if f.get("page"):
        src += f", p. {f['page']}"
    bits.append(f"— {src}")
    flags = []
    if f.get("basis") == "inferred":
        flags.append("inferred")
    figure = _figure_verification_note(f).strip().removeprefix("*(").removesuffix(")*")
    if figure:
        flags.append(figure)
    if (f.get("mark") or {}).get("status") == "verified":
        flags.append("verified")
    elif (f.get("mark") or {}).get("status") == "unverifiable":
        flags.append("the reporter could not verify this")
    if new:
        flags.append("new")
    if flags:
        bits.append("[" + "; ".join(flags) + "]")
    return " ".join(bits)


def select_facts(facts: list[dict], batch: set[str], max_facts: int | None = None,
                 budget: int | None = None) -> list[dict]:
    """The facts one synthesis call is shown, in date order: all of them when they fit; else this
    batch's facts first, then the reporter's verified ones, then the most recent, until `max_facts`
    or `budget` characters is reached. Disputed facts are never shown."""
    max_facts = FACT_MAX if max_facts is None else max_facts
    budget = FACT_BUDGET_CHARS if budget is None else budget
    usable = [f for f in facts if (f.get("mark") or {}).get("status") != "disputed"]
    if len(usable) <= max_facts and sum(len(f.get("fact") or "") + 60 for f in usable) <= budget:
        return entity_facts.chronological(usable)
    recent_first = list(reversed(entity_facts.chronological(usable)))

    def rank(f):
        return (f["sha"] not in batch, (f.get("mark") or {}).get("status") != "verified")
    chosen, size = [], 0
    for f in sorted(recent_first, key=rank):     # stable: recency breaks ties within a rank
        cost = len(f.get("fact") or "") + 60
        if len(chosen) >= max_facts or (chosen and size + cost > budget):
            break
        chosen.append(f)
        size += cost
    return entity_facts.chronological(chosen)


def build_bundle(vault_path: Path, shas: list[str], min_docs: int = 2) -> dict:
    """Gather every entity that recurs across the project into one synthesis bundle (#140, D280).

    The gate is **project-wide recurrence**: an entity earns a synthesized summary once it
    appears in ``min_docs`` (default 2) distinct documents across the whole investigation — read
    from its registry ``appears_in``, not from this batch's mention count. Only entities *touched
    this run* (named in one of ``shas``, the current batch's staged extractions) are candidates —
    an untouched entity has nothing new to synthesize. Single-document entities keep their facts
    and no summary.

    ``shas`` is deliberately the current batch's result shas, not ``_pending_commits(vault)``:
    on a resume (synthesis rate-limited after commit already landed), the docs are already in
    the registry, so `_pending_commits` is empty, but the batch's ``result_*.json`` files persist
    and a re-run must still re-synthesize them.

    Returns ``{"entities": [...], "meta": {id: {...}}}``: the first is what the model sees, the
    second what `apply_bundle` stores beside the prose (counts and the citation map)."""
    index = entity_facts.FactIndex(vault_path)
    batch = set(shas)
    touched: set[str] = set()
    for sha in sorted(shas):
        try:
            artifact = json.loads((vault_path / ".watchdog" / "extracted" / f"{sha}.json")
                                  .read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for incoming in artifact.get("entities") or []:
            eid = incoming.get("id")
            if eid:
                touched.add(index.resolve(sha, eid) or eid)

    entities, meta = [], {}
    for eid in sorted(touched):
        rec = index.entities.get(eid) or {}
        if len(rec.get("appears_in") or []) < min_docs or not rec.get("note_path"):
            continue
        facts = index.facts_for(eid)
        shown = select_facts(facts, batch)
        withheld = sum(1 for f in facts if (f.get("mark") or {}).get("status") == "disputed")
        refs = entity_facts.short_refs(shown)
        usable = len(facts) - withheld
        if len(shown) < usable:
            selection = (f"{len(shown)} of the entity's {usable} facts are shown: this batch's, the "
                         f"reporter's verified ones, then the most recent. {usable - len(shown)} "
                         "older facts are not shown.")
        else:
            selection = "All of the entity's facts are shown."
        roles = [f"{r.get('relationship', '')} {r.get('target_name', '')}".strip()
                 for r in rec.get("roles") or [] if not r.get("is_reverse")][:_ROLE_MAX]
        entities.append({
            "entity_id": eid, "name": rec.get("name", ""), "type": rec.get("type", ""),
            "aliases": (rec.get("aliases") or [])[:10],
            "documents": len(rec.get("appears_in") or []),
            "selection": selection, "withheld": withheld,
            "roles": roles,
            "facts": [_fact_line(f, refs[f["id"]], f["sha"] in batch) for f in shown],
        })
        meta[eid] = {"facts_total": len(facts), "facts_shown": len(shown),
                     "withheld_disputed": withheld,
                     "fact_refs": {refs[f["id"]]: f["id"] for f in shown}}

    entities.sort(key=lambda e: e["name"].lower())
    return {"entities": entities, "meta": meta}


def _apply_bundle_unlocked(result_path: Path, vault_path: Path, meta: dict | None = None) -> dict:
    """Store synthesized prose from the model's result JSON and re-render each note.

    Validates conservatively: unknown entity ids and entries with an empty summary are skipped
    (the entity keeps the synthesis it had). `meta` (from `build_bundle`) is stored beside each
    summary; an item's `model` names the model that wrote it. Writes entities.json + manifest
    once after all entities are applied. Citations are stored as the model wrote them, with the
    map from each short id to its D271 id; checking and linking them is a later step."""
    result = json.loads(result_path.read_text(encoding="utf-8"))
    syntheses = result.get("entity_syntheses", [])

    registry_dir   = vault_path / ".watchdog" / "registry"
    entities_path  = registry_dir / "entities.json"
    documents_path = registry_dir / "documents.json"
    if not entities_path.exists():
        sys.exit("Error: entities.json not found — is this a Watchdog vault?")

    entities_reg  = json.loads(entities_path.read_text())
    documents_reg = json.loads(documents_path.read_text()) if documents_path.exists() else {}

    applied, skipped = [], []
    for item in syntheses:
        eid = item.get("entity_id")
        summary = (item.get("summary") or "").strip()
        if not eid or not summary:
            skipped.append(eid or "<missing id>")
            continue
        analysis = (item.get("analysis") or "").strip()
        info = {**((meta or {}).get(eid) or {}), "by": "model", "model": item.get("model")}
        if apply_one(eid, summary, analysis, vault_path, entities_reg, documents_reg, meta=info):
            applied.append(eid)
        else:
            skipped.append(eid)

    if applied:
        _write_json_atomic(entities_path, entities_reg)
        _update_manifest(vault_path, entities_reg)

    return {"applied": applied, "skipped": skipped}


def apply_bundle(result_path: Path, vault_path: Path, meta: dict | None = None) -> dict:
    """`_apply_bundle_unlocked` under the registry lock every registry writer takes (D258). It can run
    from a Claude Code session or a second terminal while `watchdog bark` commits, and an
    unlocked read-modify-write here could write back a stale `entities.json` over that commit."""
    from watchdog.pipeline.write_vault import _registry_lock
    registry_dir = Path(vault_path) / ".watchdog" / "registry"
    if not registry_dir.is_dir():
        return _apply_bundle_unlocked(result_path, vault_path, meta)
    with _registry_lock(registry_dir):
        return _apply_bundle_unlocked(result_path, vault_path, meta)
