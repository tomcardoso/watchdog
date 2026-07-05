"""Tests for the Playwright/Chromium check in `watchdog setup` (#246)."""

import watchdog.setup_cmd as sc


def test_playwright_already_available_skips_prompt(monkeypatch, capsys):
    monkeypatch.setattr("watchdog.pipeline.capture.render_available", lambda: True)

    def _boom(*a):
        raise AssertionError("should not prompt when already available")
    monkeypatch.setattr("builtins.input", _boom)

    sc._check_playwright()
    out = capsys.readouterr().out
    assert "faithful web captures ready" in out


def test_playwright_missing_declined_prints_manual_hint(monkeypatch, capsys):
    monkeypatch.setattr("watchdog.pipeline.capture.render_available", lambda: False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    def _boom(*a, **k):
        raise AssertionError("should not install when declined")
    monkeypatch.setattr(sc.subprocess, "run", _boom)

    sc._check_playwright()
    out = capsys.readouterr().out
    assert "Skipped" in out
    assert "pipx inject watchdog-intel playwright" in out
    assert "playwright install chromium" in out


def test_playwright_missing_accepted_installs_both(monkeypatch, capsys):
    monkeypatch.setattr("watchdog.pipeline.capture.render_available", lambda: False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    calls = []

    class _Result:
        def __init__(self, returncode):
            self.returncode = returncode
            self.stderr = ""

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Result(0)

    monkeypatch.setattr(sc.subprocess, "run", _fake_run)

    sc._check_playwright()
    out = capsys.readouterr().out
    assert "Playwright + Chromium installed" in out
    assert calls[0][-1] == "playwright"        # pip install playwright
    assert calls[1][-2:] == ["install", "chromium"]  # playwright install chromium


def test_playwright_pip_install_failure_warns_and_stops(monkeypatch, capsys):
    monkeypatch.setattr("watchdog.pipeline.capture.render_available", lambda: False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    calls = []

    class _Result:
        def __init__(self, returncode, stderr=""):
            self.returncode = returncode
            self.stderr = stderr

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Result(1, "network unreachable")

    monkeypatch.setattr(sc.subprocess, "run", _fake_run)

    sc._check_playwright()
    out = capsys.readouterr().out
    assert "could not install playwright" in out
    assert len(calls) == 1  # never attempted the chromium download after pip failed
