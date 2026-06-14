"""
watchdog ingest-abort <sha256> — clean up after a runaway or failed extraction.

When a subagent gives up (its runaway guard fires) or returns an unparseable
result, the document must be left in a clean state so it can be re-ingested
later. A clean bail happens *before* the vault write, so the only state to undo
is the per-document staging in `.watchdog/tmp/` (and any raw timeline files), plus
moving the queue file out of the active queue into a holding area.

This never touches the vault registry, entity/document notes, or the morgue: if
post-flight had run, the extraction succeeded and this command would not be
called. The source file stays in `_INCOMING/` (post-flight never moved it).

Re-ingest later: move the queue file back —
  mv .watchdog/queue/_failed/<sha>.json .watchdog/queue/
then run `watchdog ingest` again.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(vault: Path, sha256: str) -> dict:
    tmp = vault / ".watchdog" / "tmp"
    timeline = vault / ".watchdog" / "timeline"
    queue = vault / ".watchdog" / "queue"
    removed: list[str] = []

    # Per-document staging files in .watchdog/tmp/
    fixed = [
        tmp / f"wdg_ex_{sha256}.json",
        tmp / f"wdg_nd_{sha256}.json",
        tmp / f"notes_{sha256}.md",
        tmp / f"preflight_{sha256}_pages.md",
    ]
    globs = [f"section_{sha256}_*.md", f"section_ex_{sha256}_*.json"]
    for p in fixed:
        if p.exists():
            p.unlink()
            removed.append(str(p.relative_to(vault)))
    if tmp.exists():
        for pattern in globs:
            for p in sorted(tmp.glob(pattern)):
                p.unlink()
                removed.append(str(p.relative_to(vault)))

    # Raw timeline files for this sha (named {date}_{sha[:7]}.ndjson). Canonical
    # files have no sha suffix, so they are never matched here.
    if timeline.exists():
        for p in sorted(timeline.glob(f"*_{sha256[:7]}.ndjson")):
            p.unlink()
            removed.append(str(p.relative_to(vault)))

    # Move the queue file out of the active queue into a holding area so it is
    # preserved (re-ingestable) but not retried automatically.
    requeue_path = None
    queue_file = queue / f"{sha256}.json"
    if queue_file.exists():
        failed_dir = queue / "_failed"
        failed_dir.mkdir(parents=True, exist_ok=True)
        dest = failed_dir / queue_file.name
        queue_file.replace(dest)
        requeue_path = str(dest.relative_to(vault))

    log = vault / ".watchdog" / "Registry" / "ingest.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"[{_iso_now()}] ABORT {sha256} — removed {len(removed)} artifact(s)\n")
    except OSError:
        pass

    return {"ok": True, "sha256": sha256, "removed": removed, "requeue_path": requeue_path}


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("Usage: watchdog ingest-abort <sha256>")
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")
    result = run(vault, sys.argv[1])
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
