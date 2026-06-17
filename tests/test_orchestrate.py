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
        parsed = ({"skill": "general-records.md", "document_type": "Annual Report"}
                  if task == "classify" else extraction)
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
