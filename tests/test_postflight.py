import json
from pathlib import Path

from watchdog.pipeline.postflight import _apply_match_ids, explode_key_facts
from watchdog.pipeline.postflight import run as postflight_run


# ── explode_key_facts (the unified-fact fan-out, #140) ──────────────────────

def test_explode_fans_facts_to_tagged_entities():
    extraction = {
        "document": {"key_facts": [
            {"fact": "Paid $842,018.34 to the fund.", "date": "2021-03-30", "page": 3,
             "entities": ["lu", "pbgf"]},
            {"fact": "Transfer ratio set at 65.8%.", "page": 2, "entities": ["lu"]},
            {"fact": "Boilerplate with no entity tag.", "page": 4},
        ]},
        "entities": [
            {"id": "lu", "name": "LU", "type": "Company"},
            {"id": "pbgf", "name": "PBGF", "type": "Fund"},
        ],
    }
    explode_key_facts(extraction)

    lu = next(e for e in extraction["entities"] if e["id"] == "lu")
    pbgf = next(e for e in extraction["entities"] if e["id"] == "pbgf")

    assert {f["claim"] for f in lu["evidence_fragments"]} == {
        "Paid $842,018.34 to the fund.", "Transfer ratio set at 65.8%."}
    assert [f["claim"] for f in pbgf["evidence_fragments"]] == ["Paid $842,018.34 to the fund."]
    # Only the dated fact becomes a timeline event, and on every entity it tags.
    assert [ev["date"] for ev in lu["timeline_events"]] == ["2021-03-30"]
    assert [ev["date"] for ev in pbgf["timeline_events"]] == ["2021-03-30"]
    # The document-level key_facts are left intact (doc note + global timeline read them).
    assert len(extraction["document"]["key_facts"]) == 3


def test_explode_carries_page_and_basis_but_not_to_event_when_undated():
    extraction = {
        "document": {"key_facts": [
            {"fact": "An inferred claim.", "page": 7, "basis": "inferred", "entities": ["a"]},
        ]},
        "entities": [{"id": "a", "name": "A", "type": "Person"}],
    }
    explode_key_facts(extraction)
    frag = extraction["entities"][0]["evidence_fragments"][0]
    assert frag == {"claim": "An inferred claim.", "page": 7, "basis": "inferred"}
    assert "timeline_events" not in extraction["entities"][0]


def test_explode_ignores_unknown_tags():
    extraction = {"document": {"key_facts": [{"fact": "x", "entities": ["ghost"]}]},
                  "entities": [{"id": "real", "name": "R", "type": "Person"}]}
    explode_key_facts(extraction)
    assert "evidence_fragments" not in extraction["entities"][0]


def test_apply_match_ids_remaps_key_fact_tags():
    extraction = {
        "document": {"key_facts": [{"fact": "x", "entities": ["new-slug", "other"]}]},
        "entities": [{"id": "new-slug", "match_id": "canonical", "name": "N", "type": "Person"}],
    }
    _apply_match_ids(extraction)
    assert extraction["entities"][0]["id"] == "canonical"
    assert extraction["document"]["key_facts"][0]["entities"] == ["canonical", "other"]


# ── end-to-end through postflight ───────────────────────────────────────────

def _full_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    reg = vault / ".watchdog" / "Registry"
    reg.mkdir(parents=True)
    (vault / ".watchdog" / "tmp").mkdir()
    (vault / ".watchdog" / "queue").mkdir()
    (vault / "_INCOMING").mkdir()
    (vault / "documents").mkdir()
    (reg / "entities.json").write_text("{}\n")
    (reg / "documents.json").write_text("{}\n")
    (reg / "registry.json").write_text(json.dumps({"document_count": 0, "entity_count": 0}) + "\n")
    (reg / "ingest.log").write_text("")
    return vault


def _extraction(sha="sha777aaa"):
    return {
        "document": {
            "sha256": sha, "filename": "doc.pdf", "original_path": "_INCOMING/doc.pdf",
            "title": "Doc", "document_type": "Order", "page_count": 2,
            "summary": "A court order.",
            "key_facts": [
                {"fact": "Transfer ratio set at 65.8%.", "page": 2, "entities": ["lu"]},
                {"fact": "Stayed a $842,018.34 payment.", "date": "2021-03-30", "page": 3,
                 "entities": ["lu", "pbgf"]},
            ],
        },
        "entities": [
            {"id": "lu", "name": "Laurentian University", "type": "Company", "aliases": [], "roles": []},
            {"id": "pbgf", "name": "PBGF", "type": "Fund", "aliases": [], "roles": []},
        ],
        "morgue_entity_id": "lu", "morgue_document_type": "court-order",
    }


def test_postflight_builds_entity_analysis_from_tagged_facts(tmp_path):
    vault = _full_vault(tmp_path)
    (vault / "_INCOMING" / "doc.pdf").write_text("pdf")
    ext_path = vault / ".watchdog" / "tmp" / "wdg_ex_sha777aaa.json"
    ext_path.write_text(json.dumps(_extraction()), encoding="utf-8")

    result = postflight_run(vault, ext_path)
    assert result.get("ok"), result

    lu_note = (vault / "entities" / "company" / "lu.md").read_text(encoding="utf-8")
    assert "Transfer ratio set at 65.8%." in lu_note       # tagged fact → analysis
    assert "Stayed a $842,018.34 payment." in lu_note
    assert "2021-03-30" in lu_note or "30 Mar 2021" in lu_note   # dated fact → entity timeline

    pbgf_note = (vault / "entities" / "fund" / "pbgf.md").read_text(encoding="utf-8")
    assert "Stayed a $842,018.34 payment." in pbgf_note
    assert "Transfer ratio set at 65.8%." not in pbgf_note  # not tagged to pbgf


def test_postflight_writes_morgue_markdown(tmp_path):
    vault = _full_vault(tmp_path)
    (vault / "_INCOMING" / "doc.pdf").write_text("pdf")
    (vault / ".watchdog" / "queue" / "sha777aaa.json").write_text(json.dumps({
        "pages": [{"page": 1, "markdown": "# Heading one"},
                  {"page": 2, "markdown": "Second page body."}],
    }))
    ext_path = vault / ".watchdog" / "tmp" / "wdg_ex_sha777aaa.json"
    ext_path.write_text(json.dumps(_extraction()), encoding="utf-8")

    assert postflight_run(vault, ext_path).get("ok")

    md_files = list((vault / "morgue").rglob("*.md"))
    assert len(md_files) == 1
    text = md_files[0].read_text(encoding="utf-8")
    assert "<!-- PAGE 1 -->" in text and "<!-- PAGE 2 -->" in text
    assert "# Heading one" in text and "Second page body." in text
