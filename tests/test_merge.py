import json
from pathlib import Path

from watchdog.pipeline import merge
from watchdog.pipeline.merge import merge_extractions


def test_merge_unions_entities_by_id():
    sections = [
        {"document": {"sha256": "x", "filename": "f",
                      "key_facts": [{"fact": "A", "page": 1, "confidence": "high"}]},
         "entities": [{"id": "acme", "name": "Acme Corp", "type": "Company",
                       "aliases": ["the Company"],
                       "timeline_events": [{"date": "2020", "event": "incorporated", "confidence": "high"}],
                       "roles": []}],
         "morgue_entity_id": "acme", "morgue_document_type": "annual-report"},
        {"document": {"key_facts": [{"fact": "B", "page": 60, "confidence": "high"}]},
         "entities": [
             {"id": "acme", "name": "Acme Corp", "type": "Company", "aliases": [],
              "timeline_events": [{"date": "2021", "event": "filed report", "confidence": "medium"}],
              "roles": [{"relationship": "Director of", "target_id": "jane",
                         "target_type": "Person", "target_name": "Jane", "page": 61,
                         "confidence": "high", "date_range": None}]},
             {"id": "jane", "name": "Jane Doe", "type": "Person", "aliases": [],
              "timeline_events": [], "roles": []}],
         "morgue_entity_id": None, "morgue_document_type": None},
    ]
    merged = merge_extractions(sections)
    ents = {e["id"]: e for e in merged["entities"]}

    assert set(ents) == {"acme", "jane"}
    assert "the Company" in ents["acme"]["aliases"]
    assert {ev["date"] for ev in ents["acme"]["timeline_events"]} == {"2020", "2021"}
    assert len(ents["acme"]["roles"]) == 1
    assert {f["fact"] for f in merged["document"]["key_facts"]} == {"A", "B"}
    assert merged["document"]["sha256"] == "x"          # document from first non-empty
    assert merged["morgue_entity_id"] == "acme"          # first non-null wins


def test_merge_folds_id_drift_by_normalized_name():
    sections = [
        {"document": {"sha256": "x", "filename": "f"},
         "entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                       "aliases": [], "timeline_events": [], "roles": []}]},
        {"document": {},
         "entities": [{"id": "acme-corp-2", "name": "ACME  CORP", "type": "Company",
                       "aliases": [], "timeline_events": [{"date": "2021", "event": "x", "confidence": "high"}],
                       "roles": []}]},
    ]
    merged = merge_extractions(sections)
    assert len(merged["entities"]) == 1                  # drift folded onto one id
    assert merged["entities"][0]["id"] == "acme-corp"
    assert len(merged["entities"][0]["timeline_events"]) == 1


def test_merge_dedups_timeline_and_key_facts():
    section = {
        "document": {"sha256": "x", "key_facts": [{"fact": "Same fact", "page": 1, "confidence": "high"}]},
        "entities": [{"id": "a", "name": "A", "type": "Person", "aliases": [],
                      "timeline_events": [{"date": "2020", "event": "did thing", "confidence": "high"}],
                      "roles": []}],
    }
    merged = merge_extractions([section, json.loads(json.dumps(section))])  # identical twice
    assert len(merged["entities"][0]["timeline_events"]) == 1
    assert len(merged["document"]["key_facts"]) == 1


def test_merge_omits_empty_summary_and_analysis():
    merged = merge_extractions([
        {"document": {"sha256": "x"},
         "entities": [{"id": "a", "name": "A", "type": "Person", "aliases": [],
                       "timeline_events": [], "roles": []}]},
    ])
    ent = merged["entities"][0]
    assert "summary" not in ent
    assert "analysis" not in ent


def test_run_merges_section_files(tmp_path):
    vault = tmp_path / "vault"
    tmp = vault / ".watchdog" / "tmp"
    tmp.mkdir(parents=True)
    (tmp / "section_ex_doc1_01.json").write_text(json.dumps({
        "document": {"sha256": "doc1", "filename": "f"},
        "entities": [{"id": "a", "name": "A", "type": "Person", "aliases": [],
                      "timeline_events": [], "roles": []}],
        "morgue_entity_id": "a", "morgue_document_type": "t"}))
    (tmp / "section_ex_doc1_02.json").write_text(json.dumps({
        "document": {},
        "entities": [{"id": "b", "name": "B", "type": "Person", "aliases": [],
                      "timeline_events": [], "roles": []}]}))

    result = merge.run(vault, "doc1")
    assert result["ok"] is True
    assert result["entity_count"] == 2
    assert result["sections_merged"] == 2

    out = vault / result["extraction_path"]
    assert out.exists()
    data = json.loads(out.read_text())
    assert {e["id"] for e in data["entities"]} == {"a", "b"}
    assert data["morgue_entity_id"] == "a"


def test_run_splits_new_vs_updated_against_registry(tmp_path):
    vault = tmp_path / "vault"
    tmp = vault / ".watchdog" / "tmp"
    tmp.mkdir(parents=True)
    reg = vault / ".watchdog" / "Registry"
    reg.mkdir(parents=True)
    reg.joinpath("entities.json").write_text(json.dumps({"a": {"id": "a", "name": "A"}}))

    (tmp / "section_ex_doc1_01.json").write_text(json.dumps({
        "document": {"sha256": "doc1", "filename": "f"},
        "entities": [
            {"id": "a", "name": "A", "type": "Person", "aliases": [], "timeline_events": [], "roles": []},
            {"id": "b", "name": "B", "type": "Person", "aliases": [], "timeline_events": [], "roles": []},
        ],
        "morgue_entity_id": "a", "morgue_document_type": "t"}))

    result = merge.run(vault, "doc1")
    assert result["new_entities"] == {"b": "B"}        # not in registry
    assert result["updated_entities"] == {"a": "A"}    # already in registry


def test_run_no_section_files_errors(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".watchdog" / "tmp").mkdir(parents=True)
    assert "error" in merge.run(vault, "missing")
