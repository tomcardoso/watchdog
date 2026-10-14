"""What a Claude Code session in a vault can reach without a prompt (I6, D257, D299). The tools'
own confinement is tested in test_session_tools.py."""

import json

import pytest

from watchdog import session_tools
from watchdog.ops import CollectingReporter
from watchdog.ops.maintenance import refresh_claude_setup
from watchdog.vault import claude_settings, session_may_write, session_settings


def test_vault_settings_deny_watchdog_config_and_keys():
    deny = claude_settings()["permissions"]["deny"]
    assert "Read(~/.watchdog/**)" in deny and "Edit(~/.watchdog/**)" in deny


def test_vault_settings_preapprove_only_the_read_tools_and_run_no_commands():
    """A new vault's settings pre-approve Watchdog's read-only tools, never one that writes and
    never a shell command, and carry no hooks: the app registers its own in-process (D299, D304)."""
    settings = claude_settings()
    allow = settings["permissions"]["allow"]
    assert [r for r in allow if r.startswith("mcp__")] == [session_tools.tool_name(t)
                                                           for t in session_tools.READ_TOOLS]
    assert not any(session_tools.tool_name(t) in allow for t in session_tools.WRITE_TOOLS)
    assert not any(r.startswith("Bash(") for r in allow)
    assert "hooks" not in settings
    assert "watchdog " not in json.dumps(settings)


def test_the_edit_rules_are_exactly_the_paths_a_session_may_write():
    allow = claude_settings()["permissions"]["allow"]
    assert [r for r in allow if r.startswith("Edit(")] == [
        "Edit(queries/**)", "Edit(wiki/**)", "Edit(briefings/**)", "Edit(context.md)",
        "Edit(.watchdog/research/**)"]


def test_the_apps_session_settings_switch_off_every_hook_and_project_server():
    """What the app passes on the command line (D304): the vault's rules, plus hooks and a vault's
    own MCP servers switched off, so a vault's settings file can't run a command."""
    settings = session_settings()
    assert settings["disableAllHooks"] is True and settings["enableAllProjectMcpServers"] is False
    assert settings["permissions"] == claude_settings()["permissions"]
    assert settings["permissions"]["blockReadsOutsideWorkingDirectories"] is True


@pytest.mark.parametrize("rel,ok", [
    ("queries/who-owns-lot-14.md", True), ("wiki/thread.md", True), ("briefings/research-2026.md", True),
    ("context.md", True), (".watchdog/research/queue.tsv", True),
    (".claude/settings.json", False), (".claude/settings.local.json", False),
    (".claude/commands/x.md", False), (".mcp.json", False), (".watchdog/settings.json", False),
    (".watchdog/registry/entities.json", False), (".watchdog/queue/a.json", False),
    (".watchdog/history/log.json", False), ("entities/person/x.md", False), ("timeline.md", False),
    ("watchlist.md", False), ("context.md.bak", False), ("queries", False), ("", False),
])
def test_what_a_session_may_write(rel, ok):
    assert session_may_write(rel) is ok


def test_refresh_writes_the_settings_whole_and_keeps_the_reporters_notes(tmp_path, monkeypatch):
    """Refreshing an investigation's Claude setup writes `.claude/settings.json` from the current
    rules (D298) and replaces only Watchdog's part of `.claude/CLAUDE.md`."""
    monkeypatch.setattr("watchdog.config.HOME", tmp_path / "home")
    vault = tmp_path / "v"
    (vault / ".claude").mkdir(parents=True)
    (vault / ".watchdog" / "registry").mkdir(parents=True)
    (vault / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Read(./mine)"]}}))
    (vault / ".claude" / "CLAUDE.md").write_text("<!-- watchdog:begin -->\nSTALE-SECTION\n<!-- watchdog:end -->\nMy notes.\n")
    refresh_claude_setup(CollectingReporter(), vault)
    assert json.loads((vault / ".claude" / "settings.json").read_text()) == claude_settings()
    text = (vault / ".claude" / "CLAUDE.md").read_text()
    assert "STALE-SECTION" not in text and text.endswith("My notes.\n") and "<!-- watchdog:end -->" in text
    assert (vault / ".claude" / "commands" / "watchdog-query.md").exists()
