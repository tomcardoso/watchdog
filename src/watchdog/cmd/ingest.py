"""Document pipeline commands: chew, ingest, queue-status, pre-flight, post-flight."""

import json
import sys
from pathlib import Path

from watchdog.cmd.base import (
    _BOLD, _CYAN, _DIM, _GREEN, _RESET, _YELLOW,
    _count_queued,
    _find_project,
    _launch_claude,
    _notify,
)


def _run_preprocess(
    vault: Path,
    workers: int | None = None,
    chunk_workers: int | None = None,
    confirm: bool = False,
) -> None:
    from watchdog.pipeline.preprocess_batch import run_ingest, find_files
    incoming = vault / "_INCOMING"
    queue    = vault / ".watchdog" / "queue"
    if not incoming.is_dir():
        sys.exit(f"Error: _INCOMING/ not found in {vault}")
    if confirm:
        files = find_files([incoming])
        if not files:
            queued = len(list(queue.glob("*.json"))) if queue.exists() else 0
            if queued:
                print(f"\n  {_DIM}_INCOMING/ is empty — {queued} file{'s' if queued != 1 else ''} ready for {_RESET}{_CYAN}/watchdog-ingest{_RESET}{_DIM}.{_RESET}\n")
            else:
                print(f"\n  {_DIM}_INCOMING/ is empty — nothing to chew.{_RESET}\n")
            return
        n = len(files)
        label = f"{n} file{'s' if n != 1 else ''}"
        try:
            answer = input(f"\n  Found {_BOLD}{label}{_RESET} in _INCOMING/. Chew now? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if answer not in ("", "y", "yes"):
            return
    run_ingest(vault, workers=workers, chunk_workers=chunk_workers)


def cmd_chew(args) -> None:
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: not inside a Watchdog project folder. cd into your investigation first.")

    queued_before = _count_queued(vault)
    file_arg = getattr(args, "file", None)
    chew_workers  = getattr(args, "chew_workers", None)
    chunk_workers = getattr(args, "chunk_workers", None)
    if file_arg:
        from watchdog.pipeline.preprocess_batch import run_ingest
        f = Path(file_arg).resolve()
        if not f.exists():
            sys.exit(f"Error: file not found: {f}")
        run_ingest(vault, workers=chew_workers, chunk_workers=chunk_workers, files=[f])
    else:
        _run_preprocess(vault, workers=chew_workers, chunk_workers=chunk_workers)

    new_queued = _count_queued(vault) - queued_before
    if new_queued > 0:
        _notify("Watchdog", f"{new_queued} file{'s' if new_queued != 1 else ''} chewed — ready for /watchdog-ingest.")


def cmd_ingest(args) -> None:
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")
    from watchdog.pipeline.ingest_setup import run as is_run
    result = is_run(vault)
    if "error" in result:
        sys.exit(f"\n  {_YELLOW}Error:{_RESET} {result['error']}\n")
    if result["total"] == 0:
        print(f"\n  {_DIM}Queue is empty — nothing to ingest.{_RESET}")
        print(f"  Run {_CYAN}watchdog chew{_RESET}{_DIM} to process documents in _INCOMING/ first.{_RESET}\n")
        return
    q = len(result["queue_files"])
    a = len(result["arrows_files"])
    parts = [f"{_BOLD}{q} document{'s' if q != 1 else ''}{_RESET}"]
    if a:
        parts.append(f"{a} diagram{'s' if a != 1 else ''}")
    print(f"\n  {', '.join(parts)} ready for extraction")
    try:
        answer = input(f"\n  Open in Claude Code to start ingestion? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        print(f"\n  When ready, open Claude Code and run:  {_CYAN}/watchdog-ingest{_RESET}\n")
        return
    if answer in ("", "y", "yes"):
        _launch_claude(vault)
    else:
        print(f"\n  When ready, open Claude Code and run:  {_CYAN}/watchdog-ingest{_RESET}\n")


def cmd_queue_status(args) -> None:
    cwd = Path(".").resolve()
    if (cwd / ".watchdog").is_dir():
        vault = cwd
    else:
        _, info = _find_project(args.project)
        vault = Path(info["path"])

    (vault / ".watchdog" / "tmp").mkdir(parents=True, exist_ok=True)

    queue_dir = vault / ".watchdog" / "queue"
    if not queue_dir.exists():
        print('{"total": 0, "files": []}')
        return

    files = sorted(queue_dir.glob("*.json"))
    entries = []
    for f in files:
        source_type = None
        try:
            data = json.loads(f.read_text())
            source_type = data.get("metadata", {}).get("source_type")
        except Exception:
            pass
        entries.append({"path": str(f), "source_type": source_type})

    print(json.dumps({"total": len(entries), "files": entries}, ensure_ascii=False))


def cmd_preflight(args) -> None:
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")
    from watchdog.pipeline.preflight import run as pf_run
    result = pf_run(vault, args.sha256)
    if "error" in result:
        sys.exit(f"Error: {result['error']}")
    print(json.dumps(result, ensure_ascii=False))


def cmd_postflight(args) -> None:
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")
    extraction_path = Path(args.extraction).resolve()
    if not str(extraction_path).startswith(str(vault) + "/"):
        sys.exit(f"Error: --extraction must be inside the vault directory ({vault})")
    from watchdog.pipeline.postflight import run as post_run
    result = post_run(vault, extraction_path)
    print(json.dumps(result, ensure_ascii=False))
    if "errors" in result:
        sys.exit(1)
