import json
from pathlib import Path

from watchdog.pipeline.postflight import _apply_match_ids, _sanitize_dates, _sanitize_entity_ids, explode_key_facts
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


# ── _sanitize_dates (#262: non-ISO dates must not reach timeline.py's filenames) ────────────

def test_sanitize_dates_drops_non_iso_date_and_warns():
    extraction = {"document": {"key_facts": [
        {"fact": "x", "date": "2024/03", "entities": ["a"]},
    ]}}
    warnings = _sanitize_dates(extraction)
    assert extraction["document"]["key_facts"][0]["date"] == ""
    assert len(warnings) == 1
    assert "2024/03" in warnings[0]


def test_sanitize_dates_drops_free_text_date():
    extraction = {"document": {"key_facts": [
        {"fact": "x", "date": "sometime last year", "entities": ["a"]},
    ]}}
    warnings = _sanitize_dates(extraction)
    assert extraction["document"]["key_facts"][0]["date"] == ""
    assert len(warnings) == 1


def test_sanitize_dates_keeps_valid_iso_shapes():
    extraction = {"document": {"key_facts": [
        {"fact": "a", "date": "2021", "entities": []},
        {"fact": "b", "date": "2021-03", "entities": []},
        {"fact": "c", "date": "2021-03-30", "entities": []},
        {"fact": "d", "entities": []},   # no date at all — untouched
    ]}}
    assert _sanitize_dates(extraction) == []
    dates = [f.get("date") for f in extraction["document"]["key_facts"]]
    assert dates == ["2021", "2021-03", "2021-03-30", None]


def test_apply_match_ids_remaps_key_fact_tags():
    extraction = {
        "document": {"key_facts": [{"fact": "x", "entities": ["new-slug", "other"]}]},
        "entities": [{"id": "new-slug", "match_id": "canonical", "name": "N", "type": "Person"}],
    }
    _apply_match_ids(extraction)
    assert extraction["entities"][0]["id"] == "canonical"
    assert extraction["document"]["key_facts"][0]["entities"] == ["canonical", "other"]


# ── _sanitize_entity_ids (#303: path-traversal / vault-escape via unslugified entity id) ────

def test_sanitize_entity_ids_leaves_wellformed_ids_untouched():
    extraction = {"entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company"}]}
    assert _sanitize_entity_ids(extraction) == []
    assert extraction["entities"][0]["id"] == "acme-corp"


def test_sanitize_entity_ids_slugifies_path_traversal_id():
    extraction = {"entities": [{"id": "../../../ESCAPED", "name": "Evil Corp", "type": "Person"}]}
    warnings = _sanitize_entity_ids(extraction)
    assert len(warnings) == 1
    assert extraction["entities"][0]["id"] == "escaped"


def test_sanitize_entity_ids_remaps_key_fact_tags_and_role_targets():
    extraction = {
        "document": {"key_facts": [{"fact": "x", "entities": ["../evil"]}]},
        "entities": [
            {"id": "../evil", "name": "Evil Corp", "type": "Company"},
            {"id": "alice", "name": "Alice", "type": "Person",
             "roles": [{"relationship": "Director of", "target_id": "../evil"}]},
        ],
    }
    _sanitize_entity_ids(extraction)
    new_id = extraction["entities"][0]["id"]
    assert new_id == "evil"
    assert extraction["document"]["key_facts"][0]["entities"] == [new_id]
    assert extraction["entities"][1]["roles"][0]["target_id"] == new_id


def test_sanitize_entity_ids_falls_back_to_name_when_id_slugifies_empty():
    extraction = {"entities": [{"id": "../../..", "name": "Evil Corp", "type": "Person"}]}
    _sanitize_entity_ids(extraction)
    assert extraction["entities"][0]["id"] == "evil-corp"


def test_sanitize_entity_ids_falls_back_to_placeholder_when_name_also_empty():
    extraction = {"entities": [{"id": "../../..", "name": "!!!", "type": "Person"}]}
    _sanitize_entity_ids(extraction)
    assert extraction["entities"][0]["id"] == "entity-1"


def test_sanitize_entity_ids_disambiguates_collision():
    extraction = {"entities": [
        {"id": "acme-corp", "name": "Acme Corp", "type": "Company"},
        {"id": "Acme Corp!!", "name": "Acme Corp", "type": "Company"},
    ]}
    _sanitize_entity_ids(extraction)
    ids = [e["id"] for e in extraction["entities"]]
    assert ids == ["acme-corp", "acme-corp-2"]
    assert len(set(ids)) == 2

def test_sanitize_entity_ids_identical_duplicate_keeps_references_on_first():
    # Two entities emitted with a literally identical id: the second is disambiguated to
    # "acme-corp-2", but references to "acme-corp" must stay on the surviving first entity —
    # not be misrouted to the renamed duplicate.
    extraction = {
        "document": {"key_facts": [{"fact": "x", "entities": ["acme-corp"]}]},
        "entities": [
            {"id": "acme-corp", "name": "Acme Corp", "type": "Company"},
            {"id": "acme-corp", "name": "Acme Corp", "type": "Company",
             "roles": [{"relationship": "Owns", "target_id": "acme-corp"}]},
        ],
    }
    _sanitize_entity_ids(extraction)
    assert [e["id"] for e in extraction["entities"]] == ["acme-corp", "acme-corp-2"]
    assert extraction["document"]["key_facts"][0]["entities"] == ["acme-corp"]
    assert extraction["entities"][1]["roles"][0]["target_id"] == "acme-corp"

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

    lu_note = (vault / "entities" / "organization" / "lu.md").read_text(encoding="utf-8")
    assert "Transfer ratio set at 65.8%." in lu_note       # tagged fact → analysis
    assert "Stayed a $842,018.34 payment." in lu_note
    assert "2021-03-30" in lu_note or "30 Mar 2021" in lu_note   # dated fact → entity timeline

    pbgf_note = (vault / "entities" / "organization" / "pbgf.md").read_text(encoding="utf-8")
    assert "Stayed a $842,018.34 payment." in pbgf_note
    assert "Transfer ratio set at 65.8%." not in pbgf_note  # not tagged to pbgf


def test_postflight_run_drops_malformed_date_before_timeline_write(tmp_path, capsys):
    """A non-ISO-shaped key_facts.date (#262) must not reach timeline.py's
    {date}_{sha7}.ndjson filename construction — it's dropped with a visible warning instead."""
    vault = _full_vault(tmp_path)
    (vault / "_INCOMING" / "doc.pdf").write_text("pdf")
    ext = _extraction()
    ext["document"]["key_facts"][1]["date"] = "2024/03"   # bad shape: contains a slash
    ext_path = vault / ".watchdog" / "tmp" / "wdg_ex_sha777aaa.json"
    ext_path.write_text(json.dumps(ext), encoding="utf-8")

    result = postflight_run(vault, ext_path)
    assert result.get("ok"), result   # doesn't crash the whole extraction

    timeline_dir = vault / ".watchdog" / "timeline"
    ndjson_files = list(timeline_dir.glob("*.ndjson")) if timeline_dir.exists() else []
    assert not any("2024" in f.name for f in ndjson_files)   # no file written for the bad date

    err = capsys.readouterr().err
    assert "Warning" in err and "2024/03" in err


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


# ── Quote verification against the morgue text (#267) ───────────────────────

def test_postflight_flags_unverified_quote_and_warns(tmp_path, capsys):
    vault = _full_vault(tmp_path)
    (vault / "_INCOMING" / "doc.pdf").write_text("pdf")
    (vault / ".watchdog" / "queue" / "sha777aaa.json").write_text(json.dumps({
        "pages": [{"page": 2, "markdown": "Nothing about ratios here."},
                  {"page": 3, "markdown": "Also nothing relevant."}],
    }))
    ext = _extraction()
    ext["document"]["key_facts"][0]["quote"] = "Transfer ratio set at 65.8%."
    ext_path = vault / ".watchdog" / "tmp" / "wdg_ex_sha777aaa.json"
    ext_path.write_text(json.dumps(ext), encoding="utf-8")

    result = postflight_run(vault, ext_path)
    assert result.get("ok"), result

    lu_note = (vault / "entities" / "organization" / "lu.md").read_text(encoding="utf-8")
    assert "*(quote not found on cited page — verify against source)*" in lu_note

    err = capsys.readouterr().err
    assert "Warning" in err and "not found on page" in err


def test_postflight_verifies_exact_quote_without_warning(tmp_path, capsys):
    vault = _full_vault(tmp_path)
    (vault / "_INCOMING" / "doc.pdf").write_text("pdf")
    (vault / ".watchdog" / "queue" / "sha777aaa.json").write_text(json.dumps({
        "pages": [{"page": 2, "markdown": "The transfer ratio set at 65.8%. was confirmed."},
                  {"page": 3, "markdown": "Unrelated page text."}],
    }))
    ext = _extraction()
    ext["document"]["key_facts"][0]["quote"] = "Transfer ratio set at 65.8%."
    ext_path = vault / ".watchdog" / "tmp" / "wdg_ex_sha777aaa.json"
    ext_path.write_text(json.dumps(ext), encoding="utf-8")

    result = postflight_run(vault, ext_path)
    assert result.get("ok"), result

    lu_note = (vault / "entities" / "organization" / "lu.md").read_text(encoding="utf-8")
    assert "quote not found" not in lu_note

    err = capsys.readouterr().err
    assert "not found on page" not in err
