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


# ── folder names (D266) ──────────────────────────────────────────────────────────
# One place for the names; every module builds paths through the helpers below.
INCOMING_NAME = "incoming"
INCOMING_FAILED_NAME = "failed"
INCOMING_SKIPPED_NAME = "skipped"
CONTEXT_NAME = "context"

# Names vaults were created with before D266, and what each became.
LEGACY_INCOMING_NAME = "_INCOMING"
LEGACY_CONTEXT_NAME = "_CONTEXT"
_LEGACY_SUBFOLDERS = {
    "_FAILED": INCOMING_FAILED_NAME, "_failed": INCOMING_FAILED_NAME,
    "_SKIPPED": INCOMING_SKIPPED_NAME, "_skipped": INCOMING_SKIPPED_NAME,
}
# Folders directly under incoming/ that chew never reads (legacy spellings included, so an
# unmigrated folder is still left alone).
SET_ASIDE_NAMES = frozenset({INCOMING_FAILED_NAME, INCOMING_SKIPPED_NAME,
                             "_failed", "_FAILED", "_skipped", "_SKIPPED"})


def incoming_dir(vault: Path) -> Path:
    return Path(vault) / INCOMING_NAME


def incoming_failed_dir(vault: Path) -> Path:
    return incoming_dir(vault) / INCOMING_FAILED_NAME


def incoming_skipped_dir(vault: Path) -> Path:
    return incoming_dir(vault) / INCOMING_SKIPPED_NAME


def context_dir(vault: Path) -> Path:
    return Path(vault) / CONTEXT_NAME


def is_set_aside(rel_parts) -> bool:
    """True if a path (given as its parts relative to `incoming/`) lies in the failed or skipped
    folder. Only the top level counts: a user's own subfolder called `failed` is still read."""
    parts = tuple(rel_parts)
    return len(parts) > 0 and parts[0] in SET_ASIDE_NAMES


def _free_name(target: Path) -> Path:
    n = 1
    while True:
        candidate = target.with_name(f"{target.stem}-migrated{'' if n == 1 else f'-{n}'}{target.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def _merge(old: Path, new: Path, moved: list[str], label: str) -> None:
    """Move everything in `old` into `new` without overwriting; clashing files get a suffix."""
    for child in sorted(old.iterdir()):
        dest = new / child.name
        if not dest.exists():
            child.rename(dest)
        elif child.is_dir() and dest.is_dir():
            _merge(child, dest, moved, f"{label}/{child.name}")
            try:
                child.rmdir()
            except OSError:
                pass
        else:
            child.rename(_free_name(dest))
    moved.append(f"merged {label}")


def _rename_folder(old: Path, new: Path, old_label: str, new_label: str, changes: list[str]) -> None:
    if not old.is_dir():
        return
    if not new.exists():
        old.rename(new)
        changes.append(f"{old_label}/ renamed to {new_label}/")
        return
    if new.is_dir():
        inner: list[str] = []
        _merge(old, new, inner, old_label)
        try:
            old.rmdir()
        except OSError:
            pass
        changes.append(f"{old_label}/ merged into {new_label}/")


def _rewrite_permission(rule: str) -> str:
    return (rule.replace("_INCOMING/_FAILED", "incoming/failed")
                .replace("_INCOMING/_SKIPPED", "incoming/skipped")
                .replace(LEGACY_INCOMING_NAME, INCOMING_NAME)
                .replace(LEGACY_CONTEXT_NAME, CONTEXT_NAME))


def _rewrite_settings(vault: Path) -> bool:
    import json
    path = vault / ".claude" / "settings.json"
    if not path.is_file():
        return False
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
        perms = settings.get("permissions")
        changed = False
        for key in ("allow", "deny", "ask"):
            rules = perms.get(key) if isinstance(perms, dict) else None
            if isinstance(rules, list):
                new = [_rewrite_permission(r) if isinstance(r, str) else r for r in rules]
                if new != rules:
                    deduped = []
                    for r in new:
                        if r not in deduped:
                            deduped.append(r)
                    perms[key] = deduped
                    changed = True
        if changed:
            path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        return changed
    except (OSError, ValueError, AttributeError, TypeError):
        return False


def migrate_folder_names(vault: Path) -> list[str]:
    """Rename a pre-D266 vault's `_INCOMING/` (and its `_FAILED`/`_SKIPPED`) and `_CONTEXT/` to
    their current names, and rewrite any permission rules in `.claude/settings.json` that name
    them. Idempotent; never deletes a file. When both an old and a new folder exist, the old one's
    contents are moved into the new one (a clashing file is renamed with a `-migrated` suffix).
    Returns a description of each change made (empty when there was nothing to do)."""
    vault = Path(vault)
    changes: list[str] = []
    try:
        _rename_folder(vault / LEGACY_INCOMING_NAME, incoming_dir(vault),
                       LEGACY_INCOMING_NAME, INCOMING_NAME, changes)
        inc = incoming_dir(vault)
        if inc.is_dir():
            for d in sorted(inc.iterdir()):
                new_name = _LEGACY_SUBFOLDERS.get(d.name)
                if new_name and d.is_dir():
                    _rename_folder(d, inc / new_name, f"{INCOMING_NAME}/{d.name}",
                                   f"{INCOMING_NAME}/{new_name}", changes)
        _rename_folder(vault / LEGACY_CONTEXT_NAME, context_dir(vault),
                       LEGACY_CONTEXT_NAME, CONTEXT_NAME, changes)
    except OSError as e:
        changes.append(f"stopped early: {e}")
    if _rewrite_settings(vault):
        changes.append(".claude/settings.json permissions updated")
    return changes


def ensure_current_layout(vault: Path, *, say=None, refresh: bool = True) -> list[str]:
    """Migrate `vault` if it needs it, then refresh its Claude commands and session instructions
    (reusing the `refresh-skills` machinery) and note the move in ingest.log. `say`, if given, is
    called with a one-line summary. Returns the changes; cheap when there is nothing to do."""
    vault = Path(vault)
    if not is_vault(vault):
        return []
    changes = migrate_folder_names(vault)
    if not changes:
        return []
    if refresh:
        try:
            from watchdog.cmd.setup import _refresh_vault_claude_md
            from watchdog.setup_cmd import install_skills
            install_skills(vault / ".claude" / "commands")
            _refresh_vault_claude_md(vault)
        except Exception:                                   # a refresh failure must not block work
            pass
    try:
        import datetime
        log = vault / ".watchdog" / "registry" / "ingest.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(log, "a", encoding="utf-8") as f:
            for c in changes:
                f.write(f"[{ts}] MIGRATED {c}\n")
    except OSError:
        pass
    if say:
        say("Vault folders renamed: " + "; ".join(changes))
    return changes


_ensured: set[str] = set()


def ensure_current_layout_once(vault: Path, *, say=None) -> None:
    """`ensure_current_layout`, at most once per vault per process."""
    key = str(Path(vault).resolve())
    if key in _ensured:
        return
    _ensured.add(key)
    ensure_current_layout(vault, say=say)


def modernize_path(rel: str) -> str:
    """A vault-relative path as recorded before D266, pointed at the folder's current name
    (`_INCOMING/_FAILED/x.pdf` -> `incoming/failed/x.pdf`). Records keep what they recorded; this
    is only for code that reads such a path back to find a file."""
    p = rel.replace("\\", "/")
    for old_prefix, new_prefix in (("_INCOMING/_FAILED/", "incoming/failed/"),
                                   ("_INCOMING/_SKIPPED/", "incoming/skipped/"),
                                   ("_INCOMING/", "incoming/"), ("_CONTEXT/", "context/")):
        if p.startswith(old_prefix):
            return new_prefix + p[len(old_prefix):]
    return rel
