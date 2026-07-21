"""Tests for the Phase-1 bundled synthesis path: build-synthesis-bundle gathers
the multi-mention entities, apply-syntheses bulk-writes prose while preserving
structured sections and skipping unknown ids / empty summaries.

#403 phase 4: `build_bundle` reads the staged extraction corpus (`.watchdog/extracted/<sha>.json`)
for the current batch's shas directly, instead of the retired per-entity fragment file + queue
`write_vault` used to maintain. `_stage_extracted` (borrowed from test_orchestrate.py) writes a
staged artifact the way real extraction would leave it."""

import json
from pathlib import Path

from watchdog.pipeline.write_vault import run as wv_run, _extract_section
from watchdog.pipeline.synthesis_bundle import build_bundle, apply_bundle

from tests.test_write_vault import make_vault, make_extraction
from tests.test_finalizer import _note, CALLOUT
from tests.test_orchestrate import _stage_extracted


def _second_doc(tmp_path, vault):
    """Run a second extraction so default entities reach count == 2."""
    wv_run(make_extraction(tmp_path, {"document": {"sha256": "def456", "filename": "two.pdf"}}), vault)


def _write_registry(vault: Path, reg: dict) -> None:
    (vault / ".watchdog" / "registry" / "entities.json").write_text(json.dumps(reg))


# ── build_bundle ──────────────────────────────────────────────────────────────

def test_build_bundle_empty_shas_is_empty(tmp_path):
    vault = make_vault(tmp_path)
    assert build_bundle(vault, []) == {"entities": []}


def test_build_bundle_skips_single_mention(tmp_path):
    vault = make_vault(tmp_path)
    _stage_extracted(vault, tmp_path / "a", "sha-a", "doc-a.pdf")   # alice-smith + acme-corp
    _write_registry(vault, {
        "alice-smith": {"id": "alice-smith", "name": "Alice Smith", "type": "Person",
                        "note_path": "entities/person/alice-smith", "appears_in": ["sha-a"]},
        "acme-corp": {"id": "acme-corp", "name": "Acme Corp", "type": "organization",
                     "note_path": "entities/organization/acme-corp", "appears_in": ["sha-a"]},
    })

    assert build_bundle(vault, ["sha-a"])["entities"] == []


def test_build_bundle_selects_multi_mention_with_fragments_and_prose(tmp_path):
    vault = make_vault(tmp_path)
    _stage_extracted(vault, tmp_path / "a", "sha-a", "doc-a.pdf")
    _stage_extracted(vault, tmp_path / "b", "sha-b", "doc-b.pdf")
    _write_registry(vault, {
        "alice-smith": {"id": "alice-smith", "name": "Alice Smith", "type": "Person",
                        "note_path": "entities/person/alice-smith",
                        "appears_in": ["sha-a", "sha-b"]},
    })
    note = vault / "entities" / "person" / "alice-smith.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# Alice Smith\n\n## Summary\n\nCarried summary.\n", encoding="utf-8")

    bundle = build_bundle(vault, ["sha-a", "sha-b"])
    ids = {e["entity_id"] for e in bundle["entities"]}
    assert "alice-smith" in ids

    alice = next(e for e in bundle["entities"] if e["entity_id"] == "alice-smith")
    assert alice["fragments"].count("### ") == 2      # one block per document
    assert alice["current_summary"]                   # carried prose included
    assert alice["note_path"] == "entities/person/alice-smith"


def test_build_bundle_gates_on_project_wide_appears_in(tmp_path):
    """Recurrence is counted across the whole project (appears_in), not within the batch (#140):
    an entity in 2 documents total is selected even if only touched once this run; an entity in
    1 document total is skipped even when touched this run."""
    vault = make_vault(tmp_path)
    reg = {
        "recurring-co": {"id": "recurring-co", "name": "Recurring Co", "type": "Company",
                         "note_path": "entities/company/recurring-co",
                         "appears_in": ["sha-old", "sha-new"]},   # 2 docs, across batches
        "one-off": {"id": "one-off", "name": "One Off", "type": "Person",
                    "note_path": "entities/person/one-off", "appears_in": ["sha-new"]},   # 1 doc
    }
    _write_registry(vault, reg)
    for eid, e in reg.items():
        note = vault / f"{e['note_path']}.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(f"# {e['name']}\n\n## Summary\n\ns\n", encoding="utf-8")

    _stage_extracted(vault, tmp_path / "new", "sha-new", "doc-new.pdf", overrides={
        "entities": [
            {"id": "recurring-co", "name": "Recurring Co", "type": "Company", "aliases": [],
             "summary": None, "timeline_events": [], "roles": []},
            {"id": "one-off", "name": "One Off", "type": "Person", "aliases": [],
             "summary": None, "timeline_events": [], "roles": []},
        ],
        "morgue_entity_id": "recurring-co",
        "morgue_document_type": "filing",
    })

    ids = {e["entity_id"] for e in build_bundle(vault, ["sha-new"])["entities"]}
    assert ids == {"recurring-co"}   # promoted by project-wide recurrence; one-off stays a stub


def test_build_bundle_scoped_to_batch_not_whole_corpus(tmp_path):
    """`.watchdog/extracted/` accumulates every document ever ingested and is never cleaned up, so
    build_bundle must synthesize only the entities THIS batch (its `shas`) touched — not re-scan
    the whole corpus. A recurring entity staged by a prior batch, still on disk but not in this
    run's shas, is left alone even though it clears the appears_in gate."""
    vault = make_vault(tmp_path)
    _write_registry(vault, {
        "old-co": {"id": "old-co", "name": "Old Co", "type": "Company",
                   "note_path": "entities/company/old-co", "appears_in": ["sha-old", "sha-x"]},
        "recurring-co": {"id": "recurring-co", "name": "Recurring Co", "type": "Company",
                         "note_path": "entities/company/recurring-co",
                         "appears_in": ["sha-new", "sha-y"]},
    })
    for eid, nm in [("old-co", "Old Co"), ("recurring-co", "Recurring Co")]:
        note = vault / "entities" / "company" / f"{eid}.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(f"# {nm}\n\n## Summary\n\ns\n", encoding="utf-8")

    # A prior batch's staged artifact, still on disk — recurring, so gate-eligible.
    _stage_extracted(vault, tmp_path / "old", "sha-old", "old.pdf", overrides={
        "entities": [{"id": "old-co", "name": "Old Co", "type": "Company", "aliases": [],
                      "summary": None, "timeline_events": [], "roles": []}],
        "morgue_entity_id": "old-co", "morgue_document_type": "filing",
    })
    # This run's batch.
    _stage_extracted(vault, tmp_path / "new", "sha-new", "new.pdf", overrides={
        "entities": [{"id": "recurring-co", "name": "Recurring Co", "type": "Company", "aliases": [],
                      "summary": None, "timeline_events": [], "roles": []}],
        "morgue_entity_id": "recurring-co", "morgue_document_type": "filing",
    })

    ids = {e["entity_id"] for e in build_bundle(vault, ["sha-new"])["entities"]}
    assert ids == {"recurring-co"}   # old-co is recurring + staged, but not in this batch → skipped


def test_build_bundle_fragment_carries_claim_text(tmp_path):
    """A claim's text survives from the staged artifact into the rendered fragment block that
    goes into the synthesis prompt (replaces the removed write_vault-level fragment-digest test,
    now that the digest is built here instead of accumulated by write_vault)."""
    vault = make_vault(tmp_path)
    _write_registry(vault, {
        "alice-smith": {"id": "alice-smith", "name": "Alice Smith", "type": "Person",
                        "note_path": "entities/person/alice-smith",
                        "appears_in": ["sha-a", "sha-b"]},
    })
    _stage_extracted(vault, tmp_path / "a", "sha-a", "doc-a.pdf", overrides={
        "entities": [{
            "id": "alice-smith", "name": "Alice Smith", "type": "Person", "aliases": [],
            "summary": None,
            "evidence_fragments": [{"claim": "A distinctive claim about Alice.", "basis": "stated"}],
            "timeline_events": [], "roles": [],
        }],
    })

    bundle = build_bundle(vault, ["sha-a"])
    alice = next(e for e in bundle["entities"] if e["entity_id"] == "alice-smith")
    assert "A distinctive claim about Alice." in alice["fragments"]


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
    reg = json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())
    assert "alice-smith" in reg and "acme-corp" in reg
