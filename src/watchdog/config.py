"""The user's `~/.watchdog/config.json`, read one way everywhere below the CLI layer.

Every reader is lenient: a missing, unreadable or corrupt file reads as `{}`, so each key falls back
to its default. The CLI's own `cmd.base.load_config` is the strict reader — `dig`/`bark` stop on a
corrupt file before any pipeline code runs, so this leniency never hides one from the user.
"""

import json
from pathlib import Path

# Set to a path to read that file instead (tests); None reads `~/.watchdog/config.json`, resolved
# at call time so a changed HOME is honoured.
CONFIG_FILE: Path | None = None


def path() -> Path:
    return CONFIG_FILE if CONFIG_FILE is not None else Path.home() / ".watchdog" / "config.json"


def read() -> dict:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def get(key: str, default=None):
    return read().get(key, default)
