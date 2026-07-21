"""Tests for `--force` (#424): re-extracting a document even when a cached extraction, or a
note already committed to the vault, exists for it.

The oracle test (`test_force_reingest_replaces_committed_note_and_registry_entry_in_place`) is
the safety net for the hard part of this feature: forcing a re-ingest of an already-committed
document must REPLACE its note and registry entry in place, never duplicate or strand a second
one. It runs the real preflight/postflight/write_vault path (model mocked), mirroring
`tests/test_golden_vault.py`/`tests/test_golden_merge.py`.

The remaining tests are narrower unit tests: each of the three extraction-time skip sites
(`_extract_document`, `_finish_batch_item`, `_submit_batch`) bypassing `already_extracted`/
`already_staged` under `force`, and the CLI-level plumbing in `cmd/ingest.py` — threading
`force`/`skip_finalize` into `orchestrate.run`, and the overwrite-warning gate between a forced
`ingest`'s extraction and its finalize.
"""

import argparse
import asyncio
import json
import re

import pytest

from watchdog.pipeline import orchestrate

from tests.test_orchestrate import _extraction, _mock, _queue_doc
from tests.test_write_vault import make_vault

SHA = "a" * 64


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def _ext(*, title, summary, fact_text, entity_summary):
    """A one-entity, one-fact extraction — the fact is tagged onto the entity so post-flight
    explodes an evidence fragment onto it, giving the entity note a real ## Analysis block to
    check replace-vs-append behaviour against."""
    return {
        "document": {
            "sha256": SHA, "filename": "alpha.pdf", "original_path": "_INCOMING/alpha.pdf",
            "title": title, "document_type": "Annual Report",
            "date_of_document": "2024-01-15", "page_count": 1, "source": None, "obtained": None,
            "near_duplicate_of": None, "summary": summary,
            "key_facts": [{"fact": fact_text, "page": 1, "basis": "stated", "entities": ["acme-corp"]}],
        },
        "entities": [{
            "id": "acme-corp", "name": "Acme Corp", "type": "Company", "aliases": [],
            "summary": entity_summary, "timeline_events": [], "roles": [],
        }],
        "morgue_entity_id": "acme-corp", "morgue_document_type": "annual-report",
        "scratchpad": "# notes",
    }


# ── Oracle: forced re-ingest of an already-committed document ────────────────────────────────

def test_force_reingest_replaces_committed_note_and_registry_entry_in_place(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha=SHA, filename="alpha.pdf", text="Acme Corp filed an annual report in 2024.")
    _mock(monkeypatch, extraction=_ext(
        title="Acme Annual Report", summary="Acme's annual report.",
        fact_text="Acme filed in 2024.", entity_summary="A company that filed an annual report."))
    asyncio.run(orchestrate.run(vault))

    documents_path = vault / ".watchdog" / "registry" / "documents.json"
    entities_path = vault / ".watchdog" / "registry" / "entities.json"
    docs_before = json.loads(documents_path.read_text())
    entities_before = json.loads(entities_path.read_text())
    assert SHA in docs_before
    assert docs_before[SHA]["title"] == "Acme Annual Report"
    doc_note_path = vault / f"{docs_before[SHA]['document_note']}.md"
    assert doc_note_path.exists()
    assert len(list((vault / "documents").glob("*.md"))) == 1

    entity_note_path = vault / f"{entities_before['acme-corp']['note_path']}.md"
    entity_note_before = entity_note_path.read_text()
    assert "Acme filed in 2024" in entity_note_before
    assert entities_before["acme-corp"]["appears_in"] == [SHA]

    # Re-drop the document (a fresh queue entry for the same sha) and force re-extract it under
    # "different settings" — simulated here as a different extraction result, standing in for
    # whatever a different model/effort/skill would have produced.
    _queue_doc(vault, sha=SHA, filename="alpha.pdf",
               text="Acme Corp filed a restated annual report in 2024 disclosing $9,000,000.")
    _mock(monkeypatch, extraction=_ext(
        title="Acme Restated Annual Report", summary="Acme's restated report.",
        fact_text="Acme restated 2024 disclosing $9,000,000.",
        entity_summary="A company that restated its annual report."))

    extract_summary = asyncio.run(orchestrate.run(vault, force=True, skip_finalize=True))
    assert extract_summary["finalize_skipped"] is True
    assert extract_summary["results"][0]["status"] == "ok"   # not "skipped"

    # Nothing recommitted yet — this is exactly the set cmd_ingest's overwrite gate computes.
    still_before = json.loads(documents_path.read_text())
    assert still_before[SHA]["title"] == "Acme Annual Report"

    out = asyncio.run(orchestrate.finalize(vault, post_model="haiku", force_shas=[SHA]))
    assert not out.get("error") and not out.get("briefing_error")

    docs_after = json.loads(documents_path.read_text())
    entities_after = json.loads(entities_path.read_text())

    # Not duplicated at the registry level: still exactly one document, one entity.
    assert len(docs_after) == 1
    assert docs_after[SHA]["title"] == "Acme Restated Annual Report"
    assert len(entities_after) == 1
    assert entities_after["acme-corp"]["appears_in"] == [SHA]   # not doubled by the re-commit

    # Not duplicated/stranded at the note level: still exactly one document note, and it was
    # overwritten in place with the new content.
    doc_notes = list((vault / "documents").glob("*.md"))
    assert len(doc_notes) == 1
    assert "Acme Restated Annual Report" in doc_note_path.read_text()

    # Replace-not-append on the entity note's Analysis block: the old fact text is gone, the new
    # one is present, and there is exactly one dated entry for this document (not two).
    entity_note_after = entity_note_path.read_text()
    assert "Acme filed in 2024" not in entity_note_after
    assert "$9,000,000" in entity_note_after
    # (The entity's `appears_in` frontmatter also links to the document note, so count only the
    # Analysis block's own "*<date>, via [[doc|title]]:*" attribution line, which is the part
    # that would double if the document's contribution were appended instead of replaced.)
    assert entity_note_after.count(f"via [[{docs_after[SHA]['document_note']}|") == 1


# ── Extraction-time skip-site bypass (the three sites #424 touches) ──────────────────────────

def _pf_dict(*, already_extracted=False, already_staged=False):
    return {
        "sha256": SHA, "filename": "alpha.pdf", "original_path": "_INCOMING/alpha.pdf",
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
    async def fake_submit(vault, docs, *, model, effort, skill_label, api_key):
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


# ── _pending_commits(force_shas=...) ──────────────────────────────────────────────────────────

def test_pending_commits_includes_force_shas_even_when_already_committed(tmp_path):
    vault = make_vault(tmp_path)
    extracted_dir = vault / ".watchdog" / "extracted"
    extracted_dir.mkdir(parents=True, exist_ok=True)
    (extracted_dir / "committed-sha.json").write_text("{}")
    (extracted_dir / "pending-sha.json").write_text("{}")
    (vault / ".watchdog" / "registry" / "documents.json").write_text(
        json.dumps({"committed-sha": {"document_note": "documents/x"}}))

    # Without force_shas, the already-committed sha is excluded as usual.
    assert orchestrate._pending_commits(vault) == ["pending-sha"]

    # With it forced, both are included, still sorted.
    assert orchestrate._pending_commits(
        vault, force_shas=["committed-sha"]) == ["committed-sha", "pending-sha"]


# ── CLI plumbing (cmd/ingest.py) ──────────────────────────────────────────────────────────────

def _args(**kwargs):
    return argparse.Namespace(**{
        "extractor_model": None, "finalizer_model": None, "classifier_model": None,
        "extractor_effort": None, "finalizer_effort": None, "concurrency": None,
        "classify_pages": None, "skill": None, "wait": False, "estimate": False,
        "no_finalize": False, "force": False,
        **kwargs,
    })


def _vault_with_queued_doc(tmp_path):
    vault = make_vault(tmp_path)
    qdir = vault / ".watchdog" / "queue"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / f"{SHA}.json").write_text(json.dumps({
        "sha256": SHA, "filename": "alpha.pdf", "page_count": 1,
        "pages": [{"page": 1, "markdown": "text"}],
        "near_dup": {"near_duplicates": [], "top_similarity": 0.0},
    }))
    (vault / "_INCOMING").mkdir(exist_ok=True)
    return vault


@pytest.fixture
def wdg_home(tmp_path, monkeypatch):
    import watchdog.cmd.base as _base
    import watchdog.cmd.setup as _setup
    home = tmp_path / ".watchdog"
    home.mkdir()
    monkeypatch.setattr(_base,  "WATCHDOG_HOME",  home)
    monkeypatch.setattr(_base,  "PROJECTS_FILE",  home / "projects.json")
    monkeypatch.setattr(_base,  "CONFIG_FILE",    home / "config.json")
    monkeypatch.setattr(_setup, "WATCHDOG_HOME",  home)
    monkeypatch.setattr(_setup, "CONFIG_FILE",    home / "config.json")
    return home


def test_cmd_ingest_force_threads_to_orchestrate_run(wdg_home, tmp_path, monkeypatch):
    """`ingest --force` reaches `orchestrate.run` as `force=True`, and — because finalize must be
    held off until the overwrite gate runs — as `skip_finalize=True` too, even though plain
    `ingest` has no `--no-finalize` flag of its own."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)

    calls = []
    async def fake_run(*a, **k):
        calls.append(k)
        return {"results": [{"sha256": SHA, "filename": "alpha.pdf", "status": "ok", "entity_count": 1}],
                "extracted": 1, "skipped": 0, "failed": 0, "cancelled": False,
                "rate_limited": False, "stop_message": None, "rate_limit_resets_at": None,
                "quarantined": 0, "finalize_skipped": True}
    monkeypatch.setattr(orch_module, "run", fake_run)
    # Nothing in documents.json — no overwrite target, so the gate finalizes with no confirm.
    monkeypatch.setattr(ing, "_run_finalize", lambda *a, **k: {"synthesized": 0})

    ing.cmd_ingest(_args(force=True), confirm=False)

    assert len(calls) == 1
    assert calls[0].get("force") is True
    assert calls[0].get("skip_finalize") is True


def test_cmd_extract_force_no_gate_no_finalize(wdg_home, tmp_path, monkeypatch):
    """`extract --force` re-extracts with `force=True`/`skip_finalize=True` like `ingest --force`,
    but — because `cmd_extract` sets `no_finalize` — never runs the overwrite gate or finalize."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)

    calls = []
    async def fake_run(*a, **k):
        calls.append(k)
        return {"results": [{"sha256": SHA, "filename": "alpha.pdf", "status": "ok", "entity_count": 1}],
                "extracted": 1, "skipped": 0, "failed": 0, "cancelled": False,
                "rate_limited": False, "stop_message": None, "rate_limit_resets_at": None,
                "quarantined": 0, "finalize_skipped": True}
    monkeypatch.setattr(orch_module, "run", fake_run)
    monkeypatch.setattr(ing.interactive, "pick", lambda *a, **k: 0)   # "Ingest now"
    monkeypatch.setattr(ing, "_handle_force_gate",
                        lambda *a, **k: pytest.fail("extract --force must never gate/finalize"))
    monkeypatch.setattr(ing, "_run_finalize",
                        lambda *a, **k: pytest.fail("extract --force must never finalize"))

    ing.cmd_extract(_args(force=True))

    assert len(calls) == 1
    assert calls[0].get("force") is True
    assert calls[0].get("skip_finalize") is True


def _committed_vault_with_forced_doc(tmp_path):
    """A vault where `sha` is already a committed document — so `_handle_force_gate` must treat
    it as an overwrite target — plus a fresh queue entry standing in for a forced re-extraction
    of it that just finished (mirroring what `orchestrate.run(force=True)` would have staged)."""
    vault = _vault_with_queued_doc(tmp_path)
    (vault / ".watchdog" / "registry" / "documents.json").write_text(json.dumps({
        SHA: {"document_note": "documents/alpha", "filename": "alpha.pdf"},
    }))
    return vault


def test_cmd_ingest_force_gate_defaults_to_cancel_and_leaves_batch_finalizable(
        wdg_home, tmp_path, monkeypatch, capsys):
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module

    vault = _committed_vault_with_forced_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)

    async def fake_run(*a, **k):
        return {"results": [{"sha256": SHA, "filename": "alpha.pdf", "status": "ok", "entity_count": 1}],
                "extracted": 1, "skipped": 0, "failed": 0, "cancelled": False,
                "rate_limited": False, "stop_message": None, "rate_limit_resets_at": None,
                "quarantined": 0, "finalize_skipped": True}
    monkeypatch.setattr(orch_module, "run", fake_run)

    confirm_calls = []
    def fake_confirm(prompt, default=True):
        confirm_calls.append(default)
        return False   # user cancels
    monkeypatch.setattr(ing.interactive, "confirm", fake_confirm)

    finalize_calls = []
    monkeypatch.setattr(ing, "_run_finalize", lambda *a, **k: finalize_calls.append(k) or {})

    ing.cmd_ingest(_args(force=True), confirm=False)

    assert confirm_calls == [False]           # gate defaults to Cancel
    assert finalize_calls == []                # cancel must not finalize
    out = _strip_ansi(capsys.readouterr().out)
    assert "documents/alpha" in out            # the note about to be replaced is listed
    assert "Cancelled" in out


def test_cmd_ingest_force_gate_confirms_and_finalizes_with_force_shas(
        wdg_home, tmp_path, monkeypatch):
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module

    vault = _committed_vault_with_forced_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)

    async def fake_run(*a, **k):
        return {"results": [{"sha256": SHA, "filename": "alpha.pdf", "status": "ok", "entity_count": 1}],
                "extracted": 1, "skipped": 0, "failed": 0, "cancelled": False,
                "rate_limited": False, "stop_message": None, "rate_limit_resets_at": None,
                "quarantined": 0, "finalize_skipped": True}
    monkeypatch.setattr(orch_module, "run", fake_run)
    monkeypatch.setattr(ing.interactive, "confirm", lambda *a, **k: True)   # user confirms

    finalize_calls = []
    def fake_run_finalize(vault, post_model, post_effort=None, post_backend=None, force_shas=None):
        finalize_calls.append(force_shas)
        return {"synthesized": 1}
    monkeypatch.setattr(ing, "_run_finalize", fake_run_finalize)

    ing.cmd_ingest(_args(force=True), confirm=False)

    assert finalize_calls == [[SHA]]


def test_estimate_force_prices_the_queued_document(wdg_home, tmp_path, monkeypatch, capsys):
    """`--estimate --force` must price a document the same as any other queued one — the queue
    scan has no notion of a cached artifact to discount (#424)."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing

    vault = _committed_vault_with_forced_doc(tmp_path)   # SHA is already committed
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})

    ing.cmd_ingest(_args(estimate=True, force=True), confirm=False)

    out = _strip_ansi(capsys.readouterr().out)
    assert "1 document" in out   # priced, not silently excluded as already-committed
