import json

from watchdog.pipeline import merge
from watchdog.pipeline.merge import merge_extractions


def test_merge_unions_entities_by_id():
    sections = [
        {"document": {"sha256": "x", "filename": "f",
                      "key_facts": [{"fact": "Incorporated in Ontario", "date": "2020",
                                     "page": 1, "entities": ["acme"]}]},
         "entities": [{"id": "acme", "name": "Acme Corp", "type": "Company",
                       "aliases": ["the Company"], "roles": []}],
         "morgue_entity_id": "acme", "morgue_document_type": "annual-report"},
        {"document": {"key_facts": [{"fact": "Filed its annual report", "date": "2021",
                                     "page": 60, "entities": ["acme"]}]},
         "entities": [
             {"id": "acme", "name": "Acme Corp", "type": "Company", "aliases": [],
              "roles": [{"relationship": "Director of", "target_id": "jane", "page": 61,
                         "date_range": None}]},
             {"id": "jane", "name": "Jane Doe", "type": "Person", "aliases": [], "roles": []}],
         "morgue_entity_id": None, "morgue_document_type": None},
    ]
    merged = merge_extractions(sections)
    ents = {e["id"]: e for e in merged["entities"]}

    assert set(ents) == {"acme", "jane"}
    assert "the Company" in ents["acme"]["aliases"]
    assert len(ents["acme"]["roles"]) == 1
    assert {f["fact"] for f in merged["document"]["key_facts"]} == {
        "Incorporated in Ontario", "Filed its annual report"}
    assert {f["date"] for f in merged["document"]["key_facts"]} == {"2020", "2021"}
    assert merged["document"]["sha256"] == "x"          # document from first non-empty
    assert merged["morgue_entity_id"] == "acme"          # first non-null wins


def test_merge_folds_id_drift_by_normalized_name():
    sections = [
        {"document": {"sha256": "x", "filename": "f"},
         "entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                       "aliases": [], "roles": []}]},
        {"document": {},
         "entities": [{"id": "acme-corp-2", "name": "ACME  CORP", "type": "Company",
                       "aliases": ["ACME"],
                       "roles": [{"relationship": "Owns", "target_id": "widget"}]}]},
    ]
    merged = merge_extractions(sections)
    assert len(merged["entities"]) == 1                  # drift folded onto one id
    assert merged["entities"][0]["id"] == "acme-corp"
    assert merged["entities"][0]["roles"][0]["target_id"] == "widget"   # roles carried over


def test_merge_dedups_key_facts():
    section = {
        "document": {"sha256": "x", "key_facts": [
            {"fact": "Same fact", "page": 1, "entities": ["a"]},
            {"fact": "Did a thing", "date": "2020", "entities": ["a"]},
        ]},
        "entities": [{"id": "a", "name": "A", "type": "Person", "aliases": [], "roles": []}],
    }
    merged = merge_extractions([section, json.loads(json.dumps(section))])  # identical twice
    assert len(merged["document"]["key_facts"]) == 2


def test_merge_keeps_distinct_key_facts_with_long_shared_opening():
    """Regression guard: dedup keys on the full fact text today, not a truncated prefix. A
    prefix-based key (what this function used before) would treat these two facts, extracted
    from different sections of the same document, as one and silently drop the second — the
    opening clause is identical, but who the property went to (the material fact) differs
    after the shared part."""
    shared_opening = ("On March 3, 2019, Acme Holdings Ltd. transferred beneficial ownership of "
                       "the Cayman Islands shell company, via an intermediary numbered offshore "
                       "company incorporated for this purpose, to ")
    assert len(shared_opening) > 150   # not tied to any number in the source; just long enough

    sections = [
        {"document": {"sha256": "x", "key_facts": [
            {"fact": shared_opening + "John Smith.", "page": 14, "entities": ["a"]},
        ]}, "entities": [{"id": "a", "name": "A", "type": "Person", "aliases": [], "roles": []}]},
        {"document": {"key_facts": [
            {"fact": shared_opening + "Jane Doe's family trust.", "page": 136, "entities": ["a"]},
        ]}, "entities": [{"id": "a", "name": "A", "type": "Person", "aliases": [], "roles": []}]},
    ]
    facts = merge_extractions(sections)["document"]["key_facts"]
    assert len(facts) == 2
    assert any(f["fact"].endswith("John Smith.") for f in facts)
    assert any(f["fact"].endswith("family trust.") for f in facts)


def test_merge_entity_has_no_fact_or_timeline_fields():
    """The merged entity is graph-only; facts/timeline live on document.key_facts (#140)."""
    merged = merge_extractions([
        {"document": {"sha256": "x"},
         "entities": [{"id": "a", "name": "A", "type": "Person", "aliases": [], "roles": []}]},
    ])
    ent = merged["entities"][0]
    assert "summary" not in ent
    assert "evidence_fragments" not in ent
    assert "timeline_events" not in ent
    assert set(ent) >= {"id", "name", "type", "aliases", "roles"}


def test_merge_document_has_no_summary_when_no_section_emits_one():
    """No section emits document.summary any more (#279) — the digest is composed post-merge.
    A merge of sections that don't supply the field must still produce a clean document dict,
    not a stale/empty summary key."""
    sections = [
        {"document": {"sha256": "x", "filename": "f",
                      "key_facts": [{"fact": "Filed in 2024", "entities": ["a"]}]},
         "entities": [{"id": "a", "name": "A", "type": "Person", "aliases": [], "roles": []}],
         "morgue_entity_id": "a", "morgue_document_type": "annual-report"},
        {"document": {"key_facts": [{"fact": "Revenue grew", "entities": ["a"]}]},
         "entities": [{"id": "a", "name": "A", "type": "Person", "aliases": [], "roles": []}]},
    ]
    merged = merge_extractions(sections)
    assert "summary" not in merged["document"]


def test_merge_unions_key_fact_entity_tags_across_sections():
    """The same fact tagged with different entities in two sections folds, unioning the tags."""
    sections = [
        {"document": {"sha256": "x", "filename": "f",
                      "key_facts": [{"fact": "Shared an address.", "entities": ["a"]}]},
         "entities": [{"id": "a", "name": "A", "type": "Person", "aliases": [], "roles": []}]},
        {"document": {"key_facts": [{"fact": "Shared an address.", "entities": ["b"]}]},
         "entities": [{"id": "b", "name": "B", "type": "Person", "aliases": [], "roles": []}]},
    ]
    facts = merge_extractions(sections)["document"]["key_facts"]
    assert len(facts) == 1
    assert sorted(facts[0]["entities"]) == ["a", "b"]


def test_run_merges_section_files(tmp_path):
    vault = tmp_path / "vault"
    tmp = vault / ".watchdog" / "tmp"
    tmp.mkdir(parents=True)
    (tmp / "section_ex_doc1_01.json").write_text(json.dumps({
        "document": {"sha256": "doc1", "filename": "f"},
        "entities": [{"id": "a", "name": "A", "type": "Person", "aliases": [], "roles": []}],
        "morgue_entity_id": "a", "morgue_document_type": "t"}))
    (tmp / "section_ex_doc1_02.json").write_text(json.dumps({
        "document": {},
        "entities": [{"id": "b", "name": "B", "type": "Person", "aliases": [], "roles": []}]}))

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
            {"id": "a", "name": "A", "type": "Person", "aliases": [], "roles": []},
            {"id": "b", "name": "B", "type": "Person", "aliases": [], "roles": []},
        ],
        "morgue_entity_id": "a", "morgue_document_type": "t"}))

    result = merge.run(vault, "doc1")
    assert result["new_entities"] == {"b": "B"}        # not in registry
    assert result["updated_entities"] == {"a": "A"}    # already in registry


def test_run_no_section_files_errors(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".watchdog" / "tmp").mkdir(parents=True)
    assert "error" in merge.run(vault, "missing")
