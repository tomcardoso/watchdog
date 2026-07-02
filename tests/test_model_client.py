"""Tests for ModelClient — backend routing, JSON extraction, schema validation,
tier escalation on retry, telemetry. The two SDK backends are mocked."""

import asyncio

import pytest

from watchdog import model_client as mc


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

    async def __call__(self, prompt, model_id, schema, api_key, max_tokens, effort=None):
        self.calls.append({"model_id": model_id, "api_key": api_key,
                           "prompt": prompt, "max_tokens": max_tokens, "effort": effort})
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


def test_extract_task_uses_larger_token_budget(api_key_auth, monkeypatch):
    api = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    mc.complete_json(task="extract", prompt="p", schema=SCHEMA)
    assert api.calls[0]["max_tokens"] == 16000           # per-task override
    assert mc._TASK_MAX_TOKENS.get("other-task") is None  # default applies elsewhere


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


def test_effort_dropped_for_haiku(api_key_auth, monkeypatch):
    # Haiku rejects output_config.effort (400) — it must never be sent there.
    api = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    mc.complete_json(task="classify", prompt="p", schema=SCHEMA, model="haiku", effort="low")
    assert api.calls[0]["effort"] is None


def test_effort_omitted_when_unset(api_key_auth, monkeypatch):
    api = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    mc.complete_json(task="extract", prompt="p", schema=SCHEMA, model="sonnet")
    assert api.calls[0]["effort"] is None


@pytest.mark.parametrize("provider,model_id,effort,expected", [
    ("anthropic", "claude-sonnet-4-6", "low", "low"),
    ("anthropic", "claude-opus-4-8", "medium", "medium"),
    ("anthropic", "claude-sonnet-4-6", "high", None),    # Claude: high ≡ default
    ("anthropic", "claude-haiku-4-5", "low", None),      # Claude: Haiku rejects effort
    ("anthropic", "claude-sonnet-4-6", None, None),      # unset
    ("openai", "gpt-5-mini", "low", "low"),              # OpenAI reasoning model → pass through
    ("openai", "gpt-5", "high", "high"),                 # OpenAI: high is NOT a no-op default
    ("openai", "gpt-4o", "low", None),                   # OpenAI chat model → dropped
    ("deepseek", "deepseek-reasoner", "high", None),     # DeepSeek: no portable knob
    ("deepseek", "deepseek-chat", "low", None),
])
def test_resolve_effort(provider, model_id, effort, expected):
    assert mc._resolve_effort(provider, model_id, effort) == expected


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
    with pytest.raises(mc.ModelError, match="watchdog auth set openai"):
        mc.complete_json(task="t", prompt="p", schema=SCHEMA, backend="openai")


def test_openai_effort_passed_for_reasoning_model(openai_key, monkeypatch):
    be = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "openai", be)
    mc.complete_json(task="t", prompt="p", schema=SCHEMA, backend="openai", model="gpt-5-mini", effort="low")
    assert be.calls[0]["effort"] == "low"


def test_openai_effort_dropped_for_chat_model(openai_key, monkeypatch):
    be = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "openai", be)
    # high is not a no-op default on OpenAI, but a chat model can't take reasoning_effort → dropped
    mc.complete_json(task="t", prompt="p", schema=SCHEMA, backend="openai", model="gpt-4o", effort="high")
    assert be.calls[0]["effort"] is None


def test_deepseek_drops_effort(monkeypatch):
    monkeypatch.setattr(mc.auth, "get_api_key",
                        lambda provider="anthropic": "sk-ds" if provider == "deepseek" else None)
    be = FakeBackend(_out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "deepseek", be)
    mc.complete_json(task="t", prompt="p", schema=SCHEMA, backend="deepseek",
                     model="deepseek-reasoner", effort="high")
    assert be.calls[0]["effort"] is None               # no portable knob on DeepSeek


def test_openai_cost():
    assert mc._openai_cost("deepseek-chat",
                           {"prompt_tokens": 1_000_000, "completion_tokens": 0}) == pytest.approx(0.27)
    assert mc._openai_cost("unknown-model", {"prompt_tokens": 100}) is None
    assert mc._openai_cost("deepseek-chat", None) is None


def test_openai_backend_request_shape(monkeypatch):
    import httpx
    captured = {}

    class FakeResp:
        status_code = 200
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
    out = asyncio.run(mc._openai_complete_async("prompt", "deepseek-reasoner", SCHEMA,
                                                "sk-ds", 8000, "high",
                                                base_url="https://api.deepseek.com"))
    assert out["text"] == '{"name": "Acme"}'
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-ds"
    assert captured["body"]["reasoning_effort"] == "high"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert "JSON" in captured["body"]["messages"][1]["content"]   # required for json_object mode
    assert out["cost_usd"] == pytest.approx(10 * 0.55e-6 + 5 * 2.19e-6)


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
])
def test_resolve_model_id(tier, expected):
    assert mc.resolve_model_id(tier) == expected


def test_claude_batch_rejected_as_a_single_call_backend(api_key_auth):
    """claude-batch is submit/poll/collect only (#214) — routing a normal single-call task to
    it must fail clearly, not silently misbehave or read as 'unknown backend'."""
    with pytest.raises(mc.ModelError, match="batch-mode-only"):
        mc.complete_json(task="classify", prompt="p", schema=SCHEMA, backend="claude-batch")


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
