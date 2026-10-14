"""App-level methods: liveness and environment facts the app shows on its first screen."""

from __future__ import annotations

import platform
import shutil
import sys

from watchdog.gui.rpc import method


@method("app.ping")
def ping() -> dict:
    return {"ok": True}


@method("app.info")
def info() -> dict:
    from watchdog import config, obsidian
    from watchdog.vault import pkg_version
    try:
        from watchdog.auth import claude_code_logged_in
        logged_in = claude_code_logged_in()
    except Exception:  # noqa: BLE001
        logged_in = False
    cfg = config.load()
    return {
        "version": pkg_version(),
        "python": sys.executable,
        "python_version": platform.python_version(),
        "platform": sys.platform,
        "watchdog_home": config.home(),
        "config_file": config.path(),
        # First-run setup writes the settings file, so its presence means setup is done.
        "setup_complete": config.path().exists(),
        "projects_dir": cfg.get("projects_dir"),
        "claude_code": {"installed": shutil.which("claude") is not None, "logged_in": logged_in},
        "obsidian_installed": obsidian.installed(),
        "engine_ready": engine_ready()["ready"],
    }


@method("engine.ready")
def engine_ready() -> dict:
    """Whether the app's engine has finished installing (D272). Until it has, `jobs.start` and
    `action.run` refuse commands that add documents (`engine_not_ready`)."""
    from watchdog.gui import engine_setup
    return {"ready": engine_setup.engine_ready()}


@method("engine.setReady")
def engine_set_ready() -> dict:
    """Called by the app when the engine's background phase finishes. The libraries it installed
    were only added beside the ones this process has loaded (both phases install from one resolved
    list, so nothing loaded is replaced), so the backend keeps running: it forgets the pending flag,
    for itself and every command it starts, and drops the import system's directory caches so the
    new packages can be imported."""
    import importlib
    import os

    from watchdog.gui import engine_setup
    os.environ.pop(engine_setup.PENDING_ENV, None)
    importlib.invalidate_caches()
    return {"ready": engine_setup.engine_ready()}
