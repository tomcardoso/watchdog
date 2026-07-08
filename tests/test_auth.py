"""Tests for `watchdog auth` — the interactive wizard, key storage, env precedence."""

import os
import stat

import pytest

from watchdog.cmd import auth, base


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Redirect WATCHDOG_HOME, clear the real env key, assume Claude Code logged out."""
    monkeypatch.setattr(base, "WATCHDOG_HOME", tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(auth, "claude_code_logged_in", lambda: False)
    return tmp_path


def _tty(monkeypatch, value: bool) -> None:
    monkeypatch.setattr(auth.sys.stdin, "isatty", lambda: value)


def _answers(monkeypatch, *values):
    it = iter(values)
    monkeypatch.setattr("builtins.input", lambda *a: next(it))


# ── key storage ───────────────────────────────────────────────────────────────

def test_get_api_key_unset(home):
    assert auth.get_api_key("anthropic") is None


def test_env_var_takes_precedence(home, monkeypatch):
    auth._save_state({"mode": "api-key", "keys": {"anthropic": "sk-ant-stored"}})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fromenv")
    assert auth.get_api_key("anthropic") == "sk-ant-fromenv"


def test_openai_env_var_precedence(home, monkeypatch):
    auth._save_state({"mode": "api-key", "keys": {"openai": "sk-stored"}})
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fromenv")
    assert auth.get_api_key("openai") == "sk-fromenv"


def test_deepseek_is_a_known_provider(home):
    assert "deepseek" in auth._PROVIDERS
    assert auth._PROVIDERS["deepseek"]["env"] == "DEEPSEEK_API_KEY"


def test_credentials_file_is_0600(home):
    auth._save_state({"mode": "api-key", "keys": {"anthropic": "sk-ant-test-1234567890"}})
    assert stat.S_IMODE(os.stat(auth._credentials_path()).st_mode) == 0o600


# ── masking ───────────────────────────────────────────────────────────────────

def test_mask_hides_middle():
    masked = auth._mask("sk-ant-api03-abcdefghij1234")
    assert "abcdefghij" not in masked
    assert masked.startswith("sk-ant-api")
    assert masked.endswith("1234")


# ── resolve_auth ────────────────────────────────────────────────────────────────

def test_default_mode_is_unconfigured(home):
    assert auth._load_state()["mode"] is None


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


# ── setup picker (`watchdog setup`) ───────────────────────────────────────────

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


def test_setup_offers_extra_provider_keys(home, monkeypatch):
    # mode=subscription (1), then add an OpenAI key (y), skip DeepSeek (n).
    _answers(monkeypatch, "1", "y", "n")
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "sk-openai-setup-123456")
    auth.setup_auth_interactive(interactive=True)
    state = auth._load_state()
    assert state["mode"] == "subscription"
    assert state["keys"]["openai"] == "sk-openai-setup-123456"
    assert "deepseek" not in state["keys"]       # declined


# ── `watchdog auth` (bare) — status + interactive wizard ─────────────────────

def test_bare_auth_unconfigured_points_to_setup(home, monkeypatch, capsys):
    _tty(monkeypatch, False)
    auth.cmd_auth(object())
    out = capsys.readouterr().out
    assert "Not configured" in out
    assert "watchdog setup" in out


def test_bare_auth_noninteractive_just_prints_status(home, monkeypatch, capsys):
    auth._save_state({"mode": "subscription", "keys": {}})
    _tty(monkeypatch, False)
    auth.cmd_auth(object())
    out = capsys.readouterr().out
    assert "subscription" in out
    assert "Change something" not in out


def test_bare_auth_interactive_decline_changes_nothing(home, monkeypatch):
    auth._save_state({"mode": "subscription", "keys": {}})
    _tty(monkeypatch, True)
    _answers(monkeypatch, "n")
    auth.cmd_auth(object())
    assert auth._load_state()["mode"] == "subscription"


def test_bare_auth_interactive_switch_to_api_key(home, monkeypatch):
    # Change something? y -> provider 1 (anthropic) -> choice 2 (api-key) -> paste key
    _tty(monkeypatch, True)
    _answers(monkeypatch, "y", "1", "2")
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "sk-ant-wizard-123456")
    auth.cmd_auth(object())
    state = auth._load_state()
    assert state["mode"] == "api-key"
    assert state["keys"]["anthropic"] == "sk-ant-wizard-123456"


def test_bare_auth_interactive_switch_to_subscription(home, monkeypatch):
    auth._save_state({"mode": "api-key", "keys": {"anthropic": "sk-ant-old"}})
    _tty(monkeypatch, True)
    _answers(monkeypatch, "y", "1", "1")
    auth.cmd_auth(object())
    assert auth._load_state()["mode"] == "subscription"


def test_bare_auth_interactive_store_openai_key(home, monkeypatch):
    providers = list(auth._PROVIDERS)
    openai_choice = str(providers.index("openai") + 1)
    _tty(monkeypatch, True)
    _answers(monkeypatch, "y", openai_choice)
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "sk-openai-abc1234567")
    auth.cmd_auth(object())
    assert auth.get_api_key("openai") == "sk-openai-abc1234567"
    assert auth.get_api_key("anthropic") is None    # untouched


def test_bare_auth_interactive_replace_existing_key(home, monkeypatch):
    auth._save_state({"mode": "api-key", "keys": {"openai": "sk-openai-old1234567"}})
    providers = list(auth._PROVIDERS)
    openai_choice = str(providers.index("openai") + 1)
    _tty(monkeypatch, True)
    _answers(monkeypatch, "y", openai_choice, "r")
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "sk-openai-new1234567")
    auth.cmd_auth(object())
    assert auth.get_api_key("openai") == "sk-openai-new1234567"


def test_bare_auth_interactive_delete_existing_key(home, monkeypatch):
    auth._save_state({"mode": "api-key", "keys": {"openai": "sk-openai-old1234567"}})
    providers = list(auth._PROVIDERS)
    openai_choice = str(providers.index("openai") + 1)
    _tty(monkeypatch, True)
    _answers(monkeypatch, "y", openai_choice, "d")
    auth.cmd_auth(object())
    assert auth.get_api_key("openai") is None


def test_bare_auth_interactive_cancel_leaves_existing_key(home, monkeypatch):
    auth._save_state({"mode": "api-key", "keys": {"openai": "sk-openai-old1234567"}})
    providers = list(auth._PROVIDERS)
    openai_choice = str(providers.index("openai") + 1)
    _tty(monkeypatch, True)
    _answers(monkeypatch, "y", openai_choice, "c")
    auth.cmd_auth(object())
    assert auth.get_api_key("openai") == "sk-openai-old1234567"


def test_bare_auth_invalid_provider_choice_changes_nothing(home, monkeypatch):
    auth._save_state({"mode": "subscription", "keys": {}})
    _tty(monkeypatch, True)
    _answers(monkeypatch, "y", "99")
    auth.cmd_auth(object())
    assert auth._load_state()["mode"] == "subscription"


def test_bare_auth_empty_key_entry_does_not_store(home, monkeypatch):
    providers = list(auth._PROVIDERS)
    openai_choice = str(providers.index("openai") + 1)
    _tty(monkeypatch, True)
    _answers(monkeypatch, "y", openai_choice)
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "   ")
    auth.cmd_auth(object())
    assert auth.get_api_key("openai") is None
