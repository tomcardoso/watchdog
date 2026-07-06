import json
import shutil
import sys
from pathlib import Path

import pytest

import watchdog.pipeline.write_vault as wv
from watchdog.pipeline.write_vault import run, _doc_slug
from watchdog.pipeline.entity_norm import normalize_entity_name


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
            "minhash": [],
            "summary": "A test annual report.",
            "key_facts": [
                {"fact": "Revenue was $1M.", "page": 3, "basis": "stated",
                 "quote": "Total revenue for the year was $1,000,000."},
            ],
        },
        "entities": [
            {
                "id": "alice-smith",
                "name": "Alice Smith",
                "type": "Person",
                "aliases": ["A. Smith"],
                "summary": "Alice Smith is a director of Acme Corp.",
                "evidence_fragments": [
                    {"claim": "Smith is listed as director with significant share holdings.",
                     "page": 2, "basis": "stated", "reason": "establishes control of Acme",
                     "quote": "Ms. Smith holds 4,200,000 common shares of Acme Corp."},
                ],
                # Deliberately out of chronological order so the sort test exercises
                # the sort rather than asserting on already-ordered input.
                "timeline_events": [
                    {"date": "2024", "event": "Continued as director", "page": 2, "basis": "inferred"},
                    {"date": "2020-03-15", "event": "Appointed director of Acme Corp", "page": 2, "basis": "stated"},
                ],
                "roles": [
                    {
                        "relationship": "Director of",
                        "target_id": "acme-corp",
                        "target_type": "Company",
                        "target_name": "Acme Corp",
                        "page": 2,
                        "basis": "stated",
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

    # acme-corp has no evidence_fragments
    content = (vault / "entities" / "company" / "acme-corp.md").read_text()
    assert "## Analysis" not in content


def test_slim_role_target_resolved_from_id(tmp_path):
    """A role emitted with target_id only (no target_name/target_type) is re-inflated from the
    batch's entities, so its relationship link renders with the resolved name + type."""
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path, {"entities": [
        {"id": "alice-smith", "name": "Alice Smith", "type": "Person", "aliases": [],
         "summary": "A director.", "timeline_events": [],
         "roles": [{"relationship": "Director of", "target_id": "acme-corp", "basis": "stated"}]},
        {"id": "acme-corp", "name": "Acme Corp", "type": "Company", "aliases": [],
         "summary": "The company.", "timeline_events": [], "roles": []},
    ]}), vault)

    note = (vault / "entities" / "person" / "alice-smith.md").read_text()
    assert "[[entities/company/acme-corp|Acme Corp]]" in note   # name + type resolved from the id


def test_omitted_basis_renders_unmarked(tmp_path):
    """basis is omittable on the wire (absent ⇒ stated); a stated fact renders with no marker —
    only the rare inferred fact gets an *(inferred)* tag."""
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path, {
        "document": {"key_facts": [{"fact": "Revenue was $1M.", "page": 3}]},          # no basis ⇒ stated
        "entities": [
            {"id": "alice-smith", "name": "Alice Smith", "type": "Person", "aliases": [],
             "summary": "A director.",
             "timeline_events": [{"date": "2020", "event": "Appointed director"}],      # no basis ⇒ stated
             "roles": [{"relationship": "Director of", "target_id": "acme-corp"}]},      # no basis ⇒ stated
            {"id": "acme-corp", "name": "Acme Corp", "type": "Company", "aliases": [],
             "summary": "The company.", "timeline_events": [], "roles": []},
        ],
    }), vault)

    alice = (vault / "entities" / "person" / "alice-smith.md").read_text()
    doc = (vault / "documents" / "test-doc.md").read_text()
    assert "Director of [[entities/company/acme-corp|Acme Corp]]" in alice
    assert "Appointed director" in alice and "Revenue was $1M." in doc
    assert "inferred" not in alice and "inferred" not in doc   # stated facts carry no marker


def test_inferred_fact_renders_marker(tmp_path):
    """An inferred timeline event is tagged *(inferred)* in the note — the one marked exception
    (the default fixture's 'Continued as director' event is inferred)."""
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    lines = (vault / "entities" / "person" / "alice-smith.md").read_text().splitlines()
    # the inferred event's line carries the marker…
    assert any("Continued as director" in ln and "*(inferred)*" in ln for ln in lines)
    # …and the stated event alongside it does not
    assert any("Appointed director of Acme Corp" in ln and "*(inferred)*" not in ln for ln in lines)


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


def test_resolved_contradiction_dropped_from_note_body(tmp_path):
    from watchdog.pipeline import resolutions
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    callout = "> [!contradiction] Address differs from d3"
    overrides = {"entities": [{"id": "alice-smith", "name": "Alice Smith", "type": "Person",
                               "contradictions": [callout]}]}

    run(make_extraction(tmp_path, overrides), vault)
    note = vault / "entities" / "person" / "alice-smith.md"
    assert "## Contradictions" in note.read_text()
    assert "Address differs" in note.read_text()

    # Acknowledge the callout, then re-ingest the same document — it drops from the note.
    resolutions.resolve(vault, [resolutions.contradiction_id(callout)])
    run(make_extraction(tmp_path, overrides), vault)
    assert "Address differs" not in note.read_text()


def test_unresolve_restores_contradiction_to_note(tmp_path):
    # #288: "unresolving restores it" was false for entity notes — the resolved-contradiction
    # overlay only ever applied to the note render, and the callout was dropped for good once
    # a post-resolve ingest touch rewrote the note. The registry is the ledger, so unresolving
    # must bring it back on the next touch.
    from watchdog.pipeline import resolutions
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    callout = "> [!contradiction] Address differs from d3"
    overrides = {"entities": [{"id": "alice-smith", "name": "Alice Smith", "type": "Person",
                               "contradictions": [callout]}]}
    rid = resolutions.contradiction_id(callout)
    note = vault / "entities" / "person" / "alice-smith.md"

    run(make_extraction(tmp_path, overrides), vault)
    resolutions.resolve(vault, [rid])
    run(make_extraction(tmp_path, overrides), vault)
    assert "Address differs" not in note.read_text()

    resolutions.unresolve(vault, [rid])
    run(make_extraction(tmp_path, overrides), vault)
    assert "Address differs" in note.read_text()


def test_multiblock_callout_fully_resolved_not_left_in_fragments(tmp_path):
    # #288 finding 3: a callout with an internal blank line used to get re-split by the
    # note-body regex on every render, so resolving it could leave part of the block
    # behind. Registry items are never re-split, so resolving must drop it whole.
    from watchdog.pipeline import resolutions
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    callout = (
        "> [!contradiction] Address differs\n>\n"
        "> Filed as 123 Main St in 2022, 456 Oak Ave in 2024."
    )
    overrides = {"entities": [{"id": "alice-smith", "name": "Alice Smith", "type": "Person",
                               "contradictions": [callout]}]}
    note = vault / "entities" / "person" / "alice-smith.md"

    run(make_extraction(tmp_path, overrides), vault)
    assert callout in note.read_text()

    resolutions.resolve(vault, [resolutions.contradiction_id(callout)])
    run(make_extraction(tmp_path, overrides), vault)
    content = note.read_text()
    assert "Address differs" not in content
    assert "Filed as 123 Main St" not in content


def test_note_only_contradiction_backfilled_into_registry(tmp_path):
    # #288: a callout that only ever lived in the note body (pre-#282 vault) must be folded
    # into the registry entry the first time the entity is touched by an ingest, not left
    # stranded where the lead sweep and unresolve can never see it.
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    note = vault / "entities" / "person" / "alice-smith.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "---\nid: alice-smith\n---\n\n"
        "## Contradictions\n\n> [!contradiction] Note-only callout\n\n"
        "## Notes\n\n<!-- Journalist annotations — never overwritten by ingestion. -->\n"
    )
    existing_entities = {
        "alice-smith": {
            "id": "alice-smith", "name": "Alice Smith", "type": "Person",
            "aliases": [], "appears_in": ["prior-sha"],
            "note_path": "entities/person/alice-smith",
            "roles": [], "date_first_seen": "2024-01-01", "date_last_updated": "2024-01-01",
        }
    }
    (vault / ".watchdog" / "Registry" / "entities.json").write_text(json.dumps(existing_entities))

    run(make_extraction(tmp_path), vault)

    entities = json.loads((vault / ".watchdog" / "Registry" / "entities.json").read_text())
    assert entities["alice-smith"]["contradictions"] == ["> [!contradiction] Note-only callout"]


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
                    "basis": "stated",
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


def test_new_entity_persists_contradictions_to_registry(tmp_path):
    # #252: the registry entry must carry the contradiction callouts so leads.find_leads
    # (which reads them straight from entities.json) isn't a structurally dead signal.
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    callout = "> [!contradiction] Address differs from prior filing"
    overrides = {"entities": [{"id": "alice-smith", "name": "Alice Smith", "type": "Person",
                               "contradictions": [callout]}]}

    run(make_extraction(tmp_path, overrides), vault)

    entities = json.loads(
        (vault / ".watchdog" / "Registry" / "entities.json").read_text()
    )
    assert entities["alice-smith"]["contradictions"] == [callout]


def test_merge_deduplicates_contradictions(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")

    callout = "> [!contradiction] Address differs from prior filing"
    existing_entities = {
        "alice-smith": {
            "id": "alice-smith",
            "name": "Alice Smith",
            "type": "Person",
            "aliases": [],
            "appears_in": ["prior-sha"],
            "note_path": "entities/person/alice-smith",
            "roles": [],
            "contradictions": [callout],
            "date_first_seen": "2024-01-01",
            "date_last_updated": "2024-01-01",
        }
    }
    (vault / ".watchdog" / "Registry" / "entities.json").write_text(
        json.dumps(existing_entities)
    )

    new_callout = "> [!contradiction] Director count differs from prior filing"
    overrides = {"entities": [{"id": "alice-smith", "name": "Alice Smith", "type": "Person",
                               "contradictions": [callout, new_callout]}]}
    run(make_extraction(tmp_path, overrides), vault)

    entities = json.loads(
        (vault / ".watchdog" / "Registry" / "entities.json").read_text()
    )
    assert entities["alice-smith"]["contradictions"] == [callout, new_callout]


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


# ── Manifest ──────────────────────────────────────────────────────────────────

def test_manifest_created_on_ingest(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    manifest_path = vault / ".watchdog" / "Registry" / "manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert "alice-smith" in manifest
    assert "acme-corp" in manifest


def test_manifest_contains_only_lookup_fields(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    manifest = json.loads(
        (vault / ".watchdog" / "Registry" / "manifest.json").read_text()
    )
    entry = manifest["alice-smith"]
    assert set(entry.keys()) == {"name", "type", "aliases", "note_path"}
    # Must NOT contain timeline_events or roles
    assert "timeline_events" not in entry
    assert "roles" not in entry
    assert "appears_in" not in entry


def test_manifest_includes_aliases(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    manifest = json.loads(
        (vault / ".watchdog" / "Registry" / "manifest.json").read_text()
    )
    assert "A. Smith" in manifest["alice-smith"]["aliases"]


def test_manifest_note_path_is_correct(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    manifest = json.loads(
        (vault / ".watchdog" / "Registry" / "manifest.json").read_text()
    )
    assert manifest["alice-smith"]["note_path"] == "entities/person/alice-smith"


# ── Morgue move ───────────────────────────────────────────────────────────────

def test_source_file_moved_to_morgue(tmp_path):
    vault = make_vault(tmp_path)
    source = vault / "_INCOMING" / "test-doc.pdf"
    source.write_text("dummy")

    run(make_extraction(tmp_path), vault)

    assert not source.exists()
    assert (vault / "morgue" / "acme-corp" / "annual-report" / "test-doc.pdf").exists()


def test_staging_dir_pruned_after_move_to_morgue(tmp_path):
    """Documents preprocessed via preprocess_batch.py land in .watchdog/staging/<sha>/ rather
    than _INCOMING/ — that now-empty per-doc dir must be pruned too, or it accumulates forever,
    one per ingested document (#265)."""
    vault = make_vault(tmp_path)
    staging_dir = vault / ".watchdog" / "staging" / "abc123"
    staging_dir.mkdir(parents=True)
    (staging_dir / "test-doc.pdf").write_text("dummy")

    run(make_extraction(tmp_path, {"document": {
        "original_path": ".watchdog/staging/abc123/test-doc.pdf",
    }}), vault)

    assert (vault / "morgue" / "acme-corp" / "annual-report" / "test-doc.pdf").exists()
    assert not staging_dir.exists()


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
                 "page": 2, "basis": "stated", "source_sha256": "prior-sha"},
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


def test_timeline_dedup_keeps_events_with_long_shared_opening(tmp_path):
    """Regression guard: dedup keys on the full event text today, not a truncated prefix. A
    prefix-based key (what this function used before, and what a future edit could reintroduce)
    would treat these two events as one and silently drop the second — the opening clause is
    identical, but who the property went to (the material fact) differs after the shared part."""
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")

    shared_opening = ("On March 3, 2019, Acme Holdings Ltd. transferred beneficial ownership of the "
                       "Cayman Islands shell company, via an intermediary numbered offshore company, to ")
    # Not tied to any specific number in the source — just long enough that any prefix-based
    # dedup key, past or future, would wrongly treat these two events as the same one.
    assert len(shared_opening) > 150
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
                {"date": "2019-03-03", "event": shared_opening + "shell company to John Smith.",
                 "page": 2, "basis": "stated", "source_sha256": "prior-sha"},
            ],
            "date_first_seen": "2019-03-03",
            "date_last_updated": "2019-03-03",
        }
    }
    (vault / ".watchdog" / "Registry" / "entities.json").write_text(
        json.dumps(existing_entities)
    )

    run(make_extraction(tmp_path, {"entities": [
        {"id": "alice-smith", "name": "Alice Smith", "type": "Person", "aliases": [],
         "summary": "A director.", "roles": [], "timeline_events": [
            {"date": "2019-03-03", "event": shared_opening + "shell company to Jane Doe's family trust.",
             "page": 5, "basis": "stated"},
        ]},
        {"id": "acme-corp", "name": "Acme Corp", "type": "Company", "aliases": [],
         "summary": "The company.", "timeline_events": [], "roles": []},
    ]}), vault)

    entities = json.loads(
        (vault / ".watchdog" / "Registry" / "entities.json").read_text()
    )
    matching = [
        e for e in entities["alice-smith"]["timeline_events"]
        if e["date"] == "2019-03-03"
    ]
    assert len(matching) == 2
    assert any(e["event"].endswith("John Smith.") for e in matching)
    assert any(e["event"].endswith("family trust.") for e in matching)


# The global timeline is no longer a write_vault product (#237) — write_vault.run() does not
# touch timeline.md; it is rendered from the cross-document-deduped NDJSON by
# timeline.cmd_rebuild_timeline. See test_timeline.py for the global timeline's attribution,
# entity-link, and page-link coverage. write_vault still owns the per-entity `## Timeline`
# section (tested by test_entity_note_has_timeline_section / test_entity_timeline_has_page_link).


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


def test_document_note_key_fact_quote_renders_as_blockquote(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    content = (vault / "documents" / "test-doc.md").read_text()
    # The optional verbatim quote renders as a blockquote beneath its key fact.
    assert "  > Total revenue for the year was $1,000,000." in content


def test_key_fact_without_quote_has_no_blockquote(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path, {"document": {
        "key_facts": [{"fact": "Revenue was $1M.", "page": 3, "basis": "stated"}],
    }}), vault)

    content = (vault / "documents" / "test-doc.md").read_text()
    assert "Revenue was $1M." in content
    assert "  > " not in content


def test_document_note_flags_unverified_quote(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path, {"document": {
        "key_facts": [{"fact": "Revenue was $1M.", "page": 3, "basis": "stated",
                       "quote": "Total revenue for the year was $1,000,000.",
                       "quote_verified": False}],
    }}), vault)

    content = (vault / "documents" / "test-doc.md").read_text()
    assert "  > Total revenue for the year was $1,000,000. *(quote not found on cited page — verify against source)*" in content


def test_document_note_notes_quote_found_on_different_page(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path, {"document": {
        "key_facts": [{"fact": "Revenue was $1M.", "page": 3, "basis": "stated",
                       "quote": "Total revenue for the year was $1,000,000.",
                       "quote_found_page": 4}],
    }}), vault)

    content = (vault / "documents" / "test-doc.md").read_text()
    assert "  > Total revenue for the year was $1,000,000. *(found on p. 4, not the cited page)*" in content


def test_entity_analysis_renders_claim_reason_and_quote(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    # claim bullet, its `— reason`, page link, and the verbatim quote as a blockquote
    assert "## Analysis" in content
    assert "- Smith is listed as director with significant share holdings." in content
    assert "— establishes control of Acme" in content
    assert "  > Ms. Smith holds 4,200,000 common shares of Acme Corp." in content


def test_fragment_digest_carries_claim_and_quote(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)

    frag = (vault / ".watchdog" / "tmp" / "entity-fragments" / "alice-smith.md").read_text()
    # The synthesis digest carries the claim and its quote so the finalizer can choose to surface it.
    assert "Claims:" in frag
    assert "Smith is listed as director" in frag
    assert "Ms. Smith holds 4,200,000 common shares" in frag


# ── Security: path-traversal / vault-escape guard (#303) ──────────────────────
#
# postflight._sanitize_entity_ids slugifies entity id/type before write_vault ever runs; these
# tests call write_vault.run() directly (bypassing postflight) to exercise the layer-2
# defense-in-depth backstop on its own — a malicious id/type must not escape the vault even if
# upstream sanitization were somehow skipped.

def test_path_traversal_entity_id_rejected(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    extraction = make_extraction(tmp_path, {"entities": [
        {"id": "../../../ESCAPED", "name": "Evil Corp", "type": "Person", "aliases": [],
         "timeline_events": [], "roles": []},
        {"id": "acme-corp", "name": "Acme Corp", "type": "Company", "aliases": [],
         "timeline_events": [], "roles": []},
    ]})

    with pytest.raises(SystemExit):
        run(extraction, vault)

    assert not (tmp_path / "ESCAPED.md").exists()


def test_path_traversal_entity_type_rejected(tmp_path):
    """type is not slugified upstream (it stays a display value), so _type_dir itself must
    strip traversal characters rather than just lowercasing them."""
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    extraction = make_extraction(tmp_path, {"entities": [
        {"id": "alice-smith", "name": "Alice Smith", "type": "../../../person", "aliases": [],
         "timeline_events": [], "roles": []},
        {"id": "acme-corp", "name": "Acme Corp", "type": "Company", "aliases": [],
         "timeline_events": [], "roles": []},
    ]})

    run(extraction, vault)   # traversal chars are stripped, not merely lowercased — no escape

    assert (vault / "entities" / "person" / "alice-smith.md").exists()
    assert not (tmp_path / "person.md").exists()


# ── Security: wikilink display-text defanging (#305) ───────────────────────────

def test_entity_name_bracket_injection_defanged_in_heading(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    hostile_name = "Acme]] [[entities/company/acme-corp|cleared"
    run(make_extraction(tmp_path, {"entities": [
        {"id": "alice-smith", "name": hostile_name, "type": "Person", "aliases": [],
         "timeline_events": [], "roles": []},
        {"id": "acme-corp", "name": "Acme Corp", "type": "Company", "aliases": [],
         "timeline_events": [], "roles": []},
    ]}), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    # Frontmatter keeps the raw name (safe — yaml.dump already escapes it); only the H1 heading,
    # which is interpolated as literal Markdown, needs the wikilink defang.
    heading = content.split("---\n", 2)[-1]
    assert "]] [[" not in heading
    assert "# Acme] ] [ [entities/company/acme-corp|cleared" in heading


def test_document_title_bracket_injection_defanged_in_role_line(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    hostile_title = "Report]] [[entities/company/acme-corp|Cleared"
    run(make_extraction(tmp_path, {"document": {"title": hostile_title}}), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    assert "]] [[" not in content


def test_document_note_entity_mention_name_defanged(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    hostile_name = "Acme]] [[entities/company/rival|Rival Corp"
    run(make_extraction(tmp_path, {"entities": [
        {"id": "alice-smith", "name": "Alice Smith", "type": "Person", "aliases": [],
         "timeline_events": [], "roles": []},
        {"id": "acme-corp", "name": hostile_name, "type": "Company", "aliases": [],
         "timeline_events": [], "roles": []},
    ]}), vault)

    content = (vault / "documents" / "test-doc.md").read_text()
    assert "]] [[" not in content


def test_role_target_id_bracket_injection_slugified_in_wikilink_target():
    # A role can point at an unprofiled entity (leads.py: "named but never profiled"), whose
    # target_id never passes through postflight's slugify pass. It lands in the wikilink *target*
    # position, so a hostile value must be slugified here or it forges a second wikilink.
    from watchdog.pipeline.write_vault import _role_line
    role = {
        "relationship": "Director of",
        "target_id": "acme]] [[entities/company/cleared",
        "target_type": "Company",
        "target_name": "Acme Corp",
    }
    line = _role_line(role, {})
    assert "]] [[" not in line
    assert line.count("[[") == 1 and line.count("]]") == 1
    assert "[[entities/company/acme-entitiescompanycleared|Acme Corp]]" in line


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


# ── Concurrent-safety (sequential simulation) ─────────────────────────────────

def test_two_sequential_runs_merge_shared_entity(tmp_path):
    """Two documents mentioning the same entity must produce a merged registry entry."""
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "doc-a.pdf").write_text("dummy")
    (vault / "_INCOMING" / "doc-b.pdf").write_text("dummy")

    dir_a = tmp_path / "a"
    dir_a.mkdir()
    dir_b = tmp_path / "b"
    dir_b.mkdir()

    run(make_extraction(dir_a, overrides={
        "document": {
            "sha256": "sha-a", "filename": "doc-a.pdf",
            "original_path": "_INCOMING/doc-a.pdf",
        },
        "entities": [{
            "id": "alice-smith", "name": "Alice Smith", "type": "Person",
            "aliases": ["A. Smith"], "summary": "Director.", "analysis": None,
            "timeline_events": [{"date": "2020-01-01", "event": "Joined board", "page": 1, "basis": "stated"}],
            "roles": [],
        }],
        "morgue_entity_id": "alice-smith",
        "morgue_document_type": "annual-report",
    }), vault)

    run(make_extraction(dir_b, overrides={
        "document": {
            "sha256": "sha-b", "filename": "doc-b.pdf",
            "original_path": "_INCOMING/doc-b.pdf",
        },
        "entities": [{
            "id": "alice-smith", "name": "Alice Smith", "type": "Person",
            "aliases": ["Alice S."], "summary": "Director.", "analysis": None,
            "timeline_events": [{"date": "2022-06-15", "event": "Resigned", "page": 3, "basis": "stated"}],
            "roles": [],
        }],
        "morgue_entity_id": "alice-smith",
        "morgue_document_type": "press-release",
    }), vault)

    entities = json.loads((vault / ".watchdog" / "Registry" / "entities.json").read_text())
    alice = entities["alice-smith"]
    assert "sha-a" in alice["appears_in"]
    assert "sha-b" in alice["appears_in"]
    assert "Alice S." in alice["aliases"]
    assert "A. Smith" in alice["aliases"]
    dates = {e["date"] for e in alice["timeline_events"]}
    assert "2020-01-01" in dates
    assert "2022-06-15" in dates


# ── Duplicate-slug reconciliation (issue #79) ─────────────────────────────────

@pytest.mark.parametrize("a,b", [
    ("Ernst & Young Inc.", "Ernst and Young Inc"),
    ("Ernst  and   Young  Inc", "ernst and young inc"),
])
def test_normalize_entity_name_collapses_variants(a, b):
    assert normalize_entity_name(a) == normalize_entity_name(b)


def test_normalize_entity_name_distinguishes_real_differences():
    assert normalize_entity_name("Acme Corp") != normalize_entity_name("Acme Holdings")


def _company_extraction(dirpath, sha, filename, eid, name):
    return make_extraction(dirpath, overrides={
        "document": {
            "sha256": sha, "filename": filename,
            "original_path": f"_INCOMING/{filename}",
        },
        "entities": [{
            "id": eid, "name": name, "type": "Company",
            "aliases": [], "summary": None, "analysis": None,
            "timeline_events": [], "roles": [],
        }],
        "morgue_entity_id": eid,
        "morgue_document_type": "annual-report",
    })


def test_parallel_slug_variants_reconciled(tmp_path):
    """Two docs coining different slugs for the same entity must collapse to one."""
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "doc-a.pdf").write_text("dummy")
    (vault / "_INCOMING" / "doc-b.pdf").write_text("dummy")
    dir_a, dir_b = tmp_path / "a", tmp_path / "b"
    dir_a.mkdir(); dir_b.mkdir()

    # First subagent wins the slug; second coins a near-duplicate id + name variant.
    run(_company_extraction(dir_a, "sha-a", "doc-a.pdf",
                            "ernst-and-young-inc", "Ernst & Young Inc."), vault)
    run(_company_extraction(dir_b, "sha-b", "doc-b.pdf",
                            "ernst-young-inc", "Ernst and Young Inc"), vault)

    entities = json.loads((vault / ".watchdog" / "Registry" / "entities.json").read_text())
    assert "ernst-and-young-inc" in entities
    assert "ernst-young-inc" not in entities          # reconciled away, not a duplicate
    ey = entities["ernst-and-young-inc"]
    assert "sha-a" in ey["appears_in"] and "sha-b" in ey["appears_in"]
    assert "Ernst and Young Inc" in ey["aliases"]     # variant folded in as alias


def test_distinct_same_type_entities_not_merged(tmp_path):
    """Reconciliation must not collapse genuinely different entities of the same type."""
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "doc-a.pdf").write_text("dummy")
    (vault / "_INCOMING" / "doc-b.pdf").write_text("dummy")
    dir_a, dir_b = tmp_path / "a", tmp_path / "b"
    dir_a.mkdir(); dir_b.mkdir()

    run(_company_extraction(dir_a, "sha-a", "doc-a.pdf", "acme-corp", "Acme Corp"), vault)
    run(_company_extraction(dir_b, "sha-b", "doc-b.pdf", "globex-corp", "Globex Corp"), vault)

    entities = json.loads((vault / ".watchdog" / "Registry" / "entities.json").read_text())
    assert "acme-corp" in entities
    assert "globex-corp" in entities


def test_reconcile_remaps_role_target_in_same_document(tmp_path):
    """A role pointing at a reconciled entity in the same extraction is remapped too."""
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "doc-a.pdf").write_text("dummy")
    (vault / "_INCOMING" / "doc-b.pdf").write_text("dummy")
    dir_a, dir_b = tmp_path / "a", tmp_path / "b"
    dir_a.mkdir(); dir_b.mkdir()

    # Doc A establishes the canonical company slug.
    run(_company_extraction(dir_a, "sha-a", "doc-a.pdf",
                            "ernst-and-young-inc", "Ernst & Young Inc."), vault)

    # Doc B re-coins the company under a different slug AND references it from a person.
    run(make_extraction(dir_b, overrides={
        "document": {"sha256": "sha-b", "filename": "doc-b.pdf",
                     "original_path": "_INCOMING/doc-b.pdf"},
        "entities": [
            {"id": "jane-doe", "name": "Jane Doe", "type": "Person",
             "aliases": [], "summary": None, "analysis": None, "timeline_events": [],
             "roles": [{
                 "relationship": "Partner at", "target_id": "ernst-young-inc",
                 "target_type": "Company", "target_name": "Ernst and Young Inc",
                 "page": 1, "basis": "stated", "date_range": None,
             }]},
            {"id": "ernst-young-inc", "name": "Ernst and Young Inc", "type": "Company",
             "aliases": [], "summary": None, "analysis": None,
             "timeline_events": [], "roles": []},
        ],
        "morgue_entity_id": "jane-doe",
        "morgue_document_type": "filing",
    }), vault)

    entities = json.loads((vault / ".watchdog" / "Registry" / "entities.json").read_text())
    # Person's role now points at the canonical slug, not the orphaned one.
    role = entities["jane-doe"]["roles"][0]
    assert role["target_id"] == "ernst-and-young-inc"
    # Reverse role landed on the canonical company entity.
    assert any(r["target_id"] == "jane-doe" for r in entities["ernst-and-young-inc"]["roles"])


def test_reconcile_matches_against_existing_alias(tmp_path):
    """A new slug matching an existing entity's *alias* (not its name) reconciles."""
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "doc-a.pdf").write_text("dummy")
    (vault / "_INCOMING" / "doc-b.pdf").write_text("dummy")
    dir_a, dir_b = tmp_path / "a", tmp_path / "b"
    dir_a.mkdir(); dir_b.mkdir()

    # Doc A establishes the entity with an alias.
    run(make_extraction(dir_a, overrides={
        "document": {"sha256": "sha-a", "filename": "doc-a.pdf", "original_path": "_INCOMING/doc-a.pdf"},
        "entities": [{"id": "ibm", "name": "IBM", "type": "Company",
                      "aliases": ["International Business Machines"], "summary": None, "analysis": None,
                      "timeline_events": [], "roles": []}],
        "morgue_entity_id": "ibm", "morgue_document_type": "annual-report",
    }), vault)

    # Doc B coins a new slug whose name matches the *alias* above.
    run(_company_extraction(dir_b, "sha-b", "doc-b.pdf",
                            "international-business-machines", "International Business Machines"), vault)

    entities = json.loads((vault / ".watchdog" / "Registry" / "entities.json").read_text())
    assert "international-business-machines" not in entities   # reconciled onto the alias match
    assert "sha-b" in entities["ibm"]["appears_in"]


def test_reconcile_does_not_merge_across_types(tmp_path):
    """Same normalized name but different entity types must stay separate."""
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "doc-a.pdf").write_text("dummy")
    (vault / "_INCOMING" / "doc-b.pdf").write_text("dummy")
    dir_a, dir_b = tmp_path / "a", tmp_path / "b"
    dir_a.mkdir(); dir_b.mkdir()

    # A Person and a Company that normalize to the same key.
    run(make_extraction(dir_a, overrides={
        "document": {"sha256": "sha-a", "filename": "doc-a.pdf", "original_path": "_INCOMING/doc-a.pdf"},
        "entities": [{"id": "morgan-person", "name": "Morgan", "type": "Person",
                      "aliases": [], "summary": None, "analysis": None,
                      "timeline_events": [], "roles": []}],
        "morgue_entity_id": "morgan-person", "morgue_document_type": "filing",
    }), vault)
    run(_company_extraction(dir_b, "sha-b", "doc-b.pdf", "morgan-company", "Morgan"), vault)

    entities = json.loads((vault / ".watchdog" / "Registry" / "entities.json").read_text())
    assert "morgan-person" in entities and "morgan-company" in entities  # type-scoped, not merged


# ── Transactionality / idempotent re-run (#259) ───────────────────────────────

# A realistic 64-hex sha so the fragment block's `(sha <7hex>)` key is exercised by the
# replace-not-append logic (short fixture shas like "abc123" never re-run, so they don't).
_REAL_SHA = "a1b2c3d4" * 8


def _frag_queue(vault: Path) -> dict:
    return json.loads(
        (vault / ".watchdog" / "tmp" / "entity-fragments" / "_queue.json").read_text()
    )


def _real_sha_extraction(tmp_path: Path) -> Path:
    return make_extraction(tmp_path, {"document": {"sha256": _REAL_SHA}})


def test_reingest_same_document_is_idempotent(tmp_path):
    """Running write_vault twice for the same document must replace its contribution, not
    double it — the ## Analysis block, the fragment block, and the queue count all stay singular."""
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    run(_real_sha_extraction(tmp_path), vault)
    # Second run re-reads the same extraction (the source has moved to the morgue, but the
    # note/fragment writes must still converge on the already-present contribution).
    run(_real_sha_extraction(tmp_path), vault)

    note = (vault / "entities" / "person" / "alice-smith.md").read_text()
    # The claim lives only in ## Analysis; a doubled entry would count it twice.
    assert wv._extract_section(note, "Analysis").count("via [[documents/test-doc") == 1
    assert note.count("Smith is listed as director") == 1

    frag = (vault / ".watchdog" / "tmp" / "entity-fragments" / "alice-smith.md").read_text()
    assert frag.count(f"(sha {_REAL_SHA[:7]})") == 1
    assert _frag_queue(vault)["alice-smith"]["count"] == 1


def test_repair_retry_converges_after_crash_before_registry_persist(tmp_path, monkeypatch):
    """Crash between the note writes and the registry persist: the registries stay untouched
    (the commit never landed), and a repair retry converges instead of doubling claims (#259)."""
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    ex = _real_sha_extraction(tmp_path)

    real_rename = Path.rename
    crashed = {"done": False}

    def flaky_rename(self, target):
        # Fail the first atomic registry write (entities.json) — i.e. after the notes are
        # written but before any registry file is persisted.
        if not crashed["done"] and str(target).endswith("entities.json"):
            crashed["done"] = True
            raise OSError("simulated crash before registry persist")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky_rename)
    with pytest.raises(OSError):
        run(ex, vault)

    # Commit never landed: registries empty, but the entity note was written (partial write).
    assert json.loads((vault / ".watchdog/Registry/entities.json").read_text()) == {}
    assert (vault / "entities" / "person" / "alice-smith.md").exists()

    # Repair retry (crash cleared) must converge.
    monkeypatch.setattr(Path, "rename", real_rename)
    run(_real_sha_extraction(tmp_path), vault)

    entities = json.loads((vault / ".watchdog/Registry/entities.json").read_text())
    assert _REAL_SHA in entities["alice-smith"]["appears_in"]
    note = (vault / "entities" / "person" / "alice-smith.md").read_text()
    assert wv._extract_section(note, "Analysis").count("via [[documents/test-doc") == 1
    frag = (vault / ".watchdog" / "tmp" / "entity-fragments" / "alice-smith.md").read_text()
    assert frag.count(f"(sha {_REAL_SHA[:7]})") == 1
    assert _frag_queue(vault)["alice-smith"]["count"] == 1


def test_repair_retry_converges_after_crash_during_derived_writes(tmp_path, monkeypatch):
    """Crash *after* the registries persist, midway through the derived fragment writes: the
    retry re-runs write_vault with the registries already committed, and the replace-by-sha
    fragment logic keeps each document's block singular (#259)."""
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    ex = _real_sha_extraction(tmp_path)

    real_frag = wv._record_entity_fragment
    calls = {"n": 0}

    def flaky_frag(*args, **kwargs):
        real_frag(*args, **kwargs)          # the block is actually written…
        calls["n"] += 1
        if calls["n"] == 1:                 # …then crash right after the first entity
            raise RuntimeError("simulated crash mid derived-writes")

    monkeypatch.setattr(wv, "_record_entity_fragment", flaky_frag)
    with pytest.raises(RuntimeError):
        run(ex, vault)

    # The registries committed before the crash.
    entities = json.loads((vault / ".watchdog/Registry/entities.json").read_text())
    assert _REAL_SHA in entities["alice-smith"]["appears_in"]

    # Repair retry with fragments healthy again must converge — no doubled blocks.
    monkeypatch.setattr(wv, "_record_entity_fragment", real_frag)
    run(_real_sha_extraction(tmp_path), vault)

    frag_dir = vault / ".watchdog" / "tmp" / "entity-fragments"
    for eid in ("alice-smith", "acme-corp"):
        frag = (frag_dir / f"{eid}.md").read_text()
        assert frag.count(f"(sha {_REAL_SHA[:7]})") == 1, f"{eid} block doubled"
        assert _frag_queue(vault)[eid]["count"] == 1


class _FakeMsvcrt:
    """Stand-in for the `msvcrt` stdlib module (Windows-only, unimportable in CI), so the
    Windows locking branch can be exercised on macOS/Linux test runners (issue #258)."""
    LK_LOCK = 1
    LK_UNLCK = 0

    def __init__(self):
        self.calls: list[tuple[int, int]] = []

    def locking(self, fd, mode, nbytes):
        self.calls.append((mode, nbytes))


def test_registry_lock_uses_msvcrt_on_windows(tmp_path, monkeypatch):
    """Without fcntl (Windows), `_registry_lock` must actually serialize via msvcrt.locking
    rather than silently no-op'ing — the bug fixed for issue #258."""
    vault = make_vault(tmp_path)
    fake = _FakeMsvcrt()
    monkeypatch.setattr(wv, "_HAS_FLOCK", False)
    monkeypatch.setattr(wv, "_msvcrt", fake)

    with wv._registry_lock(vault / ".watchdog" / "Registry"):
        pass

    assert fake.calls == [(fake.LK_LOCK, 1), (fake.LK_UNLCK, 1)]


def test_registry_lock_is_noop_when_neither_locking_mechanism_available(tmp_path, monkeypatch):
    """If neither fcntl nor msvcrt is importable, the lock degrades to a no-op (callers rely
    on in-process serialization only, D18) instead of raising."""
    vault = make_vault(tmp_path)
    monkeypatch.setattr(wv, "_HAS_FLOCK", False)
    monkeypatch.setattr(wv, "_msvcrt", None)

    with wv._registry_lock(vault / ".watchdog" / "Registry"):
        pass  # must not raise

    assert (vault / ".watchdog" / "Registry" / ".write-lock").exists()


def test_drop_analysis_entry_replaces_only_matching_document():
    analysis = (
        "*3 May 2026, via [[documents/doc-a|Doc A]]:*\n- Claim A\n\n"
        "*3 May 2026, via [[documents/doc-b|Doc B]]:*\n- Claim B"
    )
    kept = wv._drop_analysis_entry(analysis, "documents/doc-a")
    assert "Claim A" not in kept
    assert "Claim B" in kept


def test_drop_fragment_block_replaces_only_matching_sha():
    text = (
        "\n### Doc A — filing, 2020 (sha aaaaaaa)\nClaim A\n"
        "\n### Doc B — filing, 2021 (sha bbbbbbb)\nClaim B\n"
    )
    kept = wv._drop_fragment_block(text, "aaaaaaa" + "0" * 57)
    assert "Claim A" not in kept
    assert "(sha bbbbbbb)" in kept and "Claim B" in kept
