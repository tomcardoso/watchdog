"""Document pipeline commands: chew, ingest, queue-status, pre-flight, post-flight."""

import json
import sys
from pathlib import Path

from watchdog.cmd.base import (
    _BOLD, _CYAN, _DIM, _GREEN, _RESET, _YELLOW,
    _count_queued,
    _find_project,
    _launch_claude,
    _MODEL_IDS,
    _notify,
    _render_template,
    load_projects,
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
    orchestrator_model = getattr(args, "orchestrator_model", None) or "sonnet"
    extractor_model    = getattr(args, "extractor_model",    None) or "sonnet"
    if orchestrator_model not in _MODEL_IDS:
        sys.exit(f"Error: unknown model '{orchestrator_model}' — choose sonnet, opus, or haiku")
    if extractor_model not in _MODEL_IDS:
        sys.exit(f"Error: unknown model '{extractor_model}' — choose sonnet, opus, or haiku")
    from watchdog.pipeline.ingest_setup import run as is_run
    result = is_run(vault, extractor_model=extractor_model)
    if "error" in result:
        sys.exit(f"\n  {_YELLOW}Error:{_RESET} {result['error']}\n")
    if result["total"] == 0:
        print(f"\n  {_DIM}Queue is empty — nothing to ingest.{_RESET}")
        print(f"  Run {_CYAN}watchdog chew{_RESET}{_DIM} to process documents in _INCOMING/ first.{_RESET}\n")
        return
    q = len(result["queue_files"])
    print(f"\n  {_BOLD}{q} document{'s' if q != 1 else ''}{_RESET} ready for extraction")
    try:
        answer = input(f"\n  Open in Claude Code to start ingestion? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        print(f"\n  When ready, open Claude Code and run:  {_CYAN}/watchdog-ingest{_RESET}\n")
        return
    if answer in ("", "y", "yes"):
        log_path = vault / "log.md"
        if not log_path.exists():
            log_path.write_text(_render_template("log.md"))
        _launch_claude(vault, "/watchdog-ingest", model=orchestrator_model)
    else:
        print(f"\n  When ready, open Claude Code and run:  {_CYAN}/watchdog-ingest{_RESET}\n")


def cmd_context(args) -> None:
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        if getattr(args, "name", None):
            _, info = _find_project(args.name)
            vault = Path(info["path"])
        else:
            sys.exit("Error: not inside a Watchdog project. cd into your investigation first, or pass the investigation name.")
    model = getattr(args, "model", None) or "sonnet"
    if model not in _MODEL_IDS:
        sys.exit(f"Error: unknown model '{model}' — choose sonnet, opus, or haiku")

    projects = load_projects()
    info = next((v for v in projects.values() if Path(v["path"]).resolve() == vault.resolve()), None)
    name = info["name"] if info else vault.name

    context_dir = vault / "_CONTEXT"
    context_files = sorted(context_dir.iterdir()) if context_dir.is_dir() else []
    context_exists = (vault / "context.md").exists()

    print(f"\n  {_BOLD}{name}{_RESET}")
    if context_files:
        n = len(context_files)
        print(f"  {_DIM}{n} file{'s' if n != 1 else ''} in{_RESET} {_CYAN}_CONTEXT/{_RESET}")
    else:
        print(f"  {_YELLOW}_CONTEXT/ is empty{_RESET}{_DIM} — Claude will interview you instead{_RESET}")
    if context_exists:
        print(f"  {_DIM}existing context.md will be updated{_RESET}")

    try:
        answer = input(f"\n  Open in Claude Code to seed context? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        print(f"\n  When ready, open Claude Code and run:  {_CYAN}/watchdog-context{_RESET}\n")
        return
    if answer in ("", "y", "yes"):
        context_path = vault / "context.md"
        if not context_path.exists():
            description = info["description"] if info and info.get("description") else "<!-- One paragraph. What is the story? What pattern, question, or wrongdoing are you pursuing? -->"
            context_path.write_text(_render_template("context.md", name=name, description=description))
        _launch_claude(vault, "/watchdog-context", model=model)
    else:
        print(f"\n  When ready, open Claude Code and run:  {_CYAN}/watchdog-context{_RESET}\n")


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
