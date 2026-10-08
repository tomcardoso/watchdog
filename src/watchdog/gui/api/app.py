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
    from watchdog.cmd.base import CONFIG_FILE, WATCHDOG_HOME, load_config
    from watchdog.cmd.setup import _pkg_version
    try:
        from watchdog.cmd.auth import claude_code_logged_in
        logged_in = claude_code_logged_in()
    except Exception:  # noqa: BLE001
        logged_in = False
    config = load_config()
    return {
        "version": _pkg_version(),
        "python": sys.executable,
        "python_version": platform.python_version(),
        "platform": sys.platform,
        "watchdog_home": WATCHDOG_HOME,
        "config_file": CONFIG_FILE,
        # `watchdog setup` treats an existing config file as "already set up" (setup_cmd.run).
        "setup_complete": CONFIG_FILE.exists(),
        "projects_dir": config.get("projects_dir"),
        "claude_code": {"installed": shutil.which("claude") is not None, "logged_in": logged_in},
        "obsidian_installed": _obsidian_installed(),
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


def _obsidian_installed() -> bool:
    try:
        from watchdog.cmd.vault import _obsidian_config_path
        return _obsidian_config_path().parent.exists()
    except Exception:  # noqa: BLE001
        return False
