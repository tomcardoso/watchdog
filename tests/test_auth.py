"""Tests for `watchdog auth` — modes, setup picker, API key storage, env precedence."""

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
    auth._save_state({"mode": "api-key", "keys": {"anthropic": "sk-ant-stored"}})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fromenv")
    assert auth.get_api_key("anthropic") == "sk-ant-fromenv"


def test_empty_key_not_stored(home, monkeypatch):
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "   ")
    auth.cmd_auth(_ns("set"))
    assert auth.get_api_key("anthropic") is None


def test_remove_deletes_stored_key(home):
    auth._save_state({"mode": "api-key", "keys": {"anthropic": "sk-ant-stored"}})
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
    auth._save_state({"mode": "api-key", "keys": {"anthropic": "sk-ant-api03-abcdefghij1234"}})
    auth.cmd_auth(_ns("get"))
    out = capsys.readouterr().out
    assert "stored credential" in out
    assert "abcdefghij" not in out       # middle hidden
    assert "sk-ant-api" in out and "1234" in out


def test_unknown_provider_errors(home):
    with pytest.raises(SystemExit):
        auth.cmd_auth(_ns("get", target="bogus"))


# ── modes ─────────────────────────────────────────────────────────────────────

def test_default_mode_is_unconfigured(home):
    assert auth._load_state()["mode"] is None


def test_status_unconfigured_points_to_setup(home, capsys):
    auth.cmd_auth(_ns())
    out = capsys.readouterr().out
    assert "Not configured" in out
    assert "watchdog setup" in out


def test_use_persists_mode(home):
    auth.cmd_auth(_ns("use", "subscription"))
    assert auth._load_state()["mode"] == "subscription"


def test_use_rejects_unknown_mode(home):
    with pytest.raises(SystemExit):
        auth.cmd_auth(_ns("use", "auto"))   # 'auto' is no longer a mode


def test_resolve_unconfigured_is_none(home):
    assert auth.resolve_auth()["mode"] == "none"


def test_resolve_subscription_ignores_key(home):
    auth._save_state({"mode": "subscription", "keys": {"anthropic": "sk-ant-k"}})
    assert auth.resolve_auth() == {"mode": "subscription"}


def test_resolve_api_key_with_key(home):
    auth._save_state({"mode": "api-key", "keys": {"anthropic": "sk-ant-k"}})
    assert auth.resolve_auth() == {"mode": "api-key", "key": "sk-ant-k"}


def test_resolve_api_key_without_key_is_none(home):
    auth._save_state({"mode": "api-key", "keys": {}})
    assert auth.resolve_auth()["mode"] == "none"


# ── Claude Code login detection ───────────────────────────────────────────────

def test_keychain_check_skipped_off_darwin(monkeypatch):
    monkeypatch.setattr(auth.sys, "platform", "linux")
    assert auth._keychain_has_claude_creds() is False


def test_keychain_check_handles_missing_security_binary(monkeypatch):
    monkeypatch.setattr(auth.sys, "platform", "darwin")
    monkeypatch.setattr(auth.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    assert auth._keychain_has_claude_creds() is False


def test_logged_in_true_via_keychain_when_file_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_claude_code_creds_path", lambda: tmp_path / "nope")
    monkeypatch.setattr(auth, "_keychain_has_claude_creds", lambda: True)
    assert auth.claude_code_logged_in() is True


def test_logged_in_false_when_neither_present(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_claude_code_creds_path", lambda: tmp_path / "nope")
    monkeypatch.setattr(auth, "_keychain_has_claude_creds", lambda: False)
    assert auth.claude_code_logged_in() is False


# ── setup picker ──────────────────────────────────────────────────────────────

def test_setup_picks_subscription(home, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "1")
    auth.setup_auth_interactive(interactive=True)
    assert auth._load_state()["mode"] == "subscription"


def test_setup_picks_api_key_and_stores(home, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "2")
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "sk-ant-setupkey-123456")
    auth.setup_auth_interactive(interactive=True)
    state = auth._load_state()
    assert state["mode"] == "api-key"
    assert state["keys"]["anthropic"] == "sk-ant-setupkey-123456"


def test_setup_noninteractive_leaves_unconfigured(home):
    auth.setup_auth_interactive(interactive=False)
    assert auth._load_state()["mode"] is None
