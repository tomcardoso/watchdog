"""Setup asks whether small runs may skip the public-records pause, recommending $5 (D255)."""

import json

import pytest

import watchdog.setup_cmd as sc


@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(sc.sys.stdin, "isatty", lambda: True)


def _answers(monkeypatch, confirm, *lines):
    monkeypatch.setattr(sc.interactive, "confirm", lambda *a, **k: confirm)
    queue = list(lines)
    monkeypatch.setattr("builtins.input", lambda prompt="": queue.pop(0))


def test_declining_turns_it_off(tty, monkeypatch):
    _answers(monkeypatch, False)
    assert sc._ask_auto_approve() is None


def test_enter_takes_the_recommended_five_dollars(tty, monkeypatch):
    prompts = []
    monkeypatch.setattr(sc.interactive, "confirm", lambda *a, **k: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "")
    assert sc._ask_auto_approve() == 5.0
    assert "Enter for $5, recommended" in prompts[0]


@pytest.mark.parametrize("typed, expected", [("10", 10.0), ("$2.50", 2.5)])
def test_a_typed_limit(tty, monkeypatch, typed, expected):
    _answers(monkeypatch, True, typed)
    assert sc._ask_auto_approve() == expected


def test_invalid_amounts_ask_again(tty, monkeypatch, capsys):
    _answers(monkeypatch, True, "five", "0", "-3", "inf", "nan", "3")
    assert sc._ask_auto_approve() == 3.0
    assert capsys.readouterr().out.count("above zero") == 5


def test_rerun_offers_the_current_limit(tty, monkeypatch):
    seen = {}
    monkeypatch.setattr(sc.interactive, "confirm", lambda p, default: seen.setdefault("d", default))
    monkeypatch.setattr("builtins.input", lambda prompt="": seen.setdefault("p", prompt) and "")
    assert sc._ask_auto_approve(current=12.0) == 12.0
    assert seen["d"] is True and "$12" in seen["p"] and "recommended" not in seen["p"]


def test_off_a_terminal_it_keeps_the_current_value(monkeypatch):
    monkeypatch.setattr(sc.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sc.interactive, "confirm", lambda *a, **k: pytest.fail("prompted"))
    assert sc._ask_auto_approve() is None
    assert sc._ask_auto_approve(current=7.0) == 7.0


def _run_setup(tmp_path, monkeypatch, existing: dict, limit):
    home = tmp_path / ".watchdog"
    home.mkdir()
    monkeypatch.setattr(sc, "WATCHDOG_HOME", home)
    monkeypatch.setattr(sc, "CONFIG_FILE", home / "config.json")
    if existing:
        (home / "config.json").write_text(json.dumps(existing))
    monkeypatch.setattr(sc, "_check_deps", lambda: [])
    monkeypatch.setattr(sc, "_ask_projects_dir", lambda: tmp_path)
    monkeypatch.setattr(sc, "_detect_shell", lambda: (None, None))
    monkeypatch.setattr(sc, "_check_playwright", lambda: None)
    monkeypatch.setattr("watchdog.cmd.auth.setup_auth_interactive", lambda: None)
    seen = []
    monkeypatch.setattr(sc, "_ask_auto_approve", lambda current=None: seen.append(current) or limit)
    sc.run(force=True)
    return json.loads((home / "config.json").read_text()), seen


def test_setup_saves_the_limit(tmp_path, monkeypatch):
    config, seen = _run_setup(tmp_path, monkeypatch, {}, 5.0)
    assert config["auto_approve_usd"] == 5.0 and seen == [None]


def test_setup_rerun_can_turn_it_off(tmp_path, monkeypatch):
    config, seen = _run_setup(tmp_path, monkeypatch, {"auto_approve_usd": 8}, None)
    assert "auto_approve_usd" not in config and seen == [8.0]


def test_setup_off_a_terminal_keeps_an_existing_limit(tmp_path, monkeypatch):
    home = tmp_path / ".watchdog"
    home.mkdir()
    monkeypatch.setattr(sc, "WATCHDOG_HOME", home)
    monkeypatch.setattr(sc, "CONFIG_FILE", home / "config.json")
    (home / "config.json").write_text(json.dumps({"auto_approve_usd": 4}))
    monkeypatch.setattr(sc, "_check_deps", lambda: [])
    monkeypatch.setattr(sc, "_ask_projects_dir", lambda: tmp_path)
    monkeypatch.setattr(sc, "_detect_shell", lambda: (None, None))
    monkeypatch.setattr(sc, "_check_playwright", lambda: None)
    monkeypatch.setattr("watchdog.cmd.auth.setup_auth_interactive", lambda: None)
    monkeypatch.setattr(sc.sys.stdin, "isatty", lambda: False)
    sc.run(force=True)
    assert json.loads((home / "config.json").read_text())["auto_approve_usd"] == 4.0


@pytest.mark.parametrize("value", ["inf", "-inf", "nan", "Infinity"])
def test_configure_rejects_non_finite_numbers(value):
    from watchdog.cmd.setup import _ConfigError, _coerce_value
    with pytest.raises(_ConfigError, match="finite"):
        _coerce_value({}, "auto_approve_usd", value)
