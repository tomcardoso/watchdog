"""What a Claude Code session in a vault can reach without a prompt (I6, D257, D299). The tools'
own confinement is tested in test_session_tools.py."""

import argparse
import json

import pytest

from watchdog.cmd.base import _vault_settings

def test_vault_settings_deny_watchdog_config_and_keys():
    deny = _vault_settings()["permissions"]["deny"]
    assert "Read(~/.watchdog/**)" in deny and "Edit(~/.watchdog/**)" in deny


def _refresh(tmp_path, monkeypatch, settings_value):
    from watchdog.cmd import setup
    vault = tmp_path / "v"
    (vault / ".claude").mkdir(parents=True)
    (vault / ".watchdog").mkdir()
    settings = vault / ".claude" / "settings.json"
    settings.write_text(json.dumps(settings_value))
    monkeypatch.setattr("watchdog.setup_cmd.install_skills", lambda *a, **k: None)
    monkeypatch.chdir(vault)
    setup.cmd_refresh_skills(argparse.Namespace(name=None, project=None))
    return json.loads(settings.read_text())


@pytest.mark.parametrize("deny, expected_first", [
    (["Read(./secret)"], "Read(./secret)"),
    ("Read(./secret)", "Read(./secret)"),
    (None, "Read(~/.watchdog/**)"),
])
def test_refresh_adds_the_deny_rules_and_keeps_the_users(tmp_path, monkeypatch, deny, expected_first):
    out = _refresh(tmp_path, monkeypatch, {"permissions": {"allow": [], "deny": deny}})
    assert out["permissions"]["deny"][0] == expected_first
    assert "Read(~/.watchdog/**)" in out["permissions"]["deny"]


@pytest.mark.parametrize("value", [{"permissions": None}, ["not", "a", "dict"],
                                   {"permissions": {"allow": [], "deny": {"a": 1}}}])
def test_refresh_leaves_an_unrecognised_settings_file_alone(tmp_path, monkeypatch, capsys, value):
    assert _refresh(tmp_path, monkeypatch, value) == value
    out = capsys.readouterr().out
    assert "unchanged" in out
    for claim in ("Permissions updated", "Read access confined", "keys protected"):
        assert claim not in out


def test_vault_settings_preapprove_watchdogs_tools_and_run_no_commands():
    """A new vault's settings name Watchdog's tools, never a shell command, and carry no hooks:
    the app registers its own in-process (D299)."""
    from watchdog import session_tools
    settings = _vault_settings()
    allow = settings["permissions"]["allow"]
    assert [r for r in allow if r.startswith("mcp__")] == [session_tools.tool_name(t)
                                                           for t in session_tools.TOOLS]
    assert not any(r.startswith("Bash(") for r in allow)
    assert "hooks" not in settings
    assert "watchdog " not in json.dumps(settings)


def test_refresh_adds_the_tool_permissions_and_keeps_the_users(tmp_path, monkeypatch):
    from watchdog import session_tools
    out = _refresh(tmp_path, monkeypatch, {"permissions": {"allow": ["Read(./mine)"], "deny": []}})
    allow = out["permissions"]["allow"]
    assert allow[0] == "Read(./mine)"
    assert all(session_tools.tool_name(t) in allow for t in session_tools.TOOLS)
