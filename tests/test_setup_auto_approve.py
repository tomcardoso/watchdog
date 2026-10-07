"""Setup asks whether runs entirely on the Claude subscription may skip the public-records pause,
defaulting to no (D263)."""

import json

import pytest

import watchdog.setup_cmd as sc


@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(sc.sys.stdin, "isatty", lambda: True)


@pytest.mark.parametrize("answer", [True, False])
def test_the_answer_is_returned(tty, monkeypatch, answer):
    monkeypatch.setattr(sc.interactive, "confirm", lambda *a, **k: answer)
    assert sc._ask_auto_approve() is answer


@pytest.mark.parametrize("current", [False, True])
def test_the_default_is_the_current_value(tty, monkeypatch, current):
    seen = {}
    monkeypatch.setattr(sc.interactive, "confirm", lambda p, default: seen.setdefault("d", default))
    sc._ask_auto_approve(current=current)
    assert seen["d"] is current


def test_the_question_says_a_paid_key_always_asks(tty, monkeypatch, capsys):
    monkeypatch.setattr(sc.interactive, "confirm", lambda *a, **k: False)
    sc._ask_auto_approve()
    out = capsys.readouterr().out
    assert "subscription" in out and "paid" in out and "$" not in out


def test_off_a_terminal_it_keeps_the_current_value(monkeypatch):
    monkeypatch.setattr(sc.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sc.interactive, "confirm", lambda *a, **k: pytest.fail("prompted"))
    assert sc._ask_auto_approve() is False
    assert sc._ask_auto_approve(current=True) is True


def _run_setup(tmp_path, monkeypatch, existing: dict, answer=None):
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
    if answer is None:
        monkeypatch.setattr(sc.sys.stdin, "isatty", lambda: False)
    else:
        monkeypatch.setattr(sc, "_ask_auto_approve", lambda current=False: seen.append(current) or answer)
    sc.run(force=True)
    return json.loads((home / "config.json").read_text()), seen


def test_setup_saves_yes(tmp_path, monkeypatch):
    config, seen = _run_setup(tmp_path, monkeypatch, {}, True)
    assert config["auto_approve"] is True and seen == [False]


def test_setup_rerun_can_turn_it_off(tmp_path, monkeypatch):
    config, seen = _run_setup(tmp_path, monkeypatch, {"auto_approve": True}, False)
    assert "auto_approve" not in config and seen == [True]


def test_setup_off_a_terminal_keeps_it_on(tmp_path, monkeypatch):
    config, _ = _run_setup(tmp_path, monkeypatch, {"auto_approve": True})
    assert config["auto_approve"] is True


def test_settings_stores_a_real_boolean():
    from watchdog.cmd.setup import _coerce_value
    config = {}
    _coerce_value(config, "auto_approve", "true")
    assert config == {"auto_approve": True}


@pytest.mark.parametrize("value", ["inf", "-inf", "nan", "Infinity"])
def test_configure_rejects_non_finite_numbers(value):
    from watchdog.cmd.setup import _ConfigError, _coerce_value
    with pytest.raises(_ConfigError, match="finite"):
        _coerce_value({}, "dup_threshold", value)
