import json
from pathlib import Path

import pytest

from watchdog.pipeline.write_entity import run


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    reg_dir = vault / ".watchdog" / "registry"
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
                 "page": 1, "basis": "stated", "source_sha256": "sha-doc1"},
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
        # Out of chronological order on purpose so test_timeline_sorted_in_note
        # actually exercises the sort instead of pre-ordered input.
        "timeline_events": [
            {"date": "2019-01-15", "event": "Listed as director in annual report",
             "source_sha256": "sha-doc2", "page": 4, "basis": "stated"},
            {"date": "2015-11-03", "event": "Transferred shares with no equity received",
             "source_sha256": "sha-doc1", "page": 1, "basis": "stated"},
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
    # Old event should be gone — replaced, not accumulated. The session's events live in the
    # registry (the app's entity timeline); the note lists facts, not a Timeline section (D280).
    assert "Old event from prior ingest" not in content
    assert "## Timeline" not in content
    events = json.loads((vault / ".watchdog/registry/entities.json").read_text())["alice-smith"]["timeline_events"]
    assert {e["event"] for e in events} == {"Transferred shares with no equity received",
                                            "Listed as director in annual report"}


def test_timeline_events_replaced_in_registry(tmp_path):
    vault = make_vault(tmp_path)
    run(make_extraction(tmp_path), vault)

    entities = json.loads(
        (vault / ".watchdog" / "registry" / "entities.json").read_text()
    )
    events = entities["alice-smith"]["timeline_events"]
    dates = [e["date"] for e in events]
    assert "2015-11-03" in dates
    assert "2019-01-15" in dates
    # Old event text should be gone
    assert not any("Old event" in e["event"] for e in events)


def test_session_summary_is_stored_with_who_wrote_it(tmp_path):
    vault = make_vault(tmp_path)
    run(make_extraction(tmp_path), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    assert "\n## Summary\n" in content and "Written in a Claude session" not in content
    entry = json.loads((vault / ".watchdog/registry/entities.json").read_text())["alice-smith"]
    assert entry["synthesis"]["by"] == "session"


# ── Preserved sections ────────────────────────────────────────────────────────

def test_analysis_preserved(tmp_path):
    vault = make_vault(tmp_path)
    run(make_extraction(tmp_path), vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    assert "Prior analysis." in content



# ── Global timeline rebuild ───────────────────────────────────────────────────

def _seed_canonical(vault: Path, date: str, rec: dict) -> None:
    td = vault / ".watchdog" / "timeline"
    td.mkdir(parents=True, exist_ok=True)
    (td / f"{date}.ndjson").write_text(json.dumps(rec) + "\n", encoding="utf-8")


def test_global_timeline_rebuilt_from_canonical_ndjson(tmp_path):
    """write_entity rebuilds the global timeline through the unified renderer, which reads the
    cross-document-deduped canonical NDJSON — not the entity registry's own events (#237)."""
    vault = make_vault(tmp_path)
    _seed_canonical(vault, "2019-01-15", {
        "date": "2019-01-15", "event": "Listed as director in annual report",
        "source_sha256": "sha-doc2", "page": 4, "entity_ids": ["alice-smith"], "basis": "stated"})
    run(make_extraction(tmp_path), vault)

    content = (vault / "timeline.md").read_text()
    assert "Listed as director in annual report" in content


def test_global_timeline_resolves_entity_and_document_links(tmp_path):
    vault = make_vault(tmp_path)
    _seed_canonical(vault, "2019-01-15", {
        "date": "2019-01-15", "event": "Listed as director in annual report",
        "source_sha256": "sha-doc2", "page": 4, "entity_ids": ["alice-smith"], "basis": "stated"})
    run(make_extraction(tmp_path), vault)

    content = (vault / "timeline.md").read_text()
    assert "[[entities/person/alice-smith|Alice Smith]]" in content
    assert "[[documents/annual-report|Annual Report 2019]]" in content


# ── Error cases ───────────────────────────────────────────────────────────────

def test_unknown_entity_id_exits(tmp_path):
    vault = make_vault(tmp_path)
    extraction = make_extraction(tmp_path, entity_id="nobody-here")
    with pytest.raises(SystemExit):
        run(extraction, vault)


# ── Session-input hardening ───────────────────────────────────────────────────

def test_summary_wikilinks_defanged(tmp_path):
    """The summary is model-written from document text; it must not forge a vault link."""
    vault = make_vault(tmp_path)
    path = make_extraction(tmp_path)
    data = json.loads(path.read_text())
    data["summary"] = "See [[morgue/secret|this]] for details."
    path.write_text(json.dumps(data))
    run(path, vault)

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    assert "[[morgue/secret" not in content
    assert "[ [morgue/secret|this] ]" in content


def test_malformed_events_dropped_and_unparseable_dates_cleared(tmp_path):
    vault = make_vault(tmp_path)
    path = make_extraction(tmp_path)
    data = json.loads(path.read_text())
    data["timeline_events"] += [
        "not an event",
        {"date": "2019-02-01", "event": "   "},
        {"date": "sometime in spring", "event": "Undated meeting", "source_sha256": "sha-doc1"},
    ]
    path.write_text(json.dumps(data))
    run(path, vault)

    events = json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())[
        "alice-smith"]["timeline_events"]
    assert [e["event"] for e in events].count("Undated meeting") == 1
    assert len(events) == 3
    assert next(e for e in events if e["event"] == "Undated meeting")["date"] == ""


def test_scratch_file_under_tmp_removed_after_apply(tmp_path):
    vault = make_vault(tmp_path)
    tmp_dir = vault / ".watchdog" / "tmp"
    tmp_dir.mkdir(parents=True)
    scratch = tmp_dir / "entity-refresh-alice-smith.json"
    scratch.write_text(make_extraction(tmp_path).read_text())
    run(scratch, vault)
    assert not scratch.exists()


def test_extraction_outside_tmp_left_in_place(tmp_path):
    vault = make_vault(tmp_path)
    path = make_extraction(tmp_path)
    run(path, vault)
    assert path.exists()
