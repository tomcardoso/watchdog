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
    assert build_bundle(vault, []) == {"entities": [], "meta": {}}


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


def test_build_bundle_gives_the_facts_with_ids_not_the_old_prose(tmp_path):
    """D280: the model sees the entity's facts from every document, each with a short id that
    maps back to its D271 id, and never the note's earlier prose."""
    from watchdog.pipeline import verification
    vault = make_vault(tmp_path)
    _stage_extracted(vault, tmp_path / "a", "sha-a", "doc-a.pdf")
    _stage_extracted(vault, tmp_path / "b", "sha-b", "doc-b.pdf", overrides={"document": {
        "key_facts": [{"fact": "Resigned as director.", "page": 4, "date": "2023-02-01",
                       "basis": "inferred", "entities": ["alice-smith"]}]}})
    _write_registry(vault, {
        "alice-smith": {"id": "alice-smith", "name": "Alice Smith", "type": "Person",
                        "note_path": "entities/person/alice-smith",
                        "appears_in": ["sha-a", "sha-b"]},
    })
    note = vault / "entities" / "person" / "alice-smith.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# Alice Smith\n\n## Summary\n\nCarried summary.\n", encoding="utf-8")

    bundle = build_bundle(vault, ["sha-b"])
    alice = next(e for e in bundle["entities"] if e["entity_id"] == "alice-smith")
    text = json.dumps(alice)
    assert "Carried summary." not in text
    assert alice["documents"] == 2 and alice["selection"] == "All of the entity's facts are shown."
    lines = alice["facts"]
    assert len(lines) == 4                                 # three from doc A, one from doc B
    line = next(ln for ln in lines if "Resigned" in ln)
    assert line.startswith("[f:") and "(2023-02-01) Resigned as director." in line
    assert "[inferred; new]" in line and "p. 4" in line
    refs = bundle["meta"]["alice-smith"]["fact_refs"]
    ref = line[1:line.index("]")]
    sha_b_ids = verification.fact_ids("sha-b", [{"fact": "Resigned as director.", "page": 4}])
    assert refs[ref] == sha_b_ids[0]


def test_build_bundle_labels_disputed_facts_and_caps_large_entities(tmp_path, monkeypatch):
    from watchdog.pipeline import synthesis_bundle, verification
    vault = make_vault(tmp_path)
    facts = [{"fact": f"Fact number {i}.", "page": 1, "date": f"20{10 + i:02d}-01-01",
              "entities": ["alice-smith"]} for i in range(12)]
    _stage_extracted(vault, tmp_path / "a", "sha-a", "doc-a.pdf", overrides={"document": {"key_facts": facts}})
    _stage_extracted(vault, tmp_path / "b", "sha-b", "doc-b.pdf", overrides={"document": {
        "key_facts": [{"fact": "Batch fact.", "page": 2, "entities": ["alice-smith"]}]}})
    _write_registry(vault, {
        "alice-smith": {"id": "alice-smith", "name": "Alice Smith", "type": "Person",
                        "note_path": "entities/person/alice-smith", "appears_in": ["sha-a", "sha-b"]},
    })
    (vault / ".watchdog" / "registry" / "documents.json").write_text(json.dumps(
        {"sha-a": {"filename": "doc-a.pdf"}, "sha-b": {"filename": "doc-b.pdf"}}))
    disputed = verification.fact_ids("sha-a", facts)[0]
    (vault / ".watchdog" / "registry" / "verification.json").write_text(json.dumps({
        "schema_version": 1, "marks": {disputed: {"status": "disputed", "fact": "Fact number 0.",
                                                   "page": 1}}}))
    # Uncapped first: the disputed fact is shown, labelled, never withheld.
    full = "\n".join(build_bundle(vault, ["sha-b"])["entities"][0]["facts"])
    assert any("Fact number 0." in ln and "disputed by the reporter" in ln for ln in full.splitlines())
    monkeypatch.setattr(synthesis_bundle, "FACT_MAX", 5)

    bundle = build_bundle(vault, ["sha-b"])
    alice = bundle["entities"][0]
    joined = "\n".join(alice["facts"])
    assert bundle["meta"]["alice-smith"]["disputed"] == 1
    assert len(alice["facts"]) == 5 and "Batch fact." in joined   # this batch's fact always kept
    assert "Fact number 11." in joined and "Fact number 1." not in joined   # then the most recent
    assert alice["selection"].startswith("5 of the entity's 13 facts are shown")
    assert bundle["meta"]["alice-smith"]["facts_total"] == 13


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
    summary = _extract_section(note, "Summary")
    assert "SYNTHESIZED summary across sources." in summary and "SYNTHESIZED analysis." in summary
    assert "Old finding." not in note
    # Structured sections are rendered from data, untouched by the prose:
    assert "[!contradiction]" in _extract_section(note, "Contradictions")
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
    # Nothing was stored, so alice-smith still has no AI-written summary.
    assert "## Summary" not in _note(vault)
    entry = json.loads((vault / ".watchdog/registry/entities.json").read_text())["alice-smith"]
    assert "synthesis" not in entry


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


def test_build_bundle_rewrites_a_summary_left_stale_by_a_merge_or_undo(tmp_path):
    """D285: a summary whose note says it is out of date (written before a merge into its record,
    or before an undo) is rewritten at the next run even when the batch does not name the entity,
    and even when an undo left the record with one document."""
    vault = make_vault(tmp_path)
    stale = {"summary": "Old.", "stale": "undo"}
    _write_registry(vault, {
        "split-co": {"id": "split-co", "name": "Split Co", "type": "Company", "synthesis": stale,
                     "note_path": "entities/company/split-co", "appears_in": ["sha-old"]},
        "kept-co": {"id": "kept-co", "name": "Kept Co", "type": "Company",
                    "synthesis": {**stale, "stale": "merge"},
                    "note_path": "entities/company/kept-co", "appears_in": ["sha-old", "sha-x"]},
        "quiet-co": {"id": "quiet-co", "name": "Quiet Co", "type": "Company",
                     "synthesis": {"summary": "Fine."},
                     "note_path": "entities/company/quiet-co", "appears_in": ["sha-old", "sha-x"]},
    })
    _stage_extracted(vault, tmp_path / "new", "sha-new", "new.pdf", overrides={"entities": []})
    ids = {e["entity_id"] for e in build_bundle(vault, ["sha-new"])["entities"]}
    assert ids == {"split-co", "kept-co"}
    assert build_bundle(vault, [])["entities"] == []     # nothing runs without a batch
