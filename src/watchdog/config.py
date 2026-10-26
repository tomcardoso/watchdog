"""Watchdog's own folder (`~/.watchdog`) and its settings file, `config.json`.

`home()` is the one place the folder is decided: every file Watchdog keeps there (settings, the
list of investigations, credentials, chats) is found through it, so tests move all of them with a
single override, `HOME`.

`read()` is lenient: a missing, unreadable or damaged file reads as `{}`, so each setting falls
back to its default (the pipeline's own readers use it). `load()` is strict: a damaged file stops
with a message naming it, so a run never goes ahead on default models while the reporter's
settings are being ignored. `save()` writes the file readable by its owner only, since it can hold
the Wayback Machine secret key.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Set to a folder to use it instead of `~/.watchdog` (tests). Resolved at call time, so a changed
# HOME is honoured.
HOME: Path | None = None


def home() -> Path:
    return HOME if HOME is not None else Path.home() / ".watchdog"


def path() -> Path:
    return home() / "config.json"


# The four finishing-stage model overrides D339 replaced, by the group each now belongs to, in the
# order a value is taken when a reporter had set more than one.
_LEGACY_FINALIZER_KEYS = {
    "finalizer_writing_model": ("finalizer_synthesis_model", "finalizer_briefing_model"),
    "finalizer_housekeeping_model": ("finalizer_reconciliation_model", "finalizer_timeline_model"),
}


def _upgrade(data: dict) -> dict:
    """Settings saved before D339 read as their replacement: an old per-stage override becomes
    its group's override unless that is already set, and the old key is dropped, so the next save
    writes only the new keys."""
    data.pop("default_spending_limit", None)     # replaced by per-key limits (D347)
    for new, olds in _LEGACY_FINALIZER_KEYS.items():
        values = [data.pop(old) for old in olds if old in data]
        if new not in data and any(values):
            data[new] = next(v for v in values if v)
    return data


def read() -> dict:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return _upgrade(data) if isinstance(data, dict) else {}


def load() -> dict:
    """`config.json` as a dict, `{}` when it doesn't exist yet. A damaged file stops the caller."""
    p = path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as e:
        sys.exit(f"Error: the settings file is damaged ({e}). Fix or remove {p}.")
    return _upgrade(data) if isinstance(data, dict) else {}


def save(config: dict) -> None:
    from watchdog.pipeline.json_io import write_private_json
    home().mkdir(parents=True, exist_ok=True)
    write_private_json(path(), config)


def get(key: str, default=None):
    return read().get(key, default)
