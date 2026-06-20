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
    show_ingest_hint: bool = True,
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
                print(f"\n  {_DIM}_INCOMING/ is empty — {queued} file{'s' if queued != 1 else ''} ready. Run {_RESET}{_CYAN}watchdog ingest{_RESET}{_DIM}.{_RESET}\n")
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
    run_ingest(vault, workers=workers, chunk_workers=chunk_workers, show_ingest_hint=show_ingest_hint)


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
        run_ingest(vault, workers=chew_workers, chunk_workers=chunk_workers, files=[f],
                   show_ingest_hint=False)
    else:
        _run_preprocess(vault, workers=chew_workers, chunk_workers=chunk_workers,
                        show_ingest_hint=False)

    new_queued = _count_queued(vault) - queued_before
    if new_queued > 0:
        _notify("Watchdog", f"{new_queued} file{'s' if new_queued != 1 else ''} chewed — run watchdog ingest.")
        _offer_ingest(args, vault)


def _offer_ingest(args, vault: Path) -> None:
    """After chew, offer to run ingest right away; print the command hint if declined."""
    total = _count_queued(vault)
    label = f"{total} document{'s' if total != 1 else ''}"
    try:
        answer = input(f"\n  {_BOLD}{label}{_RESET} ready. Ingest now? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print(f"\n\n  Run:  {_CYAN}watchdog ingest{_RESET}\n")
        return
    if answer in ("", "y", "yes"):
        cmd_ingest(args, confirm=False)
    else:
        print(f"\n  Run:  {_CYAN}watchdog ingest{_RESET}\n")


def cmd_ingest(args, *, confirm: bool = True) -> None:
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")

    from watchdog.cmd.auth import resolve_auth
    a = resolve_auth()
    if a["mode"] == "none":
        sys.exit(f"\n  {_YELLOW}Error:{_RESET} {a.get('reason', 'auth not configured')}\n"
                 f"  Run {_CYAN}watchdog setup{_RESET}{_DIM} to choose how to authenticate.{_RESET}\n")

    from watchdog.cmd.base import CONFIG_FILE
    config: dict = {}
    if CONFIG_FILE.exists():
        try:
            import json as _json
            config = _json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass

    def _model(flag_val, config_key) -> str:
        m = flag_val or config.get(config_key) or "sonnet"
        if m not in _MODEL_IDS:
            sys.exit(f"Error: unknown model '{m}' — choose sonnet, opus, or haiku")
        return m

    extract_model = _model(getattr(args, "extractor_model", None), "extractor_model")
    post_model    = _model(getattr(args, "finalizer_model", None), "finalizer_model")
    try:
        concurrency = int(getattr(args, "concurrency", None) or config.get("extract_concurrency") or 5)
    except (TypeError, ValueError):
        concurrency = 5

    from watchdog.pipeline.ingest_setup import run as is_run
    result = is_run(vault)
    if "error" in result:
        sys.exit(f"\n  {_YELLOW}Error:{_RESET} {result['error']}\n")
    if result["total"] == 0:
        print(f"\n  {_DIM}Queue is empty — nothing to ingest.{_RESET}")
        print(f"  Run {_CYAN}watchdog chew{_RESET}{_DIM} to process documents in _INCOMING/ first.{_RESET}\n")
        return

    q = len(result["queue_files"])
    print(f"\n  {_BOLD}{q} document{'s' if q != 1 else ''}{_RESET} ready for extraction")

    def _release_lock() -> None:
        (vault / ".watchdog" / "Registry" / ".ingest-lock").unlink(missing_ok=True)
        (vault / ".watchdog" / "ingest-state.json").unlink(missing_ok=True)

    if confirm:
        try:
            answer = input(f"\n  Ingest now with your {_BOLD}{a['mode']}{_RESET} auth? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            _release_lock()
            print(f"\n  When ready, run:  {_CYAN}watchdog ingest{_RESET}\n")
            return
        if answer not in ("", "y", "yes"):
            _release_lock()
            print(f"\n  When ready, run:  {_CYAN}watchdog ingest{_RESET}\n")
            return
    else:
        print(f"\n  {_DIM}Using your {_BOLD}{a['mode']}{_RESET}{_DIM} auth.{_RESET}")

    import asyncio
    from watchdog.pipeline import orchestrate
    log_path = vault / "log.md"
    if not log_path.exists():
        log_path.write_text(_render_template("log.md"))
    print(f"\n  {_DIM}Extracting (≤{concurrency} parallel) — the model is called only for reasoning; "
          f"the pipeline runs in Python.{_RESET}")
    print(f"  {_DIM}Press {_RESET}{_CYAN}Ctrl+C{_RESET}{_DIM} to stop; finished documents are kept.{_RESET}\n")
    try:
        summary = asyncio.run(orchestrate.run(
            vault, concurrency=concurrency, extract_model=extract_model, post_model=post_model))
    except KeyboardInterrupt:
        # Fallback only — orchestrate.run normally traps SIGINT itself and returns a
        # cancelled summary. This catches a Ctrl+C in the brief window before/after that.
        _release_lock()
        print(f"\n  {_YELLOW}Ingest cancelled.{_RESET}{_DIM} Finished documents are saved; "
              f"re-run {_RESET}{_CYAN}watchdog ingest{_RESET}{_DIM} to resume the rest.{_RESET}\n")
        sys.exit(130)
    finally:
        _release_lock()
    _print_ingest_summary(summary)


def _print_ingest_summary(summary: dict) -> None:
    ext, skip, fail = summary["extracted"], summary["skipped"], summary["failed"]
    cancelled = summary.get("cancelled")
    n_cancelled = sum(1 for r in summary["results"] if r.get("status") == "cancelled")
    headline = f"{_YELLOW}Ingest stopped{_RESET}" if cancelled else f"{_GREEN}Ingest complete{_RESET}"
    print(f"\n  {headline}  {_BOLD}{ext}{_RESET} extracted"
          f"{f', {skip} skipped' if skip else ''}{f', {fail} failed' if fail else ''}"
          f"{f', {n_cancelled} not started' if n_cancelled else ''}\n")
    for r in summary["results"]:
        name = r.get("filename") or r.get("sha256", "?")
        if r["status"] == "ok":
            print(f"  {_GREEN}✓{_RESET} {name}  {_DIM}{r.get('entity_count', 0)} entities{_RESET}")
        elif r["status"] == "skipped":
            print(f"  {_DIM}– {name}  already extracted{_RESET}")
        elif r["status"] == "cancelled":
            continue
        else:
            print(f"  {_YELLOW}✗ {name}  {r.get('reason', '')}{_RESET}")
    if cancelled:
        print(f"\n  {_DIM}Re-run {_RESET}{_CYAN}watchdog ingest{_RESET}{_DIM} to process the remaining documents.{_RESET}\n")
    else:
        print(f"\n  {_DIM}Open a fresh Claude Code session to ask investigation questions.{_RESET}\n")


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
