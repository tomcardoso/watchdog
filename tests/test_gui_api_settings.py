"""`settings.*`, `auth.*`, `skills.*` and `setup.check` handlers."""

import json
import re
import stat

import pytest

from watchdog import skills_catalog
from watchdog.cmd import setup as setup_cmd

from tests.gui_support import call, call_error

pytest_plugins = ["tests.gui_support"]   # the rich_vault / wdg_home fixtures


def config(home):
    path = home / "config.json"
    return json.loads(path.read_text()) if path.exists() else {}


def creds(home):
    path = home / "credentials.json"
    return json.loads(path.read_text()) if path.exists() else {}


def write_creds(home, mode, keys=None):
    (home / "credentials.json").write_text(json.dumps({"mode": mode, "keys": keys or {}}))


def mode_bits(path):
    return stat.S_IMODE(path.stat().st_mode)


# ── settings.schema ──────────────────────────────────────────────────────────────

def test_schema_covers_every_key_once_in_section_order(wdg_home):
    s = call("settings.schema")
    assert [sec["title"] for sec in s["sections"]] == [t for t, _, _ in setup_cmd._CONFIGURE_SECTIONS]
    keys = [k["key"] for sec in s["sections"] for k in sec["keys"]]
    assert sorted(keys) == sorted(setup_cmd._CONFIGURE_KEYS) and len(keys) == len(set(keys))
    assert s["sections"][0]["keys"][0]["key"] == "projects_dir"
    assert s["sections"][0]["blurb"] == "Where investigations are created."
    json.dumps(s)


def test_schema_unlisted_keys_land_in_other(wdg_home, monkeypatch):
    monkeypatch.setitem(setup_cmd._CONFIGURE_KEYS, "brand_new", {"short": "New", "help": "x", "type": "int", "default": 1})
    s = call("settings.schema")
    assert s["sections"][-1]["title"] == "Other"
    assert [k["key"] for k in s["sections"][-1]["keys"]] == ["brand_new"]


def keyed(schema):
    return {k["key"]: k for sec in schema["sections"] for k in sec["keys"]}


def test_schema_kinds_and_choices(wdg_home):
    k = keyed(call("settings.schema"))
    assert k["auto_approve"]["kind"] == "bool" and k["extract_concurrency"]["kind"] == "int"
    assert k["garbled_threshold"]["kind"] == "float" and k["projects_dir"]["kind"] == "path"
    assert k["ocr_engine"]["kind"] == "choice" and k["ocr_engine"]["choices"][0] == "auto"
    assert k["extractor_effort"]["kind"] == "effort"
    assert k["extractor_effort"]["choices"] == ["low", "medium", "high", "xhigh", "max"]
    assert k["extractor_model"]["kind"] == "model" and k["extractor_model"]["choices"] is None
    assert k["finalizer_briefing_model"]["kind"] == "model"
    assert k["wayback_secret_key"]["kind"] == "secret"
    assert k["chew_workers"]["kind"] == "text" and k["ocr_languages"]["kind"] == "text"
    assert k["embed_model"]["kind"] == "text"       # a Hugging Face id, not a Watchdog model value
    assert all(v["choices"] is None for key, v in k.items() if v["kind"] not in ("choice", "effort"))


def test_schema_current_default_and_display(wdg_home):
    (wdg_home / "config.json").write_text(json.dumps({
        "extract_concurrency": 7, "extractor_model": "opus", "wayback_secret_key": "hunter2",
        "auto_approve": True}))
    k = keyed(call("settings.schema"))
    assert k["extract_concurrency"]["current"] == 7 and k["extract_concurrency"]["display"] == "7"
    assert k["extract_concurrency"]["default"] == 20 and k["extract_concurrency"]["is_set"] is True
    assert k["extractor_model"]["current"] == "opus" and k["extractor_model"]["default"] == "sonnet"
    assert k["garbled_threshold"]["current"] is None and k["garbled_threshold"]["display"] == "0.6"
    assert k["garbled_threshold"]["is_set"] is False
    assert k["auto_approve"]["display"] == "true"
    assert k["classifier_model"]["display"] == "haiku"
    assert k["finalizer_synthesis_model"]["display"] == "(not set)"


def test_schema_never_returns_a_secret(wdg_home):
    (wdg_home / "config.json").write_text(json.dumps({"wayback_secret_key": "hunter2"}))
    secret = keyed(call("settings.schema"))["wayback_secret_key"]
    assert secret["current"] is None and "hunter2" not in json.dumps(call("settings.schema"))
    assert "set" in secret["display"] and secret["is_set"] is True


def test_schema_has_no_ansi_and_resolves_auto_budgets(wdg_home):
    s = call("settings.schema")
    text = json.dumps(s)
    assert "\\u001b" not in text and "\x1b" not in text
    assert re.fullmatch(r"auto \(\d+ — sonnet\)", keyed(s)["section_token_threshold"]["display"])


def test_schema_help_is_dedented(wdg_home):
    help_text = keyed(call("settings.schema"))["ocr_engine"]["help"]
    assert help_text.startswith("OCR engine used") and "\n  " not in help_text and "tesseract:" in help_text


def test_schema_with_a_corrupt_config(wdg_home):
    (wdg_home / "config.json").write_text("{nope")
    assert call_error("settings.schema")["code"] == "config_corrupt"


# ── settings.set ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key, value, stored, display", [
    ("extract_concurrency", "5", 5, "5"),
    ("garbled_threshold", "0.75", 0.75, "0.75"),
    ("auto_approve", "yes", True, "true"),
    ("telemetry", "off", False, "false"),
    ("ocr_engine", "auto", "auto", "auto"),
    ("chew_workers", "auto", "auto", "auto"),
    ("chew_workers", "4", 4, "4"),
    ("extractor_effort", "high", None, None),
    ("extractor_model", "openai:gpt-5-mini", "openai:gpt-5-mini", "openai:gpt-5-mini"),
    ("ocr_languages", "en-US, fr-FR", ["en-US", "fr-FR"], "en-US, fr-FR"),
])
def test_set_stores_what_the_cli_would(wdg_home, key, value, stored, display):
    r = call("settings.set", key=key, value=value)
    if key == "extractor_effort":
        stored, display = "high", "high"
    assert r == {"key": key, "value": stored, "display": display}
    assert config(wdg_home)[key] == stored
    assert mode_bits(wdg_home / "config.json") == 0o600


def test_set_matches_cmd_configure(wdg_home):
    import argparse
    from watchdog.cmd.setup import cmd_configure
    call("settings.set", key="dup_threshold", value="0.9")
    via_gui = config(wdg_home)
    (wdg_home / "config.json").unlink()
    cmd_configure(argparse.Namespace(key="dup_threshold", value="0.9"))
    assert config(wdg_home) == via_gui


@pytest.mark.parametrize("key, value, message", [
    ("extract_concurrency", "0", "'extract_concurrency' must be >= 1"),
    ("extract_concurrency", "many", "'extract_concurrency' must be a whole number"),
    ("garbled_threshold", "7", "'garbled_threshold' must be <= 1.0"),
    ("garbled_threshold", "nan", "'garbled_threshold' must be a finite number (e.g. 0.85)"),
    ("auto_approve", "maybe", "'auto_approve' must be true or false"),
    ("ocr_engine", "magic", "'ocr_engine' must be one of: auto, apple_vision, tesseract, easyocr, rapidocr"),
    ("chew_workers", "-1", "'chew_workers' must be >= 1"),
])
def test_set_rejects_bad_values_with_the_cli_message(wdg_home, key, value, message):
    err = call_error("settings.set", key=key, value=value)
    assert err["message"] == message and err["code"] == "bad_value"
    assert key not in config(wdg_home)


def test_set_unknown_key(wdg_home):
    assert call_error("settings.set", key="nonsense", value="1")["code"] == "unknown_key"


def test_set_projects_dir_creates_the_folder(wdg_home, tmp_path):
    target = tmp_path / "cases" / "here"
    r = call("settings.set", key="projects_dir", value=str(target))
    assert target.is_dir() and r["value"] == str(target.resolve())
    assert call_error("settings.set", key="projects_dir", value="")["code"] == "bad_value"


def test_set_empty_clears_a_text_or_model_setting(wdg_home):
    call("settings.set", key="extractor_model", value="opus")
    r = call("settings.set", key="extractor_model", value="")
    assert r["value"] is None and "extractor_model" not in config(wdg_home)
    call("settings.set", key="default_skill", value="general-records")
    assert call("settings.set", key="default_skill", value=None)["value"] is None
    assert "default_skill" not in config(wdg_home)
    assert call_error("settings.set", key="extract_concurrency", value="")["code"] == "bad_value"


def test_set_keeps_other_settings_and_never_echoes_a_secret(wdg_home):
    (wdg_home / "config.json").write_text(json.dumps({"extractor_model": "opus"}))
    r = call("settings.set", key="wayback_secret_key", value="hunter2")
    assert r["value"] is None and "hunter2" not in json.dumps(r) and "set" in r["display"]
    assert config(wdg_home) == {"extractor_model": "opus", "wayback_secret_key": "hunter2"}
    assert mode_bits(wdg_home / "config.json") == 0o600


def test_set_with_a_corrupt_config_refuses_to_overwrite_it(wdg_home):
    (wdg_home / "config.json").write_text("{nope")
    assert call_error("settings.set", key="telemetry", value="off")["code"] == "config_corrupt"
    assert (wdg_home / "config.json").read_text() == "{nope"


# ── settings.models ──────────────────────────────────────────────────────────────

def test_models(wdg_home):
    r = call("settings.models")
    assert r["efforts"] == ["low", "medium", "high", "xhigh", "max"]
    by_value = {m["value"]: m for m in r["models"]}
    sonnet = by_value["sonnet"]
    assert sonnet["provider"] == "anthropic" and sonnet["backend"] is None
    assert sonnet["label"] == "Claude Sonnet 5.5" and sonnet["id"] == "claude-sonnet-5-5"
    assert sonnet["input_per_mtok"] == pytest.approx(2.0) and sonnet["output_per_mtok"] == pytest.approx(10.0)
    assert sonnet["context_window"] and sonnet["efforts"] == [e for e in r["efforts"] if e in sonnet["efforts"]]
    assert by_value["haiku"]["efforts"] == []
    non_claude = [m for m in r["models"] if m["provider"] != "anthropic"]
    assert non_claude and all(m["value"] == f"{m['provider']}:{m['id']}" and m["backend"] == m["provider"]
                              for m in non_claude)
    assert len({m["value"] for m in r["models"]}) == len(r["models"])
    assert all(isinstance(m["notes"], (str, type(None))) for m in r["models"])


# ── auth.status ──────────────────────────────────────────────────────────────────

def test_status_unconfigured(wdg_home):
    s = call("auth.status")
    assert s["claude"]["mode"] is None and s["claude"]["logged_in"] is False
    assert "isn't set up" in s["claude"]["reason"]
    assert [x["stage"] for x in s["stages"]] == ["classifier", "extractor", "finalizer"]
    assert s["stages"][1] == {"stage": "extractor", "config_key": "extractor_model", "value": "sonnet",
                              "provider": "anthropic", "ready": False, "billing": None}
    assert s["keys"] == []
    assert s["base_urls"] == [{"provider": "openrouter", "url": "https://openrouter.ai/api/v1"}]   # its default
    assert {p["provider"] for p in s["providers"]} == {"anthropic", "openai", "deepseek", "gemini",
                                                      "local", "openrouter"}


def test_status_subscription(wdg_home):
    write_creds(wdg_home, "subscription")
    s = call("auth.status")
    assert s["claude"] == {"mode": "subscription", "logged_in": True, "reason": None, "env_key_set": False,
                           "key_masked": None, "key_source": None}
    assert all(x["ready"] and x["billing"] == "subscription" for x in s["stages"])


def test_status_subscription_without_a_login_or_with_an_env_key(wdg_home, monkeypatch):
    write_creds(wdg_home, "subscription")
    monkeypatch.setattr("watchdog.cmd.auth.claude_code_logged_in", lambda: False)
    c = call("auth.status")["claude"]
    assert c["logged_in"] is False and "login not detected" in c["reason"]
    monkeypatch.setattr("watchdog.cmd.auth.claude_code_logged_in", lambda: True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-envenvenvenv1234")
    c = call("auth.status")["claude"]
    assert c["env_key_set"] is True and "metered" in c["reason"]


def test_status_api_key_mode_masks_and_never_returns_a_key(wdg_home):
    write_creds(wdg_home, "api-key", {"anthropic": "sk-ant-abcdefghijklmnop", "openai": "sk-openai-secret-123456"})
    s = call("auth.status")
    assert s["claude"]["mode"] == "api-key" and s["claude"]["logged_in"] is True
    assert s["claude"]["key_masked"] == "sk-ant-abc…mnop" and s["claude"]["key_source"] == "stored"
    assert s["stages"][0]["billing"] == "api-key"
    assert {k["provider"]: k for k in s["keys"]} == {
        "anthropic": {"provider": "anthropic", "masked": "sk-ant-abc…mnop", "in_use": "in use",
                      "detail": "in use", "source": "stored"},
        "openai": {"provider": "openai", "masked": "sk-openai-…3456", "in_use": "unused",
                   "detail": "unused", "source": "stored"}}
    text = json.dumps(s)
    assert "abcdefghijklmnop" not in text and "secret-123456" not in text


def test_status_inactive_anthropic_key_and_routed_providers(wdg_home, monkeypatch):
    write_creds(wdg_home, "subscription", {"anthropic": "sk-ant-abcdefghijklmnop"})
    (wdg_home / "config.json").write_text(json.dumps({"extractor_model": "openai:gpt-5-mini",
                                                      "local_base_url": "http://localhost:11434/v1"}))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env-1234567890")
    s = call("auth.status")
    keys = {k["provider"]: k for k in s["keys"]}
    assert keys["anthropic"]["in_use"] == "inactive" and keys["anthropic"]["detail"] == "inactive — subscription mode"
    assert keys["openai"]["in_use"] == "in use" and keys["openai"]["source"] == "env"
    ext = s["stages"][1]
    assert ext["provider"] == "openai" and ext["ready"] is True and ext["billing"] is None
    assert {"provider": "local", "url": "http://localhost:11434/v1"} in s["base_urls"]
    providers = {p["provider"]: p for p in s["providers"]}
    assert providers["local"]["base_url"] == "http://localhost:11434/v1" and providers["local"]["requires_key"] is False
    assert providers["local"]["ready"] is True and providers["openrouter"]["ready"] is False


# ── auth mutations ───────────────────────────────────────────────────────────────

def test_set_anthropic_mode_subscription_tunes_concurrency(wdg_home):
    r = call("auth.setAnthropicMode", mode="subscription")
    assert r["claude"]["mode"] == "subscription" and r["warning"] is None
    assert creds(wdg_home)["mode"] == "subscription" and mode_bits(wdg_home / "credentials.json") == 0o600
    assert config(wdg_home)["extract_concurrency"] == 3
    # A value the user chose is left alone.
    (wdg_home / "config.json").write_text(json.dumps({"extract_concurrency": 9}))
    call("auth.setAnthropicMode", mode="subscription")
    assert config(wdg_home)["extract_concurrency"] == 9


def test_set_anthropic_mode_api_key_stores_the_key_and_restores_concurrency(wdg_home):
    call("auth.setAnthropicMode", mode="subscription")
    r = call("auth.setAnthropicMode", mode="api-key", key="  sk-ant-abcdefghijklmnop ")
    assert r["claude"]["mode"] == "api-key" and r["claude"]["key_masked"] == "sk-ant-abc…mnop"
    assert creds(wdg_home) == {"mode": "api-key", "keys": {"anthropic": "sk-ant-abcdefghijklmnop"}}
    assert "extract_concurrency" not in config(wdg_home)
    assert "abcdefghijklmnop" not in json.dumps(r)


def test_set_anthropic_mode_without_a_key_reports_it(wdg_home):
    r = call("auth.setAnthropicMode", mode="api-key")
    assert r["claude"]["mode"] == "api-key" and r["claude"]["logged_in"] is False
    assert "no key" in r["claude"]["reason"]


def test_set_anthropic_mode_warns_on_an_odd_key_but_stores_it(wdg_home):
    r = call("auth.setAnthropicMode", mode="api-key", key="not-an-anthropic-key")
    assert "sk-ant-" in r["warning"] and creds(wdg_home)["keys"]["anthropic"] == "not-an-anthropic-key"


def test_set_anthropic_mode_validation(wdg_home):
    assert call_error("auth.setAnthropicMode", mode="free")["code"] == "bad_params"
    assert not (wdg_home / "credentials.json").exists()


def test_set_key_and_delete_key(wdg_home):
    r = call("auth.setKey", provider="openai", key=" sk-openai-abcdef123456 ")
    assert {k["provider"]: k["masked"] for k in r["keys"]} == {"openai": "sk-openai-…3456"}
    assert creds(wdg_home)["keys"] == {"openai": "sk-openai-abcdef123456"}
    assert mode_bits(wdg_home / "credentials.json") == 0o600
    assert r["warning"] is None
    assert "sk-" in call("auth.setKey", provider="openai", key="odd")["warning"]
    assert call("auth.setKey", provider="gemini", key="any-format-works")["warning"] is None
    r = call("auth.deleteKey", provider="openai")
    assert [k["provider"] for k in r["keys"]] == ["gemini"] and "openai" not in creds(wdg_home)["keys"]
    call("auth.deleteKey", provider="openai")          # deleting what isn't there is a no-op
    assert [k["provider"] for k in call("auth.status")["keys"]] == ["gemini"]


def test_set_key_does_not_touch_the_mode(wdg_home):
    write_creds(wdg_home, "subscription")
    call("auth.setKey", provider="deepseek", key="sk-deepseek-1234567890")
    assert creds(wdg_home)["mode"] == "subscription"


def test_key_validation(wdg_home):
    assert call_error("auth.setKey", provider="skynet", key="x")["code"] == "bad_params"
    assert call_error("auth.setKey", provider="openai", key="   ")["code"] == "bad_params"
    assert call_error("auth.deleteKey", provider="skynet")["code"] == "bad_params"
    assert not (wdg_home / "credentials.json").exists()


def test_set_base_url(wdg_home):
    r = call("auth.setBaseUrl", provider="local", url="http://localhost:11434/v1/")
    assert config(wdg_home)["local_base_url"] == "http://localhost:11434/v1"
    assert {"provider": "local", "url": "http://localhost:11434/v1"} in r["base_urls"]
    assert {p["provider"]: p["ready"] for p in r["providers"]}["local"] is True
    r = call("auth.setBaseUrl", provider="local", url="")
    assert "local_base_url" not in config(wdg_home)
    assert [b["provider"] for b in r["base_urls"]] == ["openrouter"]
    assert mode_bits(wdg_home / "config.json") == 0o600


def test_set_base_url_keeps_other_settings_and_validates(wdg_home):
    (wdg_home / "config.json").write_text(json.dumps({"extractor_model": "opus"}))
    call("auth.setBaseUrl", provider="openrouter", url="https://openrouter.example/api/v1")
    assert config(wdg_home) == {"extractor_model": "opus",
                                "openrouter_base_url": "https://openrouter.example/api/v1"}
    for bad in ("not a url", "ftp://host/x", "localhost:11434"):
        assert call_error("auth.setBaseUrl", provider="local", url=bad)["code"] == "bad_params"
    assert call_error("auth.setBaseUrl", provider="openai", url="https://x")["code"] == "bad_params"
    assert call_error("auth.setBaseUrl", provider="skynet", url="https://x")["code"] == "bad_params"


def test_auth_status_after_a_corrupt_credentials_file(wdg_home):
    (wdg_home / "credentials.json").write_text("{nope")
    err = call_error("auth.status")
    assert "corrupt" in err["message"] and not err["message"].startswith("Error")


# ── skills ───────────────────────────────────────────────────────────────────────

def test_skills_list_package_and_user(wdg_home):
    user_dir = skills_catalog.USER_SKILLS_DIR
    user_dir.mkdir(parents=True)
    (user_dir / "my-skill.md").write_text("---\ndescription: My own records\n---\n# Mine\n")
    (user_dir / "general-records.md").write_text("# Override\n\nAn override of the bundled skill.\n")
    r = call("skills.list")
    assert r["user_dir"] == str(user_dir)
    by_name = {s["name"]: s for s in r["skills"]}
    assert by_name["my-skill"] == {"name": "my-skill", "description": "My own records", "source": "user"}
    assert by_name["general-records"]["source"] == "user"          # a user skill overrides the bundled one
    assert by_name["general-records"]["description"] == "An override of the bundled skill"
    package = [s for s in r["skills"] if s["source"] == "package"]
    assert package and all(s["description"] for s in package)
    assert "_template" not in by_name
    assert [s["name"] for s in r["skills"]] == sorted(by_name)


def test_skills_read(wdg_home):
    r = call("skills.read", name="general-records")
    assert r["name"] == "general-records" and len(r["text"]) > 100
    assert call("skills.read", name="general-records.md")["name"] == "general-records"
    for bad in ("nope", "../../etc/passwd", "", "_template"):
        assert call_error("skills.read", name=bad)["code"] == "not_found"


# ── setup.check ──────────────────────────────────────────────────────────────────

def test_setup_check(wdg_home, monkeypatch, tmp_path):
    from watchdog import setup_cmd
    from watchdog.pipeline import capture
    present = {"qpdf"}
    monkeypatch.setattr("shutil.which", lambda b: f"/usr/bin/{b}" if b in present else None)
    monkeypatch.setattr(capture, "render_available", lambda: True)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    r = call("setup.check")
    assert [d["label"] for d in r["deps"]] == [d[1] for d in setup_cmd._DEPS]
    qpdf = r["deps"][0]
    assert qpdf == {"label": "qpdf", "ok": True, "hint": None}
    gs = r["deps"][1]
    assert gs["ok"] is False and "ghostscript" in gs["hint"]
    assert r["playwright"] is True and r["gliner_model"] is False
    assert r["config_exists"] is False and r["projects_dir"] is None


def test_setup_check_reads_config_and_the_model_cache(wdg_home, monkeypatch, tmp_path):
    (wdg_home / "config.json").write_text(json.dumps({"projects_dir": "/cases"}))
    hub = tmp_path / "hf" / "hub" / "models--urchade--gliner_multi-v2.1"
    hub.mkdir(parents=True)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    from watchdog.pipeline import capture
    monkeypatch.setattr(capture, "render_available", lambda: (_ for _ in ()).throw(RuntimeError("no browser")))
    r = call("setup.check")
    assert r["config_exists"] is True and r["projects_dir"] == "/cases"
    assert r["gliner_model"] is True and r["playwright"] is False      # a failing probe reads as absent
