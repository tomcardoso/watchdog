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

from watchdog.pipeline.backup import snapshot as _snapshot
from watchdog.pipeline.json_io import _read_json_or
from watchdog.pipeline.write_vault import (
    _write_json_atomic,
    _extract_contradictions,
    _extract_notes_section,
    _extract_section,
    _now_iso,
    _timeline_dedup_key,
    _today,
    _update_manifest,
    _frontmatter,
)
from watchdog.pipeline import resolutions
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


def _union_contradictions(keep: dict, merge_entry: dict) -> list[str]:
    return resolutions.dedup_callouts(
        list(keep.get("contradictions") or []) + list(merge_entry.get("contradictions") or [])
    )


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

    Unions aliases, `appears_in`, roles, timeline events, and contradictions (#288 —
    the registry list is the contradiction ledger, so a merge must union it same as
    every other list field); remaps every `role.target_id` across the *whole* registry
    that points at the losing id (not just the two entities being merged — a third
    entity naming the losing id as a relationship target must follow it too); then
    deletes the losing entry. Raises `ValueError` on a bad pair of ids.
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
    keep["contradictions"] = _union_contradictions(keep, merge_entry)
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
        "contradictions": len(keep["contradictions"]),
    }


def undo_snapshot(entry: dict) -> dict:
    """The parts of a registry entry a later split needs (D279): who it was, which documents
    named it, and the relationships it held — enough, with the facts' original entity tags in
    `.watchdog/extracted/`, to rebuild it."""
    return {
        "id": entry.get("id"), "name": entry.get("name"), "type": entry.get("type"),
        "aliases": list(entry.get("aliases") or []),
        "appears_in": list(entry.get("appears_in") or []),
        "note_path": entry.get("note_path"),
        "roles": [{k: r.get(k) for k in ("relationship", "target_id", "source_sha256", "is_reverse")}
                  for r in entry.get("roles") or []],
    }


def _reporter_log_entry(vault_path: Path, keep: dict, merged: dict) -> dict:
    """The merge-log entry for a merge a reporter asked for. When it settles a "possible same" pair
    from Review, the pair's tier, rule and evidence come with it."""
    from watchdog.pipeline import identity, merge_log
    from watchdog.pipeline.verification import reporter_name
    data = merge_log.load(vault_path)
    cand = data["candidates"].get(identity.pair_id(keep["id"], merged["id"]))
    evidence = identity.Evidence(vault_path, {})
    loser = evidence.registry_profile(merged["id"], merged)
    occurrence = {"sha": None, "documents": list(merged.get("appears_in") or []),
                  "facts": [f["id"] for f in loser.facts[:5]],
                  "identifier": None, "shared": []}
    reason = "Merged by a reporter."
    if cand:
        reason = f"Merged by a reporter from Review. {cand.get('reason') or ''}".strip()
        ev = cand.get("evidence") or {}
        occurrence.update({"identifier": ev.get("identifier"), "shared": ev.get("shared") or []})
    return merge_log.merge_entry(
        keep={"id": keep["id"], "name": keep["name"], "type": keep["type"]},
        merged={"id": merged["id"], "name": merged["name"], "type": merged["type"]},
        tier=cand.get("tier") if cand else "manual", decided_by="reporter",
        rule=cand.get("rule") if cand else None, reason=reason, occurrence=occurrence,
        reporter=reporter_name(), run=None)


# ── Vault-level operation ──────────────────────────────────────────────────────

def _remap_timeline_ndjson(vault_path: Path, keep_id: str, merge_id: str) -> int:
    """Rewrite `merge_id` → `keep_id` in every timeline NDJSON record (canonical and raw), so the
    unified timeline's entity links follow the merge (#237). Returns the records changed."""
    from watchdog.pipeline.timeline import remap_entity_ids
    return remap_entity_ids(vault_path, {merge_id: keep_id})


def _run_unlocked(vault_path: Path, keep_id: str, merge_id: str, log_entry: dict | None = None) -> dict:
    """Perform the full `watchdog merge-entities` operation on a vault on disk:
    registry surgery (`merge`), note concatenation/redirect, timeline NDJSON entity-tag
    remap, manifest + timeline rebuild, and a best-effort search-index refresh of the two
    touched notes.

    Returns a report dict for the CLI to print. Raises `ValueError` on bad input —
    the caller is expected to turn that into a clean `sys.exit`.
    """
    registry_dir = vault_path / ".watchdog" / "registry"
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

    # The merged record's note is about to become a redirect stub; its journalist notes are the
    # one thing nothing else recovers, so read them first. Everything else is data (D280).
    keep_note_callouts = resolutions.split_callouts(_extract_contradictions(keep_note_file))
    merge_note_callouts = resolutions.split_callouts(_extract_contradictions(merge_note_file))
    notes_section = _extract_notes_section(keep_note_file)
    merge_note_text = merge_note_file.read_text(encoding="utf-8") if merge_note_file.exists() else ""
    merge_notes_body = _extract_section(merge_note_text, "Notes") if merge_note_text else ""
    from watchdog.pipeline import entity_notes
    keep_synth, merge_synth = entities_reg[keep_id].get("synthesis"), entities_reg[merge_id].get("synthesis")
    def _has_facts(sha: str) -> bool:
        return (vault_path / ".watchdog" / "extracted" / f"{sha}.json").exists()
    entity_notes.carry_old_prose(entities_reg[keep_id], keep_note_file.read_text(encoding="utf-8")
                                 if keep_note_file.exists() else None, documents_reg, _has_facts)
    entity_notes.carry_old_prose(entities_reg[merge_id], merge_note_text or None, documents_reg,
                                 _has_facts)
    keep_synth = entities_reg[keep_id].get("synthesis")
    merge_synth = entities_reg[merge_id].get("synthesis")
    merge_legacy = entities_reg[merge_id].get("legacy_claims")

    # The merge log (D279): a reporter's merge is logged here; a merge the pipeline decided arrives
    # with its entry already built (`log_entry`). Either way it gets the losing record's snapshot.
    if log_entry is None:
        log_entry = _reporter_log_entry(vault_path, entities_reg[keep_id], entities_reg[merge_id])
    log_entry.setdefault("undo", {})["entry"] = undo_snapshot(entities_reg[merge_id])

    stats = merge(entities_reg, keep_id, merge_id)
    keep = entities_reg[keep_id]

    # Snapshot everything this operation is about to overwrite or delete, while it's
    # still pristine on disk — entities_reg above is mutated in memory only so far,
    # and stats["touched_entities"] tells us which third-party notes are about to be
    # regenerated too (#270).
    backup_paths = [entities_path, registry_dir / "manifest.json", keep_note_file, merge_note_file]
    backup_paths += [
        vault_path / f"{entities_reg[eid]['note_path']}.md" for eid in stats["touched_entities"]
    ]
    from watchdog.pipeline import merge_log
    log_path = merge_log.path(vault_path)
    if log_path.exists():
        backup_paths.append(log_path)
    backup_dir = _snapshot(vault_path, "merge-entities", backup_paths)
    if backup_dir:
        log_entry["undo"]["backup"] = str(Path(backup_dir).relative_to(vault_path)) \
            if Path(backup_dir).is_relative_to(vault_path) else str(backup_dir)

    # The registry list is the contradiction ledger (#282); fold in note-only callouts first.
    keep["contradictions"] = resolutions.dedup_callouts(
        keep["contradictions"] + keep_note_callouts + merge_note_callouts
    )
    stats["contradictions"] = len(keep["contradictions"])

    # One AI-written summary survives: the keeper's, else the merged record's. Either was written
    # before the merge, and the note says so until the next synthesis replaces it (#313, D280).
    summary_dropped = bool(keep_synth and merge_synth)
    chosen = keep_synth or merge_synth
    if chosen:
        keep["synthesis"] = {**chosen, "stale": "merge"}
    if merge_legacy:
        keep["legacy_claims"] = "\n\n".join(x for x in (keep.get("legacy_claims"), merge_legacy) if x)

    # Carry over the losing note's journalist annotations too, if the writer left any
    # beyond the boilerplate placeholder — those are the one thing nothing else recovers.
    if merge_notes_body and merge_notes_body.strip() != _DEFAULT_NOTES_MARKER:
        notes_section = (
            notes_section.rstrip()
            + f"\n\n*Notes carried over from merged entity [[{merge_note_path}|{merge_name}]]:*\n\n"
            + merge_notes_body + "\n"
        )

    # Facts in older extractions still name the merged id; the note finds them by following the
    # merge log (`entity_facts.resolve`), so the render sees the log as it will be once written.
    from watchdog.pipeline import entity_facts
    preview = merge_log.load(vault_path)
    preview["merges"] = list(preview["merges"]) + [log_entry]
    index = entity_facts.FactIndex(vault_path, entities_reg, documents_reg, merges=preview)
    written = entity_notes.write_entities(
        vault_path, [keep_id, *stats["touched_entities"]], entities_reg, documents_reg,
        index=index, notes={keep_id: notes_section})

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

    _write_json_atomic(entities_path, entities_reg)
    _update_manifest(vault_path, entities_reg)
    stats["timeline_records_remapped"] = _remap_timeline_ndjson(vault_path, keep_id, merge_id)
    cmd_rebuild_timeline(vault_path, quiet=True)

    existing_registry = (
        _read_json_or(registry_path, {}, catch=(json.JSONDecodeError,))
        if registry_path.exists() else {}
    )
    existing_registry.update({"last_updated": _now_iso(), "entity_count": len(entities_reg)})
    registry_path.write_text(
        json.dumps(existing_registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    # Acknowledgments follow the merge (#266 / D54): a lead resolved under the merged-away id
    # is rewritten onto the survivor. Contradiction/alert resolutions are keyed on content, not
    # entity id, so they need no remap.
    stats["resolutions_remapped"] = resolutions.remap_entity(vault_path, merge_id, keep_id)

    log_data = merge_log.load(vault_path)
    merge_log.mark_candidate_merged(log_data, keep_id, merge_id)
    merge_log.record(vault_path, [log_entry], data=log_data)

    all_notes = {p_: (n_, c_) for p_, n_, c_ in written}
    all_notes[merge_note_path] = (merge_name, stub_content)
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
        "summary_dropped": summary_dropped,
        "backup_dir":     backup_dir,
    }


def run(vault_path: Path, keep_id: str, merge_id: str, log_entry: dict | None = None) -> dict:
    """`_run_unlocked` under the registry lock every registry writer takes (D258). It can run
    from a Claude Code session or a second terminal while `watchdog bark` commits, and an
    unlocked read-modify-write here could write back a stale `entities.json` over that commit."""
    from watchdog.pipeline.write_vault import _registry_lock
    registry_dir = Path(vault_path) / ".watchdog" / "registry"
    if not registry_dir.is_dir():
        return _run_unlocked(vault_path, keep_id, merge_id, log_entry)
    with _registry_lock(registry_dir):
        return _run_unlocked(vault_path, keep_id, merge_id, log_entry)
