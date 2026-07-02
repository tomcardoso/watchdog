"""Tests for `watchdog merge-entities` (#219): deterministic registry surgery that
folds a duplicate entity into another. No model calls."""

import argparse
import json
import re
from pathlib import Path

import pytest

from watchdog.cmd.merge_entities import cmd_merge_entities
from watchdog.pipeline.merge_entities import merge, run


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_vault(tmp_path: Path) -> Path:
    """A vault with four entities wired up like a real post-ingest registry:
    - alice-smith (Person) — the survivor
    - a-smith-duplicate (Person) — a duplicate coined under a different id/spelling
    - bob-jones (Person) — a third entity whose own role targets the duplicate
    - acme-corp (Company) — carries the auto-generated reverse roles for both
    """
    vault = tmp_path / "vault"
    reg_dir = vault / ".watchdog" / "Registry"
    reg_dir.mkdir(parents=True)
    (vault / "entities" / "person").mkdir(parents=True)
    (vault / "entities" / "company").mkdir(parents=True)
    (vault / "documents").mkdir()

    entities = {
        "alice-smith": {
            "id": "alice-smith", "name": "Alice Smith", "type": "Person",
            "aliases": ["A. Smith"],
            "appears_in": ["sha-a"],
            "note_path": "entities/person/alice-smith",
            "roles": [
                {"relationship": "Director of", "target_id": "acme-corp",
                 "target_type": "Company", "target_name": "Acme Corp",
                 "page": 2, "basis": "stated", "date_range": "2020-2024",
                 "source_sha256": "sha-a", "is_reverse": False},
            ],
            "timeline_events": [
                {"date": "2020-03-15", "event": "Appointed director of Acme Corp",
                 "page": 2, "basis": "stated", "source_sha256": "sha-a"},
            ],
            "date_first_seen": "2020-03-15", "date_last_updated": "2020-03-15",
        },
        "a-smith-duplicate": {
            "id": "a-smith-duplicate", "name": "A. Smith", "type": "Person",
            "aliases": ["Smith, A."],
            "appears_in": ["sha-b"],
            "note_path": "entities/person/a-smith-duplicate",
            "roles": [
                {"relationship": "Officer of", "target_id": "acme-corp",
                 "target_type": "Company", "target_name": "Acme Corp",
                 "page": 5, "basis": "stated", "date_range": None,
                 "source_sha256": "sha-b", "is_reverse": False},
            ],
            "timeline_events": [
                {"date": "2021-05-01", "event": "Signed contract as officer",
                 "page": 5, "basis": "stated", "source_sha256": "sha-b"},
            ],
            "date_first_seen": "2021-05-01", "date_last_updated": "2021-05-01",
        },
        "bob-jones": {
            "id": "bob-jones", "name": "Bob Jones", "type": "Person",
            "aliases": [], "appears_in": ["sha-b"],
            "note_path": "entities/person/bob-jones",
            "roles": [
                {"relationship": "Reports to", "target_id": "a-smith-duplicate",
                 "target_type": "Person", "target_name": "A. Smith",
                 "page": 5, "basis": "stated", "date_range": None,
                 "source_sha256": "sha-b", "is_reverse": False},
            ],
            "timeline_events": [],
            "date_first_seen": "2021-05-01", "date_last_updated": "2021-05-01",
        },
        "acme-corp": {
            "id": "acme-corp", "name": "Acme Corp", "type": "Company",
            "aliases": [], "appears_in": ["sha-a", "sha-b"],
            "note_path": "entities/company/acme-corp",
            "roles": [
                {"relationship": "Director of", "target_id": "alice-smith",
                 "target_type": "Person", "target_name": "Alice Smith",
                 "page": 2, "basis": "stated", "date_range": "2020-2024",
                 "source_sha256": "sha-a", "is_reverse": True},
                {"relationship": "Officer of", "target_id": "a-smith-duplicate",
                 "target_type": "Person", "target_name": "A. Smith",
                 "page": 5, "basis": "stated", "date_range": None,
                 "source_sha256": "sha-b", "is_reverse": True},
            ],
            "timeline_events": [],
            "date_first_seen": "2020-03-15", "date_last_updated": "2021-05-01",
        },
    }
    documents = {
        "sha-a": {"sha256": "sha-a", "filename": "doc-a.pdf", "title": "Doc A",
                  "document_note": "documents/doc-a", "morgue_path": "morgue/x/y/doc-a.pdf"},
        "sha-b": {"sha256": "sha-b", "filename": "doc-b.pdf", "title": "Doc B",
                  "document_note": "documents/doc-b", "morgue_path": "morgue/x/y/doc-b.pdf"},
    }
    (reg_dir / "entities.json").write_text(json.dumps(entities))
    (reg_dir / "documents.json").write_text(json.dumps(documents))
    (reg_dir / "registry.json").write_text(
        json.dumps({"schema_version": "1", "document_count": 2, "entity_count": 4,
                    "last_updated": "2020-01-01T00:00:00Z"})
    )

    (vault / "entities" / "person" / "alice-smith.md").write_text(
        "---\nid: alice-smith\nname: Alice Smith\ntype: Person\n---\n\n"
        "# Alice Smith\n\n"
        "## Summary\n\nAlice is a director of Acme Corp.\n\n"
        "## Analysis\n\n*2020-03-15, via [[documents/doc-a|Doc A]]:*\n"
        "- Alice Smith is listed as director. (p. 2)\n\n"
        "## Notes\n\n<!-- Journalist annotations — never overwritten by ingestion. -->\n"
    )
    (vault / "entities" / "person" / "a-smith-duplicate.md").write_text(
        "---\nid: a-smith-duplicate\nname: A. Smith\ntype: Person\n---\n\n"
        "# A. Smith\n\n"
        "## Analysis\n\n*2021-05-01, via [[documents/doc-b|Doc B]]:*\n"
        "- Signed as officer of Acme Corp. (p. 5)\n\n"
        "## Notes\n\nCheck whether this is the same person as Alice Smith from Doc A.\n"
    )
    (vault / "entities" / "person" / "bob-jones.md").write_text(
        "---\nid: bob-jones\nname: Bob Jones\ntype: Person\n---\n\n# Bob Jones\n\n"
        "## Notes\n\n<!-- Journalist annotations — never overwritten by ingestion. -->\n"
    )
    (vault / "entities" / "company" / "acme-corp.md").write_text(
        "---\nid: acme-corp\nname: Acme Corp\ntype: Company\n---\n\n# Acme Corp\n\n"
        "## Notes\n\n<!-- Journalist annotations — never overwritten by ingestion. -->\n"
    )
    return vault


# ── merge() — pure registry surgery ───────────────────────────────────────────

def test_merge_unions_aliases():
    reg = json.loads(json.dumps(_bare_entities()))
    merge(reg, "keep", "loser")
    assert set(reg["keep"]["aliases"]) == {"Alias1", "Loser Name", "Alias2"}


def test_merge_unions_appears_in():
    reg = _bare_entities()
    merge(reg, "keep", "loser")
    assert set(reg["keep"]["appears_in"]) == {"sha-1", "sha-2"}


def test_merge_unions_timeline_events():
    reg = _bare_entities()
    merge(reg, "keep", "loser")
    dates = {e["date"] for e in reg["keep"]["timeline_events"]}
    assert dates == {"2020-01-01", "2021-06-01"}


def test_merge_deletes_losing_entity():
    reg = _bare_entities()
    merge(reg, "keep", "loser")
    assert "loser" not in reg
    assert "keep" in reg


def test_merge_rejects_same_id():
    reg = _bare_entities()
    with pytest.raises(ValueError):
        merge(reg, "keep", "keep")


def test_merge_rejects_unknown_keep_id():
    reg = _bare_entities()
    with pytest.raises(ValueError):
        merge(reg, "nobody", "loser")


def test_merge_rejects_unknown_merge_id():
    reg = _bare_entities()
    with pytest.raises(ValueError):
        merge(reg, "keep", "nobody")


def test_merge_remaps_third_party_role_target(tmp_path):
    """Acceptance criteria: a third entity's role pointing at the merged id must
    follow the merge."""
    vault = make_vault(tmp_path)
    entities = json.loads((vault / ".watchdog" / "Registry" / "entities.json").read_text())
    result = merge(entities, "alice-smith", "a-smith-duplicate")

    bob_targets = {r["target_id"] for r in entities["bob-jones"]["roles"]}
    assert bob_targets == {"alice-smith"}
    assert entities["bob-jones"]["roles"][0]["target_name"] == "Alice Smith"
    assert result["remapped_roles"] >= 2   # bob-jones + acme-corp's reverse role
    assert "bob-jones" in result["touched_entities"]
    assert "acme-corp" in result["touched_entities"]


def test_merge_remaps_reverse_role_and_keeps_both_relationships(tmp_path):
    """acme-corp carries reverse roles pointing at *both* alice-smith ("Director of")
    and a-smith-duplicate ("Officer of") — after the merge both should point at
    alice-smith, and since the relationship labels differ, both survive (Alice is
    legitimately both) rather than being collapsed into one."""
    vault = make_vault(tmp_path)
    entities = json.loads((vault / ".watchdog" / "Registry" / "entities.json").read_text())
    merge(entities, "alice-smith", "a-smith-duplicate")

    acme_roles = {(r["relationship"], r["target_id"]) for r in entities["acme-corp"]["roles"]}
    assert acme_roles == {("Director of", "alice-smith"), ("Officer of", "alice-smith")}
    assert all(t != "acme-corp" for _, t in acme_roles)


def test_merge_drops_role_that_becomes_self_referential():
    """The two merging entities already pointed at each other (keep -> loser and
    loser -> keep) — after folding loser into keep, both of those roles would say
    'keep is related to keep', which must not survive the merge."""
    reg = _bare_entities()
    reg["keep"]["roles"] = [
        {"relationship": "Knows", "target_id": "loser", "target_name": "Loser Name",
         "target_type": "Person", "source_sha256": "sha-1", "is_reverse": False},
    ]
    reg["loser"]["roles"] = [
        {"relationship": "Knows", "target_id": "keep", "target_name": "Keep Name",
         "target_type": "Person", "source_sha256": "sha-2", "is_reverse": True},
    ]
    merge(reg, "keep", "loser")

    assert reg["keep"]["roles"] == []


def _bare_entities() -> dict:
    return {
        "keep": {
            "id": "keep", "name": "Keep Name", "type": "Person",
            "aliases": ["Alias1"], "appears_in": ["sha-1"],
            "note_path": "entities/person/keep",
            "roles": [], "timeline_events": [
                {"date": "2020-01-01", "event": "Kept event", "page": 1,
                 "basis": "stated", "source_sha256": "sha-1"},
            ],
            "date_first_seen": "2020-01-01", "date_last_updated": "2020-01-01",
        },
        "loser": {
            "id": "loser", "name": "Loser Name", "type": "Person",
            "aliases": ["Alias2"], "appears_in": ["sha-2"],
            "note_path": "entities/person/loser",
            "roles": [], "timeline_events": [
                {"date": "2021-06-01", "event": "Lost event", "page": 3,
                 "basis": "stated", "source_sha256": "sha-2"},
            ],
            "date_first_seen": "2021-06-01", "date_last_updated": "2021-06-01",
        },
    }


# ── run() — full vault-level operation ────────────────────────────────────────

def test_run_unknown_keep_id_raises(tmp_path):
    vault = make_vault(tmp_path)
    with pytest.raises(ValueError):
        run(vault, "nobody", "a-smith-duplicate")


def test_run_writes_merged_entities_json(tmp_path):
    vault = make_vault(tmp_path)
    run(vault, "alice-smith", "a-smith-duplicate")

    entities = json.loads((vault / ".watchdog" / "Registry" / "entities.json").read_text())
    assert "a-smith-duplicate" not in entities
    alice = entities["alice-smith"]
    assert "Smith, A." in alice["aliases"]
    assert "sha-b" in alice["appears_in"]
    relationships = {(r["relationship"], r["target_id"]) for r in alice["roles"]}
    assert ("Officer of", "acme-corp") in relationships
    assert ("Director of", "acme-corp") in relationships


def test_run_updates_manifest(tmp_path):
    vault = make_vault(tmp_path)
    run(vault, "alice-smith", "a-smith-duplicate")

    manifest = json.loads((vault / ".watchdog" / "Registry" / "manifest.json").read_text())
    assert "a-smith-duplicate" not in manifest
    assert "Smith, A." in manifest["alice-smith"]["aliases"]


def test_run_updates_registry_entity_count(tmp_path):
    vault = make_vault(tmp_path)
    run(vault, "alice-smith", "a-smith-duplicate")

    registry = json.loads((vault / ".watchdog" / "Registry" / "registry.json").read_text())
    assert registry["entity_count"] == 3


def test_run_concatenates_analysis_with_provenance(tmp_path):
    vault = make_vault(tmp_path)
    run(vault, "alice-smith", "a-smith-duplicate")

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    assert "Alice Smith is listed as director" in content
    assert "Signed as officer of Acme Corp" in content
    assert "Merged from" in content
    assert "a-smith-duplicate" in content


def test_run_carries_over_journalist_notes(tmp_path):
    vault = make_vault(tmp_path)
    run(vault, "alice-smith", "a-smith-duplicate")

    content = (vault / "entities" / "person" / "alice-smith.md").read_text()
    assert "Check whether this is the same person as Alice Smith from Doc A." in content


def test_run_stubs_the_losing_note(tmp_path):
    vault = make_vault(tmp_path)
    run(vault, "alice-smith", "a-smith-duplicate")

    stub = (vault / "entities" / "person" / "a-smith-duplicate.md").read_text()
    assert "merged_into: alice-smith" in stub
    assert "[[entities/person/alice-smith|Alice Smith]]" in stub


def test_run_regenerates_third_party_note_with_remapped_link(tmp_path):
    """bob-jones's own note renders its Relationships section from its roles list —
    after the merge that link must point at the survivor, not the merged-away id."""
    vault = make_vault(tmp_path)
    run(vault, "alice-smith", "a-smith-duplicate")

    bob_note = (vault / "entities" / "person" / "bob-jones.md").read_text()
    assert "entities/person/alice-smith" in bob_note
    assert "entities/person/a-smith-duplicate" not in bob_note


def test_run_rebuilds_global_timeline(tmp_path):
    vault = make_vault(tmp_path)
    run(vault, "alice-smith", "a-smith-duplicate")

    timeline = (vault / "timeline.md").read_text()
    assert "Appointed director of Acme Corp" in timeline
    assert "Signed contract as officer" in timeline


def test_run_returns_report_dict(tmp_path):
    vault = make_vault(tmp_path)
    result = run(vault, "alice-smith", "a-smith-duplicate")

    assert result["keep_id"] == "alice-smith"
    assert result["merge_name"] == "A. Smith"
    assert result["keep_name"] == "Alice Smith"
    assert result["keep_note_path"] == "entities/person/alice-smith"
    assert result["remapped_roles"] >= 1


# ── cmd_merge_entities — CLI wrapper ──────────────────────────────────────────

def _args(keep_id, merge_id):
    return argparse.Namespace(keep_id=keep_id, merge_id=merge_id)


def test_cli_requires_running_inside_a_vault(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)   # no .watchdog/ here
    with pytest.raises(SystemExit):
        cmd_merge_entities(_args("alice-smith", "a-smith-duplicate"))


def test_cli_reports_unknown_id(tmp_path, monkeypatch, capsys):
    vault = make_vault(tmp_path)
    monkeypatch.chdir(vault)
    with pytest.raises(SystemExit) as exc:
        cmd_merge_entities(_args("alice-smith", "nobody-here"))
    assert "not found" in str(exc.value)


def test_cli_prints_merge_summary(tmp_path, monkeypatch, capsys):
    vault = make_vault(tmp_path)
    monkeypatch.chdir(vault)
    cmd_merge_entities(_args("alice-smith", "a-smith-duplicate"))

    out = _strip_ansi(capsys.readouterr().out)
    assert "Merged:" in out
    assert "A. Smith" in out and "Alice Smith" in out
    assert "watchdog reindex" in out

    entities = json.loads((vault / ".watchdog" / "Registry" / "entities.json").read_text())
    assert "a-smith-duplicate" not in entities
