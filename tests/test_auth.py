"""Key storage and resolution (`watchdog.auth`): environment precedence, auth modes, Claude Code
login detection, provider readiness and the status Settings shows."""

import json
import os
import stat

import pytest

from watchdog import auth, config


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Watchdog's folder in tmp_path, the real env key cleared, Claude Code logged out."""
    monkeypatch.setattr(config, "HOME", tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(auth, "claude_code_logged_in", lambda: False)
    return tmp_path


def _save(mode, **keys):
    """Store `mode` and one default key per provider, as Settings does."""
    state = {"mode": mode, "keys": {}}
    for provider, key in keys.items():
        auth.store_default_key(state, provider, key)
    auth.save_state(state)


def _config(home, **values):
    (home / "config.json").write_text(json.dumps(values))


# ── key storage ───────────────────────────────────────────────────────────────

def test_get_api_key_unset(home):
    assert auth.get_api_key("anthropic") is None


def test_env_var_takes_precedence(home, monkeypatch):
    _save("api-key", anthropic="sk-ant-stored")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fromenv")
    assert auth.get_api_key("anthropic") == "sk-ant-fromenv"


def test_openai_env_var_precedence(home, monkeypatch):
    _save("api-key", openai="sk-stored")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fromenv")
    assert auth.get_api_key("openai") == "sk-fromenv"


def test_deepseek_is_a_known_provider(home):
    assert "deepseek" in auth.PROVIDERS
    assert auth.PROVIDERS["deepseek"]["env"] == "DEEPSEEK_API_KEY"


def test_gemini_is_a_known_provider(home):
    assert "gemini" in auth.PROVIDERS
    assert auth.PROVIDERS["gemini"]["env"] == "GEMINI_API_KEY"


def test_credentials_file_is_0600(home):
    _save("api-key", anthropic="sk-ant-test-1234567890")
    assert stat.S_IMODE(os.stat(auth.credentials_path()).st_mode) == 0o600


# ── masking ───────────────────────────────────────────────────────────────────

def test_mask_hides_middle():
    masked = auth.mask("sk-ant-api03-abcdefghij1234")
    assert "abcdefghij" not in masked
    assert masked.startswith("sk-ant-api")
    assert masked.endswith("1234")


# ── resolve_auth ────────────────────────────────────────────────────────────────

def test_default_mode_is_unconfigured(home):
    assert auth.load_state()["mode"] is None


def test_resolve_unconfigured_is_none(home):
    assert auth.resolve_auth()["mode"] == "none"


def test_resolve_subscription_ignores_key(home):
    _save("subscription", anthropic="sk-ant-k")
    assert auth.resolve_auth() == {"mode": "subscription"}


def test_resolve_api_key_with_key(home):
    _save("api-key", anthropic="sk-ant-k")
    assert auth.resolve_auth() == {"mode": "api-key", "key": "sk-ant-k"}


def test_resolve_api_key_without_key_is_none(home):
    auth.save_state({"mode": "api-key", "keys": {}})
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


# ── status: every stored key is listed, labelled in-use / unused ──────────────

def _key_row(provider):
    return next(k for k in auth.status_data()["keys"] if k["provider"] == provider)


def test_status_lists_a_routed_key_as_in_use(home):
    _config(home, extractor_model="gemini:gemini-2.5-flash")
    _save("subscription", gemini="gm-routed-key-123456")
    row = _key_row("gemini")
    assert row["masked"] == auth.mask("gm-routed-key-123456") and row["status"] == "in use"


def test_status_lists_an_unrouted_key_as_unused(home):
    _config(home, classifier_model="haiku", extractor_model="sonnet", finalizer_model="haiku")
    _save("subscription", openai="sk-openai-idle1234567")
    assert _key_row("openai")["status"] == "unused"


def test_status_flags_a_stored_anthropic_key_as_inactive_on_subscription(home):
    _save("subscription", anthropic="sk-ant-still-here123")
    assert _key_row("anthropic")["status"].startswith("inactive")


def test_status_lists_no_anthropic_key_when_none_stored(home):
    _save("subscription")
    assert not [k for k in auth.status_data()["keys"] if k["provider"] == "anthropic"]


def test_status_lists_anthropic_key_as_in_use(home):
    _config(home, extractor_model="sonnet")
    _save("api-key", anthropic="sk-ant-api-key-123456")
    assert _key_row("anthropic")["status"] == "in use"


def test_status_stages_name_each_provider(home):
    _config(home, extractor_model="gemini:gemini-2.5-flash", classifier_model="haiku")
    _save("api-key", gemini="gm-key-123456")
    stages = {s["stage"]: s for s in auth.status_data()["stages"]}
    assert stages["extractor"]["provider"] == "gemini" and stages["extractor"]["ready"]
    assert stages["classifier"]["provider"] == "anthropic" and not stages["classifier"]["ready"]


# ── the subscription concurrency tune (#400, #493) ──────────────────────────────

def test_tune_sets_concurrency_once_and_leaves_a_chosen_value(home):
    assert auth.tune_concurrency_for_subscription() is True
    assert json.loads((home / "config.json").read_text())["extract_concurrency"] == auth.SUBSCRIPTION_CONCURRENCY
    _config(home, extract_concurrency=8)
    assert auth.tune_concurrency_for_subscription() is False
    assert json.loads((home / "config.json").read_text())["extract_concurrency"] == 8


def test_restore_clears_only_the_tuned_value(home):
    _config(home, extract_concurrency=auth.SUBSCRIPTION_CONCURRENCY)
    assert auth.restore_concurrency_from_subscription() is True
    assert "extract_concurrency" not in json.loads((home / "config.json").read_text())
    _config(home, extract_concurrency=8)
    assert auth.restore_concurrency_from_subscription() is False


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
    (home / "config.json").write_text(json.dumps({"local_base_url": "http://localhost:11434/v1/"}))
    # trailing slash is stripped so it composes cleanly with the appended request path
    assert auth.get_base_url("local") == "http://localhost:11434/v1"


def test_get_base_url_env_var_wins_over_configured(home, monkeypatch):
    (home / "config.json").write_text(json.dumps({"local_base_url": "http://configured:1234/v1"}))
    monkeypatch.setenv("LOCAL_BASE_URL", "http://from-env:5678/v1")
    assert auth.get_base_url("local") == "http://from-env:5678/v1"


def test_provider_ready_local_needs_only_base_url(home):
    assert auth.provider_ready("local") is False       # nothing configured yet
    (home / "config.json").write_text(json.dumps({"local_base_url": "http://localhost:11434/v1"}))
    assert auth.provider_ready("local") is True         # no key needed


def test_provider_ready_openrouter_needs_a_key_too(home):
    # openrouter has a default base URL, but still needs a key
    assert auth.provider_ready("openrouter") is False
    _save("api-key", openrouter="sk-or-test-123456")
    assert auth.provider_ready("openrouter") is True


def test_write_private_json_creates_owner_only_file(tmp_path):
    import os
    import stat
    import sys
    from watchdog.pipeline.json_io import write_private_json
    path = tmp_path / "sub" / "credentials.json"
    old = os.umask(0o022)
    try:
        write_private_json(path, {"keys": {"anthropic": "sk"}})
    finally:
        os.umask(old)
    assert json.loads(path.read_text())["keys"]["anthropic"] == "sk"
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


# ── routing every stage to one provider's model (first-run setup) ────────────────────────────

def test_route_stages_applies_one_model_and_each_stages_default_effort():
    cfg = {}
    auth.route_stages_to_model(cfg, "gemini", "gemini:gemini-3.7-flash")
    assert cfg["classifier_model"] == cfg["extractor_model"] == cfg["finalizer_model"] \
        == "gemini:gemini-3.7-flash"
    assert (cfg["classifier_effort"], cfg["extractor_effort"], cfg["finalizer_effort"]) \
        == ("low", "medium", "high")


def test_route_stages_to_a_model_with_no_effort_levels_leaves_effort_unset():
    """DeepSeek's base (non-thinking) ids accept no effort level; applying one would make the
    next call fail."""
    cfg = {}
    auth.route_stages_to_model(cfg, "deepseek", "deepseek:deepseek-flash")
    assert cfg["extractor_model"] == "deepseek:deepseek-flash"
    assert not {"classifier_effort", "extractor_effort", "finalizer_effort"} & set(cfg)
