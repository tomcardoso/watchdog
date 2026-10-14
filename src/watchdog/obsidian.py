"""Obsidian's own list of vaults (`obsidian.json`), which Watchdog keeps in step with its
investigations so each one opens in Obsidian without the reporter adding it by hand.

Every function here is best-effort: Obsidian not being installed, or its file being missing or
unreadable, never stops the change to the investigation itself.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path


def config_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "obsidian" / "obsidian.json"
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "obsidian" / "obsidian.json"
    # An unset or empty XDG_CONFIG_HOME means ~/.config (`Path("")` is ".", which is truthy).
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "obsidian" / "obsidian.json"


def installed() -> bool:
    try:
        return config_path().parent.exists()
    except Exception:  # noqa: BLE001
        return False


def register(vault: Path) -> None:
    cfg = config_path()
    try:
        data = json.loads(cfg.read_text()) if cfg.exists() else {"vaults": {}}
        data.setdefault("vaults", {})[secrets.token_hex(8)] = {
            "path": str(vault),
            "ts": int(datetime.now(timezone.utc).timestamp() * 1000),
        }
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps(data))
    except Exception:  # noqa: BLE001
        pass


def unregister(vault: Path) -> None:
    _edit(lambda vaults: {k: v for k, v in vaults.items() if v.get("path") != str(vault)})


def relocate(old: Path | str, new: Path) -> None:
    """Point Obsidian's entry for `old` at `new` (an investigation renamed or moved)."""
    def move(vaults: dict) -> dict:
        for v in vaults.values():
            if v.get("path") == str(old):
                v["path"] = str(new)
        return vaults
    _edit(move)


def _edit(change) -> None:
    cfg = config_path()
    try:
        if not cfg.exists():
            return
        data = json.loads(cfg.read_text())
        data["vaults"] = change(data.get("vaults", {}))
        cfg.write_text(json.dumps(data))
    except Exception:  # noqa: BLE001
        pass
