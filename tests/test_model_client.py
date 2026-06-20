"""Tests for ModelClient — backend routing, JSON extraction, schema validation,
tier escalation on retry, telemetry. The two SDK backends are mocked."""

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

    async def __call__(self, prompt, model_id, schema, api_key, max_tokens):
        self.calls.append({"model_id": model_id, "api_key": api_key,
                           "prompt": prompt, "max_tokens": max_tokens})
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

def test_invalid_then_valid_retries_and_escalates(api_key_auth, monkeypatch):
    # haiker tier requested; first output bad JSON, second valid
    api = FakeBackend(_out("not json"), _out('{"name": "Acme"}'))
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", api)
    r = mc.complete_json(task="t", prompt="p", schema=SCHEMA, model="haiku")
    assert r.parsed == {"name": "Acme"}
    assert r.attempts == 2
    assert api.calls[0]["model_id"] == mc._MODEL_IDS["haiku"]    # first attempt
    assert api.calls[1]["model_id"] == mc._MODEL_IDS["sonnet"]   # escalated


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
