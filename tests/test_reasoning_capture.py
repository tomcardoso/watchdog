"""Saved model reasoning (D368): off by default and invisible on the wire; on (the setting, or
any benchmark run), each call's reasoning, stop reason and token counts are saved under
`.watchdog/reasoning/<run>/`, the usage record names the file, and one run can be exported to a
zip or every run cleared. Every model is stubbed; nothing is sent anywhere."""

import asyncio
import json
import zipfile

import pytest

from watchdog import model_client as mc
from watchdog.pipeline import orchestrate, reasoning, schemas

from tests.test_model_client import SCHEMA, FakeBackend, _fake_anthropic_client, _out
from tests.test_write_vault import make_vault


# ── the Anthropic request ─────────────────────────────────────────────────────────────────

class _Usage:
    def model_dump(self):
        return {"input_tokens": 10, "output_tokens": 50,
                "output_tokens_details": {"thinking_tokens": 40}}


class _Thinking:
    type = "thinking"
    thinking = "The document is a report; I should list its figures."


class _Text:
    type = "text"
    text = '{"name": "Acme"}'


class _Message:
    content = [_Thinking(), _Text()]
    usage = _Usage()
    stop_reason = "end_turn"


def _api_call(monkeypatch, capture):
    calls = []
    _fake_anthropic_client(monkeypatch, message=_Message(), headers={}, calls=calls)

    async def go():
        token = mc._CAPTURE_REASONING.set(capture)
        try:
            return await mc._api_complete_async("p", "claude-haiku-5-5", SCHEMA, "sk-x", 8000, "high")
        finally:
            mc._CAPTURE_REASONING.reset(token)
    return asyncio.run(go()), calls[0]


def test_off_sends_the_same_request_as_before(monkeypatch):
    out, sent = _api_call(monkeypatch, capture=False)
    # Haiku 5.5 thinks by default: nothing about thinking is sent, and no reasoning is returned.
    assert "thinking" not in sent
    assert "reasoning" not in out
    assert out["finish_reason"] == "end_turn"


def test_on_asks_for_a_summarized_display_and_returns_it(monkeypatch):
    out, sent = _api_call(monkeypatch, capture=True)
    assert sent["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert out["reasoning"] == _Thinking.thinking
    # Everything else sent is unchanged.
    _, off = _api_call(monkeypatch, capture=False)
    assert {k: v for k, v in sent.items() if k != "thinking"} == off


def test_acomplete_json_carries_stop_reason_and_reasoning(monkeypatch):
    monkeypatch.setattr(mc.auth, "get_api_key",
                        lambda provider="anthropic": "sk-x" if provider == "deepseek" else None)
    out = {**_out('{"name": "Acme"}'), "finish_reason": "stop", "reasoning": "Thought it through."}
    be = FakeBackend(dict(out), dict(out))
    monkeypatch.setitem(mc._ABACKENDS, "deepseek", be)
    r = asyncio.run(mc.acomplete_json(task="t", prompt="p", schema=SCHEMA, backend="deepseek",
                                      model="deepseek-flash", capture_reasoning=True))
    assert r.stop_reason == "stop" and r.reasoning == "Thought it through."
    # The flag is scoped to the call.
    assert mc._CAPTURE_REASONING.get() is False


def test_deepseek_reasoning_content_is_captured_only_when_asked():
    class Resp:
        status_code = 200
        headers = {}
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": '{"name": "A"}',
                                             "reasoning_content": "Because the page says so."},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2}}
    import httpx

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            return Resp()

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(httpx, "AsyncClient", Client)

        async def go(capture):
            token = mc._CAPTURE_REASONING.set(capture)
            try:
                return await mc._openai_complete_async("p", "deepseek-flash-thinking", SCHEMA, "k", 8000,
                                                       base_url="https://api.deepseek.com")
            finally:
                mc._CAPTURE_REASONING.reset(token)
        assert asyncio.run(go(True))["reasoning"] == "Because the page says so."
        assert "reasoning" not in asyncio.run(go(False))
    finally:
        mp.undo()


# ── through a run ─────────────────────────────────────────────────────────────────────────

def _run_one_call(tmp_path, monkeypatch, *, save, benchmark=None):
    vault = make_vault(tmp_path)
    seen = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None,
                   **extra):
        seen.append(extra)
        return mc.ModelResult(parsed={"name": "Acme"}, text="", model="claude-haiku-5-5",
                              backend="claude-api", auth_mode="api-key", cost_usd=0.001,
                              usage={"input_tokens": 100, "output_tokens": 20},
                              stop_reason="end_turn",
                              reasoning="Reading the order." if extra.get("capture_reasoning") else None)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)
    monkeypatch.setattr(reasoning, "enabled", lambda: save)
    orchestrate._begin_usage_run(vault, benchmark_arm_id=benchmark)
    try:
        asyncio.run(orchestrate._call_model(task="extract", prompt="p", schema=schemas.EXTRACTION,
                                            filename="order.pdf", vault=vault, effort="high"))
        records = list(orchestrate._run.usage)
    finally:
        orchestrate._end_usage_run(vault)
    return vault, seen, records


def test_off_by_default_saves_nothing_and_passes_no_new_argument(tmp_path, monkeypatch):
    vault, seen, records = _run_one_call(tmp_path, monkeypatch, save=False)
    assert seen == [{}]
    assert not (vault / ".watchdog" / "reasoning").exists()
    assert records[0]["stop_reason"] == "end_turn"          # recorded either way
    assert "reasoning_file" not in records[0]


def test_on_saves_each_call_and_links_it_from_usage(tmp_path, monkeypatch):
    vault, seen, records = _run_one_call(tmp_path, monkeypatch, save=True)
    assert seen == [{"capture_reasoning": True}]
    rel = records[0]["reasoning_file"]
    saved = json.loads((vault / rel).read_text())
    assert saved["task"] == "extract" and saved["filename"] == "order.pdf"
    assert saved["model"] == "claude-haiku-5-5" and saved["effort"] == "high"
    assert saved["stop_reason"] == "end_turn"
    assert saved["input_tokens"] == 100 and saved["output_tokens"] == 20
    assert saved["reasoning"] == "Reading the order."


def test_a_benchmark_run_always_saves(tmp_path, monkeypatch):
    vault, seen, records = _run_one_call(tmp_path, monkeypatch, save=False, benchmark="arm-1")
    assert seen == [{"capture_reasoning": True}]
    assert records[0]["reasoning_file"].startswith(".watchdog/reasoning/")


def test_stop_reason_and_reasoning_kept_for_a_failed_call(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)

    async def fake(**k):
        raise mc.ModelError("bad", usage={"input_tokens": 5, "output_tokens": 9}, cost_usd=0.0,
                            attempts=2, model="m", backend="claude-api", auth_mode="api-key",
                            stop_reason="max_tokens", reasoning="Ran long.")
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)
    monkeypatch.setattr(reasoning, "enabled", lambda: True)
    orchestrate._begin_usage_run(vault)
    try:
        with pytest.raises(mc.ModelError):
            asyncio.run(orchestrate._call_model(task="extract", prompt="p", schema=schemas.EXTRACTION,
                                                filename="a.pdf", vault=vault))
        rec = orchestrate._run.usage[0]
    finally:
        orchestrate._end_usage_run(vault)
    assert rec["stop_reason"] == "max_tokens" and rec["failed"] is True
    saved = json.loads((vault / rec["reasoning_file"]).read_text())
    assert saved["failed"] is True and saved["reasoning"] == "Ran long."


# ── sharing and clearing ──────────────────────────────────────────────────────────────────

def test_export_bundles_one_run_and_clear_removes_all(tmp_path, monkeypatch):
    vault, _, records = _run_one_call(tmp_path, monkeypatch, save=True)
    runs = reasoning.runs(vault)
    assert len(runs) == 1 and runs[0]["calls"] == 1 and runs[0]["documents"] == ["order.pdf"]

    out = tmp_path / "out"
    out.mkdir()
    from watchdog.ops import CollectingReporter
    from watchdog.ops.troubleshoot import reasoning_clear, reasoning_export
    res = reasoning_export(CollectingReporter(), vault, output=str(out), run=runs[0]["run_id"])
    with zipfile.ZipFile(res["output"]) as z:
        names = z.namelist()
    assert any(n.startswith("reasoning/") for n in names)
    assert any(n.startswith("usage/usage-") for n in names)
    assert "processing.log" in names and "README.txt" in names

    with pytest.raises(SystemExit):
        reasoning_export(CollectingReporter(), vault, output=str(out), run="../../etc")

    cleared = reasoning_clear(CollectingReporter(), vault)
    assert cleared["runs"] == 1
    assert reasoning.runs(vault) == []


def test_setting_is_declared_off_and_listed():
    from watchdog import settings
    assert settings.SETTINGS["save_reasoning"]["default"] is False
    help_text = settings.SETTINGS["save_reasoning"]["help"]
    assert "restate what your documents say" in help_text and "disk space" in help_text
    assert any("save_reasoning" in keys for _t, _b, keys in settings.SECTIONS)
