"""What a Claude Code session in a vault can reach without a prompt (I6, D257)."""

import argparse
import json
import sys

import pytest

import watchdog.cli as cli
import watchdog.cmd.vault as vault_cmd
from watchdog.cmd.base import _vault_settings

SECRET = "sk-test-not-a-real-key"


@pytest.fixture
def session(tmp_path, monkeypatch):
    """A registered vault `here`, two other registered investigations, a fake key file outside
    them, and a shell marked as Claude Code's. The real project lookup is used throughout."""
    vaults = {}
    for slug in ("here", "secret-probe-acme", "secret-probe-bravo"):
        v = tmp_path / slug
        (v / ".watchdog" / "registry").mkdir(parents=True)
        vaults[slug] = v
    projects = {s: {"path": str(v), "name": s} for s, v in vaults.items()}
    monkeypatch.setattr(vault_cmd, "load_projects", lambda: projects)
    monkeypatch.setattr("watchdog.cmd.base.load_projects", lambda: projects)
    keys = tmp_path / "credentials.json"
    keys.write_text(json.dumps({"anthropic_api_key": SECRET}))
    monkeypatch.chdir(vaults["here"])
    monkeypatch.setenv("CLAUDECODE", "1")
    return vaults["here"], tmp_path, keys


def _args(**kw):
    base = dict(project=None, query=None, batch=None, everywhere=False)
    base.update(kw)
    return argparse.Namespace(**base)


def _refused(kw):
    with pytest.raises(SystemExit) as e:
        vault_cmd._confine_to_session_vault(_args(**kw))
    return str(e.value)


@pytest.mark.parametrize("kw", [
    dict(everywhere=True, query="x"),
    dict(batch="../credentials.json"),
    dict(project="secret-probe-acme", query="x"),
    dict(project="secret-probe-acme", batch="terms.txt"),
])
def test_a_session_cannot_search_outside_its_vault(session, kw):
    assert "Run it in your own terminal" in _refused(kw)


def test_refusals_do_not_reveal_which_investigations_exist(session):
    # An ambiguous prefix, an exact other name and a name that exists nowhere all read the same.
    messages = {_refused(dict(project=p, query="x"))
                for p in ("secret-probe", "secret-probe-a", "secret-probe-acme", "nope")}
    assert len(messages) == 1
    assert "secret-probe" not in messages.pop()


def test_a_symlink_out_of_the_vault_is_refused(session):
    here, _, keys = session
    (here / "names.txt").symlink_to(keys)
    assert "--batch file inside it" in _refused(dict(batch="names.txt"))


def test_an_unregistered_folder_is_refused(session, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert "own folder" in _refused(dict(project="acme"))


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


@pytest.mark.parametrize("argv", [
    ["search", "--batch", "{keys}", "--json"],
    ["search", "--batch={keys}"],
    ["search", "--bat", "{keys}"],
])
def test_the_key_file_never_reaches_the_output(session, monkeypatch, capsys, argv):
    _, _, keys = session
    monkeypatch.setattr(cli, "CONFIG_FILE", type("P", (), {"exists": lambda self: True})())
    monkeypatch.setattr(sys, "argv", ["watchdog", *[a.format(keys=keys) for a in argv]])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert "Run it in your own terminal" in str(e.value)       # stopped by the guard itself
    out = capsys.readouterr()
    assert SECRET not in out.out and SECRET not in out.err


def test_write_entity_cannot_target_another_vault(session, monkeypatch):
    from watchdog.pipeline import write_entity
    here, root, _ = session
    other = root / "secret-probe-acme"
    (other / "_INCOMING").mkdir()
    (other / "_INCOMING" / "doc.json").write_text(json.dumps({"entity_id": "bob"}))
    written = []
    monkeypatch.setattr(write_entity, "run", lambda *a: written.append(a))
    monkeypatch.setattr(sys, "argv", ["write-entity", "--entity-id", "bob", "--vault", str(other),
                                      "--extraction", str(other / "_INCOMING" / "doc.json")])
    with pytest.raises(SystemExit, match="only writes to this investigation"):
        write_entity.main()
    assert not written


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


@pytest.mark.parametrize("value", [{"permissions": None}, ["not", "a", "dict"]])
def test_refresh_leaves_an_unrecognised_settings_file_alone(tmp_path, monkeypatch, capsys, value):
    assert _refresh(tmp_path, monkeypatch, value) == value
    assert "unchanged" in capsys.readouterr().out
