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
        return await client.options.can_use_tool("Bash", {"command": "pwd"}, ToolPermissionContext())

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
