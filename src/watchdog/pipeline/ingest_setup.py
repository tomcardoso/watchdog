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
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from watchdog.pipeline.section import (
    section_token_threshold as _section_token_threshold,
    est_tokens_from_pages as _est_tokens_from_pages,
)

STALE_SECONDS = 30 * 60


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(vault: Path, extractor_model: str = "sonnet", finalizer_model: str = "sonnet") -> dict:
    """Acquire lock, scan queue, write state file. Returns the state dict."""
    lock_file = vault / ".watchdog" / "Registry" / ".ingest-lock"
    state_file = vault / ".watchdog" / "ingest-state.json"

    if lock_file.exists():
        try:
            for line in lock_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("started_at:"):
                    ts = line.split(":", 1)[1].strip()
                    lock_dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                    age = (datetime.now(timezone.utc) - lock_dt).total_seconds()
                    if age < STALE_SECONDS:
                        return {"error": f"ingest already running (lock acquired {ts}); if stale, run: watchdog unlock"}
                    break
        except Exception:
            pass
        lock_file.unlink(missing_ok=True)

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

    if total == 0:
        state_file.unlink(missing_ok=True)
        return {"total": 0, "lock_acquired": False, "queue_files": []}

    started_at = _iso_now()
    batch_start = int(time.time())
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(f"pid: cli\nstarted_at: {started_at}\n", encoding="utf-8")

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
