"""Tests for `watchdog auth` — modes, API key storage, env precedence, masking, perms."""

import os
import stat
import types

import pytest

from watchdog.cmd import auth, base


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Redirect WATCHDOG_HOME, clear the real env key, assume Claude Code logged out."""
    monkeypatch.setattr(base, "WATCHDOG_HOME", tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(auth, "claude_code_logged_in", lambda: False)
    return tmp_path


def _ns(action=None, target=None):
    return types.SimpleNamespace(action=action, target=target)


# ── key storage ───────────────────────────────────────────────────────────────

def test_get_api_key_unset(home):
    assert auth.get_api_key("anthropic") is None


def test_set_then_resolve_stored(home, monkeypatch):
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "sk-ant-test-1234567890")
    auth.cmd_auth(_ns("set"))
    assert auth.get_api_key("anthropic") == "sk-ant-test-1234567890"


def test_credentials_file_is_0600(home, monkeypatch):
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "sk-ant-test-1234567890")
    auth.cmd_auth(_ns("set"))
    assert stat.S_IMODE(os.stat(auth._credentials_path()).st_mode) == 0o600


def test_env_var_takes_precedence(home, monkeypatch):
    auth._save_state({"mode": "auto", "keys": {"anthropic": "sk-ant-stored"}})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fromenv")
    assert auth.get_api_key("anthropic") == "sk-ant-fromenv"


def test_empty_key_not_stored(home, monkeypatch):
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "   ")
    auth.cmd_auth(_ns("set"))
    assert auth.get_api_key("anthropic") is None


def test_remove_deletes_stored_key(home):
    auth._save_state({"mode": "auto", "keys": {"anthropic": "sk-ant-stored"}})
    auth.cmd_auth(_ns("remove"))
    assert auth.get_api_key("anthropic") is None


def test_remove_when_absent_is_noop(home, capsys):
    auth.cmd_auth(_ns("remove"))
    assert "No stored key" in capsys.readouterr().out


def test_set_does_not_clobber_mode(home, monkeypatch):
    auth._save_state({"mode": "subscription", "keys": {}})
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "sk-ant-test-1234567890")
    auth.cmd_auth(_ns("set"))
    assert auth._load_state()["mode"] == "subscription"


# ── masking ───────────────────────────────────────────────────────────────────

def test_get_masks_and_reports_source(home, capsys):
    auth._save_state({"mode": "auto", "keys": {"anthropic": "sk-ant-api03-abcdefghij1234"}})
    auth.cmd_auth(_ns("get"))
    out = capsys.readouterr().out
    assert "stored credential" in out
    assert "abcdefghij" not in out       # middle hidden
    assert "sk-ant-api" in out and "1234" in out


def test_unknown_provider_errors(home):
    with pytest.raises(SystemExit):
        auth.cmd_auth(_ns("get", target="bogus"))


# ── modes ─────────────────────────────────────────────────────────────────────

def test_default_mode_is_auto(home):
    assert auth._load_state()["mode"] == "auto"


def test_use_persists_mode(home):
    auth.cmd_auth(_ns("use", "subscription"))
    assert auth._load_state()["mode"] == "subscription"


def test_use_rejects_unknown_mode(home):
    with pytest.raises(SystemExit):
        auth.cmd_auth(_ns("use", "bogus"))


def test_resolve_auto_prefers_key(home, monkeypatch):
    monkeypatch.setattr(auth, "claude_code_logged_in", lambda: True)
    auth._save_state({"mode": "auto", "keys": {"anthropic": "sk-ant-k"}})
    assert auth.resolve_auth() == {"mode": "api-key", "key": "sk-ant-k"}


def test_resolve_auto_falls_back_to_subscription(home, monkeypatch):
    monkeypatch.setattr(auth, "claude_code_logged_in", lambda: True)
    assert auth.resolve_auth() == {"mode": "subscription"}


def test_resolve_auto_none_when_nothing_available(home):
    # home fixture already stubs claude_code_logged_in → False, env key cleared
    assert auth.resolve_auth()["mode"] == "none"


def test_resolve_subscription_mode_ignores_key(home):
    auth._save_state({"mode": "subscription", "keys": {"anthropic": "sk-ant-k"}})
    assert auth.resolve_auth() == {"mode": "subscription"}


def test_resolve_api_key_mode_without_key_is_none(home):
    auth._save_state({"mode": "api-key", "keys": {}})
    assert auth.resolve_auth()["mode"] == "none"
