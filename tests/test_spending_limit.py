"""An investigation's spending limit (D324): a run stops on recorded processing spend, before a
document, between sections and before each finishing stage, and resumes once it is raised."""

import asyncio
import datetime
import json
import time

import pytest

from tests.gui_support import call, call_error
from tests.test_orchestrate import (
    _checkpoint_plan_and_pf, _checkpoint_section_out, _extraction, _queue_doc,
)
from tests.test_write_vault import make_vault
from watchdog import model_client, spend
from watchdog.pipeline import orchestrate

pytest_plugins = ["tests.gui_support"]   # the rich_vault / wdg_home fixtures


def _usage_file(vault, ts, calls):
    d = vault / ".watchdog" / "registry" / "usage"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"usage-{ts}.json").write_text(json.dumps({"calls": calls, "totals": {}}))


def _call(cost, *, task="extract", auth_mode="api-key", end_ts=None):
    return {"task": task, "model": "m", "backend": "claude-api", "input_tokens": 1,
            "output_tokens": 1, "cache_read_tokens": 0, "cache_write_tokens": 0,
            "cost_usd": cost, "auth_mode": auth_mode, "end_ts": end_ts or time.time()}


def _fake_model(monkeypatch, costs, *, auth_mode="api-key", seen=None, parsed=None):
    """Every call answers like `_mock` in test_orchestrate, costing `costs[task]` (else 0)."""
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if seen is not None:
            seen.append(task)
        answer = (parsed or {}).get(task) or {
            "classify": {"skill": "general-records.md"},
            "entity-synthesis": {"entity_syntheses": []},
            "timeline-dedup": {"groups": []},
            "briefing": {"investigation_status": "Early days.", "what_was_ingested": ["x"]},
        }.get(task, _extraction())
        return model_client.ModelResult(
            parsed=answer, text="", model="m",
            backend="claude-agent-sdk" if auth_mode == "subscription" else "claude-api",
            auth_mode=auth_mode, cost_usd=costs.get(task, 0.0))
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)


def _queue_names(vault):
    return {p.name for p in (vault / ".watchdog" / "queue").glob("*.json")}


# ── the setting ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value, usd", [(25, 25.0), ("25.5", 25.5), ("$1,000", 1000.0),
                                        ("US$ 3", 3.0), ("", None), (None, None), ("  ", None)])
def test_parse_accepts_dollar_amounts_and_blank(value, usd):
    assert spend.parse_usd(value) == usd


@pytest.mark.parametrize("value", [0, -5, 0.001, "abc", "nan", "inf", True, [3], 2_000_000])
def test_parse_refuses_anything_else(value):
    with pytest.raises(ValueError):
        spend.parse_usd(value)


def test_the_limit_is_kept_beside_the_key_choice_and_reset_keeps_the_limit(tmp_path):
    vault = make_vault(tmp_path)
    settings = vault / ".watchdog" / "settings.json"
    settings.write_text(json.dumps({"schema_version": 1, "keys": {"openai": {"id": "k1", "label": "Work"}}}))
    spend.set_limit(vault, "40")
    stamp = spend.reset(vault, now=datetime.datetime(2026, 10, 24, 12, 0, tzinfo=datetime.timezone.utc))
    data = json.loads(settings.read_text())
    assert data["keys"] == {"openai": {"id": "k1", "label": "Work"}}
    assert data["spending_limit"] == {"usd": 40.0, "reset_at": "2026-10-24T12:00:00Z"} and stamp == "2026-10-24T12:00:00Z"
    spend.set_limit(vault, None)
    assert spend.limit(vault) == {"usd": None, "reset_at": "2026-10-24T12:00:00Z"}


def test_a_hand_edited_bad_value_reads_as_no_limit(tmp_path):
    vault = make_vault(tmp_path)
    (vault / ".watchdog" / "settings.json").write_text(
        json.dumps({"spending_limit": {"usd": "lots", "reset_at": "yesterday"}}))
    assert spend.limit(vault) == {"usd": None, "reset_at": None}


# ── what counts ──────────────────────────────────────────────────────────────────────────

def test_only_charged_processing_spend_after_the_reset_counts(tmp_path):
    vault = make_vault(tmp_path)
    old = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc).timestamp()
    new = datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc).timestamp()
    _usage_file(vault, "20260101T000000Z", [_call(5.0, end_ts=old), _call(1.0, end_ts=new),
                                            _call(9.0, task="reconcile", auth_mode="subscription", end_ts=new),
                                            _call(7.0, task="ask", end_ts=new)])
    # A record with no `end_ts` is dated by its file.
    rec = _call(0.5)
    rec.pop("end_ts")
    _usage_file(vault, "20260701T000000Z", [rec, _call(0.25, task="reconcile", end_ts=new)])
    # A conversation's own file is never read; a running run's partial file is.
    sessions = vault / ".watchdog" / "registry" / "usage" / "sessions"
    sessions.mkdir()
    (sessions / "usage-20260601T000000Z-s1.json").write_text(json.dumps({"calls": [_call(50.0, task="ask")]}))
    (vault / ".watchdog" / "registry" / "usage" / "usage-20260801T000000Z.partial.jsonl").write_text(
        json.dumps(_call(2.0, task="classify", end_ts=new)) + "\n")
    assert spend.recorded(vault) == pytest.approx(5.0 + 1.0 + 0.5 + 0.25 + 2.0)
    assert spend.recorded(vault, "2026-03-01T00:00:00Z") == pytest.approx(1.0 + 0.5 + 0.25 + 2.0)
    assert spend.recorded(vault, "2026-03-01T00:00:00Z", include_partial=False) == pytest.approx(1.75)


# ── a run stops at the limit ─────────────────────────────────────────────────────────────

def test_a_reached_limit_stops_before_anything_is_sent(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="aaa111", filename="one.pdf")
    _usage_file(vault, "20260101T000000Z", [_call(3.0)])
    spend.set_limit(vault, 3)
    seen = []
    _fake_model(monkeypatch, {}, seen=seen)

    summary = asyncio.run(orchestrate.run(vault))

    assert seen == []
    assert summary["cancelled"] is True and summary["extracted"] == 0
    assert "spending limit" in summary["spending_limit"] and "$3.00" in summary["spending_limit"]
    assert "Overview" in summary["spending_limit"]
    assert "post_ingest" not in summary and summary["quarantined"] == 0
    assert _queue_names(vault) == {"aaa111.json"}
    assert summary["rate_limited"] is False


def test_the_limit_stops_mid_batch_and_raising_it_resumes(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    for sha, name in (("aaa111", "one.pdf"), ("bbb222", "two.pdf"), ("ccc333", "three.pdf")):
        _queue_doc(vault, sha=sha, filename=name)
    spend.set_limit(vault, 1.0)
    seen = []
    _fake_model(monkeypatch, {"extract": 0.6}, seen=seen)

    summary = asyncio.run(orchestrate.run(vault, concurrency=1))

    # Two documents fit under $1 by recorded spend at the time each was checked; the third waits.
    assert seen.count("extract") == 2 and summary["extracted"] == 2
    assert summary["spending_limit"] and summary["cancelled"] is True
    assert "post_ingest" not in summary                      # finishing waits too
    assert orchestrate.has_pending_finalization(vault)
    assert len(_queue_names(vault)) == 3                     # staged ones keep theirs until commit
    usage = json.loads((vault / summary["usage_path"]).read_text())
    assert usage["totals"]["cost_usd"] == pytest.approx(1.2)  # the overshoot is the in-flight call

    spend.set_limit(vault, 10)
    seen.clear()
    again = asyncio.run(orchestrate.run(vault, concurrency=1))
    assert seen.count("extract") == 1 and again["extracted"] == 1 and again["skipped"] == 2
    assert not again.get("spending_limit") and again["post_ingest"].get("briefing")
    assert not orchestrate.has_pending_finalization(vault)


def test_only_spend_after_a_reset_counts_towards_the_limit(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    long_ago = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    _usage_file(vault, "20260101T000000Z", [_call(50.0, end_ts=long_ago.timestamp())])
    spend.set_limit(vault, 5)
    assert spend.status(vault)["reached"] is True
    spend.reset(vault, now=long_ago + datetime.timedelta(days=1))
    assert spend.status(vault) | {"reset_at": None} == {
        "limit_usd": 5.0, "reset_at": None, "spent_usd": 0.0, "remaining_usd": 5.0, "reached": False}
    _fake_model(monkeypatch, {"extract": 0.1})
    summary = asyncio.run(orchestrate.run(vault))
    assert summary["extracted"] == 1 and not summary.get("spending_limit")


def test_the_subscription_is_exempt(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="aaa111", filename="one.pdf")
    _queue_doc(vault, sha="bbb222", filename="two.pdf")
    spend.set_limit(vault, 1)
    _fake_model(monkeypatch, {"extract": 4.0, "classify": 1.0}, auth_mode="subscription")
    summary = asyncio.run(orchestrate.run(vault, concurrency=1))
    assert summary["extracted"] == 2 and not summary.get("spending_limit")
    assert spend.status(vault)["spent_usd"] == 0.0


def test_ask_claude_and_research_spend_does_not_count(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    sessions = vault / ".watchdog" / "registry" / "usage" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "usage-20260601T000000Z-s1.json").write_text(
        json.dumps({"calls": [_call(80.0, task="ask"), _call(20.0, task="research")]}))
    spend.set_limit(vault, 5)
    _fake_model(monkeypatch, {"extract": 0.1})
    assert asyncio.run(orchestrate.run(vault))["extracted"] == 1


def test_a_stop_between_sections_leaves_the_document_resumable(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    plan, pf = _checkpoint_plan_and_pf(vault, n=3)
    spend.set_limit(vault, 1.0)
    outs = {1: _checkpoint_section_out(1), 2: _checkpoint_section_out(2), 3: _checkpoint_section_out(3)}
    seen = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        flat = model_client._flatten_prompt(prompt)
        idx = next((i for i in (1, 2, 3) if f"Section {i} text." in flat), None)
        seen.append(idx or task)
        return model_client.ModelResult(parsed=outs.get(idx) or {"summary": "digest"}, text="",
                                        model="m", backend="claude-api", auth_mode="api-key",
                                        cost_usd=0.6)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    orchestrate._begin_usage_run(vault)
    try:
        with pytest.raises(orchestrate.SpendingLimitReached):
            asyncio.run(orchestrate._extract_sectioned(vault, "abc123", pf, "SKILL", plan, "sonnet",
                                                       "annual-report"))
    finally:
        orchestrate._end_usage_run(vault)
    assert seen == [1, 2]
    assert orchestrate._section_checkpoint_path(vault, "abc123", 1).exists()
    assert orchestrate._section_checkpoint_path(vault, "abc123", 2).exists()
    assert not orchestrate._section_checkpoint_path(vault, "abc123", 3).exists()

    # Raised: the next attempt replays sections 1 and 2 and pays only for 3 and the digest.
    spend.set_limit(vault, 100)
    seen.clear()
    orchestrate._begin_usage_run(vault)
    try:
        extraction, _pad, _cost, ok, errors, _w = asyncio.run(orchestrate._extract_sectioned(
            vault, "abc123", pf, "SKILL", plan, "sonnet", "annual-report"))
    finally:
        orchestrate._end_usage_run(vault)
    assert ok, errors
    assert seen[0] == 3 and 1 not in seen and 2 not in seen
    assert {f["fact"] for f in extraction["document"]["key_facts"]} == {"Fact 1", "Fact 2", "Fact 3"}


def test_a_section_stop_inside_a_run_keeps_the_queue_file_and_quarantines_nothing(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    spend.set_limit(vault, 1.0)
    monkeypatch.setattr(orchestrate.section, "run", lambda *a, **k: _checkpoint_plan_and_pf(vault, n=3)[0])

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            parsed = {"skill": "general-records.md"}
        else:
            parsed = _checkpoint_section_out(1)
        return model_client.ModelResult(parsed=parsed, text="", model="m", backend="claude-api",
                                        auth_mode="api-key", cost_usd=0.0 if task == "classify" else 0.7)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault))
    assert summary["spending_limit"] and summary["failed"] == 0 and summary["quarantined"] == 0
    assert _queue_names(vault) == {"abc123.json"}
    assert orchestrate._section_checkpoint_path(vault, "abc123", 2).exists()
    assert not orchestrate._section_checkpoint_path(vault, "abc123", 3).exists()


# ── finishing stages ─────────────────────────────────────────────────────────────────────

def test_finishing_stops_before_reconciliation_and_commits_nothing(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    _fake_model(monkeypatch, {"extract": 2.0})
    staged = asyncio.run(orchestrate.run(vault, skip_finalize=True))
    assert staged["extracted"] == 1 and orchestrate.has_pending_finalization(vault)

    spend.set_limit(vault, 2.0)
    out = asyncio.run(orchestrate.finalize(vault))
    assert out["spending_limit"] and out["commit_skipped"] is True
    assert json.loads((vault / ".watchdog" / "registry" / "documents.json").read_text()) == {}
    assert orchestrate.has_pending_finalization(vault)

    spend.set_limit(vault, None)
    out = asyncio.run(orchestrate.finalize(vault))
    assert not out.get("error") and out["briefing"]
    assert not orchestrate.has_pending_finalization(vault)


def test_finishing_stops_between_stages_and_the_next_run_completes_it(tmp_path, monkeypatch):
    """A stop after the commit skips every later model stage and keeps the batch's inputs, so
    the next post-processing writes the briefing."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    seen = []
    _fake_model(monkeypatch, {}, seen=seen)
    asyncio.run(orchestrate.run(vault, skip_finalize=True))
    seen.clear()

    def committed():
        return bool(json.loads((vault / ".watchdog" / "registry" / "documents.json").read_text()))
    monkeypatch.setattr(orchestrate, "_spending_stop", lambda: "Stopped at the limit." if committed() else None)
    out = asyncio.run(orchestrate.finalize(vault))
    assert committed() and out["spending_limit"] == "Stopped at the limit."
    assert out["error"] and out["briefing"] is None and "briefing" not in seen
    assert orchestrate.has_pending_finalization(vault)

    monkeypatch.setattr(orchestrate, "_spending_stop", lambda: None)
    out = asyncio.run(orchestrate.finalize(vault))
    assert out["briefing"] and "briefing" in seen
    assert not orchestrate.has_pending_finalization(vault)


def test_the_add_operation_reports_the_stop_and_exits_resumable(tmp_path, monkeypatch, capsys):
    from watchdog.ops.ingest import _print_summary, exit_code_for
    summary = {"results": [], "extracted": 1, "skipped": 0, "failed": 0, "cancelled": True,
               "rate_limited": False, "spending_limit": spend.stop_message(5.1, 5.0)}
    monkeypatch.setattr("watchdog.ops.ingest.say", print)
    _print_summary(summary)
    out = capsys.readouterr().out
    assert "Stopped at the spending limit." in out and "$5.10" in out
    assert exit_code_for(summary) == 2


# ── the app ──────────────────────────────────────────────────────────────────────────────

def test_rpc_sets_validates_and_resets_the_limit(wdg_home, rich_vault):
    st = call("spend.limit", vault=str(rich_vault))
    assert st["limit_usd"] is None and st["remaining_usd"] is None and st["reached"] is False
    assert st["sample"] is False
    st = call("spend.setLimit", vault=str(rich_vault), usd="12.5")
    assert st["limit_usd"] == 12.5 and st["remaining_usd"] == 12.5
    assert call_error("spend.setLimit", vault=str(rich_vault), usd="twelve")["code"] == "bad_value"
    assert call_error("spend.setLimit", vault=str(rich_vault), usd=0)["code"] == "bad_value"
    assert call("spend.limit", vault=str(rich_vault))["limit_usd"] == 12.5   # a refusal changes nothing

    _usage_file(rich_vault, "20260101T000000Z", [_call(20.0, end_ts=time.time() - 60)])
    st = call("spend.limit", vault=str(rich_vault))
    assert st["reached"] is True and st["remaining_usd"] == 0.0 and st["spent_usd"] == 20.0
    pf = call("ingest.preflight", vault=str(rich_vault))
    assert pf["spending_limit"]["reached"] is True and "reached its spending limit" in pf["spending_limit"]["warning"]
    st = call("spend.reset", vault=str(rich_vault))
    assert st["reset_at"] and st["spent_usd"] == 0.0 and st["reached"] is False
    assert call("ingest.preflight", vault=str(rich_vault))["spending_limit"]["warning"] is None

    st = call("spend.setLimit", vault=str(rich_vault), usd=None)
    assert st["limit_usd"] is None and st["reset_at"]                      # the reset time stays
    assert call("ingest.preflight", vault=str(rich_vault))["spending_limit"] is None
    assert call_error("spend.limit", vault=str(rich_vault / "nope"))["code"] == "not_a_vault"


def test_estimate_warning_never_blocks():
    st = {"limit_usd": 10.0, "remaining_usd": 4.0, "reached": False, "spent_usd": 6.0}
    assert spend.estimate_warning(st, 3.0) is None
    assert "$4.00 left" in spend.estimate_warning(st, 5.0)
    assert spend.estimate_warning(st, None) is None
    assert spend.estimate_warning({"limit_usd": None}, 100.0) is None
