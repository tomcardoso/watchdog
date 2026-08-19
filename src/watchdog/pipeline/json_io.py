"""Shared low-level JSON file I/O helpers (#636).

Stdlib-only by design (mirrors `pipeline/abort.py`'s pattern) so both `cmd/*.py` and any
`pipeline/*.py` module can import it with no circular-import risk.
"""

import json
from pathlib import Path


def _read_json(path: Path):
    """Read and parse a JSON file. Raises `OSError` (e.g. `FileNotFoundError`) if the file
    can't be read, or `json.JSONDecodeError` if it's corrupt — same as the inline
    `json.loads(path.read_text(encoding="utf-8"))` this replaces at each call site."""
    return json.loads(path.read_text(encoding="utf-8"))


def _read_json_or(path: Path, default, catch=(OSError, json.JSONDecodeError)):
    """Read and parse a JSON file, returning `default` if reading raises one of `catch`
    (missing/corrupt by default)."""
    try:
        return _read_json(path)
    except catch:
        return default
