"""
Watchdog section merge — deterministically combine per-section extraction JSONs
into one document-level extraction JSON.

Sectioned extraction carries a running scratchpad forward, so each section
reuses the entity ids found by earlier sections. That makes this merge a pure
set-union — no LLM reasoning:

  * entities grouped by id; aliases / timeline_events / roles unioned
  * a normalized-name pass folds any id drift (OCR variance) onto one id
  * document key_facts concatenated and deduped

The output is shape-identical to a single-document extraction JSON, so it feeds
straight into `watchdog post-flight` unchanged.
"""

import json
import re
import sys
from pathlib import Path


def _norm(text: str) -> str:
    """Normalized surface form for drift dedup."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _event_key(ev: dict) -> str:
    return f"{ev.get('date', '')}|{(ev.get('event') or '')[:80].lower()}"


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
    out, seen = [], set()
    for f in facts:
        k = (f.get("fact") or "")[:120].lower()
        if k and k not in seen:
            seen.add(k)
            out.append(f)
    return out


def merge_extractions(sections: list[dict]) -> dict:
    """Merge a list of partial extraction dicts into one extraction dict."""
    docs = [s.get("document") or {} for s in sections]
    document = next((dict(d) for d in docs if d), {})

    key_facts: list = []
    for d in docs:
        key_facts.extend(d.get("key_facts", []))

    by_id: dict[str, dict] = {}
    norm_index: dict[str, str] = {}   # normalized surface form -> canonical id
    morgue_entity_id = None
    morgue_document_type = None

    for sec in sections:
        morgue_entity_id = morgue_entity_id or sec.get("morgue_entity_id")
        morgue_document_type = morgue_document_type or sec.get("morgue_document_type")

        for ent in sec.get("entities", []):
            eid = ent.get("id")
            if not eid:
                continue
            surfaces = [ent.get("name", "")] + list(ent.get("aliases", []))

            # Fold id drift: reuse an existing id that shares a surface form.
            for nm in surfaces:
                k = _norm(nm)
                if k and k in norm_index:
                    eid = norm_index[k]
                    break

            cur = by_id.get(eid)
            if cur is None:
                cur = {
                    "id": eid,
                    "name": ent.get("name", ""),
                    "type": ent.get("type", ""),
                    "_surfaces": set(),
                    "summary": None,
                    "analysis": None,
                    "timeline_events": [],
                    "roles": [],
                }
                by_id[eid] = cur

            if len(ent.get("name", "")) > len(cur["name"]):
                cur["name"] = ent["name"]
            cur["type"] = cur["type"] or ent.get("type", "")
            cur["summary"] = cur["summary"] or ent.get("summary")

            analysis = ent.get("analysis")
            if analysis and (not cur["analysis"] or analysis not in cur["analysis"]):
                cur["analysis"] = (
                    (cur["analysis"] + "\n\n" + analysis).strip()
                    if cur["analysis"] else analysis
                )

            for nm in surfaces:
                if nm:
                    cur["_surfaces"].add(nm)
            _merge_into(cur["timeline_events"], ent.get("timeline_events", []), _event_key)
            _merge_into(cur["roles"], ent.get("roles", []), _role_key)

            for nm in surfaces:
                k = _norm(nm)
                if k:
                    norm_index.setdefault(k, eid)

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
        if not cur["summary"]:
            cur.pop("summary")
        if not cur["analysis"]:
            cur.pop("analysis")
        entities.append(cur)

    document.pop("key_facts", None)
    document["key_facts"] = _dedup_key_facts(key_facts)

    return {
        "document": document,
        "entities": entities,
        "morgue_entity_id": morgue_entity_id,
        "morgue_document_type": morgue_document_type,
    }


def run(vault: Path, sha256: str) -> dict:
    tmp = vault / ".watchdog" / "tmp"
    section_files = sorted(tmp.glob(f"section_ex_{sha256}_*.json"))
    if not section_files:
        return {"error": f"no section extraction files found for sha256 {sha256}"}

    sections = []
    for f in section_files:
        try:
            sections.append(json.loads(f.read_text(encoding="utf-8")))
        except json.JSONDecodeError as e:
            return {"error": f"invalid section JSON {f.name}: {e}"}

    merged = merge_extractions(sections)
    out = tmp / f"wdg_ex_{sha256}.json"
    out.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    # New-vs-updated split, computed against the registry *before* post-flight writes.
    registry: dict = {}
    reg_path = vault / ".watchdog" / "Registry" / "entities.json"
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


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("Usage: watchdog merge-sections <sha256>")
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")
    result = run(vault, sys.argv[1])
    if "error" in result:
        sys.exit(f"Error: {result['error']}")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
