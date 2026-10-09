"""`history.*` — the version history of every file Watchdog generates or the app edits (D286).

App-only: there is no terminal command. Reads come from `pipeline/history`; the two writes,
restoring a version and clearing the history, call its library functions directly, the first
vault mutation with no CLI equivalent (D286 amends I10). Folder access (D268) still applies: the
vault must be allowed, and the audit hook refuses any write outside it.
"""

from __future__ import annotations

from watchdog.gui import vaultio
from watchdog.gui.rpc import RpcError, method
from watchdog.gui.vaultio import require_vault, resolve_in_vault


def _path(v, path) -> str:
    """The vault-relative path of a tracked file. A note path may omit `.md`, as wikilinks do."""
    from watchdog.pipeline import history
    target = resolve_in_vault(v, path)
    rel = vaultio.rel_posix(v, target)
    if not history.tracked(rel) and history.tracked(rel + ".md"):
        rel += ".md"
    if not history.tracked(rel):
        raise RpcError("Watchdog keeps no history for that file.", code="not_tracked")
    return rel


def _version(version) -> int:
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise RpcError("version must be a positive whole number.", code="bad_params")
    return version


def _errors(fn):
    from watchdog.pipeline import history
    try:
        return fn()
    except history.HistoryTooNew as e:
        raise RpcError(str(e), code="history_too_new")
    except history.CannotRestore as e:
        raise RpcError(str(e), code="cannot_restore")
    except (KeyError, LookupError):
        raise RpcError("That version is not in this file's history.", code="not_found")


@method("history.file")
def file(vault: str, path: str) -> dict:
    from watchdog.pipeline import history
    v = require_vault(vault)
    return history.file_history(v, _path(v, path))


@method("history.read")
def read(vault: str, path: str, version: int) -> dict:
    from watchdog.pipeline import history
    v = require_vault(vault)
    rel, n = _path(v, path), _version(version)
    return _errors(lambda: history.read_version(v, rel, n))


@method("history.diff")
def diff(vault: str, path: str, version: int, against: str = "previous") -> dict:
    from watchdog.pipeline import history
    v = require_vault(vault)
    if against not in ("previous", "current"):
        raise RpcError('against must be "previous" or "current".', code="bad_params")
    rel, n = _path(v, path), _version(version)
    return _errors(lambda: history.diff(v, rel, n, against))


@method("history.restore")
def restore(vault: str, path: str, version: int) -> dict:
    from watchdog.pipeline import history
    v = require_vault(vault)
    rel, n = _path(v, path), _version(version)
    return _errors(lambda: history.restore(v, rel, n))


@method("history.versions")
def versions(vault: str, limit: int = 100, before: int | None = None) -> dict:
    from watchdog.pipeline import history
    v = require_vault(vault)
    if before is not None:
        before = _version(before)
    return history.versions(v, limit=limit if isinstance(limit, int) else 100, before=before)


@method("history.stats")
def stats(vault: str) -> dict:
    from watchdog.pipeline import history
    return history.stats(require_vault(vault))


@method("history.clear")
def clear(vault: str) -> dict:
    from watchdog.pipeline import history
    v = require_vault(vault)
    if history.run_in_progress(v):
        raise RpcError("Documents are being added to this investigation. Clear the history when "
                       "that has finished.", code="busy")
    return _errors(lambda: history.clear(v))
