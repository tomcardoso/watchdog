"""Integration test for the Python orchestrator: the per-document flow runs through
the REAL preflight/postflight/write_vault with the model mocked."""

import asyncio
import json

import pytest

from watchdog import model_client
from watchdog.cmd import auth as auth_module
from watchdog.pipeline import batch_extract, orchestrate

from tests.test_write_vault import make_vault

_flat = model_client._flatten_prompt   # extract/section prompts are content-block lists (A1)


def _queue_doc(vault, sha="abc123", filename="test-doc.pdf", text="Acme Corp filed an annual report."):
    qdir = vault / ".watchdog" / "queue"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / f"{sha}.json").write_text(json.dumps({
        "sha256": sha, "filename": filename, "source_path": f"_INCOMING/{filename}",
        "page_count": 1, "pages": [{"page": 1, "markdown": text}],
        "near_dup": {"near_duplicates": [], "top_similarity": 0.0},
    }))
    (vault / "_INCOMING" / filename).write_text("dummy source bytes")


def _extraction(sha="abc123", filename="test-doc.pdf", *, valid=True):
    ext = {
        "document": {
            "sha256": sha, "filename": filename, "original_path": f"_INCOMING/{filename}",
            "title": "Acme Annual Report", "document_type": "Annual Report",
            "date_of_document": "2024-01-15", "page_count": 1, "source": None, "obtained": None,
            "near_duplicate_of": None, "summary": "Acme's annual report.",
            "key_facts": [{"fact": "Filed in 2024", "page": 1, "basis": "stated"}],
        },
        "entities": [{
            "id": "acme-corp", "name": "Acme Corp", "type": "Company", "aliases": [],
            "summary": "A company that filed an annual report.", "timeline_events": [], "roles": [],
        }],
        "morgue_entity_id": "acme-corp", "morgue_document_type": "annual-report",
        "scratchpad": "# notes\n- filed 2024",
    }
    if not valid:
        del ext["morgue_entity_id"]   # post-flight rejects this
    return ext


def _mock(monkeypatch, *, extraction):
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        parsed = {
            "classify": {"skill": "general-records.md"},
            "extract": extraction,
            "entity-synthesis": {"entity_syntheses": []},
            "timeline-dedup": {"groups": []},
            "briefing": {"investigation_status": "Early days.",
                         "what_was_ingested": ["test-doc.pdf — Annual Report"],
                         "new_entities": ["Acme Corp"]},
        }.get(task, extraction)
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                         backend="claude-agent-sdk", auth_mode="subscription", cost_usd=0.01)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)


def _ext_with_fact_pages(pages):
    return {"document": {"key_facts": [{"fact": "f", "page": p} for p in pages]}}


def test_coverage_warning_flags_front_loaded_extraction():
    # 36-page doc, facts only on pages 1-4 → nothing past page 4 (< 18) → warn
    warn = orchestrate._coverage_warning(_ext_with_fact_pages([1, 2, 3, 4]), 36)
    assert warn and "may have stopped reading early" in warn and "of 36" in warn
    assert "check pages 5–36" in warn        # actionable range = first uncited page → end


def test_coverage_warning_silent_when_well_covered():
    # facts reach page 30 of 36 (>= 18) → no warning
    assert orchestrate._coverage_warning(_ext_with_fact_pages([1, 5, 20, 30]), 36) is None


def test_coverage_warning_skips_short_docs():
    assert orchestrate._coverage_warning(_ext_with_fact_pages([1]), 5) is None


def test_coverage_warning_skips_when_no_page_anchors():
    ext = {"document": {"key_facts": [{"fact": "f"}, {"fact": "g", "page": None}]}}
    assert orchestrate._coverage_warning(ext, 40) is None


def test_coverage_warning_handles_missing_page_count():
    assert orchestrate._coverage_warning(_ext_with_fact_pages([1, 2]), None) is None


def test_briefing_facts_projects_fact_and_date_only():
    """The briefing projection (#150) keeps the fact text and a date when present, and drops
    page/basis/entities/quote — narrative noise the briefing doesn't need."""
    doc = {"key_facts": [
        {"fact": "Filed in 2024", "page": 3, "basis": "stated", "entities": ["acme"]},
        {"fact": "Order issued", "date": "2024-01-15", "quote": "It is ordered…", "page": 1},
    ]}
    assert orchestrate._briefing_facts(doc) == [
        {"fact": "Filed in 2024"},
        {"fact": "Order issued", "date": "2024-01-15"},
    ]


def test_briefing_facts_empty_when_no_key_facts():
    assert orchestrate._briefing_facts({}) == []


def test_compact_result_carries_key_facts_for_the_briefing():
    """key_facts ride along on the compact result so the briefing (and a standalone finalize,
    which reads only result_*.json) can draw figures + chronology from them (#150)."""
    extraction = {
        "document": {"document_type": "Annual Report", "date_of_document": "2024-01-15",
                     "key_facts": [{"fact": "Revenue was $5M", "page": 2, "basis": "stated"},
                                   {"fact": "Merger closed", "date": "2024-03-01"}]},
        "entities": [{"id": "acme", "name": "Acme", "type": "Company"}],
    }
    r = orchestrate._compact_result("sha1", "doc.pdf", extraction, {}, 0.01)
    assert r["key_facts"] == [{"fact": "Revenue was $5M"},
                              {"fact": "Merger closed", "date": "2024-03-01"}]


def test_select_kept_keeps_survivors_in_original_order():
    """timeline-dedup returns `groups`; Python re-selects the authoritative originals (which carry
    source_sha256/page/basis), order-preserving, dropping each group's folded duplicates."""
    events = [
        {"event": "A", "source_sha256": "sha-a", "page": 1},
        {"event": "B", "source_sha256": "sha-b", "page": 2},
        {"event": "C", "source_sha256": "sha-c", "page": 3},
    ]
    kept = orchestrate._select_kept(
        events, [{"keep": 0, "duplicates": [1]}, {"keep": 2, "duplicates": []}])
    assert [e["event"] for e in kept] == ["A", "C"]
    assert kept[0]["source_sha256"] == "sha-a"   # authoritative original carried through


def test_select_kept_unions_entity_tags_of_dropped_duplicates():
    """A collapsed group's survivor carries the union of its own and its dropped duplicates'
    entity_ids (#237) — attribution survives regardless of which restatement the model kept.
    The originals are left unmutated."""
    events = [
        {"event": "Acme filed", "entity_ids": ["acme"]},
        {"event": "Acme filed for bankruptcy", "entity_ids": ["acme", "alice"]},
    ]
    kept = orchestrate._select_kept(events, [{"keep": 0, "duplicates": [1]}])
    assert len(kept) == 1
    assert kept[0]["entity_ids"] == ["acme", "alice"]   # unioned, order-preserving, deduped
    assert events[0]["entity_ids"] == ["acme"]          # original untouched


def test_select_kept_preserves_events_the_model_never_placed():
    """Dedup must never lose events: an index the model omits from every group stays kept."""
    events = [{"event": "A"}, {"event": "B"}, {"event": "C"}]
    kept = orchestrate._select_kept(events, [{"keep": 0, "duplicates": []}])
    assert [e["event"] for e in kept] == ["A", "B", "C"]


def test_select_kept_falls_back_to_all_on_bad_input():
    events = [{"event": "A"}, {"event": "B"}]
    assert orchestrate._select_kept(events, None) == events   # missing/non-list → keep all
    assert orchestrate._select_kept(events, []) == events     # no groups → nothing placed, keep all
    # an out-of-range keep skips the whole group, so its members stay unplaced and are kept
    assert orchestrate._select_kept(events, [{"keep": 9, "duplicates": [0]}]) == events


def test_stamp_document_overwrites_model_identity(tmp_path):
    """Identity fields are stamped from Python, overriding whatever the model emitted."""
    vault = make_vault(tmp_path)
    pf = {"filename": "real.pdf", "original_path": "_INCOMING/real.pdf",
          "page_count": 7, "pages": [{}]}
    ext = {"document": {"sha256": "WRONGSHA", "filename": "wrong.pdf", "page_count": 999}}
    orchestrate._stamp_document(ext, sha="realsha", pf=pf, skill_label="court-documents", vault=vault)
    d = ext["document"]
    assert d["sha256"] == "realsha"
    assert d["filename"] == "real.pdf"
    assert d["original_path"] == "_INCOMING/real.pdf"
    assert d["page_count"] == 7
    assert d["record_skill"] == "court-documents"


def test_stamp_document_derives_morgue_type_from_document_type(tmp_path):
    """morgue_document_type is slugify(document_type), derived in Python — the model's value
    (if any) is overridden."""
    vault = make_vault(tmp_path)
    pf = {"filename": "f.pdf", "original_path": None, "page_count": 1, "pages": [{}]}
    ext = {"document": {"document_type": "CCAA Initial Order"}, "morgue_document_type": "WRONG"}
    orchestrate._stamp_document(ext, sha="s", pf=pf, skill_label="court-documents", vault=vault)
    assert ext["morgue_document_type"] == "ccaa-initial-order"


def test_stamp_document_morgue_type_falls_back_when_no_type(tmp_path):
    vault = make_vault(tmp_path)
    pf = {"filename": "f.pdf", "original_path": None, "page_count": 1, "pages": [{}]}
    ext = {"document": {}}
    orchestrate._stamp_document(ext, sha="s", pf=pf, skill_label="general-records", vault=vault)
    assert ext["morgue_document_type"] == "document"


def test_sidecar_provenance_parsed_in_python(tmp_path):
    """source/obtained come from the .yml sidecar (parsed in Python), not the model — and an
    unquoted ISO date is coerced back to a string rather than a date object."""
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "doc.pdf.yml").write_text(
        "source: https://sedar.com/x\nobtained: 2026-06-05\nnotes: check p.12\n", encoding="utf-8")
    assert orchestrate._sidecar_provenance(vault, "doc.pdf") == {
        "source": "https://sedar.com/x", "obtained": "2026-06-05"}


def test_sidecar_provenance_absent_or_malformed(tmp_path):
    vault = make_vault(tmp_path)
    assert orchestrate._sidecar_provenance(vault, "missing.pdf") == {}
    (vault / "_INCOMING" / "bad.pdf.yml").write_text("just a string, not a map\n", encoding="utf-8")
    assert orchestrate._sidecar_provenance(vault, "bad.pdf") == {}


def test_stamp_document_applies_sidecar_provenance(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "real.pdf.yml").write_text(
        "source: FOI A-2026-001\nobtained: 2026-06-05\n", encoding="utf-8")
    pf = {"filename": "real.pdf", "original_path": "_INCOMING/real.pdf", "page_count": 1, "pages": [{}]}
    ext = {"document": {}}   # model emitted no source/obtained
    orchestrate._stamp_document(ext, sha="s", pf=pf, skill_label="foi-responses", vault=vault)
    assert ext["document"]["source"] == "FOI A-2026-001"
    assert ext["document"]["obtained"] == "2026-06-05"


def test_orchestrator_extracts_and_writes_vault(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    _mock(monkeypatch, extraction=_extraction())

    summary = asyncio.run(orchestrate.run(vault))

    assert summary["extracted"] == 1 and summary["failed"] == 0
    # real write_vault produced the notes
    assert (vault / "entities" / "company" / "acme-corp.md").exists()
    assert list((vault / "documents").glob("*.md"))
    # housekeeping: queue file consumed; post-ingest finalized and cleaned its per-run inputs
    # (the scratchpad is consumed by the briefing, then removed on a clean finalize)
    assert not (vault / ".watchdog" / "queue" / "abc123.json").exists()
    assert "post_ingest" in summary
    assert not (vault / ".watchdog" / "tmp" / "notes_abc123.md").exists()
    # compact result block
    r = summary["results"][0]
    assert r["status"] == "ok" and r["entity_count"] == 1
    assert r["new_entities"] == ["acme-corp"] and r["document_type"] == "Annual Report"

    # post-ingest ran: briefing + hot.md + log.md + timeline written
    assert "post_ingest" in summary
    assert list((vault / "briefings").glob("*.md"))
    assert (vault / "hot.md").exists()
    assert "— Ingest" in (vault / "log.md").read_text()
    assert (vault / "timeline.md").exists()


def test_orchestrator_reports_failed_on_postflight_rejection(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    _mock(monkeypatch, extraction=_extraction(valid=False))   # missing morgue_entity_id

    summary = asyncio.run(orchestrate.run(vault))

    assert summary["failed"] == 1 and summary["extracted"] == 0
    assert summary["results"][0]["status"] == "failed"
    assert "post-flight rejected" in summary["results"][0]["reason"]
    # abort cleanup: queue file moved to _failed/ (preserved, not auto-retried), failure logged
    assert not (vault / ".watchdog" / "queue" / "abc123.json").exists()
    assert (vault / ".watchdog" / "queue" / "_failed" / "abc123.json").exists()
    assert "FAILED" in (vault / ".watchdog" / "Registry" / "ingest.log").read_text()


def test_orchestrator_empty_queue(tmp_path):
    vault = make_vault(tmp_path)
    summary = asyncio.run(orchestrate.run(vault))
    assert summary == {"results": [], "extracted": 0, "skipped": 0, "failed": 0}


def test_orchestrator_threads_configured_models(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    seen = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen.append((task, model))
        parsed = {
            "classify": {"skill": "general-records.md"},
            "extract": _extraction(),
            "entity-synthesis": {"entity_syntheses": []},
            "timeline-dedup": {"groups": []},
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, _extraction())
        return model_client.ModelResult(parsed=parsed, text="", model=model or "?",
                                        backend="b", auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    asyncio.run(orchestrate.run(vault, extract_model="opus", post_model="haiku", classify_model="haiku"))
    by_task = dict(seen)
    assert by_task["classify"] == "haiku"
    assert by_task["extract"] == "opus"
    assert by_task["briefing"] == "haiku"


def test_orchestrator_threads_configured_efforts(tmp_path, monkeypatch):
    """extract_effort / post_effort reach the right stages; classify gets no effort (D34)."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    seen = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen.append((task, effort))
        parsed = {
            "classify": {"skill": "general-records.md"},
            "extract": _extraction(),
            "entity-synthesis": {"entity_syntheses": []},
            "timeline-dedup": {"groups": []},
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, _extraction())
        return model_client.ModelResult(parsed=parsed, text="", model=model or "?",
                                        backend="b", auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    asyncio.run(orchestrate.run(vault, extract_effort="low", post_effort="medium"))
    by_task = dict(seen)
    assert by_task["classify"] is None      # classify never gets an effort
    assert by_task["extract"] == "low"
    assert by_task["briefing"] == "medium"


def test_orchestrator_threads_configured_backends(tmp_path, monkeypatch):
    """extract_backend / post_backend / classify_backend reach the right stages (D37)."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    seen = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen.append((task, backend))
        parsed = {
            "classify": {"skill": "general-records.md"},
            "extract": _extraction(),
            "entity-synthesis": {"entity_syntheses": []},
            "timeline-dedup": {"groups": []},
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, _extraction())
        return model_client.ModelResult(parsed=parsed, text="", model=model or "?",
                                        backend=backend or "b", auth_mode="api-key", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    asyncio.run(orchestrate.run(vault, extract_backend="deepseek", post_backend="openai",
                                classify_backend="claude-api"))
    by_task = dict(seen)
    assert by_task["classify"] == "claude-api"
    assert by_task["extract"] == "deepseek"
    assert by_task["briefing"] == "openai"


def test_orchestrator_updates_graph_colours(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian" / "graph.json").write_text(json.dumps({"colorGroups": []}))
    _mock(monkeypatch, extraction=_extraction())

    asyncio.run(orchestrate.run(vault))

    graph = json.loads((vault / ".obsidian" / "graph.json").read_text())
    queries = [g["query"] for g in graph["colorGroups"]]
    assert "path:entities/company" in queries     # Acme Corp → entities/company/


def test_classifier_sees_only_first_n_pages(tmp_path, monkeypatch):
    """classify_pages bounds the classifier excerpt to the first N pages (page-aware)."""
    vault = make_vault(tmp_path)
    qdir = vault / ".watchdog" / "queue"
    qdir.mkdir(parents=True, exist_ok=True)
    pages = [{"page": i, "markdown": f"distinctword{i}"} for i in (1, 2, 3)]
    (qdir / "abc123.json").write_text(json.dumps({
        "sha256": "abc123", "filename": "test-doc.pdf", "source_path": "_INCOMING/test-doc.pdf",
        "page_count": 3, "pages": pages,
        "near_dup": {"near_duplicates": [], "top_similarity": 0.0},
    }))
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")

    seen = {}
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            seen["prompt"] = prompt
        parsed = {
            "classify": {"skill": "general-records.md"},
            "extract": _extraction(),
            "entity-synthesis": {"entity_syntheses": []},
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, {"events": []})
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    asyncio.run(orchestrate.run(vault, classify_pages=2))
    assert "distinctword1" in seen["prompt"] and "distinctword2" in seen["prompt"]
    assert "distinctword3" not in seen["prompt"]   # page 3 excluded


def test_whole_doc_failure_falls_back_to_sectioning(tmp_path, monkeypatch):
    """A multi-page doc whose whole-doc extraction is rejected is re-extracted in sections."""
    vault = make_vault(tmp_path)
    monkeypatch.setattr(orchestrate.section, "_config_get", lambda k, d: d)   # deterministic defaults
    qdir = vault / ".watchdog" / "queue"
    qdir.mkdir(parents=True, exist_ok=True)
    pages = [{"page": 1, "markdown": "Acme part one " * 50},
             {"page": 2, "markdown": "Acme part two " * 50}]
    (qdir / "abc123.json").write_text(json.dumps({
        "sha256": "abc123", "filename": "test-doc.pdf", "source_path": "_INCOMING/test-doc.pdf",
        "page_count": 2, "pages": pages,
        "near_dup": {"near_duplicates": [], "top_similarity": 0.0},
    }))
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")

    calls = {"extract": 0, "section": 0}
    sec_first = {
        "document": {"sha256": "abc123", "filename": "test-doc.pdf", "title": "Acme AR",
                     "document_type": "Annual Report", "summary": "Acme report.",
                     "key_facts": [{"fact": "x", "basis": "stated"}]},
        "entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                      "timeline_events": [], "roles": []}],
        "morgue_entity_id": "acme-corp", "morgue_document_type": "annual-report",
        "observations": "sec1",
    }
    sec_later = {"entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                              "timeline_events": [], "roles": []}], "observations": "sec2"}

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            parsed = {"skill": "general-records.md"}
        elif task == "extract":
            calls["extract"] += 1
            parsed = _extraction(valid=False)                 # whole-doc → postflight rejects
        elif task == "extract-section":
            calls["section"] += 1
            parsed = sec_first if "This is SECTION 1" in _flat(prompt) else sec_later
        elif task == "briefing":
            parsed = {"investigation_status": "x", "what_was_ingested": []}
        else:
            parsed = {"entity_syntheses": []} if task == "entity-synthesis" else {"events": []}
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.01)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault))
    assert calls["extract"] >= 1 and calls["section"] >= 2     # whole-doc tried, then sectioned
    assert summary["extracted"] == 1 and summary["failed"] == 0
    assert (vault / "entities" / "company" / "acme-corp.md").exists()


def test_single_page_failure_does_not_section(tmp_path, monkeypatch):
    """A 1-page doc can't be split, so a rejection just fails (no fallback loop)."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)                                          # single page
    _mock(monkeypatch, extraction=_extraction(valid=False))
    summary = asyncio.run(orchestrate.run(vault))
    assert summary["failed"] == 1 and summary["extracted"] == 0


def test_pinned_skill_skips_classification(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    tasks = []
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        tasks.append(task)
        parsed = {
            "extract": _extraction(),
            "entity-synthesis": {"entity_syntheses": []},
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, {"events": []})
        return model_client.ModelResult(parsed=parsed, text="", model="m", backend="b",
                                        auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("PINNED SKILL BODY")
    summary = asyncio.run(orchestrate.run(vault, pinned_skill=str(skill_file)))
    assert summary["extracted"] == 1
    assert "classify" not in tasks          # classification skipped entirely


def test_pinned_skill_is_injected_into_extraction(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    skill_file = tmp_path / "corporate-filings.md"
    skill_file.write_text("CORPORATE FILINGS SKILL BODY")
    seen = {}
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "extract":
            seen["prompt"] = prompt
        parsed = {
            "extract": _extraction(),
            "entity-synthesis": {"entity_syntheses": []},
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, {"events": []})
        return model_client.ModelResult(parsed=parsed, text="", model="m", backend="b",
                                        auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    asyncio.run(orchestrate.run(vault, pinned_skill=str(skill_file)))
    assert "CORPORATE FILINGS SKILL BODY" in _flat(seen["prompt"])


def test_record_skill_provenance_is_persisted(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    _mock(monkeypatch, extraction=_extraction())     # classify mock returns general-records.md
    asyncio.run(orchestrate.run(vault))

    docs = json.loads((vault / ".watchdog" / "Registry" / "documents.json").read_text())
    assert next(iter(docs.values()))["record_skill"] == "general-records"
    note = next((vault / "documents").glob("*.md")).read_text(encoding="utf-8")
    assert "record_skill: general-records" in note


def test_nudge_skill_pin_fires_when_batch_is_homogeneous(capsys):
    orchestrate._nudge_skill_pin([
        {"status": "ok", "record_skill": "general-records"},
        {"status": "ok", "record_skill": "general-records"},
    ])
    assert "watchdog ingest --skill general-records" in capsys.readouterr().out


def test_nudge_skill_pin_silent_when_mixed_or_single_or_failed(capsys):
    orchestrate._nudge_skill_pin([
        {"status": "ok", "record_skill": "general-records"},
        {"status": "ok", "record_skill": "court-documents"},
    ])
    assert capsys.readouterr().out == ""                       # mixed skills

    orchestrate._nudge_skill_pin([{"status": "ok", "record_skill": "general-records"}])
    assert capsys.readouterr().out == ""                       # only one document

    orchestrate._nudge_skill_pin([
        {"status": "ok", "record_skill": "general-records"},
        {"status": "failed", "record_skill": None},
    ])
    assert capsys.readouterr().out == ""                       # only one succeeded


def test_skill_pin_nudge_silent_when_run_was_pinned(tmp_path, monkeypatch, capsys):
    """The nudge only makes sense when classification ran at all."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="aaa", filename="a.pdf")
    _queue_doc(vault, sha="bbb", filename="b.pdf")
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        # _stamp_document overwrites sha256/filename from the queue entry regardless of what the
        # mocked extraction returns, so both docs can safely share one fixture body here.
        parsed = {
            "extract": _extraction(sha="aaa", filename="a.pdf"),
            "entity-synthesis": {"entity_syntheses": []},
            "timeline-dedup": {"groups": []},
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, {})
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)
    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("PINNED SKILL BODY")

    asyncio.run(orchestrate.run(vault, concurrency=2, pinned_skill=str(skill_file)))
    assert "watchdog ingest --skill" not in capsys.readouterr().out


def test_usage_telemetry_persisted_after_ingest(tmp_path, monkeypatch):
    """A2: every model call's usage is accumulated and written to a per-run usage file, with
    totals surfaced on the run summary — `ModelResult.usage` was previously discarded."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        parsed = {
            "classify": {"skill": "general-records.md"},
            "extract": _extraction(),
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, {"entity_syntheses": []} if task == "entity-synthesis" else {"groups": []})
        return model_client.ModelResult(
            parsed=parsed, text="", model="claude-sonnet-4-6", backend="claude-api",
            auth_mode="api-key", cost_usd=0.01, usage={"input_tokens": 100, "output_tokens": 20})
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault))
    assert summary["extracted"] == 1

    usage_path = summary["usage_path"]
    assert usage_path and (vault / usage_path).exists()
    data = json.loads((vault / usage_path).read_text())
    tasks = [c["task"] for c in data["calls"]]
    assert "classify" in tasks and "extract" in tasks and "briefing" in tasks
    assert all(c["input_tokens"] == 100 for c in data["calls"])

    n_calls = len(data["calls"])
    assert data["totals"]["input_tokens"] == 100 * n_calls
    assert data["totals"]["output_tokens"] == 20 * n_calls
    assert summary["usage"]["input_tokens"] == 100 * n_calls
    assert round(summary["usage"]["cost_usd"], 4) == round(0.01 * n_calls, 4)


def test_log_md_ingest_entry_includes_usage_line(tmp_path, monkeypatch):
    """F5/#222: the log.md entry for an ingest carries the run's token/cost totals, the
    user-facing half of A2's telemetry."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        parsed = {
            "classify": {"skill": "general-records.md"},
            "extract": _extraction(),
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, {"entity_syntheses": []} if task == "entity-synthesis" else {"groups": []})
        return model_client.ModelResult(
            parsed=parsed, text="", model="claude-sonnet-4-6", backend="claude-api",
            auth_mode="api-key", cost_usd=0.01, usage={"input_tokens": 100, "output_tokens": 20})
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    asyncio.run(orchestrate.run(vault))
    log = (vault / "log.md").read_text()
    assert "**Usage:**" in log
    assert "in /" in log and "out tokens" in log
    assert "$0.0" in log   # non-zero cost rendered


def test_latest_usage_none_before_any_ingest(tmp_path):
    vault = make_vault(tmp_path)
    assert orchestrate.latest_usage(vault) is None


def test_latest_usage_returns_the_most_recent_run(tmp_path):
    vault = make_vault(tmp_path)
    reg = vault / ".watchdog" / "Registry"
    (reg / "usage-20260101T000000Z.json").write_text(
        json.dumps({"calls": [], "totals": {"input_tokens": 1, "output_tokens": 1,
                                            "cache_read_tokens": 0, "cache_write_tokens": 0,
                                            "cost_usd": 0.001}}))
    (reg / "usage-20260102T000000Z.json").write_text(
        json.dumps({"calls": [], "totals": {"input_tokens": 999, "output_tokens": 999,
                                            "cache_read_tokens": 0, "cache_write_tokens": 0,
                                            "cost_usd": 0.05}}))
    totals = orchestrate.latest_usage(vault)
    assert totals["input_tokens"] == 999


def test_orchestrator_cancels_gracefully_on_sigint(tmp_path, monkeypatch):
    """Ctrl+C during extraction → cancelled summary, no traceback, unfinished docs keep
    their queue file, and post-ingest is skipped."""
    import os
    import signal

    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="aaa111", filename="one.pdf")
    _queue_doc(vault, sha="bbb222", filename="two.pdf")

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "extract":
            # Simulate the user pressing Ctrl+C mid-extraction. The loop's SIGINT
            # handler cancels the in-flight tasks; the sleep below is interrupted.
            os.kill(os.getpid(), signal.SIGINT)
            await asyncio.sleep(5)
        parsed = {"classify": {"skill": "general-records.md"}}.get(task, _extraction())
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="claude-agent-sdk", auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault, concurrency=2))

    assert summary["cancelled"] is True
    assert summary["extracted"] == 0
    assert "post_ingest" not in summary                      # post-ingest skipped on cancel
    # both queue files survive for a clean resume
    assert (vault / ".watchdog" / "queue" / "aaa111.json").exists()
    assert (vault / ".watchdog" / "queue" / "bbb222.json").exists()


def test_rate_limit_stops_batch_keeps_queue(tmp_path, monkeypatch):
    """A provider rate limit stops the batch cleanly: the summary carries the reason,
    nothing is quarantined, and every queue file is kept for resume."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="aaa111", filename="one.pdf")
    _queue_doc(vault, sha="bbb222", filename="two.pdf")

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            return model_client.ModelResult(parsed={"skill": "general-records.md"}, text="",
                                            model="m", backend="claude-agent-sdk", auth_mode="subscription")
        raise model_client.RateLimitError("You've hit your session limit · resets 6:10pm")
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault, concurrency=2))

    assert summary["rate_limited"] is True
    assert "session limit" in summary["stop_message"]
    assert summary["extracted"] == 0
    assert summary["quarantined"] == 0
    assert "post_ingest" not in summary                      # skipped when the batch stops
    # neither doc is quarantined; both stay queued for a clean resume
    assert {p.name for p in (vault / ".watchdog" / "queue").glob("*.json")} == {"aaa111.json", "bbb222.json"}


def test_failed_doc_is_named_and_quarantine_surfaced(tmp_path, monkeypatch):
    """A genuine (non-rate-limit) error names the file rather than a bare sha, quarantines
    it to _failed/, and the summary reports the quarantined count."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="ccc333", filename="boom.pdf")

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            return model_client.ModelResult(parsed={"skill": "general-records.md"}, text="",
                                            model="m", backend="claude-agent-sdk", auth_mode="subscription")
        raise RuntimeError("kaboom")
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault, concurrency=1))

    assert summary["failed"] == 1
    assert summary["quarantined"] == 1
    failed = next(r for r in summary["results"] if r["status"] == "failed")
    assert failed["filename"] == "boom.pdf"                   # resolved, not a bare sha
    assert (vault / ".watchdog" / "queue" / "_failed" / "ccc333.json").exists()
    assert not (vault / ".watchdog" / "queue" / "ccc333.json").exists()


def test_post_ingest_model_failure_degrades_without_crashing(tmp_path, monkeypatch):
    """A rate limit (or model error) during post-ingest must not crash: the extracted docs
    are already saved, synthesis is skipped, and the run returns a summary cleanly."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="aaa", filename="a.pdf", text="Acme Corp filed.")
    _queue_doc(vault, sha="bbb", filename="b.pdf", text="Acme Corp again.")

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            return model_client.ModelResult(parsed={"skill": "general-records.md"}, text="",
                                            model="m", backend="claude-agent-sdk", auth_mode="subscription")
        if task == "extract":
            sha = "aaa" if "Acme Corp filed." in prompt else "bbb"   # share an entity across both docs
            return model_client.ModelResult(parsed=_extraction(sha=sha, filename=f"{sha}.pdf"),
                                            text="", model="m", backend="claude-agent-sdk", auth_mode="subscription")
        raise model_client.RateLimitError("You've hit your session limit · resets 7pm")
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault, concurrency=2))   # must not raise

    assert summary["extracted"] == 2
    assert summary["post_ingest"]["synthesized"] == 0
    assert "error" in summary["post_ingest"]                       # synthesis degraded, recorded


def test_post_ingest_unexpected_crash_is_contained(tmp_path, monkeypatch):
    """An unforeseen error in post-ingest is caught at the batch level — the saved
    extraction is reported and the CLI does not crash."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="aaa", filename="a.pdf")
    _mock(monkeypatch, extraction=_extraction())

    def boom(*a, **k):
        raise RuntimeError("kaboom in post-ingest")
    monkeypatch.setattr(orchestrate.synthesis_bundle, "build_bundle", boom)

    summary = asyncio.run(orchestrate.run(vault, concurrency=1))   # must not raise

    assert summary["extracted"] == 1
    assert "post_ingest_error" in summary


def test_finalize_completes_an_interrupted_run(tmp_path, monkeypatch):
    """A rate limit during post-ingest leaves the batch finalizable; a later finalize
    completes synthesis + briefing and clears the per-run inputs."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="aaa", filename="a.pdf", text="Acme Corp filed.")
    _queue_doc(vault, sha="bbb", filename="b.pdf", text="Acme Corp again.")
    state = {"synthesis_ok": False}

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        def res(parsed):
            return model_client.ModelResult(parsed=parsed, text="", model="m",
                                            backend="claude-agent-sdk", auth_mode="subscription")
        if task == "classify":
            return res({"skill": "general-records.md"})
        if task == "extract":
            sha = "aaa" if "Acme Corp filed." in prompt else "bbb"   # share one entity across both docs
            return res(_extraction(sha=sha, filename=f"{sha}.pdf"))
        if task == "entity-synthesis":
            if not state["synthesis_ok"]:
                raise model_client.RateLimitError("You've hit your session limit · resets 7pm")
            return res({"entity_syntheses": [{"entity_id": "acme-corp", "summary": "Synthesized prose.", "analysis": ""}]})
        if task == "timeline-dedup":
            return res({"groups": []})
        return res({"investigation_status": "x", "what_was_ingested": ["a.pdf", "b.pdf"]})
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    # Phase 1: ingest extracts both docs but the rate limit interrupts synthesis.
    summary = asyncio.run(orchestrate.run(vault, concurrency=2))
    assert summary["extracted"] == 2
    assert "error" in summary["post_ingest"]                 # synthesis degraded
    assert orchestrate.has_pending_finalization(vault) is True

    # Phase 2: the limit has reset — watchdog finalize completes the batch.
    state["synthesis_ok"] = True
    out = asyncio.run(orchestrate.finalize(vault, post_model="haiku"))

    assert out["synthesized"] == 1
    assert "error" not in out
    assert "Synthesized prose." in (vault / "entities" / "company" / "acme-corp.md").read_text()
    # a clean finalize clears the per-run inputs, so there is nothing left pending
    assert not (vault / ".watchdog" / "tmp" / "entity-fragments").exists()
    assert not list((vault / ".watchdog" / "tmp").glob("result_*.json"))
    assert orchestrate.has_pending_finalization(vault) is False


def test_pending_finalization_uses_registry_appears_in_gate(tmp_path):
    """Entity count reflects the registry's `appears_in >= 2` gate (D26), not the fragment
    queue's `count`, which is only a touched-set marker post-D26."""
    vault = make_vault(tmp_path)
    tmp = vault / ".watchdog" / "tmp"
    frag = tmp / "entity-fragments"
    frag.mkdir(parents=True, exist_ok=True)
    (frag / "_queue.json").write_text(json.dumps({
        "acme-corp": {"count": 1},   # touched once this run...
        "beta-llc": {"count": 1},
    }))
    (vault / ".watchdog" / "Registry" / "entities.json").write_text(json.dumps({
        "acme-corp": {"appears_in": ["doc1", "doc2"]},   # ...but recurs project-wide → eligible
        "beta-llc": {"appears_in": ["doc1"]},            # single-document → not eligible
    }))
    (tmp / "result_a.json").write_text("{}")

    result = orchestrate.pending_finalization(vault)
    assert result["docs"] == 1
    assert result["entities"] == 1   # only acme-corp crosses appears_in >= 2


def test_ingest_setup_wipe_pending_controls_cleanup(tmp_path):
    """wipe_pending=False (the merge choice) keeps a prior batch's post-ingest inputs;
    the default clears them."""
    from watchdog.pipeline import ingest_setup
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="new1", filename="new.pdf")          # a queued doc → total > 0
    tmp = vault / ".watchdog" / "tmp"
    frag = tmp / "entity-fragments"
    frag.mkdir(parents=True, exist_ok=True)
    (frag / "_queue.json").write_text('{"acme-corp": {"count": 2}}')
    (tmp / "result_old.json").write_text("{}")
    (tmp / "notes_old.md").write_text("obs")
    lock = vault / ".watchdog" / "Registry" / ".ingest-lock"

    # merge: inputs preserved so this run finalizes together with the pending batch
    ingest_setup.run(vault, wipe_pending=False)
    assert (frag / "_queue.json").exists()
    assert (tmp / "result_old.json").exists() and (tmp / "notes_old.md").exists()

    # default: inputs wiped for a fresh batch
    lock.unlink(missing_ok=True)                               # release the lock from the prior call
    ingest_setup.run(vault, wipe_pending=True)
    assert not (frag / "_queue.json").exists()
    assert not (tmp / "result_old.json").exists() and not (tmp / "notes_old.md").exists()


def test_requeue_moves_failed_back(tmp_path, monkeypatch):
    """watchdog requeue moves quarantined queue files back into the active queue."""
    from watchdog.cmd.ingest import cmd_requeue
    vault = make_vault(tmp_path)
    failed = vault / ".watchdog" / "queue" / "_failed"
    failed.mkdir(parents=True, exist_ok=True)
    (failed / "ddd444.json").write_text("{}")
    monkeypatch.chdir(vault)

    cmd_requeue(None)

    assert (vault / ".watchdog" / "queue" / "ddd444.json").exists()
    assert not (failed / "ddd444.json").exists()


def test_orchestrator_sectioned_path(tmp_path, monkeypatch):
    """Large doc → section.run plans sections → per-section extract → merge → vault."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, text="a very long document ...")
    tmpd = vault / ".watchdog" / "tmp"
    tmpd.mkdir(parents=True, exist_ok=True)
    (tmpd / "section_abc123_01.md").write_text("<!-- PAGE 1 -->\n\nAcme part 1")
    (tmpd / "section_abc123_02.md").write_text("<!-- PAGE 2 -->\n\nAcme part 2")

    monkeypatch.setattr(orchestrate.section, "run", lambda v, s: {
        "sectioned": True, "page_count": 2, "sections": [
            {"index": 1, "label": "pages 1–1", "paginated": True, "pages_path": ".watchdog/tmp/section_abc123_01.md"},
            {"index": 2, "label": "pages 2–2", "paginated": True, "pages_path": ".watchdog/tmp/section_abc123_02.md"},
        ]})

    sec1 = {
        "document": {"sha256": "abc123", "filename": "test-doc.pdf", "title": "Acme AR",
                     "document_type": "Annual Report", "summary": "Acme report.",
                     "key_facts": [{"fact": "x", "basis": "stated"}]},
        "entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                      "timeline_events": [], "roles": []}],
        "morgue_entity_id": "acme-corp", "morgue_document_type": "annual-report",
        "observations": "section 1 obs",
    }
    sec2 = {"entities": [{"id": "acme-corp", "name": "Acme Corporation", "type": "Company",
                          "timeline_events": [], "roles": []}],
            "observations": "section 2 obs"}

    captured: dict = {}

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            parsed = {"skill": "general-records.md"}
        elif task == "extract-section":
            parsed = sec1 if "This is SECTION 1" in _flat(prompt) else sec2
        elif task == "briefing":
            captured["briefing_prompt"] = prompt
            parsed = {"investigation_status": "x", "what_was_ingested": ["test-doc.pdf"]}
        else:
            parsed = {"entity_syntheses": []} if task == "entity-synthesis" else {"events": []}
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="claude-agent-sdk", auth_mode="subscription", cost_usd=0.02)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault))
    assert summary["extracted"] == 1 and summary["failed"] == 0
    assert (vault / "entities" / "company" / "acme-corp.md").exists()
    # carry-forward merged the two sections into one entity
    note = (vault / "entities" / "company" / "acme-corp.md").read_text()
    assert "Acme Corporation" in note   # merge kept the longer surface form
    # the two sections' observations were merged into the scratchpad and fed to the briefing
    assert "section 1 obs" in captured["briefing_prompt"]
    assert "section 2 obs" in captured["briefing_prompt"]
    # and the compact result's key_facts reach the briefing too (#150)
    assert '"key_facts"' in captured["briefing_prompt"]


def test_sectioned_carry_forward_dedupes_entities_and_caps_observations(tmp_path, monkeypatch):
    """A5/A6: across 3 sections, an entity present in every section is carried forward once
    (not once per section it already appeared in), only the immediately preceding section's
    observations are carried (not every prior section's concatenated), and the investigation
    brief reaches every section's prompt."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, text="a very long document ...")
    tmpd = vault / ".watchdog" / "tmp"
    tmpd.mkdir(parents=True, exist_ok=True)
    for i in (1, 2, 3):
        (tmpd / f"section_abc123_0{i}.md").write_text(f"<!-- PAGE {i} -->\n\npart {i}")
    plan = {"sectioned": True, "page_count": 3, "sections": [
        {"index": i, "label": f"pages {i}", "paginated": True,
         "pages_path": f".watchdog/tmp/section_abc123_0{i}.md"} for i in (1, 2, 3)
    ]}
    pf = {"filename": "test-doc.pdf", "existing_entities": [], "known_document_types": [],
          "page_count": 3}

    acme_entity = {"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                   "timeline_events": [], "roles": []}
    sections_out = [
        {"document": {"sha256": "abc123", "filename": "test-doc.pdf", "title": "Acme AR",
                      "document_type": "Annual Report", "summary": "s",
                      "key_facts": [{"fact": "x", "basis": "stated"}]},
         "entities": [acme_entity], "morgue_entity_id": "acme-corp",
         "morgue_document_type": "annual-report", "observations": "section 1 obs"},
        {"entities": [acme_entity], "observations": "section 2 obs"},
        {"entities": [acme_entity], "observations": "section 3 obs"},
    ]
    seen_prompts = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen_prompts.append(prompt)
        return model_client.ModelResult(parsed=sections_out[len(seen_prompts) - 1], text="",
                                        model="m", backend="b", auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    asyncio.run(orchestrate._extract_sectioned(
        vault, "abc123", pf, "SKILL", plan, "sonnet", "annual-report",
        brief="INVESTIGATE THE FRAUD"))

    assert len(seen_prompts) == 3
    flat_prompts = [_flat(p) for p in seen_prompts]
    # section 2's prompt carries section 1's single entity line, once (not duplicated)
    assert flat_prompts[1].count("acme-corp | Acme Corp | Company") == 1
    assert "section 1 obs" in flat_prompts[1]
    # section 3's prompt still lists the entity exactly once — no per-section duplication
    assert flat_prompts[2].count("acme-corp | Acme Corp | Company") == 1
    # only the immediately preceding section's observations are carried forward
    assert "section 2 obs" in flat_prompts[2]
    assert "section 1 obs" not in flat_prompts[2]
    # A6: the investigation brief reaches every section's prompt
    for p in flat_prompts:
        assert "INVESTIGATE THE FRAUD" in p


# ── claude-batch (#214) ─────────────────────────────────────────────────────────

def test_run_batch_requires_pinned_skill(tmp_path):
    vault = make_vault(tmp_path)
    with pytest.raises(model_client.ModelError, match="pinned skill"):
        asyncio.run(orchestrate._run_batch(vault, [], None, "sonnet", None, None, 5,
                                           "haiku", 5, None))


def test_run_batch_requires_api_key_auth(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "subscription"})
    with pytest.raises(model_client.ModelError, match="api-key auth"):
        asyncio.run(orchestrate._run_batch(vault, [], None, "sonnet", "/tmp/skill.md", None, 5,
                                           "haiku", 5, None))


def test_submit_batch_splits_sectioned_and_whole_doc(tmp_path, monkeypatch):
    """A sectioned doc is routed to the normal synchronous _extract_document (forced onto
    claude-api — a batch request can't carry sequential section carry-forward); a non-sectioned
    doc's prompt is handed to batch_extract.submit instead of extracted synchronously."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="small", filename="small.pdf", text="short doc")
    _queue_doc(vault, sha="big", filename="big.pdf", text="a very long document ...")
    monkeypatch.setattr(orchestrate.section, "run", lambda v, s, **kw: {"sectioned": s == "big"})

    sectioned_calls = []
    async def fake_extract_document(vault, sha, brief, extract_model, classify_model,
                                    classify_pages, pinned_skill, extract_effort,
                                    extract_backend, classify_backend):
        sectioned_calls.append({"sha": sha, "extract_backend": extract_backend})
        return {"sha256": sha, "filename": f"{sha}.pdf", "status": "ok", "record_skill": "s"}
    monkeypatch.setattr(orchestrate, "_extract_document", fake_extract_document)

    submitted = {}
    async def fake_submit(vault, docs, *, model, effort, skill_label, api_key):
        submitted["docs"] = docs
        return "batch_xyz"
    monkeypatch.setattr(orchestrate.batch_extract, "submit", fake_submit)

    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("SKILL BODY")

    out = asyncio.run(orchestrate._submit_batch(
        vault, ["small", "big"], None, "sonnet", str(skill_file), None, 5, "haiku", 5, None,
        api_key="sk-x"))

    assert out["batch_pending"] is True
    assert sectioned_calls == [{"sha": "big", "extract_backend": "claude-api"}]
    assert any(r.get("sha256") == "big" for r in out["results"])
    assert [d["sha"] for d in submitted["docs"]] == ["small"]
    assert submitted["docs"][0]["prompt"][1]["cache_control"]["ttl"] == "1h"


def test_submit_batch_skips_already_extracted_and_preflight_errors(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    monkeypatch.setattr(orchestrate.preflight, "run", lambda v, s: (
        {"error": "not found"} if s == "gone" else
        {"already_extracted": True, "filename": "done.pdf"}))
    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("SKILL")

    out = asyncio.run(orchestrate._submit_batch(
        vault, ["gone", "done"], None, "sonnet", str(skill_file), None, 5, "haiku", 5, None,
        api_key="sk-x"))

    statuses = {r["sha256"]: r["status"] for r in out["results"]}
    assert statuses == {"gone": "failed", "done": "skipped"}
    assert out["batch_pending"] is False   # nothing left to submit


def test_resume_batch_reports_progress_when_not_ended(tmp_path, monkeypatch, capsys):
    vault = make_vault(tmp_path)
    state = {"batch_id": "b1", "shas": ["a", "b"], "model": "claude-sonnet-4-6",
            "skill_label": "s", "effort": None}

    async def fake_status(batch_id, api_key):
        return {"processing_status": "in_progress", "request_counts": {"processing": 1, "succeeded": 1}}
    monkeypatch.setattr(orchestrate.batch_extract, "status", fake_status)

    out = asyncio.run(orchestrate._resume_batch(vault, state, "/tmp/x.md", None, "sk-x"))
    assert out == {"results": [], "batch_pending": True}
    assert "still processing" in capsys.readouterr().out


def test_resume_batch_collects_and_clears_state_when_ended(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha1", filename="a.pdf")
    state = {"batch_id": "b1", "shas": ["sha1"], "model": "claude-sonnet-4-6",
            "skill_label": "annual-report", "effort": None}
    batch_extract.write_state(vault, state)

    async def fake_status(batch_id, api_key):
        return {"processing_status": "ended", "request_counts": {"succeeded": 1}}
    monkeypatch.setattr(orchestrate.batch_extract, "status", fake_status)

    async def fake_collect(batch_id, api_key, model_id):
        return {"sha1": {"ok": True, "parsed": _extraction(sha="sha1", filename="a.pdf"),
                         "usage": {}, "cost_usd": 0.02, "error": None}}
    monkeypatch.setattr(orchestrate.batch_extract, "collect", fake_collect)

    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("SKILL")

    out = asyncio.run(orchestrate._resume_batch(vault, state, str(skill_file), None, "sk-x"))
    assert out["batch_pending"] is False
    assert out["results"][0]["status"] == "ok"
    assert batch_extract.read_state(vault) is None   # state cleared on a clean collection


def test_finish_batch_item_repairs_invalid_result_via_claude_api(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha1", filename="a.pdf")

    seen_backends = []
    async def fake_acomplete(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen_backends.append(backend)
        return model_client.ModelResult(parsed=_extraction(sha="sha1", filename="a.pdf"),
                                        text="", model="m", backend="claude-api",
                                        auth_mode="api-key", cost_usd=0.03)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake_acomplete)

    item = {"ok": False, "parsed": None, "usage": None, "cost_usd": None,
           "error": "batch response was not valid JSON"}
    result = asyncio.run(orchestrate._finish_batch_item(
        vault, "sha1", item, "SKILL BODY", "annual-report", None, "sk-x"))

    assert result["status"] == "ok"
    assert seen_backends == ["claude-api"]   # repaired synchronously, not re-batched


def test_finish_batch_item_fails_when_result_missing(tmp_path):
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha1", filename="a.pdf")
    result = asyncio.run(orchestrate._finish_batch_item(
        vault, "sha1", None, "SKILL", "s", None, "sk-x"))
    assert result["status"] == "failed"


def test_run_dispatches_to_batch_and_merges_batch_pending_into_summary(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha1", filename="a.pdf")
    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("SKILL")

    async def fake_run_batch(*args, **kwargs):
        return {"results": [], "batch_pending": True}
    monkeypatch.setattr(orchestrate, "_run_batch", fake_run_batch)

    summary = asyncio.run(orchestrate.run(vault, extract_backend="claude-batch",
                                          pinned_skill=str(skill_file)))
    assert summary["batch_pending"] is True
    assert summary["extracted"] == 0
    assert "post_ingest" not in summary   # nothing extracted this run → no finalize


def test_run_resumes_pending_batch_even_with_empty_queue(tmp_path, monkeypatch):
    """A pending batch must be checked even when nothing is newly queued — mirrors
    has_pending_finalization's 'resolve the pending thing first' precedent."""
    vault = make_vault(tmp_path)
    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("SKILL")
    batch_extract.write_state(vault, {"batch_id": "b1", "shas": [], "model": "claude-sonnet-4-6",
                                      "skill_label": "s", "effort": None})
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})

    submit_calls = []
    async def fake_submit(*a, **k):
        submit_calls.append(1)
        return "should-not-be-called"
    monkeypatch.setattr(orchestrate.batch_extract, "submit", fake_submit)

    async def fake_status(batch_id, api_key):
        return {"processing_status": "in_progress", "request_counts": {}}
    monkeypatch.setattr(orchestrate.batch_extract, "status", fake_status)

    summary = asyncio.run(orchestrate.run(vault, extract_backend="claude-batch",
                                          pinned_skill=str(skill_file)))
    assert summary["batch_pending"] is True
    assert not submit_calls   # resumed the pending batch instead of submitting a new one
