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
    ("deepseek", "deepseek-v4-pro", "high", None),     # DeepSeek: no portable knob
    ("deepseek", "deepseek-v4-flash", "low", None),
    ("gemini", "gemini-2.5-flash", "low", "low"),     # Gemini: every model passes through
    ("gemini", "gemini-2.5-pro", "high", "high"),
])
def test_resolve_effort(provider, model_id, effort, expected):
    assert mc._resolve_effort(provider, model_id, effort) == expected


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


def test_openai_cost_prices_gemini_models():
    # gemini-2.5-flash: $0.30/1M input, $2.50/1M output.
    assert mc._openai_cost("gemini-2.5-flash",
                           {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}) \
        == pytest.approx(0.30 + 2.50)
    # gemini-3.5-flash: $1.50/1M input, $9.00/1M output.
    assert mc._openai_cost("gemini-3.5-flash",
                           {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}) \
        == pytest.approx(1.50 + 9.00)


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


def test_split_deepseek_thinking():
    assert mc._split_deepseek_thinking("deepseek-v4-flash") == ("deepseek-v4-flash", False)
    assert mc._split_deepseek_thinking("deepseek-v4-flash-thinking") == ("deepseek-v4-flash", True)
    assert mc._split_deepseek_thinking("deepseek-v4-pro-thinking") == ("deepseek-v4-pro", True)


def _fake_httpx(monkeypatch, captured):
    """Patch httpx.AsyncClient to capture the request body and return a canned OK response."""
    import httpx

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


def test_gemini_backend_request_shape(monkeypatch):
    captured = {}
    _fake_httpx(monkeypatch, captured)
    out = asyncio.run(mc._openai_complete_async("prompt", "gemini-2.5-flash", SCHEMA, "AIza-x", 8000,
                                                "low",
                                                base_url="https://generativelanguage.googleapis.com/v1beta/openai"))
    assert captured["url"] == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer AIza-x"
    assert captured["body"]["model"] == "gemini-2.5-flash"        # no marker-stripping (DeepSeek-only)
    assert captured["body"]["reasoning_effort"] == "low"
    assert "thinking" not in captured["body"]                      # DeepSeek-only toggle
    assert out["cost_usd"] == pytest.approx(10 * 0.30e-6 + 5 * 2.50e-6)


# ── response_format: real json_schema on Gemini, json_object elsewhere (D98) ───────────────────

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


def test_openai_stays_on_json_object_mode(monkeypatch):
    captured = {}
    _fake_httpx(monkeypatch, captured)
    asyncio.run(mc._openai_complete_async("prompt", "gpt-4o", SCHEMA, "sk-o", 8000,
                                          base_url="https://api.openai.com/v1"))
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert "Return JSON matching this schema" in captured["body"]["messages"][1]["content"]


def test_deepseek_stays_on_json_object_mode(monkeypatch):
    captured = {}
    _fake_httpx(monkeypatch, captured)
    asyncio.run(mc._openai_complete_async("prompt", "deepseek-v4-flash", SCHEMA, "sk-ds", 8000,
                                          base_url="https://api.deepseek.com"))
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert "Return JSON matching this schema" in captured["body"]["messages"][1]["content"]


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
