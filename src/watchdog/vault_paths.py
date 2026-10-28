"""What counts as a Watchdog vault on disk, and where its folders and working files live.
Stdlib-only, so any module can import it.

A vault is a folder with a `.watchdog/` directory, but the global folder is also called `.watchdog`
(`~/.watchdog`), so a bare `.watchdog/` check would treat the home folder itself as a vault. The
global folder is recognized by its own files and the absence of a vault registry.
"""

from pathlib import Path

_GLOBAL_MARKERS = ("config.json", "projects.json", "credentials.json", "telemetry.db")


def is_vault(path: Path) -> bool:
    """True if `path` is an investigation vault (not merely a folder holding the global
    `~/.watchdog` config directory)."""
    d = Path(path) / ".watchdog"
    if not d.is_dir():
        return False
    if (d / "registry").is_dir() or (d / "queue").is_dir():
        return True
    return not any((d / name).exists() for name in _GLOBAL_MARKERS)


# ── folder names ─────────────────────────────────────────────────────────────────
# One place for the names; every module builds paths through the helpers below.
INCOMING_NAME = "incoming"
INCOMING_FAILED_NAME = "failed"
INCOMING_SKIPPED_NAME = "skipped"
CONTEXT_NAME = "context"
# Folders directly under incoming/ that pre-processing never reads.
SET_ASIDE_NAMES = frozenset({INCOMING_FAILED_NAME, INCOMING_SKIPPED_NAME})


def incoming_dir(vault: Path) -> Path:
    return Path(vault) / INCOMING_NAME


def incoming_failed_dir(vault: Path) -> Path:
    return incoming_dir(vault) / INCOMING_FAILED_NAME


def incoming_skipped_dir(vault: Path) -> Path:
    return incoming_dir(vault) / INCOMING_SKIPPED_NAME


def context_dir(vault: Path) -> Path:
    return Path(vault) / CONTEXT_NAME


# ── working files ────────────────────────────────────────────────────────────────
# Named for the stages the app shows (D275): pre-processing holds one lock while it converts
# files; processing and post-processing share the run lock, the run state and the run log.

def preprocessing_lock(vault: Path) -> Path:
    return Path(vault) / ".watchdog" / ".preprocessing-lock"


def processing_lock(vault: Path) -> Path:
    return Path(vault) / ".watchdog" / "registry" / ".processing-lock"


def processing_state(vault: Path) -> Path:
    return Path(vault) / ".watchdog" / "processing-state.json"


def processing_log(vault: Path) -> Path:
    return Path(vault) / ".watchdog" / "registry" / "processing.log"


def unreadable_reasons(vault: Path) -> Path:
    """Why each file in `incoming/failed/` could not be read, by file name. Kept under `.watchdog/`,
    not beside the files: anything in `incoming/` is a document to pre-processing."""
    return Path(vault) / ".watchdog" / "unreadable.json"


def is_set_aside(rel_parts) -> bool:
    """True if a path (given as its parts relative to `incoming/`) lies in the failed or skipped
    folder. Only the top level counts: a user's own subfolder called `failed` is still read."""
    parts = tuple(rel_parts)
    return len(parts) > 0 and parts[0] in SET_ASIDE_NAMES
