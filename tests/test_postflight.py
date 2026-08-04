import json
from pathlib import Path

from watchdog.pipeline import postflight
from watchdog.pipeline.postflight import (
    _find_coverage_gap,
    _render_coverage_warning,
    _sanitize_dates,
    _sanitize_entity_ids,
    _validate,
    explode_key_facts,
)
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


def test_explode_propagates_resolved_quote_fields_to_fragments():
    """#529: `resolve_quotes` runs before `explode_key_facts` and annotates the key_fact with
    `quote`/`quote_verified`/`quote_found_page` — the fan-out must copy all three onto the
    fanned-out evidence fragment, not just `quote`."""
    extraction = {
        "document": {"key_facts": [
            {"fact": "x", "page": 3, "entities": ["a"],
             "quote": "The resolved sentence.", "quote_found_page": 4},
        ]},
        "entities": [{"id": "a", "name": "A", "type": "Person"}],
    }
    explode_key_facts(extraction)
    frag = extraction["entities"][0]["evidence_fragments"][0]
    assert frag["quote"] == "The resolved sentence."
    assert frag["quote_found_page"] == 4
    assert "quote_verified" not in frag   # only propagated when explicitly False


def test_explode_propagates_quote_verified_false_to_fragments():
    extraction = {
        "document": {"key_facts": [
            {"fact": "x", "page": 3, "entities": ["a"],
             "quote": "the locator", "quote_verified": False},
        ]},
        "entities": [{"id": "a", "name": "A", "type": "Person"}],
    }
    explode_key_facts(extraction)
    frag = extraction["entities"][0]["evidence_fragments"][0]
    assert frag["quote_verified"] is False


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

# ── _find_coverage_gap / _render_coverage_warning (#339 gap detector) ───────────────────────

def _ext_with_fact_pages(pages):
    return {"document": {"key_facts": [{"fact": "f", "page": p} for p in pages]}}


def test_coverage_gap_flags_front_loaded_extraction():
    # 36-page doc, facts only on pages 1-4 → the trailing 32-page uncited run (89%) → flagged
    gap = _find_coverage_gap(_ext_with_fact_pages([1, 2, 3, 4]), 36)
    assert gap == {"start": 5, "end": 36, "pages": 32}
    warn = _render_coverage_warning(gap, 36)
    assert "may have skipped" in warn and "of 36 pages" in warn and "pages 5–36" in warn


def test_coverage_gap_flags_interior_gap():
    # 50-page doc cited at both ends but with a 29-page hole in the middle (58%) — the old
    # tail-only rule passed this clean; the gap rule is the point of #339.
    gap = _find_coverage_gap(_ext_with_fact_pages([1, 4, 7, 10, 40, 45, 50]), 50)
    assert gap == {"start": 11, "end": 39, "pages": 29}
    warn = _render_coverage_warning(gap, 50)
    assert "pages 11–39" in warn and "29 of 50 pages" in warn


def test_coverage_gap_flags_leading_gap():
    # Facts only in the back half: the *leading* 44-page run is the flagged span.
    gap = _find_coverage_gap(_ext_with_fact_pages([45, 48, 50]), 50)
    assert gap == {"start": 1, "end": 44, "pages": 44}
    assert "pages 1–44" in _render_coverage_warning(gap, 50)


def test_coverage_gap_ignores_out_of_range_citations():
    # A fabricated page 999 must not mask the real uncited tail (or create negative gaps).
    gap = _find_coverage_gap(_ext_with_fact_pages([1, 2, 999]), 40)
    assert gap == {"start": 3, "end": 40, "pages": 38}
    assert "pages 3–40" in _render_coverage_warning(gap, 40)


def test_coverage_gap_silent_when_well_covered():
    # Largest uncited run is 14 pages (6–19) of 36 — under the 40% gap threshold → no gap
    assert _find_coverage_gap(_ext_with_fact_pages([1, 5, 20, 30]), 36) is None


def test_coverage_gap_skips_short_docs():
    assert _find_coverage_gap(_ext_with_fact_pages([1]), 5) is None


def test_coverage_gap_skips_when_no_page_anchors():
    ext = {"document": {"key_facts": [{"fact": "f"}, {"fact": "g", "page": None}]}}
    assert _find_coverage_gap(ext, 40) is None


def test_coverage_gap_handles_missing_page_count():
    assert _find_coverage_gap(_ext_with_fact_pages([1, 2]), None) is None


# ── empty-extraction guard (#507/#510: sonnet-high silently produced zero key_facts on a
#    17-page court order — billed, no error, no coverage-gap flag, since the gap detector
#    itself needs at least one citation to work). Gated on actual source-text word count
#    (page_texts), not nominal page_count — a page count is a poor proxy for how much there
#    was to extract from (an exhibit-heavy filing can be long but nearly blank). ─────────────

def _minimal_valid_doc(key_facts=None):
    return {
        "document": {
            "sha256": "sha1", "filename": "doc.pdf", "page_count": 17,
            "key_facts": key_facts if key_facts is not None else [],
        },
        "entities": [{"id": "acme", "name": "Acme", "type": "organization"}],
        "morgue_entity_id": "acme", "morgue_document_type": "court-order",
    }


def _page_texts(word_count, n_pages=1):
    """`word_count` words of filler text, spread evenly across `n_pages`."""
    words_per_page = word_count // n_pages
    return {i + 1: " ".join(["word"] * words_per_page) for i in range(n_pages)}


def test_validate_rejects_empty_key_facts_with_substantial_source_text():
    errors = _validate(_minimal_valid_doc(key_facts=[]), _page_texts(5000))
    assert any("key_facts is empty" in e and "5000 words" in e for e in errors)


def test_validate_allows_empty_key_facts_with_thin_source_text():
    # A short cover letter or signature page can legitimately have nothing to extract, even if
    # the model happens to report a nominally long page_count (e.g. a mostly-blank exhibit set).
    assert _validate(_minimal_valid_doc(key_facts=[]), _page_texts(100)) == []


def test_validate_allows_nonempty_key_facts_regardless_of_source_text_volume():
    facts = [{"fact": "Something happened."}]
    assert _validate(_minimal_valid_doc(key_facts=facts), _page_texts(5000)) == []


def test_validate_skips_empty_check_when_no_page_texts_available():
    # No queue file / page_texts (e.g. it went missing) must not itself trigger the check —
    # absence of source text to measure is not evidence of a failed extraction.
    assert _validate(_minimal_valid_doc(key_facts=[])) == []


def test_validate_empty_check_honours_configured_threshold(monkeypatch):
    # `empty_extraction_min_words` (D153): a lower configured threshold catches a shorter
    # document than the 500-word default would.
    monkeypatch.setattr(postflight, "_config_get", lambda k, d: 100)
    errors = _validate(_minimal_valid_doc(key_facts=[]), _page_texts(150))
    assert any("key_facts is empty" in e for e in errors)


def test_validate_empty_check_honours_raised_threshold(monkeypatch):
    # A raised configured threshold exempts a document the 500-word default would have caught.
    monkeypatch.setattr(postflight, "_config_get", lambda k, d: 10_000)
    assert _validate(_minimal_valid_doc(key_facts=[]), _page_texts(5000)) == []


# ── end-to-end through postflight ───────────────────────────────────────────
#
# Post-flight no longer writes to the vault (#403 phase 1) — it validates, sanitizes, explodes,
# and stages the result to `.watchdog/extracted/<sha>.json`. These tests read that staged
# artifact directly rather than a vault note; the commit pass that replays `write_vault` over it
# (and the vault notes that produces) is covered in tests/test_orchestrate.py.

def _staged(vault: Path, sha: str) -> dict:
    return json.loads((vault / ".watchdog" / "extracted" / f"{sha}.json").read_text(encoding="utf-8"))


def _full_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    reg = vault / ".watchdog" / "registry"
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
    """The staged artifact — not a vault note (#403 phase 1: post-flight no longer writes to the
    vault) — carries each entity's fanned-out evidence_fragments/timeline_events."""
    vault = _full_vault(tmp_path)
    (vault / "_INCOMING" / "doc.pdf").write_text("pdf")
    ext_path = vault / ".watchdog" / "tmp" / "wdg_ex_sha777aaa.json"
    ext_path.write_text(json.dumps(_extraction()), encoding="utf-8")

    result = postflight_run(vault, ext_path)
    assert result.get("ok"), result

    staged = _staged(vault, "sha777aaa")
    lu = next(e for e in staged["entities"] if e["id"] == "lu")
    pbgf = next(e for e in staged["entities"] if e["id"] == "pbgf")

    lu_claims = {f["claim"] for f in lu["evidence_fragments"]}
    assert lu_claims == {"Transfer ratio set at 65.8%.", "Stayed a $842,018.34 payment."}
    assert [ev["date"] for ev in lu["timeline_events"]] == ["2021-03-30"]

    pbgf_claims = {f["claim"] for f in pbgf["evidence_fragments"]}
    assert pbgf_claims == {"Stayed a $842,018.34 payment."}   # not tagged to pbgf


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


def test_postflight_does_not_write_morgue_and_leaves_queue_file_in_place(tmp_path):
    """Post-flight no longer calls write_vault (#403 phase 1): the morgue directory is not
    created and the queue file (needed at commit time by write_vault._write_morgue_markdown and
    the corpus indexer) is left untouched. See tests/test_orchestrate.py for the commit pass
    actually writing the morgue markdown from this same queue file."""
    vault = _full_vault(tmp_path)
    (vault / "_INCOMING" / "doc.pdf").write_text("pdf")
    queue_file = vault / ".watchdog" / "queue" / "sha777aaa.json"
    queue_file.write_text(json.dumps({
        "pages": [{"page": 1, "markdown": "# Heading one"},
                  {"page": 2, "markdown": "Second page body."}],
    }))
    ext_path = vault / ".watchdog" / "tmp" / "wdg_ex_sha777aaa.json"
    ext_path.write_text(json.dumps(_extraction()), encoding="utf-8")

    assert postflight_run(vault, ext_path).get("ok")

    assert not (vault / "morgue").exists()
    assert queue_file.exists()
    assert (vault / ".watchdog" / "extracted" / "sha777aaa.json").exists()


def test_postflight_run_rejects_and_does_not_stage_empty_extraction_on_substantive_document(tmp_path):
    """#507/#510 end-to-end: a document with substantial source text but zero key_facts must be
    rejected by postflight (feeding the caller's repair-retry loop), not silently staged as 'ok'."""
    vault = _full_vault(tmp_path)
    (vault / "_INCOMING" / "doc.pdf").write_text("pdf")
    (vault / ".watchdog" / "queue" / "sha777aaa.json").write_text(json.dumps({
        "pages": [{"page": p, "markdown": " ".join(["word"] * 500)} for p in range(1, 18)],
    }))
    ext = _extraction()
    ext["document"]["page_count"] = 17
    ext["document"]["key_facts"] = []
    ext_path = vault / ".watchdog" / "tmp" / "wdg_ex_sha777aaa.json"
    ext_path.write_text(json.dumps(ext), encoding="utf-8")

    result = postflight_run(vault, ext_path)
    assert "errors" in result
    assert any("key_facts is empty" in e for e in result["errors"])
    assert not (vault / ".watchdog" / "extracted" / "sha777aaa.json").exists()


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

    lu = next(e for e in _staged(vault, "sha777aaa")["entities"] if e["id"] == "lu")
    frag = next(f for f in lu["evidence_fragments"] if f["claim"] == "Transfer ratio set at 65.8%.")
    assert frag["quote_verified"] is False

    err = capsys.readouterr().err
    assert "Warning" in err and "not found on page" in err


def test_postflight_verifies_exact_quote_without_warning(tmp_path, capsys):
    vault = _full_vault(tmp_path)
    (vault / "_INCOMING" / "doc.pdf").write_text("pdf")
    (vault / ".watchdog" / "queue" / "sha777aaa.json").write_text(json.dumps({
        "pages": [{"page": 2, "markdown": "The transfer ratio set at 65.8%. was confirmed."},
                  {"page": 3, "markdown": "A $842,018.34 payment was stayed."}],
    }))
    ext = _extraction()
    ext["document"]["key_facts"][0]["quote"] = "Transfer ratio set at 65.8%."
    ext_path = vault / ".watchdog" / "tmp" / "wdg_ex_sha777aaa.json"
    ext_path.write_text(json.dumps(ext), encoding="utf-8")

    result = postflight_run(vault, ext_path)
    assert result.get("ok"), result

    lu = next(e for e in _staged(vault, "sha777aaa")["entities"] if e["id"] == "lu")
    frag = next(f for f in lu["evidence_fragments"] if f["claim"] == "Transfer ratio set at 65.8%.")
    assert frag.get("quote_verified") is not False

    err = capsys.readouterr().err
    assert "not found on page" not in err


# ── Quote locator resolution (#529) ──────────────────────────────────────────

def test_postflight_resolves_quote_locator_into_full_quote(tmp_path, capsys):
    """End-to-end: a `quote_locator` on a key_fact, plus the chew-time queue descriptor's page
    text, produces a resolved `quote` on the staged extraction."""
    vault = _full_vault(tmp_path)
    (vault / "_INCOMING" / "doc.pdf").write_text("pdf")
    (vault / ".watchdog" / "queue" / "sha777aaa.json").write_text(json.dumps({
        "pages": [{"page": 2, "markdown": "The transfer ratio set at 65.8% was confirmed by the board."},
                  {"page": 3, "markdown": "A $842,018.34 payment was stayed."}],
    }))
    ext = _extraction()   # key_facts[0] is already page 2 ("Transfer ratio set at 65.8%.")
    ext["document"]["key_facts"][0]["quote_locator"] = "The transfer ratio set at"
    ext_path = vault / ".watchdog" / "tmp" / "wdg_ex_sha777aaa.json"
    ext_path.write_text(json.dumps(ext), encoding="utf-8")

    result = postflight_run(vault, ext_path)
    assert result.get("ok"), result

    staged = _staged(vault, "sha777aaa")
    fact = staged["document"]["key_facts"][0]
    assert fact["quote"] == "The transfer ratio set at 65.8% was confirmed by the board."
    assert fact["quote_locator"] == "The transfer ratio set at"   # left in place, not consumed

    err = capsys.readouterr().err
    assert "not found on page" not in err


# ── Deterministic date-mismatch check (#369) ─────────────────────────────────

def test_postflight_warns_on_file_metadata_date_mismatch(tmp_path, capsys):
    vault = _full_vault(tmp_path)
    (vault / "_INCOMING" / "doc.pdf").write_text("pdf")
    (vault / ".watchdog" / "queue" / "sha777aaa.json").write_text(json.dumps({
        "pages": [], "metadata": {"ocr_used": False, "source_type": "direct_text"},
    }))
    ext = _extraction()
    ext["document"]["date_of_document"] = "2019-01-01"
    ext["document"]["file_metadata"] = {"created": "2023-06-01T00:00:00"}
    ext_path = vault / ".watchdog" / "tmp" / "wdg_ex_sha777aaa.json"
    ext_path.write_text(json.dumps(ext), encoding="utf-8")

    result = postflight_run(vault, ext_path)
    assert result.get("ok"), result

    err = capsys.readouterr().err
    assert "Warning" in err and "postdates" in err


def test_postflight_silent_on_date_mismatch_when_ocr_used(tmp_path, capsys):
    """The single most important behaviour: a scanned document's embedded creation date
    describes the scan, not the original, so the check must stay silent — otherwise every
    scanned exhibit in the vault produces a false lead."""
    vault = _full_vault(tmp_path)
    (vault / "_INCOMING" / "doc.pdf").write_text("pdf")
    (vault / ".watchdog" / "queue" / "sha777aaa.json").write_text(json.dumps({
        "pages": [], "metadata": {"ocr_used": True, "source_type": "docling"},
    }))
    ext = _extraction()
    ext["document"]["date_of_document"] = "2019-01-01"
    ext["document"]["file_metadata"] = {"created": "2023-06-01T00:00:00"}
    ext_path = vault / ".watchdog" / "tmp" / "wdg_ex_sha777aaa.json"
    ext_path.write_text(json.dumps(ext), encoding="utf-8")

    result = postflight_run(vault, ext_path)
    assert result.get("ok"), result

    err = capsys.readouterr().err
    assert "postdates" not in err


# ── coverage_gap persisted on the document registry record (#339 skip-telemetry) ────────────

def _gappy_extraction(sha="sha777aaa"):
    """A 12-page doc with a fact only on page 1 — an 11-page uncited tail (92%) flags a gap."""
    ext = _extraction(sha)
    ext["document"]["page_count"] = 12
    ext["document"]["key_facts"] = [{"fact": "Filed.", "page": 1, "entities": ["lu"]}]
    return ext


def test_postflight_persists_coverage_gap_on_document_registry_record(tmp_path):
    """`coverage_gap` is written onto the staged artifact's document record — write_vault (at
    commit time, #403 phase 1) carries it into `documents.json` unchanged, since it just persists
    whatever `document` dict it's handed."""
    vault = _full_vault(tmp_path)
    (vault / "_INCOMING" / "doc.pdf").write_text("pdf")
    ext_path = vault / ".watchdog" / "tmp" / "wdg_ex_sha777aaa.json"
    ext_path.write_text(json.dumps(_gappy_extraction()), encoding="utf-8")

    result = postflight_run(vault, ext_path)
    assert result.get("ok"), result

    staged = _staged(vault, "sha777aaa")
    assert staged["document"]["coverage_gap"] == {"start": 2, "end": 12, "pages": 11}


def test_postflight_persists_none_coverage_gap_for_clean_extraction(tmp_path):
    vault = _full_vault(tmp_path)
    (vault / "_INCOMING" / "doc.pdf").write_text("pdf")
    ext_path = vault / ".watchdog" / "tmp" / "wdg_ex_sha777aaa.json"
    # The default fixture's page_count (2) is under the 8-page minimum — never assessable as a gap.
    ext_path.write_text(json.dumps(_extraction()), encoding="utf-8")

    result = postflight_run(vault, ext_path)
    assert result.get("ok"), result

    doc = _staged(vault, "sha777aaa")["document"]
    assert "coverage_gap" in doc   # key present even when assessed clean/not-assessable
    assert doc["coverage_gap"] is None


def test_postflight_coverage_gap_warning_reaches_warn_callback(tmp_path):
    vault = _full_vault(tmp_path)
    (vault / "_INCOMING" / "doc.pdf").write_text("pdf")
    ext_path = vault / ".watchdog" / "tmp" / "wdg_ex_sha777aaa.json"
    ext_path.write_text(json.dumps(_gappy_extraction()), encoding="utf-8")

    warnings = []
    result = postflight_run(vault, ext_path, warn=warnings.append)
    assert result.get("ok"), result
    assert any("pages 2–12" in w and "may have skipped" in w for w in warnings)


# ── Figure verification against the morgue text (#363) ──────────────────────

def test_postflight_flags_invented_figure_and_warns(tmp_path, capsys):
    vault = _full_vault(tmp_path)
    (vault / "_INCOMING" / "doc.pdf").write_text("pdf")
    (vault / ".watchdog" / "queue" / "sha777aaa.json").write_text(json.dumps({
        "pages": [{"page": 2, "markdown": "The transfer ratio was set at 65.8%."},
                  {"page": 3, "markdown": "Transfers of $250,000 and $180,000 were recorded."}],
    }))
    ext = _extraction()
    ext["document"]["key_facts"][1]["fact"] = "$430,000 across two transfers."
    ext_path = vault / ".watchdog" / "tmp" / "wdg_ex_sha777aaa.json"
    ext_path.write_text(json.dumps(ext), encoding="utf-8")

    result = postflight_run(vault, ext_path)
    assert result.get("ok"), result

    err = capsys.readouterr().err
    assert "Warning" in err and "430000" in err and "not found on page" in err


def test_postflight_does_not_flag_inferred_figure(tmp_path, capsys):
    vault = _full_vault(tmp_path)
    (vault / "_INCOMING" / "doc.pdf").write_text("pdf")
    (vault / ".watchdog" / "queue" / "sha777aaa.json").write_text(json.dumps({
        "pages": [{"page": 2, "markdown": "The transfer ratio was set at 65.8%."},
                  {"page": 3, "markdown": "Nothing about payments here."}],
    }))
    ext = _extraction()
    ext["document"]["key_facts"][1]["fact"] = "$430,000 across two transfers."
    ext["document"]["key_facts"][1]["basis"] = "inferred"
    ext_path = vault / ".watchdog" / "tmp" / "wdg_ex_sha777aaa.json"
    ext_path.write_text(json.dumps(ext), encoding="utf-8")

    result = postflight_run(vault, ext_path)
    assert result.get("ok"), result

    err = capsys.readouterr().err
    assert "not found on page" not in err
