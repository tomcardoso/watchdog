"""Processing in the sample investigation (D307): its two waiting files are added with prepared
answers, never a real model. Building, then adding them, makes no network call and no real model
call, records $0 `sample` usage, and leaves a new briefing, new facts and one new contradiction. A
file of the reporter's own is refused before any model call, the re-check answers from the story,
and Ask Claude is refused."""

import json
from pathlib import Path

import pytest

from tests.test_sample import _create, _home
from watchdog import model_client, ops, telemetry_db
from watchdog.ops import CollectingReporter
from watchdog.ops import sample as sample_op

pytest_plugins = ["tests.gui_support"]


def _no_real_model(*_a, **_k):
    raise AssertionError("the sample called a real model")


def _guard(mp: pytest.MonkeyPatch) -> None:
    """Fail any real model client: every backend's call, and the clients behind them."""
    for name in ("_api_complete_async", "_openai_complete_async", "_agent_complete_async",
                 "_anthropic_client"):
        mp.setattr(model_client, name, _no_real_model)
    mp.setattr("watchdog.pipeline.batch_extract._client", _no_real_model, raising=False)


@pytest.fixture(scope="module")
def added(tmp_path_factory):
    root = tmp_path_factory.mktemp("sample-run")
    mp = pytest.MonkeyPatch()
    try:
        home = _home(mp, root)
        _guard(mp)
        result, _ = _create()
        vault = Path(result["path"])
        before = {
            "briefings": sorted(p.name for p in (vault / "briefings").glob("20*.md")),
            "documents": json.loads((vault / ".watchdog" / "registry" / "documents.json").read_text()),
            "entities": json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text()),
            "usage": sorted((vault / ".watchdog" / "registry" / "usage").glob("usage-*.json")),
        }
        # The app's Add documents, with nothing acknowledged and the reporter's own model settings
        # pointing at a paid cloud model: the sample ignores both.
        (home / "config.json").write_text(json.dumps({
            "projects_dir": str(root / "Investigations"), "extractor_model": "openai:gpt-6-luna",
            "finalizer_model": "claude-api:sonnet", "verify_extraction": True}))
        rep = CollectingReporter()
        out = ops.run("add", {}, rep, vault)
        yield {"root": root, "home": home, "vault": vault, "before": before, "rep": rep, "out": out}
    finally:
        telemetry_db.close()
        mp.undo()


def test_the_marker_lists_every_prepared_file(added):
    names = set(sample_op.prepared(added["vault"]).values())
    assert {"pier-9-change-order-3.pdf", "site-meeting-notes-2022-09-14.docx",
            "scanned-memo-illegible.pdf", "council-minutes-2022-02-08.pdf"} <= names
    assert len(names) == 17


def test_adding_the_waiting_files_runs_with_prepared_answers(added):
    text = added["rep"].text
    assert added["out"]["extracted"] == 2 and added["out"]["failed"] == 0, text
    assert sample_op.NOTE in text
    assert "Nothing was sent" not in text and "acknowledgement" not in text
    assert not list((added["vault"] / "incoming").glob("*.pdf"))
    docs = json.loads((added["vault"] / ".watchdog" / "registry" / "documents.json").read_text())
    names = {d["filename"] for d in docs.values()}
    assert {"pier-9-change-order-3.pdf", "site-meeting-notes-2022-09-14.docx"} <= names
    assert len(docs) == len(added["before"]["documents"]) + 2
    # The tour still has everything it points at; its Adding documents step says the files are in.
    from tests.gui_support import call
    t = call("sample.tour", vault=str(added["vault"]))
    assert t["incoming"] == [] and t["briefings"] == 3


def test_the_run_costs_nothing_and_stays_out_of_the_usage_database(added):
    usage_dir = added["vault"] / ".watchdog" / "registry" / "usage"
    new = [p for p in sorted(usage_dir.glob("usage-*.json")) if p not in added["before"]["usage"]]
    assert new
    calls = [c for p in new for c in json.loads(p.read_text())["calls"]]
    assert calls and {c["backend"] for c in calls} == {"sample"}
    assert {c["model"] for c in calls} == {"sample"}
    assert sum(c.get("cost_usd") or 0 for c in calls) == 0
    assert {"classify", "extract", "briefing"} <= {c["task"] for c in calls}
    assert not (added["root"] / "telemetry.db").exists()


def test_the_run_leaves_a_new_briefing_new_facts_and_a_new_contradiction(added):
    vault = added["vault"]
    briefings = sorted(p.name for p in (vault / "briefings").glob("20*.md"))
    assert len(briefings) == len(added["before"]["briefings"]) + 1
    newest = max((vault / "briefings").glob("20*.md"), key=lambda p: p.stat().st_mtime).read_text()
    assert "Change Order 3" in newest and "Tideway" in newest
    ents = json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())
    tideway = ents["tideway-marine-services"]
    assert any("Whether the City may pay Tideway directly" in c for c in tideway["contradictions"])
    assert len(tideway["contradictions"]) == len(added["before"]["entities"]["tideway-marine-services"]
                                                 .get("contradictions") or []) + 1
    note = (vault / "entities").rglob("tideway-marine-services.md")
    body = next(note).read_text()
    assert "asked that its next invoice go to the City directly" in body


def test_a_retried_failure_fails_again_without_a_model(added):
    rep = CollectingReporter()
    out = ops.run("add", {"retry": True}, rep, added["vault"])
    assert out["failed"] == 1 and out["extracted"] == 0, rep.text
    assert (added["vault"] / ".watchdog" / "queue" / "_failed").glob("*.json")


def test_a_file_of_the_reporters_own_is_refused_before_any_call(added, tmp_path, monkeypatch):
    from watchdog.gui import demo
    calls = []

    async def counting(self, **kw):
        calls.append(kw["task"])
        raise AssertionError("no call for a file the sample has no answer for")
    vault = added["vault"]
    queued = vault / ".watchdog" / "queue" / f"{'e' * 64}.json"
    queued.write_text(json.dumps({"filename": "my-own-records.pdf", "sha256": "e" * 64, "page_count": 1,
                                  "pages": [{"page": 1, "markdown": "Something of mine."}],
                                  "metadata": {}, "near_dup": {}}))
    try:
        monkeypatch.setattr(demo.CannedModel, "__call__", counting)
        with pytest.raises(SystemExit) as e:
            ops.run("dig", {}, CollectingReporter(), vault)
        assert sample_op.REFUSAL in str(e.value) and "my-own-records.pdf" in str(e.value)
        assert calls == []
        # The preflight says so too, before the app starts anything.
        from tests.gui_support import call
        pf = call("ingest.preflight", vault=str(vault))
        assert pf["sample"]["unprepared"] == ["my-own-records.pdf"]
        assert pf["ack_required"] is False and pf["auth"]["ok"] is True and pf["billing"] == []
    finally:
        queued.unlink()


def test_the_preflight_and_estimate_in_the_sample(added):
    from tests.gui_support import call
    pf = call("ingest.preflight", vault=str(added["vault"]))
    assert pf["ack_note"] == sample_op.NOTE and pf["models"] == [] and pf["sample"]["unprepared"] == []
    est = call("ingest.estimate", vault=str(added["vault"]), stage="dig", all_models=True)
    assert est["text"].startswith("Estimated cost: $0.00.") and "prepared answers" in est["text"]
    assert est["all_models"] is None


def test_the_recheck_in_the_sample_answers_from_the_story(added):
    from tests.gui_support import call
    vault = added["vault"]
    est = call("contradictions.estimate", vault=str(vault), all=True)
    assert est["sample"] is True and est["cost_high"] == 0 and est["auth"]["ok"] and est["billing"] == []
    rep = CollectingReporter()
    out = ops.run("recheck-contradictions", {"all": True}, rep, vault)
    assert out["error"] is None and out["calls_done"] == out["calls_total"] > 0, rep.text
    assert out["filed"] == []         # everything the story knows is already recorded
    usage_dir = vault / ".watchdog" / "registry" / "usage"
    last = json.loads(sorted(usage_dir.glob("usage-*.json"))[-1].read_text())
    assert {c["backend"] for c in last["calls"]} == {"sample"}


def test_ask_claude_is_refused_in_the_sample(added):
    from watchdog.gui import chat
    from watchdog.gui.rpc import RpcError
    with pytest.raises(RpcError) as e:
        chat.MANAGER.start(added["vault"], "ask", None, None)
    assert e.value.code == "sample" and "sample investigation" in str(e.value)
