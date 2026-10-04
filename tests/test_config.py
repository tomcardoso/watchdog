import json

from watchdog import config


def test_read_is_lenient(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", cfg)
    assert config.read() == {}                      # missing
    cfg.write_text("{not json")
    assert config.read() == {}                      # corrupt
    cfg.write_text("[1, 2]")
    assert config.read() == {}                      # not an object
    cfg.write_text(json.dumps({"dup_threshold": 0.9}))
    assert config.get("dup_threshold", 0.85) == 0.9
    assert config.get("missing", "fallback") == "fallback"


def test_default_path_follows_home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_FILE", None)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config.path() == tmp_path / ".watchdog" / "config.json"
