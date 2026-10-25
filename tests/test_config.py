import json

import pytest

from watchdog import config


def test_read_is_lenient(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME", tmp_path)
    cfg = tmp_path / "config.json"
    assert config.read() == {}                      # missing
    cfg.write_text("{not json")
    assert config.read() == {}                      # corrupt
    cfg.write_text("[1, 2]")
    assert config.read() == {}                      # not an object
    cfg.write_text(json.dumps({"dup_threshold": 0.9}))
    assert config.get("dup_threshold", 0.85) == 0.9
    assert config.get("missing", "fallback") == "fallback"


def test_load_is_strict(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME", tmp_path)
    assert config.load() == {}                      # missing: nothing set yet
    (tmp_path / "config.json").write_text("{not json")
    with pytest.raises(SystemExit, match="settings file is damaged"):
        config.load()


def test_save_writes_an_owner_only_file(tmp_path, monkeypatch):
    import os
    import stat
    import sys
    monkeypatch.setattr(config, "HOME", tmp_path / "wd")
    config.save({"projects_dir": "/x"})
    assert config.read() == {"projects_dir": "/x"}
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(config.path()).st_mode) == 0o600


def test_default_home_follows_home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME", None)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config.path() == tmp_path / ".watchdog" / "config.json"


def test_old_finishing_stage_overrides_read_as_their_group(tmp_path, monkeypatch):
    """D339 replaced four per-stage overrides with writing and housekeeping: an old key reads as
    its group's (the first set one, in the documented order), unless the group is already set."""
    monkeypatch.setattr(config, "HOME", tmp_path)
    config.path().write_text(json.dumps({
        "finalizer_briefing_model": "sonnet", "finalizer_timeline_model": "openai:gpt-6-luna",
        "finalizer_reconciliation_model": "haiku", "finalizer_model": "haiku"}))
    for data in (config.load(), config.read()):
        assert data == {"finalizer_model": "haiku", "finalizer_writing_model": "sonnet",
                        "finalizer_housekeeping_model": "haiku"}
    config.path().write_text(json.dumps({"finalizer_writing_model": "gemini:gemini-3.8-flash",
                                         "finalizer_synthesis_model": "sonnet"}))
    assert config.load() == {"finalizer_writing_model": "gemini:gemini-3.8-flash"}
