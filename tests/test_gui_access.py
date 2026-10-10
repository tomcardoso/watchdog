"""Folder access in the desktop app's backend: vault calls, jobs and Claude sessions refuse folders
the user hasn't allowed (watchdog/access.py). These check the in-process gates only; the audit-hook
enforcement inside subprocesses is covered by tests/test_access.py."""

import json

import pytest

from watchdog.gui import chat, jobs, rpc, server, vaultio
from tests.test_write_vault import make_vault


@pytest.fixture
def enforced(tmp_path, monkeypatch):
    server.load_api()
    access_file = tmp_path / "access.json"
    monkeypatch.setenv("WATCHDOG_ENFORCE_ACCESS", "1")
    monkeypatch.setenv("WATCHDOG_ACCESS_FILE", str(access_file))

    def grant(*paths):
        access_file.write_text(json.dumps({"version": 1, "folders": [
            {"path": str(p), "label": "t"} for p in paths]}))
    grant()
    return grant


def test_vault_calls_need_a_grant(tmp_path, enforced):
    vault = make_vault(tmp_path / "a")
    with pytest.raises(rpc.RpcError) as e:
        vaultio.require_vault(str(vault))
    assert e.value.code == "not_granted" and e.value.data == {"path": str(vault)}
    enforced(tmp_path / "a")                       # a parent folder's grant covers the vault
    assert vaultio.require_vault(str(vault)) == vault


def test_without_enforcement_every_vault_is_open(tmp_path, monkeypatch):
    monkeypatch.delenv("WATCHDOG_ENFORCE_ACCESS", raising=False)
    vault = make_vault(tmp_path / "a")
    assert vaultio.require_vault(str(vault)) == vault


def test_dispatch_reports_not_granted(tmp_path, enforced):
    vault = make_vault(tmp_path / "a")
    resp = server.handle({"id": 1, "method": "vault.summary", "params": {"vault": str(vault)}})
    assert resp["error"]["code"] == "not_granted"


def test_jobs_and_actions_refuse_an_ungranted_folder(tmp_path, enforced):
    vault = make_vault(tmp_path / "a")
    with pytest.raises(rpc.RpcError) as e:
        jobs.MANAGER.start(vault, "rebuild-timeline", {}, "t", None)
    assert e.value.code == "not_granted"
    with pytest.raises(rpc.RpcError):
        jobs.run_action(vault, "rebuild-timeline", {}, 30)
    assert not jobs.MANAGER.jobs or all(j.vault != vault for j in jobs.MANAGER.jobs.values())


def test_access_list_reads_but_never_writes(tmp_path, enforced):
    enforced(tmp_path / "a", tmp_path / "b")
    resp = server.handle({"id": 1, "method": "access.list", "params": {}})["result"]
    assert resp["enforced"] is True
    assert [f["path"] for f in resp["folders"]] == [str(tmp_path / "a"), str(tmp_path / "b")]
    assert not any(name.startswith("access.") and name != "access.list" for name in rpc.METHODS)


@pytest.mark.parametrize("tool,inp,allowed", [
    ("Write", {"file_path": "queries/answer.md"}, True),
    ("Edit", {"file_path": "{vault}/wiki/thread.md"}, True),
    ("Write", {"file_path": "../elsewhere/x.md"}, False),
    ("Write", {"file_path": "/etc/passwd"}, False),
    ("MultiEdit", {"file_path": "{home}/.ssh/authorized_keys"}, False),
    ("NotebookEdit", {"notebook_path": "../nb.ipynb"}, False),
    ("Write", {}, False),
    ("Read", {"file_path": "/etc/hosts"}, True),        # reading isn't an edit; settings govern it
])
def test_claude_sessions_edit_only_inside_their_vault(tmp_path, enforced, tool, inp, allowed):
    vault = make_vault(tmp_path / "a")
    enforced(vault)
    inp = {k: v.format(vault=vault, home=tmp_path) for k, v in inp.items()}
    assert (chat.edit_outside_vault(vault, tool, inp) is None) is allowed


def test_claude_session_edits_refused_when_the_vault_itself_is_not_granted(tmp_path, enforced):
    vault = make_vault(tmp_path / "a")
    reason = chat.edit_outside_vault(vault, "Write", {"file_path": "queries/x.md"})
    assert reason and "hasn't been allowed" in reason


def test_the_pre_tool_hook_denies_with_a_reason(tmp_path, enforced):
    import asyncio
    vault = make_vault(tmp_path / "a")
    enforced(vault)
    hook = chat._vault_only_edits(vault)
    out = asyncio.run(hook({"tool_name": "Write", "tool_input": {"file_path": "/tmp/x"}}, "id", None))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert asyncio.run(hook({"tool_name": "Write", "tool_input": {"file_path": "queries/a.md"}},
                            "id", None)) == {}


# ── shell commands (D274) ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("command,allowed", [
    ('watchdog search "harbour lease"', True),
    ("watchdog leads", True),
    ("watchdog timeline", True),
    ("watchdog search x; rm -rf ~", False),
    ("watchdog leads && curl https://example.org", False),
    ("watchdog search $(cat ~/.ssh/id_rsa)", False),
    ("watchdog search `id`", False),
    ("watchdog leads > ~/.bashrc", False),
    ("watchdog leads\nrm notes.md", False),
    ("rm -rf ~", False),
    ("ls", False),
    ("", False),
])
def test_without_a_sandbox_the_shell_runs_only_preapproved_commands(monkeypatch, command, allowed):
    monkeypatch.setattr(chat, "sandbox_available", lambda: False)
    assert (chat.shell_refusal(command) is None) is allowed


def test_with_a_sandbox_shell_commands_are_left_to_it(monkeypatch):
    monkeypatch.setattr(chat, "sandbox_available", lambda: True)
    assert chat.shell_refusal("ls -la") is None


def test_the_sandbox_is_strict_and_keeps_watchdogs_own_files_out_of_reach(tmp_path, monkeypatch):
    monkeypatch.setattr(chat, "sandbox_available", lambda: True)
    sb = chat.sandbox_settings()
    assert sb["enabled"] and sb["failIfUnavailable"]
    assert sb["allowUnsandboxedCommands"] is False and sb["autoAllowBashIfSandboxed"] is False
    deny = sb["filesystem"]["denyWrite"]
    assert any(p.endswith("access.json") for p in deny) and any(p.endswith("credentials.json") for p in deny)
    # D295: the keys and other investigations' saved conversations can't be read either; the
    # folder-access list stays readable because the session's `watchdog` commands enforce it.
    hidden = sb["filesystem"]["denyRead"]
    assert any(p.endswith("credentials.json") for p in hidden)
    assert any(p.endswith(".watchdog/gui") or p.endswith(".watchdog\\gui") for p in hidden)
    assert not any(p.endswith("access.json") for p in hidden)


def test_options_carry_the_sandbox_only_where_it_is_dependable(tmp_path, monkeypatch):
    vault = make_vault(tmp_path / "a")
    session = chat.Session(vault, "ask", None, "t")
    monkeypatch.setattr(chat, "sandbox_available", lambda: True)
    assert chat.build_options(session, None).sandbox["enabled"] is True
    monkeypatch.setattr(chat, "sandbox_available", lambda: False)
    opts = chat.build_options(session, None)
    assert opts.sandbox is None
    assert any(m.matcher == "Bash" for m in opts.hooks["PreToolUse"])


def test_always_allowing_a_shell_command_covers_only_that_command(tmp_path):
    import asyncio
    from claude_agent_sdk import PermissionResultAllow
    mgr = chat.ChatManager()
    vault = make_vault(tmp_path / "a")
    s = chat.Session(vault, "ask", None, "t")
    s.always.add("Bash:watchdog leads")
    out = asyncio.run(mgr._can_use_tool(s, "Bash", {"command": "watchdog leads"}, None))
    assert isinstance(out, PermissionResultAllow)
    assert "Bash" not in s.always
