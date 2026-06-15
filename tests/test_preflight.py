import json
from pathlib import Path

from watchdog.pipeline import preflight


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    reg = vault / ".watchdog" / "Registry"
    reg.mkdir(parents=True)
    (vault / ".watchdog" / "queue").mkdir()
    return vault


def _write_queue(vault: Path, sha: str, text: str) -> None:
    queue = {
        "sha256": sha,
        "filename": "doc.pdf",
        "page_count": 1,
        "pages": [{"page": 1, "markdown": text}],
        "near_dup": {},
    }
    (vault / ".watchdog" / "queue" / f"{sha}.json").write_text(json.dumps(queue))


def _write_registry(vault: Path) -> None:
    reg = vault / ".watchdog" / "Registry"
    manifest = {
        "alice-smith": {"name": "Alice Smith", "type": "Person",
                        "aliases": ["A. Smith"], "note_path": "entities/person/alice-smith"},
        "bob-jones": {"name": "Bob Jones", "type": "Person",
                      "aliases": [], "note_path": "entities/person/bob-jones"},
    }
    (reg / "manifest.json").write_text(json.dumps(manifest))
    entities = {
        "alice-smith": {
            "id": "alice-smith", "name": "Alice Smith", "type": "Person",
            "timeline_events": [
                {"date": "2020-03-15", "event": "Appointed director",
                 "page": 2, "confidence": "high", "source_sha256": "x"},
            ],
            "roles": [
                {"relationship": "Director of", "target_id": "acme",
                 "target_name": "Acme Corp", "target_type": "Company",
                 "date_range": "2020–", "confidence": "high",
                 "source_sha256": "x", "is_reverse": False, "page": 2},
            ],
        },
    }
    (reg / "entities.json").write_text(json.dumps(entities))
    (reg / "documents.json").write_text("{}")

    note = vault / "entities" / "person" / "alice-smith.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "---\nid: alice-smith\n---\n\n# Alice Smith\n\n"
        "## Summary\n\nA director.\n\n"
        "## Analysis\n\n> [!contradiction] role mismatch\n> - foo\n\n"
        "## Timeline\n\n- timeline stuff\n"
    )


def test_candidate_enriched_with_digest_and_analysis(tmp_path):
    vault = _vault(tmp_path)
    _write_registry(vault)
    _write_queue(vault, "doc1", "Alice Smith is a director of Acme Corp.")

    result = preflight.run(vault, "doc1")
    by_id = {e["id"]: e for e in result["existing_entities"]}

    assert "alice-smith" in by_id          # name appears in text
    assert "bob-jones" not in by_id        # name absent from text

    a = by_id["alice-smith"]
    # timeline_events trimmed to comparison fields (no page / source_sha256)
    assert a["timeline_events"] == [
        {"date": "2020-03-15", "event": "Appointed director", "confidence": "high"}
    ]
    # roles trimmed to comparison fields
    assert a["roles"] == [
        {"relationship": "Director of", "target_name": "Acme Corp",
         "target_type": "Company", "date_range": "2020–", "confidence": "high"}
    ]
    # analysis carries prior contradiction callouts, scoped to the Analysis section
    assert "[!contradiction] role mismatch" in a["analysis"]
    assert "timeline stuff" not in a["analysis"]


def test_candidate_without_registry_entry_has_empty_digest(tmp_path):
    vault = _vault(tmp_path)
    reg = vault / ".watchdog" / "Registry"
    (reg / "manifest.json").write_text(json.dumps({
        "ghost": {"name": "Ghost Co", "type": "Company", "aliases": [],
                  "note_path": "entities/company/ghost"},
    }))
    (reg / "entities.json").write_text("{}")
    (reg / "documents.json").write_text("{}")
    _write_queue(vault, "doc2", "Ghost Co appears here.")

    result = preflight.run(vault, "doc2")
    a = result["existing_entities"][0]
    assert a["id"] == "ghost"
    assert a["timeline_events"] == []
    assert a["roles"] == []
    assert a["analysis"] == ""
