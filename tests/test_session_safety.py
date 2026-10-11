"""What a Claude Code session in a vault can reach without a prompt (I6, D257, D299). The tools'
own confinement is tested in test_session_tools.py."""

import json

from watchdog import session_tools
from watchdog.ops import CollectingReporter
from watchdog.ops.maintenance import refresh_claude_setup
from watchdog.vault import claude_settings


def test_vault_settings_deny_watchdog_config_and_keys():
    deny = claude_settings()["permissions"]["deny"]
    assert "Read(~/.watchdog/**)" in deny and "Edit(~/.watchdog/**)" in deny


def test_vault_settings_preapprove_watchdogs_tools_and_run_no_commands():
    """A new vault's settings name Watchdog's tools, never a shell command, and carry no hooks:
    the app registers its own in-process (D299)."""
    settings = claude_settings()
    allow = settings["permissions"]["allow"]
    assert [r for r in allow if r.startswith("mcp__")] == [session_tools.tool_name(t)
                                                           for t in session_tools.TOOLS]
    assert not any(r.startswith("Bash(") for r in allow)
    assert "hooks" not in settings
    assert "watchdog " not in json.dumps(settings)


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
