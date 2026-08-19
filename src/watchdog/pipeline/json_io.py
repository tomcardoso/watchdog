"""Shared low-level JSON file I/O helper (#636).

Stdlib-only by design (mirrors `pipeline/abort.py`'s pattern) so both `cmd/*.py` and any
`pipeline/*.py` module can import it with no circular-import risk.
"""

import json
from pathlib import Path


def _read_json_or(path: Path, default):
    """Read and parse a JSON file, returning `default` if it's missing or corrupt."""
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except (OSError, json.JSONDecodeError):
        return default
