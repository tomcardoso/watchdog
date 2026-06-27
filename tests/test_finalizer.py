"""Tests for the entity-synthesis path: contradictions split, fragment emission,
summary carryforward in pre-flight, and the finalizer's prose-only write."""

import json
from pathlib import Path

from watchdog.pipeline.write_vault import run as wv_run, _extract_section
import watchdog.pipeline.finalize_entity as fe
import watchdog.pipeline.preflight as pf

from tests.test_write_vault import make_vault, make_extraction


def _note(vault: Path, eid: str = "alice-smith") -> str:
    return (vault / "entities" / "person" / f"{eid}.md").read_text()


CALLOUT = "> [!contradiction] role\n> - **director** — [[documents/a|A]], p.1 (high)\n> - **officer** — [[documents/b|B]], p.2 (high)"


# ── Contradictions split out of Analysis ──────────────────────────────────────

def test_contradictions_go_to_own_section_not_analysis(tmp_path):
    vault = make_vault(tmp_path)
    wv_run(make_extraction(tmp_path, {
        "entities": [{
            "id": "alice-smith", "name": "Alice Smith", "type": "Person", "aliases": [],
            "summary": "A director.",
            "evidence_fragments": [{"claim": "Holds significant shares.", "basis": "stated"}],
            "contradictions": [CALLOUT],
            "timeline_events": [], "roles": [],
        }],
    }), vault)

    analysis = _extract_section(_note(vault), "Analysis")
    contradictions = _extract_section(_note(vault), "Contradictions")
    assert "Holds significant shares." in analysis
    assert "[!contradiction]" not in analysis        # callout is NOT in Analysis
    assert "[!contradiction]" in contradictions       # it IS in its own section


def test_contradictions_dedupe_on_repeat(tmp_path):
    vault = make_vault(tmp_path)
    ext = {"entities": [{
        "id": "alice-smith", "name": "Alice Smith", "type": "Person", "aliases": [],
        "summary": "A director.", "contradictions": [CALLOUT],
        "timeline_events": [], "roles": [],
    }]}
    wv_run(make_extraction(tmp_path, ext), vault)
    wv_run(make_extraction(tmp_path, {**ext, "document": {"sha256": "def456", "filename": "two.pdf"}}), vault)

    assert _note(vault).count("[!contradiction]") == 1


# ── Fragment emission + finalizer gate ────────────────────────────────────────

def _frag_queue(vault: Path) -> dict:
    return json.loads((vault / ".watchdog" / "tmp" / "entity-fragments" / "_queue.json").read_text())


def test_fragment_written_per_entity_with_count(tmp_path):
    vault = make_vault(tmp_path)
    wv_run(make_extraction(tmp_path), vault)  # mentions alice-smith + acme-corp once

    q = _frag_queue(vault)
    assert q["alice-smith"]["count"] == 1
    assert q["alice-smith"]["name"] == "Alice Smith"
    frag = (vault / ".watchdog" / "tmp" / "entity-fragments" / "alice-smith.md").read_text()
    assert "Alice Smith is a director" in frag


def test_two_documents_bump_gate_to_two(tmp_path):
    vault = make_vault(tmp_path)
    wv_run(make_extraction(tmp_path), vault)
    wv_run(make_extraction(tmp_path, {"document": {"sha256": "def456", "filename": "two.pdf"}}), vault)

    q = _frag_queue(vault)
    assert q["alice-smith"]["count"] == 2          # gated → finalize
    frag = (vault / ".watchdog" / "tmp" / "entity-fragments" / "alice-smith.md").read_text()
    assert frag.count("### ") == 2                 # one block per document


# ── Pre-flight carries the current summary forward ────────────────────────────

def test_preflight_carries_entity_summary(tmp_path):
    vault = make_vault(tmp_path)
    (vault / ".watchdog" / "queue").mkdir(parents=True)
    wv_run(make_extraction(tmp_path), vault)  # creates alice-smith note + manifest

    # Queue a new document whose text names the existing entity.
    new_sha = "newdoc99"
    (vault / ".watchdog" / "queue" / f"{new_sha}.json").write_text(json.dumps({
        "filename": "new.pdf",
        "pages": [{"markdown": "A meeting attended by Alice Smith was held."}],
    }))

    result = pf.run(vault, new_sha)
    alice = next(c for c in result["existing_entities"] if c["id"] == "alice-smith")
    assert alice["summary"] == "Alice Smith is a director of Acme Corp."


# ── Finalizer writes prose only, preserves structured sections ────────────────

def test_finalizer_replaces_summary_and_analysis_preserves_rest(tmp_path):
    vault = make_vault(tmp_path)
    wv_run(make_extraction(tmp_path, {
        "entities": [{
            "id": "alice-smith", "name": "Alice Smith", "type": "Person", "aliases": [],
            "summary": "Old summary.",
            "evidence_fragments": [{"claim": "Old finding.", "basis": "stated"}],
            "contradictions": [CALLOUT],
            "timeline_events": [{"date": "2020-03-15", "event": "Appointed director", "page": 2, "basis": "stated"}],
            "roles": [{"relationship": "Director of", "target_id": "acme-corp", "target_type": "Company",
                       "target_name": "Acme Corp", "page": 2, "basis": "stated", "date_range": None}],
        }],
    }), vault)

    synth = vault / ".watchdog" / "tmp" / "wdg_synth-alice-smith.json"
    synth.parent.mkdir(parents=True, exist_ok=True)
    synth.write_text(json.dumps({
        "entity_id": "alice-smith",
        "summary": "SYNTHESIZED summary across sources.",
        "analysis": "SYNTHESIZED analysis.",
    }))
    fe.run(synth, vault)

    note = _note(vault)
    assert "SYNTHESIZED summary across sources." in _extract_section(note, "Summary")
    assert "SYNTHESIZED analysis." in _extract_section(note, "Analysis")
    assert "Old summary." not in note and "Old finding." not in note
    # Structured sections untouched:
    assert "[!contradiction]" in _extract_section(note, "Contradictions")
    assert "Appointed director" in _extract_section(note, "Timeline")
    assert "Acme Corp" in _extract_section(note, "Relationships")
