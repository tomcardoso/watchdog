"""What counts as a Watchdog vault on disk. Stdlib-only, so any module can import it.

A vault is a folder with a `.watchdog/` directory — but the global config directory is also
called `.watchdog` (`~/.watchdog`), so a bare `.watchdog/` check treated the home folder itself as
a vault: `watchdog context` run from `~` offered to write `~/context.md` and launch Claude Code
there. The global directory is recognized by its own files and the absence of a vault registry.
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
