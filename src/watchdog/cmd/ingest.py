"""Document pipeline commands: chew, ingest, queue-status, pre-flight, post-flight."""

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from watchdog import interactive
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
# `xhigh`/`max` (#518) are accepted here syntactically; `model_client._resolve_effort` is the
# source of truth for which provider/model actually supports which level, and rejects an
# unsupported one loudly — at any level, not just xhigh/max (D158).
_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

# Metered-provider default (#493) — clears the lowest published paid-tier RPM floor across
# Anthropic/OpenAI/DeepSeek with margin, given how long an extraction call actually takes (D162).
# `watchdog setup`/`watchdog auth` override this to `auth._SUBSCRIPTION_CONCURRENCY` (3) in
# config when ingestion stays on a Claude subscription, which throttles well below this value.
_DEFAULT_EXTRACT_CONCURRENCY = 20

# --wait (#271): cushion past the provider's reported reset time, since a resume attempted
# right at the boundary can still land inside the window.
_WAIT_BUFFER_SECONDS = 30
# Fallback sleep when RateLimitError carried no `resets_at` — true for the claude-api and
# OpenAI-compatible backends, which don't report a reset timestamp (only claude-agent-sdk does).
_WAIT_FALLBACK_SECONDS = 15 * 60
# Sleep in chunks under ingest_setup.STALE_SECONDS (30 min), refreshing the lock after each —
# otherwise a wait longer than the staleness window would make a live --wait run look abandoned.
_WAIT_REFRESH_SECONDS = 20 * 60


def _effort(flag_val, config_val, *, default=None, backend=None, model=None):
    """Resolve a per-stage effort knob (flag > config > model-aware default > unset); validated.

    `flag_val`/`config_val` are explicit choices — the user typed `--extractor-effort` or ran
    `watchdog configure`, so an unsupported level for the resolved model fails loud downstream in
    `model_client._resolve_effort`, correctly, since it's exactly what was asked for. `default`
    (extractor_effort's `medium`, D26) is different: nothing the user touched, so it's applied
    only when `backend`/`model` actually supports it — otherwise routing a stage to a model with
    no effort control (e.g. Haiku) would turn a setting nobody set into a hard failure (#518)."""
    e = flag_val or config_val
    if e is not None:
        if e not in _EFFORT_LEVELS:
            sys.exit(f"Error: unknown effort '{e}' — choose {', '.join(_EFFORT_LEVELS)}")
        return e
    if default is None:
        return None
    from watchdog.model_client import effort_supported
    return default if effort_supported(backend, model, default) else None


# Per-stage finalizer overrides (#433): each key pair below routes just that post-ingest stage
# to a different model than the aggregate --finalizer-model, falling back to it when unset.
_FINALIZER_STAGES = ("reconciliation", "synthesis", "timeline", "briefing")


def _resolve_finalizer_overrides(args, config: dict, post_backend: str | None, post_model: str) -> dict:
    """Resolve the four per-stage `--finalizer-<stage>-model` overrides (#433) — reconciliation,
    synthesis, timeline, briefing — each via the same `[backend:]model` parsing `_resolve_stage`
    already does for the aggregate `--finalizer-model`. `default` is the already-resolved
    finalizer stage itself, so an unset override falls back to exactly what the rest of
    post-ingest runs on, not a hardcoded tier.

    Returns a dict with `<stage>_model`/`<stage>_backend` keys for every stage — the shape
    `orchestrate.finalize`'s `finalizer_overrides` parameter expects directly."""
    default = f"{post_backend}:{post_model}" if post_backend else post_model
    overrides: dict = {}
    for stage in _FINALIZER_STAGES:
        flag_attr = f"finalizer_{stage}_model"
        backend, model = _resolve_stage(
            getattr(args, flag_attr, None), config.get(flag_attr), default=default)
        overrides[f"{stage}_backend"] = backend
        overrides[f"{stage}_model"] = model
    return overrides


def _resolve_stage(flag_val, config_val, default="sonnet") -> tuple[str | None, str]:
    """Resolve a stage's `[backend:]model` knob into (backend, model) (#125).

    Plain `sonnet`/`opus`/`haiku` → (None, tier): Claude, routed by auth mode (unchanged). A
    `backend:model` form selects a backend explicitly — `claude-api:opus` (a Claude tier), or
    `openai:gpt-5-mini` / `deepseek:deepseek-v4-flash` (a raw provider model id). Carrying both in
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
                     f"or a backend:model form like deepseek:deepseek-v4-flash")
    elif not model:
        sample = "deepseek-v4-flash" if backend == "deepseek" else "gpt-5-mini"
        sys.exit(f"Error: backend '{backend}' needs a model id, e.g. {backend}:{sample}")
    return backend, model


def _fmt_tokens(n: int) -> str:
    """Compact token count for the pre-flight estimate line — 2.1M / 410K / 950."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def _format_cost_estimate(est: dict) -> str:
    """Render `ingest_setup.cost_estimate`'s result as the pre-flight estimate line (#269),
    e.g. '18 documents · ~410 pages · est. ~2.1M tokens in (~$9-12 based on your last 3 runs)'."""
    n, pages = est["documents"], est["pages"]
    line = (f"  {_BOLD}{n}{_RESET} document{'s' if n != 1 else ''} · "
            f"~{pages} page{'s' if pages != 1 else ''} · "
            f"est. ~{_fmt_tokens(est['est_tokens'])} tokens in")
    if est["cost_low"] is not None:
        low, high = round(est["cost_low"]), round(est["cost_high"])
        cost = f"${low}" if low == high else f"${low}-{high}"
        runs = est["runs_used"]
        run_label = "last run" if runs == 1 else f"last {runs} runs"
        line += f" {_DIM}(~{cost} based on your {run_label}){_RESET}"
    return line


def _format_finalize_estimate(est: dict) -> str:
    """Render `ingest_setup.finalize_cost_estimate`'s result as `watchdog bark --estimate`'s
    pre-flight line (#417), e.g. '3 documents staged · est. ~45K tokens in (~$0.18-0.24 based on
    your last 2 standalone finalizes)'. Costs here run far smaller than ingest's own line (a
    handful of reconciliation/synthesis calls, not a whole document's extraction), so the dollar
    figure keeps two decimal places instead of `_format_cost_estimate`'s whole dollars."""
    n = est["docs"]
    line = (f"  {_BOLD}{n}{_RESET} document{'s' if n != 1 else ''} staged · "
            f"est. ~{_fmt_tokens(est['est_tokens'])} tokens in")
    if est["cost_low"] is not None:
        low, high = round(est["cost_low"], 2), round(est["cost_high"], 2)
        cost = f"${low}" if low == high else f"${low}-{high}"
        runs = est["runs_used"]
        run_label = "last standalone finalize" if runs == 1 else f"last {runs} standalone finalizes"
        line += f" {_DIM}(~{cost} based on your {run_label}){_RESET}"
    return line


def _format_all_models_estimate(rows: list[dict]) -> str:
    """Render `ingest_setup.cost_estimate_all_models`/`finalize_cost_estimate_all_models`'s
    per-model projection (#469) as a single cheapest-first table — one line per catalog model,
    rather than a separate estimate per model per pipeline stage, which is what keeps this
    readable as the catalog grows. `rows` is already sorted by `cost` ascending."""
    if not rows:
        return (f"  {_DIM}Not enough usage history yet to project other models — run an ingest "
                f"or finalize first, then re-run with {_RESET}{_CYAN}--estimate-all{_RESET}"
                f"{_DIM}.{_RESET}")
    name_w = max(len(r["name"]) for r in rows)
    lines = [f"  {_DIM}Projected list price by model, cheapest first {_RESET}{_DIM}(every input "
             f"token priced as a cache miss — a rough ceiling, not what you'd actually pay with "
             f"caching):{_RESET}"]
    for r in rows:
        cost = r["cost"]
        cost_s = f"${cost:.4f}" if cost < 1 else f"${cost:,.2f}"
        lines.append(f"    {r['name']:<{name_w}}  {_DIM}{r['provider']:<10}{_RESET}  {cost_s}")
    return "\n".join(lines)


def _effective_extract_backend(extract_backend: str | None, auth_mode: str) -> str:
    """The backend that will actually serve extraction calls when `extract_backend` is unset
    (plain sonnet/opus/haiku) — mirrors `model_client`'s own subscription/api-key routing, so the
    cost estimate knows whether a dollar figure means anything (#269)."""
    return extract_backend or ("claude-agent-sdk" if auth_mode == "subscription" else "claude-api")


def _format_models_line(classify_backend, classify_model, extract_backend, extract_model,
                        post_backend, post_model, extract_effort=None, post_effort=None,
                        finalizer_overrides=None, concurrency=None, is_dig=False) -> str:
    """Which model runs each ingest stage — printed alongside the cost estimate before an
    ingest starts, so a run under a non-default provider (#325) is obvious up front instead of
    the older generic 'Using the configured provider(s).' notice. Stage names are padded to a
    common width so the model values line up in a column (#411); classify has no effort knob
    (D36), so only the extractor/finalizer rows can carry an "(effort: …)" suffix.

    `finalizer_overrides` (#433) adds one extra row per post-ingest stage whose resolved
    model/backend differs from the aggregate finalizer row — an unoverridden stage (the common
    case) stays folded into the single "finalizer" line rather than repeating it four times.

    `concurrency` (#456), when given, is the raw `--concurrency` value the user explicitly
    passed — omitted (None) when a run falls back to the config/default value, so this row
    only appears when it actually reflects a deliberate choice rather than a fixed default.

    `is_dig` (#456) drops the finalizer row(s) entirely: `watchdog dig` always stops before
    finalization in the same run (unlike the bare guided walk or the deprecated `ingest`, both of
    which finalize inline), so which model would run it is irrelevant noise on this run's summary."""
    def label(backend, model):
        return f"{backend}:{model}" if backend else model
    stages = [("classifier", classify_backend, classify_model, None),
              ("extractor", extract_backend, extract_model, extract_effort)]
    if not is_dig:
        stages.append(("finalizer", post_backend, post_model, post_effort))
        for stage in _FINALIZER_STAGES:
            b = (finalizer_overrides or {}).get(f"{stage}_backend", post_backend)
            m = (finalizer_overrides or {}).get(f"{stage}_model", post_model)
            if (b, m) != (post_backend, post_model):
                stages.append((f"finalizer:{stage}", b, m, post_effort))
    width = max(len(name) for name, *_ in stages)
    if concurrency is not None:
        width = max(width, len("concurrency"))
    lines = []
    for name, b, m, effort in stages:
        suffix = f" {_DIM}(effort: {effort}){_RESET}" if effort else ""
        lines.append(f"  {_DIM}{name:<{width}}{_RESET} {_CYAN}{label(b, m)}{_RESET}{suffix}")
    if concurrency is not None:
        lines.append(f"  {_DIM}{'concurrency':<{width}}{_RESET} {_CYAN}{concurrency}{_RESET}")
    return "\n".join(lines)


def _preview_ingest(vault: Path, args) -> tuple[str, str] | None:
    """Read-only preview of what an ingest run would do — the doc/page/token cost estimate and
    which models would run each stage — shown before any ingest confirm prompt, mirroring
    `--estimate`'s lock-free scan (#269, #325). None when the queue is empty."""
    from watchdog.pipeline.ingest_setup import scan_queue, cost_estimate
    from watchdog.cmd.auth import resolve_auth
    from watchdog.cmd.base import CONFIG_FILE
    queue_files = scan_queue(vault)
    if not queue_files:
        return None
    config: dict = {}
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    extract_backend, extract_model = _resolve_stage(
        getattr(args, "extractor_model", None), config.get("extractor_model"))
    post_backend, post_model = _resolve_stage(
        getattr(args, "finalizer_model", None), config.get("finalizer_model"), default="haiku")
    classify_backend, classify_model = _resolve_stage(
        getattr(args, "classifier_model", None), config.get("classifier_model"), default="haiku")
    extract_effort = _effort(getattr(args, "extractor_effort", None), config.get("extractor_effort"),
                             default="medium", backend=extract_backend, model=extract_model)
    post_effort = _effort(getattr(args, "finalizer_effort", None), config.get("finalizer_effort"))
    finalizer_overrides = _resolve_finalizer_overrides(args, config, post_backend, post_model)

    auth_mode = resolve_auth()["mode"] if extract_backend is None else None
    est = cost_estimate(vault, queue_files, _effective_extract_backend(extract_backend, auth_mode))
    models_line = _format_models_line(classify_backend, classify_model,
                                      extract_backend, extract_model, post_backend, post_model,
                                      extract_effort, post_effort, finalizer_overrides,
                                      concurrency=getattr(args, "concurrency", None),
                                      is_dig=getattr(args, "command", None) == "dig")
    return _format_cost_estimate(est), models_line


def _pick_skill_interactive() -> str | None:
    """Numbered picker for `watchdog dig --skill` (no value), drawn from the global
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
    resolved = skills_catalog.resolve(value)
    if resolved:
        return resolved
    avail = ", ".join(skills_catalog.catalog()) or "(none available)"
    sys.exit(f"\n  {_YELLOW}Error:{_RESET} record skill {_BOLD}{value.removesuffix('.md')}{_RESET} "
             f"not found (not a known skill or a file path).\n  Available: {_CYAN}{avail}{_RESET}\n")


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
                print(f"\n  {_DIM}_INCOMING/ is empty — {queued} file{'s' if queued != 1 else ''} ready. Run {_RESET}{_CYAN}watchdog{_RESET}{_DIM}.{_RESET}\n")
            else:
                print(f"\n  {_DIM}_INCOMING/ is empty — nothing to chew.{_RESET}\n")
            return
        n = len(files)
        label = f"{n} file{'s' if n != 1 else ''}"
        if not interactive.confirm(f"\n  Found {_BOLD}{label}{_RESET} in _INCOMING/. Chew now?", default=True):
            return
    run_ingest(vault, workers=workers, chunk_workers=chunk_workers, show_ingest_hint=show_ingest_hint)


def cmd_chew(args) -> dict | None:
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
        _notify("Watchdog", f"{new_queued} file{'s' if new_queued != 1 else ''} chewed — run watchdog dig.")
        return _offer_ingest(args, vault)
    return None


def _public_records_warning(n_docs: int) -> str:
    """The README's `## Public records only` warning (README.md:13-17), reworded for the
    terminal gate at the point of no return (#426). Wording tracks the README section so the
    two never drift.

    No trailing `\\n` on the returned string (#456 follow-up): the caller's `print()` already
    appends one, and `interactive.pick()`'s own leading blank line supplies the separator before
    the menu — an embedded trailing newline on top of both stacked into a double blank line."""
    return (
        f"\n  {_YELLOW}⚠{_RESET}  {_BOLD}Public records only{_RESET}\n\n"
        "  The extracted text of every queued document will be sent to a\n"
        "  cloud AI model. This cannot be undone. Use Watchdog only for\n"
        "  documents that are public, or presumptively public — never for\n"
        "  confidential source material, leaks, or anything that could\n"
        "  identify a source.\n\n"
        f"  {_BOLD}{n_docs}{_RESET} document{'s' if n_docs != 1 else ''} will be sent to the model."
    )


def _confirm_public_records(n_docs: int, *, skip_warning: bool = False) -> bool:
    """The point-of-no-return gate before any ingest/extract that will call the model (#426):
    shows the README's 'Public records only' warning and requires an explicit acknowledgement,
    defaulting to Acknowledge — the standing warning is the real safeguard; a Cancel default
    would just train people to reflexively pick past it. This replaces the old generic
    'Ingest now?' pick at both call sites rather than stacking a second prompt.

    `n_docs == 0` means this run makes no new model call (e.g. only checking on a pending
    batch extraction) — nothing to warn about, so it's a silent no-op.

    `--skip-warning` (for repeated/scripted runs on an already-vetted corpus) suppresses the
    interactive pause but still prints a one-line notice, so a skipped run is never silent
    about what it sent.
    """
    if n_docs == 0:
        return True
    if skip_warning:
        print(f"\n  {_DIM}Sending {_RESET}{_BOLD}{n_docs}{_RESET}{_DIM} document"
              f"{'s' if n_docs != 1 else ''} to a cloud AI model.{_RESET}")
        return True
    print(_public_records_warning(n_docs))
    return interactive.pick(["Acknowledge and ingest", "Cancel"], 0) == 0


def _offer_ingest(args, vault: Path) -> dict | None:
    """After chew, offer to run ingest right away; print the command hint if declined.

    Returns the ingest summary (or None when declined) so callers can propagate it to
    `exit_code_for` — a rate limit that pauses the run reached from `watchdog` or `watchdog
    chew` has to surface as exit 2 the same way a bare `watchdog dig` does (#499)."""
    preview = _preview_ingest(vault, args)
    if preview:
        estimate_line, models_line = preview
        print(estimate_line)
        print(models_line)
    n_docs = _count_queued(vault)
    if _confirm_public_records(n_docs, skip_warning=getattr(args, "skip_warning", False)):
        return cmd_ingest(args, confirm=False, skip_preview=True)
    else:
        # No leading blank line here — pick()'s own close-out already leaves one (#411).
        # Reached from `watchdog chew` (manual control) or the guided `watchdog` walk (bare) —
        # point back at whichever got here, not the retired `watchdog ingest` (#441, D138).
        next_cmd = "watchdog dig" if getattr(args, "command", None) == "chew" else "watchdog"
        print(f"  Run:  {_CYAN}{next_cmd}{_RESET}\n")


def _wait_seconds(resets_at: int | None) -> tuple[int, bool]:
    """Seconds to sleep before resuming after a rate limit, and whether that's an exact reset
    time (vs. the fallback used when the error carried no `resets_at`)."""
    if resets_at:
        return max(1, int(resets_at - time.time()) + _WAIT_BUFFER_SECONDS), True
    return _WAIT_FALLBACK_SECONDS, False


def _wait_for_rate_limit(lock_file: Path, resets_at: int | None) -> None:
    """Sleep through a rate limit, refreshing the held ingest lock so it doesn't go stale."""
    from watchdog.pipeline.locks import refresh_lock
    sleep_s, exact = _wait_seconds(resets_at)
    wake_at = datetime.now().astimezone() + timedelta(seconds=sleep_s)
    note = "" if exact else f"{_DIM} (estimated — the provider didn't report a reset time){_RESET}"
    print(f"\n  {_YELLOW}Rate limit{_RESET}{_DIM} — resuming at{_RESET} {_BOLD}{wake_at:%H:%M}{_RESET}"
          f"{note}{_DIM}. {_RESET}{_CYAN}Ctrl+C{_RESET}{_DIM} to stop; finished documents are saved.{_RESET}\n")
    remaining = sleep_s
    while remaining > 0:
        chunk = min(remaining, _WAIT_REFRESH_SECONDS)
        time.sleep(chunk)
        remaining -= chunk
        refresh_lock(lock_file)


def _merge_summary(base: dict | None, new: dict) -> dict:
    """Fold one --wait iteration's summary into the running total.

    `results` are deduped by sha256 (a later cycle's outcome for the same doc replaces an
    earlier cycle's "cancelled" stub); extracted/skipped/failed/usage are summed. Every other
    field reflects only the latest iteration (rate_limited/cancelled/quarantined/post_ingest
    are point-in-time or already vault-wide state, not per-run deltas)."""
    if base is None:
        return new
    merged = dict(new)
    # A doc cancelled by one cycle's rate limit keeps its queue file and is retried by the
    # next cycle — key by sha256 so that cycle's real outcome replaces the stale "cancelled"
    # stub instead of both landing in the list (which would double-count it in the printed
    # summary, e.g. as both extracted and "not started").
    by_sha = {r.get("sha256"): r for r in base["results"]}
    by_sha.update({r.get("sha256"): r for r in new["results"]})
    merged["results"] = list(by_sha.values())
    merged["extracted"] = base["extracted"] + new["extracted"]
    merged["skipped"] = base["skipped"] + new["skipped"]
    merged["failed"] = base["failed"] + new["failed"]
    b_usage, n_usage = base.get("usage"), new.get("usage")
    if b_usage or n_usage:
        b_usage, n_usage = b_usage or {}, n_usage or {}
        merged["usage"] = {
            "input_tokens": b_usage.get("input_tokens", 0) + n_usage.get("input_tokens", 0),
            "output_tokens": b_usage.get("output_tokens", 0) + n_usage.get("output_tokens", 0),
            "cost_usd": (b_usage.get("cost_usd") or 0) + (n_usage.get("cost_usd") or 0),
        }
    return merged


def _forced_overwrite_targets(vault: Path, summary: dict) -> list[tuple[str, str]]:
    """Which of this run's force-re-extracted documents (#424) already have a committed vault
    note — the set the overwrite-warning gate must list and confirm before finalize recommits
    them. Returns (sha256, document_note) pairs, in `summary["results"]` order."""
    documents_path = vault / ".watchdog" / "registry" / "documents.json"
    if not documents_path.exists():
        return []
    try:
        committed = json.loads(documents_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for r in summary.get("results", []):
        entry = committed.get(r.get("sha256"))
        if r.get("status") == "ok" and entry:
            out.append((r["sha256"], entry.get("document_note") or r["sha256"][:12]))
    return out


def _handle_force_gate(vault: Path, summary: dict, post_model: str,
                       post_effort: str | None, post_backend: str | None,
                       skip_briefing: bool = False, finalizer_overrides: dict | None = None) -> None:
    """After a forced re-extraction (`ingest --force`, or `dig --force`) with finalize held off,
    warn before finalize recommits any document that was already in the vault — replacing an
    already-committed note/registry entry is genuinely destructive, unlike the routine ingest
    confirm, so this defaults to Cancel (#424). On cancel the re-staged extraction is left
    pending, exactly as `dig --force` would leave it, for a later `watchdog bark` to pick up;
    nothing is rolled back.

    When force touched no already-committed document (a queue of only new documents, extracted
    with finalize held off purely because `--force` was passed), there is nothing to warn about —
    finalize just runs.
    """
    targets = _forced_overwrite_targets(vault, summary)
    if targets:
        n = len(targets)
        print(f"\n  {_YELLOW}--force replaces {n} note{'s' if n != 1 else ''} already in the vault:{_RESET}")
        for _, note in targets:
            print(f"    {_CYAN}{note}{_RESET}")
        if not interactive.confirm("\n  Overwrite and finalize?", default=False):
            print(f"\n  {_DIM}Cancelled — nothing overwritten. The re-extracted batch is staged; "
                  f"run {_RESET}{_CYAN}watchdog bark{_RESET}{_DIM} later to complete it.{_RESET}")
            return
    summary["finalize_skipped"] = False
    summary["post_ingest"] = _run_finalize(vault, post_model, post_effort, post_backend,
                                           force_shas=[sha for sha, _ in targets] or None,
                                           skip_briefing=skip_briefing,
                                           finalizer_overrides=finalizer_overrides)


def _resolve_force_selectors(vault: Path, selectors: list[str]) -> list[str]:
    """Resolve each `ingest --force <selector>` argument to a committed document's sha256, against
    `registry/documents.json` (#424). A selector matches by full sha256, an unambiguous sha256
    prefix, its original filename, or its document note (`documents/<slug>` or just `<slug>`).
    Exits with a clear error on no match or an ambiguous prefix — never a silent no-op. Returns
    shas in selector order, de-duplicated (two selectors naming the same document collapse to one)."""
    documents_path = vault / ".watchdog" / "registry" / "documents.json"
    try:
        documents = json.loads(documents_path.read_text(encoding="utf-8")) if documents_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        documents = {}

    resolved: list[str] = []
    for sel in selectors:
        if sel in documents:
            resolved.append(sel)
            continue
        candidates = {sha for sha in documents if sha.startswith(sel)}
        for sha, entry in documents.items():
            note = entry.get("document_note") or ""
            if sel == entry.get("filename") or sel == note or sel == Path(note).name:
                candidates.add(sha)
        if len(candidates) == 1:
            resolved.append(next(iter(candidates)))
        elif len(candidates) > 1:
            sys.exit(f"\n  {_YELLOW}Error:{_RESET} '{sel}' matches more than one committed "
                     f"document — use the full sha256, or an exact filename.\n")
        else:
            sys.exit(f"\n  {_YELLOW}Error:{_RESET} '{sel}' does not match any committed document "
                     f"(by sha256, filename, or note).\n")

    seen: set[str] = set()
    out: list[str] = []
    for sha in resolved:
        if sha not in seen:
            seen.add(sha)
            out.append(sha)
    return out


def _requeue_forced_selectors(vault: Path, selectors: list[str]) -> None:
    """`ingest --force <selector…>` (#424): resolve each selector to a committed document, then
    re-chew its original — which a commit moves out of `.watchdog/staging/<sha>/` into the morgue,
    recorded as `documents.json[sha]["morgue_path"]` — with chew's dedup filter and near-dup
    self-match both bypassed for that sha, so it re-enters `.watchdog/queue/` for the force
    re-extraction that follows. Reuses the real chew/OCR pipeline (`preprocess_batch.run_ingest`)
    rather than duplicating it. A sha whose queue entry already exists (e.g. an earlier `--force`
    run staged it and it was never finalized) is left alone — no need to pay for OCR twice."""
    shas = _resolve_force_selectors(vault, selectors)
    documents_path = vault / ".watchdog" / "registry" / "documents.json"
    documents = json.loads(documents_path.read_text(encoding="utf-8")) if documents_path.exists() else {}
    queue_dir = vault / ".watchdog" / "queue"

    to_requeue: list[tuple[str, Path]] = []
    for sha in shas:
        if (queue_dir / f"{sha}.json").exists():
            continue
        entry = documents.get(sha, {})
        morgue_rel = entry.get("morgue_path")
        morgue_path = (vault / morgue_rel) if morgue_rel else None
        if not morgue_path or not morgue_path.exists():
            name = entry.get("filename", sha[:12])
            sys.exit(f"\n  {_YELLOW}Error:{_RESET} the original for {_CYAN}{name}{_RESET} isn't "
                     f"on disk at its recorded morgue path — cannot re-chew it.\n")
        to_requeue.append((sha, morgue_path))

    if not to_requeue:
        return
    from watchdog.pipeline import preprocess_batch
    names = ", ".join(p.name for _, p in to_requeue)
    print(f"\n  {_DIM}Re-chewing from the morgue (dedup bypassed):{_RESET} {_CYAN}{names}{_RESET}"
          f"{_DIM} — local and free, but re-OCR of a big document can take a moment.{_RESET}")
    preprocess_batch.run_ingest(vault, files=[p for _, p in to_requeue], show_ingest_hint=False,
                                force_shas={sha for sha, _ in to_requeue})


def _failed_count(vault: Path) -> int:
    """How many documents are parked in `.watchdog/queue/_failed/` awaiting `watchdog requeue`
    (#406) — shared by every place that needs to know whether an empty active queue also means
    nothing needs attention, or just that a prior run's failures are still sitting there."""
    failed_dir = vault / ".watchdog" / "queue" / "_failed"
    return len(list(failed_dir.glob("*.json"))) if failed_dir.exists() else 0


def _quarantine_notice(n: int) -> str:
    """The '_failed/ needs attention' line (#406), shared by the normal-completion summary,
    the Ctrl+C message, and the empty-queue checks — same wording everywhere a quarantined
    document could otherwise go unmentioned."""
    return (f"{_YELLOW}{n} document{'s' if n != 1 else ''} need attention{_RESET}"
            f"{_DIM} in {_RESET}{_CYAN}queue/_failed/{_RESET}{_DIM} — run {_RESET}"
            f"{_CYAN}watchdog requeue{_RESET}{_DIM} to retry {'them' if n != 1 else 'it'}.{_RESET}")


@contextlib.contextmanager
def _caffeinate():
    """Keep the machine from sleeping for the life of a real ingest run (#415) — unlike a
    network blip a retry can absorb, the OS suspending mid-run kills any active model call
    outright. Never a hard dependency, and a no-op everywhere else (including
    `--estimate`/preview paths, which never reach this) — including when the platform's own
    tool isn't on PATH (Windows has no equivalent worth shelling out to at all).

    macOS: `caffeinate -i -w <pid>` self-terminates if this process dies, so a plain
    `terminate()` in `finally` is enough.

    Linux: `systemd-inhibit` (present wherever systemd is, i.e. most mainstream distros) only
    holds its sleep/idle inhibitor for as long as a command it launches is running — there's no
    "watch this other pid" mode — so it wraps a `sleep infinity` placeholder in its own process
    group (`start_new_session`). Killing just the `systemd-inhibit` process on the way out would
    leave that placeholder as an orphan still running forever, so cleanup signals the whole
    group instead.
    """
    proc = None
    own_group = False
    if sys.platform == "darwin" and shutil.which("caffeinate"):
        try:
            proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            proc = None
    elif sys.platform.startswith("linux") and shutil.which("systemd-inhibit"):
        try:
            proc = subprocess.Popen(
                ["systemd-inhibit", "--what=sleep:idle", "--who=watchdog",
                 "--why=ingest running", "sleep", "infinity"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            own_group = True
        except OSError:
            proc = None
    try:
        yield
    finally:
        # Note: no `return` in here — inside a `finally`, that would silently swallow whatever
        # exception (e.g. the KeyboardInterrupt that stops an ingest) is propagating through.
        if proc is not None:
            if own_group:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Same target as the initial signal above (#437 review) — SIGKILL-ing only the
                # systemd-inhibit process itself, not its group, could leave the `sleep infinity`
                # placeholder it launched running as an orphan.
                if own_group:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    proc.kill()
                proc.wait(timeout=5)


def _requeue_failed(vault: Path) -> int:
    """Move every document from `queue/_failed/` back into the active queue. Returns how many
    were moved (0 if none were waiting) — the mutation behind `watchdog requeue`, also reused
    by `cmd_ingest`'s empty-queue prompt (#406) so accepting it doesn't duplicate the move."""
    failed_dir = vault / ".watchdog" / "queue" / "_failed"
    files = sorted(failed_dir.glob("*.json")) if failed_dir.exists() else []
    if not files:
        return 0
    queue_dir = vault / ".watchdog" / "queue"
    for f in files:
        f.replace(queue_dir / f.name)
    return len(files)


def cmd_ingest(args, *, confirm: bool = True, skip_preview: bool = False,
              non_interactive: bool = False) -> dict | None:
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")

    # This function backs three CLI surfaces: the deprecated `ingest` (full pipeline),
    # `dig` (extract only, via cmd_extract setting no_finalize), and the guided `watchdog`
    # walk (bare, via _offer_ingest). "Run it again" hints below point at whichever of those
    # got the caller here, rather than the retired `watchdog ingest` (#441, D138).
    pipeline_hint = "watchdog dig" if getattr(args, "command", None) == "dig" else "watchdog"
    is_dig = pipeline_hint == "watchdog dig"

    raw_force = getattr(args, "force", False)
    if isinstance(raw_force, list):
        force = True
        force_selectors = raw_force
    else:
        force = bool(raw_force)
        force_selectors = []
    skip_briefing = getattr(args, "skip_briefing", False)
    # --estimate promises "no lock, no confirm, no extraction" — read-only, same for its
    # --estimate-all sibling (#469). Re-queueing named documents is a real mutation (moves the
    # morgue original into staging, writes a queue file), so it must not run under --estimate;
    # bare --force --estimate is unaffected, since it has no selectors to re-queue in the first
    # place (#424).
    is_estimate = getattr(args, "estimate", False) or getattr(args, "estimate_all", False)
    if force_selectors and is_estimate:
        print(f"\n  {_DIM}--estimate is read-only — the named document(s) are not re-queued; "
              f"this estimate reflects the current queue only.{_RESET}")
    elif force_selectors:
        _requeue_forced_selectors(vault, force_selectors)

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

    from watchdog.cmd.auth import resolve_auth
    from watchdog.model_client import CLAUDE_BACKENDS

    if is_estimate:
        from watchdog.pipeline.ingest_setup import scan_queue, cost_estimate, cost_estimate_all_models
        queue_files = scan_queue(vault)
        if not queue_files:
            failed = _failed_count(vault)
            if failed:
                # --estimate is read-only (#406): say what's blocking it rather than requeueing
                # for the user, unlike the interactive bare-ingest prompt below.
                print(f"\n  {_quarantine_notice(failed)}{_DIM} Nothing else is queued.{_RESET}\n")
            else:
                print(f"\n  {_DIM}Queue is empty — nothing to estimate.{_RESET}")
                print(f"  Run {_CYAN}watchdog chew{_RESET}{_DIM} to process documents in _INCOMING/ first.{_RESET}\n")
            return
        # Claude's auth mode only matters for the estimate when the extractor is actually
        # routed to Claude (it picks subscription vs api-key pricing) — a stage pinned to
        # another provider needs no Claude auth at all (#325).
        auth_mode = resolve_auth()["mode"] if extract_backend is None else None
        # cost_estimate sums est_tokens over every queue_files entry unconditionally — it already
        # prices a --force run the same as any other, since it has no notion of a cached artifact
        # to discount (#424 needs no extra handling here).
        est = cost_estimate(vault, queue_files, _effective_extract_backend(extract_backend, auth_mode))
        print(f"\n{_format_cost_estimate(est)}")
        if getattr(args, "estimate_all", False):
            rows = cost_estimate_all_models(vault, est["est_tokens"])
            print(f"\n{_format_all_models_estimate(rows)}")
        print()
        return

    post_backend, post_model = _resolve_stage(
        getattr(args, "finalizer_model", None), config.get("finalizer_model"), default="haiku")
    classify_backend, classify_model = _resolve_stage(
        getattr(args, "classifier_model", None), config.get("classifier_model"), default="haiku")
    finalizer_overrides = _resolve_finalizer_overrides(args, config, post_backend, post_model)

    # Claude auth is only required when at least one stage is actually routed to it — a vault
    # configured entirely on another provider (e.g. all three stages set to gemini:...) must be
    # able to ingest without Claude being configured at all (#325). Includes the per-stage
    # finalizer overrides (#433) — a lone overridden stage still routed to Claude must not let
    # an all-non-Claude aggregate --finalizer-model skip the auth check.
    needs_claude_auth = any(
        b is None or b in CLAUDE_BACKENDS
        for b in (extract_backend, post_backend, classify_backend,
                  *(finalizer_overrides.get(f"{s}_backend") for s in _FINALIZER_STAGES)))
    if needs_claude_auth:
        a = resolve_auth()
        if a["mode"] == "none":
            sys.exit(f"\n  {_YELLOW}Error:{_RESET} {a.get('reason', 'auth not configured')}\n"
                     f"  Run {_CYAN}watchdog setup{_RESET}{_DIM} to choose how to authenticate.{_RESET}\n")
    else:
        a = {"mode": None}

    extract_effort = _effort(getattr(args, "extractor_effort", None), config.get("extractor_effort"),
                             default="medium", backend=extract_backend, model=extract_model)
    post_effort    = _effort(getattr(args, "finalizer_effort", None), config.get("finalizer_effort"))
    try:
        concurrency = int(getattr(args, "concurrency", None) or config.get("extract_concurrency")
                          or _DEFAULT_EXTRACT_CONCURRENCY)
    except (TypeError, ValueError):
        concurrency = _DEFAULT_EXTRACT_CONCURRENCY
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
                  f"{_RESET}{_CYAN}watchdog bark{_RESET}{_DIM} to complete it.{_RESET}\n")
            return
        # A programmatic caller (run_benchmark.py driving cmd_extract directly, not a human at
        # `watchdog dig`) must never block on the merge/discard/finalize pick below — it has no
        # way to answer it (#494). Fail loud instead of hanging on an invisible prompt.
        if non_interactive:
            sys.exit(f"\n  {_YELLOW}Error:{_RESET} a previous batch is pending finalization in "
                     f"this vault — refusing to prompt for a decision in a non-interactive run.\n"
                     f"  Run {_CYAN}watchdog bark{_RESET} to finalize it, or clear the vault's "
                     f"pending state, then retry.\n")
        p = _orch.pending_finalization(vault)
        bits = []
        if p["docs"]:
            bits.append(f"{p['docs']} document{'s' if p['docs'] != 1 else ''}")
        if p["entities"]:
            bits.append(f"{p['entities']} entit{'ies' if p['entities'] != 1 else 'y'} to synthesize")
        detail = f" {_DIM}({', '.join(bits)}){_RESET}" if bits else ""
        print(f"\n  {_YELLOW}A previous batch is pending finalization{_RESET}{detail}{_DIM}.{_RESET}")
        print(f"  {_DIM}A new ingest resets it — what would you like to do?{_RESET}")
        # `dig` never finalizes in the same run it's invoked from — "then finalize everything
        # together" would be wrong there, since merging just carries the old batch's state
        # forward for a later `watchdog bark` (#456). For the same reason, `dig` drops the
        # "finalize it now" choice entirely: dig-by-definition stops before finalization, so
        # offering to finalize inline here would contradict the command it was invoked as (#456).
        merge_label = (
            f"Merge it into this ingest {_DIM}— extract the new docs; a later "
            f"{_RESET}{_CYAN}watchdog bark{_RESET}{_DIM} finalizes both batches together{_RESET}"
            if is_dig else
            f"Merge it into this ingest {_DIM}— extract the new docs, then finalize everything together{_RESET}")
        discard_label = (
            f"Discard it and ingest only the new docs {_DIM}— safe: never touches what's already "
            f"extracted, just clears state kept for a future bark{_RESET}")
        options = [merge_label]
        if not is_dig:
            options.append(
                f"Finalize it now, then stop {_DIM}— real model spend now (reconciliation, synthesis, "
                f"the briefing); ingest the new docs after{_RESET}")
        options.append(discard_label)
        choice = interactive.pick(options, 0, title="Pending batch")
        if choice is interactive.CANCELLED:
            return
        discard_choice = len(options) - 1
        if not is_dig and choice == 1:         # finalize now, then stop
            out = _run_finalize(vault, post_model, post_effort, post_backend,
                                skip_briefing=skip_briefing, finalizer_overrides=finalizer_overrides)
            if not (out.get("error") or out.get("briefing_error")):
                print(f"  {_DIM}Now run {_RESET}{_CYAN}{pipeline_hint}{_RESET}{_DIM} for the queued documents.{_RESET}\n")
            return
        if choice == discard_choice:           # discard
            wipe_pending = True
            print(f"  {_DIM}Discarding the pending batch — ingesting only the new documents.{_RESET}")
        else:                                  # default: merge (non-destructive)
            wipe_pending = False
            print(f"  {_DIM}Merging the pending batch into this ingest.{_RESET}")

    from watchdog.pipeline import batch_extract
    from watchdog.model_client import BATCH_BACKENDS
    # A pending batch (#214, #530) must still be checked even with nothing newly queued — mirrors
    # the has_pending_finalization precedent above ("resolve the pending thing first"). force_lock
    # so two concurrent runs can't both try to collect the same batch —
    # is_run normally only acquires the lock when the queue is non-empty.
    batch_pending = extract_backend in BATCH_BACKENDS and batch_extract.read_state(vault) is not None

    result = is_run(vault, wipe_pending=wipe_pending, force_lock=batch_pending)
    if "error" in result:
        sys.exit(f"\n  {_YELLOW}Error:{_RESET} {result['error']}\n")
    if result["total"] == 0 and not batch_pending:
        failed = _failed_count(vault)
        # #406: an empty active queue with documents parked in _failed/ isn't really "nothing to
        # ingest" — offer to requeue right here rather than send the user hunting for the fix.
        if failed and not non_interactive and interactive.confirm(
                f"\n  {_quarantine_notice(failed)} Nothing else is queued."
                f"{_DIM} Requeue {'them' if failed != 1 else 'it'} and retry now?{_RESET}", default=True):
            _requeue_failed(vault)
            result = is_run(vault, wipe_pending=wipe_pending, force_lock=batch_pending)
            if "error" in result:
                sys.exit(f"\n  {_YELLOW}Error:{_RESET} {result['error']}\n")
        if result["total"] == 0:
            # Re-check rather than trust the `failed` count captured above (#437 review) — a
            # requeue attempt in between (or a concurrent `watchdog requeue`) can have already
            # emptied _failed/, and "run watchdog requeue" would be a dead end at that point.
            if _failed_count(vault):
                print(f"\n  {_DIM}Run {_RESET}{_CYAN}watchdog requeue{_RESET}{_DIM} when ready, then "
                      f"{_RESET}{_CYAN}{pipeline_hint}{_RESET}{_DIM} again.{_RESET}\n")
            else:
                print(f"\n  {_DIM}Queue is empty — nothing to ingest.{_RESET}")
                print(f"  Run {_CYAN}watchdog chew{_RESET}{_DIM} to process documents in _INCOMING/ first.{_RESET}\n")
            return

    q = len(result["queue_files"])
    if q and not skip_preview:
        from watchdog.pipeline.ingest_setup import cost_estimate
        est = cost_estimate(vault, result["queue_files"], _effective_extract_backend(extract_backend, a["mode"]))
        print(f"\n{_format_cost_estimate(est)}")
        print(_format_models_line(classify_backend, classify_model, extract_backend, extract_model,
                                  post_backend, post_model, extract_effort, post_effort,
                                  finalizer_overrides, concurrency=getattr(args, "concurrency", None),
                                  is_dig=is_dig))
    elif batch_pending:
        print(f"\n  {_DIM}Checking on a pending batch extraction…{_RESET}")

    if result.get("backup_dir"):
        rel = Path(result["backup_dir"]).relative_to(vault)
        print(f"  {_DIM}backup: {_CYAN}{rel}{_RESET}{_DIM} — copy files back to undo the discard{_RESET}")

    pinned_skill = _resolve_pinned_skill(args, config)
    if pinned_skill:
        print(f"  {_DIM}Skill pinned:{_RESET} {_CYAN}{Path(pinned_skill).stem}{_RESET}{_DIM} — classification skipped.{_RESET}")

    if (classify_backend in BATCH_BACKENDS or post_backend in BATCH_BACKENDS
            or any(finalizer_overrides.get(f"{s}_backend") in BATCH_BACKENDS for s in _FINALIZER_STAGES)):
        sys.exit(f"\n  {_YELLOW}Error:{_RESET} a batch backend ({', '.join(BATCH_BACKENDS)}) is "
                 f"only valid for extractor_model, not classifier_model/finalizer_model.\n")
    if extract_backend == "claude-batch":
        # No pinned-skill requirement (D144): each document resolves its own skill — sidecar pin,
        # run-wide pin, else one cheap classify call — before the batch is built, so a mixed-type
        # drop batches fine. Classification stays a per-document synchronous call; what is batched
        # is extraction, which is where the 50% discount is worth having.
        if a["mode"] != "api-key":
            sys.exit(f"\n  {_YELLOW}Error:{_RESET} claude-batch requires api-key auth mode "
                     f"(it needs a metered key) — switch to it with {_CYAN}watchdog auth{_RESET}.\n")
    # openai-batch (#530) needs no equivalent check — OpenAI has no subscription auth mode in
    # this codebase, so it's already covered by the ordinary api-key resolution above.
    # The verification pass (#535): flag beats config, config beats off. Off is the default
    # because the pass has not yet been measured for precision on the benchmark corpus — it
    # reliably adds facts, and whether those facts are worth a reporter's attention is exactly
    # the open question (D172).
    verify_flag = getattr(args, "verify", None)
    verify = bool(config.get("verify_extraction", False)) if verify_flag is None else verify_flag
    if verify and extract_backend in BATCH_BACKENDS:
        # Every batch backend, not just claude-batch: the pass re-reads a document immediately
        # after extracting it, out of the prompt cache that extraction call just wrote, and a
        # batch's results come back hours later in a different run with neither of those left.
        # Name whichever turned it on, so the fix the message implies is the one that works —
        # "--verify isn't supported" is unhelpful advice to someone who never typed it.
        source = ("--verify" if verify_flag else f"{_CYAN}verify_extraction{_RESET}")
        sys.exit(f"\n  {_YELLOW}Error:{_RESET} {source} isn't supported with {extract_backend} — "
                 f"the verification pass re-reads a document immediately after extracting it, and "
                 f"a batch's results come back hours later in a separate run.\n"
                 + ("" if verify_flag else
                    f"  Pass {_CYAN}--no-verify{_RESET} for this run, or turn the setting off.\n"))
    wait = getattr(args, "wait", False)
    if wait and extract_backend in BATCH_BACKENDS:
        sys.exit(f"\n  {_YELLOW}Error:{_RESET} --wait isn't supported with {extract_backend} — a "
                 f"batch already runs in the background; re-run {_CYAN}{pipeline_hint}{_RESET} "
                 f"later to collect it.\n")
    no_finalize = getattr(args, "no_finalize", False)
    # `force`/`force_selectors` were already resolved at the top of this function (before the
    # re-queue-from-morgue step, which must run ahead of everything else here). `ingest --force`
    # always extracts with finalize held off, same as `dig --force` (no_finalize) — the
    # difference is what happens after: `cmd_extract` leaves the batch pending for a later
    # `watchdog bark`; plain `ingest --force` gates the overwrite of any already-committed
    # document below, then finalizes itself once confirmed.
    run_skip_finalize = no_finalize or force

    def _release_lock() -> None:
        (vault / ".watchdog" / "registry" / ".ingest-lock").unlink(missing_ok=True)
        (vault / ".watchdog" / "ingest-state.json").unlink(missing_ok=True)

    if confirm:
        if not _confirm_public_records(q, skip_warning=getattr(args, "skip_warning", False)):
            _release_lock()
            # No leading blank line — pick()'s own close-out already leaves one (#411).
            print(f"  When ready, run:  {_CYAN}{pipeline_hint}{_RESET}\n")
            return

    import asyncio
    from watchdog.pipeline import orchestrate
    log_path = vault / "log.md"
    if not log_path.exists():
        log_path.write_text(_render_template("log.md"))

    # The block below is what a user sees right as extraction starts — reached either straight
    # from this function's own "Ingest now?" pick() (confirm=True) or via _offer_ingest's pick()
    # in the caller (confirm=False); either way a picker just closed and already left one blank
    # line, so the first of these notices must not add its own leading "\n" on top of it (#411).
    said_since_pick = False

    def _say_since_pick(msg: str) -> None:
        nonlocal said_since_pick
        print((f"\n{msg}" if said_since_pick else msg))
        said_since_pick = True

    if force:
        _say_since_pick(f"  {_YELLOW}--force{_RESET}{_DIM}: re-extracting even where a cached "
                        f"extraction or a committed vault note already exists.{_RESET}")
    if no_finalize:
        # `no_finalize` is only ever set by `cmd_extract` (dig) — there is no user-facing
        # `--no-finalize` flag to reference here (#456).
        _say_since_pick(f"  {_DIM}Running {_RESET}{_CYAN}watchdog dig{_RESET}{_DIM} and stopping "
                        f"after extraction — run {_RESET}{_CYAN}watchdog bark{_RESET}{_DIM} later "
                        f"to complete the batch.{_RESET}")
    if extract_backend in BATCH_BACKENDS:
        sync_backend = "claude-api" if extract_backend == "claude-batch" else "openai"
        _say_since_pick(f"  {_DIM}{extract_backend}: sectioned documents (if any) extract via "
                        f"{sync_backend} now; the rest submit as one batch and finish later.{_RESET}")
    else:
        _say_since_pick(f"  {_DIM}Extracting (≤{concurrency} parallel) — the model is called only for "
                        f"reasoning; the pipeline runs in Python.{_RESET}")
        print(f"  {_YELLOW}Large documents can take several minutes each{_RESET}{_DIM} — a long pause on a "
              f"row is normal, not a stall.{_RESET}")
        print(f"  {_DIM}Press {_RESET}{_CYAN}Ctrl+C{_RESET}{_DIM} to stop; finished documents are kept.{_RESET}\n")
    lock_file = vault / ".watchdog" / "registry" / ".ingest-lock"
    try:
        summary = None
        # This loop's only exit condition is `iter_summary["rate_limited"]`, which reflects a
        # rate limit hit *during extraction* — finalize (skipped entirely when no_finalize is
        # set) never sets it, so --wait + --no-finalize already stops as soon as the queue
        # drains, with no special-casing needed here (#384).
        with _caffeinate():
            while True:
                iter_summary = asyncio.run(orchestrate.run(
                    vault, concurrency=concurrency, extract_model=extract_model, post_model=post_model,
                    classify_model=classify_model, classify_pages=classify_pages, pinned_skill=pinned_skill,
                    extract_effort=extract_effort, post_effort=post_effort,
                    extract_backend=extract_backend, post_backend=post_backend,
                    classify_backend=classify_backend, wait=wait, skip_finalize=run_skip_finalize,
                    force=force, skip_briefing=skip_briefing, finalizer_overrides=finalizer_overrides,
                    resume_hint=pipeline_hint, verify=verify))
                summary = _merge_summary(summary, iter_summary)
                if not (wait and iter_summary.get("rate_limited")):
                    break
                _wait_for_rate_limit(lock_file, iter_summary.get("rate_limit_resets_at"))
    except KeyboardInterrupt:
        # Fallback only — orchestrate.run normally traps SIGINT itself and returns a
        # cancelled summary. This catches a Ctrl+C in the brief window before/after that,
        # or on platforms where asyncio can't install a SIGINT handler at all (e.g. Windows'
        # Proactor event loop) — there, every interrupt takes this rougher path, and can
        # land mid-write rather than after a document finishes cleanly. It's also the *only*
        # path a Ctrl+C during finalize's sequential post-processing takes (#406): that phase
        # runs after `orchestrate.run`'s own SIGINT handler is torn down, so the interrupt
        # propagates as a real `KeyboardInterrupt` instead of the graceful cancelled-summary
        # path extraction gets — meaning this message, not `_print_ingest_summary`, is the one
        # place that needs to mention a document quarantined earlier in the same run.
        _release_lock()
        print(f"\n  {_YELLOW}Ingest cancelled.{_RESET}{_DIM} Documents that finished before the "
              f"interrupt are saved; the one in progress may be incomplete. Re-run "
              f"{_RESET}{_CYAN}{pipeline_hint}{_RESET}{_DIM} to resume.{_RESET}\n")
        failed = _failed_count(vault)
        if failed:
            print(f"  {_quarantine_notice(failed)}\n")
        sys.exit(130)
    finally:
        _release_lock()
    if force and not no_finalize and summary.get("finalize_skipped") and not summary.get("batch_pending"):
        _handle_force_gate(vault, summary, post_model, post_effort, post_backend,
                           skip_briefing=skip_briefing, finalizer_overrides=finalizer_overrides)
    _print_ingest_summary(summary, pipeline_hint)
    return summary


def _print_ingest_summary(summary: dict, pipeline_hint: str = "watchdog") -> None:
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
        print(f"\n  {_quarantine_notice(quarantined)}")
    pi_error = summary.get("post_ingest_error") or (summary.get("post_ingest") or {}).get("error")
    if pi_error:
        print(f"\n  {_YELLOW}Post-processing didn't finish{_RESET}{_DIM} — {pi_error}.{_RESET}")
        if (summary.get("post_ingest") or {}).get("commit_skipped"):
            # Reconciliation failed before the commit pass (#403 phase 3): nothing was written to
            # the vault yet — the extracted documents are staged and a re-run picks up where it
            # stopped, so don't imply they're already saved.
            print(f"  {_DIM}Nothing was written to the vault yet; re-run {_RESET}"
                  f"{_CYAN}watchdog bark{_RESET}{_DIM} once your rate limit resets to finish the ingest.{_RESET}")
        else:
            print(f"  {_DIM}Documents are saved with their extracted claims; run {_RESET}"
                  f"{_CYAN}watchdog bark{_RESET}{_DIM} to complete synthesis + the briefing.{_RESET}")
    elif (summary.get("post_ingest") or {}).get("briefing_skipped"):
        print(f"\n  {_DIM}Briefing skipped{_RESET} {_DIM}({_RESET}{_CYAN}--skip-briefing{_RESET}"
              f"{_DIM}) — entities synthesized and the timeline rebuilt.{_RESET}")
    usage = summary.get("usage")
    if usage:
        cost = f" · ~${usage['cost_usd']:.4f}" if usage.get("cost_usd") else ""
        print(f"  {_DIM}{ext} doc{'s' if ext != 1 else ''} · "
              f"{usage['input_tokens']:,} in / {usage['output_tokens']:,} out tokens{cost}{_RESET}")
    if batch_pending:
        print(f"\n  {_DIM}A batch extraction is in flight — re-run {_RESET}{_CYAN}{pipeline_hint}{_RESET}"
              f"{_DIM} later to check on it and collect results.{_RESET}\n")
    elif cancelled:
        print(f"\n  {_DIM}Re-run {_RESET}{_CYAN}{pipeline_hint}{_RESET}{_DIM} to process the remaining documents.{_RESET}\n")
    elif summary.get("finalize_skipped"):
        print(f"\n  {_DIM}Extraction staged, post-processing skipped{_RESET} "
              f"{_DIM}({_RESET}{_BOLD}{ext}{_RESET}{_DIM} document{'s' if ext != 1 else ''} on disk).{_RESET}")
        print(f"  {_DIM}Finalize when ready — run it once for the vault as-is, or copy the vault "
              f"folder to try more than one finalizer:{_RESET}")
        print(f"  {_CYAN}watchdog bark{_RESET}\n")
    else:
        print(f"\n  {_DIM}Open a fresh Claude Code session to ask investigation questions.{_RESET}\n")


def exit_code_for(result) -> int:
    """Map a `cmd_extract`/`cmd_finalize` return value to a process exit code (#499 follow-up).

    2 means incomplete but resumable: the run stopped partway, vault state is consistent, and
    re-running continues the work — a rate limit, a pending batch, documents left cancelled/
    unstarted, or post-processing (`bark`) that didn't finish. Quarantined/failed documents
    alone stay 0: the run completed and set them aside for `watchdog requeue`, which is a
    reported outcome, not an incomplete one. Anything that isn't a dict carrying one of these
    keys — `None`, or some other return value — is 0, so this is a no-op for every command that
    doesn't build this summary shape.

    Applied only at the CLI dispatch site (`main()`), never inside `cmd_extract`/`cmd_finalize`
    themselves — programmatic callers (e.g. the benchmark runner) call those directly and must
    keep getting the dict back, not a `SystemExit`."""
    if not isinstance(result, dict):
        return 0
    if result.get("rate_limited") or result.get("batch_pending"):
        return 2
    if result.get("cancelled") or any(r.get("status") == "cancelled" for r in result.get("results", [])):
        return 2
    if result.get("post_ingest_error") or (result.get("post_ingest") or {}).get("error"):
        return 2
    if result.get("error") or result.get("briefing_error"):
        return 2
    return 0


def cmd_extract(args, *, non_interactive: bool = False) -> dict | None:
    """`watchdog dig` (#425, renamed from `extract` in #441/D138) — classify + extract queued
    documents, staging the artifacts, and stop before finalize. A thin wrapper around
    `cmd_ingest` with finalization forced off: inherits the estimate path, cost preview, skill
    pinning, `--wait`, the lock/summary machinery, and the "run watchdog bark next" closing
    message for free.

    `non_interactive` (#494) is for programmatic callers (run_benchmark.py) that have no human to
    answer a prompt: it fails loud (`sys.exit`) instead of blocking on the pending-finalization
    merge/discard pick, and skips the quarantined-documents requeue offer instead of blocking on
    that confirm."""
    args.no_finalize = True
    return cmd_ingest(args, non_interactive=non_interactive)


def cmd_finalize(args) -> dict | None:
    """`watchdog bark` (renamed from `finalize` in #441/D138) — complete post-ingest (entity
    reconciliation + synthesis + timeline + briefing) for an already-extracted batch.

    The guided `watchdog` walk finalizes automatically at the end; run this when a rate limit or
    interrupt stopped post-processing before it finished, so the batch isn't left half-done."""
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")

    from watchdog.pipeline import orchestrate
    if not orchestrate.has_pending_finalization(vault):
        print(f"\n  {_DIM}Nothing to finalize — run {_RESET}{_CYAN}watchdog dig{_RESET}{_DIM} first.{_RESET}\n")
        return

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
    finalizer_overrides = _resolve_finalizer_overrides(args, config, post_backend, post_model)

    if getattr(args, "estimate", False) or getattr(args, "estimate_all", False):
        # Read-only, like ingest/extract's own --estimate (#269, #406) — no lock, no auth
        # requirement beyond what's needed to resolve which backend would actually run.
        from watchdog.pipeline.ingest_setup import finalize_cost_estimate, finalize_cost_estimate_all_models
        from watchdog.cmd.auth import resolve_auth
        auth_mode = resolve_auth()["mode"] if post_backend is None else None
        est = finalize_cost_estimate(vault, _effective_extract_backend(post_backend, auth_mode))
        print(f"\n{_format_finalize_estimate(est)}")
        if getattr(args, "estimate_all", False):
            rows = finalize_cost_estimate_all_models(vault, est["est_tokens"])
            print(f"\n{_format_all_models_estimate(rows)}")
        print()
        return

    # Claude auth is only required when the finalizer is actually routed to it — a stage pinned
    # to another provider must be able to finalize without Claude being configured at all (#325).
    # Includes the per-stage overrides (#433): a lone overridden stage still routed to Claude
    # must not let an all-non-Claude aggregate --finalizer-model skip the auth check.
    from watchdog.model_client import CLAUDE_BACKENDS
    stage_backends = (post_backend, *(finalizer_overrides.get(f"{s}_backend") for s in _FINALIZER_STAGES))
    if any(b is None or b in CLAUDE_BACKENDS for b in stage_backends):
        from watchdog.cmd.auth import resolve_auth
        a = resolve_auth()
        if a["mode"] == "none":
            sys.exit(f"\n  {_YELLOW}Error:{_RESET} {a.get('reason', 'auth not configured')}\n"
                     f"  Run {_CYAN}watchdog setup{_RESET}{_DIM} to choose how to authenticate.{_RESET}\n")


    return _run_finalize(vault, post_model, post_effort, post_backend,
                 skip_briefing=getattr(args, "skip_briefing", False),
                 finalizer_overrides=finalizer_overrides)


def _run_finalize(vault: Path, post_model: str, post_effort: str | None = None,
                  post_backend: str | None = None, force_shas: list[str] | None = None,
                  skip_briefing: bool = False, finalizer_overrides: dict | None = None) -> dict:
    """Acquire the ingest lock, run post-ingest over the pending batch, print the outcome.

    `force_shas` (#424) are already-committed shas a forced re-extraction just re-staged —
    passed straight through to `orchestrate.finalize`, which puts them back through the commit
    pass so their vault note and registry entry are replaced rather than silently skipped as
    already-committed.

    `skip_briefing` (#410) passes straight through to `orchestrate.finalize` — reconciliation,
    synthesis, and the timeline still run; only the briefing model call is skipped.

    `finalizer_overrides` (#433) passes straight through to `orchestrate.finalize` — per-stage
    model/backend overrides for reconciliation, synthesis, timeline, and briefing."""
    from watchdog.pipeline import orchestrate
    from watchdog.pipeline.locks import acquire_or_take_stale, lock_started_at
    from watchdog.pipeline.ingest_setup import STALE_SECONDS, _iso_now
    lock = vault / ".watchdog" / "registry" / ".ingest-lock"
    # Atomic acquisition (#257): the shared .ingest-lock means a running ingest or a second
    # finalize is excluded without a check-then-write race; a >30-min stale lock is taken over.
    if not acquire_or_take_stale(lock, f"pid: cli-finalize\nstarted_at: {_iso_now()}\n", STALE_SECONDS):
        ts = lock_started_at(lock)
        when = f" (lock acquired {ts})" if ts else ""
        sys.exit(f"\n  {_YELLOW}Error:{_RESET} an ingest or finalize is already running{when}.\n"
                 f"  If stale, run {_CYAN}watchdog unlock{_RESET}.\n")
    stages = "entity reconciliation + synthesis + timeline" if skip_briefing else \
        "entity reconciliation + synthesis + timeline + briefing"
    print(f"\n  {_DIM}Finalizing — {stages} (model: {_RESET}"
          f"{_BOLD}{post_model}{_RESET}{_DIM}).{_RESET}")
    try:
        import asyncio
        # #467: a bark run has no upper bound on how long reconciliation/synthesis/the briefing
        # take, the same failure mode _caffeinate() was added to guard extraction against (#415)
        # — without it, the machine sleeping mid-call kills a finalize outright.
        with _caffeinate():
            out = asyncio.run(orchestrate.finalize(vault, post_model=post_model, post_effort=post_effort,
                                                   post_backend=post_backend, force_shas=force_shas,
                                                   skip_briefing=skip_briefing,
                                                   finalizer_overrides=finalizer_overrides))
    finally:
        lock.unlink(missing_ok=True)

    if out.get("error") or out.get("briefing_error"):
        reason = out.get("error") or out.get("briefing_error")
        print(f"\n  {_YELLOW}Finalize didn't finish{_RESET}{_DIM} — {reason}.{_RESET}")
        print(f"  {_DIM}Re-run {_RESET}{_CYAN}watchdog bark{_RESET}{_DIM} once the limit resets.{_RESET}\n")
        return out
    n = out.get("synthesized", 0)
    parts = [f"{_BOLD}{n}{_RESET} entit{'ies' if n != 1 else 'y'} synthesized"]
    if out.get("briefing"):
        parts.append(f"briefing {_CYAN}{out['briefing']}{_RESET}")
    elif out.get("briefing_skipped"):
        parts.append("briefing skipped")
    print(f"\n  {_GREEN}Finalized{_RESET}  " + ", ".join(parts) + "\n")
    return out


def cmd_requeue(args) -> None:
    """Move documents from queue/_failed/ back into the active queue for re-ingest."""
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")
    n = _requeue_failed(vault)
    if not n:
        print(f"\n  {_DIM}No documents in {_RESET}{_CYAN}queue/_failed/{_RESET}{_DIM} — nothing to requeue.{_RESET}\n")
        return
    print(f"\n  {_GREEN}Requeued {_BOLD}{n}{_RESET}{_GREEN} document{'s' if n != 1 else ''}{_RESET}"
          f"{_DIM} — run {_RESET}{_CYAN}watchdog dig{_RESET}{_DIM} to retry.{_RESET}\n")


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

    if interactive.confirm("\n  Open in Claude Code to seed context?", default=True):
        context_path = vault / "context.md"
        if not context_path.exists():
            description = info["description"] if info and info.get("description") else "<!-- One paragraph. What is the story? What pattern, question, or wrongdoing are you pursuing? -->"
            context_path.write_text(_render_template("context.md", name=name, description=description))
        _launch_claude(vault, "/watchdog-context", model=model)
    else:
        print(f"\n  When ready, open Claude Code and run:  {_CYAN}/watchdog-context{_RESET}\n")


def cmd_guided(args) -> dict | None:
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
        return _offer_ingest(args, vault)

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
