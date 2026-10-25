"""The content-free diagnostics report (`watchdog.ops.diagnostics`, D331): counts and shapes only,
built from an allowlist, with nothing a reporter's material says anywhere in it."""

import json

import pytest

from watchdog.ops import CollectingReporter, diagnostics
from watchdog.ops.diagnostics import diagnostics_report, warning_shape

pytest_plugins = ["tests.gui_support"]   # the wdg_home and rich_vault fixtures

# Distinctive strings planted wherever a user's material can live. None may reach the report.
FILE_A, FILE_B = "Quillfeather-ledger-scan.pdf", "Brindlemoor minutes final.docx"
TITLE = "Brindlemoor Harbour Authority Board Minutes"
ENT_A, ENT_B, ENT_C = "Ottoline Vandermeer", "Thornbury Quay Holdings", "Marisol Quillfeather"
ALIAS = "Tolly Vandermeer-Quince"
FACT = "Vandermeer sold the quayside warehouse for 7,310,442 dollars"
QUOTE = "sold the quayside warehouse to Thornbury"
REL = "silent partner in the harbour dredging consortium"
REQUEST = "Sealed tender bids for the Brindlemoor dredging contract"
REQ_WHY = "Would show who underbid Thornbury"
REQ_SOURCE = "Harbour Authority registry office"
SUMMARY = "A narrow majority approved the dredging award despite objections"
ID_NUMBER = "REG-8841-QX"
KEY_LABEL = "Newsroom desk key Orchid"
PATH = "/Users/ottoline/Investigations/Brindlemoor"
REPORTER = "Reporter Zephyrine Okafor-Lindqvist"
URL = "https://models.brindlemoor.example/v1"
CUSTOM_MODEL = "local/quillfeather-13b-custom"
NEW_FIELD = "value-of-a-field-added-in-some-later-version"
PLANTS = [FILE_A, FILE_B, TITLE, ENT_A, ENT_B, ENT_C, ALIAS, FACT, QUOTE, REL, REQUEST, REQ_WHY,
          REQ_SOURCE, SUMMARY, ID_NUMBER, KEY_LABEL, PATH, REPORTER, URL, CUSTOM_MODEL, NEW_FIELD,
          "Quillfeather", "Brindlemoor", "Vandermeer", "Thornbury", "Ottoline", "7,310,442",
          "Orchid", "Zephyrine", "dredging"]

SHA_A, SHA_B = "a" * 64, "b" * 64


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


@pytest.fixture
def planted(wdg_home, rich_vault):
    """The rich vault with its registry replaced by one that holds the planted strings in every
    field a user's content can reach, plus a field the report has never heard of."""
    v = rich_vault
    reg = v / ".watchdog" / "registry"

    def doc(sha, filename, pages):
        return {"sha256": sha, "filename": filename, "title": TITLE, "original_path": f"incoming/{filename}",
                "document_note": f"documents/{ID_NUMBER.lower()}", "ingested_at": "2026-03-01T10:30:00Z",
                "page_count": pages, "document_type": REL, "record_skill": "general-records",
                "extract_model": CUSTOM_MODEL, "extract_effort": NEW_FIELD,
                "file_metadata": {"author": ENT_A}, "coverage_gap": None,
                "entities_extracted": ["e-a", "e-b"], "near_duplicate_of": None, "minhash": [],
                "morgue_path": f"morgue/{ENT_A}/x.pdf", "zz_new_field": NEW_FIELD}

    def ent(eid, name, typ, **kw):
        return {"id": eid, "name": name, "type": typ, "aliases": [ALIAS], "appears_in": [SHA_A, SHA_B],
                "note_path": f"entities/{typ}/{eid}",
                "roles": [{"relationship": REL, "target_id": "e-b", "target_name": ENT_B,
                           "target_type": "organization", "page": 2, "basis": "stated",
                           "date_range": "2019-2023", "source_sha256": SHA_A, "is_reverse": False}],
                "timeline_events": [{"date": "2019-03-04", "event": FACT, "page": 2, "basis": "stated",
                                     "source_sha256": SHA_A}],
                "contradictions": [f"> [!contradiction] {FACT}\n> {QUOTE}"],
                "synthesis": {"summary": SUMMARY, "analysis": FACT}, "zz_new_field": NEW_FIELD, **kw}

    _write(reg / "documents.json", {SHA_A: doc(SHA_A, FILE_A, 12), SHA_B: doc(SHA_B, FILE_B, 150)})
    _write(reg / "entities.json", {
        "e-a": ent("e-a", ENT_A, "person"), "e-b": ent("e-b", ENT_B, "organization"),
        "e-c": ent("e-c", ENT_B, "organization"), "e-d": ent("e-d", ENT_B.upper(), "organization"),
        "e-e": ent("e-e", ENT_C, "weird-" + NEW_FIELD)})
    _write(reg / "merges.json", {
        "schema_version": 1,
        "merges": [{"id": "merge:1", "keep": {"id": "e-b", "name": ENT_B, "type": "organization"},
                    "merged": {"id": "e-c", "name": ENT_B, "type": "organization"}, "tier": NEW_FIELD,
                    "decided_by": "rule", "rule": ID_NUMBER, "reason": FACT, "same_name": True,
                    "occurrences": [{"document": FILE_A}], "zz_new_field": NEW_FIELD}],
        "candidates": {"same:1": {"id": "same:1", "tier": "medium", "rule": "same-name",
                                  "status": "open", "reason": QUOTE, "model_declined": True,
                                  "a": {"id": "e-b", "name": ENT_B, "aliases": [ALIAS]},
                                  "b": {"id": "e-d", "name": ENT_B.upper()}, "evidence": {"x": FACT}}},
        "carry": {"ids": ["e-b", "e-c", "e-d"], "at": "2026-03-01T00:00:00Z"}})
    _write(reg / "requests.json", {"schema_version": 2, "requests": {
        "request:1": {"type": REL, "what": REQUEST, "why_it_matters": REQ_WHY, "likely_source": REQ_SOURCE,
                      "sources": [{"sha256": SHA_A, "filename": FILE_A, "document_note": TITLE}],
                      "added": "2026-03-01T00:00:00", "zz_new_field": NEW_FIELD}}})
    for f in (reg / "usage").glob("*"):
        f.unlink()
    _write(reg / "usage" / "usage-20260301T000000Z.json", {"calls": [
        {"task": "extract", "model": CUSTOM_MODEL, "backend": URL, "input_tokens": 1000, "output_tokens": 50,
         "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 0.5, "attempts": 3, "latency_s": 12.5,
         "effort": NEW_FIELD, "auth_mode": "api-key", "filename": FILE_A, "detail": FACT,
         "key_label": KEY_LABEL, "end_ts": 100.0, "failed": True, "stop_reason": "max_tokens",
         "pruned": [NEW_FIELD], "zz_new_field": NEW_FIELD},
        {"task": REL, "model": "claude-sonnet-5-5", "backend": "claude-api", "input_tokens": 10,
         "output_tokens": 5, "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 0.01,
         "attempts": 1, "latency_s": 1.0, "effort": "high", "end_ts": 120.0}]})
    # Extractions: facts, quotes and a figure, one of them a quote that was not found.
    _write(v / ".watchdog" / "extracted" / f"{SHA_A}.json", {"document": {
        "filename": FILE_A, "title": TITLE, "summary": SUMMARY,
        "key_facts": [{"fact": FACT, "page": 2, "basis": "stated", "date": "2019-03-04", "quote": QUOTE,
                       "entities": ["e-a"]},
                      {"fact": FACT, "basis": NEW_FIELD, "date": "the fourth of " + ID_NUMBER,
                       "quote": QUOTE, "quote_verified": False, "figures_unverified": ["7310442"]}]}})
    # The log: warnings that name files, quote values, spell an entity bare and carry figures.
    (reg / "processing.log").write_text(
        f"[2026-03-01T10:00:00Z] START {FILE_A}\n"
        f"[2026-03-01T10:01:00Z] WARN {FILE_A}: document.key_facts[3].date '4th of {ID_NUMBER}' is not recognizable\n"
        f"[2026-03-01T10:01:30Z] WARN {FILE_B}: document.key_facts[11].date '9th of {ID_NUMBER}' is not recognizable\n"
        f"[2026-03-01T10:02:00Z] WARN {FILE_A}: quote \"{QUOTE}\" not found on page 4 for {ENT_A} and {ALIAS}\n"
        f"[2026-03-01T10:03:00Z] WARN {TITLE}: 2 of 9 entities not synthesized\n"
        f"[2026-03-01T10:04:00Z] FAILED {FILE_B}: model returned invalid JSON ({FACT})\n"
        f"[2026-03-01T10:05:00Z] WARN unlabelled message naming {ENT_C} and `{REQUEST}` and {FILE_A}\n"
        f"[2026-03-01T10:06:00Z] MERGED merge:1 into {ENT_B}\n"
        f"[2026-03-01T10:07:00Z] CONTRADICTION e-a: {FACT}\n"
        f"[2026-03-01T10:08:00Z] OK {FILE_A}: 12p, 2 entities\n", encoding="utf-8")
    # Settings: a reporter's name, a web address, a folder, a custom model and an unknown key.
    (wdg_home / "config.json").write_text(json.dumps({
        "reporter_name": REPORTER, "local_base_url": URL, "projects_dir": PATH,
        "extractor_model": CUSTOM_MODEL, "classifier_model": "haiku", "chew_workers": 3,
        "ocr_engine": "tesseract", "unknown_key": PATH, "wayback_secret_key": "sk-secret"}))
    # Free text in files the report must not read at all, and a note whose name says what it is.
    _write(v / "entities" / "person" / f"{ENT_A.lower().replace(' ', '-')}.md", FACT)
    return v


def test_no_planted_string_reaches_the_report(planted):
    report = diagnostics.build(planted)
    for blob in (diagnostics.render(report), json.dumps(report, ensure_ascii=False)):
        low = blob.lower()
        for plant in PLANTS:
            assert plant.lower() not in low, plant
        assert "sk-secret" not in low


def test_the_report_still_says_something_useful(planted):
    """The planted vault must not pass by producing an empty report."""
    r = diagnostics.build(planted)
    assert r["documents"]["total"] == 2 and r["documents"]["by_file_type"] == {"docx": 1, "pdf": 1}
    assert r["documents"]["by_page_count"]["6-20"] == 1 and r["documents"]["by_page_count"]["101+"] == 1
    assert r["facts"]["total"] == 2 and r["facts"]["quote_not_found"] == 1
    assert r["facts"]["by_file_type"]["pdf"] == {"facts": 2, "with_quote": 2, "quote_not_found": 1}
    assert r["facts"]["date_shape"]["day"] == 1 and r["facts"]["date_shape"]["other"] == 1
    # Three records named the same, in one type (the fourth differs only in case, so it joins them).
    e = r["entities"]
    assert e["records"] == 5 and e["same_name_groups"] == 1 and e["largest_same_name_group"] == 3
    assert e["same_name_group_sizes"]["3"] == 1 and e["by_type"] == {"organization": 3, "other": 1, "person": 1}
    m = r["merges"]
    assert m["merges"] == 1 and m["by_rule"] == {"other": 1} and m["records_carried_to_the_next_run"] == 3
    assert m["pairs_the_model_declined"] == 1
    assert r["requests"]["total"] == 1 and r["requests"]["open"] == 1
    t = r["runs"]["by_task"]["extract"]
    assert t["calls"] == 1 and t["failed"] == 1 and t["retried"] == 1 and t["extra_attempts"] == 2
    assert t["stopped_at_output_limit"] == 1 and t["models"] == {"other": 1} and t["cost_usd"] == 0.5
    assert set(r["runs"]["by_task"]) == {"extract", "other"}
    s = r["settings"]
    assert s["classifier_model"] == "haiku" and s["extractor_model"] == "custom"
    assert s["chew_workers"] == 3 and s["ocr_engine"] == "tesseract" and s["reporter_name"] == "set"
    assert "projects_dir" not in s and "unknown_key" not in s and "wayback_secret_key" not in s


def test_a_new_registry_field_cannot_leak(planted):
    """The allowlist is what is read: a field the report does not name is never copied, however
    many records carry it, and a known field holding an unknown value is counted as "other"."""
    reg = planted / ".watchdog" / "registry"
    for name in ("entities.json", "documents.json"):
        data = json.loads((reg / name).read_text())
        for rec in data.values():
            rec["brand_new_field"] = "FRESHLY-ADDED-SECRET"
            rec["synthesis"] = {"summary": "FRESHLY-ADDED-SECRET", "brand_new": "FRESHLY-ADDED-SECRET"}
        (reg / name).write_text(json.dumps(data))
    merges = json.loads((reg / "merges.json").read_text())
    merges["merges"][0]["brand_new_field"] = "FRESHLY-ADDED-SECRET"
    merges["brand_new_section"] = {"x": "FRESHLY-ADDED-SECRET"}
    (reg / "merges.json").write_text(json.dumps(merges))
    usage = next((reg / "usage").glob("*.json"))
    u = json.loads(usage.read_text())
    u["calls"][0]["brand_new_field"] = "FRESHLY-ADDED-SECRET"
    u["calls"][0]["task"] = "FRESHLY-ADDED-SECRET"
    usage.write_text(json.dumps(u))
    after = diagnostics.render(diagnostics.build(planted))
    assert "freshly-added-secret" not in after.lower()
    assert "brand" not in after.lower()
    assert "FRESHLY" not in json.dumps(diagnostics.build(planted))


def test_a_malformed_registry_does_not_break_the_report(wdg_home, rich_vault):
    reg = rich_vault / ".watchdog" / "registry"
    (reg / "entities.json").write_text("[1, 2")
    (reg / "documents.json").write_text(json.dumps({"x": "not a record", "y": {"page_count": "many"}}))
    (reg / "merges.json").write_text("null")
    (reg / "requests.json").write_text("{")
    text = diagnostics.text(rich_vault)
    assert "## Entities" in text and "## Processing log" in text


@pytest.mark.parametrize("line, shape", [
    ("[2026-03-01T10:01:00Z] WARN report-one.pdf: document.key_facts[3].date '4th of Smarch' is not recognizable",
     "WARN document.key_facts.date is not recognizable"),
    ("[2026-03-01T10:01:00Z] WARN some file.pdf: no facts cite pages 3–5 (2 of 9)", "WARN no facts cite pages N–N"),
    ("[2026-03-01T10:01:00Z] WARN a.pdf: tags entity id \"jane-doe\", which is not in entities",
     "WARN tags entity id , which is not in entities"),
    ("[2026-03-01T10:01:00Z] WARN a.pdf: dropped 12 of 40 facts at 0xdeadbeef01", "WARN dropped N of N facts at N"),
    ("[2026-03-01T10:01:00Z] FAILED broken.pdf: model returned invalid JSON", "FAILED model returned invalid JSON"),
    ("[2026-03-01T10:01:00Z] WARN a.pdf: broken \"quote never closed and more text", "WARN broken"),
    ("[2026-03-01T10:01:00Z] WARN not labelled but names scan.tiff here", "WARN not labelled but names <file> here"),
    ("[2026-03-01T10:01:00Z] START report-one.pdf", ""),
])
def test_warning_shape(line, shape):
    assert warning_shape(line) == shape


def test_warnings_group_by_shape(planted):
    log = diagnostics.build(planted)["processing_log"]
    shapes = {s["shape"]: s["count"] for s in log["warning_shapes"]}
    assert shapes["WARN document.key_facts.date is not recognizable"] == 2   # two files, one kind
    assert log["by_kind"]["WARN"] == 5 and log["by_kind"]["FAILED"] == 1 and log["by_kind"]["MERGED"] == 1
    assert log["lines"] == 10


def test_the_operation_writes_one_markdown_file_named_without_the_investigation(planted, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    rep = CollectingReporter()
    result = diagnostics_report(rep, planted, output=str(out))
    files = list(out.iterdir())
    assert [f.name for f in files] == [result["output"].rsplit("/", 1)[1]]
    name = files[0].name
    assert name.startswith("watchdog-diagnostics-") and name.endswith(".md")
    assert planted.name.lower() not in name.lower()
    body = files[0].read_text(encoding="utf-8")
    assert body.startswith("# Watchdog diagnostics report")
    assert result["bytes"] == len(body.encode("utf-8")) and "Read it through" in rep.text
    again = diagnostics_report(CollectingReporter(), planted, output=str(out))
    assert again["output"] != result["output"] and not list(out.glob("*.part"))
    with pytest.raises(SystemExit, match="does not exist"):
        diagnostics_report(CollectingReporter(), planted, output=str(tmp_path / "nope"))


def test_report_rpc(planted):
    from tests.gui_support import call
    text = call("vault.diagnosticsReport", vault=str(planted))["text"]
    assert text.startswith("# Watchdog diagnostics report")
    assert not any(p.lower() in text.lower() for p in PLANTS)
