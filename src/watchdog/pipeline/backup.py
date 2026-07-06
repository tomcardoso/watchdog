"""Pre-mutation snapshots for irreversible vault operations (#270).

`merge-entities`, ingest's `discard` choice, and `delete --purge` are all one-way,
but the state actually at risk is small — a couple of registry files and a handful
of notes. `snapshot()` copies exactly those files into `.watchdog/backups/<ts>-
<operation>/` before the caller mutates or deletes them, so a mistake is a manual
file copy away from undone instead of gone for good. This is a hedge against typos,
not a versioning system — backups are pruned to the most recent few.
"""

import shutil
from datetime import datetime, timezone
from pathlib import Path

_KEEP = 5


def snapshot(vault_path: Path, operation: str, paths: list[Path]) -> Path | None:
    """Copy each of `paths` that exists into a fresh timestamped directory under
    `.watchdog/backups/`, preserving its position relative to `vault_path`. Returns
    the backup directory, or None if none of `paths` existed (nothing to snapshot,
    so no empty backup directory is left behind).
    """
    existing = [p for p in paths if p.exists()]
    if not existing:
        return None

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backups_root = vault_path / ".watchdog" / "backups"
    dest_dir = backups_root / f"{ts}-{operation}"
    dest_dir.mkdir(parents=True, exist_ok=True)

    for p in existing:
        dest = dest_dir / p.relative_to(vault_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if p.is_dir():
            shutil.copytree(p, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(p, dest)

    _prune(backups_root)
    return dest_dir


def _prune(backups_root: Path, keep: int = _KEEP) -> None:
    """Backup directory names are timestamp-prefixed, so a plain name sort is
    oldest-first; drop everything past the most recent `keep`."""
    entries = sorted(d for d in backups_root.iterdir() if d.is_dir())
    for stale in entries[:-keep]:
        shutil.rmtree(stale, ignore_errors=True)
