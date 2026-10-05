"""What a Claude Code session in a vault can reach without a prompt (I6)."""

import argparse
import json

import pytest

import watchdog.cmd.vault as vault_cmd
from watchdog.cmd.base import _vault_settings


@pytest.fixture
def session(tmp_path, monkeypatch):
    here = tmp_path / "here"
    (here / ".watchdog").mkdir(parents=True)
    other = tmp_path / "other"
    (other / ".watchdog").mkdir(parents=True)
    projects = {"here": {"path": str(here), "name": "Here"}, "other": {"path": str(other), "name": "Other"}}
    monkeypatch.setattr(vault_cmd, "_find_project", lambda name: (name, projects[name]))
    monkeypatch.chdir(here)
    monkeypatch.setenv("CLAUDECODE", "1")
    return here, tmp_path


def _args(**kw):
    base = dict(project=None, query=None, batch=None, everywhere=False)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.mark.parametrize("kw, why", [
    (dict(everywhere=True, query="x"), "--everywhere"),
    (dict(batch="../credentials.json"), "inside this investigation"),
    (dict(batch="~/.watchdog/credentials.json"), "inside this investigation"),
    (dict(project="other", query="x"), "another investigation"),
    (dict(project="other", batch="terms.txt"), "another investigation"),
])
def test_a_session_cannot_search_outside_its_vault(session, kw, why):
    with pytest.raises(SystemExit, match=why):
        vault_cmd._confine_to_session_vault(_args(**kw))


@pytest.mark.parametrize("kw", [
    dict(query="acme"), dict(project="acme"),           # one positional inside a vault is the query
    dict(project="here", query="acme"), dict(batch="terms.txt"), dict(batch="lists/names.txt"),
])
def test_a_session_can_search_its_own_vault(session, kw):
    vault_cmd._confine_to_session_vault(_args(**kw))


def test_a_person_at_a_terminal_is_not_confined(session, monkeypatch):
    monkeypatch.delenv("CLAUDECODE")
    vault_cmd._confine_to_session_vault(_args(everywhere=True, query="x"))
    vault_cmd._confine_to_session_vault(_args(batch="~/anything.txt"))


def test_vault_settings_deny_watchdog_config_and_keys():
    deny = _vault_settings()["permissions"]["deny"]
    assert "Read(~/.watchdog/**)" in deny and "Edit(~/.watchdog/**)" in deny


def test_refresh_adds_the_deny_rules_to_an_existing_vault(tmp_path, monkeypatch):
    from watchdog.cmd import setup
    vault = tmp_path / "v"
    (vault / ".claude").mkdir(parents=True)
    (vault / ".watchdog").mkdir()
    settings = vault / ".claude" / "settings.json"
    settings.write_text(json.dumps({"permissions": {"allow": [], "deny": ["Read(./secret)"]}}))
    monkeypatch.setattr(setup, "install_skills", lambda *a, **k: None, raising=False)
    monkeypatch.chdir(vault)
    setup.cmd_refresh_skills(argparse.Namespace(name=None, project=None))
    deny = json.loads(settings.read_text())["permissions"]["deny"]
    assert deny[0] == "Read(./secret)" and "Read(~/.watchdog/**)" in deny
