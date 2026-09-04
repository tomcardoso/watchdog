"""Tests for the Playwright/Chromium check in `watchdog setup` (#246)."""

from pathlib import Path

import watchdog.cmd.base as base
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
    monkeypatch.setattr(sc.sys, "executable", "/Users/x/.local/pipx/venvs/watchdog-intel/bin/python")

    def _boom(*a, **k):
        raise AssertionError("should not install when declined")
    monkeypatch.setattr(sc.subprocess, "run", _boom)

    sc._check_playwright()
    out = capsys.readouterr().out
    assert "Skipped" in out
    assert "pipx inject watchdog-intel playwright" in out
    assert "playwright install chromium" in out


def test_playwright_missing_declined_uv_install_prints_uv_hint(monkeypatch, capsys):
    """Under a `uv tool install` install, the fallback hint should tell the user the `uv`
    equivalent of `pipx inject`, not a pipx command that wouldn't work for them (#610)."""
    monkeypatch.setattr("watchdog.pipeline.capture.render_available", lambda: False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    monkeypatch.setattr(sc.sys, "executable", "/Users/x/.local/share/uv/tools/watchdog-intel/bin/python")

    def _boom(*a, **k):
        raise AssertionError("should not install when declined")
    monkeypatch.setattr(sc.subprocess, "run", _boom)

    sc._check_playwright()
    out = capsys.readouterr().out
    assert "Skipped" in out
    assert "uv tool install watchdog-intel --with playwright" in out
    assert "pipx" not in out
    assert "playwright install chromium" in out


def test_playwright_missing_accepted_installs_both(monkeypatch, capsys):
    monkeypatch.setattr("watchdog.pipeline.capture.render_available", lambda: False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    monkeypatch.setattr(sc, "_node_ca_bundle", lambda: None)

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


def test_playwright_install_sets_node_extra_ca_certs_when_missing(monkeypatch):
    """Playwright's chromium download shells out to a bundled Node driver, which never consults
    the OS trust store — under a TLS-inspecting corporate proxy (Netskope), the download fails
    with SELF_SIGNED_CERT_IN_CHAIN even though the OS trusts the proxy's root CA. `NODE_EXTRA_CA_
    CERTS` is Node's own escape hatch for this."""
    monkeypatch.setattr("watchdog.pipeline.capture.render_available", lambda: False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    monkeypatch.delenv("NODE_EXTRA_CA_CERTS", raising=False)
    monkeypatch.setattr(sc, "_node_ca_bundle", lambda: "/fake/ca-bundle.pem")

    envs = []

    class _Result:
        def __init__(self, returncode):
            self.returncode = returncode
            self.stderr = ""

    def _fake_run(cmd, **kwargs):
        envs.append(kwargs.get("env"))
        return _Result(0)

    monkeypatch.setattr(sc.subprocess, "run", _fake_run)

    sc._check_playwright()
    assert envs[1]["NODE_EXTRA_CA_CERTS"] == "/fake/ca-bundle.pem"


def test_playwright_install_respects_existing_node_extra_ca_certs(monkeypatch):
    """If the user has already set NODE_EXTRA_CA_CERTS themselves, don't overwrite it — and
    don't even bother building a bundle."""
    monkeypatch.setattr("watchdog.pipeline.capture.render_available", lambda: False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    monkeypatch.setenv("NODE_EXTRA_CA_CERTS", "/already/set.pem")

    def _boom():
        raise AssertionError("should not build a bundle when NODE_EXTRA_CA_CERTS is already set")
    monkeypatch.setattr(sc, "_node_ca_bundle", _boom)

    envs = []

    class _Result:
        def __init__(self, returncode):
            self.returncode = returncode
            self.stderr = ""

    def _fake_run(cmd, **kwargs):
        envs.append(kwargs.get("env"))
        return _Result(0)

    monkeypatch.setattr(sc.subprocess, "run", _fake_run)

    sc._check_playwright()
    assert envs[1]["NODE_EXTRA_CA_CERTS"] == "/already/set.pem"


def test_node_ca_bundle_returns_none_on_non_darwin(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "linux")
    assert sc._node_ca_bundle() is None


def test_node_ca_bundle_combines_keychain_exports_and_default_cafile(monkeypatch, tmp_path):
    monkeypatch.setattr(sc.sys, "platform", "darwin")
    monkeypatch.setattr(sc, "WATCHDOG_HOME", tmp_path)

    class _Result:
        def __init__(self, stdout):
            self.returncode = 0
            self.stdout = stdout

    def _fake_run(cmd, **kwargs):
        keychain = cmd[-1]
        return _Result(f"-----BEGIN CERTIFICATE-----\n{keychain}\n-----END CERTIFICATE-----\n")
    monkeypatch.setattr(sc.subprocess, "run", _fake_run)

    default_cafile = tmp_path / "default-ca.pem"
    default_cafile.write_text("-----BEGIN CERTIFICATE-----\ndefault-root\n-----END CERTIFICATE-----\n")
    import ssl
    monkeypatch.setattr(ssl, "get_default_verify_paths",
                         lambda: type("P", (), {"cafile": str(default_cafile)})())

    bundle = sc._node_ca_bundle()
    assert bundle is not None
    content = Path(bundle).read_text()
    assert "System.keychain" in content
    assert "SystemRootCertificates.keychain" in content
    assert "default-root" in content


def test_node_ca_bundle_returns_none_when_nothing_found(monkeypatch, tmp_path):
    monkeypatch.setattr(sc.sys, "platform", "darwin")
    monkeypatch.setattr(sc, "WATCHDOG_HOME", tmp_path)

    class _Result:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(sc.subprocess, "run", lambda cmd, **kwargs: _Result())
    import ssl
    monkeypatch.setattr(ssl, "get_default_verify_paths",
                         lambda: type("P", (), {"cafile": str(tmp_path / "missing.pem")})())

    assert sc._node_ca_bundle() is None


def test_detected_install_manager_pipx(monkeypatch):
    monkeypatch.setattr(base.sys, "executable", "/Users/x/.local/pipx/venvs/watchdog-intel/bin/python")
    assert base._detected_install_manager() == "pipx"


def test_detected_install_manager_uv(monkeypatch):
    monkeypatch.setattr(base.sys, "executable", "/Users/x/.local/share/uv/tools/watchdog-intel/bin/python")
    assert base._detected_install_manager() == "uv"


def test_detected_install_manager_defaults_to_pipx(monkeypatch):
    """Neither pipx's nor uv tool's private-venv layout — e.g. a plain venv in CI — falls back
    to pipx, the long-documented default."""
    monkeypatch.setattr(base.sys, "executable", "/opt/hostedtoolcache/Python/3.12.0/x64/bin/python")
    assert base._detected_install_manager() == "pipx"


def test_extra_install_cmd_matches_detected_manager(monkeypatch):
    monkeypatch.setattr(base.sys, "executable", "/Users/x/.local/pipx/venvs/watchdog-intel/bin/python")
    assert base._extra_install_cmd("gliner") == "pipx inject watchdog-intel gliner"

    monkeypatch.setattr(base.sys, "executable", "/Users/x/.local/share/uv/tools/watchdog-intel/bin/python")
    assert base._extra_install_cmd("gliner") == "uv tool install watchdog-intel --with gliner"


def test_venv_bin_prefers_sibling_of_executable_when_present(monkeypatch, tmp_path):
    fake_python = tmp_path / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.touch()
    (fake_python.parent / "playwright").touch()
    monkeypatch.setattr(base.sys, "executable", str(fake_python))
    assert base._venv_bin("playwright") == str(fake_python.parent / "playwright")


def test_venv_bin_falls_back_to_bare_name_when_absent(monkeypatch, tmp_path):
    fake_python = tmp_path / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.touch()
    monkeypatch.setattr(base.sys, "executable", str(fake_python))
    assert base._venv_bin("playwright") == "playwright"
