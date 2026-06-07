import json
import shutil
import sys
from pathlib import Path

import pytest

from watchdog.pipeline.write_vault import run, _doc_slug


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_vault(tmp_path: Path) -> Path:
    """Create a minimal vault structure."""
    vault = tmp_path / "vault"
    reg_dir = vault / ".watchdog" / "Registry"
    reg_dir.mkdir(parents=True)
    (vault / "_INCOMING").mkdir()
    (vault / "entities" / "person").mkdir(parents=True)
    (vault / "entities" / "company").mkdir(parents=True)
    (vault / "documents").mkdir()
    (reg_dir / "entities.json").write_text("{}\n")
    (reg_dir / "documents.json").write_text("{}\n")
    (reg_dir / "registry.json").write_text(
        json.dumps({"schema_version": "1", "document_count": 0, "entity_count": 0}) + "\n"
    )
    (reg_dir / "ingest.log").write_text("")
    return vault


def make_extraction(tmp_path: Path, overrides: dict | None = None) -> Path:
    """Write a minimal extraction JSON and return its path."""
    base = {
        "document": {
            "sha256": "abc123",
            "filename": "test-doc.pdf",
            "original_path": "_INCOMING/test-doc.pdf",
            "title": "Test Document",
            "document_type": "Annual Report",
            "date_of_document": "2024-01-15",
            "page_count": 10,
            "source": "SEDAR",
            "obtained": "2025-06-01",
            "near_duplicate_of": None,
            "shingles": [],
            "summary": "A test annual report.",
            "key_facts": [{"fact": "Revenue was $1M.", "page": 3, "confidence": "high"}],
        },
        "entities": [
            {
                "id": "alice-smith",
                "name": "Alice Smith",
                "type": "Person",
                "aliases": ["A. Smith"],
                "summary": "Alice Smith is a director of Acme Corp.",
                "analysis": "Smith is listed as director with significant share holdings.",
                "timeline_events": [
                    {"date": "2020-03-15", "event": "Appointed director of Acme Corp", "page": 2, "confidence": "high"},
                    {"date": "2024", "event": "Continued as director", "page": 2, "confidence": "medium"},
                ],
                "roles": [
                    {
                        "relationship": "Director of",
                        "target_id": "acme-corp",
                        "target_type": "Company",
                        "target_name": "Acme Corp",
                        "page": 2,
                        "confidence": "high",
                        "date_range": "2020–2024",
                    }
                ],
            },
            {
                "id": "acme-corp",
                "name": "Acme Corp",
                "type": "Company",
                "aliases": ["ACME"],
                "summary": "Acme Corp is the subject of this annual report.",
                "analysis": None,
                "timeline_events": [],
                "roles": [],
            },
        ],
        "morgue_entity_id": "acme-corp",
        "morgue_document_type": "annual-report",
    }
    if overrides:
        _deep_update(base, overrides)
    path = tmp_path / "extraction.json"
    path.write_text(json.dumps(base))
    return path


def _deep_update(base: dict, overrides: dict) -> None:
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v


# ── Entity note creation ──────────────────────────────────────────────────────

def test_new_entity_note_created(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    note = vault / "entities" / "person" / "alice-smith.md"
    assert note.exists()
    content = note.read_text()
    assert "Alice Smith" in content
    assert "A. Smith" in content


def test_entity_note_has_h1_heading(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    assert "# Alice Smith" in content


def test_new_entity_note_has_summary_section(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    assert "## Summary" in content
    assert "Alice Smith is a director of Acme Corp." in content


def test_entity_note_has_analysis_section(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    assert "## Analysis" in content
    assert "Smith is listed as director" in content


def test_entity_note_analysis_omitted_when_null(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    # acme-corp has analysis: None
    content = (vault / "entities" / "company" / "acme-corp.md").read_text()
    assert "## Analysis" not in content


def test_new_entity_note_has_relationships_section(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    assert "## Relationships" in content
    assert "Director of" in content
    assert "acme-corp" in content



def test_relationship_line_uses_pretty_link(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    # Should use pipe alias, not bare path
    assert "[[entities/company/acme-corp|Acme Corp]]" in content


def test_relationship_line_includes_source_doc_link(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    assert "via [[documents/test-doc|Test Document]]" in content


def test_appears_in_uses_pretty_link(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    assert "[[documents/test-doc|Test Document]]" in content


def test_existing_entity_notes_section_preserved(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")

    existing_note = vault / "entities" / "person" / "alice-smith.md"
    existing_note.write_text(
        "---\nid: alice-smith\n---\n\n## Notes\n\nMy hand-written note.\n"
    )
    existing_entities = {
        "alice-smith": {
            "id": "alice-smith",
            "name": "Alice Smith",
            "type": "Person",
            "aliases": [],
            "appears_in": ["prior-sha"],
            "note_path": "entities/person/alice-smith",
            "roles": [],
            "date_first_seen": "2024-01-01",
            "date_last_updated": "2024-01-01",
        }
    }
    (vault / ".watchdog" / "Registry" / "entities.json").write_text(
        json.dumps(existing_entities)
    )

    run(make_extraction(tmp_path), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    assert "My hand-written note." in content


def test_analysis_accumulates_across_ingests(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")

    existing_note = vault / "entities" / "person" / "alice-smith.md"
    existing_note.write_text(
        "---\nid: alice-smith\n---\n\n"
        "## Analysis\n\n*2024-01-01, via [[documents/old-doc|Old Doc]]:* Prior finding.\n\n"
        "## Notes\n\nJournalist note.\n"
    )
    existing_entities = {
        "alice-smith": {
            "id": "alice-smith",
            "name": "Alice Smith",
            "type": "Person",
            "aliases": [],
            "appears_in": ["prior-sha"],
            "note_path": "entities/person/alice-smith",
            "roles": [],
            "date_first_seen": "2024-01-01",
            "date_last_updated": "2024-01-01",
        }
    }
    (vault / ".watchdog" / "Registry" / "entities.json").write_text(
        json.dumps(existing_entities)
    )

    run(make_extraction(tmp_path), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    assert "Prior finding." in content
    assert "Smith is listed as director" in content
    # Journalist note must survive too
    assert "Journalist note." in content


def test_summary_replaced_on_reingest(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")

    existing_note = vault / "entities" / "person" / "alice-smith.md"
    existing_note.write_text(
        "---\nid: alice-smith\n---\n\n"
        "## Summary\n\nOld stale summary.\n\n"
        "## Notes\n\n"
    )
    existing_entities = {
        "alice-smith": {
            "id": "alice-smith",
            "name": "Alice Smith",
            "type": "Person",
            "aliases": [],
            "appears_in": ["prior-sha"],
            "note_path": "entities/person/alice-smith",
            "roles": [],
            "date_first_seen": "2024-01-01",
            "date_last_updated": "2024-01-01",
        }
    }
    (vault / ".watchdog" / "Registry" / "entities.json").write_text(
        json.dumps(existing_entities)
    )

    run(make_extraction(tmp_path), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    assert "Old stale summary." not in content
    assert "Alice Smith is a director of Acme Corp." in content


# ── Entity merge ──────────────────────────────────────────────────────────────

def test_merge_adds_new_alias(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")

    existing_entities = {
        "alice-smith": {
            "id": "alice-smith",
            "name": "Alice Smith",
            "type": "Person",
            "aliases": ["Alice"],
            "appears_in": ["prior-sha"],
            "note_path": "entities/person/alice-smith",
            "roles": [],
            "date_first_seen": "2024-01-01",
            "date_last_updated": "2024-01-01",
        }
    }
    reg_dir = vault / ".watchdog" / "Registry"
    (reg_dir / "entities.json").write_text(json.dumps(existing_entities))

    run(make_extraction(tmp_path), vault)

    entities = json.loads((reg_dir / "entities.json").read_text())
    assert "A. Smith" in entities["alice-smith"]["aliases"]
    assert "Alice" in entities["alice-smith"]["aliases"]


def test_merge_adds_sha_to_appears_in(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")

    existing_entities = {
        "alice-smith": {
            "id": "alice-smith",
            "name": "Alice Smith",
            "type": "Person",
            "aliases": [],
            "appears_in": ["prior-sha"],
            "note_path": "entities/person/alice-smith",
            "roles": [],
            "date_first_seen": "2024-01-01",
            "date_last_updated": "2024-01-01",
        }
    }
    (vault / ".watchdog" / "Registry" / "entities.json").write_text(
        json.dumps(existing_entities)
    )

    run(make_extraction(tmp_path), vault)

    entities = json.loads(
        (vault / ".watchdog" / "Registry" / "entities.json").read_text()
    )
    assert "prior-sha" in entities["alice-smith"]["appears_in"]
    assert "abc123" in entities["alice-smith"]["appears_in"]


def test_merge_deduplicates_roles(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")

    existing_entities = {
        "alice-smith": {
            "id": "alice-smith",
            "name": "Alice Smith",
            "type": "Person",
            "aliases": [],
            "appears_in": ["prior-sha"],
            "note_path": "entities/person/alice-smith",
            "roles": [
                {
                    "relationship": "Director of",
                    "target_id": "acme-corp",
                    "target_type": "Company",
                    "target_name": "Acme Corp",
                    "page": 1,
                    "confidence": "high",
                    "source_sha256": "prior-sha",
                    "is_reverse": False,
                }
            ],
            "date_first_seen": "2024-01-01",
            "date_last_updated": "2024-01-01",
        }
    }
    (vault / ".watchdog" / "Registry" / "entities.json").write_text(
        json.dumps(existing_entities)
    )

    run(make_extraction(tmp_path), vault)

    entities = json.loads(
        (vault / ".watchdog" / "Registry" / "entities.json").read_text()
    )
    director_roles = [
        r for r in entities["alice-smith"]["roles"]
        if r["relationship"].lower() == "director of" and r["target_id"] == "acme-corp"
    ]
    assert len(director_roles) == 1


# ── Reverse relationship ──────────────────────────────────────────────────────

def test_reverse_relationship_written_to_target(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    content = (vault / "entities" / "company" / "acme-corp.md").read_text()
    assert "alice-smith" in content
    assert "Director of" in content


# ── Document note ─────────────────────────────────────────────────────────────

def test_document_note_created(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    doc_note = vault / "documents" / "test-doc.md"
    assert doc_note.exists()
    content = doc_note.read_text()
    assert "Annual Report" in content
    assert "A test annual report." in content
    assert "Revenue was $1M." in content


def test_document_note_links_entities_with_pretty_names(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    content = (vault / "documents" / "test-doc.md").read_text()
    assert "[[entities/person/alice-smith|Alice Smith]]" in content
    assert "[[entities/company/acme-corp|Acme Corp]]" in content


# ── Registry updates ──────────────────────────────────────────────────────────

def test_documents_json_updated(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    docs = json.loads(
        (vault / ".watchdog" / "Registry" / "documents.json").read_text()
    )
    assert "abc123" in docs
    assert docs["abc123"]["filename"] == "test-doc.pdf"
    assert docs["abc123"]["title"] == "Test Document"


def test_entities_json_updated(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    entities = json.loads(
        (vault / ".watchdog" / "Registry" / "entities.json").read_text()
    )
    assert "alice-smith" in entities
    assert "acme-corp" in entities


def test_registry_json_counts_updated(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    reg = json.loads(
        (vault / ".watchdog" / "Registry" / "registry.json").read_text()
    )
    assert reg["document_count"] == 1
    assert reg["entity_count"] == 2


def test_ingest_log_appended(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    log = (vault / ".watchdog" / "Registry" / "ingest.log").read_text()
    assert "INGEST" in log
    assert "test-doc.pdf" in log
    assert "abc123" in log


# ── Morgue move ───────────────────────────────────────────────────────────────

def test_source_file_moved_to_morgue(tmp_path):
    vault = make_vault(tmp_path)
    source = vault / "_INCOMING" / "test-doc.pdf"
    source.write_text("dummy")

    run(make_extraction(tmp_path), vault)

    assert not source.exists()
    assert (vault / "morgue" / "acme-corp" / "annual-report" / "test-doc.pdf").exists()


def test_sidecar_moved_with_source(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    sidecar = vault / "_INCOMING" / "test-doc.pdf.yml"
    sidecar.write_text("source: SEDAR\n")

    run(make_extraction(tmp_path), vault)

    assert not sidecar.exists()
    assert (
        vault / "morgue" / "acme-corp" / "annual-report" / "test-doc.pdf.yml"
    ).exists()


def test_missing_source_file_does_not_raise(tmp_path):
    vault = make_vault(tmp_path)
    run(make_extraction(tmp_path), vault)

    docs = json.loads(
        (vault / ".watchdog" / "Registry" / "documents.json").read_text()
    )
    assert "abc123" in docs


# ── Timeline ──────────────────────────────────────────────────────────────────

def test_entity_note_has_timeline_section(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    assert "## Timeline" in content
    assert "Appointed director of Acme Corp" in content


def test_timeline_day_precision_rendered(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    assert "15 Mar 2020" in content


def test_timeline_year_precision_rendered(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    # Year-only dates should appear bolded in the timeline, not just anywhere in the note
    assert "**2024**" in content


def test_timeline_sorted_chronologically(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    pos_2020 = content.find("Appointed director")
    pos_2024 = content.find("Continued as director")
    assert pos_2020 < pos_2024


def test_timeline_events_stored_in_registry(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    entities = json.loads(
        (vault / ".watchdog" / "Registry" / "entities.json").read_text()
    )
    events = entities["alice-smith"]["timeline_events"]
    assert len(events) == 2
    assert any(e["date"] == "2020-03-15" for e in events)


def test_timeline_events_deduplicated(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")

    existing_entities = {
        "alice-smith": {
            "id": "alice-smith",
            "name": "Alice Smith",
            "type": "Person",
            "aliases": [],
            "appears_in": ["prior-sha"],
            "note_path": "entities/person/alice-smith",
            "roles": [],
            "timeline_events": [
                {"date": "2020-03-15", "event": "Appointed director of Acme Corp",
                 "page": 2, "confidence": "high", "source_sha256": "prior-sha"},
            ],
            "date_first_seen": "2020-03-15",
            "date_last_updated": "2020-03-15",
        }
    }
    (vault / ".watchdog" / "Registry" / "entities.json").write_text(
        json.dumps(existing_entities)
    )

    run(make_extraction(tmp_path), vault)

    entities = json.loads(
        (vault / ".watchdog" / "Registry" / "entities.json").read_text()
    )
    # The "Appointed director" event already existed — should not be duplicated
    matching = [
        e for e in entities["alice-smith"]["timeline_events"]
        if e["date"] == "2020-03-15"
    ]
    assert len(matching) == 1



def test_global_timeline_contains_event(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    content = (vault / "timeline.md").read_text()
    assert "Appointed director of Acme Corp" in content
    assert "Alice Smith" in content


def test_global_timeline_no_events_creates_placeholder(tmp_path):
    vault = make_vault(tmp_path)
    # Extraction with no timeline events
    extraction = make_extraction(tmp_path, overrides={
        "entities": [
            {"id": "alice-smith", "name": "Alice Smith", "type": "Person",
             "aliases": [], "summary": None, "analysis": None,
             "timeline_events": [], "roles": []},
        ]
    })
    run(extraction, vault)

    content = (vault / "timeline.md").read_text()
    assert "No timeline events yet" in content


# ── Direct file links ──────────────────────────────────────────────────────────

def test_document_note_has_source_file_link(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    content = (vault / "documents" / "test-doc.md").read_text()
    assert "[[morgue/acme-corp/annual-report/test-doc.pdf]]" in content


def test_document_note_key_facts_have_page_links(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    content = (vault / "documents" / "test-doc.md").read_text()
    assert "[[morgue/acme-corp/annual-report/test-doc.pdf#page=3|p. 3]]" in content


def test_entity_timeline_has_page_link(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    # Timeline event on page 2 should link directly to that page in the morgue file
    assert "[[morgue/acme-corp/annual-report/test-doc.pdf#page=2|p. 2]]" in content


def test_entity_role_has_page_link(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    # Role on page 2 should link directly to that page
    assert "[[morgue/acme-corp/annual-report/test-doc.pdf#page=2|p. 2]]" in content


def test_global_timeline_has_page_link(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    content = (vault / "timeline.md").read_text()
    assert "[[morgue/acme-corp/annual-report/test-doc.pdf#page=2|p. 2]]" in content


# ── Empty folder cleanup ──────────────────────────────────────────────────────

def test_empty_incoming_subdirs_removed_after_ingest(tmp_path):
    vault = make_vault(tmp_path)
    subdir = vault / "_INCOMING" / "David Sam KASSEM"
    subdir.mkdir()
    (subdir / "test-doc.pdf").write_text("dummy")

    extraction = make_extraction(tmp_path, overrides={
        "document": {"original_path": "_INCOMING/David Sam KASSEM/test-doc.pdf"}
    })
    run(extraction, vault)

    assert not subdir.exists()


def test_nonempty_incoming_subdirs_preserved(tmp_path):
    vault = make_vault(tmp_path)
    subdir = vault / "_INCOMING" / "David Sam KASSEM"
    subdir.mkdir()
    (subdir / "test-doc.pdf").write_text("dummy")
    (subdir / "other.pdf").write_text("also here")

    extraction = make_extraction(tmp_path, overrides={
        "document": {"original_path": "_INCOMING/David Sam KASSEM/test-doc.pdf"}
    })
    run(extraction, vault)

    assert subdir.exists()
    assert (subdir / "other.pdf").exists()


# ── _doc_slug ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("filename,expected", [
    ("annual-report.pdf",          "annual-report"),
    ("Annual Report 2024.pdf",     "annual-report-2024"),
    ("report [final] (v2).pdf",    "report-final-v2"),
    ("Q1 Results & Summary!.pdf",  "q1-results-summary"),
    ("  spaced  .pdf",             "spaced"),
    ("[].pdf",                     "document"),        # all chars stripped → fallback
])
def test_doc_slug_strips_special_chars(filename, expected):
    assert _doc_slug(filename) == expected


# ── slug collision ────────────────────────────────────────────────────────────

def test_slug_collision_appends_sha_prefix(tmp_path, capsys):
    vault = make_vault(tmp_path)

    # Ingest first document
    run(make_extraction(tmp_path, overrides={
        "document": {"sha256": "aaa111", "filename": "annual-report.pdf",
                     "original_path": "_INCOMING/annual-report.pdf"}
    }), vault)

    # Ingest a second document that slugifies to the same name but is a different file
    (vault / "_INCOMING" / "annual-report.docx").write_text("dummy")
    run(make_extraction(tmp_path, overrides={
        "document": {"sha256": "bbb222", "filename": "annual-report.docx",
                     "original_path": "_INCOMING/annual-report.docx"}
    }), vault)

    notes = list((vault / "documents").iterdir())
    slugs = {n.stem for n in notes}
    # Both notes must exist — neither should have overwritten the other
    assert any(s == "annual-report" for s in slugs)
    assert any(s.startswith("annual-report-") for s in slugs)
    assert len(notes) == 2
