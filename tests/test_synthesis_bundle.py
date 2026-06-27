"""Tests for the Phase-1 bundled synthesis path: build-synthesis-bundle gathers
the multi-mention entities, apply-syntheses bulk-writes prose while preserving
structured sections and skipping unknown ids / empty summaries."""

import json
from pathlib import Path

from watchdog.pipeline.write_vault import run as wv_run, _extract_section
from watchdog.pipeline.synthesis_bundle import build_bundle, apply_bundle

from tests.test_write_vault import make_vault, make_extraction
from tests.test_finalizer import _note, CALLOUT


def _second_doc(tmp_path, vault):
    """Run a second extraction so default entities reach count == 2."""
    wv_run(make_extraction(tmp_path, {"document": {"sha256": "def456", "filename": "two.pdf"}}), vault)


# ── build_bundle ──────────────────────────────────────────────────────────────

def test_build_bundle_no_queue_is_empty(tmp_path):
    vault = make_vault(tmp_path)
    assert build_bundle(vault) == {"entities": []}


def test_build_bundle_skips_single_mention(tmp_path):
    vault = make_vault(tmp_path)
    wv_run(make_extraction(tmp_path), vault)          # alice-smith + acme-corp, count == 1
    assert build_bundle(vault)["entities"] == []


def test_build_bundle_selects_multi_mention_with_fragments_and_prose(tmp_path):
    vault = make_vault(tmp_path)
    wv_run(make_extraction(tmp_path), vault)
    _second_doc(tmp_path, vault)                      # bump to count == 2

    bundle = build_bundle(vault)
    ids = {e["entity_id"] for e in bundle["entities"]}
    assert "alice-smith" in ids

    alice = next(e for e in bundle["entities"] if e["entity_id"] == "alice-smith")
    assert alice["fragments"].count("### ") == 2      # one block per document
    assert alice["current_summary"]                   # carried prose included
    assert alice["note_path"].endswith("alice-smith")


def test_build_bundle_gates_on_project_wide_appears_in(tmp_path):
    """Recurrence is counted across the whole project (appears_in), not within the batch (#140):
    an entity in 2 documents total is selected even if only touched once this run; an entity in
    1 document total is skipped even when touched this run."""
    vault = make_vault(tmp_path)
    reg_path = vault / ".watchdog" / "Registry" / "entities.json"
    reg = {
        "recurring-co": {"id": "recurring-co", "name": "Recurring Co", "type": "Company",
                         "note_path": "entities/company/recurring-co",
                         "appears_in": ["sha-old", "sha-new"]},   # 2 docs, across batches
        "one-off": {"id": "one-off", "name": "One Off", "type": "Person",
                    "note_path": "entities/person/one-off", "appears_in": ["sha-new"]},   # 1 doc
    }
    reg_path.write_text(json.dumps(reg))

    frag_dir = vault / ".watchdog" / "tmp" / "entity-fragments"
    frag_dir.mkdir(parents=True, exist_ok=True)
    queue = {}
    for eid, e in reg.items():
        (frag_dir / f"{eid}.md").write_text(f"### doc\nclaim about {eid}\n", encoding="utf-8")
        note = vault / f"{e['note_path']}.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(f"# {e['name']}\n\n## Summary\n\ns\n", encoding="utf-8")
        queue[eid] = {"name": e["name"], "note_path": e["note_path"], "count": 1}   # touched once this run
    (frag_dir / "_queue.json").write_text(json.dumps(queue), encoding="utf-8")

    ids = {e["entity_id"] for e in build_bundle(vault)["entities"]}
    assert ids == {"recurring-co"}   # promoted by project-wide recurrence; one-off stays a stub


# ── apply_bundle ──────────────────────────────────────────────────────────────

def _full_extraction(tmp_path):
    return make_extraction(tmp_path, {
        "entities": [{
            "id": "alice-smith", "name": "Alice Smith", "type": "Person", "aliases": [],
            "summary": "Old summary.",
            "evidence_fragments": [{"claim": "Old finding.", "basis": "stated"}],
            "contradictions": [CALLOUT],
            "timeline_events": [{"date": "2020-03-15", "event": "Appointed director", "page": 2, "basis": "stated"}],
            "roles": [{"relationship": "Director of", "target_id": "acme-corp", "target_type": "Company",
                       "target_name": "Acme Corp", "page": 2, "basis": "stated", "date_range": None}],
        }],
    })


def _write_result(vault: Path, syntheses: list[dict]) -> Path:
    path = vault / ".watchdog" / "tmp" / "synthesis-result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"entity_syntheses": syntheses}))
    return path


def test_apply_bundle_writes_prose_preserves_structured_sections(tmp_path):
    vault = make_vault(tmp_path)
    wv_run(_full_extraction(tmp_path), vault)

    result = _write_result(vault, [{
        "entity_id": "alice-smith",
        "summary": "SYNTHESIZED summary across sources.",
        "analysis": "SYNTHESIZED analysis.",
    }])
    outcome = apply_bundle(result, vault)

    assert outcome["applied"] == ["alice-smith"]
    note = _note(vault)
    assert "SYNTHESIZED summary across sources." in _extract_section(note, "Summary")
    assert "SYNTHESIZED analysis." in _extract_section(note, "Analysis")
    assert "Old summary." not in note and "Old finding." not in note
    # Structured sections untouched:
    assert "[!contradiction]" in _extract_section(note, "Contradictions")
    assert "Appointed director" in _extract_section(note, "Timeline")
    assert "Acme Corp" in _extract_section(note, "Relationships")


def test_apply_bundle_skips_unknown_id_and_empty_summary(tmp_path):
    vault = make_vault(tmp_path)
    wv_run(make_extraction(tmp_path), vault)          # alice-smith summary set

    result = _write_result(vault, [
        {"entity_id": "ghost-entity", "summary": "Should not be written."},
        {"entity_id": "alice-smith", "summary": "   "},   # empty → skip
    ])
    outcome = apply_bundle(result, vault)

    assert outcome["applied"] == []
    assert set(outcome["skipped"]) == {"ghost-entity", "alice-smith"}
    # alice-smith keeps her carried-forward summary, untouched.
    assert "Alice Smith is a director of Acme Corp." in _note(vault)


def test_apply_bundle_writes_registry_once_for_multiple(tmp_path):
    vault = make_vault(tmp_path)
    wv_run(make_extraction(tmp_path), vault)
    _second_doc(tmp_path, vault)

    result = _write_result(vault, [
        {"entity_id": "alice-smith", "summary": "Alice synthesized.", "analysis": ""},
        {"entity_id": "acme-corp", "summary": "Acme synthesized.", "analysis": "Notable."},
    ])
    outcome = apply_bundle(result, vault)

    assert set(outcome["applied"]) == {"alice-smith", "acme-corp"}
    reg = json.loads((vault / ".watchdog" / "Registry" / "entities.json").read_text())
    assert "alice-smith" in reg and "acme-corp" in reg
