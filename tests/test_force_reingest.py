"""Re-extracting with `force` (#424): processing extracts a queued document again even when a
staged or committed extraction exists for it.

The narrow tests cover each of the three extraction-time skip sites (`_extract_document`,
`_finish_batch_item`, `_submit_batch`) bypassing `already_extracted`/`already_staged` under
`force`; the rest cover the processing operation threading `force` and the stage defaults into
`orchestrate.run`.
"""

import asyncio
import json

import pytest

from watchdog.ops import CollectingReporter, using
from watchdog.ops import ingest as ing
from watchdog.pipeline import orchestrate

from tests.test_orchestrate import _extraction, _mock, _queue_doc  # noqa: F401
from tests.test_write_vault import make_vault

SHA = "a" * 64


# ── Extraction-time skip-site bypass (the three sites #424 touches) ──────────────────────────

def _pf_dict(*, already_extracted=False, already_staged=False):
    return {
        "sha256": SHA, "filename": "alpha.pdf", "original_path": "incoming/alpha.pdf",
        "page_count": 1, "already_extracted": already_extracted, "already_staged": already_staged,
        "pages": [{"page": 1, "markdown": "Acme Corp filed an annual report."}],
        "near_dup": {"near_duplicates": [], "top_similarity": 0.0},
        "known_document_types": [], "file_metadata": {}, "processing": {}, "sidecar": None,
    }


def test_force_bypasses_already_staged_skip_in_extract_document(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha=SHA, filename="alpha.pdf")
    monkeypatch.setattr(orchestrate.preflight, "run",
                       lambda v, s: _pf_dict(already_staged=True))
    _mock(monkeypatch, extraction=_extraction(sha=SHA, filename="alpha.pdf"))

    skipped = asyncio.run(orchestrate._extract_document(vault, SHA, None, "sonnet", "haiku"))
    assert skipped["status"] == "skipped"

    forced = asyncio.run(orchestrate._extract_document(
        vault, SHA, None, "sonnet", "haiku", force=True))
    assert forced["status"] == "ok"


def test_force_bypasses_already_extracted_skip_in_extract_document(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha=SHA, filename="alpha.pdf")
    monkeypatch.setattr(orchestrate.preflight, "run",
                       lambda v, s: _pf_dict(already_extracted=True))
    _mock(monkeypatch, extraction=_extraction(sha=SHA, filename="alpha.pdf"))

    skipped = asyncio.run(orchestrate._extract_document(vault, SHA, None, "sonnet", "haiku"))
    assert skipped["status"] == "skipped"
    assert not (vault / ".watchdog" / "queue" / f"{SHA}.json").exists()   # unlinked on the skip path

    _queue_doc(vault, sha=SHA, filename="alpha.pdf")   # re-queue — the plain skip path removed it
    forced = asyncio.run(orchestrate._extract_document(
        vault, SHA, None, "sonnet", "haiku", force=True))
    assert forced["status"] == "ok"
    assert (vault / ".watchdog" / "queue" / f"{SHA}.json").exists()   # kept for the commit pass


def test_force_bypasses_already_extracted_skip_in_finish_batch_item(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha=SHA, filename="alpha.pdf")
    monkeypatch.setattr(orchestrate.preflight, "run",
                       lambda v, s: _pf_dict(already_extracted=True))
    item = {"ok": True, "parsed": _extraction(sha=SHA, filename="alpha.pdf"),
           "usage": None, "cost_usd": 0.01, "error": None}

    skipped = asyncio.run(orchestrate._finish_batch_item(
        vault, SHA, item, "SKILL", "s", None, "sk-x"))
    assert skipped["status"] == "skipped"
    assert not (vault / ".watchdog" / "queue" / f"{SHA}.json").exists()

    _queue_doc(vault, sha=SHA, filename="alpha.pdf")
    forced = asyncio.run(orchestrate._finish_batch_item(
        vault, SHA, item, "SKILL", "s", None, "sk-x", force=True))
    assert forced["status"] == "ok"
    assert (vault / ".watchdog" / "queue" / f"{SHA}.json").exists()


def test_force_bypasses_already_extracted_skip_in_submit_batch(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha=SHA, filename="alpha.pdf")
    monkeypatch.setattr(orchestrate.preflight, "run",
                       lambda v, s: _pf_dict(already_extracted=True))
    monkeypatch.setattr(orchestrate.section, "run", lambda v, s, **kw: {"sectioned": False})
    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("SKILL BODY")

    submitted = {}
    async def fake_submit(vault, docs, *, model, effort, skills, api_key, backend=None):
        submitted["docs"] = docs
        return "batch_xyz"
    monkeypatch.setattr(orchestrate.batch_extract, "submit", fake_submit)

    out = asyncio.run(orchestrate._submit_batch(
        vault, [SHA], None, "sonnet", str(skill_file), None, 5, "haiku", 5, None, api_key="sk-x"))
    assert out["batch_pending"] is False   # nothing submitted — skipped as already extracted
    statuses = {r["sha256"]: r["status"] for r in out["results"]}
    assert statuses == {SHA: "skipped"}

    _queue_doc(vault, sha=SHA, filename="alpha.pdf")
    out = asyncio.run(orchestrate._submit_batch(
        vault, [SHA], None, "sonnet", str(skill_file), None, 5, "haiku", 5, None,
        api_key="sk-x", force=True))
    assert out["batch_pending"] is True
    assert [d["sha"] for d in submitted["docs"]] == [SHA]


# ── the processing operation ─────────────────────────────────────────────────────

def _vault_with_queued_doc(tmp_path):
    vault = make_vault(tmp_path)
    qdir = vault / ".watchdog" / "queue"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / f"{SHA}.json").write_text(json.dumps({
        "sha256": SHA, "filename": "alpha.pdf", "page_count": 1,
        "pages": [{"page": 1, "markdown": "text"}],
        "near_dup": {"near_duplicates": [], "top_similarity": 0.0},
    }))
    (vault / "incoming").mkdir(exist_ok=True)
    return vault


@pytest.fixture
def run_calls(tmp_path, monkeypatch):
    """A queued vault, settings in tmp_path, Claude on an API key, and `orchestrate.run`
    recording what it was asked to do."""
    from watchdog import auth
    monkeypatch.setattr("watchdog.config.HOME", tmp_path / ".watchdog")
    monkeypatch.setattr(auth, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orchestrate, "has_pending_finalization", lambda v: False)
    calls = []

    async def fake_run(*a, **k):
        calls.append(k)
        return {"results": [{"sha256": SHA, "filename": "alpha.pdf", "status": "ok", "entity_count": 1}],
                "extracted": 1, "skipped": 0, "failed": 0, "cancelled": False,
                "rate_limited": False, "stop_message": None, "rate_limit_resets_at": None,
                "quarantined": 0, "finalize_skipped": True}
    monkeypatch.setattr(orchestrate, "run", fake_run)
    monkeypatch.setattr(ing, "_run_finalize", lambda *a, **k: pytest.fail("processing alone never finalizes"))
    return _vault_with_queued_doc(tmp_path), calls


def _process(vault, **opts):
    with using(CollectingReporter()):
        return ing.process(vault, ing.RunOptions(skip_warning=True, **opts), finalize=False)


def test_force_threads_to_orchestrate_run_without_finalizing(run_calls):
    vault, calls = run_calls
    _process(vault, force=True)
    assert len(calls) == 1
    assert calls[0].get("force") is True and calls[0].get("skip_finalize") is True


def test_extractor_effort_defaults_to_high_when_unconfigured(run_calls):
    """D140: with no run option and no `extractor_effort` setting, processing reaches
    `orchestrate.run` with `extract_effort="high"`, not None (which would omit the parameter)."""
    vault, calls = run_calls
    _process(vault)
    assert calls[0].get("extract_effort") == "high"


def test_classifier_effort_is_low_on_the_default_haiku_classifier(run_calls):
    """D221's `classify_effort="low"` default reaches `orchestrate.run` on an unconfigured
    install now that the default `haiku` is Haiku 5.5, which accepts effort (D277)."""
    vault, calls = run_calls
    _process(vault)
    assert calls[0].get("classify_effort") == "low"


def test_classifier_effort_defaults_to_low_on_a_classifier_model_that_supports_it(run_calls):
    vault, calls = run_calls
    _process(vault, classifier_model="openai:gpt-5.6-luna")
    assert calls[0].get("classify_effort") == "low"
