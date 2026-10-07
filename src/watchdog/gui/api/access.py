"""Folder access, read-only. The app's main process grants and revokes (it owns
`~/.watchdog/access.json`, see watchdog/access.py); the renderer can only ask what is granted."""

from __future__ import annotations

import json

from watchdog import access
from watchdog.gui.rpc import method


@method("access.list")
def list_access() -> dict:
    try:
        data = json.loads(access.access_file().read_text(encoding="utf-8"))
        folders = [f for f in data.get("folders", []) if isinstance(f, dict) and f.get("path")]
    except (OSError, ValueError, AttributeError):
        folders = []
    return {"enforced": access.enforced(), "file": str(access.access_file()),
            "folders": [{"path": f["path"], "label": f.get("label") or "",
                         "granted": f.get("granted")} for f in folders]}
