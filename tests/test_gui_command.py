"""`watchdog gui`: finds and opens the desktop app."""

import os
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from watchdog.cmd import gui, groups


def _run():
    gui.cmd_gui(SimpleNamespace())


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("WATCHDOG_APP", raising=False)


def test_registered_as_a_maintenance_command():
    from watchdog.cli import _subparsers, build_parser
    assert "gui" in _subparsers(build_parser())
    assert "gui" in [c for c, _ in groups.MAINTENANCE]


@pytest.mark.skipif(os.name == "nt", reason="needs an executable bit")
def test_watchdog_app_is_launched(tmp_path, monkeypatch, capsys):
    app = tmp_path / "app"
    app.write_text("#!/bin/sh\n")
    app.chmod(app.stat().st_mode | stat.S_IXUSR)
    launched = []
    monkeypatch.setenv("WATCHDOG_APP", str(app))
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: launched.append(cmd))
    _run()
    assert launched == [[str(app)]] and "Opening Watchdog" in capsys.readouterr().out


def test_watchdog_app_must_be_executable(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHDOG_APP", str(tmp_path / "missing"))
    with pytest.raises(SystemExit) as e:
        _run()
    assert "WATCHDOG_APP" in str(e.value)


def test_macos_opens_the_installed_app(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd) or SimpleNamespace(returncode=0))
    _run()
    assert calls == [["open", "-a", "Watchdog"]]
    assert "Opening Watchdog" in capsys.readouterr().out


def test_dev_checkout_runs_the_dev_server(tmp_path, monkeypatch):
    (tmp_path / "gui" / "node_modules").mkdir(parents=True)
    (tmp_path / "gui" / "package.json").write_text("{}")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(gui, "_repo_root", lambda: tmp_path)
    seen = {}

    def fake_call(cmd, cwd, env):
        seen.update(cmd=cmd, cwd=cwd, env=env)
        return 0
    monkeypatch.setattr(subprocess, "call", fake_call)
    _run()
    assert seen["cmd"] == ["npm", "run", "dev"] and seen["cwd"] == str(tmp_path / "gui")
    assert seen["env"]["WATCHDOG_PYTHON"] == sys.executable
    assert seen["env"]["WATCHDOG_SRC"] == str(tmp_path / "src")


def test_repo_root_is_detected_for_this_checkout():
    root = gui._repo_root()
    assert root is None or (root / "src" / "watchdog").is_dir()


def test_otherwise_explains_where_to_get_the_app(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(gui, "_repo_root", lambda: None)
    with pytest.raises(SystemExit) as e:
        _run()
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert "not installed" in out and "docs/app.md" in out and "WATCHDOG_APP" in out
