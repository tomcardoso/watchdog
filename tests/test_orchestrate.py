"""Integration test for the Python orchestrator: the per-document flow runs through
the REAL preflight/postflight/write_vault with the model mocked."""

import asyncio
import json

import pytest

from watchdog import model_client
from watchdog.pipeline import orchestrate

from tests.test_write_vault import make_vault


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
            "key_facts": [{"fact": "Filed in 2024", "page": 1, "confidence": "high"}],
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
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1):
        parsed = {
            "classify": {"skill": "general-records.md"},
            "extract": extraction,
            "entity-synthesis": {"entity_syntheses": []},
            "timeline-dedup": {"events": []},
            "briefing": {"investigation_status": "Early days.",
                         "what_was_ingested": ["test-doc.pdf — Annual Report"],
                         "new_entities": ["Acme Corp"]},
        }.get(task, extraction)
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                         backend="claude-agent-sdk", auth_mode="subscription", cost_usd=0.01)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)


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

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1):
        seen.append((task, model))
        parsed = {
            "classify": {"skill": "general-records.md"},
            "extract": _extraction(),
            "entity-synthesis": {"entity_syntheses": []},
            "timeline-dedup": {"events": []},
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
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1):
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
                     "key_facts": [{"fact": "x", "confidence": "high"}]},
        "entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                      "timeline_events": [], "roles": []}],
        "morgue_entity_id": "acme-corp", "morgue_document_type": "annual-report",
        "observations": "sec1",
    }
    sec_later = {"entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                              "timeline_events": [], "roles": []}], "observations": "sec2"}

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1):
        if task == "classify":
            parsed = {"skill": "general-records.md"}
        elif task == "extract":
            calls["extract"] += 1
            parsed = _extraction(valid=False)                 # whole-doc → postflight rejects
        elif task == "extract-section":
            calls["section"] += 1
            parsed = sec_first if "This is SECTION 1" in prompt else sec_later
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
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1):
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
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1):
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
    assert "CORPORATE FILINGS SKILL BODY" in seen["prompt"]


def test_record_skill_provenance_is_persisted(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    _mock(monkeypatch, extraction=_extraction())     # classify mock returns general-records.md
    asyncio.run(orchestrate.run(vault))

    docs = json.loads((vault / ".watchdog" / "Registry" / "documents.json").read_text())
    assert next(iter(docs.values()))["record_skill"] == "general-records"
    note = next((vault / "documents").glob("*.md")).read_text(encoding="utf-8")
    assert "record_skill: general-records" in note


def test_orchestrator_cancels_gracefully_on_sigint(tmp_path, monkeypatch):
    """Ctrl+C during extraction → cancelled summary, no traceback, unfinished docs keep
    their queue file, and post-ingest is skipped."""
    import os
    import signal

    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="aaa111", filename="one.pdf")
    _queue_doc(vault, sha="bbb222", filename="two.pdf")

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1):
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

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1):
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

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1):
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

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1):
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

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1):
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
            return res({"events": []})
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
                     "key_facts": [{"fact": "x", "confidence": "high"}]},
        "entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                      "timeline_events": [], "roles": []}],
        "morgue_entity_id": "acme-corp", "morgue_document_type": "annual-report",
        "observations": "section 1 obs",
    }
    sec2 = {"entities": [{"id": "acme-corp", "name": "Acme Corporation", "type": "Company",
                          "timeline_events": [], "roles": []}],
            "observations": "section 2 obs"}

    captured: dict = {}

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1):
        if task == "classify":
            parsed = {"skill": "general-records.md"}
        elif task == "extract-section":
            parsed = sec1 if "This is SECTION 1" in prompt else sec2
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
