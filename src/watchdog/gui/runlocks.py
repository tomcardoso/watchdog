"""The run locks as the app shows them (D293).

The app never offers to release a lock. Each status call clears a lock whose run has died, with
`locks.clear_abandoned`, the function `watchdog unlock` calls for the same case (I10), and reports
what is left: a run in progress on this computer, a run in progress on another computer (a synced
folder), or nothing. The app's first status call after it starts is what clears the locks of jobs
that died with an earlier session of the app.

When a cleared lock shows a run on this computer stopped before it finished, and the reporter did
not stop it here with Stop, the status carries a one-line note until a new run starts.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from watchdog.pipeline import locks
from watchdog.vault_paths import preprocessing_lock, processing_lock

_NOTES: dict[str, dict] = {}
_NOTES_LOCK = threading.Lock()

_NOTE_TEXT = {
    "chew": "Pre-processing stopped before it finished. The files it had converted were kept; "
            "add documents again to convert the rest.",
    "recheck-contradictions": "A contradiction re-check stopped before it finished. Nothing it "
                              "found was filed; run it again from the entity or from Maintenance.",
}
_DEFAULT_NOTE = ("A run stopped before it finished. The documents it had finished were kept; add "
                 "documents again to finish the rest.")


def _key(vault: Path) -> str:
    return os.path.realpath(vault)


def _stopped_by_reporter(pid: int | None) -> bool:
    """True when the lock's process was a job the reporter stopped in this session of the app."""
    if pid is None:
        return False
    from watchdog.gui.jobs import MANAGER
    for job in list(MANAGER.jobs.values()):
        proc = job.proc
        if proc is not None and proc.pid == pid and job.cancel_requests:
            return True
    return False


def _pid(raw: str) -> int | None:
    for line in raw.splitlines():
        if line.startswith("pid:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def _public(holder: dict) -> dict:
    return {"where": holder["where"], "host": holder.get("host"),
            "started_at": holder.get("started_at")}


def run_locks(vault: Path) -> dict:
    """``{"locks": {"chew": holder|None, "ingest": holder|None}, "stopped_run": str|None}``.

    A holder is ``{"where": "here"|"elsewhere"|"unknown", "host", "started_at"}``; ``where`` is
    ``"unknown"`` for a lock written by an older Watchdog, which names no computer."""
    vault = Path(vault)
    out: dict = {}
    for key, path in (("chew", preprocessing_lock(vault)), ("ingest", processing_lock(vault))):
        try:
            cleared = locks.clear_abandoned(path)
        except OSError:             # a read-only or unreachable folder: report, don't clear
            cleared = None
        if cleared and cleared["where"] != "elsewhere" and not _stopped_by_reporter(_pid(cleared["raw"])):
            label = "chew" if key == "chew" else (cleared.get("label") or "")
            with _NOTES_LOCK:
                _NOTES[_key(vault)] = {"text": _NOTE_TEXT.get(label, _DEFAULT_NOTE)}
        holder = locks.held(path)
        out[key] = _public(holder) if holder else None
    with _NOTES_LOCK:
        if out["chew"] or out["ingest"]:
            _NOTES.pop(_key(vault), None)          # a new run has started: the note is old news
        note = _NOTES.get(_key(vault))
    return {"locks": out, "stopped_run": note["text"] if note else None}


def forget_note(vault: Path) -> None:
    """Drop the stopped-run note, when a new job starts on this investigation."""
    with _NOTES_LOCK:
        _NOTES.pop(_key(vault), None)
