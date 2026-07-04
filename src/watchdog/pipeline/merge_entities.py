"""
Deterministic registry surgery: fold one entity into another (#219).

Three shipped features can *detect* a duplicate entity — the dashboard's "Possible
duplicates" view, the `/watchdog-health` near-duplicate check, and D39's Neo4j-export
tradeoff note ("the same person under name variants appears as separate nodes, which
the export can't fix") — but none of them could *fix* one. `merge()` is the surgery;
`run()` is the vault-level operation `watchdog merge-entities <keep-id> <merge-id>`
drives. Pure I1-side code: no model calls, no judgement calls beyond the two ids the
caller supplies.
"""

import json
from pathlib import Path

from watchdog.pipeline.write_vault import (
    _accumulate_contradictions,
    _extract_analysis,
    _extract_contradictions,
    _extract_notes_section,
    _extract_section,
    _extract_summary,
    _now_iso,
    _timeline_dedup_key,
    _today,
    _update_manifest,
    build_entity_note,
    _frontmatter,
)
from watchdog.pipeline.timeline import cmd_rebuild_timeline

_DEFAULT_NOTES_MARKER = "<!-- Journalist annotations — never overwritten by ingestion. -->"


# ── Pure registry surgery ──────────────────────────────────────────────────────

def _union_aliases(keep: dict, merge_entry: dict) -> list[str]:
    aliases = list(keep.get("aliases", []))
    known_lower = {a.lower() for a in aliases} | {keep["name"].lower()}
    for candidate in [merge_entry["name"], *merge_entry.get("aliases", [])]:
        if candidate.lower() not in known_lower:
            aliases.append(candidate)
            known_lower.add(candidate.lower())
    return aliases


def _union_appears_in(keep: dict, merge_entry: dict) -> list[str]:
    result = list(keep.get("appears_in", []))
    seen = set(result)
    for sha in merge_entry.get("appears_in", []):
        if sha not in seen:
            result.append(sha)
            seen.add(sha)
    return result


def _union_timeline_events(keep: dict, merge_entry: dict) -> list[dict]:
    result = list(keep.get("timeline_events", []))
    seen = {_timeline_dedup_key(e) for e in result}
    for ev in merge_entry.get("timeline_events", []):
        key = _timeline_dedup_key(ev)
        if key not in seen:
            result.append(ev)
            seen.add(key)
    return result


def _clean_roles(
    entry_id: str, roles: list[dict], keep_id: str, merge_id: str, keep_name: str, keep_type: str
) -> list[dict]:
    """Remap any role targeting the losing id onto the surviving one, drop
    roles that would now point at the entity itself (self-referential after
    the remap, or because the two merged entities already pointed at each
    other), and dedupe by (relationship, target_id)."""
    result = []
    seen = set()
    for role in roles:
        role = dict(role)
        if role.get("target_id") == merge_id:
            role["target_id"] = keep_id
            role["target_name"] = keep_name
            role["target_type"] = keep_type
        if role.get("target_id") == entry_id:
            continue
        key = (role.get("relationship", "").lower(), role.get("target_id"))
        if key in seen:
            continue
        seen.add(key)
        result.append(role)
    return result


def merge(entities_reg: dict, keep_id: str, merge_id: str) -> dict:
    """Mutate `entities_reg` in place: fold `merge_id` into `keep_id`.

    Unions aliases, `appears_in`, roles, and timeline events; remaps every
    `role.target_id` across the *whole* registry that points at the losing id
    (not just the two entities being merged — a third entity naming the
    losing id as a relationship target must follow it too); then deletes the
    losing entry. Raises `ValueError` on a bad pair of ids.
    """
    if keep_id == merge_id:
        raise ValueError("keep-id and merge-id must be different entities")
    if keep_id not in entities_reg:
        raise ValueError(f"entity '{keep_id}' not found in entities.json")
    if merge_id not in entities_reg:
        raise ValueError(f"entity '{merge_id}' not found in entities.json")

    keep = entities_reg[keep_id]
    merge_entry = entities_reg[merge_id]

    keep["aliases"] = _union_aliases(keep, merge_entry)
    keep["appears_in"] = _union_appears_in(keep, merge_entry)
    keep["timeline_events"] = _union_timeline_events(keep, merge_entry)
    keep["roles"] = list(keep.get("roles", [])) + list(merge_entry.get("roles", []))
    keep["date_first_seen"] = min(
        keep.get("date_first_seen") or _today(), merge_entry.get("date_first_seen") or _today()
    )
    keep["date_last_updated"] = _today()

    # Remap every entity's roles (including keep's own, just combined above) so a
    # role naming the losing id as its target follows the merge, wherever it lives.
    remapped = 0
    touched_entities: list[str] = []
    for eid, entry in entities_reg.items():
        if eid == merge_id:
            continue
        roles = entry.get("roles")
        if not roles:
            continue
        hits = sum(1 for r in roles if r.get("target_id") == merge_id)
        remapped += hits
        if hits and eid not in (keep_id, merge_id):
            touched_entities.append(eid)
        entry["roles"] = _clean_roles(eid, roles, keep_id, merge_id, keep["name"], keep["type"])

    del entities_reg[merge_id]

    return {
        "remapped_roles": remapped,
        "touched_entities": touched_entities,
        "aliases": len(keep["aliases"]),
        "appears_in": len(keep["appears_in"]),
        "roles": len(keep["roles"]),
        "timeline_events": len(keep["timeline_events"]),
    }


# ── Vault-level operation ──────────────────────────────────────────────────────

def _remap_timeline_ndjson(vault_path: Path, keep_id: str, merge_id: str) -> int:
    """Rewrite `merge_id` → `keep_id` in the `entity_ids` of every timeline NDJSON record
    (canonical and raw), so the unified timeline's entity links follow the merge instead of
    pointing at the merged-away stub (#237). Parallel to the registry surgery in `merge()` —
    deterministic, no model call. Returns the number of records changed."""
    td = vault_path / ".watchdog" / "timeline"
    if not td.exists():
        return 0
    changed = 0
    for f in sorted(td.glob("*.ndjson")):
        out_lines: list[str] = []
        touched = False
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                out_lines.append(line)   # leave a malformed line untouched rather than drop it
                continue
            eids = rec.get("entity_ids")
            if isinstance(eids, list) and merge_id in eids:
                remapped: list[str] = []
                for eid in eids:
                    eid = keep_id if eid == merge_id else eid
                    if eid not in remapped:
                        remapped.append(eid)
                rec["entity_ids"] = remapped
                touched = True
                changed += 1
            out_lines.append(json.dumps(rec, ensure_ascii=False))
        if touched:
            f.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return changed


def run(vault_path: Path, keep_id: str, merge_id: str) -> dict:
    """Perform the full `watchdog merge-entities` operation on a vault on disk:
    registry surgery (`merge`), note concatenation/redirect, timeline NDJSON entity-tag
    remap, manifest + timeline rebuild, and a best-effort search-index refresh of the two
    touched notes.

    Returns a report dict for the CLI to print. Raises `ValueError` on bad input —
    the caller is expected to turn that into a clean `sys.exit`.
    """
    registry_dir = vault_path / ".watchdog" / "Registry"
    entities_path = registry_dir / "entities.json"
    documents_path = registry_dir / "documents.json"
    registry_path = registry_dir / "registry.json"

    if not entities_path.exists():
        raise ValueError("entities.json not found — is this a Watchdog vault?")

    entities_reg = json.loads(entities_path.read_text(encoding="utf-8"))
    documents_reg = (
        json.loads(documents_path.read_text(encoding="utf-8")) if documents_path.exists() else {}
    )

    if keep_id not in entities_reg:
        raise ValueError(f"entity '{keep_id}' not found in entities.json")
    if merge_id not in entities_reg:
        raise ValueError(f"entity '{merge_id}' not found in entities.json")

    merge_name = entities_reg[merge_id]["name"]
    merge_type = entities_reg[merge_id]["type"]
    merge_note_path = entities_reg[merge_id]["note_path"]
    keep_note_path = entities_reg[keep_id]["note_path"]

    keep_note_file = vault_path / f"{keep_note_path}.md"
    merge_note_file = vault_path / f"{merge_note_path}.md"

    # Read both notes' prose *before* the registry mutation and before the losing
    # note is overwritten with its redirect stub.
    keep_analysis = _extract_analysis(keep_note_file)
    merge_analysis = _extract_analysis(merge_note_file)
    keep_contradictions = _extract_contradictions(keep_note_file)
    merge_contradictions = _extract_contradictions(merge_note_file)
    keep_summary = _extract_summary(keep_note_file)
    merge_summary = _extract_summary(merge_note_file)
    notes_section = _extract_notes_section(keep_note_file)
    merge_note_text = merge_note_file.read_text(encoding="utf-8") if merge_note_file.exists() else ""
    merge_notes_body = _extract_section(merge_note_text, "Notes") if merge_note_text else ""

    stats = merge(entities_reg, keep_id, merge_id)
    keep = entities_reg[keep_id]

    # Concatenate Analysis with provenance intact — a labelled block, not a blind splice.
    if merge_analysis:
        provenance = f"*Merged from [[{merge_note_path}|{merge_name}]] on {_today()}:*"
        combined_analysis = (
            keep_analysis.rstrip() + "\n\n" + provenance + "\n" + merge_analysis
        ).lstrip() if keep_analysis else f"{provenance}\n{merge_analysis}"
    else:
        combined_analysis = keep_analysis

    combined_contradictions = _accumulate_contradictions(
        keep_contradictions, [merge_contradictions] if merge_contradictions else []
    )

    combined_summary = keep_summary or merge_summary

    # Carry over the losing note's journalist annotations too, if the writer left any
    # beyond the boilerplate placeholder — those are the one thing nothing else recovers.
    if merge_notes_body and merge_notes_body.strip() != _DEFAULT_NOTES_MARKER:
        notes_section = (
            notes_section.rstrip()
            + f"\n\n*Notes carried over from merged entity [[{merge_note_path}|{merge_name}]]:*\n\n"
            + merge_notes_body + "\n"
        )

    note_content = build_entity_note(
        keep, notes_section, documents_reg, combined_summary, combined_analysis, combined_contradictions
    )
    keep_note_file.parent.mkdir(parents=True, exist_ok=True)
    keep_note_file.write_text(note_content, encoding="utf-8")

    stub_content = _frontmatter({
        "id":                merge_id,
        "name":              merge_name,
        "type":              merge_type,
        "merged_into":       keep_id,
        "date_last_updated": _today(),
    }) + (
        f"\n# {merge_name}\n\n"
        f"*Merged into [[{keep_note_path}|{keep['name']}]] on {_today()} — "
        f"see that note for the full record.*\n"
    )
    merge_note_file.parent.mkdir(parents=True, exist_ok=True)
    merge_note_file.write_text(stub_content, encoding="utf-8")

    # A third entity's own Relationships section renders its roles list, so a role that
    # got remapped onto keep_id needs that entity's note regenerated too — every other
    # section (Analysis/Contradictions/Notes/Summary) is read back untouched.
    other_notes: dict[str, tuple[str, str]] = {}
    for eid in stats["touched_entities"]:
        entry = entities_reg[eid]
        note_file = vault_path / f"{entry['note_path']}.md"
        content = build_entity_note(
            entry,
            _extract_notes_section(note_file),
            documents_reg,
            _extract_summary(note_file),
            _extract_analysis(note_file),
            _extract_contradictions(note_file),
        )
        note_file.parent.mkdir(parents=True, exist_ok=True)
        note_file.write_text(content, encoding="utf-8")
        other_notes[entry["note_path"]] = (entry["name"], content)

    entities_path.write_text(
        json.dumps(entities_reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _update_manifest(vault_path, entities_reg)
    stats["timeline_records_remapped"] = _remap_timeline_ndjson(vault_path, keep_id, merge_id)
    cmd_rebuild_timeline(vault_path, quiet=True)

    try:
        existing_registry = json.loads(registry_path.read_text()) if registry_path.exists() else {}
    except json.JSONDecodeError:
        existing_registry = {}
    existing_registry.update({"last_updated": _now_iso(), "entity_count": len(entities_reg)})
    registry_path.write_text(
        json.dumps(existing_registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    all_notes = {keep_note_path: (keep["name"], note_content),
                 merge_note_path: (merge_name, stub_content),
                 **other_notes}
    for note_path, (name, content) in all_notes.items():
        try:
            from watchdog.pipeline.embed import add_note
            add_note(vault_path, note_path, content)
        except Exception:
            pass
        try:
            from watchdog.pipeline.fulltext import add_note as fts_add_note
            fts_add_note(vault_path, note_path, "entity", name, content)
        except Exception:
            pass

    return {
        **stats,
        "keep_id":        keep_id,
        "keep_name":      keep["name"],
        "merge_name":     merge_name,
        "keep_note_path": keep_note_path,
    }
