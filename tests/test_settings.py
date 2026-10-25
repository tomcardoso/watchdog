"""The settings schema (`watchdog.settings`): validation of a new value, and how Settings shows
one."""

import pytest

from watchdog import settings


@pytest.mark.parametrize("key, value, stored", [
    ("garbled_threshold", "0.6", 0.6),
    ("chunk_size", "20", 20),                         # stored as a number, not "20"
    ("chunk_workers", "auto", "auto"),
    ("chunk_workers", "4", 4),
    ("chew_workers", "auto", "auto"),
    ("extract_token_budget", "auto", "auto"),
    ("extract_token_budget", "150000", 150000),
    ("section_token_threshold", "auto", "auto"),
    ("table_structure", "false", False),
    ("table_structure", "on", True),
    ("table_structure", "1", True),
    ("auto_approve", "true", True),
    ("ocr_engine", "tesseract", "tesseract"),
    ("extractor_effort", "medium", "medium"),
    ("ocr_languages", "en-US, fr-FR", ["en-US", "fr-FR"]),
    ("reporter_name", "  Jane   Doe  ", "Jane Doe"),
])
def test_coerce_stores_a_typed_value(key, value, stored):
    cfg = {}
    settings.coerce(cfg, key, value)
    assert cfg == {key: stored}


@pytest.mark.parametrize("key, value, message", [
    ("garbled_threshold", "not-a-number", "must be a number"),
    ("garbled_threshold", "1.5", "must be <= 1.0"),
    ("dup_threshold", "-0.1", "must be >="),
    ("chunk_size", "3.5", "whole number"),
    ("chunk_workers", "0", "must be >= 1"),
    ("chunk_workers", "fast", "'auto' or a whole number"),
    ("extract_token_budget", "lots", "'auto' or a whole number"),
    ("extract_token_budget", "0", "must be >="),
    ("table_structure", "maybe", "true or false"),
    ("ocr_engine", "badengine", "must be one of"),
    ("finalizer_effort", "ludicrous", "must be one of"),
])
def test_coerce_rejects_a_bad_value(key, value, message):
    with pytest.raises(settings.SettingError, match=message):
        settings.coerce({}, key, value)


@pytest.mark.parametrize("value", ["inf", "-inf", "nan", "Infinity"])
def test_coerce_rejects_non_finite_numbers(value):
    with pytest.raises(settings.SettingError, match="finite"):
        settings.coerce({}, "dup_threshold", value)


def test_apple_vision_is_refused_off_macos(monkeypatch):
    monkeypatch.setattr(settings.sys, "platform", "linux")
    with pytest.raises(settings.SettingError, match="macOS"):
        settings.coerce({}, "ocr_engine", "apple_vision")


def test_projects_dir_is_created(tmp_path):
    cfg = {}
    settings.coerce(cfg, "projects_dir", str(tmp_path / "cases"))
    assert (tmp_path / "cases").is_dir() and cfg["projects_dir"] == str((tmp_path / "cases").resolve())


def test_every_key_is_in_one_section():
    listed = [k for _t, _b, keys in settings.SECTIONS for k in keys]
    assert sorted(listed) == sorted(settings.SETTINGS) and len(listed) == len(set(listed))


# ── how a value is shown ─────────────────────────────────────────────────────────

def test_section_auto_shows_the_resolved_number():
    # 93750 is sonnet's 120K window fraction over its 1.28 tokenizer_ratio (#617).
    assert settings.display_value("section_token_threshold", "auto", {"extractor_model": "sonnet"}) \
        == "auto (93750 — sonnet)"
    assert settings.display_value("section_token_threshold", None, {"extractor_model": "sonnet"}) \
        == "auto (93750 — sonnet)"
    assert settings.display_value("section_token_budget", None, {"extractor_model": "sonnet"}) \
        == "auto (46875 — sonnet)"


def test_section_auto_resolves_the_default_extractor_when_unset():
    real_default = settings.SETTINGS["extractor_model"]["default"]
    assert real_default in settings.display_value("section_token_threshold", "auto", {})


def test_section_auto_names_a_non_claude_backend_only():
    # A Claude backend prefix is inert for the number; a non-Claude one selects the model id.
    assert settings.display_value("section_token_threshold", "auto",
                                  {"extractor_model": "claude-api:sonnet"}) == "auto (93750 — sonnet)"
    assert settings.display_value("section_token_threshold", "auto",
                                  {"extractor_model": "openai:gpt-5-mini"}) == "auto (240000 — openai:gpt-5-mini)"
    assert settings.display_value("section_token_threshold", "auto",
                                  {"extractor_model": "gemini:gemini-3.8-flash"}) \
        == "auto (659340 — gemini:gemini-3.8-flash)"


def test_section_auto_ignores_extractor_effort():
    # #555: sectioning is sized from the context window and tokenizer ratio alone.
    outs = {settings.display_value("section_token_threshold", "auto",
                                   {"extractor_model": "openai:gpt-5-mini", "extractor_effort": e})
            for e in ("low", "medium", "high", None)}
    assert outs == {"auto (240000 — openai:gpt-5-mini)"}


def test_display_of_defaults_booleans_and_secrets():
    assert settings.display_value("garbled_threshold", None) == "0.6"
    assert settings.display_value("table_structure", False) == "false"
    assert settings.display_value("ocr_languages", None) == "auto-detect (default)"
    assert settings.display_value("wayback_secret_key", "hunter2") == "•••••••• (set)"
    assert settings.display_value("finalizer_writing_model", None) == "(not set)"


def test_defaults_shown_match_the_runtime_defaults():
    """Settings shows each key's default from `SETTINGS`; the runtime reads `watchdog.defaults`.
    They once disagreed (extract_concurrency: shown 5, used 20)."""
    from watchdog import defaults
    from watchdog.ops.ingest import _DEFAULT_EXTRACT_CONCURRENCY
    s = settings.SETTINGS
    assert s["extract_concurrency"]["default"] == _DEFAULT_EXTRACT_CONCURRENCY == 20
    assert s["classify_pages"]["default"] == defaults.CLASSIFY_PAGES
    assert s["extractor_model"]["default"] == defaults.EXTRACTOR_MODEL
    assert s["classifier_model"]["default"] == defaults.CLASSIFIER_MODEL
    assert s["finalizer_model"]["default"] == defaults.FINALIZER_MODEL
    assert s["extractor_effort"]["default"] == defaults.EXTRACTOR_EFFORT
    assert s["classifier_effort"]["default"] == defaults.CLASSIFIER_EFFORT


def test_model_keys_are_free_form_and_default_skill_is_a_setting():
    """A stage's model is a free-form `[backend:]model` string, not an enum."""
    assert settings.SETTINGS["classifier_model"]["type"] == "string"
    assert "default_skill" in settings.SETTINGS
    cfg = {}
    settings.coerce(cfg, "default_skill", "court-documents")
    assert cfg == {"default_skill": "court-documents"}


def test_ocr_languages_are_shown_as_set():
    cfg = {}
    settings.coerce(cfg, "ocr_languages", "en-US,fr-FR")
    shown = settings.display_value("ocr_languages", cfg["ocr_languages"])
    assert "en-US" in shown and "fr-FR" in shown


def test_a_saved_settings_file_is_tightened_to_owner_only(tmp_path, monkeypatch):
    """A settings file written loosely (0644) is tightened on the next save: it can hold the
    Wayback Machine secret key (#304)."""
    import os
    import stat
    import sys
    from watchdog import config
    monkeypatch.setattr(config, "HOME", tmp_path / "wd")
    config.path().parent.mkdir(parents=True)
    config.path().write_text("{}\n")
    config.path().chmod(0o644)
    config.save({"projects_dir": "/x"})
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(config.path()).st_mode) == 0o600
