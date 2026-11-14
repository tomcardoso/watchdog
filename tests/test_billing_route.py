"""Billing goes only where the reporter chose (D408): an investigation's chosen key beats a key in
the environment, nothing in the environment outranks the key a call is given, and a call stopped
on extra usage keeps the subscription off in that investigation until the reporter has looked."""

import asyncio
import json
import sqlite3

import pytest

from watchdog import auth, model_client as mc, spend, telemetry_db


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    monkeypatch.setattr("watchdog.config.HOME", h)
    return h


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "case"
    (v / ".watchdog" / "registry").mkdir(parents=True)
    return v


# ── the extra-usage gate ──────────────────────────────────────────────────────────────────

def test_a_flagged_stop_counts_calls_until_acknowledged(vault):
    assert spend.extra_usage_flag(vault) is None
    spend.flag_extra_usage(vault)
    spend.flag_extra_usage(vault)
    flag = spend.extra_usage_flag(vault)
    assert flag["calls"] == 2 and flag["since"]
    assert "2 Claude calls" in spend.extra_usage_message(flag)
    auth.write_investigation_settings(vault, {**auth.read_investigation_settings(vault),
                                              "spending_limit": {"usd": 5.0}})
    spend.acknowledge_extra_usage(vault)
    assert spend.extra_usage_flag(vault) is None
    assert spend.limit(vault)["usd"] == 5.0          # acknowledging keeps the other settings


def test_the_subscription_waits_for_the_reporter_after_a_stop(home, vault):
    assert auth.subscription_check("this run", vault) == (None, "claude.ai")
    spend.flag_extra_usage(vault)
    problem, _ = auth.subscription_check("this run", vault)
    assert "Activity → Usage" in problem
    assert auth.subscription_check("this run", None)[0] is None     # another investigation runs
    spend.acknowledge_extra_usage(vault)
    assert auth.subscription_check("this run", vault)[0] is None


def test_a_run_stopped_on_extra_usage_holds_the_next_one(home, vault, monkeypatch):
    """The stopped call raises the flag, and a later run in the same investigation is refused
    before its first call, in its own process (no `_metered_stop` carried over)."""
    import sys
    import types
    from watchdog.pipeline import orchestrate
    auth.save_state({"mode": "subscription"})
    info = type("RateLimitInfo", (), {"status": "allowed", "rate_limit_type": None,
                                      "resets_at": None, "raw": {"isUsingOverage": True}})()
    stream = [type("SystemMessage", (), {"subtype": "init", "data": {"apiKeySource": "none"},
                                         "content": None})(),
              type("RateLimitEvent", (), {"rate_limit_info": info, "content": None})()]
    seen = []

    async def fake_query(prompt, options):
        for m in stream:
            seen.append(m)
            yield m
    sdk = types.ModuleType("claude_agent_sdk")
    sdk.query, sdk.ClaudeAgentOptions = fake_query, (lambda **kw: kw)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    mc._agent_supports_tools.cache_clear()
    monkeypatch.setattr(mc, "_metered_stop", None)
    orchestrate._begin_usage_run(vault)
    try:
        with pytest.raises(mc.MeteredBillingError):
            asyncio.run(orchestrate._call_model(task="extract", prompt="p", schema={}))
    finally:
        orchestrate._end_usage_run(vault)
    assert spend.extra_usage_flag(vault)["calls"] == 1
    monkeypatch.setattr(mc, "_metered_stop", None)      # a new worker process
    seen.clear()
    auth.use_investigation(vault)
    try:
        with pytest.raises(mc.MeteredBillingError, match="Activity → Usage"):
            asyncio.run(mc._agent_complete_async("p", "model", {}, None))
    finally:
        auth.use_investigation(None)
    assert seen == []


def test_acknowledging_from_usage_lets_the_subscription_run_again(home, vault):
    from tests.gui_support import call
    spend.flag_extra_usage(vault)
    assert call("spend.acknowledgeExtraUsage", vault=str(vault)) == {"extra_usage_pending": False}
    assert spend.extra_usage_flag(vault) is None


# ── keys ──────────────────────────────────────────────────────────────────────────────────

def test_the_chosen_anthropic_key_is_handed_to_a_job_even_with_one_in_the_environment(home, tmp_path, monkeypatch):
    import base64
    from watchdog import keystore
    chosen = "sk-ant-api03-chosen-1111"
    blob = {"enc": keystore.ENC_TAG, "data": base64.b64encode(chosen[::-1].encode()).decode()}
    auth.save_state({"mode": "api-key", "keys": {"anthropic": {
        "default": "k1", "items": [{"id": "k1", "label": "Work", "key": {**blob, "masked": "sk-…1111"}}]}}})
    keystore.provide({blob["data"]: chosen}, store="encrypted", backend="keychain")
    v = tmp_path / "v"
    (v / ".watchdog").mkdir(parents=True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-0000")
    assert auth.secrets_for_run(v, ["anthropic"]) == {}          # no choice: the environment pays
    auth.choose_key(v, "anthropic", "k1")
    assert list(auth.secrets_for_run(v, ["anthropic"]).values()) == [chosen]
    assert auth.resolve_key("anthropic", v)["key"] == chosen


@pytest.mark.parametrize("name", auth.KEY_ENV_CONFLICTS)
def test_every_variable_that_outranks_a_key_is_named_before_a_run(home, name, monkeypatch):
    monkeypatch.setenv(name, "1")
    assert "ANTHROPIC_API_KEY" not in auth.KEY_ENV_CONFLICTS
    assert name in auth.billing_problem("api-key", None)
    monkeypatch.delenv(name)
    assert auth.billing_problem("api-key", None) is None


# ── what Add documents shows before a run ───────────────────────────────────────────────────

def test_billing_problem_covers_the_sign_in_and_the_gate(home, vault, monkeypatch):
    assert auth.billing_problem("subscription", vault) is None
    monkeypatch.setattr(auth, "_run_auth_status", lambda: {"loggedIn": True, "authMethod": "oauth_token"})
    monkeypatch.setattr(auth, "_auth_status_cache", None)
    assert "Console account" in auth.billing_problem("subscription", vault)
    monkeypatch.setattr(auth, "_run_auth_status", lambda: {"loggedIn": True, "authMethod": "claude.ai"})
    monkeypatch.setattr(auth, "_auth_status_cache", None)
    spend.flag_extra_usage(vault)
    assert "Activity → Usage" in auth.billing_problem("subscription", vault)
    assert auth.billing_problem("api-key", vault) is None       # the gate is the subscription's
    assert auth.billing_problem(None, vault) is None


# ── telemetry ─────────────────────────────────────────────────────────────────────────────

def test_telemetry_keeps_how_a_claude_call_was_billed(tmp_path, monkeypatch):
    db = tmp_path / "telemetry.db"
    monkeypatch.setattr(telemetry_db, "DB_PATH", db)
    v = tmp_path / "v"
    v.mkdir()
    record = {"task": "extract", "model": "claude-haiku-5-5", "backend": "claude-agent-sdk",
              "auth_mode": "subscription", "input_tokens": 1, "output_tokens": 1,
              "cache_read_tokens": 0, "cache_write_tokens": 0, "key_source": "none",
              "auth_method": "claude.ai", "overage": True}
    telemetry_db.record_call(record, vault=v, run_id="r1", benchmark_arm_id=None,
                             prompt_hash=None, config_snapshot=None)
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT key_source, auth_method, overage FROM calls").fetchone()
    finally:
        conn.close()
    assert row == ("none", "claude.ai", 1)
    json.dumps(record)


# ── what the backend keeps of its environment (D409) ───────────────────────────────────────

def test_under_the_app_the_backend_drops_every_provider_credential(monkeypatch):
    from watchdog import environment
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR", "OPENAI_API_KEY",
                 "OPENAI_BASE_URL", "DEEPSEEK_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
                 "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "LOCAL_API_KEY", "LOCAL_BASE_URL",
                 "AWS_PROFILE", "AZURE_OPENAI_API_KEY", "HF_TOKEN"):
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:8080")
    monkeypatch.delenv(environment.APP_MARKER, raising=False)
    assert environment.scrub() == []                       # a benchmark script keeps them
    monkeypatch.setenv(environment.APP_MARKER, "1")
    gone = environment.scrub()
    assert "ANTHROPIC_API_KEY" in gone and "LOCAL_BASE_URL" in gone and "HF_TOKEN" in gone
    import os
    assert not [k for k in os.environ if environment.scrubbed(k)]
    assert os.environ["HTTPS_PROXY"] == "http://proxy:8080"
    assert auth.resolve_key("openai")["source"] == "none"

