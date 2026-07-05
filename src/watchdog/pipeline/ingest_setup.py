"""
watchdog ingest — human-facing setup step for the /watchdog-ingest skill.

Run from the vault root before opening Claude Code. Handles:
  1. Stale lock detection (>30 min) and re-acquisition
  2. Queue directory scan
  3. Writes .watchdog/ingest-state.json for the skill to read

Human workflow:
  watchdog chew    →  watchdog ingest    →  open Claude Code  →  /watchdog-ingest
  (OCR/docling)       (lock + queue)        (skill reads state file)
"""

import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from watchdog.pipeline.locks import acquire_or_take_stale, lock_age_seconds, lock_started_at
from watchdog.pipeline.section import (
    section_token_threshold as _section_token_threshold,
    est_tokens_from_pages as _est_tokens_from_pages,
)

STALE_SECONDS = 30 * 60


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(vault: Path, extractor_model: str = "sonnet", finalizer_model: str = "sonnet",
        wipe_pending: bool = True, force_lock: bool = False) -> dict:
    """Acquire lock, scan queue, write state file. Returns the state dict.

    ``wipe_pending=False`` keeps a prior run's un-finalized post-ingest inputs so this
    ingest *merges* into that batch (both finalize together) instead of discarding it.

    ``force_lock=True`` acquires the lock even with an empty queue (#214) — a pending
    claude-batch extraction still needs mutual exclusion (so two concurrent `watchdog
    ingest` invocations can't both try to collect the same batch) even when there's
    nothing new to chew.
    """
    lock_file = vault / ".watchdog" / "Registry" / ".ingest-lock"
    state_file = vault / ".watchdog" / "ingest-state.json"

    queue_dir = vault / ".watchdog" / "queue"
    queue_files: list[dict] = []

    if queue_dir.exists():
        for qf in sorted(queue_dir.glob("*.json")):
            try:
                data = json.loads(qf.read_text(encoding="utf-8"))
            except Exception:
                continue
            queue_files.append({
                "path": str(qf.relative_to(vault)),
                "sha256": qf.stem,
                "filename": data.get("filename", qf.stem),
                "document_type": data.get("document_type"),
                "page_count": data.get("page_count") or len(data.get("pages", [])),
                "est_tokens": _est_tokens_from_pages(data.get("pages", [])),
            })

    total = len(queue_files)

    def _live_lock_error() -> dict | None:
        """If a non-stale (or unknown-age) lock is held, return the 'already running' error;
        None if the lock is absent or provably stale (safe to take over / ignore)."""
        if not lock_file.exists():
            return None
        age = lock_age_seconds(lock_file)   # None ⇒ unparseable ⇒ treat as live (see #257)
        if age is None or age < STALE_SECONDS:
            ts = lock_started_at(lock_file)
            when = f" (lock acquired {ts})" if ts else ""
            return {"error": f"ingest already running{when}; if stale, run: watchdog unlock"}
        return None

    if total == 0 and not force_lock:
        # Nothing new to ingest. Don't acquire a lock — but if a live ingest holds one, say so
        # rather than silently clearing its ingest-state.json.
        err = _live_lock_error()
        if err is not None:
            return err
        lock_file.unlink(missing_ok=True)   # only reached when the lock is absent or stale
        state_file.unlink(missing_ok=True)
        return {"total": 0, "lock_acquired": False, "queue_files": []}

    # Atomically acquire the lock *before* any mutation. O_CREAT|O_EXCL means two racing
    # `watchdog ingest` invocations can't both pass an existence check and both proceed (#257);
    # a provably-stale lock (>30 min, from a crashed run) is taken over, an unparseable one is
    # left for `watchdog unlock` rather than blindly deleted.
    started_at = _iso_now()
    batch_start = int(time.time())
    if not acquire_or_take_stale(lock_file, f"pid: cli\nstarted_at: {started_at}\n", STALE_SECONDS):
        err = _live_lock_error()
        return err if err is not None else {
            "error": "ingest already running; if stale, run: watchdog unlock"}

    # Fresh run — clear the post-ingest inputs (entity fragments, per-doc results, and
    # scratchpads) left by a prior ingest so the finalizer gate + briefing see only this run's
    # documents. Skipped when merging into a pending batch (wipe_pending=False), so this run's
    # documents accumulate onto it and they finalize together.
    if wipe_pending:
        tmp = vault / ".watchdog" / "tmp"
        shutil.rmtree(tmp / "entity-fragments", ignore_errors=True)
        for p in list(tmp.glob("result_*.json")) + list(tmp.glob("notes_*.md")):
            p.unlink(missing_ok=True)

    state = {
        "lock_acquired": True,
        "started_at": started_at,
        "batch_start": batch_start,
        "total": total,
        "queue_files": queue_files,
        "extractor_model": extractor_model,
        "finalizer_model": finalizer_model,
        "section_token_threshold": _section_token_threshold(),
    }
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state


def main() -> None:
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")
    result = run(vault)
    if "error" in result:
        sys.exit(f"Error: {result['error']}")
    if result["total"] == 0:
        print("\n  Queue is empty — nothing to ingest.")
        print("  Run watchdog chew to process documents in _INCOMING/ first.\n")
        return
    q = len(result["queue_files"])
    print(f"\n  {q} document{'s' if q != 1 else ''} ready for extraction")
    print("\n  Open Claude Code and run:  /watchdog-ingest\n")


if __name__ == "__main__":
    main()
