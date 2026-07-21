#!/usr/bin/env python3
"""
Build the entity-synthesis bundle and bulk-apply synthesized prose.

Phase-1 cost reduction (issue #87/#80 follow-up, superseded by #140/D26's
recurrence-gated synthesis): instead of one model call per multi-mention entity —
each re-reading its fragment file and note, paying startup + preamble cache-write
overhead — Python gathers every entity that recurs project-wide (``appears_in``
count in the registry meets ``min_docs``, not this batch's mention count) into a
single bundle for one post-ingest model call, then bulk-applies the returned prose
deterministically. Called from `orchestrate.py` as library functions
(`build_bundle` / `apply_bundle`); there is no standalone `watchdog` subcommand.

#403 phase 4: `build_bundle` reads the staged extraction corpus directly
(``.watchdog/extracted/<sha>.json``) for the shas in the current batch, instead of a
per-entity fragment file + queue that `write_vault` used to maintain as a side effect
of every document write. The `entity-fragments/` mechanism is retired.
"""

import json
import sys
from pathlib import Path

from watchdog.pipeline.finalize_entity import apply_one
from watchdog.pipeline.write_vault import (
    _defang,
    _extract_summary,
    _extract_analysis,
    _render_evidence_fragments,
    _update_manifest,
)


def _fragment_block(doc: dict, doc_title: str, incoming: dict) -> str:
    """Render one document's view of an entity — the same block shape write_vault used to
    accumulate into a per-entity fragment file, now built on demand from staged artifacts."""
    dtype = doc.get("document_type") or "document"
    ddate = doc.get("date_of_document") or "undated"
    sha7 = (doc.get("sha256") or "")[:7]
    parts = [f"\n### {_defang(doc_title)} — {dtype}, {ddate} (sha {sha7})\n"]
    if incoming.get("summary"):
        parts.append(incoming["summary"].strip() + "\n")
    fragments = incoming.get("evidence_fragments") or []
    if fragments:
        parts.append("\nClaims:\n" + _render_evidence_fragments(fragments) + "\n")
    roles = incoming.get("roles", [])
    if roles:
        rendered = "; ".join(
            f"{r.get('relationship', '')} {r.get('target_name', '')}".strip() for r in roles
        )
        parts.append(f"\nRoles: {rendered}\n")
    return "".join(parts)


def build_bundle(vault_path: Path, shas: list[str], min_docs: int = 2) -> dict:
    """Gather every entity that recurs across the project into one synthesis bundle (#140).

    The gate is **project-wide recurrence**: an entity earns a synthesized summary once it
    appears in ``min_docs`` (default 2) distinct documents across the whole investigation — read
    from its registry ``appears_in``, not from this batch's mention count. Only entities *touched
    this run* (mentioned in one of ``shas``, the current batch's staged extractions) are
    candidates — an untouched entity has nothing new to reconcile. Single-document entities are
    left as deterministic stubs (facts + relationships, no summary section); a recurring entity
    that only got a fresh stub this run is promoted here as its ``appears_in`` crosses the
    threshold, even when the two documents arrived in different batches years apart.

    ``shas`` is deliberately the current batch's result shas, not ``_pending_commits(vault)``:
    on a resume (synthesis rate-limited after commit already landed), the docs are already in
    the registry, so `_pending_commits` is empty, but the batch's ``result_*.json`` files persist
    and a re-run must still re-synthesize them.
    """
    entities_path = vault_path / ".watchdog" / "registry" / "entities.json"
    entities_reg = json.loads(entities_path.read_text(encoding="utf-8")) if entities_path.exists() else {}

    extracted_dir = vault_path / ".watchdog" / "extracted"
    frag_by_id: dict[str, list[str]] = {}
    for sha in sorted(shas):
        artifact_path = extracted_dir / f"{sha}.json"
        if not artifact_path.exists():
            continue
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        doc = artifact.get("document") or {}
        doc_title = doc.get("title", doc.get("filename", ""))
        for incoming in artifact.get("entities") or []:
            eid = incoming.get("id")
            if not eid:
                continue
            frag_by_id.setdefault(eid, []).append(_fragment_block(doc, doc_title, incoming))

    entities = []
    for eid, blocks in frag_by_id.items():
        if len(entities_reg.get(eid, {}).get("appears_in", [])) < min_docs:
            continue
        rec = entities_reg.get(eid, {})
        note_rel = rec.get("note_path")
        if not note_rel:
            continue
        note_path = vault_path / f"{note_rel}.md"
        entities.append({
            "entity_id":        eid,
            "name":             rec.get("name", ""),
            "note_path":        note_rel,
            "current_summary":  _extract_summary(note_path) or "",
            "current_analysis": _extract_analysis(note_path),
            "fragments":        "".join(blocks),
        })

    entities.sort(key=lambda e: e["name"].lower())
    return {"entities": entities}


def apply_bundle(result_path: Path, vault_path: Path) -> dict:
    """Bulk-write synthesized prose from the model's result JSON.

    Validates conservatively: unknown entity ids and entries with an empty
    summary are skipped (the note keeps its carried-forward prose, which is still
    correct). Writes entities.json + manifest once after all entities are applied.
    """
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
        if apply_one(eid, summary, analysis, vault_path, entities_reg, documents_reg):
            applied.append(eid)
        else:
            skipped.append(eid)

    if applied:
        entities_path.write_text(
            json.dumps(entities_reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        _update_manifest(vault_path, entities_reg)

    return {"applied": applied, "skipped": skipped}
