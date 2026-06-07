import json
from pathlib import Path

import pytest

from watchdog.pipeline.write_entity import run


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    reg_dir = vault / ".watchdog" / "Registry"
    reg_dir.mkdir(parents=True)
    (vault / "entities" / "person").mkdir(parents=True)
    (vault / "documents").mkdir()

    existing_entities = {
        "alice-smith": {
            "id": "alice-smith",
            "name": "Alice Smith",
            "type": "Person",
            "aliases": ["A. Smith"],
            "appears_in": ["sha-doc1", "sha-doc2"],
            "note_path": "entities/person/alice-smith",
            "roles": [],
            "timeline_events": [
                {"date": "2015-11-03", "event": "Old event from prior ingest",
                 "page": 1, "confidence": "high", "source_sha256": "sha-doc1"},
            ],
            "date_first_seen": "2015-11-03",
            "date_last_updated": "2015-11-03",
        }
    }
    existing_docs = {
        "sha-doc1": {"sha256": "sha-doc1", "filename": "form-79.pdf",
                     "title": "Form 79", "document_note": "documents/form-79"},
        "sha-doc2": {"sha256": "sha-doc2", "filename": "annual-report.pdf",
                     "title": "Annual Report 2019", "document_note": "documents/annual-report"},
    }

    (reg_dir / "entities.json").write_text(json.dumps(existing_entities))
    (reg_dir / "documents.json").write_text(json.dumps(existing_docs))

    existing_note = vault / "entities" / "person" / "alice-smith.md"
    existing_note.write_text(
        "---\nid: alice-smith\n---\n\n# Alice Smith\n\n"
        "## Summary\n\nOld summary.\n\n"
        "## Analysis\n\n*2015-11-03, via [[documents/form-79|Form 79]]:* Prior analysis.\n\n"
        "## Timeline\n\n### 2015\n- **3 Nov 2015** — Old event from prior ingest\n\n"
        "## Notes\n\nJournalist note.\n"
    )
    return vault


def make_extraction(tmp_path: Path, entity_id: str = "alice-smith") -> Path:
    data = {
        "entity_id": entity_id,
        "summary": "Alice Smith is a key figure appearing across 2 documents.",
        "timeline_events": [
            {"date": "2015-11-03", "event": "Transferred shares with no equity received",
             "source_sha256": "sha-doc1", "page": 1, "confidence": "high"},
            {"date": "2019-01-15", "event": "Listed as director in annual report",
             "source_sha256": "sha-doc2", "page": 4, "confidence": "high"},
        ],
    }
    path = tmp_path / "entity-refresh.json"
    path.write_text(json.dumps(data))
    return path


# ── Summary replacement ───────────────────────────────────────────────────────

def test_summary_replaced(tmp_path):
    vault = make_vault(tmp_path)
    run(make_extraction(tmp_path), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    assert "Old summary." not in content
    assert "Alice Smith is a key figure appearing across 2 documents." in content


# ── Timeline replacement ──────────────────────────────────────────────────────

def test_timeline_replaced_not_accumulated(tmp_path):
    vault = make_vault(tmp_path)
    run(make_extraction(tmp_path), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    # Old event should be gone — replaced, not accumulated
    assert "Old event from prior ingest" not in content
    assert "Transferred shares with no equity received" in content
    assert "Listed as director in annual report" in content


def test_timeline_events_replaced_in_registry(tmp_path):
    vault = make_vault(tmp_path)
    run(make_extraction(tmp_path), vault)

    entities = json.loads(
        (vault / ".watchdog" / "Registry" / "entities.json").read_text()
    )
    events = entities["alice-smith"]["timeline_events"]
    dates = [e["date"] for e in events]
    assert "2015-11-03" in dates
    assert "2019-01-15" in dates
    # Old event text should be gone
    assert not any("Old event" in e["event"] for e in events)


def test_timeline_sorted_in_note(tmp_path):
    vault = make_vault(tmp_path)
    run(make_extraction(tmp_path), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    pos_2015 = content.find("Transferred shares")
    pos_2019 = content.find("Listed as director")
    assert pos_2015 < pos_2019


# ── Preserved sections ────────────────────────────────────────────────────────

def test_analysis_preserved(tmp_path):
    vault = make_vault(tmp_path)
    run(make_extraction(tmp_path), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    assert "Prior analysis." in content



# ── Global timeline rebuild ───────────────────────────────────────────────────

def test_global_timeline_rebuilt(tmp_path):
    vault = make_vault(tmp_path)
    run(make_extraction(tmp_path), vault)

    content = (vault / "timeline.md").read_text()
    assert "Transferred shares with no equity received" in content
    assert "Listed as director in annual report" in content


def test_global_timeline_uses_pretty_links(tmp_path):
    vault = make_vault(tmp_path)
    run(make_extraction(tmp_path), vault)

    content = (vault / "timeline.md").read_text()
    assert "[[entities/person/alice-smith|Alice Smith]]" in content
    assert "[[documents/form-79|Form 79]]" in content


# ── Error cases ───────────────────────────────────────────────────────────────

def test_unknown_entity_id_exits(tmp_path):
    vault = make_vault(tmp_path)
    extraction = make_extraction(tmp_path, entity_id="nobody-here")
    with pytest.raises(SystemExit):
        run(extraction, vault)
