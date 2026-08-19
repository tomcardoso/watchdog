"""Tests for ModelClient — backend routing, JSON extraction, schema validation,
tier escalation on retry, telemetry. The two SDK backends are mocked."""

import asyncio
import importlib.resources
import json

import pytest

from watchdog import fixture_capture as fc
from watchdog import model_client as mc
from watchdog.pipeline import prompts


SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
    "additionalProperties": False,
}


class FakeBackend:
    """Records calls; returns queued outputs in order."""
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def __call__(self, prompt, model_id, schema, api_key, max_tokens, effort=None,
                       prefix=None, base_url=None):
        self.calls.append({"model_id": model_id, "api_key": api_key,
                           "prompt": prompt, "max_tokens": max_tokens, "effort": effort,
                           "base_url": base_url})
        return self.outputs.pop(0)


def _out(text, cost=0.01):
    return {"text": text, "usage": {"input_tokens": 10}, "cost_usd": cost}


@pytest.fixture
def api_key_auth(monkeypatch):
    monkeypatch.setattr(mc.auth, "resolve_auth", lambda *a, **k: {"mode": "api-key", "key": "sk-ant-x"})


@pytest.fixture
def subscription_auth(monkeypatch):
    monkeypatch.setattr(mc.auth, "resolve_auth", lambda *a, **k: {"mode": "subscription"})


# ── routing ───────────────────────────────────────────────────────────────────

def test_api_key_mode_routes_to_claude_api(api_key_auth, monkeypatch):
    api = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    r = mc.complete_json(task="t", prompt="p", schema=SCHEMA)
    assert r.backend == "claude-api"
    assert r.parsed == {"name": "Acme"}
    assert api.calls[0]["api_key"] == "sk-ant-x"


def test_subscription_mode_routes_to_agent_sdk(subscription_auth, monkeypatch):
    agent = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-agent-sdk", agent)
    r = mc.complete_json(task="t", prompt="p", schema=SCHEMA)
    assert r.backend == "claude-agent-sdk"
    assert agent.calls[0]["api_key"] is None      # subscription → no key passed


def test_explicit_backend_override(api_key_auth, monkeypatch):
    agent = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-agent-sdk", agent)
    r = mc.complete_json(task="t", prompt="p", schema=SCHEMA, backend="claude-agent-sdk")
    assert r.backend == "claude-agent-sdk"


def test_claude_api_without_key_errors(subscription_auth):
    with pytest.raises(mc.ModelError, match="needs an API key"):
        mc.complete_json(task="t", prompt="p", schema=SCHEMA, backend="claude-api")


def test_no_auth_errors(monkeypatch):
    monkeypatch.setattr(mc.auth, "resolve_auth",
                        lambda *a, **k: {"mode": "none", "reason": "run setup"})
    with pytest.raises(mc.ModelError, match="run setup"):
        mc.complete_json(task="t", prompt="p", schema=SCHEMA)


# ── validation, retry, escalation ─────────────────────────────────────────────

def test_invalid_then_valid_retries_same_model(api_key_auth, monkeypatch):
    # haiku requested; first output bad JSON, second valid — retry stays on haiku (no escalation)
    api = FakeBackend(_out("not json"), _out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    r = mc.complete_json(task="t", prompt="p", schema=SCHEMA, model="haiku")
    assert r.parsed == {"name": "Acme"}
    assert r.attempts == 2
    assert api.calls[0]["model_id"] == api.calls[1]["model_id"] == mc._MODEL_IDS["haiku"]


def test_schema_violation_then_valid(api_key_auth, monkeypatch):
    api = FakeBackend(_out('{"wrong": 1}'), _out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    r = mc.complete_json(task="t", prompt="p", schema=SCHEMA, model="sonnet")
    assert r.parsed == {"name": "Acme"} and r.attempts == 2


def test_fails_after_retries(api_key_auth, monkeypatch):
    api = FakeBackend(_out("nope"), _out("still nope"))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    with pytest.raises(mc.ModelError, match="failed JSON validation after 2"):
        mc.complete_json(task="t", prompt="p", schema=SCHEMA, max_retries=1)


# ── usage telemetry for retried/failed calls (#412/D125) ─────────────────────

def test_usage_merges_across_attempts_on_success(api_key_auth, monkeypatch):
    """A retried call's usage is the SUM over every attempt, not just the last one — the
    failed first attempt still spent real tokens."""
    api = FakeBackend(_out("not json"), _out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    r = mc.complete_json(task="t", prompt="p", schema=SCHEMA)
    assert r.usage == {"input_tokens": 20}   # 10 + 10, both attempts' usage


def test_model_error_carries_merged_usage_cost_and_attempts(api_key_auth, monkeypatch):
    """When every attempt fails, the raised ModelError still carries the aggregate usage/cost/
    attempts/model/backend/auth_mode — a failed call's spend must not vanish from telemetry."""
    api = FakeBackend(_out("nope", cost=0.02), _out("still nope", cost=0.03))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    with pytest.raises(mc.ModelError) as excinfo:
        mc.complete_json(task="t", prompt="p", schema=SCHEMA, max_retries=1)
    err = excinfo.value
    assert err.usage == {"input_tokens": 20}
    assert err.cost_usd == pytest.approx(0.05)
    assert err.attempts == 2
    assert err.backend == "claude-api"
    assert err.auth_mode == "api-key"


# ── prune-and-log unknown JSON keys (#412/D124) ───────────────────────────────

_ROLE_SCHEMA = {
    "type": "object",
    "properties": {"relationship": {"type": "string"}, "target_id": {"type": "string"}},
    "required": ["relationship", "target_id"],
    "additionalProperties": False,
}
_ENTITY_SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "string"}, "roles": {"type": "array", "items": _ROLE_SCHEMA}},
    "required": ["id"],
    "additionalProperties": False,
}
_NESTED_SCHEMA = {
    "type": "object",
    "properties": {"entities": {"type": "array", "items": _ENTITY_SCHEMA}},
    "required": ["entities"],
    "additionalProperties": False,
}


def test_acomplete_json_prunes_top_level_extra_key_and_succeeds(api_key_auth, monkeypatch):
    api = FakeBackend(_out('{"name": "Acme", "extra_field": "nope"}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    r = mc.complete_json(task="t", prompt="p", schema=SCHEMA)
    assert r.parsed == {"name": "Acme"}
    assert r.attempts == 1
    assert r.pruned == ["extra_field"]


def test_acomplete_json_prunes_key_nested_in_array_item(api_key_auth, monkeypatch):
    payload = ('{"entities": [{"id": "e1", "roles": [{"relationship": "ceo", '
              '"target_id": "x", "date": "2020"}]}]}')
    api = FakeBackend(_out(payload))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    r = mc.complete_json(task="t", prompt="p", schema=_NESTED_SCHEMA)
    assert r.pruned == ["entities[0].roles[0].date"]
    assert "date" not in r.parsed["entities"][0]["roles"][0]


def test_prune_does_not_rescue_missing_required_field(api_key_auth, monkeypatch):
    # "name" is required but absent — pruning an unrelated extra key must not paper over it.
    api = FakeBackend(_out('{"extra_field": "nope"}'), _out('{"extra_field": "still nope"}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    with pytest.raises(mc.ModelError):
        mc.complete_json(task="t", prompt="p", schema=SCHEMA, max_retries=1)


def test_prune_does_not_rescue_wrong_typed_field(api_key_auth, monkeypatch):
    api = FakeBackend(_out('{"name": 123}'), _out('{"name": 456}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    with pytest.raises(mc.ModelError):
        mc.complete_json(task="t", prompt="p", schema=SCHEMA, max_retries=1)


def test_raw_model_id_does_not_escalate(api_key_auth, monkeypatch):
    api = FakeBackend(_out("bad"), _out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    mc.complete_json(task="t", prompt="p", schema=SCHEMA, model="claude-sonnet-4-6")
    assert api.calls[0]["model_id"] == api.calls[1]["model_id"] == "claude-sonnet-4-6"


def test_cost_accumulates_across_attempts(api_key_auth, monkeypatch):
    api = FakeBackend(_out("bad", cost=0.02), _out('{"name": "Acme"}', cost=0.03))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    r = mc.complete_json(task="t", prompt="p", schema=SCHEMA)
    assert r.cost_usd == pytest.approx(0.05)


@pytest.mark.parametrize("task", ["extract", "classify", "verify", "briefing", "other-task"])
def test_wire_max_tokens_is_task_independent(api_key_auth, monkeypatch, task):
    # #598: `max_tokens` is derived from the model's catalogued envelope alone — no per-task
    # override, unlike the old `_TASK_MAX_TOKENS` table.
    api = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    mc.complete_json(task=task, prompt="p", schema=SCHEMA)
    assert api.calls[0]["max_tokens"] == mc._wire_max_tokens("claude-api", "claude-sonnet-4-6")


# ── effort knob (D29) ─────────────────────────────────────────────────────────

def test_effort_passed_to_backend_for_sonnet(api_key_auth, monkeypatch):
    api = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    mc.complete_json(task="extract", prompt="p", schema=SCHEMA, model="sonnet", effort="low")
    assert api.calls[0]["effort"] == "low"


def test_effort_high_is_treated_as_no_override(api_key_auth, monkeypatch):
    # `high` is the model default — sending nothing preserves current behaviour.
    api = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    mc.complete_json(task="extract", prompt="p", schema=SCHEMA, model="sonnet", effort="high")
    assert api.calls[0]["effort"] is None


def test_effort_rejected_for_haiku(api_key_auth, monkeypatch):
    # Haiku rejects output_config.effort (400) — requesting any level errors rather than
    # silently sending nothing, so a misconfigured stage is caught instead of running unnoticed.
    api = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    with pytest.raises(mc.ModelError, match="low"):
        mc.complete_json(task="classify", prompt="p", schema=SCHEMA, model="haiku", effort="low")


def test_effort_omitted_when_unset(api_key_auth, monkeypatch):
    api = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    mc.complete_json(task="extract", prompt="p", schema=SCHEMA, model="sonnet")
    assert api.calls[0]["effort"] is None


@pytest.mark.parametrize("provider,model_id,effort,expected", [
    ("anthropic", "claude-sonnet-4-6", "low", "low"),
    ("anthropic", "claude-opus-4-8", "medium", "medium"),
    ("anthropic", "claude-sonnet-4-6", "high", None),    # Claude: high ≡ default
    ("anthropic", "claude-sonnet-4-6", None, None),      # unset
    ("openai", "gpt-5-mini", "low", "low"),              # OpenAI reasoning model → pass through
    ("openai", "gpt-5", "high", "high"),                 # OpenAI: high is NOT a no-op default
    ("gemini", "gemini-2.5-flash", "low", "low"),     # Gemini: every model passes through
    ("gemini", "gemini-2.5-pro", "high", "high"),
    # xhigh/max (#518): OpenAI reasoning models pass them through like any other level.
    ("openai", "gpt-5.6-luna", "xhigh", "xhigh"),
    ("openai", "gpt-5.6-terra", "max", "max"),
    # Claude coverage is per-model (Anthropic's own effort docs), not a flat yes/no: Sonnet 4.6
    # takes `max` but not `xhigh`; Opus 4.8 takes both.
    ("anthropic", "claude-sonnet-4-6", "max", "max"),
    ("anthropic", "claude-opus-4-8", "max", "max"),
    ("anthropic", "claude-opus-4-8", "xhigh", "xhigh"),
    # Sonnet 5 (#361/#509, D165) accepts xhigh unlike Sonnet 4.6 — additive catalog entry, not a
    # tier-wide capability change.
    ("anthropic", "claude-sonnet-5", "xhigh", "xhigh"),
    ("anthropic", "claude-sonnet-5", "high", None),   # Claude: high ≡ default, same as any model
])
def test_resolve_effort(provider, model_id, effort, expected):
    assert mc._resolve_effort(provider, model_id, effort) == expected


# ── Opus 5 catalog entry (#635) — additive alongside Opus 4.8, same pattern as Sonnet 5 ────────

def test_opus_5_tier_resolves():
    assert mc.resolve_model_id("opus-5") == "claude-opus-5"
    assert mc.resolve_model_id("opus") == "claude-opus-4-8"   # bare `opus` still means 4.8


def test_opus_5_effort_levels_match_opus_4_8():
    assert mc._effort_levels("anthropic", "claude-opus-5") == \
        mc._effort_levels("anthropic", "claude-opus-4-8") == \
        {"low", "medium", "high", "xhigh", "max"}


def test_opus_5_pricing_matches_opus_4_8():
    assert mc._PRICING["claude-opus-5"] == mc._PRICING["claude-opus-4-8"]


@pytest.mark.parametrize("model_id,needs_thinking_param", [
    ("claude-sonnet-4-6", True),
    ("claude-opus-4-8", True),
    ("claude-sonnet-5", False),    # ships on by default — no param needed
    ("claude-opus-5", False),      # ships on by default — no param needed
    ("claude-haiku-4-5", False),   # no thinking control at all
    ("gpt-5", False),              # non-Claude — never consulted
    ("not-a-real-model", False),   # uncatalogued — correctness-safe default
])
def test_catalog_needs_thinking_param(model_id, needs_thinking_param):
    assert mc.catalog_needs_thinking_param(model_id) is needs_thinking_param


@pytest.mark.parametrize("model_id,has_reasoning", [
    ("claude-sonnet-4-6", True),   # thinking sent explicitly (#635)
    ("claude-opus-4-8", True),     # thinking sent explicitly (#635)
    ("claude-sonnet-5", True),     # thinking on by default
    ("claude-opus-5", True),       # thinking on by default
    ("claude-haiku-4-5", False),   # no thinking control at all
    ("gpt-5.6-luna", True),        # OpenAI reasoning model
    ("gpt-5.4-nano", True),        # OpenAI reasoning model
    ("deepseek-v4-flash", False),  # no reasoning field in the catalog
    ("gemini-3.5-flash", False),   # no reasoning field in the catalog
    ("not-a-real-model", False),   # uncatalogued — never assume a channel that isn't confirmed
])
def test_catalog_has_reasoning(model_id, has_reasoning):
    from watchdog.model_catalog import catalog_has_reasoning
    assert catalog_has_reasoning(model_id) is has_reasoning


# ── unsupported effort requests fail loud, at any level (#518, D158) ──────────
# `model_catalog.yaml`'s `effort_levels` is authoritative for every provider now, not just a
# per-provider "supports effort at all" flag — so a request for a level a model doesn't accept
# always raises, whether that's a brand-new level (xhigh/max) or one of the original three.

@pytest.mark.parametrize("provider,model_id,effort", [
    ("anthropic", "claude-sonnet-4-6", "xhigh"),   # Sonnet 4.6 takes max, not xhigh
    ("anthropic", "claude-haiku-4-5", "max"),      # Haiku has no effort control at all
    ("anthropic", "claude-haiku-4-5", "low"),      # ...at any level, not just xhigh/max
    ("gemini", "gemini-2.5-pro", "xhigh"),
    ("deepseek", "deepseek-v4-pro", "max"),
    ("deepseek", "deepseek-v4-pro", "high"),       # DeepSeek has no portable knob at all
    ("local", "llama-3.3-70b", "xhigh"),
    ("local", "llama-3.3-70b", "low"),             # local/openrouter: no capability info, ever
    ("openrouter", "anthropic/claude-3.5-sonnet", "max"),
    ("openai", "gpt-4o", "xhigh"),       # OpenAI, but a chat model, not a reasoning one
    ("openai", "gpt-4o", "low"),         # ...at any level
])
def test_resolve_effort_rejects_unsupported_levels(provider, model_id, effort):
    with pytest.raises(mc.ModelError, match=effort):
        mc._resolve_effort(provider, model_id, effort)


def test_openai_reasoning_model_accepts_xhigh(openai_key, monkeypatch):
    be = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "openai", be)
    mc.complete_json(task="t", prompt="p", schema=SCHEMA, backend="openai", model="gpt-5.6-luna", effort="xhigh")
    assert be.calls[0]["effort"] == "xhigh"


def test_openai_chat_model_rejects_max(openai_key, monkeypatch):
    be = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "openai", be)
    with pytest.raises(mc.ModelError, match="max"):
        mc.complete_json(task="t", prompt="p", schema=SCHEMA, backend="openai", model="gpt-4o", effort="max")


def test_claude_sonnet_rejects_xhigh(api_key_auth, monkeypatch):
    # Sonnet 4.6 takes `max` but Anthropic hasn't extended `xhigh` coverage to it yet.
    api = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    with pytest.raises(mc.ModelError, match="xhigh"):
        mc.complete_json(task="extract", prompt="p", schema=SCHEMA, model="sonnet", effort="xhigh")


def test_claude_sonnet_accepts_max(api_key_auth, monkeypatch):
    api = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    mc.complete_json(task="extract", prompt="p", schema=SCHEMA, model="sonnet", effort="max")
    assert api.calls[0]["effort"] == "max"


def test_claude_opus_accepts_xhigh(api_key_auth, monkeypatch):
    api = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    mc.complete_json(task="extract", prompt="p", schema=SCHEMA, model="opus", effort="xhigh")
    assert api.calls[0]["effort"] == "xhigh"


def test_claude_sonnet5_accepts_xhigh_via_tier_name(api_key_auth, monkeypatch):
    # End-to-end through the `sonnet-5` tier alias (#361/#509, D165) — not just _resolve_effort
    # in isolation. Sonnet 4.6 rejects xhigh (test_claude_sonnet_rejects_xhigh above); Sonnet 5
    # accepts it, confirming the new catalog entry resolves and its effort_levels take effect.
    api = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    mc.complete_json(task="extract", prompt="p", schema=SCHEMA, model="sonnet-5", effort="xhigh")
    assert api.calls[0]["effort"] == "xhigh"
    assert api.calls[0]["model_id"] == "claude-sonnet-5"


def test_gemini_rejects_max(gemini_key, monkeypatch):
    be = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "gemini", be)
    with pytest.raises(mc.ModelError, match="max"):
        mc.complete_json(task="t", prompt="p", schema=SCHEMA, backend="gemini", model="gemini-2.5-pro", effort="max")


def test_deepseek_rejects_xhigh(monkeypatch):
    monkeypatch.setattr(mc.auth, "get_api_key",
                        lambda provider="anthropic": "sk-ds" if provider == "deepseek" else None)
    be = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "deepseek", be)
    with pytest.raises(mc.ModelError, match="xhigh"):
        mc.complete_json(task="t", prompt="p", schema=SCHEMA, backend="deepseek",
                         model="deepseek-reasoner", effort="xhigh")


# ── context windows (provider-aware sectioning, #321) ─────────────────────────

@pytest.mark.parametrize("model, window", [
    ("sonnet", 200_000),                        # tier → claude
    ("opus", 200_000),
    ("haiku", 200_000),
    (None, 200_000),                            # default tier (sonnet)
    ("deepseek-v4-flash", 1_000_000),
    ("deepseek-v4-flash-thinking", 1_000_000),  # -thinking marker still matches deepseek-v4
    ("deepseek-v4-pro", 1_000_000),
    ("deepseek-chat", 128_000),                 # legacy id → deepseek fallback, not v4
    ("gemini-2.5-flash", 1_000_000),
    ("gemini-2.5-flash-lite", 1_000_000),
    ("gemini-2.5-pro", 1_000_000),
    ("gemini-3.5-flash", 1_000_000),
    ("gemini-3.1-flash-lite", 1_000_000),
    ("gemini-3.1-pro-preview", 1_000_000),
    ("gpt-5-mini", 400_000),
    ("gpt-4o", 128_000),
    ("some-unknown-model", 128_000),            # conservative default for anything unlisted
])
def test_context_window(model, window):
    assert mc.context_window(model) == window


# ── OpenAI-compatible backends (#125) ──────────────────────────────────────────

@pytest.fixture
def openai_key(monkeypatch):
    monkeypatch.setattr(mc.auth, "get_api_key",
                        lambda provider="anthropic": "sk-openai-x" if provider == "openai" else None)


def test_openai_backend_routes_with_stored_key(openai_key, monkeypatch):
    be = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "openai", be)
    r = mc.complete_json(task="t", prompt="p", schema=SCHEMA, backend="openai", model="gpt-4o")
    assert r.backend == "openai"
    assert be.calls[0]["api_key"] == "sk-openai-x"     # uses the provider key, not Claude auth


def test_openai_backend_without_key_errors(monkeypatch):
    monkeypatch.setattr(mc.auth, "get_api_key", lambda provider="anthropic": None)
    with pytest.raises(mc.ModelError, match="watchdog auth"):
        mc.complete_json(task="t", prompt="p", schema=SCHEMA, backend="openai")


def test_openai_effort_passed_for_reasoning_model(openai_key, monkeypatch):
    be = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "openai", be)
    mc.complete_json(task="t", prompt="p", schema=SCHEMA, backend="openai", model="gpt-5-mini", effort="low")
    assert be.calls[0]["effort"] == "low"


def test_openai_effort_rejected_for_chat_model(openai_key, monkeypatch):
    be = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "openai", be)
    # a chat model can't take reasoning_effort at all → errors rather than running silently
    with pytest.raises(mc.ModelError, match="high"):
        mc.complete_json(task="t", prompt="p", schema=SCHEMA, backend="openai", model="gpt-4o", effort="high")


def test_deepseek_rejects_effort(monkeypatch):
    monkeypatch.setattr(mc.auth, "get_api_key",
                        lambda provider="anthropic": "sk-ds" if provider == "deepseek" else None)
    be = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "deepseek", be)
    with pytest.raises(mc.ModelError, match="high"):     # no portable knob on DeepSeek at all
        mc.complete_json(task="t", prompt="p", schema=SCHEMA, backend="deepseek",
                         model="deepseek-reasoner", effort="high")


@pytest.fixture
def gemini_key(monkeypatch):
    monkeypatch.setattr(mc.auth, "get_api_key",
                        lambda provider="anthropic": "AIza-x" if provider == "gemini" else None)


def test_gemini_backend_routes_with_stored_key(gemini_key, monkeypatch):
    be = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "gemini", be)
    r = mc.complete_json(task="t", prompt="p", schema=SCHEMA, backend="gemini", model="gemini-2.5-flash")
    assert r.backend == "gemini"
    assert be.calls[0]["api_key"] == "AIza-x"          # uses the provider key, not Claude auth


def test_gemini_backend_without_key_errors(monkeypatch):
    monkeypatch.setattr(mc.auth, "get_api_key", lambda provider="anthropic": None)
    with pytest.raises(mc.ModelError, match="watchdog auth"):
        mc.complete_json(task="t", prompt="p", schema=SCHEMA, backend="gemini")


def test_gemini_effort_passed_through(gemini_key, monkeypatch):
    # Unlike OpenAI, every Gemini model accepts reasoning_effort — no capability gate.
    be = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "gemini", be)
    mc.complete_json(task="t", prompt="p", schema=SCHEMA, backend="gemini", model="gemini-2.5-pro", effort="medium")
    assert be.calls[0]["effort"] == "medium"


# ── local / self-hosted + OpenRouter (#380) ──────────────────────────────────

@pytest.fixture
def local_configured(monkeypatch):
    """A local backend with a base URL configured and no key (the common case — most self-hosted
    runners don't check for one)."""
    monkeypatch.setattr(mc.auth, "get_base_url",
                        lambda provider: "http://localhost:11434/v1" if provider == "local" else None)
    monkeypatch.setattr(mc.auth, "get_api_key", lambda provider="anthropic": None)
    monkeypatch.setattr(mc.auth, "provider_requires_key", lambda provider: provider != "local")


def test_local_backend_runs_without_a_key(local_configured, monkeypatch):
    be = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "local", be)
    r = mc.complete_json(task="t", prompt="p", schema=SCHEMA, backend="local", model="llama-3.3-70b")
    assert r.backend == "local"
    assert be.calls[0]["api_key"] is None
    assert be.calls[0]["base_url"] == "http://localhost:11434/v1"


def test_local_backend_missing_base_url_errors(monkeypatch):
    monkeypatch.setattr(mc.auth, "get_base_url", lambda provider: None)
    with pytest.raises(mc.ModelError, match="local_base_url"):
        mc.complete_json(task="t", prompt="p", schema=SCHEMA, backend="local", model="llama-3.3-70b")


def test_local_effort_is_always_rejected(local_configured, monkeypatch):
    # No capability table for an arbitrary self-hosted model — effort always errors rather than
    # guessing it's supported (or silently doing nothing if it isn't).
    be = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "local", be)
    with pytest.raises(mc.ModelError, match="high"):
        mc.complete_json(task="t", prompt="p", schema=SCHEMA, backend="local",
                         model="llama-3.3-70b", effort="high")


def test_openrouter_backend_uses_default_base_url(monkeypatch):
    monkeypatch.setattr(mc.auth, "get_base_url",
                        lambda provider: "https://openrouter.ai/api/v1" if provider == "openrouter" else None)
    monkeypatch.setattr(mc.auth, "get_api_key",
                        lambda provider="anthropic": "sk-or-x" if provider == "openrouter" else None)
    be = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "openrouter", be)
    r = mc.complete_json(task="t", prompt="p", schema=SCHEMA, backend="openrouter",
                         model="anthropic/claude-3.5-sonnet")
    assert r.backend == "openrouter"
    assert be.calls[0]["api_key"] == "sk-or-x"
    assert be.calls[0]["base_url"] == "https://openrouter.ai/api/v1"


def test_openrouter_backend_without_key_errors(monkeypatch):
    monkeypatch.setattr(mc.auth, "get_base_url",
                        lambda provider: "https://openrouter.ai/api/v1" if provider == "openrouter" else None)
    monkeypatch.setattr(mc.auth, "get_api_key", lambda provider="anthropic": None)
    with pytest.raises(mc.ModelError, match="watchdog auth"):
        mc.complete_json(task="t", prompt="p", schema=SCHEMA, backend="openrouter",
                         model="anthropic/claude-3.5-sonnet")


def test_context_window_local_conservative_default(monkeypatch):
    from watchdog.cmd import base as cmd_base
    monkeypatch.setattr(cmd_base, "CONFIG_FILE", cmd_base.WATCHDOG_HOME / "does-not-exist.json")
    assert mc.context_window("llama-3.3-70b", "local") == 8_000


def test_context_window_local_config_override(tmp_path, monkeypatch):
    from watchdog.cmd import base as cmd_base
    config_file = tmp_path / "config.json"
    config_file.write_text('{"local_context_window": 32000}')
    monkeypatch.setattr(cmd_base, "CONFIG_FILE", config_file)
    assert mc.context_window("llama-3.3-70b", "local") == 32_000


def test_context_window_ignores_backend_for_hosted_models():
    # backend=None (or any non-"local" backend) keeps the substring-table behaviour untouched.
    assert mc.context_window("deepseek-v4-flash", "deepseek") == 1_000_000


# ── tokenizer ratio (#574, remeasured against corpus-v1 in #617) ────────────────

# FOUR tokenizers cover all fifteen catalogued models: Claude through Sonnet 4.6, Claude 4.7+,
# Gemini, GPT-5.x, and DeepSeek V4. Members of a family share a value exactly — every model within
# one returned byte-identical counts on the corpus — which is what the paired rows below assert.
# Only an UNCATALOGUED id falls through to 1.0 now.
@pytest.mark.parametrize("model, ratio", [
    ("sonnet", 0.93),            # old Claude tokenizer (Sonnet 4.6)
    ("haiku", 0.93),             # old Claude tokenizer — same value, same tokenizer
    (None, 0.93),                # default tier (sonnet)
    ("sonnet-5", 1.28),          # new Claude tokenizer
    ("opus", 1.28),              # new Claude tokenizer (Opus 4.8) — same value
    ("gemini-3.5-flash", 0.91),  # Gemini tokenizer
    ("gemini-3.1-pro-preview", 0.91),
    ("gpt-5.4-nano", 0.80),      # GPT-5.x tokenizer — measured by billed probe (#617)
    ("gpt-5.5", 0.80),           # same family, same value
    ("gpt-5.6-luna", 0.80),
    ("deepseek-v4-flash", 0.81),  # DeepSeek V4 tokenizer
    ("deepseek-v4-pro", 0.81),
    ("gpt-5-mini", 1.0),         # uncatalogued id → no correction
    ("gemini-2.5-flash", 1.0),   # deliberately not catalogued (#583/D182) → no declared ratio
    ("some-unknown-model", 1.0),  # uncatalogued → no correction
])
def test_tokenizer_ratio(model, ratio):
    assert mc.tokenizer_ratio(model) == ratio


def test_tokenizer_ratio_local_backend_always_one():
    # A self-hosted model's id carries no catalog entry to declare a ratio.
    assert mc.tokenizer_ratio("claude-sonnet-5", backend="local") == 1.0


def test_tokenizer_ratio_no_vault_uses_catalog(tmp_path):
    # No vault context (e.g. `watchdog configure`'s preview, #606 Part A) — plain catalog value.
    assert mc.tokenizer_ratio("sonnet-5", backend=None, vault=None) == 1.28


def test_tokenizer_ratio_uses_vault_calibration_when_available(tmp_path, monkeypatch):
    from watchdog.pipeline import ingest_setup

    calls = []

    def fake_calibration(vault, model, backend, *a, **kw):
        calls.append((vault, model, backend))
        return 1.55

    monkeypatch.setattr(ingest_setup, "_model_tokenizer_calibration", fake_calibration)
    vault = tmp_path / "vault"
    assert mc.tokenizer_ratio("sonnet-5", backend=None, vault=vault) == 1.55
    assert calls == [(vault, "sonnet-5", None)]


def test_tokenizer_ratio_falls_back_to_catalog_when_calibration_returns_none(tmp_path, monkeypatch):
    from watchdog.pipeline import ingest_setup

    monkeypatch.setattr(ingest_setup, "_model_tokenizer_calibration", lambda *a, **kw: None)
    vault = tmp_path / "vault"
    # sonnet-5's catalog ratio (1.28) is used when the vault has no matching calibration history.
    assert mc.tokenizer_ratio("sonnet-5", backend=None, vault=vault) == 1.28


def test_output_ceiling_applies_to_local_and_openrouter():
    # local/openrouter enforce max_tokens and can't paginate (#380) — same treatment as
    # openai/gemini. Neither "llama-3.3-70b" nor the OpenRouter id is catalogued, so both fall
    # back to `_DEFAULT_MAX_OUTPUT_TOKENS` under headroom (#598).
    uncatalogued = int(mc._DEFAULT_MAX_OUTPUT_TOKENS * (1 - mc._OUTPUT_HEADROOM))
    assert mc.output_ceiling_for_sectioning("local", "llama-3.3-70b") == uncatalogued
    assert mc.output_ceiling_for_sectioning("openrouter", "anthropic/claude-3.5-sonnet") == uncatalogued
    assert mc.output_ceiling_for_sectioning("claude-agent-sdk", "sonnet") is None


@pytest.mark.parametrize("model_id, expected_cap", [
    ("gpt-5-mini", 128_000),        # uncatalogued GPT-5 family member
    ("gpt-5.9-turbo", 128_000),     # a GPT-5.x id shipped after this catalog was last updated
    ("gemini-4.0-flash", 65_536),   # ditto, Gemini
    ("deepseek-v4-turbo", 384_000),
])
def test_uncatalogued_model_resolves_via_the_family_fallback_table(model_id, expected_cap):
    # An id the catalog doesn't list yet still gets its documented family cap (#598) rather than
    # the much smaller `_DEFAULT_MAX_OUTPUT_TOKENS` — the normal state of affairs a few months
    # after this catalog was last updated.
    from watchdog.model_catalog import catalog_max_output_tokens
    assert catalog_max_output_tokens(model_id) is None      # genuinely uncatalogued
    assert mc._output_envelope(model_id) == int(expected_cap * (1 - mc._OUTPUT_HEADROOM))


@pytest.mark.parametrize("model_id", [
    "anthropic/claude-3.5-sonnet",   # old/third-party-routed Claude: real cap 8,192
    "gpt-4o",                        # real cap 16,384 — the default already fits under it
    "llama-3.3-70b",                 # self-hosted, cap unknowable
])
def test_families_excluded_from_the_fallback_keep_the_conservative_default(model_id):
    # Deliberately NOT in `max_output_tokens_fallback` — over-claiming here could exceed the
    # model's real cap, so these keep `_DEFAULT_MAX_OUTPUT_TOKENS`. See the table's own comment.
    assert mc._output_envelope(model_id) == int(mc._DEFAULT_MAX_OUTPUT_TOKENS
                                                * (1 - mc._OUTPUT_HEADROOM))


def test_fallback_table_is_matched_most_specific_first():
    # Same ordering contract as `context_window_fallback`: list order is match order, so no row may
    # be shadowed by an earlier, broader one.
    from watchdog.model_catalog import _MAX_OUTPUT_TOKENS_FALLBACK, fallback_max_output_tokens
    markers = [m for m, _ in _MAX_OUTPUT_TOKENS_FALLBACK]
    for i, marker in enumerate(markers):
        earlier = markers[:i]
        assert not any(e in marker for e in earlier), (
            f"{marker!r} is shadowed by an earlier, broader row {earlier!r}")
    assert fallback_max_output_tokens("deepseek-v4-flash") == 384_000
    assert fallback_max_output_tokens("mistral-large") is None


def test_uncatalogued_reasoning_model_gets_a_family_sized_envelope():
    """Regression sentinel (#598, narrowed by #555). A reasoning model draws chain-of-thought and
    its visible answer from ONE output budget, so an uncatalogued GPT-5/Gemini id falling back to
    the generic 16,000 default starves the thinking and truncates the answer. The family-fallback
    table is what prevents that.

    This used to assert against `section._invert_output_ceiling`, because the envelope also fed
    back into the *input*-side section budget and a too-small one shredded documents into hundreds
    of tiny sections. #555 removed that feedback — sectioning no longer consults the envelope at
    all — so the sentinel now guards the wire ceiling directly, which is the thing the fallback
    table actually governs."""
    for model_id in ("gpt-5-mini", "gpt-5.9-turbo", "gemini-4.0-flash"):
        envelope = mc._output_envelope(model_id)
        assert envelope > int(mc._DEFAULT_MAX_OUTPUT_TOKENS * (1 - mc._OUTPUT_HEADROOM)), (
            f"{model_id} fell through to the generic default envelope ({envelope})")


def test_openai_cost():
    assert mc._openai_cost("deepseek-v4-flash",
                           {"prompt_tokens": 1_000_000, "completion_tokens": 0}) == pytest.approx(0.14)
    assert mc._openai_cost("unknown-model", {"prompt_tokens": 100}) is None
    assert mc._openai_cost("deepseek-v4-flash", None) is None


def test_openai_cost_prices_openai_models():
    # gpt-5.4 standard: $2.50/1M input, $15/1M output.
    assert mc._openai_cost("gpt-5.4",
                           {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}) \
        == pytest.approx(2.5 + 15)


@pytest.mark.parametrize("model_id, price_in, price_out", [
    ("gpt-5.5", 5.0, 30.0),
    ("gpt-5.4", 2.5, 15.0),
    ("gpt-5.6-terra", 2.0, 12.0),
    ("gpt-5.6-luna", 0.20, 1.20),
])
def test_openai_family_prices_match_the_published_rate_card(model_id, price_in, price_out):
    """Per-model price sentinel (#555). gpt-5.6-terra shipped carrying gpt-5.4's rates (2.50/15.00
    rather than 2.00/12.00), over-stating every Terra cost by 25% — a copy-paste when the row was
    added, invisible because nothing asserted a per-model price. Verified 2026-08-16 against
    developers.openai.com/api/docs/models/<id>. A row whose rate changes should update this table
    deliberately, not discover the drift through a cost report."""
    assert mc._openai_cost(model_id, {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}) \
        == pytest.approx(price_in + price_out)


def test_openai_cost_deepseek_cache_hit():
    # DeepSeek reports prompt_tokens = hit + miss; the hit portion is priced at the cheap rate.
    cost = mc._openai_cost("deepseek-v4-flash",
                           {"prompt_tokens": 1_000_000, "completion_tokens": 0,
                            "prompt_cache_hit_tokens": 900_000, "prompt_cache_miss_tokens": 100_000})
    assert cost == pytest.approx(100_000 * 0.14e-6 + 900_000 * 0.0028e-6)


def test_openai_cost_openai_cache_hit():
    # OpenAI nests the cached count under prompt_tokens_details; it is a subset of prompt_tokens.
    cost = mc._openai_cost("gpt-5.4",
                           {"prompt_tokens": 1_000_000, "completion_tokens": 0,
                            "prompt_tokens_details": {"cached_tokens": 400_000}})
    assert cost == pytest.approx(600_000 * 2.5e-6 + 400_000 * 0.25e-6)


def test_openai_cost_charges_gpt56_cache_writes_at_the_premium_rate():
    """#586/D195: the GPT-5.6 family bills cache writes at 1.25x uncached input and reports them
    under `prompt_tokens_details.cache_write_tokens`. Reads, writes, and plain input are three
    disjoint slices of `prompt_tokens`."""
    cost = mc._openai_cost("gpt-5.6-luna",
                           {"prompt_tokens": 1_000_000, "completion_tokens": 0,
                            "prompt_tokens_details": {"cached_tokens": 300_000,
                                                      "cache_write_tokens": 200_000}})
    assert cost == pytest.approx(500_000 * 0.20e-6      # uncached remainder
                                 + 300_000 * 0.02e-6    # cache reads
                                 + 200_000 * 0.25e-6)   # cache writes, 1.25x input


def test_openai_cost_cache_writes_before_the_gpt56_family_stay_plain_input():
    """Earlier families carry no additional fee for a write, which is NOT the same as free: those
    tokens are still ordinary input at the full rate. Carving them out at a 0.0 write rate would
    hand back a 40% discount that doesn't exist."""
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 0,
             "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 400_000}}
    assert mc._openai_cost("gpt-5.4", usage) == pytest.approx(1_000_000 * 2.5e-6)


def test_openai_cost_clamps_overlapping_cache_counters():
    """A provider reporting reads + writes above `prompt_tokens` must never drive the uncached
    remainder negative and under-charge the call."""
    cost = mc._openai_cost("gpt-5.6-luna",
                           {"prompt_tokens": 1000, "completion_tokens": 0,
                            "prompt_tokens_details": {"cached_tokens": 900,
                                                      "cache_write_tokens": 900}})
    assert cost == pytest.approx(900 * 0.02e-6 + 100 * 0.25e-6)


def test_cache_write_tokens_reads_the_openai_shape_and_defaults_to_zero():
    assert mc.cache_write_tokens(
        {"prompt_tokens_details": {"cache_write_tokens": 4352}}) == 4352
    assert mc.cache_write_tokens({"prompt_tokens_details": {"cached_tokens": 10}}) == 0
    assert mc.cache_write_tokens({"prompt_tokens": 10}) == 0
    assert mc.cache_write_tokens(None) == 0


def test_openai_batch_cost_is_half_the_openai_cost():
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
    live = mc._openai_cost("gpt-5.4", usage)
    assert mc._openai_batch_cost("gpt-5.4", usage) == pytest.approx(live * 0.5)


def test_openai_batch_cost_none_for_unknown_model_or_usage():
    assert mc._openai_batch_cost("not-a-real-model", {"prompt_tokens": 100}) is None
    assert mc._openai_batch_cost("gpt-5.4", None) is None


def test_openai_cost_prices_gemini_models():
    # gemini-3.1-flash-lite: $0.25/1M input, $1.50/1M output.
    assert mc._openai_cost("gemini-3.1-flash-lite",
                           {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}) \
        == pytest.approx(0.25 + 1.50)
    # gemini-3.5-flash: $1.50/1M input, $9.00/1M output.
    assert mc._openai_cost("gemini-3.5-flash",
                           {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}) \
        == pytest.approx(1.50 + 9.00)


# ── hidden reasoning tokens (#547) ────────────────────────────────────────────

def test_fold_in_hidden_reasoning_recovers_the_total_tokens_gap():
    # The real shape from the failing benchmark call: Gemini leaves thinking tokens out of
    # completion_tokens entirely, so the only evidence they were spent (and billed) is that
    # total_tokens exceeds prompt + completion. Folding the gap in must land it in *both*
    # places — completion_tokens (what _openai_cost charges) and the reasoning_tokens detail
    # (what telemetry and the truncation diagnostic read).
    out = mc._fold_in_hidden_reasoning(
        {"prompt_tokens": 27147, "completion_tokens": 847, "total_tokens": 43131})
    assert out["completion_tokens"] == 15984                      # 847 visible + 15,137 thinking
    assert out["completion_tokens_details"]["reasoning_tokens"] == 15137
    assert out["prompt_tokens"] == 27147                          # input side untouched


@pytest.mark.parametrize("usage", [
    None,
    {},
    # No gap: total accounts for exactly prompt + completion, so nothing was hidden.
    {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    # No total_tokens at all — nothing to reconstruct from.
    {"prompt_tokens": 100, "completion_tokens": 50},
    # Provider already reported its own reasoning count (OpenAI's shape, where reasoning is
    # already a subset of completion_tokens) — folding again would double-count it.
    {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 200,
     "completion_tokens_details": {"reasoning_tokens": 30}},
])
def test_fold_in_hidden_reasoning_leaves_other_usage_shapes_alone(usage):
    assert mc._fold_in_hidden_reasoning(usage) == usage


# ── Anthropic thinking-token telemetry (#635 sent `thinking`; the reasoning-tokens count it
#    produces was never read, because it arrives under a different key than OpenAI's) ─────────

def test_fold_in_anthropic_thinking_surfaces_the_count():
    # The real shape anthropic's SDK returns for a thinking-enabled call: reasoning tokens are
    # already included in output_tokens (Anthropic bills them there, unconditionally), and
    # broken out separately under output_tokens_details.thinking_tokens — a different key than
    # completion_tokens_details.reasoning_tokens, the one every reader here actually checks.
    out = mc._fold_in_anthropic_thinking(
        {"input_tokens": 9408, "output_tokens": 1500,
         "output_tokens_details": {"thinking_tokens": 1200}})
    assert out["completion_tokens_details"]["reasoning_tokens"] == 1200
    # Unlike Gemini's fold, output_tokens is untouched — it already includes the 1,200.
    assert out["output_tokens"] == 1500
    assert out["input_tokens"] == 9408


@pytest.mark.parametrize("usage_dict", [
    {"input_tokens": 100, "output_tokens": 50},                          # no details at all
    {"input_tokens": 100, "output_tokens": 50, "output_tokens_details": {}},
    # thinking off, or a model with no thinking control — reports 0, not absent
    {"input_tokens": 100, "output_tokens": 50, "output_tokens_details": {"thinking_tokens": 0}},
    {"input_tokens": 100, "output_tokens": 50, "output_tokens_details": {"thinking_tokens": None}},
])
def test_fold_in_anthropic_thinking_leaves_other_usage_shapes_alone(usage_dict):
    assert mc._fold_in_anthropic_thinking(usage_dict) == usage_dict


def _fake_usage_response(monkeypatch, usage):
    """Point httpx at a fixed JSON body carrying `usage`, for the wire-level usage tests."""
    import httpx

    class FakeResp:
        status_code = 200
        headers = {}
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": '{"name": "Acme"}'},
                                 "finish_reason": "stop"}], "usage": usage}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None): return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)


def test_gemini_hidden_reasoning_is_billed_as_output(monkeypatch):
    # The bug this fixes: Gemini bills thinking tokens at the output rate, but they never appear
    # in completion_tokens, so cost was charged on the visible answer alone — understating every
    # Gemini call in proportion to how hard the model thought.
    _fake_usage_response(monkeypatch, {"prompt_tokens": 27147, "completion_tokens": 847,
                                       "total_tokens": 43131})
    out = asyncio.run(mc._openai_complete_async("p", "gemini-3.5-flash", SCHEMA, "AIza-x", 64000,
                                                "high", base_url=mc._OPENAI_BASE["gemini"]))
    assert out["usage"]["completion_tokens"] == 15984
    assert out["cost_usd"] == pytest.approx(27147 * 1.5e-6 + 15984 * 9e-6)
    # …and not the 19x-understated figure the visible answer alone would have produced.
    assert out["cost_usd"] > mc._openai_cost("gemini-3.5-flash",
                                             {"prompt_tokens": 27147, "completion_tokens": 847})


def test_non_gemini_backends_keep_their_reported_usage(monkeypatch):
    # Gated to Gemini's endpoint: DeepSeek's total_tokens legitimately equals prompt + completion,
    # and no other provider's gap has been shown to mean hidden reasoning — reconstructing one
    # everywhere would invent tokens.
    usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 200}
    _fake_usage_response(monkeypatch, usage)
    out = asyncio.run(mc._openai_complete_async("p", "deepseek-v4-flash", SCHEMA, "sk-ds", 8000,
                                                base_url="https://api.deepseek.com"))
    assert out["usage"] == usage


# ── rate-limit header capture (#563) ────────────────────────────────────────────

def test_rate_limit_headers_normalizes_limit_and_remaining_to_int():
    headers = {"x-ratelimit-limit-tokens": "150000", "x-ratelimit-remaining-tokens": "149800",
              "x-ratelimit-reset-tokens": "6m0s"}
    assert mc._rate_limit_headers(headers, mc._OPENAI_RATE_LIMIT_HEADERS) == {
        "limit_tokens": 150000, "remaining_tokens": 149800, "reset_tokens": "6m0s"}


def test_rate_limit_headers_none_when_none_present():
    assert mc._rate_limit_headers({}, mc._OPENAI_RATE_LIMIT_HEADERS) is None


def _fake_openai_resp(monkeypatch, *, status_code=200, headers=None):
    """Point httpx at a canned response carrying `headers`, for rate-limit capture tests."""
    import httpx

    class FakeResp:
        pass
    FakeResp.status_code = status_code
    FakeResp.headers = headers or {}
    FakeResp.raise_for_status = lambda self: None
    FakeResp.json = lambda self: {"choices": [{"message": {"content": '{"name": "Acme"}'}}],
                                  "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None): return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)


def test_openai_backend_captures_rate_limit_headers_on_success(monkeypatch):
    _fake_openai_resp(monkeypatch, headers={"x-ratelimit-limit-tokens": "150000",
                                            "x-ratelimit-remaining-tokens": "149800",
                                            "x-ratelimit-reset-tokens": "6m0s"})
    out = asyncio.run(mc._openai_complete_async("p", "deepseek-v4-flash", SCHEMA, "sk-ds", 8000,
                                                base_url="https://api.deepseek.com"))
    assert out["rate_limit"] == {"limit_tokens": 150000, "remaining_tokens": 149800,
                                 "reset_tokens": "6m0s"}


def test_openai_backend_rate_limit_none_when_headers_absent(monkeypatch):
    _fake_openai_resp(monkeypatch)
    out = asyncio.run(mc._openai_complete_async("p", "deepseek-v4-flash", SCHEMA, "sk-ds", 8000,
                                                base_url="https://api.deepseek.com"))
    assert out["rate_limit"] is None


def test_openai_429_carries_rate_limit_headers_on_the_exception(monkeypatch):
    _fake_openai_resp(monkeypatch, status_code=429,
                      headers={"x-ratelimit-limit-tokens": "150000",
                              "x-ratelimit-remaining-tokens": "0",
                              "x-ratelimit-reset-tokens": "12s"})
    with pytest.raises(mc.RateLimitError) as exc_info:
        asyncio.run(mc._openai_complete_async("p", "deepseek-v4-flash", SCHEMA, "sk-ds", 8000,
                                              base_url="https://api.deepseek.com"))
    assert exc_info.value.rate_limit == {"limit_tokens": 150000, "remaining_tokens": 0,
                                         "reset_tokens": "12s"}


def test_openai_backend_request_shape(monkeypatch):
    import httpx
    captured = {}

    class FakeResp:
        status_code = 200
        headers = {}
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": '{"name": "Acme"}'}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            captured.update(url=url, headers=headers, body=json)
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    out = asyncio.run(mc._openai_complete_async("prompt", "deepseek-v4-flash", SCHEMA,
                                                "sk-ds", 8000, "high",
                                                base_url="https://api.deepseek.com"))
    assert out["text"] == '{"name": "Acme"}'
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-ds"
    assert captured["body"]["reasoning_effort"] == "high"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert "JSON" in captured["body"]["messages"][1]["content"]   # required for json_object mode
    assert out["cost_usd"] == pytest.approx(10 * 0.14e-6 + 5 * 0.28e-6)


def test_openai_backend_verifies_via_os_trust_store(monkeypatch):
    # Cert verification must go through the OS trust store (truststore), not certifi's bundled
    # list — otherwise a TLS-inspecting corporate proxy whose root CA the OS
    # already trusts fails cert verification here even though the browser is fine.
    import httpx
    import truststore

    client_kwargs = {}

    class FakeResp:
        status_code = 200
        headers = {}
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": '{"name": "Acme"}'}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    class FakeClient:
        def __init__(self, *a, **k): client_kwargs.update(k)
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None): return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    asyncio.run(mc._openai_complete_async("prompt", "deepseek-v4-flash", SCHEMA, "sk-ds", 8000,
                                          base_url="https://api.deepseek.com"))
    assert isinstance(client_kwargs["verify"], truststore.SSLContext)


def test_split_deepseek_thinking():
    assert mc._split_deepseek_thinking("deepseek-v4-flash") == ("deepseek-v4-flash", False)
    assert mc._split_deepseek_thinking("deepseek-v4-flash-thinking") == ("deepseek-v4-flash", True)
    assert mc._split_deepseek_thinking("deepseek-v4-pro-thinking") == ("deepseek-v4-pro", True)


def _fake_httpx(monkeypatch, captured):
    """Patch httpx.AsyncClient to capture the request body and return a canned OK response."""
    import httpx

    class FakeResp:
        status_code = 200
        headers = {}
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": '{"name": "Acme"}'}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            captured.update(url=url, headers=headers, body=json)
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)


def test_deepseek_thinking_toggle(monkeypatch):
    # Bare id → thinking explicitly disabled (the non-thinking default), sent even though the
    # provider default is enabled, so the mode is pinned.
    captured = {}
    _fake_httpx(monkeypatch, captured)
    out = asyncio.run(mc._openai_complete_async("p", "deepseek-v4-flash", SCHEMA, "sk-ds", 8000,
                                                base_url="https://api.deepseek.com"))
    assert captured["body"]["thinking"] == {"type": "disabled"}
    assert captured["body"]["model"] == "deepseek-v4-flash"        # marker not present
    assert out["cost_usd"] == pytest.approx(10 * 0.14e-6 + 5 * 0.28e-6)

    # `-thinking` marker → thinking enabled; the bare id is used for the request + cost lookup.
    captured = {}
    _fake_httpx(monkeypatch, captured)
    out = asyncio.run(mc._openai_complete_async("p", "deepseek-v4-flash-thinking", SCHEMA,
                                                "sk-ds", 8000, base_url="https://api.deepseek.com"))
    assert captured["body"]["thinking"] == {"type": "enabled"}
    assert captured["body"]["model"] == "deepseek-v4-flash"        # stripped before the request
    assert out["cost_usd"] == pytest.approx(10 * 0.14e-6 + 5 * 0.28e-6)   # priced on the bare id


def test_openai_backend_no_thinking_param(monkeypatch):
    # Non-DeepSeek OpenAI-compatible providers never get a thinking toggle, and the marker is
    # left untouched (it is a DeepSeek-only convention).
    captured = {}
    _fake_httpx(monkeypatch, captured)
    asyncio.run(mc._openai_complete_async("p", "gpt-5-mini", SCHEMA, "sk-o", 8000,
                                          base_url="https://api.openai.com/v1"))
    assert "thinking" not in captured["body"]


@pytest.mark.parametrize("model_id, reasoning", [
    ("gpt-5", True), ("gpt-5-mini", True), ("gpt-5.4", True), ("gpt-5.5-pro", True),
    ("o1", True), ("o3-mini", True), ("o4-mini", True),
    ("gpt-4o", False), ("gpt-4.1", False), ("chatgpt-4o-latest", False),
    ("some-new-model", False),   # unlisted → chat, the safe default (never sends an unsupported param)
])
def test_openai_is_reasoning(model_id, reasoning):
    assert mc._openai_is_reasoning(model_id) is reasoning


def test_openai_reasoning_model_uses_max_completion_tokens(monkeypatch):
    # OpenAI reasoning models reject max_tokens → send max_completion_tokens instead.
    captured = {}
    _fake_httpx(monkeypatch, captured)
    asyncio.run(mc._openai_complete_async("p", "gpt-5-mini", SCHEMA, "sk-o", 8000,
                                          base_url="https://api.openai.com/v1"))
    assert captured["body"]["max_completion_tokens"] == 8000
    assert "max_tokens" not in captured["body"]


def test_openai_chat_model_uses_max_tokens(monkeypatch):
    # A chat model takes the classic max_tokens field.
    captured = {}
    _fake_httpx(monkeypatch, captured)
    asyncio.run(mc._openai_complete_async("p", "gpt-4o", SCHEMA, "sk-o", 8000,
                                          base_url="https://api.openai.com/v1"))
    assert captured["body"]["max_tokens"] == 8000
    assert "max_completion_tokens" not in captured["body"]


def test_deepseek_uses_max_tokens(monkeypatch):
    # DeepSeek speaks the classic wire format regardless of thinking mode → max_tokens.
    captured = {}
    _fake_httpx(monkeypatch, captured)
    asyncio.run(mc._openai_complete_async("p", "deepseek-v4-flash", SCHEMA, "sk-ds", 8000,
                                          base_url="https://api.deepseek.com"))
    assert captured["body"]["max_tokens"] == 8000
    assert "max_completion_tokens" not in captured["body"]


def test_gemini_uses_max_tokens(monkeypatch):
    # Gemini isn't in the OpenAI reasoning-model capability table, so it takes the classic field.
    captured = {}
    _fake_httpx(monkeypatch, captured)
    asyncio.run(mc._openai_complete_async("p", "gemini-2.5-flash", SCHEMA, "AIza-x", 8000,
                                          base_url="https://generativelanguage.googleapis.com/v1beta/openai"))
    assert captured["body"]["max_tokens"] == 8000
    assert "max_completion_tokens" not in captured["body"]


def test_local_uses_max_tokens_even_for_an_openai_reasoning_style_id(monkeypatch):
    # A self-hosted model's id carries no relation to OpenAI's naming (#380) — an operator could
    # name one "gpt-5-mini" or "o3-mini" and it would still speak the classic max_tokens field.
    # Gating on base_url (not just model_id) keeps a local/OpenRouter call from sending
    # max_completion_tokens to a runner that doesn't recognize it.
    captured = {}
    _fake_httpx(monkeypatch, captured)
    asyncio.run(mc._openai_complete_async("p", "gpt-5-mini", SCHEMA, None, 8000,
                                          base_url="http://localhost:11434/v1"))
    assert captured["body"]["max_tokens"] == 8000
    assert "max_completion_tokens" not in captured["body"]


def test_openrouter_uses_max_tokens_even_for_an_openai_reasoning_style_id(monkeypatch):
    captured = {}
    _fake_httpx(monkeypatch, captured)
    asyncio.run(mc._openai_complete_async("p", "o3-mini", SCHEMA, "sk-or-x", 8000,
                                          base_url="https://openrouter.ai/api/v1"))
    assert captured["body"]["max_tokens"] == 8000
    assert "max_completion_tokens" not in captured["body"]


def test_gemini_backend_request_shape(monkeypatch):
    captured = {}
    _fake_httpx(monkeypatch, captured)
    out = asyncio.run(mc._openai_complete_async("prompt", "gemini-3.1-flash-lite", SCHEMA, "AIza-x", 8000,
                                                "low",
                                                base_url="https://generativelanguage.googleapis.com/v1beta/openai"))
    assert captured["url"] == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer AIza-x"
    assert captured["body"]["model"] == "gemini-3.1-flash-lite"   # no marker-stripping (DeepSeek-only)
    assert captured["body"]["reasoning_effort"] == "low"
    assert "thinking" not in captured["body"]                      # DeepSeek-only toggle
    assert out["cost_usd"] == pytest.approx(10 * 0.25e-6 + 5 * 1.50e-6)


# ── response_format: real json_schema on Gemini and OpenAI, json_object elsewhere (D98/D151) ────

def test_gemini_uses_real_json_schema_mode(monkeypatch):
    captured = {}
    _fake_httpx(monkeypatch, captured)
    asyncio.run(mc._openai_complete_async("prompt", "gemini-2.5-flash", SCHEMA, "AIza-x", 8000,
                                          base_url="https://generativelanguage.googleapis.com/v1beta/openai"))
    assert captured["body"]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "watchdog_response", "schema": SCHEMA},
    }
    # The schema is enforced at the wire level, so it isn't also duplicated into the prompt text.
    assert "Return JSON matching this schema" not in captured["body"]["messages"][1]["content"]


def test_openai_uses_strict_json_schema_mode(monkeypatch):
    """#479/D151: OpenAI gets real wire-level enforcement too, via a mechanically-derived
    all-fields-required strict variant (unlike Gemini, which accepts the schema as-authored)."""
    captured = {}
    _fake_httpx(monkeypatch, captured)
    asyncio.run(mc._openai_complete_async("prompt", "gpt-4o", SCHEMA, "sk-o", 8000,
                                          base_url="https://api.openai.com/v1"))
    rf = captured["body"]["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == mc._to_strict_schema(SCHEMA)
    # The schema is enforced at the wire level, so it isn't also duplicated into the prompt text.
    assert "Return JSON matching this schema" not in captured["body"]["messages"][1]["content"]


def test_deepseek_stays_on_json_object_mode(monkeypatch):
    captured = {}
    _fake_httpx(monkeypatch, captured)
    asyncio.run(mc._openai_complete_async("prompt", "deepseek-v4-flash", SCHEMA, "sk-ds", 8000,
                                          base_url="https://api.deepseek.com"))
    assert captured["body"]["response_format"] == {"type": "json_object"}


# ── prompt_cache_key (#562): routing hint derived from the first cache_control breakpoint ──────

_BLOCK_PROMPT = [
    {"type": "text", "text": "instructions"},
    {"type": "text", "text": "skill", "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": "document text"},
]


def test_openai_sends_prompt_cache_key_for_a_block_prompt_with_a_breakpoint(monkeypatch):
    captured = {}
    _fake_httpx(monkeypatch, captured)
    asyncio.run(mc._openai_complete_async(_BLOCK_PROMPT, "gpt-4o", SCHEMA, "sk-o", 8000,
                                          base_url="https://api.openai.com/v1"))
    assert captured["body"]["prompt_cache_key"] == mc._prompt_cache_key(_BLOCK_PROMPT)
    assert captured["body"]["prompt_cache_key"].startswith("wd-")


@pytest.mark.parametrize("base_url", [
    "https://api.deepseek.com",
    "https://generativelanguage.googleapis.com/v1beta/openai",
    "http://localhost:11434/v1",
])
def test_non_openai_backends_never_get_a_prompt_cache_key(monkeypatch, base_url):
    """Only the real OpenAI endpoint documents this parameter — DeepSeek/Gemini-compat/local
    servers aren't guaranteed to ignore an unknown body field (#562)."""
    captured = {}
    _fake_httpx(monkeypatch, captured)
    asyncio.run(mc._openai_complete_async(_BLOCK_PROMPT, "some-model", SCHEMA, "key", 8000,
                                          base_url=base_url))
    assert "prompt_cache_key" not in captured["body"]


def test_openai_plain_string_prompt_gets_no_prompt_cache_key(monkeypatch):
    captured = {}
    _fake_httpx(monkeypatch, captured)
    asyncio.run(mc._openai_complete_async("plain string prompt", "gpt-4o", SCHEMA, "sk-o", 8000,
                                          base_url="https://api.openai.com/v1"))
    assert "prompt_cache_key" not in captured["body"]


def test_openai_block_prompt_with_no_cache_control_gets_no_prompt_cache_key(monkeypatch):
    captured = {}
    _fake_httpx(monkeypatch, captured)
    no_breakpoint = [{"type": "text", "text": "instructions"}, {"type": "text", "text": "document"}]
    asyncio.run(mc._openai_complete_async(no_breakpoint, "gpt-4o", SCHEMA, "sk-o", 8000,
                                          base_url="https://api.openai.com/v1"))
    assert "prompt_cache_key" not in captured["body"]


# ── explicit cache breakpoints (#586, D195): GPT-5.6+ doesn't fall back to the longest prefix ──

def _user_content(captured):
    return captured["body"]["messages"][1]["content"]


def test_gpt56_sends_an_explicit_breakpoint_on_the_first_cache_control_block(monkeypatch):
    """The fix for #586. GPT-5.6 places one implicit breakpoint at the latest user message and
    does NOT fall back to the longest matching unmarked prefix before it — so with the whole
    prompt in one user message, an unmarked request can only hit on a byte-identical whole-prompt
    repeat. Marking the end of the run-stable prefix is what makes a partial hit possible at all."""
    captured = {}
    _fake_httpx(monkeypatch, captured)
    asyncio.run(mc._openai_complete_async(_BLOCK_PROMPT, "gpt-5.6-luna", SCHEMA, "sk-o", 8000,
                                          base_url="https://api.openai.com/v1"))
    content = _user_content(captured)
    assert [b.get("prompt_cache_breakpoint") for b in content] == [
        None, {"mode": "explicit"}, None]
    # Explicit mode suppresses the implicit end-of-prompt breakpoint, which would otherwise keep
    # writing the document text at 1.25x for a cache nothing reads back.
    assert captured["body"]["prompt_cache_options"] == {"mode": "explicit"}


def test_gpt56_cache_blocks_render_byte_identically_to_the_flattened_prompt(monkeypatch):
    """This change is meant to alter the request's cache metadata and nothing the model reads.
    Adjacent text parts concatenate with no separator, so each block but the last has to carry
    the newline `_flatten_prompt`'s join would have put after it."""
    captured = {}
    _fake_httpx(monkeypatch, captured)
    asyncio.run(mc._openai_complete_async(_BLOCK_PROMPT, "gpt-5.6-luna", SCHEMA, "sk-o", 8000,
                                          base_url="https://api.openai.com/v1"))
    rendered = "".join(b["text"] for b in _user_content(captured))
    assert rendered == mc._flatten_prompt(_BLOCK_PROMPT)


def test_gpt56_marks_only_the_first_breakpoint_even_with_a_document_breakpoint():
    """`prompts._document_block`'s second breakpoint (#535) stays unmarked on OpenAI: extract and
    verify each serialize their own structured-output schema ahead of the system message, so no
    marking of the document block could make them share a prefix (D181) — and marking it would
    pay the 1.25x write on document text nothing reads back."""
    two_breakpoints = prompts.build_extract_prompt(
        pages_text="document text", skill_text="SKILL", sidecar=None, brief=None,
        known_document_types=[], cache_document=True)
    assert sum("cache_control" in b for b in two_breakpoints) == 2
    marked = [i for i, b in enumerate(mc._openai_cache_blocks(two_breakpoints))
              if "prompt_cache_breakpoint" in b]
    assert marked == [1]


def test_earlier_openai_families_keep_the_flat_string_and_no_cache_options(monkeypatch):
    """GPT-5.5 and earlier still do implicit longest-prefix matching, and don't accept these
    fields — leave those requests exactly as they were."""
    captured = {}
    _fake_httpx(monkeypatch, captured)
    asyncio.run(mc._openai_complete_async(_BLOCK_PROMPT, "gpt-5.4-mini", SCHEMA, "sk-o", 8000,
                                          base_url="https://api.openai.com/v1"))
    assert _user_content(captured) == mc._flatten_prompt(_BLOCK_PROMPT)
    assert "prompt_cache_options" not in captured["body"]


def test_non_openai_backends_never_get_cache_breakpoints(monkeypatch):
    """Same gate as `prompt_cache_key` (#562): a DeepSeek/Gemini-compat/local server isn't
    guaranteed to ignore body fields it doesn't recognize."""
    captured = {}
    _fake_httpx(monkeypatch, captured)
    asyncio.run(mc._openai_complete_async(_BLOCK_PROMPT, "gpt-5.6-luna", SCHEMA, "k", 8000,
                                          base_url="http://localhost:11434/v1"))
    # Still one flat string (this endpoint also appends the schema-in-prompt tail, D98).
    assert _user_content(captured).startswith(mc._flatten_prompt(_BLOCK_PROMPT))
    assert "prompt_cache_options" not in captured["body"]


@pytest.mark.parametrize("prompt", [
    "a plain string prompt",
    [{"type": "text", "text": "instructions"}, {"type": "text", "text": "document"}],
])
def test_gpt56_prompt_with_nothing_to_mark_stays_implicit(monkeypatch, prompt):
    """With no breakpoint to mark, switching the request into explicit mode would disable the
    implicit breakpoint and remove even the whole-prompt caching such a call has today."""
    captured = {}
    _fake_httpx(monkeypatch, captured)
    asyncio.run(mc._openai_complete_async(prompt, "gpt-5.6-luna", SCHEMA, "sk-o", 8000,
                                          base_url="https://api.openai.com/v1"))
    assert _user_content(captured) == mc._flatten_prompt(prompt)
    assert "prompt_cache_options" not in captured["body"]


@pytest.mark.parametrize("builder", ["extract", "digest"])
def test_marked_prefix_clears_openais_1024_token_floor_for_a_real_skill(builder):
    """GPT-5.6+ enforces a strict 1,024-token minimum on the marked prefix, so a breakpoint in
    the right place still buys nothing if what precedes it is too short. Extract's prefix is
    never in doubt (instructions alone are ~14K chars), but digest's own template is only ~1.5K —
    it clears the floor because the skill sits inside the prefix with it. Shrink the digest
    template, or move the skill out of the prefix, and this silently stops caching (D194)."""
    skill = (importlib.resources.files("watchdog") / "skills" / "records"
             / "court-documents.md").read_text(encoding="utf-8")
    if builder == "extract":
        prompt = prompts.build_extract_prompt(pages_text="doc", skill_text=skill, sidecar=None,
                                              brief=None, known_document_types=[])
    else:
        prompt = prompts.build_digest_prompt(filename="f.pdf", title="T", document_type="Order",
                                             page_count=3, skill_text=skill, brief=None,
                                             sidecar=None, key_facts=[{"fact": "x"}])
    blocks = mc._openai_cache_blocks(prompt)
    marked = next(i for i, b in enumerate(blocks) if "prompt_cache_breakpoint" in b)
    prefix_chars = sum(len(b["text"]) for b in blocks[:marked + 1])
    assert prefix_chars // 4 > 1024, f"{builder} prefix is only ~{prefix_chars // 4} est tokens"


def test_openai_cache_blocks_none_when_there_is_nothing_to_mark():
    assert mc._openai_cache_blocks("plain string") is None
    assert mc._openai_cache_blocks([{"type": "text", "text": "a"}]) is None


def test_catalog_cache_breakpoints_marks_only_the_gpt56_family():
    from watchdog.model_catalog import catalog_cache_breakpoints
    assert catalog_cache_breakpoints("gpt-5.6-luna") is True
    assert catalog_cache_breakpoints("gpt-5.6-terra") is True
    assert catalog_cache_breakpoints("gpt-5.5") is False
    assert catalog_cache_breakpoints("gpt-5.4-mini") is False
    assert catalog_cache_breakpoints("claude-sonnet-4-6") is False
    # An uncatalogued id (a local/OpenRouter model behind an arbitrary runner) must never be sent
    # a body field it might reject.
    assert catalog_cache_breakpoints("some-self-hosted-model") is False


def test_gpt56_cache_write_price_is_exactly_1_25x_input():
    """OpenAI's rule, not a per-model quote — a drifting hand-typed rate would silently
    mis-bill every cached call on the family."""
    from watchdog.model_catalog import _OPENAI_PRICING
    for model_id in ("gpt-5.6-luna", "gpt-5.6-terra"):
        inp, _out, _cached, write = _OPENAI_PRICING[model_id]
        assert write == pytest.approx(inp * 1.25)


def test_prompt_cache_key_none_for_plain_string():
    assert mc._prompt_cache_key("just a plain string") is None


def test_prompt_cache_key_none_when_no_breakpoint():
    assert mc._prompt_cache_key([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) is None


def test_prompt_cache_key_shared_across_documents_with_the_same_skill_and_by_the_derived_verify_prompt():
    """The property #562 exists to fix: extract prompts for two different documents that share
    the same run-stable prefix (instructions+brief+skill) must get the SAME cache key, and the
    verify prompt built from one of them (which appends a block *after* the first breakpoint)
    must get that same key too — that's the whole point of keying on the FIRST breakpoint rather
    than the last."""
    kwargs = dict(skill_text="SKILL TEXT", brief="Investigate the fraud", known_document_types=[])
    doc1 = prompts.build_extract_prompt(pages_text="document one text", sidecar=None, **kwargs)
    doc2 = prompts.build_extract_prompt(pages_text="a completely different document, much longer",
                                        sidecar="unrelated sidecar", **kwargs)
    verify = prompts.build_verify_prompt(doc1, key_facts=[{"fact": "Filed in 2024"}],
                                         entities=[{"id": "acme", "name": "Acme"}])

    key1 = mc._prompt_cache_key(doc1)
    key2 = mc._prompt_cache_key(doc2)
    key_verify = mc._prompt_cache_key(verify)

    assert key1 is not None
    assert key1 == key2 == key_verify


def test_prompt_cache_key_shared_across_digest_calls_with_the_same_skill():
    """Digest (#586) uses the same block layout as extract (A1) — the cacheable prefix is
    instructions+brief then skill, with filename/title/type/page_count/sidecar/key_facts confined
    to the volatile block after the breakpoint. Two digest prompts for different documents that
    share a skill must derive the same cache key, same as extract's."""
    kwargs = dict(skill_text="SKILL TEXT", brief="Investigate the fraud")
    doc1 = prompts.build_digest_prompt(filename="doc-a.pdf", title="Doc A",
                                       document_type="Annual Report", page_count=12,
                                       sidecar="sidecar A", key_facts=[{"fact": "Fact about A"}],
                                       **kwargs)
    doc2 = prompts.build_digest_prompt(filename="doc-b.pdf", title="Doc B",
                                       document_type="Affidavit", page_count=99,
                                       sidecar="a different sidecar",
                                       key_facts=[{"fact": "A different fact"}], **kwargs)
    key1 = mc._prompt_cache_key(doc1)
    key2 = mc._prompt_cache_key(doc2)
    assert key1 is not None
    assert key1 == key2


def test_prompt_cache_key_differs_by_skill():
    kwargs = dict(pages_text="x", sidecar=None, brief=None, known_document_types=[])
    a = prompts.build_extract_prompt(skill_text="SKILL A", **kwargs)
    b = prompts.build_extract_prompt(skill_text="SKILL B", **kwargs)
    assert mc._prompt_cache_key(a) != mc._prompt_cache_key(b)


def test_prompt_cache_key_unaffected_by_cache_document():
    """`cache_document` (#535) adds a SECOND breakpoint after the per-document text — keying on
    the first breakpoint means turning it on must not change the derived key."""
    kwargs = dict(pages_text="x", skill_text="SKILL", sidecar=None, brief=None,
                  known_document_types=[])
    without = prompts.build_extract_prompt(cache_document=False, **kwargs)
    with_doc_cache = prompts.build_extract_prompt(cache_document=True, **kwargs)
    assert mc._prompt_cache_key(without) == mc._prompt_cache_key(with_doc_cache)


# ── strict-mode schema derivation and response normalization (#479/D151) ───────────────────────

def test_to_strict_schema_forces_every_property_required():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
        "required": ["a"],
        "additionalProperties": False,
    }
    strict = mc._to_strict_schema(schema)
    assert set(strict["required"]) == {"a", "b"}


def test_to_strict_schema_widens_optional_scalar_to_nullable():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
        "required": ["a"],
        "additionalProperties": False,
    }
    strict = mc._to_strict_schema(schema)
    assert strict["properties"]["a"]["type"] == "string"          # already required — untouched
    assert strict["properties"]["b"]["type"] == ["string", "null"]


def test_to_strict_schema_leaves_optional_array_unnulled():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}},
        "required": ["a"],
        "additionalProperties": False,
    }
    strict = mc._to_strict_schema(schema)
    assert strict["properties"]["tags"]["type"] == "array"        # required now, but not nulled
    assert "tags" in strict["required"]


def test_to_strict_schema_widens_enum_by_appending_null():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"},
                       "basis": {"type": "string", "enum": ["stated", "inferred"]}},
        "required": ["a"],
        "additionalProperties": False,
    }
    strict = mc._to_strict_schema(schema)
    assert strict["properties"]["basis"]["enum"] == ["stated", "inferred", None]


def test_to_strict_schema_recurses_into_array_items():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "items": {"type": "array", "items": {
            "type": "object",
            "properties": {"x": {"type": "string"}, "y": {"type": "string"}},
            "required": ["x"],
            "additionalProperties": False,
        }}},
        "required": ["a", "items"],
        "additionalProperties": False,
    }
    strict = mc._to_strict_schema(schema)
    item_schema = strict["properties"]["items"]["items"]
    assert set(item_schema["required"]) == {"x", "y"}
    assert item_schema["properties"]["y"]["type"] == ["string", "null"]


def test_to_strict_schema_does_not_mutate_the_original():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
        "required": ["a"],
        "additionalProperties": False,
    }
    import copy
    before = copy.deepcopy(schema)
    mc._to_strict_schema(schema)
    assert schema == before


def test_strip_none_removes_nulls_recursively():
    assert mc._strip_none({"a": 1, "b": None, "c": {"d": None, "e": 2}, "f": [{"g": None}]}) == {
        "a": 1, "c": {"e": 2}, "f": [{}],
    }


def test_denormalize_strict_json_round_trips_through_strip_none():
    text = '{"a": 1, "b": null}'
    assert json.loads(mc._denormalize_strict_json(text)) == {"a": 1}


def test_denormalize_strict_json_passes_through_invalid_json_unchanged():
    assert mc._denormalize_strict_json("not json") == "not json"


def test_openai_backend_denormalizes_nulls_before_returning_text(monkeypatch):
    """The wire response from a strict-mode OpenAI call may carry explicit nulls for fields the
    model considered "empty" — those must be stripped before the shared validate/prune path
    (which expects schemas.py's non-strict, omit-optional-fields shape) ever sees them."""
    captured = {}

    class FakeResp:
        status_code = 200
        headers = {}
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": '{"name": "Acme", "extra": null}'}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            captured.update(body=json)
            return FakeResp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    out = asyncio.run(mc._openai_complete_async("prompt", "gpt-4o", SCHEMA, "sk-o", 8000,
                                                base_url="https://api.openai.com/v1"))
    assert json.loads(out["text"]) == {"name": "Acme"}


# ── JSON extraction ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ('{"name": "Acme"}', {"name": "Acme"}),
    ('```json\n{"name": "Acme"}\n```', {"name": "Acme"}),
    ('Here you go:\n{"name": "Acme"} — done', {"name": "Acme"}),
    ('no json here', None),
    ('[1, 2, 3]', None),        # arrays aren't accepted (we want an object)
])
def test_extract_json(text, expected):
    assert mc._extract_json(text) == expected


def test_validate_reports_errors():
    assert mc._validate({"wrong": 1}, SCHEMA)        # non-empty → errors
    assert mc._validate({"name": "ok"}, SCHEMA) == []


def test_validate_error_messages_are_path_qualified():
    """A live gpt-nano failure logged three identical 'None is not of type string' errors with
    no way to tell which fields — bare jsonschema messages don't include the offending path.
    Every error _validate returns must name its field, so a future failure like that one is
    diagnosable straight from the log instead of requiring a live repro to find the culprit."""
    nested_schema = {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {
                "type": "object",
                "properties": {"label": {"type": "string"}},
                "required": ["label"],
                "additionalProperties": False,
            }},
        },
        "required": [],
        "additionalProperties": False,
    }
    errors = mc._validate({"items": [{"label": None}]}, nested_schema)
    assert errors == ["items[0].label: None is not of type 'string'"]


def test_prune_unknown_removes_top_level_extra_key():
    obj = {"name": "Acme", "extra": "nope"}
    removed = mc._prune_unknown(obj, SCHEMA)
    assert removed == ["extra"]
    assert obj == {"name": "Acme"}


def test_prune_unknown_removes_nested_array_item_key():
    obj = {"entities": [{"id": "e1", "roles": [
        {"relationship": "ceo", "target_id": "x", "date": "2020"}]}]}
    removed = mc._prune_unknown(obj, _NESTED_SCHEMA)
    assert removed == ["entities[0].roles[0].date"]
    assert "date" not in obj["entities"][0]["roles"][0]


def test_prune_unknown_leaves_free_form_object_untouched():
    # A hypothetical free-form object property: a plain {"type": "object"} with no
    # additionalProperties:False, so its contents must never be touched.
    free_form_schema = {
        "type": "object",
        "properties": {"file_metadata": {"type": "object"}},
        "required": [],
        "additionalProperties": False,
    }
    obj = {"file_metadata": {"author": "Jane Doe", "weird_key": 1}}
    removed = mc._prune_unknown(obj, free_form_schema)
    assert removed == []
    assert obj["file_metadata"] == {"author": "Jane Doe", "weird_key": 1}


def test_prune_unknown_no_op_when_nothing_extra():
    obj = {"name": "Acme"}
    assert mc._prune_unknown(obj, SCHEMA) == []
    assert obj == {"name": "Acme"}


def test_api_cost_uses_pricing_table():
    class U:
        input_tokens = 1_000_000
        output_tokens = 0
        cache_creation_input_tokens = 0
        cache_read_input_tokens = 0
    assert mc._api_cost("claude-sonnet-4-6", U()) == pytest.approx(3.0)   # $3 / 1M input


def test_batch_cost_is_half_the_api_cost():
    class U:
        input_tokens = 1_000_000
        output_tokens = 1_000_000
        cache_creation_input_tokens = 0
        cache_read_input_tokens = 0
    api = mc._api_cost("claude-sonnet-4-6", U())
    assert mc._batch_cost("claude-sonnet-4-6", U()) == pytest.approx(api * 0.5)


def test_batch_cost_none_for_unknown_model():
    assert mc._batch_cost("not-a-real-model", object()) is None


@pytest.mark.parametrize("tier, expected", [
    ("haiku", "claude-haiku-4-5"), ("sonnet", "claude-sonnet-4-6"),
    ("opus", "claude-opus-4-8"), ("claude-sonnet-4-6", "claude-sonnet-4-6"),
    # Sonnet 5 tier-alias groundwork (#361/#509, D165): `sonnet-4.6` is the new explicit,
    # version-pinned alias to the same id `sonnet` already resolves to; `sonnet-5` selects the
    # new Claude Sonnet 5 entry. Bare `sonnet` must keep resolving to 4.6 — regression check that
    # adding the new tier didn't silently move the default.
    ("sonnet-4.6", "claude-sonnet-4-6"), ("sonnet-5", "claude-sonnet-5"),
])
def test_resolve_model_id(tier, expected):
    assert mc.resolve_model_id(tier) == expected


def test_claude_batch_rejected_as_a_single_call_backend(api_key_auth):
    """claude-batch is submit/poll/collect only (#214) — routing a normal single-call task to
    it must fail clearly, not silently misbehave or read as 'unknown backend'."""
    with pytest.raises(mc.ModelError, match="batch-mode-only"):
        mc.complete_json(task="classify", prompt="p", schema=SCHEMA, backend="claude-batch")


def test_openai_batch_rejected_as_a_single_call_backend(api_key_auth):
    """openai-batch (#530) gets the same batch-mode-only guard as claude-batch."""
    with pytest.raises(mc.ModelError, match="batch-mode-only"):
        mc.complete_json(task="classify", prompt="p", schema=SCHEMA, backend="openai-batch")


def test_openai_batch_registered_alongside_claude_batch():
    assert "openai-batch" in mc.BACKENDS
    assert "openai-batch" in mc.BATCH_BACKENDS
    assert "claude-batch" in mc.BATCH_BACKENDS
    assert mc.provider_for_backend("openai-batch") == "openai"
    # openai-batch takes a raw OpenAI model id, not a Claude tier name — unlike claude-batch,
    # which is provider="anthropic" and so does belong in CLAUDE_BACKENDS.
    assert "openai-batch" not in mc.CLAUDE_BACKENDS


def test_looks_like_rate_limit_detects_429_and_text():
    assert mc._looks_like_rate_limit(429)                                  # HTTP 429
    assert mc._looks_like_rate_limit(None, "You've hit your session limit")
    assert mc._looks_like_rate_limit(None, "", "error: rate_limit")
    assert not mc._looks_like_rate_limit(500, "internal server error")
    assert not mc._looks_like_rate_limit(None, "all good")


def test_rate_limit_error_is_not_a_model_error():
    # Must NOT subclass ModelError, or extraction's retry + sectioning fallback would
    # swallow it instead of letting the orchestrator stop the batch.
    assert not issubclass(mc.RateLimitError, mc.ModelError)


def _fake_anthropic_client(monkeypatch, *, message=None, headers=None, error=None, calls=None):
    """Patch `anthropic.AsyncAnthropic` so `.messages.stream(...)` (#598, replacing
    `.with_raw_response.create`) is an async context manager whose `__aenter__` returns a fake
    stream — `get_final_message()` yields `message`, `.response.headers` yields `headers` — or
    raises `error`. The real SDK issues the HTTP request inside `__aenter__`, so that's also
    where a 429 now surfaces (`_api_complete_async`'s `except` wraps the whole `async with`).

    `calls`, when given a list, has each `.stream(**kwargs)` call's kwargs appended — for tests
    that need to inspect what was actually sent (e.g. whether `thinking` was included, #635)."""
    import anthropic

    class FakeResponse:
        def __init__(self, headers):
            self.headers = headers

    class FakeStream:
        def __init__(self, message, headers):
            self._message = message
            self.response = FakeResponse(headers)

        async def get_final_message(self):
            return self._message

    class FakeStreamManager:
        async def __aenter__(self):
            if error is not None:
                raise error
            return FakeStream(message, headers)

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeMessages:
        def stream(self, **kwargs):
            if calls is not None:
                calls.append(kwargs)
            return FakeStreamManager()

    class FakeClient:
        def __init__(self, api_key=None):
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeClient)


def test_anthropic_backend_captures_rate_limit_headers_on_success(monkeypatch):
    class FakeUsage:
        def model_dump(self):
            return {"input_tokens": 10, "output_tokens": 5}

    class FakeBlock:
        type = "text"
        text = '{"name": "Acme"}'

    class FakeMessage:
        content = [FakeBlock()]
        usage = FakeUsage()
        stop_reason = "end_turn"

    _fake_anthropic_client(
        monkeypatch, message=FakeMessage(),
        headers={"anthropic-ratelimit-tokens-limit": "40000",
                "anthropic-ratelimit-tokens-remaining": "39000",
                "anthropic-ratelimit-tokens-reset": "2026-08-09T12:00:00Z"})
    out = asyncio.run(mc._api_complete_async("p", "claude-sonnet-4-6", SCHEMA, "sk-x", 8000))
    assert out["text"] == '{"name": "Acme"}'
    assert out["rate_limit"] == {"limit_tokens": 40000, "remaining_tokens": 39000,
                                 "reset_tokens": "2026-08-09T12:00:00Z"}


def test_anthropic_backend_429_carries_rate_limit_headers_on_the_exception(monkeypatch):
    import anthropic
    import httpx

    resp = httpx.Response(
        429,
        headers={"anthropic-ratelimit-tokens-limit": "40000",
                "anthropic-ratelimit-tokens-remaining": "0",
                "anthropic-ratelimit-tokens-reset": "2026-08-09T12:01:00Z"},
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    err = anthropic.RateLimitError("rate limited", response=resp, body=None)
    _fake_anthropic_client(monkeypatch, error=err)
    with pytest.raises(mc.RateLimitError) as exc_info:
        asyncio.run(mc._api_complete_async("p", "claude-sonnet-4-6", SCHEMA, "sk-x", 8000))
    assert exc_info.value.rate_limit == {"limit_tokens": 40000, "remaining_tokens": 0,
                                         "reset_tokens": "2026-08-09T12:01:00Z"}


def _fake_anthropic_message(text='{"name": "Acme"}'):
    class FakeUsage:
        def model_dump(self):
            return {"input_tokens": 10, "output_tokens": 5}

    class FakeBlock:
        type = "text"

    block = FakeBlock()
    block.text = text

    class FakeMessage:
        content = [block]
        usage = FakeUsage()
        stop_reason = "end_turn"

    return FakeMessage()


# ── thinking (#635, D206) ────────────────────────────────────────────────────

def test_api_complete_sends_thinking_for_a_model_that_defaults_off(monkeypatch):
    calls = []
    _fake_anthropic_client(monkeypatch, message=_fake_anthropic_message(), headers={}, calls=calls)
    asyncio.run(mc._api_complete_async("p", "claude-sonnet-4-6", SCHEMA, "sk-x", 8000))
    assert calls[0]["thinking"] == mc._THINKING_ADAPTIVE


def test_api_complete_sends_thinking_for_opus_4_8(monkeypatch):
    calls = []
    _fake_anthropic_client(monkeypatch, message=_fake_anthropic_message(), headers={}, calls=calls)
    asyncio.run(mc._api_complete_async("p", "claude-opus-4-8", SCHEMA, "sk-x", 8000))
    assert calls[0]["thinking"] == mc._THINKING_ADAPTIVE


def test_api_complete_omits_thinking_for_a_model_already_on_by_default(monkeypatch):
    # Sonnet 5 / Opus 5 ship with thinking on already — sending the param isn't needed and would
    # risk overriding the vendor default in a way this fix never intended.
    calls = []
    _fake_anthropic_client(monkeypatch, message=_fake_anthropic_message(), headers={}, calls=calls)
    asyncio.run(mc._api_complete_async("p", "claude-sonnet-5", SCHEMA, "sk-x", 8000))
    assert "thinking" not in calls[0]


def test_api_complete_omits_thinking_for_haiku(monkeypatch):
    calls = []
    _fake_anthropic_client(monkeypatch, message=_fake_anthropic_message(), headers={}, calls=calls)
    asyncio.run(mc._api_complete_async("p", "claude-haiku-4-5", SCHEMA, "sk-x", 8000))
    assert "thinking" not in calls[0]


def test_api_complete_composes_thinking_with_effort(monkeypatch):
    # `thinking` and `effort` are independent controls (Anthropic docs) — both should land on
    # the same call, `effort` inside `output_config`, `thinking` as its own top-level kwarg.
    calls = []
    _fake_anthropic_client(monkeypatch, message=_fake_anthropic_message(), headers={}, calls=calls)
    asyncio.run(mc._api_complete_async("p", "claude-opus-4-8", SCHEMA, "sk-x", 8000,
                                       effort="medium"))
    assert calls[0]["thinking"] == mc._THINKING_ADAPTIVE
    assert calls[0]["output_config"]["effort"] == "medium"


def test_api_complete_omits_thinking_on_a_continuation(monkeypatch):
    # Anthropic rejects prefilling the assistant turn while thinking is on, and a continuation
    # (`prefix` set) does exactly that to resume a truncated call — thinking must stay off on
    # the retry even though the model needs it explicitly enabled on a fresh call.
    calls = []
    _fake_anthropic_client(monkeypatch, message=_fake_anthropic_message(), headers={}, calls=calls)
    asyncio.run(mc._api_complete_async("p", "claude-sonnet-4-6", SCHEMA, "sk-x", 8000,
                                       prefix="partial output"))
    assert "thinking" not in calls[0]


def test_api_complete_surfaces_anthropic_reasoning_tokens(monkeypatch):
    # The bug this fixes: a thinking-enabled Claude call's usage carries thinking tokens under
    # output_tokens_details.thinking_tokens, but nothing read that key — every reader here checks
    # completion_tokens_details.reasoning_tokens (OpenAI's shape) and silently saw nothing.
    class FakeUsage:
        def model_dump(self):
            return {"input_tokens": 9408, "output_tokens": 1500,
                   "output_tokens_details": {"thinking_tokens": 1200}}

    class FakeBlock:
        type = "text"
        text = '{"name": "Acme"}'

    class FakeMessage:
        content = [FakeBlock()]
        usage = FakeUsage()
        stop_reason = "end_turn"

    _fake_anthropic_client(monkeypatch, message=FakeMessage(), headers={})
    out = asyncio.run(mc._api_complete_async("p", "claude-sonnet-4-6", SCHEMA, "sk-x", 8000))
    assert out["usage"]["completion_tokens_details"]["reasoning_tokens"] == 1200


def test_claude_envelope_requires_streaming():
    # The Anthropic SDK (0.116.0) refuses a *non-streaming* request once
    # `3600 * max_tokens / 128_000 > 600`, i.e. max_tokens > 21,333
    # (`Anthropic._calculate_nonstreaming_timeout`). Sonnet 4.6's catalogued envelope (#598) sits
    # well past that threshold — this is why `_api_complete_async` streams instead of calling
    # `.create()`; it's the regression guard against reverting to the non-streaming call, which
    # would raise at request time for exactly this model/envelope.
    non_streaming_guard = 128_000 * 600 // 3600   # 21,333
    envelope = mc._wire_max_tokens("claude-api", "claude-sonnet-4-6")
    assert envelope > non_streaming_guard


def test_acomplete_json_carries_rate_limit_onto_model_result(api_key_auth, monkeypatch):
    async def be(prompt, model_id, schema, api_key, max_tokens, effort=None, prefix=None):
        return {"text": '{"name": "Acme"}', "usage": {"input_tokens": 10}, "cost_usd": 0.01,
                "rate_limit": {"limit_tokens": 100, "remaining_tokens": 50, "reset_tokens": "1m0s"}}
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", be)
    r = mc.complete_json(task="t", prompt="p", schema=SCHEMA, backend="claude-api")
    assert r.rate_limit == {"limit_tokens": 100, "remaining_tokens": 50, "reset_tokens": "1m0s"}


# ── response pagination / truncation guard (#343) ──────────────────────────────

class PagingBackend:
    """Backend fake that supports prefill continuation. Returns queued (text, finish_reason)
    rounds in order and records the `prefix` each call received, so a test can assert both the
    assembled output and that continuation actually happened (or didn't)."""
    def __init__(self, *rounds, cost=0.01):
        self.rounds = list(rounds)      # each: (text, finish_reason)
        self.cost = cost
        self.calls = []

    async def __call__(self, prompt, model_id, schema, api_key, max_tokens, effort=None, prefix=None):
        self.calls.append({"prefix": prefix, "max_tokens": max_tokens})
        text, finish = self.rounds.pop(0)
        return {"text": text, "usage": {"output_tokens": 5}, "cost_usd": self.cost,
                "finish_reason": finish}


@pytest.fixture
def deepseek_key(monkeypatch):
    monkeypatch.setattr(mc.auth, "get_api_key",
                        lambda provider="anthropic": "sk-ds" if provider == "deepseek" else None)


@pytest.mark.parametrize("finish, truncated", [
    ("length", True), ("max_tokens", True), ("MAX_TOKENS", True),
    ("stop", False), ("end_turn", False), (None, False), ("", False),
])
def test_is_truncated(finish, truncated):
    assert mc._is_truncated(finish) is truncated


def test_truncated_response_continues_until_complete(api_key_auth, monkeypatch):
    # claude-api can prefill: a max-token cut is continued from the partial and concatenated.
    be = PagingBackend(('{"name": "Ac', "max_tokens"), ('me"}', "end_turn"))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", be)
    r = mc.complete_json(task="extract", prompt="p", schema=SCHEMA, backend="claude-api")
    assert r.parsed == {"name": "Acme"}
    assert be.calls[0]["prefix"] is None
    assert be.calls[1]["prefix"] == '{"name": "Ac'      # partial prefilled to continue
    assert r.cost_usd == pytest.approx(0.02)            # cost summed across both rounds


def test_deepseek_paginates(deepseek_key, monkeypatch):
    # DeepSeek's prefix-completion beta is also a continuation backend.
    be = PagingBackend(('{"na', "length"), ('me": "Acme"}', "stop"))
    monkeypatch.setitem(mc._ABACKENDS, "deepseek", be)
    r = mc.complete_json(task="extract", prompt="p", schema=SCHEMA, backend="deepseek",
                         model="deepseek-v4-flash")
    assert r.parsed == {"name": "Acme"}
    assert be.calls[1]["prefix"] == '{"na'


def test_truncated_result_rejected_even_when_parseable(openai_key, monkeypatch):
    # openai returns a *new* message, not a continuation, so it can't paginate. A truncated result
    # must never be accepted even though this partial happens to be valid JSON — it errors so the
    # orchestrator falls back to bounded-output sectioning. Truncation is deterministic in the
    # prompt, so it short-circuits the retry loop (one call, no wasted re-run) rather than retrying.
    be = PagingBackend(('{"name": "Acme"}', "length"), ('{"name": "Acme"}', "length"))
    monkeypatch.setitem(mc._ABACKENDS, "openai", be)
    with pytest.raises(mc.ModelError, match="truncated at the model's max-token ceiling"):
        mc.complete_json(task="extract", prompt="p", schema=SCHEMA, backend="openai", model="gpt-4o")
    assert len(be.calls) == 1                            # no wasted retry of an un-continuable cut
    assert be.calls[0]["prefix"] is None                # never attempted to continue


def test_truncated_error_carries_a_structured_truncated_flag(openai_key, monkeypatch):
    # #540: the orchestrator's section-level re-split fallback needs a structured way to tell "this
    # ModelError was a truncation" apart from any other failure — matching on last_err's message
    # text would be brittle (#547 already changed one of those strings once).
    be = PagingBackend(('{"name": "Acme"}', "length"))
    monkeypatch.setitem(mc._ABACKENDS, "openai", be)
    with pytest.raises(mc.ModelError) as exc_info:
        mc.complete_json(task="extract", prompt="p", schema=SCHEMA, backend="openai", model="gpt-4o")
    assert exc_info.value.truncated is True


def test_json_validation_failure_is_not_flagged_truncated(api_key_auth, monkeypatch):
    # A schema-validation failure is a different failure from a truncation (#540) — `.truncated`
    # must stay False so a caller can't mistake one for the other.
    be = PagingBackend(("not json", "end_turn"))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", be)
    with pytest.raises(mc.ModelError) as exc_info:
        mc.complete_json(task="extract", prompt="p", schema=SCHEMA, backend="claude-api",
                         max_retries=0)
    assert exc_info.value.truncated is False


def test_truncated_empty_text_reports_reasoning_starvation(openai_key, monkeypatch):
    # #354: the model spent its whole output budget on chain-of-thought and returned zero
    # visible characters — a different failure from an ordinary partial truncation ("the
    # document was too dense"), so it gets its own message naming the reasoning-token count and
    # pointing at the actual fix (a lower extractor_effort).
    async def backend(prompt, model_id, schema, api_key, max_tokens, effort=None, prefix=None):
        return {"text": "", "usage": {"completion_tokens": 48000,
                                      "completion_tokens_details": {"reasoning_tokens": 48000}},
                "cost_usd": 0.01, "finish_reason": "length"}
    monkeypatch.setitem(mc._ABACKENDS, "openai", backend)
    with pytest.raises(mc.ModelError,
                       match="entire 48,000-token output budget on internal reasoning") as exc_info:
        mc.complete_json(task="extract", prompt="p", schema=SCHEMA, backend="openai", model="gpt-4o")
    # #558: a caller needs to tell starvation apart from an ordinary truncation structurally, not
    # by matching this message's text — re-splitting the input doesn't fix a starved call.
    assert exc_info.value.truncated is True
    assert exc_info.value.starved is True


def test_truncated_partial_text_keeps_ordinary_message_when_the_answer_dominated(openai_key,
                                                                                 monkeypatch):
    # A response whose visible answer outweighs its chain-of-thought really did run out of room
    # writing the answer — that one is a density problem, and re-sectioning is the right fix, so
    # it keeps the plain truncation message even though reasoning tokens were reported.
    async def backend(prompt, model_id, schema, api_key, max_tokens, effort=None, prefix=None):
        return {"text": '{"name": "Ac', "usage": {"completion_tokens": 48000,
                                                   "completion_tokens_details":
                                                       {"reasoning_tokens": 8000}},
                "cost_usd": 0.01, "finish_reason": "length"}
    monkeypatch.setitem(mc._ABACKENDS, "openai", backend)
    with pytest.raises(mc.ModelError,
                       match="truncated at the model's max-token ceiling") as exc_info:
        mc.complete_json(task="extract", prompt="p", schema=SCHEMA, backend="openai", model="gpt-4o")
    # #558: an ordinary truncation (answer outweighed reasoning) must not be flagged starved —
    # re-splitting the input is the right recovery here, not an effort-level retry.
    assert exc_info.value.starved is False


def test_truncated_partial_text_reports_starvation_when_reasoning_dominated(openai_key,
                                                                           monkeypatch):
    # #547: starvation also has a partial-text shape — reasoning eats most of the envelope, the
    # model starts writing, and the ceiling cuts it a few hundred tokens in. Non-empty text, so
    # #354's empty-text branch misses it and the caller was told to re-section sections that were
    # already small enough. The counts here are the real ones from the failing Gemini call.
    async def backend(prompt, model_id, schema, api_key, max_tokens, effort=None, prefix=None):
        return {"text": '{"document": {"key_facts": [{"fact": "On April 22, 2022, the aud',
                "usage": {"completion_tokens": 15984,
                          "completion_tokens_details": {"reasoning_tokens": 15137}},
                "cost_usd": 0.01, "finish_reason": "length"}
    monkeypatch.setitem(mc._ABACKENDS, "openai", backend)
    with pytest.raises(mc.ModelError,
                       match="spent 15,137 of its output budget on internal reasoning, leaving "
                             "only 847 tokens of answer") as exc_info:
        mc.complete_json(task="extract", prompt="p", schema=SCHEMA, backend="openai", model="gpt-4o")
    assert exc_info.value.starved is True   # #558: the partial-text starvation shape too


def test_truncated_partial_text_without_usage_keeps_ordinary_message(openai_key, monkeypatch):
    # No reasoning count reported at all (any non-thinking backend) — the starvation branch must
    # not fire on a zero, or every ordinary truncation would be misdiagnosed as starvation.
    async def backend(prompt, model_id, schema, api_key, max_tokens, effort=None, prefix=None):
        return {"text": '{"name": "Ac', "usage": None, "cost_usd": 0.01, "finish_reason": "length"}
    monkeypatch.setitem(mc._ABACKENDS, "openai", backend)
    with pytest.raises(mc.ModelError, match="truncated at the model's max-token ceiling"):
        mc.complete_json(task="extract", prompt="p", schema=SCHEMA, backend="openai", model="gpt-4o")


def test_continuation_stops_at_the_guard(api_key_auth, monkeypatch):
    # A backend that never stops naturally is capped at _MAX_CONTINUATIONS and reported truncated,
    # so a pathological run falls back to sectioning instead of looping forever.
    rounds = [("x", "length")] * (mc._MAX_CONTINUATIONS + 5)
    be = PagingBackend(*rounds)
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", be)
    with pytest.raises(mc.ModelError, match="truncated"):
        mc.complete_json(task="extract", prompt="p", schema=SCHEMA, backend="claude-api",
                         max_retries=0)
    # first call + exactly _MAX_CONTINUATIONS continuation rounds, then it gives up
    assert len(be.calls) == mc._MAX_CONTINUATIONS + 1


def test_natural_stop_is_not_paginated(api_key_auth, monkeypatch):
    be = PagingBackend(('{"name": "Acme"}', "end_turn"))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", be)
    r = mc.complete_json(task="extract", prompt="p", schema=SCHEMA, backend="claude-api")
    assert r.parsed == {"name": "Acme"}
    assert len(be.calls) == 1                            # no continuation for a natural stop


# ── fixture capture (#352) ────────────────────────────────────────────────────
# `acomplete_json`/`_complete_with_pagination` call `fixture_capture.capture` at each condition
# it observes; capture is a no-op unless a benchmark run has called `fixture_capture.enable`, so
# these tests enable it explicitly to assert the hooks actually fire with the right condition.

@pytest.fixture
def capture_dir(tmp_path):
    fc.enable(tmp_path)
    yield tmp_path
    fc.disable()


def _captured(directory, condition):
    import json
    return [json.loads(f.read_text(encoding="utf-8")) for f in directory.glob(f"{condition}-*.json")]


def test_truncation_is_captured(openai_key, monkeypatch, capture_dir):
    be = PagingBackend(('{"name": "Acme"}', "length"))
    monkeypatch.setitem(mc._ABACKENDS, "openai", be)
    with pytest.raises(mc.ModelError):
        mc.complete_json(task="extract", prompt="p", schema=SCHEMA, backend="openai", model="gpt-4o")
    records = _captured(capture_dir, "truncation")
    assert len(records) == 1
    assert records[0]["backend"] == "openai"
    assert records[0]["task"] == "extract"


def test_malformed_json_is_captured(api_key_auth, monkeypatch, capture_dir):
    api = FakeBackend(_out("not json"), _out("still not json"))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    with pytest.raises(mc.ModelError):
        mc.complete_json(task="extract", prompt="p", schema=SCHEMA, max_retries=1)
    records = _captured(capture_dir, "malformed_json")
    assert len(records) == 2                      # one per failed attempt
    assert {r["text"] for r in records} == {"not json", "still not json"}


def test_schema_drift_is_captured(api_key_auth, monkeypatch, capture_dir):
    api = FakeBackend(_out('{"name": "Acme", "extra": 1}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    r = mc.complete_json(task="extract", prompt="p", schema=SCHEMA)
    assert r.parsed == {"name": "Acme"}
    records = _captured(capture_dir, "schema_drift")
    assert len(records) == 1
    assert records[0]["removed"] == ["extra"]


def test_no_capture_when_response_is_clean(api_key_auth, monkeypatch, capture_dir):
    api = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    mc.complete_json(task="extract", prompt="p", schema=SCHEMA)
    assert list(capture_dir.glob("*.json")) == []


def test_continuation_is_captured(api_key_auth, monkeypatch, capture_dir):
    be = PagingBackend(('{"name": "Ac', "max_tokens"), ('me"}', "end_turn"))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", be)
    mc.complete_json(task="extract", prompt="p", schema=SCHEMA, backend="claude-api")
    records = _captured(capture_dir, "continuation")
    assert len(records) == 1
    assert records[0]["prefix"] == '{"name": "Ac'
    assert records[0]["continuation_text"] == 'me"}'
    assert records[0]["round"] == 1


def test_capture_disabled_by_default(api_key_auth, monkeypatch, tmp_path):
    # No capture_dir fixture here — fixture_capture must stay off unless explicitly enabled.
    api = FakeBackend(_out("not json"))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    with pytest.raises(mc.ModelError):
        mc.complete_json(task="extract", prompt="p", schema=SCHEMA, max_retries=0)
    assert not fc.enabled()


def test_merge_usage_sums_numeric_counts():
    a = {"input_tokens": 10, "output_tokens": 5, "model": "x"}
    b = {"input_tokens": 3, "output_tokens": 7, "model": "x"}
    assert mc._merge_usage(a, b) == {"input_tokens": 13, "output_tokens": 12, "model": "x"}


def test_merge_usage_handles_nested_and_one_sided():
    a = {"prompt_tokens_details": {"cached_tokens": 4}, "input_tokens": 2}
    b = {"prompt_tokens_details": {"cached_tokens": 1}, "output_tokens": 9}
    assert mc._merge_usage(a, b) == {
        "prompt_tokens_details": {"cached_tokens": 5}, "input_tokens": 2, "output_tokens": 9}
    assert mc._merge_usage(None, b) == b                 # one side missing → the other passes through
    assert mc._merge_usage(a, None) == a


def test_merge_usage_does_not_add_booleans():
    # bools are ints in Python; a flag must not be arithmetically summed into a token count —
    # guarded on both sides, so an asymmetric (bool, int) pairing is left alone too, not coerced.
    assert mc._merge_usage({"cache_hit": True}, {"cache_hit": True}) == {"cache_hit": True}
    assert mc._merge_usage({"cache_hit": True}, {"cache_hit": 3}) == {"cache_hit": True}
    assert mc._merge_usage({"cache_hit": 3}, {"cache_hit": True}) == {"cache_hit": 3}


def test_merge_usage_sums_agent_sdk_timing_fields():
    # #402: duration_api_ms/num_turns are plain numeric fields once inside the usage dict, so a
    # continuation round's harness timing accumulates the same way token counts do — deliberate,
    # not an accident of the generic dict-merge (arguably correct: the total call spent that much
    # time across both API requests, and made that many internal turns in total).
    a = {"input_tokens": 10, "duration_api_ms": 500, "num_turns": 1}
    b = {"input_tokens": 3, "duration_api_ms": 200, "num_turns": 2}
    assert mc._merge_usage(a, b) == {"input_tokens": 13, "duration_api_ms": 700, "num_turns": 3}


# ── agent-SDK harness timing (#402) ─────────────────────────────────────────────

def _result_message(**attrs):
    """A stand-in for `claude_agent_sdk.ResultMessage` — `_agent_query` only dispatches on
    `type(message).__name__`, so a dynamically-named plain object works fine."""
    defaults = dict(result="", total_cost_usd=None, usage=None, is_error=False,
                    api_error_status=None, duration_api_ms=None, num_turns=None, content=None)
    defaults.update(attrs)
    return type("ResultMessage", (), defaults)()


def _patch_agent_query(monkeypatch, message):
    """Stands in for the real `claude_agent_sdk` package via `sys.modules`, so these tests don't
    require it installed — CI's test job deliberately excludes heavy SDK deps (see ci.yml)."""
    import sys
    import types

    async def fake_query(prompt, options):
        yield message

    fake_module = types.ModuleType("claude_agent_sdk")
    fake_module.query = fake_query
    fake_module.ClaudeAgentOptions = lambda **kwargs: kwargs
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_module)
    # `_agent_supports_tools` is lru_cached and introspects whatever ClaudeAgentOptions is
    # installed — clear it so a verdict from another test's fake SDK can't leak in here (D145).
    mc._agent_supports_tools.cache_clear()


def test_agent_query_captures_harness_timing_into_usage(monkeypatch):
    msg = _result_message(result='{"name": "Acme"}', total_cost_usd=0.02,
                          usage={"input_tokens": 10}, duration_api_ms=1234, num_turns=3)
    _patch_agent_query(monkeypatch, msg)
    out = asyncio.run(mc._agent_query("p", "model", None))
    assert out["usage"] == {"input_tokens": 10, "duration_api_ms": 1234, "num_turns": 3}


def test_agent_query_timing_present_even_without_token_usage(monkeypatch):
    # The SDK's `usage` dict is optional but duration_api_ms/num_turns are not (#402) — the
    # harness timing must still surface even when there's no token-usage dict to piggyback on.
    msg = _result_message(result="{}", usage=None, duration_api_ms=500, num_turns=1)
    _patch_agent_query(monkeypatch, msg)
    out = asyncio.run(mc._agent_query("p", "model", None))
    assert out["usage"] == {"duration_api_ms": 500, "num_turns": 1}


def test_agent_query_usage_normalizes_non_dict_shape_without_crashing(monkeypatch):
    # Defensive normalization: a weird (non-dict, non-None) `usage` shape must not crash the
    # call — it's discarded rather than trusted, and harness timing still lands.
    msg = _result_message(result="{}", usage="not-a-dict", duration_api_ms=42, num_turns=2)
    _patch_agent_query(monkeypatch, msg)
    out = asyncio.run(mc._agent_query("p", "model", None))
    assert out["usage"] == {"duration_api_ms": 42, "num_turns": 2}


def test_agent_query_usage_stays_none_when_nothing_present(monkeypatch):
    # No token usage and no timing (e.g. an older SDK build) — usage stays None, matching the
    # pre-#402 contract, rather than becoming a spurious empty dict.
    msg = _result_message(result="{}", usage=None, duration_api_ms=None, num_turns=None)
    _patch_agent_query(monkeypatch, msg)
    out = asyncio.run(mc._agent_query("p", "model", None))
    assert out["usage"] is None


@pytest.mark.parametrize("backend, model_id", [
    ("claude-api", "claude-sonnet-4-6"),
    ("claude-api", "claude-haiku-4-5"),
    ("claude-api", "claude-opus-4-8"),
    ("deepseek", "deepseek-v4-flash"),
    ("deepseek", "deepseek-v4-flash-thinking"),   # -thinking marker doesn't change the envelope
    ("openai", "gpt-5.4"),
    ("gemini", "gemini-3.5-flash"),
])
def test_wire_max_tokens_derives_from_catalog(backend, model_id):
    # #598: one per-model envelope — the catalogued `max_output_tokens` cap under
    # `_OUTPUT_HEADROOM` — replaces the old per-task base plus per-provider reasoning reserve.
    bare = model_id[: -len(mc._DEEPSEEK_THINKING_SUFFIX)] if model_id.endswith(
        mc._DEEPSEEK_THINKING_SUFFIX) else model_id
    from watchdog.model_catalog import catalog_max_output_tokens
    cap = catalog_max_output_tokens(bare)
    assert cap is not None
    assert mc._wire_max_tokens(backend, model_id) == int(cap * (1 - mc._OUTPUT_HEADROOM))


def test_wire_max_tokens_uncatalogued_falls_back_to_default():
    # An id with no catalog entry (a raw id past CLI validation, or a local/OpenRouter model) —
    # we don't know its real cap, so this keeps the historical hand-picked value rather than
    # guessing upward.
    expected = int(mc._DEFAULT_MAX_OUTPUT_TOKENS * (1 - mc._OUTPUT_HEADROOM))
    assert mc._wire_max_tokens("openai", "totally-unknown-model") == expected
    assert mc._wire_max_tokens("local", "llama-3.3-70b") == expected


@pytest.mark.parametrize("backend, model", [
    ("openai", "gpt-5.4"),                # OpenAI reasoning model — used to get an effort-scaled reserve
    ("gemini", "gemini-3.5-flash"),       # Gemini — used to get an effort-scaled reserve unconditionally
    ("claude-api", "claude-sonnet-4-6"),  # never had a reserve
])
def test_wire_max_tokens_is_deterministic_for_fixed_args(backend, model):
    # `_wire_max_tokens(backend, model_id)` doesn't take task/effort parameters at all (#598
    # removed the per-task base and the per-provider reasoning reserve — see its docstring), so
    # calling it twice with identical args can only verify it's deterministic/idempotent for
    # those args, not "unaffected by task or effort" — there's no task/effort input here to vary.
    assert mc._wire_max_tokens(backend, model) == mc._wire_max_tokens(backend, model)


@pytest.mark.parametrize("backend, model", [
    ("claude-api", "sonnet"),           # continuation backend — pagination grows past the cap
    ("claude-agent-sdk", "sonnet"),     # no enforced output ceiling
    ("deepseek", "deepseek-v4-flash-thinking"),
    (None, None),                       # unresolved → routes to a Claude backend
])
def test_output_ceiling_is_none_when_nothing_to_protect(backend, model):
    assert mc.output_ceiling_for_sectioning(backend, model) is None


@pytest.mark.parametrize("backend, model", [
    ("openai", "gpt-5.4"),
    ("gemini", "gemini-3.5-flash"),
    ("local", "llama-3.3-70b"),
    ("openrouter", "anthropic/claude-3.5-sonnet"),
])
def test_output_ceiling_returned_for_non_continuation_capped_backends(backend, model):
    # openai, gemini, local, and openrouter enforce max_tokens yet can't continue — a real
    # number, matching `_wire_max_tokens` exactly (#598: no more per-task/effort variation).
    model_id = mc.resolve_model_id(model)
    assert mc.output_ceiling_for_sectioning(backend, model) == mc._wire_max_tokens(backend, model_id)


def _fake_httpx_sequence(monkeypatch, status_codes):
    """Patch httpx.AsyncClient to return canned responses with the given status codes, one per
    post, repeating the last one if posts continue. Returns the list of recorded post calls."""
    import httpx

    codes = list(status_codes)
    posts = []

    class FakeResp:
        headers = {}

        def __init__(self, status_code):
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=None,
                                            response=None)

        def json(self):
            return {"choices": [{"message": {"content": '{"name": "Acme"}'}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            posts.append(url)
            return FakeResp(codes.pop(0) if len(codes) > 1 else codes[0])

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    return posts


def _no_sleep(monkeypatch):
    """Stub the retry backoff so tests don't actually wait; returns the recorded delays."""
    delays = []

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(mc.asyncio, "sleep", fake_sleep)
    return delays


def test_openai_backend_retries_transient_5xx(monkeypatch):
    # Two 502s then a 200 → the call succeeds after backing off twice (#354).
    posts = _fake_httpx_sequence(monkeypatch, [502, 502, 200])
    delays = _no_sleep(monkeypatch)
    out = asyncio.run(mc._openai_complete_async("p", "deepseek-v4-flash", SCHEMA, "sk-ds", 8000,
                                                base_url="https://api.deepseek.com"))
    assert out["text"] == '{"name": "Acme"}'
    assert len(posts) == 3
    assert delays == [mc._TRANSIENT_BACKOFF_S, mc._TRANSIENT_BACKOFF_S * 2]


def test_openai_backend_gives_up_after_bounded_5xx_retries(monkeypatch):
    # A persistent 5xx exhausts the retry budget and raises — it must not loop forever.
    import httpx
    posts = _fake_httpx_sequence(monkeypatch, [502])
    _no_sleep(monkeypatch)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(mc._openai_complete_async("p", "deepseek-v4-flash", SCHEMA, "sk-ds", 8000,
                                              base_url="https://api.deepseek.com"))
    assert len(posts) == mc._TRANSIENT_RETRIES + 1


def test_openai_backend_never_retries_429(monkeypatch):
    # 429 is a session-wide condition, not a transient blip: one post, straight to the typed
    # RateLimitError so the orchestrator stops the batch cleanly.
    posts = _fake_httpx_sequence(monkeypatch, [429])
    delays = _no_sleep(monkeypatch)
    with pytest.raises(mc.RateLimitError):
        asyncio.run(mc._openai_complete_async("p", "deepseek-v4-flash", SCHEMA, "sk-ds", 8000,
                                              base_url="https://api.deepseek.com"))
    assert len(posts) == 1
    assert delays == []


# ── agent SDK: built-in tools stay out of the request (D145, #475) ─────────────

def _patch_agent_query_capturing(monkeypatch, *, with_tools_field: bool,
                                 with_thinking_field: bool = False):
    """Like `_patch_agent_query`, but returns a dict that captures the options `_agent_query`
    built. `with_tools_field`/`with_thinking_field` control whether the stand-in
    `ClaudeAgentOptions` dataclass declares a `tools`/`thinking` field, so both sides of each
    version guard can be exercised independently."""
    import sys
    import types
    import dataclasses

    captured = {}

    async def fake_query(prompt, options):
        captured.update(options)
        yield _result_message(result="{}", usage={"input_tokens": 1})

    fields = [("model", object, None), ("system_prompt", object, None),
              ("allowed_tools", object, None), ("setting_sources", object, None),
              ("max_turns", object, None), ("env", object, None), ("effort", object, None)]
    if with_tools_field:
        fields.append(("tools", object, None))
    if with_thinking_field:
        fields.append(("thinking", object, None))
    Options = dataclasses.make_dataclass(
        "ClaudeAgentOptions", [(n, t, dataclasses.field(default=d)) for n, t, d in fields])

    fake_module = types.ModuleType("claude_agent_sdk")
    fake_module.query = fake_query
    # `_agent_query` calls ClaudeAgentOptions(**opts); returning the kwargs keeps the capture
    # simple while `dataclasses.fields` on the real class drives the version guard.
    fake_module.ClaudeAgentOptions = lambda **kw: kw
    fake_module.ClaudeAgentOptions.__wrapped_dataclass__ = Options
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_module)
    monkeypatch.setattr(mc, "_agent_supports_tools", lambda: with_tools_field)
    monkeypatch.setattr(mc, "_agent_supports_thinking", lambda: with_thinking_field)
    return captured


def test_agent_query_disables_builtin_tools(monkeypatch):
    """`allowed_tools=[]` only withholds auto-approval — every Claude Code tool stayed *defined*
    in the request, measured at ~11.2K tokens per call billed at the cache-write rate, to
    describe tools this stage may not call. `tools=[]` is the knob that removes them."""
    captured = _patch_agent_query_capturing(monkeypatch, with_tools_field=True)
    asyncio.run(mc._agent_query("p", "model", None))
    assert captured["tools"] == []
    assert captured["allowed_tools"] == []   # still no auto-approval; the two are independent


def test_agent_query_omits_tools_on_an_older_sdk(monkeypatch):
    """`tools` postdates the `claude-agent-sdk>=0.1` floor, and ClaudeAgentOptions is a
    dataclass — passing it blind would TypeError. An older SDK just keeps the old behaviour."""
    captured = _patch_agent_query_capturing(monkeypatch, with_tools_field=False)
    asyncio.run(mc._agent_query("p", "model", None))
    assert "tools" not in captured


def test_agent_query_opts_out_of_claude_ai_connectors_and_nonessential_traffic(monkeypatch):
    """These are headless, tools-disabled single-turn calls over documents that are often
    privileged or confidential — claude.ai connectors are never reachable from them, and
    telemetry/error-reporting/other non-essential traffic serves no purpose here. Opting out
    via env var (#491) avoids the CLI's per-call "connectors are disabled" stderr warning
    under api-key auth, and keeps subprocess network chatter to just the model call."""
    captured = _patch_agent_query_capturing(monkeypatch, with_tools_field=True)
    asyncio.run(mc._agent_query("p", "model", None))
    assert captured["env"]["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false"
    assert captured["env"]["DISABLE_TELEMETRY"] == "1"
    assert captured["env"]["DISABLE_ERROR_REPORTING"] == "1"
    assert captured["env"]["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


def test_agent_query_env_opt_outs_do_not_clobber_caller_env(monkeypatch):
    """The api-key auth mode passes `env={"ANTHROPIC_API_KEY": ...}` through — the opt-outs
    above must be additive, not a replacement for it."""
    captured = _patch_agent_query_capturing(monkeypatch, with_tools_field=True)
    asyncio.run(mc._agent_query("p", "model", {"ANTHROPIC_API_KEY": "sk-test"}))
    assert captured["env"] == {
        "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "ANTHROPIC_API_KEY": "sk-test",
    }


def test_agent_supports_tools_detects_the_real_sdk_dataclass(monkeypatch):
    """The guard reads the installed dataclass's fields rather than a version string."""
    import sys
    import types
    import dataclasses

    for has in (True, False):
        names = [("allowed_tools", list, dataclasses.field(default_factory=list))]
        if has:
            names.append(("tools", object, dataclasses.field(default=None)))
        mod = types.ModuleType("claude_agent_sdk")
        mod.ClaudeAgentOptions = dataclasses.make_dataclass("ClaudeAgentOptions", names)
        monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)
        mc._agent_supports_tools.cache_clear()
        assert mc._agent_supports_tools() is has
    mc._agent_supports_tools.cache_clear()


# ── agent SDK: thinking (#635, D206) ────────────────────────────────────────────

def test_agent_query_sends_thinking_for_a_model_that_defaults_off(monkeypatch):
    captured = _patch_agent_query_capturing(monkeypatch, with_tools_field=True,
                                            with_thinking_field=True)
    asyncio.run(mc._agent_query("p", "claude-sonnet-4-6", None))
    assert captured["thinking"] == mc._THINKING_ADAPTIVE


def test_agent_query_omits_thinking_for_a_model_already_on_by_default(monkeypatch):
    # Sonnet 5 / Opus 5 ship with thinking on already — no catalog `thinking: true` flag, so
    # the param is never sent and the model's own native default is left untouched.
    captured = _patch_agent_query_capturing(monkeypatch, with_tools_field=True,
                                            with_thinking_field=True)
    asyncio.run(mc._agent_query("p", "claude-sonnet-5", None))
    assert "thinking" not in captured


def test_agent_query_omits_thinking_on_an_older_sdk(monkeypatch):
    # `thinking` postdates the SDK floor just like `tools` — an older install keeps old behaviour
    # (no crash) rather than sending a kwarg ClaudeAgentOptions doesn't accept.
    captured = _patch_agent_query_capturing(monkeypatch, with_tools_field=True,
                                            with_thinking_field=False)
    asyncio.run(mc._agent_query("p", "claude-sonnet-4-6", None))
    assert "thinking" not in captured


def test_agent_supports_thinking_detects_the_real_sdk_dataclass(monkeypatch):
    import sys
    import types
    import dataclasses

    for has in (True, False):
        names = [("allowed_tools", list, dataclasses.field(default_factory=list))]
        if has:
            names.append(("thinking", object, dataclasses.field(default=None)))
        mod = types.ModuleType("claude_agent_sdk")
        mod.ClaudeAgentOptions = dataclasses.make_dataclass("ClaudeAgentOptions", names)
        monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)
        mc._agent_supports_thinking.cache_clear()
        assert mc._agent_supports_thinking() is has
    mc._agent_supports_thinking.cache_clear()
