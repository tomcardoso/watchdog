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
            "classify": {"skill": "general-records.md", "document_type": "Annual Report"},
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


def test_orchestrator_extracts_and_writes_vault(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    _mock(monkeypatch, extraction=_extraction())

    summary = asyncio.run(orchestrate.run(vault))

    assert summary["extracted"] == 1 and summary["failed"] == 0
    # real write_vault produced the notes
    assert (vault / "entities" / "company" / "acme-corp.md").exists()
    assert list((vault / "documents").glob("*.md"))
    # housekeeping: queue file consumed, scratchpad written for the briefing
    assert not (vault / ".watchdog" / "queue" / "abc123.json").exists()
    assert (vault / ".watchdog" / "tmp" / "notes_abc123.md").exists()
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
    # the queue file is left in place so the doc can be retried
    assert (vault / ".watchdog" / "queue" / "abc123.json").exists()


def test_orchestrator_empty_queue(tmp_path):
    vault = make_vault(tmp_path)
    summary = asyncio.run(orchestrate.run(vault))
    assert summary == {"results": [], "extracted": 0, "skipped": 0, "failed": 0}


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

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1):
        if task == "classify":
            parsed = {"skill": "general-records.md"}
        elif task == "extract-section":
            parsed = sec1 if "This is SECTION 1" in prompt else sec2
        elif task == "briefing":
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
    assert (vault / ".watchdog" / "tmp" / "notes_abc123.md").read_text() == "section 1 obs\nsection 2 obs"
