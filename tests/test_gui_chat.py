"""Chat sessions (`watchdog.gui.chat`) with the Agent SDK client faked: no network, no Claude Code."""

import time

import pytest
from claude_agent_sdk import (AssistantMessage, PermissionResultAllow, PermissionResultDeny,
                              ResultMessage, StreamEvent, TextBlock, ToolPermissionContext,
                              ToolResultBlock, ToolUseBlock, UserMessage)

from watchdog.cmd import base
from watchdog.gui import chat, rpc
from watchdog.gui.api import chat as api


def _result(session_id="sdk-1", cost=0.02, is_error=False, text=None):
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=is_error,
                         num_turns=1, session_id=session_id, total_cost_usd=cost, result=text)


def _assistant(*blocks):
    return AssistantMessage(content=list(blocks), model="m")


def _delta(text):
    return StreamEvent(uuid="u", session_id="s", event={
        "type": "content_block_delta", "delta": {"type": "text_delta", "text": text}})


class FakeClient:
    """Scripted turns: each `query` pops the next list of messages (or callables run mid-turn)."""
    instances: list = []
    scripts: list = []

    def __init__(self, options):
        self.options = options
        self.prompts: list[str] = []
        self.connected = self.disconnected = self.interrupted = False
        self.permission_results: list = []
        FakeClient.instances.append(self)

    async def connect(self):
        self.connected = True

    async def query(self, prompt):
        self.prompts.append(prompt)

    async def receive_response(self):
        script = FakeClient.scripts.pop(0) if FakeClient.scripts else [_result()]
        for item in script:
            if callable(item):
                self.permission_results.append(await item(self))
            else:
                yield item

    async def interrupt(self):
        self.interrupted = True

    async def disconnect(self):
        self.disconnected = True


@pytest.fixture
def env(tmp_path, monkeypatch):
    FakeClient.instances, FakeClient.scripts = [], []
    monkeypatch.setattr(base, "WATCHDOG_HOME", tmp_path / "home" / ".watchdog")
    monkeypatch.setattr(chat, "make_client", FakeClient)
    events: list = []
    monkeypatch.setattr(rpc, "_test_sink", events)
    vault = tmp_path / "vault"
    (vault / ".watchdog" / "registry").mkdir(parents=True)
    (vault / ".claude" / "commands").mkdir(parents=True)
    (vault / ".claude" / "commands" / "watchdog-query.md").write_text("query skill")
    monkeypatch.setattr(chat, "MANAGER", chat.ChatManager())
    monkeypatch.setattr(api.chat, "MANAGER", chat.MANAGER)
    yield vault, events
    for sid in list(chat.MANAGER.sessions):
        try:
            chat.MANAGER.close(sid)
        except Exception:  # noqa: BLE001
            pass


def _wait_state(events, states=("idle", "error"), count=1, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        hits = [e for e in events if e["event"] == "chat.status" and e["data"]["state"] in states]
        if len(hits) >= count:
            return hits
        time.sleep(0.02)
    raise AssertionError(f"no {states} status; saw {[e for e in events if e['event'] == 'chat.status']}")


def test_ask_builds_the_cli_prompt_and_streams(env):
    vault, events = env
    FakeClient.scripts = [[_delta("The "), _delta("answer"),
                           _assistant(TextBlock("The answer is 42.")), _result(cost=0.05)]]
    sid = api.start(str(vault), "ask", model="sonnet", prompt="  who owns   Acme?  ")["session"]
    _wait_state(events)
    client = FakeClient.instances[0]
    assert client.prompts == ["/watchdog-query who owns Acme?"]          # ask.prompt_for, verbatim
    opts = client.options
    assert opts.cwd == str(vault) and opts.setting_sources == ["project"]
    assert opts.include_partial_messages is True and opts.model.startswith("claude-")
    assert opts.resume is None and opts.can_use_tool is not None
    deltas = [e["data"] for e in events if e["event"] == "chat.delta"]
    assert [d["text"] for d in deltas] == ["The ", "answer"] and len({d["message_id"] for d in deltas}) == 1
    msgs = [e["data"]["message"] for e in events if e["event"] == "chat.message"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["text"] == "The answer is 42." and msgs[1]["id"] == deltas[0]["message_id"]
    final = _wait_state(events)[-1]["data"]
    assert final["state"] == "idle" and final["cost_usd"] == 0.05
    assert api.get(sid)["title"] == "who owns Acme?"


def test_ask_without_skill_or_question(env):
    vault, events = env
    (vault / ".claude" / "commands" / "watchdog-query.md").unlink()
    api.start(str(vault), "ask", prompt="Is it real?")
    _wait_state(events)
    assert FakeClient.instances[0].prompts == ["Is it real?"]
    api.start(str(vault), "ask")                                          # no question: nothing sent
    assert len(FakeClient.instances) == 1


def test_research_and_context_prompts(env):
    vault, events = env
    api.start(str(vault), "research", prompt="  shell companies in Port Calder ")
    _wait_state(events)
    assert FakeClient.instances[0].prompts == ["/watchdog-research shell companies in Port Calder"]
    assert not (vault / "context.md").exists()
    api.start(str(vault), "context")
    _wait_state(events, count=2)
    assert FakeClient.instances[1].prompts == ["/watchdog-context"]
    assert (vault / "context.md").read_text(encoding="utf-8").startswith("# vault")  # template, vault name


def test_bad_mode_and_not_a_vault(env, tmp_path):
    vault, _ = env
    with pytest.raises(rpc.RpcError):
        api.start(str(vault), "chatter")
    with pytest.raises(rpc.RpcError) as e:
        api.start(str(tmp_path), "ask")
    assert e.value.code == "not_a_vault"


def test_tool_use_and_result_are_one_message(env):
    vault, events = env
    FakeClient.scripts = [[
        _assistant(TextBlock("Let me look."), ToolUseBlock("t1", "Grep", {"pattern": "Acme"})),
        UserMessage(content=[ToolResultBlock("t1", [{"type": "text", "text": "3 matches"}], False)]),
        _assistant(TextBlock("Found it.")), _result()]]
    sid = api.start(str(vault), "ask", prompt="acme")["session"]
    _wait_state(events)
    tools = [m for m in api.get(sid)["messages"] if m["role"] == "tool"]
    assert len(tools) == 1 and tools[0]["id"] == "t1"
    assert tools[0]["tool"] == {"name": "Grep", "input": {"pattern": "Acme"}, "result": "3 matches",
                                "is_error": False}
    updates = [e for e in events if e["event"] == "chat.message" and e["data"]["message"]["id"] == "t1"]
    assert len(updates) == 2                                              # announced, then result attached


def test_permission_round_trip(env):
    vault, events = env

    async def ask_tool(client):
        return await client.options.can_use_tool("Bash", {"command": "ls"}, ToolPermissionContext(title="Run ls?"))

    async def ask_again(client):
        # The same command again: "always" covers that exact command only (D274).
        return await client.options.can_use_tool("Bash", {"command": "ls"}, ToolPermissionContext())

    FakeClient.scripts = [[ask_tool, ask_again, _result()]]
    sid = api.start(str(vault), "ask", prompt="x")["session"]
    deadline = time.time() + 10
    while not any(e["event"] == "chat.permission" for e in events) and time.time() < deadline:
        time.sleep(0.02)
    req = next(e["data"] for e in events if e["event"] == "chat.permission")
    assert req["tool"] == "Bash" and req["input"] == {"command": "ls"} and req["description"] == "Run ls?"
    api.permission(sid, req["request_id"], True, always=True)
    _wait_state(events)
    results = FakeClient.instances[0].permission_results
    assert all(isinstance(r, PermissionResultAllow) for r in results)
    assert len([e for e in events if e["event"] == "chat.permission"]) == 1   # "always" skipped the second ask


def test_permission_denied(env):
    vault, events = env

    async def ask_tool(client):
        return await client.options.can_use_tool("Write", {}, ToolPermissionContext())

    FakeClient.scripts = [[ask_tool, _result()]]
    sid = api.start(str(vault), "ask", prompt="x")["session"]
    deadline = time.time() + 10
    while not any(e["event"] == "chat.permission" for e in events) and time.time() < deadline:
        time.sleep(0.02)
    rid = next(e["data"]["request_id"] for e in events if e["event"] == "chat.permission")
    api.permission(sid, rid, False)
    _wait_state(events)
    assert isinstance(FakeClient.instances[0].permission_results[0], PermissionResultDeny)
    with pytest.raises(rpc.RpcError):
        api.permission(sid, rid, True)                                    # no longer waiting


def test_send_follow_up_interrupt_and_close(env):
    vault, events = env
    (vault / ".watchdog" / "research").mkdir()
    (vault / ".watchdog" / "research" / "queue.tsv").write_text("https://x.example\tT\n")
    FakeClient.scripts = [[_assistant(TextBlock("one")), _result()], [_assistant(TextBlock("two")), _result()]]
    sid = api.start(str(vault), "ask", prompt="q")["session"]
    _wait_state(events)
    api.send(sid, "and then?")
    _wait_state(events, count=2)
    client = FakeClient.instances[0]
    assert client.prompts[1] == "and then?" and len(FakeClient.instances) == 1   # one client per session
    api.interrupt(sid)
    deadline = time.time() + 5
    while not client.interrupted and time.time() < deadline:
        time.sleep(0.02)
    assert client.interrupted
    assert api.close(sid) == {"ok": True, "research_queued": 1}
    assert client.disconnected
    assert events[-1]["data"]["state"] == "closed"
    with pytest.raises(rpc.RpcError):
        api.send(sid, "late")


def test_transcripts_list_get_resume_delete(env, tmp_path):
    vault, events = env
    FakeClient.scripts = [[_assistant(TextBlock("hello")), _result(session_id="sdk-77")]]
    sid = api.start(str(vault), "ask", prompt="persist me")["session"]
    _wait_state(events)
    api.close(sid)
    saved = list((chat.chats_dir()).glob("*.json"))
    assert [p.stem for p in saved] == [sid]
    listed = api.list_chats(str(vault))
    assert [c["session"] for c in listed] == [sid] and listed[0]["mode"] == "ask"
    assert listed[0]["title"] == "persist me"
    other = tmp_path / "other"
    (other / ".watchdog" / "registry").mkdir(parents=True)
    assert api.list_chats(str(other)) == []                               # keyed by vault
    got = api.get(sid)
    assert [m["text"] for m in got["messages"]] == ["/watchdog-query persist me", "hello"]
    assert api.resume(sid) == {"session": sid}
    FakeClient.scripts = [[_assistant(TextBlock("welcome back")), _result(session_id="sdk-77")]]
    api.send(sid, "continue")
    deadline = time.time() + 10
    while len(FakeClient.instances) < 2 and time.time() < deadline:
        time.sleep(0.02)
    _wait_state(events, count=3)
    assert FakeClient.instances[-1].options.resume == "sdk-77"
    api.delete(sid)
    assert not list(chat.chats_dir().glob("*.json"))
    with pytest.raises(rpc.RpcError):
        api.get(sid)


def test_missing_cli_and_not_signed_in_are_clear_errors(env, monkeypatch):
    vault, events = env

    class Boom(FakeClient):
        async def connect(self):
            raise chat_errors.CLINotFoundError("claude not found")

    from claude_agent_sdk import _errors as chat_errors
    monkeypatch.setattr(chat, "make_client", Boom)
    api.start(str(vault), "ask", prompt="x")
    err = _wait_state(events, states=("error",))[0]["data"]
    assert "Repair the engine" in err["detail"]

    monkeypatch.setattr(chat, "make_client", FakeClient)
    FakeClient.scripts = [[_result(is_error=True, text="Invalid API key · Please run /login")]]
    api.start(str(vault), "ask", prompt="y")
    err = _wait_state(events, states=("error",), count=2)[1]["data"]
    assert "not signed in" in err["detail"]


# ── usage (D296) ─────────────────────────────────────────────────────────────────────────────

def _usage_result(cost, tokens_in, tokens_out, session_id="sdk-9", model="claude-sonnet-5-5"):
    """A result as the bundled Claude Code sends it: cost and per-model tokens are running totals
    for the session, not this turn's."""
    return ResultMessage(subtype="success", duration_ms=2500, duration_api_ms=2000, is_error=False,
                         num_turns=2, session_id=session_id, total_cost_usd=cost,
                         usage={"input_tokens": 1, "output_tokens": 1},
                         model_usage={model: {"inputTokens": tokens_in, "outputTokens": tokens_out,
                                              "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
                                              "webSearchRequests": 0, "costUSD": cost,
                                              "contextWindow": 200000, "maxOutputTokens": 64000}})


def _init(source):
    from claude_agent_sdk import SystemMessage
    return SystemMessage(subtype="init", data={"type": "system", "subtype": "init", "apiKeySource": source})


def _session_calls(vault):
    import json
    files = sorted((vault / ".watchdog" / "registry" / "usage" / "sessions").glob("usage-*.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text())


def test_each_turn_is_recorded_once_in_the_vaults_usage_and_telemetry(env, monkeypatch):
    import sqlite3
    from watchdog import telemetry_db
    from watchdog.gui.api import usage
    vault, events = env
    monkeypatch.setattr(chat, "session_auth_env", lambda s: setattr(s, "key_label", "Work") or {})
    FakeClient.scripts = [[_init("ANTHROPIC_API_KEY"), _assistant(TextBlock("one")), _usage_result(0.10, 1000, 200)],
                          [_assistant(TextBlock("two")), _usage_result(0.25, 2500, 450)]]
    sid = api.start(str(vault), "ask", prompt="who paid?")["session"]
    _wait_state(events)
    api.send(sid, "and then?")
    final = _wait_state(events, count=2)[-1]["data"]
    assert final["cost_usd"] == pytest.approx(0.25), "the chat's cost is the running total, not a sum of totals"

    data = _session_calls(vault)
    calls = data["calls"]
    assert [c["task"] for c in calls] == ["ask", "ask"]
    assert [c["cost_usd"] for c in calls] == pytest.approx([0.10, 0.15])
    assert [c["input_tokens"] for c in calls] == [1000, 1500] and [c["output_tokens"] for c in calls] == [200, 250]
    assert {c["session"] for c in calls} == {sid}
    assert all(c["key_label"] == "Work" and c["auth_mode"] == "api-key" for c in calls)
    assert all(c["model"] == "claude-sonnet-5-5" and c["backend"] == "claude-agent-sdk" for c in calls)
    assert data["totals"]["cost_usd"] == pytest.approx(0.25)
    assert data["session"] == {"id": sid, "mode": "ask", "task": "ask", "title": "who paid?"}

    with sqlite3.connect(telemetry_db.DB_PATH) as db:
        rows = db.execute("SELECT task, run_id, cost_usd, key_label FROM calls").fetchall()
    assert [(r[0], r[1], r[3]) for r in rows] == [("ask", f"session-{sid}", "Work")] * 2
    assert sum(r[2] for r in rows) == pytest.approx(0.25)

    # The processing-run history is untouched: cost estimates never read a conversation.
    from watchdog.pipeline.orchestrate import usage_files
    assert usage_files(vault) == []
    runs = usage.runs(str(vault))["runs"]
    assert len(runs) == 1 and runs[0]["kind"] == "ask" and runs[0]["title"] == "who paid?"
    assert runs[0]["stages"] == {"ask": pytest.approx(0.25)} and runs[0]["calls"] == 2
    detail = usage.run(str(vault))
    assert detail["kind"] == "ask" and [s["stage"] for s in detail["stages"]] == ["ask"]


def test_a_subscription_session_records_tokens_with_the_cost_marked_not_billed(env, monkeypatch):
    from watchdog.gui.api import usage
    vault, events = env
    monkeypatch.setattr(chat, "session_auth_env", lambda s: setattr(s, "key_label", None) or {})
    FakeClient.scripts = [[_init("none"), _assistant(TextBlock("found it")), _usage_result(0.40, 5000, 900)]]
    api.start(str(vault), "research", prompt="shell companies")
    _wait_state(events)
    call = _session_calls(vault)["calls"][0]
    assert (call["task"], call["auth_mode"], call["input_tokens"]) == ("research", "subscription", 5000)
    assert "key_label" not in call
    run = usage.run(str(vault))
    assert run["subscription_note"] and run["stages"][0]["stage"] == "research"
    assert usage.runs(str(vault))["runs"][0]["subscription"] is True


def test_a_resumed_session_continues_from_its_saved_totals(env, monkeypatch):
    vault, events = env
    monkeypatch.setattr(chat, "session_auth_env", lambda s: {})
    FakeClient.scripts = [[_usage_result(0.10, 1000, 100)]]
    sid = api.start(str(vault), "ask", prompt="first")["session"]
    _wait_state(events)
    api.close(sid)
    api.resume(sid)
    # The resumed process restored its total: the first result carries the earlier turn too.
    FakeClient.scripts = [[_usage_result(0.16, 1600, 160)]]
    api.send(sid, "again")
    _wait_state(events, count=3)
    assert [c["cost_usd"] for c in _session_calls(vault)["calls"]] == pytest.approx([0.10, 0.06])


def test_a_total_that_starts_again_counts_whole(env, monkeypatch):
    """A `/clear`, or a resumed session with no saved total, restarts the SDK's running total."""
    from watchdog.pipeline import session_usage
    first, prev = session_usage.turn(None, _usage_result(0.30, 3000, 300))
    again, _ = session_usage.turn(prev, _usage_result(0.05, 400, 40))
    assert first["cost_usd"] == pytest.approx(0.30)
    assert again["cost_usd"] == pytest.approx(0.05)
    assert again["models"]["claude-sonnet-5-5"]["input_tokens"] == 400


# ── what a session is given (D299) ────────────────────────────────────────────────────────

def test_a_sessions_options_carry_watchdogs_tools_the_primer_and_no_shell(env):
    from watchdog import session_tools
    from watchdog.cmd import primer
    vault, events = env
    api.start(str(vault), "ask", prompt="who?")
    _wait_state(events)
    opts = FakeClient.instances[0].options
    assert set(opts.mcp_servers) == {"watchdog"} and opts.mcp_servers["watchdog"]["type"] == "sdk"
    assert opts.allowed_tools == [f"mcp__watchdog__{t}" for t in session_tools.TOOLS]
    assert "Bash" in opts.disallowed_tools and opts.sandbox is None
    assert opts.system_prompt["preset"] == "claude_code"
    assert opts.system_prompt["append"] == primer.session_text(vault)
    assert "PATH" not in opts.env                     # no `watchdog` command to find
    assert {m.matcher for m in opts.hooks["PreToolUse"]} == {"Write|Edit|MultiEdit|NotebookEdit",
                                                               "Write|Edit|MultiEdit"}
    assert [m.matcher for m in opts.hooks["PostToolUse"]] == ["Write|Edit|MultiEdit"]
    assert len(opts.hooks["UserPromptSubmit"]) == 1


def test_the_prompt_hook_notes_documents_still_waiting(env):
    import asyncio
    vault, _ = env
    session = chat.Session(vault, "ask", None, "t")
    hook = chat.build_options(session, None).hooks["UserPromptSubmit"][0].hooks[0]
    assert asyncio.run(hook({"prompt": "hi"}, None, None)) == {}
    q = vault / ".watchdog" / "queue"
    q.mkdir(parents=True)
    (q / "a.json").write_text("{}")
    (q / "b.json").write_text("{}")
    (vault / ".watchdog" / "extracted").mkdir()
    (vault / ".watchdog" / "extracted" / "b.json").write_text("{}")
    out = asyncio.run(hook({"prompt": "hi"}, None, None))["hookSpecificOutput"]
    assert out["hookEventName"] == "UserPromptSubmit"
    line = out["additionalContext"]
    assert "1 file(s) waiting for processing" in line
    assert "1 file(s) processed and waiting for post-processing" in line
    assert "terminal" not in line and "watchdog dig" not in line


def test_the_prompt_hook_never_fails_a_prompt(env, monkeypatch):
    import asyncio
    vault, _ = env
    monkeypatch.setattr(chat, "prompt_status", lambda v: 1 / 0)
    hook = chat._prompt_status(vault)
    assert asyncio.run(hook({}, None, None)) == {}


def test_a_session_outside_an_investigation_gets_no_primer(env, tmp_path):
    session = chat.Session(tmp_path, "ask", None, "t")
    assert "append" not in chat.build_options(session, None).system_prompt
