"""
Watchdog post-flight — validates an extraction JSON and writes it to the vault.

Handles everything after Claude produces the extraction JSON:
  1. Validates the extraction (schema + required fields)
  2. Applies match_id decisions (Claude signals entity merges)
  3. Reads near-dup minhash from the queue file
  4. Calls write_vault.run() directly
  5. Cleans up temp files
  6. Returns {"ok": true} or {"errors": [...]}
"""

import json
import re
import sys
from pathlib import Path

_VALID_BASIS = {"stated", "inferred"}
_DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


def _validate(data: dict) -> list[str]:
    errors: list[str] = []

    doc = data.get("document")
    if not isinstance(doc, dict):
        errors.append("missing or invalid 'document' field")
    else:
        for field in ("sha256", "filename"):
            if not doc.get(field):
                errors.append(f"document.{field} is missing or empty")
        for i, fact in enumerate(doc.get("key_facts", [])):
            if not isinstance(fact, dict):
                errors.append(f"document.key_facts[{i}] is not an object")
            else:
                if fact.get("basis") and fact["basis"] not in _VALID_BASIS:
                    errors.append(f"document.key_facts[{i}].basis '{fact['basis']}' must be one of: {', '.join(sorted(_VALID_BASIS))}")
                if "entities" in fact and not isinstance(fact["entities"], list):
                    errors.append(f"document.key_facts[{i}].entities must be a list of entity ids")

    entities = data.get("entities")
    if not isinstance(entities, list):
        errors.append("missing or invalid 'entities' field")
    else:
        for i, ent in enumerate(entities):
            if not isinstance(ent, dict):
                errors.append(f"entities[{i}] is not an object")
                continue
            for field in ("id", "name", "type"):
                if not ent.get(field):
                    errors.append(f"entities[{i}].{field} is missing or empty")
            for j, role in enumerate(ent.get("roles", [])):
                if not isinstance(role, dict):
                    errors.append(f"entities[{i}].roles[{j}] must be an object with relationship/target_id/page/basis/date_range keys — not a string")

    if not data.get("morgue_entity_id"):
        errors.append("morgue_entity_id is missing or empty — this is the kebab-case id of the entity this document is primarily about")
    if not data.get("morgue_document_type"):
        errors.append("morgue_document_type is missing or empty — use a slug like annual-report, court-order, bankruptcy-filing")

    return errors


def _apply_match_ids(extraction: dict) -> dict:
    """Rewrite entity IDs based on Claude's match_id merge decisions.

    Also remaps any ``key_facts.entities`` tags that referenced the extraction-time id onto the
    canonical matched id, so the explode step files facts under the right entity.
    """
    remap: dict[str, str] = {}
    for entity in extraction.get("entities", []):
        match_id = entity.pop("match_id", None)
        if match_id:
            remap[entity["id"]] = match_id
            entity["id"] = match_id
    if remap:
        for fact in extraction.get("document", {}).get("key_facts", []):
            tags = fact.get("entities")
            if tags:
                fact["entities"] = [remap.get(t, t) for t in tags]
    return extraction


def _sanitize_entity_ids(extraction: dict) -> list[str]:
    """Slugify every entity ``id`` before it can reach write_vault, which uses it verbatim as a
    filesystem path segment (#303) — an unslugified id like ``"../../../ESCAPED"`` is a
    path-traversal / vault-escape write primitive. Ids are already meant to be kebab-case slugs
    (see extract_instructions.md), so this is a no-op warning-free pass for a well-formed
    extraction; it only changes anything for a malformed or hostile value.

    Falls back to a slug of the entity's ``name``, then a generic placeholder, if the id
    slugifies to empty, and disambiguates any collision this creates between two entities in the
    same extraction (so they don't silently merge). Remaps ``key_facts.entities`` tags and
    ``roles.target_id`` so referential integrity holds after the rewrite. Mutates ``extraction``
    in place; returns a warning per changed id."""
    from watchdog.pipeline.write_vault import slugify

    entities = extraction.get("entities", [])
    changes: list[tuple[str, str]] = []   # (old_id, new_id) for every id actually rewritten
    seen: set[str] = set()
    warnings: list[str] = []

    for i, entity in enumerate(entities):
        old_id = entity.get("id", "")
        new_id = slugify(old_id) or slugify(entity.get("name", "")) or f"entity-{i + 1}"
        base_id, suffix = new_id, 2
        while new_id in seen:
            new_id = f"{base_id}-{suffix}"
            suffix += 1
        seen.add(new_id)
        if new_id != old_id:
            warnings.append(f"entities[{i}].id {old_id!r} sanitized to {new_id!r}")
            if old_id:
                changes.append((old_id, new_id))
            entity["id"] = new_id

    # Only remap references for an old id that no longer names any surviving entity. If a
    # duplicate id was disambiguated (two "acme-corp" entities → the second becomes
    # "acme-corp-2"), the original id still belongs to the first entity, so references to it
    # must stay put rather than being misrouted to the renamed duplicate.
    remap = {old: new for old, new in changes if old not in seen}
    if remap:
        for fact in extraction.get("document", {}).get("key_facts", []):
            tags = fact.get("entities")
            if tags:
                fact["entities"] = [remap.get(t, t) for t in tags]
        for entity in entities:
            for role in entity.get("roles", []):
                tid = role.get("target_id")
                if tid in remap:
                    role["target_id"] = remap[tid]

    return warnings


def _sanitize_dates(extraction: dict) -> list[str]:
    """Drop ``key_facts.date`` values that aren't ISO-shaped (``YYYY``, ``YYYY-MM``, or
    ``YYYY-MM-DD``) before they can reach timeline.py's ``{date}_{sha7}.ndjson`` filename
    construction — a value like ``"2024/03"`` or free text would otherwise produce a broken or
    nested file write. Mutates ``key_facts`` in place; returns a warning per dropped date so the
    loss is visible rather than silent."""
    warnings: list[str] = []
    for i, fact in enumerate(extraction.get("document", {}).get("key_facts", [])):
        date = fact.get("date")
        if date and not _DATE_RE.match(date):
            warnings.append(
                f"document.key_facts[{i}].date '{date}' is not ISO-shaped (YYYY, YYYY-MM, or "
                "YYYY-MM-DD) — dropped from timeline placement"
            )
            fact["date"] = ""
    return warnings


def explode_key_facts(extraction: dict) -> None:
    """Reconstruct the per-entity views from the unified `key_facts` primitive (#140).

    The model emits each material fact once on ``document.key_facts``, tagged with the entity ids
    it concerns and an optional ``date``. Here we deterministically fan those tags back out onto
    each entity as ``evidence_fragments`` (every tagged fact) and ``timeline_events`` (tagged facts
    that carry a date), the per-entity shapes that write_vault already renders. Mutates the entities
    in place. The document-level ``key_facts`` are left intact (the document note and the global
    timeline read them directly).
    """
    by_id = {e["id"]: e for e in extraction.get("entities", []) if e.get("id")}
    for fact in extraction.get("document", {}).get("key_facts", []):
        text = (fact.get("fact") or "").strip()
        if not text:
            continue
        page = fact.get("page")
        basis = fact.get("basis")
        quote = fact.get("quote")
        date = (fact.get("date") or "").strip()
        for eid in fact.get("entities", []) or []:
            ent = by_id.get(eid)
            if ent is None:
                continue
            frag = {"claim": text}
            if page is not None:
                frag["page"] = page
            if basis:
                frag["basis"] = basis
            if quote:
                frag["quote"] = quote
            ent.setdefault("evidence_fragments", []).append(frag)
            if date:
                event = {"date": date, "event": text}
                if page is not None:
                    event["page"] = page
                if basis:
                    event["basis"] = basis
                ent.setdefault("timeline_events", []).append(event)


def run(vault: Path, extraction_path: Path, quiet: bool = False) -> dict:
    if not extraction_path.exists():
        return {"errors": [f"extraction file not found: {extraction_path}"]}

    try:
        extraction = json.loads(extraction_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"errors": [f"invalid JSON: {e}"]}

    errors = _validate(extraction)
    if errors:
        return {"errors": errors}

    extraction = _apply_match_ids(extraction)

    # Slugify entity ids before anything downstream uses them as a path segment (#303) —
    # a warning per id actually changed, so a malicious/malformed value is visible, not silent.
    for warning in _sanitize_entity_ids(extraction):
        print(f"Warning: {warning}", file=sys.stderr)

    # Drop non-ISO-shaped key_facts dates before they can reach explode_key_facts or
    # timeline.py's filename construction — a malformed date is a visible warning, not a
    # silent event loss.
    for warning in _sanitize_dates(extraction):
        print(f"Warning: {warning}", file=sys.stderr)

    # Fan the unified key_facts out into the per-entity evidence_fragments / timeline_events that
    # write_vault and timeline staging consume (#140).
    explode_key_facts(extraction)

    # Get near-dup minhash and page text from the queue file (both computed/captured at chew time)
    sha256 = extraction.get("document", {}).get("sha256", "")
    neardup_data: dict = {}
    page_texts: dict[int, str] = {}
    if sha256:
        queue_file = vault / ".watchdog" / "queue" / f"{sha256}.json"
        if queue_file.exists():
            try:
                q = json.loads(queue_file.read_text(encoding="utf-8"))
                neardup_data = q.get("near_dup", {})
                page_texts = {
                    p["page"]: p.get("markdown", "")
                    for p in q.get("pages", []) if p.get("page") is not None
                }
            except Exception:
                pass

    # Deterministic quote verification against the morgue text (#267): flags any
    # key_facts.quote that can't be matched on (or near) its cited page — annotation only,
    # never blocks the document.
    from watchdog.pipeline.quote_verify import verify_quotes
    for warning in verify_quotes(extraction, page_texts):
        print(f"Warning: {warning}", file=sys.stderr)

    # Write the validated (and match_id-resolved) extraction back so write_vault reads it
    extraction_path.write_text(
        json.dumps(extraction, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    try:
        from watchdog.pipeline.write_vault import run as wv_run
        wv_run(
            extraction_path=extraction_path,
            vault_path=vault,
            neardup_data=neardup_data,
            quiet=quiet,
        )
    except SystemExit as e:
        return {"errors": [str(e)]}
    except Exception as e:
        return {"errors": [str(e)]}

    # Stage raw timeline NDJSON files (replaces the pre-D18 subagent's manual
    # per-date writes). The vault is already written at this point, so a staging failure
    # is reported as a warning rather than failing the whole extraction —
    # erroring here would trigger a retry and double-write the vault.
    try:
        from watchdog.pipeline.timeline import stage_timeline_events
        stage_timeline_events(vault, extraction)
    except Exception as e:
        print(f"Warning: timeline staging failed: {e}", file=sys.stderr)

    # Clean up temp files
    for path in [
        extraction_path,
        vault / ".watchdog" / "tmp" / f"wdg_nd_{sha256}.json",
    ]:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    return {"ok": True, "sha256": sha256}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Watchdog post-flight processor")
    parser.add_argument("--extraction", required=True, help="Path to extraction JSON")
    args = parser.parse_args()

    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")

    extraction_path = Path(args.extraction).resolve()
    if not str(extraction_path).startswith(str(vault) + "/"):
        sys.exit(f"Error: --extraction must be inside the vault directory ({vault})")

    result = run(vault, extraction_path)
    print(json.dumps(result, ensure_ascii=False))
    if "errors" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
