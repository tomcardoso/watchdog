"""Shared low-level JSON file I/O helpers (#636).

Stdlib-only by design (mirrors `pipeline/abort.py`'s pattern) so both `cmd/*.py` and any
`pipeline/*.py` module can import it with no circular-import risk.
"""

import json
import os
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


def write_private_json(path: Path, data) -> None:
    """Write `data` as JSON to a file only its owner can read (0600), for the config and
    credentials files (#304). The file is created with that mode rather than chmod-ed after
    writing, so a newly created file holding API keys is never world-readable, even briefly;
    an existing file's mode is tightened before the new contents go in."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        else:  # Windows: no fchmod; os.chmod only toggles the read-only bit there anyway
            os.chmod(path, 0o600)
        os.write(fd, (json.dumps(data, indent=2) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
