"""Tests for `watchdog auth` — the interactive wizard, key storage, env precedence."""

import json
import os
import stat

import pytest

from watchdog.cmd import auth, base


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Redirect WATCHDOG_HOME/CONFIG_FILE, clear the real env key, assume Claude Code logged out."""
    monkeypatch.setattr(base, "WATCHDOG_HOME", tmp_path)
    monkeypatch.setattr(base, "CONFIG_FILE", tmp_path / "config.json")
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


def test_gemini_is_a_known_provider(home):
    assert "gemini" in auth._PROVIDERS
    assert auth._PROVIDERS["gemini"]["env"] == "GEMINI_API_KEY"


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
    # mode=api-key (2), then add an OpenAI key (y), skip DeepSeek (n), skip Gemini (n).
    # (Subscription mode now offers the dedicated metered-ingestion wizard instead — see
    # test_setup_subscription_declines_metered_ingestion / test_setup_subscription_routes_
    # ingestion_to_metered_provider below.)
    # Extras are offered in _PROVIDERS order: OpenAI (y), DeepSeek (n), Gemini (n),
    # Local (n), OpenRouter (n).
    _answers(monkeypatch, "2", "y", "n", "n", "n", "n")
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "sk-openai-setup-123456")
    auth.setup_auth_interactive(interactive=True)
    state = auth._load_state()
    assert state["mode"] == "api-key"
    assert state["keys"]["openai"] == "sk-openai-setup-123456"
    assert "deepseek" not in state["keys"]       # declined


def test_setup_subscription_declines_metered_ingestion(home, monkeypatch):
    # mode=subscription (1), then decline routing ingestion to a metered service (n).
    _answers(monkeypatch, "1", "n")
    auth.setup_auth_interactive(interactive=True)
    state = auth._load_state()
    assert state["mode"] == "subscription"
    assert state["keys"] == {}


def test_setup_subscription_declined_metered_tunes_concurrency(home, monkeypatch, capsys):
    # Staying on the subscription for ingestion (issue #400) auto-tunes extract_concurrency
    # down from the built-in default of 5, since concurrent extractions on that path share
    # one Claude Code session's rate limit.
    _answers(monkeypatch, "1", "n")
    auth.setup_auth_interactive(interactive=True)
    config = json.loads(base.CONFIG_FILE.read_text())
    assert config["extract_concurrency"] == 3
    assert "Detected Claude subscription auth" in capsys.readouterr().out


def test_setup_subscription_declined_metered_leaves_custom_concurrency(home, monkeypatch):
    # A concurrency value the user already set (via `watchdog configure`, or a prior
    # auto-tune) is a deliberate choice — the auto-tune must not silently overwrite it.
    base.CONFIG_FILE.write_text(json.dumps({"extract_concurrency": 8}))
    _answers(monkeypatch, "1", "n")
    auth.setup_auth_interactive(interactive=True)
    config = json.loads(base.CONFIG_FILE.read_text())
    assert config["extract_concurrency"] == 8


def test_setup_subscription_routes_ingestion_to_metered_provider(home, tmp_path, monkeypatch):
    monkeypatch.setattr(base, "CONFIG_FILE", tmp_path / "config.json")
    # mode=subscription (1), route to metered (y), pick Gemini (3rd provider), then pick the
    # first listed model for each of the three ingest stages.
    _answers(monkeypatch, "1", "y", "3", "1", "1", "1")
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "gm-test-key-123456")
    auth.setup_auth_interactive(interactive=True)

    state = auth._load_state()
    assert state["mode"] == "subscription"
    assert state["keys"]["gemini"] == "gm-test-key-123456"

    config = json.loads((tmp_path / "config.json").read_text())
    assert config["classifier_model"].startswith("gemini:")
    assert config["extractor_model"].startswith("gemini:")
    assert config["finalizer_model"].startswith("gemini:")
    # Extraction no longer runs on the subscription session, so nothing tunes concurrency.
    assert "extract_concurrency" not in config


def test_setup_api_key_does_not_tune_concurrency(home, monkeypatch):
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "sk-ant-setupkey-123456")
    # mode=api-key (2), then decline all five extra-provider offers.
    _answers(monkeypatch, "2", "n", "n", "n", "n", "n")
    auth.setup_auth_interactive(interactive=True)
    config = json.loads(base.CONFIG_FILE.read_text()) if base.CONFIG_FILE.exists() else {}
    assert "extract_concurrency" not in config


def test_setup_api_key_restores_stale_subscription_tune(home, monkeypatch, capsys):
    # #493 follow-up — re-running `watchdog setup` and picking api-key this time must not leave
    # a stale auto-tuned extract_concurrency=3 from a prior subscription run capping a metered key.
    base.CONFIG_FILE.write_text(json.dumps({"extract_concurrency": 3}))
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "sk-ant-setupkey-123456")
    _answers(monkeypatch, "2", "n", "n", "n", "n", "n")
    auth.setup_auth_interactive(interactive=True)
    config = json.loads(base.CONFIG_FILE.read_text())
    assert "extract_concurrency" not in config
    assert "reset to the metered default" in capsys.readouterr().out


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


# The service picker leads with a "Done — nothing to change" row, so a provider's number is its
# index in _PROVIDERS + 2 (+1 to make it 1-based, +1 for the Done row).
def _provider_choice(name: str) -> str:
    return str(list(auth._PROVIDERS).index(name) + 2)


def test_bare_auth_interactive_done_row_changes_nothing(home, monkeypatch):
    auth._save_state({"mode": "subscription", "keys": {}})
    _tty(monkeypatch, True)
    _answers(monkeypatch, "1")          # "Done — nothing to change" is the first row
    auth.cmd_auth(object())
    assert auth._load_state()["mode"] == "subscription"


def test_bare_auth_interactive_switch_to_api_key(home, monkeypatch):
    # Anthropic -> mode 2 (api-key) -> paste key
    _tty(monkeypatch, True)
    _answers(monkeypatch, _provider_choice("anthropic"), "2")
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "sk-ant-wizard-123456")
    auth.cmd_auth(object())
    state = auth._load_state()
    assert state["mode"] == "api-key"
    assert state["keys"]["anthropic"] == "sk-ant-wizard-123456"


def test_bare_auth_interactive_switch_to_subscription(home, monkeypatch):
    auth._save_state({"mode": "api-key", "keys": {"anthropic": "sk-ant-old"}})
    _tty(monkeypatch, True)
    _answers(monkeypatch, _provider_choice("anthropic"), "1")
    auth.cmd_auth(object())
    assert auth._load_state()["mode"] == "subscription"


def test_bare_auth_interactive_switch_to_subscription_tunes_concurrency(home, monkeypatch, capsys):
    # #493 — a later mode switch via `watchdog auth` needs the same auto-tune (#400) that
    # `watchdog setup` applies, since in practice mode switches happen through `watchdog auth`
    # far more often than through the initial setup wizard.
    auth._save_state({"mode": "api-key", "keys": {"anthropic": "sk-ant-old"}})
    _tty(monkeypatch, True)
    _answers(monkeypatch, _provider_choice("anthropic"), "1")
    auth.cmd_auth(object())
    config = json.loads(base.CONFIG_FILE.read_text())
    assert config["extract_concurrency"] == 3
    assert "Detected Claude subscription auth" in capsys.readouterr().out


def test_bare_auth_interactive_switch_to_subscription_leaves_custom_concurrency(home, monkeypatch):
    base.CONFIG_FILE.write_text(json.dumps({"extract_concurrency": 8}))
    auth._save_state({"mode": "api-key", "keys": {"anthropic": "sk-ant-old"}})
    _tty(monkeypatch, True)
    _answers(monkeypatch, _provider_choice("anthropic"), "1")
    auth.cmd_auth(object())
    config = json.loads(base.CONFIG_FILE.read_text())
    assert config["extract_concurrency"] == 8


def test_bare_auth_interactive_switch_to_subscription_skips_tune_when_extraction_routed_elsewhere(home, monkeypatch):
    # Extraction already routed to another provider (#325) — nothing on the subscription
    # session would be throttled, so the auto-tune shouldn't fire.
    base.CONFIG_FILE.write_text(json.dumps({"extractor_model": "gemini:gemini-2.5-flash"}))
    auth._save_state({"mode": "api-key", "keys": {"anthropic": "sk-ant-old"}})
    _tty(monkeypatch, True)
    _answers(monkeypatch, _provider_choice("anthropic"), "1")
    auth.cmd_auth(object())
    config = json.loads(base.CONFIG_FILE.read_text())
    assert "extract_concurrency" not in config


def test_bare_auth_interactive_switch_to_api_key_does_not_tune_concurrency(home, monkeypatch):
    _tty(monkeypatch, True)
    _answers(monkeypatch, _provider_choice("anthropic"), "2")
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "sk-ant-wizard-123456")
    auth.cmd_auth(object())
    config = json.loads(base.CONFIG_FILE.read_text()) if base.CONFIG_FILE.exists() else {}
    assert "extract_concurrency" not in config


def test_bare_auth_interactive_switch_to_api_key_restores_stale_subscription_tune(home, monkeypatch, capsys):
    # #493 follow-up — a still-active auto-tuned extract_concurrency=3 must not silently cap a
    # metered key forever once the user switches off subscription auth via `watchdog auth`.
    base.CONFIG_FILE.write_text(json.dumps({"extract_concurrency": 3}))
    auth._save_state({"mode": "subscription", "keys": {}})
    _tty(monkeypatch, True)
    _answers(monkeypatch, _provider_choice("anthropic"), "2")
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "sk-ant-wizard-123456")
    auth.cmd_auth(object())
    config = json.loads(base.CONFIG_FILE.read_text())
    assert "extract_concurrency" not in config
    assert "reset to the metered default" in capsys.readouterr().out


def test_bare_auth_interactive_switch_to_api_key_leaves_non_auto_tuned_concurrency(home, monkeypatch):
    # A value the user set deliberately (anything other than the exact auto-tuned 3) is left
    # alone — the restore can't tell a deliberate choice from a stale tune, so it only ever
    # touches the one value it knows it wrote itself.
    base.CONFIG_FILE.write_text(json.dumps({"extract_concurrency": 8}))
    auth._save_state({"mode": "subscription", "keys": {}})
    _tty(monkeypatch, True)
    _answers(monkeypatch, _provider_choice("anthropic"), "2")
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "sk-ant-wizard-123456")
    auth.cmd_auth(object())
    config = json.loads(base.CONFIG_FILE.read_text())
    assert config["extract_concurrency"] == 8


def test_bare_auth_interactive_store_openai_key(home, monkeypatch):
    _tty(monkeypatch, True)
    _answers(monkeypatch, _provider_choice("openai"))
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "sk-openai-abc1234567")
    auth.cmd_auth(object())
    assert auth.get_api_key("openai") == "sk-openai-abc1234567"
    assert auth.get_api_key("anthropic") is None    # untouched


def test_bare_auth_interactive_replace_existing_key(home, monkeypatch):
    auth._save_state({"mode": "api-key", "keys": {"openai": "sk-openai-old1234567"}})
    _tty(monkeypatch, True)
    _answers(monkeypatch, _provider_choice("openai"), "1")       # 1 = Replace
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "sk-openai-new1234567")
    auth.cmd_auth(object())
    assert auth.get_api_key("openai") == "sk-openai-new1234567"


def test_bare_auth_interactive_delete_existing_key(home, monkeypatch):
    auth._save_state({"mode": "api-key", "keys": {"openai": "sk-openai-old1234567"}})
    _tty(monkeypatch, True)
    _answers(monkeypatch, _provider_choice("openai"), "2")       # 2 = Delete
    auth.cmd_auth(object())
    assert auth.get_api_key("openai") is None


def test_bare_auth_interactive_cancel_leaves_existing_key(home, monkeypatch):
    auth._save_state({"mode": "api-key", "keys": {"openai": "sk-openai-old1234567"}})
    _tty(monkeypatch, True)
    _answers(monkeypatch, _provider_choice("openai"), "3")       # 3 = Cancel
    auth.cmd_auth(object())
    assert auth.get_api_key("openai") == "sk-openai-old1234567"


def test_bare_auth_invalid_provider_choice_changes_nothing(home, monkeypatch):
    auth._save_state({"mode": "subscription", "keys": {}})
    _tty(monkeypatch, True)
    _answers(monkeypatch, "99")
    auth.cmd_auth(object())
    assert auth._load_state()["mode"] == "subscription"


def test_bare_auth_empty_key_entry_does_not_store(home, monkeypatch):
    _tty(monkeypatch, True)
    _answers(monkeypatch, _provider_choice("openai"))
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "   ")
    auth.cmd_auth(object())
    assert auth.get_api_key("openai") is None


# ── status: every stored key is listed, labelled in-use / unused ──────────────

def test_status_lists_a_routed_key_as_in_use(home, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(base, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({"extractor_model": "gemini:gemini-2.5-flash"}))
    auth._save_state({"mode": "subscription", "keys": {"gemini": "gm-routed-key-123456"}})
    _tty(monkeypatch, False)
    auth.cmd_auth(object())
    out = capsys.readouterr().out
    # The masked value of a key already in active use must appear — it used to be omitted
    # entirely, because only keys with no stage routed to them were listed.
    assert auth._mask("gm-routed-key-123456") in out
    assert "in use" in out


def test_status_lists_an_unrouted_key_as_unused(home, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(base, "CONFIG_FILE", tmp_path / "config.json")
    # Every stage explicitly routed to Claude — the pipeline default is openai:gpt-5.6-luna, so
    # without this the stored openai key would actually be in use, not unrouted.
    (tmp_path / "config.json").write_text(json.dumps(
        {"classifier_model": "haiku", "extractor_model": "sonnet", "finalizer_model": "haiku"}))
    auth._save_state({"mode": "subscription", "keys": {"openai": "sk-openai-idle1234567"}})
    _tty(monkeypatch, False)
    auth.cmd_auth(object())
    out = capsys.readouterr().out
    assert auth._mask("sk-openai-idle1234567") in out
    assert "unused" in out


def test_status_flags_a_stored_anthropic_key_as_inactive_on_subscription(home, monkeypatch, capsys):
    # Switching Claude access back to subscription leaves a previously-stored Anthropic key on
    # disk (resolve_auth() just won't return it) — the status view used to say nothing about it,
    # which read as the key having been deleted.
    auth._save_state({"mode": "subscription", "keys": {"anthropic": "sk-ant-still-here123"}})
    _tty(monkeypatch, False)
    auth.cmd_auth(object())
    out = capsys.readouterr().out
    assert auth._mask("sk-ant-still-here123") in out
    assert "inactive" in out


def test_status_says_nothing_about_anthropic_key_when_none_stored(home, monkeypatch, capsys):
    auth._save_state({"mode": "subscription", "keys": {}})
    _tty(monkeypatch, False)
    auth.cmd_auth(object())
    out = capsys.readouterr().out
    assert "inactive" not in out


def test_status_lists_anthropic_key_under_providers_as_in_use(home, tmp_path, monkeypatch, capsys):
    # Providers is meant to be the definitive list of stored keys — Anthropic used to be
    # filtered out of it unconditionally, which contradicted that claim.
    monkeypatch.setattr(base, "CONFIG_FILE", tmp_path / "config.json")
    # At least one stage routed to Claude — the pipeline default is openai:gpt-5.6-luna, so
    # without this the stored anthropic key would be inactive, not in use.
    (tmp_path / "config.json").write_text(json.dumps({"extractor_model": "sonnet"}))
    auth._save_state({"mode": "api-key", "keys": {"anthropic": "sk-ant-api-key-123456"}})
    _tty(monkeypatch, False)
    auth.cmd_auth(object())
    out = capsys.readouterr().out
    assert "Providers" in out
    assert auth._mask("sk-ant-api-key-123456") in out
    assert "in use" in out


def test_status_claude_code_heading_has_no_ingestion_subtitle(home, monkeypatch, capsys):
    # The old "— interactive commands, and ingestion by default" subtitle overstated this
    # command's scope: `watchdog auth` only ever configures ingestion, never the separate
    # interactive Claude Code session (which manages its own login).
    auth._save_state({"mode": "subscription", "keys": {}})
    _tty(monkeypatch, False)
    auth.cmd_auth(object())
    out = capsys.readouterr().out
    assert "interactive commands" not in out


# ── status: an Anthropic-routed ingestion stage names its billing mode ────────

def test_status_ingestion_row_names_anthropic_mode(home, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(base, "CONFIG_FILE", tmp_path / "config.json")
    # The pipeline default is openai:gpt-5.6-luna — route a stage to Claude explicitly so there's
    # an anthropic-routed row to name a billing mode on.
    (tmp_path / "config.json").write_text(json.dumps({"extractor_model": "sonnet"}))
    auth._save_state({"mode": "api-key", "keys": {"anthropic": "sk-ant-anything-123456"}})
    _tty(monkeypatch, False)
    auth.cmd_auth(object())
    out = capsys.readouterr().out
    assert "anthropic · api-key" in out


def test_status_ingestion_row_names_anthropic_subscription_mode(home, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(base, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({"extractor_model": "sonnet"}))
    auth._save_state({"mode": "subscription", "keys": {}})
    _tty(monkeypatch, False)
    auth.cmd_auth(object())
    out = capsys.readouterr().out
    assert "anthropic · subscription" in out


def test_status_ingestion_row_omits_mode_for_non_anthropic_provider(home, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(base, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({"extractor_model": "gemini:gemini-2.5-flash"}))
    auth._save_state({"mode": "api-key", "keys": {"gemini": "gm-key-123456"}})
    _tty(monkeypatch, False)
    auth.cmd_auth(object())
    out = capsys.readouterr().out
    assert "(gemini)" in out
    assert "gemini ·" not in out


# ── ensure_provider_key: picking a model must not leave a stage keyless ───────

def test_ensure_provider_key_prompts_and_stores_for_new_provider(home, monkeypatch):
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "gm-fresh-key-123456")
    auth.ensure_provider_key("gemini:gemini-2.5-flash")
    assert auth.get_api_key("gemini") == "gm-fresh-key-123456"


def test_ensure_provider_key_noop_for_claude_tier(home, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("must not prompt for a key when the model is a Claude tier")
    monkeypatch.setattr(auth, "getpass", _boom)
    auth.ensure_provider_key("sonnet")
    assert auth._load_state()["keys"] == {}


def test_ensure_provider_key_noop_when_key_already_stored(home, monkeypatch):
    auth._save_state({"mode": "subscription", "keys": {"openai": "sk-openai-existing123"}})

    def _boom(*a, **k):
        raise AssertionError("must not re-prompt when a key is already stored")
    monkeypatch.setattr(auth, "getpass", _boom)
    auth.ensure_provider_key("openai:gpt-5-mini")
    assert auth.get_api_key("openai") == "sk-openai-existing123"


def test_ensure_provider_key_noop_when_key_in_environment(home, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-fromenv12")

    def _boom(*a, **k):
        raise AssertionError("must not prompt when the provider's env var is set")
    monkeypatch.setattr(auth, "getpass", _boom)
    auth.ensure_provider_key("deepseek:deepseek-chat")
    assert auth._load_state()["keys"] == {}      # nothing written to disk


def test_ensure_provider_key_declined_leaves_no_key(home, monkeypatch):
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "")
    auth.ensure_provider_key("gemini:gemini-2.5-flash")
    assert auth.get_api_key("gemini") is None


def test_configure_model_key_prompts_for_the_providers_key(home, tmp_path, monkeypatch):
    """The gap this closes: routing a stage to a provider you have no key for used to succeed
    silently and only blow up mid-ingest."""
    from watchdog.cmd import setup as setup_cmd

    monkeypatch.setattr(base, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(setup_cmd, "_pick_model_interactive", lambda *a, **k: "gemini:gemini-2.5-flash")
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "gm-configure-key-123")

    config: dict = {}
    setup_cmd._edit_key_interactive(config, "extractor_model")

    assert config["extractor_model"] == "gemini:gemini-2.5-flash"
    assert auth.get_api_key("gemini") == "gm-configure-key-123"


# ── local / self-hosted + OpenRouter (#380) ──────────────────────────────────

def test_local_requires_no_key():
    assert auth.provider_requires_key("local") is False
    assert auth.provider_requires_key("openrouter") is True
    assert auth.provider_requires_key("openai") is True


def test_get_base_url_unset_returns_none_for_local(home):
    assert auth.get_base_url("local") is None


def test_get_base_url_openrouter_has_a_default(home):
    assert auth.get_base_url("openrouter") == "https://openrouter.ai/api/v1"


def test_get_base_url_reads_configured_value(home, tmp_path):
    base.CONFIG_FILE.write_text(json.dumps({"local_base_url": "http://localhost:11434/v1/"}))
    # trailing slash is stripped so it composes cleanly with the appended request path
    assert auth.get_base_url("local") == "http://localhost:11434/v1"


def test_get_base_url_env_var_wins_over_configured(home, monkeypatch):
    base.CONFIG_FILE.write_text(json.dumps({"local_base_url": "http://configured:1234/v1"}))
    monkeypatch.setenv("LOCAL_BASE_URL", "http://from-env:5678/v1")
    assert auth.get_base_url("local") == "http://from-env:5678/v1"


def test_provider_ready_local_needs_only_base_url(home):
    assert auth.provider_ready("local") is False       # nothing configured yet
    base.CONFIG_FILE.write_text(json.dumps({"local_base_url": "http://localhost:11434/v1"}))
    assert auth.provider_ready("local") is True         # no key needed


def test_provider_ready_openrouter_needs_a_key_too(home):
    # openrouter has a default base URL, but still needs a key
    assert auth.provider_ready("openrouter") is False
    auth._save_state({"mode": "api-key", "keys": {"openrouter": "sk-or-test-123456"}})
    assert auth.provider_ready("openrouter") is True


def test_prompt_and_store_base_url(home, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "http://localhost:11434/v1")
    assert auth.prompt_and_store_base_url("local") is True
    config = json.loads(base.CONFIG_FILE.read_text())
    assert config["local_base_url"] == "http://localhost:11434/v1"


def test_prompt_and_store_base_url_blank_is_skipped(home, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "")
    assert auth.prompt_and_store_base_url("local") is False
    assert auth.get_base_url("local") is None


def test_ensure_provider_key_prompts_for_local_base_url_not_a_key(home, monkeypatch):
    """Picking a `local:...` model should prompt for the base URL, and never ask for a key —
    most self-hosted runners don't check for one."""
    monkeypatch.setattr("builtins.input", lambda *a: "http://localhost:11434/v1")

    def _boom(*a, **k):
        raise AssertionError("must not prompt for a key when the provider doesn't require one")
    monkeypatch.setattr(auth, "getpass", _boom)

    auth.ensure_provider_key("local:llama-3.3-70b")
    assert auth.get_base_url("local") == "http://localhost:11434/v1"
    assert auth.get_api_key("local") is None


def test_ensure_provider_key_noop_when_local_already_ready(home, monkeypatch):
    base.CONFIG_FILE.write_text(json.dumps({"local_base_url": "http://localhost:11434/v1"}))

    def _boom(*a, **k):
        raise AssertionError("must not re-prompt once the provider is already ready")
    monkeypatch.setattr("builtins.input", _boom)
    monkeypatch.setattr(auth, "getpass", _boom)
    auth.ensure_provider_key("local:llama-3.3-70b")


def test_ensure_provider_key_prompts_for_openrouter_key(home, monkeypatch):
    """OpenRouter has a default base URL, so only the key needs prompting."""
    monkeypatch.setattr(auth, "getpass", lambda *a, **k: "sk-or-test-123456")
    auth.ensure_provider_key("openrouter:anthropic/claude-3.5-sonnet")
    assert auth.get_api_key("openrouter") == "sk-or-test-123456"
