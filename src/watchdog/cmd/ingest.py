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
    _warn_pending_research,
    load_projects,
)

# Sentinel for `--skill` with no value: trigger the interactive record-skill picker.
_PICK_SKILL = "\x00pick"

# Reasoning-effort levels for the per-stage effort knobs (D36). `high` is the model
# default, so an unset knob behaves as before (the orchestrator sends no effort param).
_EFFORT_LEVELS = ("low", "medium", "high")


def _effort(flag_val, config_val):
    """Resolve a per-stage effort knob (flag > config > unset). None when unset; validated."""
    e = flag_val or config_val
    if e is None:
        return None
    if e not in _EFFORT_LEVELS:
        sys.exit(f"Error: unknown effort '{e}' — choose {', '.join(_EFFORT_LEVELS)}")
    return e


def _resolve_stage(flag_val, config_val, default="sonnet") -> tuple[str | None, str]:
    """Resolve a stage's `[backend:]model` knob into (backend, model) (#125).

    Plain `sonnet`/`opus`/`haiku` → (None, tier): Claude, routed by auth mode (unchanged). A
    `backend:model` form selects a backend explicitly — `claude-api:opus` (a Claude tier), or
    `openai:gpt-5-mini` / `deepseek:deepseek-chat` (a raw provider model id). Carrying both in
    one value means a stage can never be half-configured."""
    from watchdog.model_client import BACKENDS, CLAUDE_BACKENDS
    raw = flag_val or config_val or default
    backend, sep, model = raw.rpartition(":")
    backend = backend or None
    if backend is not None and backend not in BACKENDS:
        sys.exit(f"Error: unknown backend '{backend}' — choose {', '.join(BACKENDS)}")
    if backend is None or backend in CLAUDE_BACKENDS:
        if model not in _MODEL_IDS:
            sys.exit(f"Error: unknown model '{model}' — choose sonnet, opus, or haiku, "
                     f"or a backend:model form like deepseek:deepseek-chat")
    elif not model:
        sample = "deepseek-chat" if backend == "deepseek" else "gpt-5-mini"
        sys.exit(f"Error: backend '{backend}' needs a model id, e.g. {backend}:{sample}")
    return backend, model


def _pick_skill_interactive() -> str | None:
    """Numbered picker for `watchdog ingest --skill` (no value), drawn from the global
    skill catalog. Returns the chosen skill's file path; Enter → classify per doc."""
    from watchdog import skills_catalog
    catalog = skills_catalog.catalog()
    if not catalog:
        print(f"\n  {_DIM}No record skills available — classifying each document.{_RESET}")
        return None
    names = list(catalog)
    print(f"\n  {_BOLD}Pin a record skill{_RESET} {_DIM}for all documents (skips per-document classification):{_RESET}\n")
    for i, name in enumerate(names, 1):
        print(f"    {_CYAN}{i:>2}{_RESET}  {name}")
    try:
        ans = input("\n  Number, or Enter to classify each: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if ans.isdigit() and 1 <= int(ans) <= len(names):
        return catalog[names[int(ans) - 1]]
    if ans:
        print(f"  {_DIM}Not a valid choice — classifying each document.{_RESET}")
    return None


def _resolve_pinned_skill(args, config: dict) -> str | None:
    """Resolve the pinned record skill to a file path — `--skill` flag → interactive picker
    → `default_skill` config. The value may be a **skill name** (from the global catalog) or
    a **path to a skill file**. Exits if a named skill isn't found and isn't a file."""
    from watchdog import skills_catalog
    raw = getattr(args, "skill", None)
    if raw == _PICK_SKILL:
        return _pick_skill_interactive()                   # picker returns a path or None
    value = raw or config.get("default_skill")
    if not value:
        return None
    as_path = Path(value).expanduser()
    if as_path.is_file():                                   # an explicit skill file
        return str(as_path.resolve())
    catalog = skills_catalog.catalog()                      # otherwise a catalog name
    canon = value.removesuffix(".md")
    if canon in catalog:
        return catalog[canon]
    avail = ", ".join(catalog) or "(none available)"
    sys.exit(f"\n  {_YELLOW}Error:{_RESET} record skill {_BOLD}{canon}{_RESET} not found "
             f"(not a known skill or a file path).\n  Available: {_CYAN}{avail}{_RESET}\n")


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

    _warn_pending_research(vault)
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

    extract_backend, extract_model = _resolve_stage(
        getattr(args, "extractor_model", None), config.get("extractor_model"))
    post_backend, post_model = _resolve_stage(
        getattr(args, "finalizer_model", None), config.get("finalizer_model"))
    classify_backend, classify_model = _resolve_stage(
        getattr(args, "classifier_model", None), config.get("classifier_model"), default="haiku")
    extract_effort = _effort(getattr(args, "extractor_effort", None), config.get("extractor_effort"))
    post_effort    = _effort(getattr(args, "finalizer_effort", None), config.get("finalizer_effort"))
    try:
        concurrency = int(getattr(args, "concurrency", None) or config.get("extract_concurrency") or 5)
    except (TypeError, ValueError):
        concurrency = 5
    try:
        classify_pages = int(getattr(args, "classify_pages", None) or config.get("classify_pages") or 5)
    except (TypeError, ValueError):
        classify_pages = 5
    classify_pages = max(1, classify_pages)

    from watchdog.pipeline import orchestrate as _orch
    from watchdog.pipeline.ingest_setup import run as is_run

    queue_dir = vault / ".watchdog" / "queue"
    queued = list(queue_dir.glob("*.json")) if queue_dir.exists() else []

    # A prior run may have left a batch un-finalized (e.g. a rate limit hit during synthesis).
    # A new ingest resets those inputs, so ask what to do rather than silently discarding them.
    wipe_pending = True
    if _orch.has_pending_finalization(vault):
        if not queued:
            print(f"\n  {_YELLOW}A previous batch is pending finalization{_RESET}{_DIM} — run "
                  f"{_RESET}{_CYAN}watchdog finalize{_RESET}{_DIM} to complete it.{_RESET}\n")
            return
        p = _orch.pending_finalization(vault)
        bits = []
        if p["docs"]:
            bits.append(f"{p['docs']} document{'s' if p['docs'] != 1 else ''}")
        if p["entities"]:
            bits.append(f"{p['entities']} entit{'ies' if p['entities'] != 1 else 'y'} to synthesize")
        detail = f" {_DIM}({', '.join(bits)}){_RESET}" if bits else ""
        print(f"\n  {_YELLOW}A previous batch is pending finalization{_RESET}{detail}{_DIM}.{_RESET}")
        print(f"  {_DIM}A new ingest resets it — what would you like to do?{_RESET}\n")
        print(f"    {_BOLD}m{_RESET}  merge it into this ingest {_DIM}— extract the new docs, then finalize everything together{_RESET}")
        print(f"    {_BOLD}f{_RESET}  finalize it now, then stop {_DIM}— ingest the new docs afterward{_RESET}")
        print(f"    {_BOLD}d{_RESET}  discard it and ingest only the new docs")
        try:
            choice = input(f"\n  Choice? [{_BOLD}m{_RESET}] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice in ("f", "finalize"):
            out = _run_finalize(vault, post_model, post_effort, post_backend)
            if not (out.get("error") or out.get("briefing_error")):
                print(f"  {_DIM}Now run {_RESET}{_CYAN}watchdog ingest{_RESET}{_DIM} for the queued documents.{_RESET}\n")
            return
        if choice in ("d", "discard"):
            wipe_pending = True
        else:                                  # default: merge (non-destructive)
            wipe_pending = False
            print(f"  {_DIM}Merging the pending batch into this ingest.{_RESET}")

    from watchdog.pipeline import batch_extract
    # A pending batch (#214) must still be checked even with nothing newly queued — mirrors the
    # has_pending_finalization precedent above ("resolve the pending thing first"). force_lock
    # so two concurrent `watchdog ingest` invocations can't both try to collect the same batch —
    # is_run normally only acquires the lock when the queue is non-empty.
    batch_pending = extract_backend == "claude-batch" and batch_extract.read_state(vault) is not None

    result = is_run(vault, wipe_pending=wipe_pending, force_lock=batch_pending)
    if "error" in result:
        sys.exit(f"\n  {_YELLOW}Error:{_RESET} {result['error']}\n")
    if result["total"] == 0 and not batch_pending:
        print(f"\n  {_DIM}Queue is empty — nothing to ingest.{_RESET}")
        print(f"  Run {_CYAN}watchdog chew{_RESET}{_DIM} to process documents in _INCOMING/ first.{_RESET}\n")
        return

    q = len(result["queue_files"])
    if q:
        print(f"\n  {_BOLD}{q} document{'s' if q != 1 else ''}{_RESET} ready for extraction")
    elif batch_pending:
        print(f"\n  {_DIM}Checking on a pending batch extraction…{_RESET}")

    pinned_skill = _resolve_pinned_skill(args, config)
    if pinned_skill:
        print(f"  {_DIM}Skill pinned:{_RESET} {_CYAN}{Path(pinned_skill).stem}{_RESET}{_DIM} — classification skipped.{_RESET}")

    if classify_backend == "claude-batch" or post_backend == "claude-batch":
        sys.exit(f"\n  {_YELLOW}Error:{_RESET} claude-batch is only valid for extractor_model, "
                 f"not classifier_model/finalizer_model.\n")
    if extract_backend == "claude-batch":
        if not pinned_skill:
            sys.exit(f"\n  {_YELLOW}Error:{_RESET} claude-batch requires a pinned skill — "
                     f"classification isn't batchable.\n  Use {_CYAN}--skill{_RESET} or set "
                     f"{_CYAN}default_skill{_RESET} via {_CYAN}watchdog configure{_RESET}.\n")
        if a["mode"] != "api-key":
            sys.exit(f"\n  {_YELLOW}Error:{_RESET} claude-batch requires api-key auth mode "
                     f"(it needs a metered key) — run {_CYAN}watchdog auth use api-key{_RESET}.\n")

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
    if extract_backend == "claude-batch":
        print(f"\n  {_DIM}claude-batch: sectioned documents (if any) extract via claude-api now; "
              f"the rest submit as one batch and finish later.{_RESET}")
    else:
        print(f"\n  {_DIM}Extracting (≤{concurrency} parallel) — the model is called only for reasoning; "
              f"the pipeline runs in Python.{_RESET}")
        print(f"  {_YELLOW}Large documents can take several minutes each{_RESET}{_DIM} — a long pause on a "
              f"row is normal, not a stall.{_RESET}")
        print(f"  {_DIM}Press {_RESET}{_CYAN}Ctrl+C{_RESET}{_DIM} to stop; finished documents are kept.{_RESET}\n")
    try:
        summary = asyncio.run(orchestrate.run(
            vault, concurrency=concurrency, extract_model=extract_model, post_model=post_model,
            classify_model=classify_model, classify_pages=classify_pages, pinned_skill=pinned_skill,
            extract_effort=extract_effort, post_effort=post_effort,
            extract_backend=extract_backend, post_backend=post_backend,
            classify_backend=classify_backend))
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
    rate_limited = summary.get("rate_limited")
    batch_pending = summary.get("batch_pending")
    n_cancelled = sum(1 for r in summary["results"] if r.get("status") == "cancelled")
    if rate_limited:
        headline = f"{_YELLOW}Ingest paused — rate limit{_RESET}"
    elif cancelled:
        headline = f"{_YELLOW}Ingest stopped{_RESET}"
    elif batch_pending and not summary["results"]:
        # Pure submit-and-exit or still-processing (#214) — nothing extracted *this run*, so
        # "Ingest complete 0 extracted" would read as if nothing happened.
        headline = f"{_YELLOW}Batch extraction pending{_RESET}"
    else:
        headline = f"{_GREEN}Ingest complete{_RESET}"
    print(f"\n  {headline}  {_BOLD}{ext}{_RESET} extracted"
          f"{f', {skip} skipped' if skip else ''}{f', {fail} failed' if fail else ''}"
          f"{f', {n_cancelled} not started' if n_cancelled else ''}\n")
    if rate_limited and summary.get("stop_message"):
        print(f"  {_DIM}{summary['stop_message']}{_RESET}\n")
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
    quarantined = summary.get("quarantined", 0)
    if quarantined:
        print(f"\n  {_YELLOW}{quarantined} document{'s' if quarantined != 1 else ''} need attention{_RESET}"
              f"{_DIM} in {_RESET}{_CYAN}queue/_failed/{_RESET}{_DIM} — run {_RESET}"
              f"{_CYAN}watchdog requeue{_RESET}{_DIM} to retry {'them' if quarantined != 1 else 'it'}.{_RESET}")
    pi_error = summary.get("post_ingest_error") or (summary.get("post_ingest") or {}).get("error")
    if pi_error:
        print(f"\n  {_YELLOW}Post-processing didn't finish{_RESET}{_DIM} — {pi_error}.{_RESET}")
        print(f"  {_DIM}Documents are saved with their extracted claims; run {_RESET}"
              f"{_CYAN}watchdog finalize{_RESET}{_DIM} to complete synthesis + the briefing.{_RESET}")
    usage = summary.get("usage")
    if usage:
        cost = f" · ~${usage['cost_usd']:.4f}" if usage.get("cost_usd") else ""
        print(f"  {_DIM}{ext} doc{'s' if ext != 1 else ''} · "
              f"{usage['input_tokens']:,} in / {usage['output_tokens']:,} out tokens{cost}{_RESET}")
    if batch_pending:
        print(f"\n  {_DIM}A batch extraction is in flight — re-run {_RESET}{_CYAN}watchdog ingest{_RESET}"
              f"{_DIM} later to check on it and collect results.{_RESET}\n")
    elif cancelled:
        print(f"\n  {_DIM}Re-run {_RESET}{_CYAN}watchdog ingest{_RESET}{_DIM} to process the remaining documents.{_RESET}\n")
    else:
        print(f"\n  {_DIM}Open a fresh Claude Code session to ask investigation questions.{_RESET}\n")


def cmd_finalize(args) -> None:
    """Complete post-ingest (synthesis + timeline + briefing) for an already-extracted batch.

    `watchdog ingest` finalizes automatically at the end; run this when a rate limit or
    interrupt stopped post-processing before it finished, so the batch isn't left half-done."""
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")

    from watchdog.pipeline import orchestrate
    if not orchestrate.has_pending_finalization(vault):
        print(f"\n  {_DIM}Nothing to finalize — run {_RESET}{_CYAN}watchdog ingest{_RESET}{_DIM} first.{_RESET}\n")
        return

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
    post_backend, post_model = _resolve_stage(
        getattr(args, "finalizer_model", None), config.get("finalizer_model"), default="haiku")
    post_effort = _effort(getattr(args, "finalizer_effort", None), config.get("finalizer_effort"))

    _run_finalize(vault, post_model, post_effort, post_backend)


def _run_finalize(vault: Path, post_model: str, post_effort: str | None = None,
                  post_backend: str | None = None) -> dict:
    """Acquire the ingest lock, run post-ingest over the pending batch, print the outcome."""
    from watchdog.pipeline import orchestrate
    lock = vault / ".watchdog" / "Registry" / ".ingest-lock"
    if lock.exists():
        sys.exit(f"\n  {_YELLOW}Error:{_RESET} an ingest or finalize is already running (lock present).\n"
                 f"  If stale, run {_CYAN}watchdog unlock{_RESET}.\n")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("pid: cli-finalize\n", encoding="utf-8")
    print(f"\n  {_DIM}Finalizing — synthesis + timeline + briefing (model: {_RESET}"
          f"{_BOLD}{post_model}{_RESET}{_DIM}).{_RESET}")
    try:
        import asyncio
        out = asyncio.run(orchestrate.finalize(vault, post_model=post_model, post_effort=post_effort,
                                               post_backend=post_backend))
    finally:
        lock.unlink(missing_ok=True)

    if out.get("error") or out.get("briefing_error"):
        reason = out.get("error") or out.get("briefing_error")
        print(f"\n  {_YELLOW}Finalize didn't finish{_RESET}{_DIM} — {reason}.{_RESET}")
        print(f"  {_DIM}Re-run {_RESET}{_CYAN}watchdog finalize{_RESET}{_DIM} once the limit resets.{_RESET}\n")
        return out
    n = out.get("synthesized", 0)
    parts = [f"{_BOLD}{n}{_RESET} entit{'ies' if n != 1 else 'y'} synthesized"]
    if out.get("briefing"):
        parts.append(f"briefing {_CYAN}{out['briefing']}{_RESET}")
    print(f"\n  {_GREEN}Finalized{_RESET}  " + ", ".join(parts) + "\n")
    return out


def cmd_requeue(args) -> None:
    """Move documents from queue/_failed/ back into the active queue for re-ingest."""
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")
    failed_dir = vault / ".watchdog" / "queue" / "_failed"
    files = sorted(failed_dir.glob("*.json")) if failed_dir.exists() else []
    if not files:
        print(f"\n  {_DIM}No documents in {_RESET}{_CYAN}queue/_failed/{_RESET}{_DIM} — nothing to requeue.{_RESET}\n")
        return
    queue_dir = vault / ".watchdog" / "queue"
    for f in files:
        f.replace(queue_dir / f.name)
    n = len(files)
    print(f"\n  {_GREEN}Requeued {_BOLD}{n}{_RESET}{_GREEN} document{'s' if n != 1 else ''}{_RESET}"
          f"{_DIM} — run {_RESET}{_CYAN}watchdog ingest{_RESET}{_DIM} to retry.{_RESET}\n")


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


def cmd_guided(args) -> None:
    """Bare `watchdog` inside a project: walk the pipeline in order — context → chew → ingest —
    offering each stage that has pending work and falling through to the next when declined (#132).

    Each stage self-skips when its directory/queue is empty, so the flow always lands on the
    sensible next step. Reuses the same prompt helpers as the individual commands for consistency.
    """
    from watchdog.pipeline.preprocess_batch import find_files
    from watchdog.pipeline import research
    vault = Path(".").resolve()
    did_offer = False

    # Crash-recovery: a research session that died before its post-flight download left URLs queued.
    if research.pending_count(vault):
        print()
        _warn_pending_research(vault)

    # 1. Context — when _CONTEXT/ has files and context.md hasn't been seeded yet. (Re-seeding an
    #    existing context.md stays the explicit `watchdog context`; the guided flow won't nag.)
    #    cmd_context offers to open Claude Code; accepting replaces this process (os.execvp), so
    #    the walk ends there — seed context, then re-run `watchdog` for chew/ingest. Declining
    #    returns here and falls through to chew below.
    context_dir = vault / "_CONTEXT"
    context_files = find_files([context_dir]) if context_dir.is_dir() else []
    if context_files and not (vault / "context.md").exists():
        did_offer = True
        cmd_context(args)

    # 2. Chew — when _INCOMING/ has files. _run_preprocess prompts and chews; the ingest offer
    #    below then picks up whatever it queued.
    incoming = vault / "_INCOMING"
    if incoming.is_dir() and find_files([incoming]):
        did_offer = True
        _run_preprocess(vault, confirm=True, show_ingest_hint=False)

    # 3. Ingest — when the queue has documents ready.
    if _count_queued(vault):
        did_offer = True
        _offer_ingest(args, vault)
        return

    if not did_offer:
        print(f"\n  {_DIM}Nothing pending — drop files in {_RESET}{_CYAN}_INCOMING/{_RESET}{_DIM} "
              f"then run {_RESET}{_CYAN}watchdog{_RESET}{_DIM}, or {_RESET}{_CYAN}watchdog status{_RESET}"
              f"{_DIM} for details.{_RESET}")
        # With nothing to process, surface standing leads as a nudge (deterministic, no model).
        from watchdog.pipeline import leads
        n = leads.total(leads.scan(vault))
        if n:
            print(f"  {_YELLOW}⚠{_RESET}  {_BOLD}{n}{_RESET} open lead{'s' if n != 1 else ''} "
                  f"{_DIM}— run {_RESET}{_CYAN}watchdog leads{_RESET}{_DIM} to review{_RESET}")
        print()


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
