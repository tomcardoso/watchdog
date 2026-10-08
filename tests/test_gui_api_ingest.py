"""`ingest.preflight` / `ingest.estimate` handlers."""

import json
import re

import pytest

from tests.gui_support import SHA1, call, call_error
from tests.test_write_vault import make_vault

pytest_plugins = ["tests.gui_support"]   # the rich_vault / wdg_home fixtures


def V(vault):
    return str(vault)


def credentials(home, mode, keys=None):
    (home / "credentials.json").write_text(json.dumps({"mode": mode, "keys": keys or {}}))


def config(home, **values):
    (home / "config.json").write_text(json.dumps(values))


def usage_file(vault, name="usage-20260301T000000Z", input_tokens=1000, output_tokens=200, cost=0.5):
    d = vault / ".watchdog" / "registry" / "usage"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps({"calls": [{"task": "extract", "model": "m"}],
        "totals": {"input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": cost}}))


# ── preflight ────────────────────────────────────────────────────────────────────

def test_preflight_defaults_on_subscription(rich_vault, wdg_home):
    credentials(wdg_home, "subscription")
    p = call("ingest.preflight", vault=V(rich_vault))
    assert p["documents_to_send"] == 1            # the staged one is neither re-sent nor counted
    assert (p["incoming"], p["queued"], p["staged"], p["failed"]) == (2, 2, 1, 1)
    assert p["pending_finalization"] == {"docs": 0, "entities": 0}
    assert p["auth"] == {"mode": "subscription", "ok": True, "reason": None}
    assert p["models"] == [
        {"stage": "classifier", "backend": "claude-agent-sdk", "model": "haiku", "effort": None,
         "label": "haiku"},   # Haiku takes no effort level, so the default is not applied
        {"stage": "extractor", "backend": "claude-agent-sdk", "model": "sonnet", "effort": "medium",
         "label": "sonnet"},
        {"stage": "finalizer", "backend": "claude-agent-sdk", "model": "haiku", "effort": None,
         "label": "haiku"},
    ]
    assert p["auto_approve"] == {"enabled": False, "approve": False, "blocker": None}
    json.dumps(p)


def test_preflight_warning_text_is_plain(rich_vault, wdg_home):
    credentials(wdg_home, "subscription")
    w = call("ingest.preflight", vault=V(rich_vault))["warning_text"]
    assert not re.search(r"\x1b", w)
    assert "Public records only" in w and "1 document will be sent to the model." in w
    assert not w.startswith(("\n", " "))


def test_preflight_reports_missing_auth(rich_vault, wdg_home):
    p = call("ingest.preflight", vault=V(rich_vault))
    assert p["auth"]["mode"] == "none" and p["auth"]["ok"] is False
    assert "watchdog setup" in p["auth"]["reason"]
    assert all(m["backend"] is None for m in p["models"])


def test_preflight_api_key_mode_routes_to_claude_api(rich_vault, wdg_home):
    credentials(wdg_home, "api-key", {"anthropic": "sk-ant-1234567890"})
    p = call("ingest.preflight", vault=V(rich_vault))
    assert p["auth"]["mode"] == "api-key" and p["models"][1]["backend"] == "claude-api"


def test_preflight_options_override_the_configured_models(rich_vault, wdg_home):
    credentials(wdg_home, "subscription")
    config(wdg_home, extractor_model="haiku", extractor_effort="low")
    p = call("ingest.preflight", vault=V(rich_vault), options={
        "extractor_model": "opus", "extractor_effort": "high", "finalizer_briefing_model": "sonnet",
        "classifier_model": None, "concurrency": 2})
    by_stage = {m["stage"]: m for m in p["models"]}
    assert by_stage["extractor"]["model"] == "opus" and by_stage["extractor"]["effort"] == "high"
    assert by_stage["finalizer:briefing"]["model"] == "sonnet"
    assert "finalizer:synthesis" not in by_stage
    assert by_stage["classifier"]["model"] == "haiku"


def test_preflight_reads_configured_models(rich_vault, wdg_home):
    credentials(wdg_home, "subscription")
    config(wdg_home, extractor_model="opus", finalizer_model="sonnet", finalizer_effort="high")
    by_stage = {m["stage"]: m for m in call("ingest.preflight", vault=V(rich_vault))["models"]}
    assert by_stage["extractor"]["model"] == "opus" and by_stage["finalizer"]["effort"] == "high"
    assert by_stage["finalizer"]["label"] == "sonnet"


def test_preflight_needs_no_claude_auth_when_every_stage_is_elsewhere(rich_vault, wdg_home):
    config(wdg_home, classifier_model="openai:gpt-5-mini", extractor_model="openai:gpt-5-mini",
           finalizer_model="openai:gpt-5-mini")
    p = call("ingest.preflight", vault=V(rich_vault))
    assert p["auth"] == {"mode": None, "ok": True, "reason": None}
    assert {m["backend"] for m in p["models"]} == {"openai"}
    assert p["models"][1]["label"] == "openai:gpt-5-mini"


def test_preflight_auto_approve(rich_vault, wdg_home):
    credentials(wdg_home, "subscription")
    config(wdg_home, auto_approve=True)
    assert call("ingest.preflight", vault=V(rich_vault))["auto_approve"] == {
        "enabled": True, "approve": True, "blocker": None}
    config(wdg_home, auto_approve=True, extractor_model="openai:gpt-5-mini")
    a = call("ingest.preflight", vault=V(rich_vault))["auto_approve"]
    assert a["enabled"] is True and a["approve"] is False and "paid API key" in a["blocker"]
    credentials(wdg_home, "api-key", {"anthropic": "sk-ant-1234567890"})
    config(wdg_home, auto_approve=True)
    assert call("ingest.preflight", vault=V(rich_vault))["auto_approve"]["approve"] is False


def test_preflight_auto_approve_with_nothing_to_send(wdg_home, tmp_path):
    credentials(wdg_home, "subscription")
    config(wdg_home, auto_approve=True)
    p = call("ingest.preflight", vault=V(make_vault(tmp_path)))
    assert p["documents_to_send"] == 0
    assert p["auto_approve"] == {"enabled": True, "approve": False, "blocker": None}
    assert "0 documents will be sent" in p["warning_text"]


def test_preflight_force_and_limit(rich_vault, wdg_home):
    credentials(wdg_home, "subscription")
    assert call("ingest.preflight", vault=V(rich_vault), options={"force": True})["documents_to_send"] == 2
    assert call("ingest.preflight", vault=V(rich_vault), options={"limit": 1})["documents_to_send"] == 1
    assert call_error("ingest.preflight", vault=V(rich_vault), options={"limit": "many"})["code"] == "bad_params"


@pytest.mark.parametrize("options, text", [
    ({"extractor_model": "nonsense"}, "unknown model"),
    ({"classifier_model": "wat:thing"}, "unknown backend"),
    ({"extractor_effort": "ludicrous"}, "unknown effort"),
])
def test_preflight_rejects_bad_options_with_the_cli_message(rich_vault, wdg_home, options, text):
    credentials(wdg_home, "subscription")
    err = call_error("ingest.preflight", vault=V(rich_vault), options=options)
    assert text in err["message"] and not err["message"].startswith("Error")


def test_preflight_requires_a_vault(tmp_path):
    assert call_error("ingest.preflight", vault=str(tmp_path))["code"] == "not_a_vault"


# ── estimate ─────────────────────────────────────────────────────────────────────

def test_estimate_dig(rich_vault, wdg_home):
    credentials(wdg_home, "subscription")
    r = call("ingest.estimate", vault=V(rich_vault), stage="dig")
    assert r["estimate"]["documents"] == 2 and r["estimate"]["subscription"] is True
    assert r["estimate"]["cost_low"] is None
    assert re.fullmatch(r"2 documents · ~8 pages · est\. ~\d+ tokens in", r["text"])
    assert r["all_models"] is None


def test_estimate_dig_with_history_prices_a_metered_run(rich_vault, wdg_home):
    credentials(wdg_home, "api-key", {"anthropic": "sk-ant-1234567890"})
    usage_file(rich_vault)
    r = call("ingest.estimate", vault=V(rich_vault), stage="dig")
    assert r["estimate"]["cost_low"] is not None and "based on your last run" in r["text"]


def test_estimate_all_models_without_history(rich_vault, wdg_home):
    credentials(wdg_home, "subscription")
    r = call("ingest.estimate", vault=V(rich_vault), stage="dig", all_models=True)
    assert r["all_models"] == [] and "Not enough usage history" in r["text"]


def test_estimate_all_models_with_history(rich_vault, wdg_home):
    credentials(wdg_home, "subscription")
    usage_file(rich_vault)
    r = call("ingest.estimate", vault=V(rich_vault), stage="dig", all_models=True)
    rows = r["all_models"]
    assert rows and {"label", "provider", "cost_usd", "note"} == set(rows[0])
    assert [x["cost_usd"] for x in rows] == sorted(x["cost_usd"] for x in rows)
    assert "Projected list price by model" in r["text"] and rows[0]["label"] in r["text"]
    assert not re.search(r"\x1b", r["text"])


def test_estimate_honours_the_extractor_option(rich_vault, wdg_home):
    credentials(wdg_home, "subscription")
    config(wdg_home, extractor_model="openai:gpt-5-mini")
    assert call("ingest.estimate", vault=V(rich_vault), stage="dig")["estimate"]["subscription"] is False
    assert call("ingest.estimate", vault=V(rich_vault), stage="dig", options={
        "extractor_model": "sonnet"})["estimate"]["subscription"] is True
    assert call("ingest.estimate", vault=V(rich_vault), stage="dig", options={"limit": 1})[
        "estimate"]["documents"] == 1


def test_estimate_empty_queue(wdg_home, tmp_path):
    credentials(wdg_home, "subscription")
    r = call("ingest.estimate", vault=V(make_vault(tmp_path)), stage="dig")
    assert r == {"text": "The queue is empty — nothing to estimate.", "estimate": None, "all_models": None}


def test_estimate_empty_queue_with_quarantined_documents(rich_vault, wdg_home):
    credentials(wdg_home, "subscription")
    for f in (rich_vault / ".watchdog" / "queue").glob("*.json"):
        f.unlink()
    r = call("ingest.estimate", vault=V(rich_vault), stage="dig")
    assert "1 document need attention" in r["text"] and "queue/_failed/" in r["text"]
    assert r["estimate"] is None


def test_estimate_bark(rich_vault, wdg_home):
    credentials(wdg_home, "subscription")
    tmp = rich_vault / ".watchdog" / "tmp"
    tmp.mkdir(exist_ok=True)
    (tmp / f"result_{SHA1}.json").write_text(json.dumps({"x": "y" * 400}))
    r = call("ingest.estimate", vault=V(rich_vault), stage="bark", all_models=True)
    assert r["estimate"]["docs"] == 1 and "1 document staged" in r["text"]
    assert r["all_models"] == [] and "Not enough usage history" in r["text"]


def test_estimate_bark_with_nothing_staged(wdg_home, tmp_path):
    credentials(wdg_home, "subscription")
    r = call("ingest.estimate", vault=V(make_vault(tmp_path)), stage="bark")
    assert r == {"text": "Nothing to finish — run processing first.", "estimate": None, "all_models": None}


def test_estimate_rejects_an_unknown_stage(rich_vault, wdg_home):
    assert call_error("ingest.estimate", vault=V(rich_vault), stage="chew")["code"] == "bad_params"
