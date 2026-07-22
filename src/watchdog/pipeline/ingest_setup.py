"""
watchdog ingest — setup step for the Python ingest orchestrator (`pipeline/orchestrate.py`).

Called from `cmd/ingest.py` before extraction runs. Handles:
  1. Stale lock detection (>30 min) and re-acquisition
  2. Queue directory scan
  3. Writes .watchdog/ingest-state.json (present for the run's duration; a stale one
     signals an interrupted ingest to resume with `watchdog ingest`)

Human workflow:
  watchdog chew    →  watchdog ingest
  (OCR/docling)       (lock + queue + extract, all in-terminal)
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from watchdog.pipeline.backup import snapshot as _snapshot
from watchdog.pipeline.locks import acquire_or_take_stale, lock_age_seconds, lock_started_at
from watchdog.pipeline.section import (
    section_token_threshold as _section_token_threshold,
    est_tokens_from_pages as _est_tokens_from_pages,
)

STALE_SECONDS = 30 * 60


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def scan_queue(vault: Path) -> list[dict]:
    """Read every queued file's metadata (filename, type, page count, est_tokens) without
    touching the lock — shared by `run` (which then acquires the lock) and `cost_estimate`'s
    `--estimate` path (#269), which must stay lock-free and read-only."""
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
    return queue_files


def _tokens_calibration(vault: Path, max_runs: int = 3) -> float | None:
    """Empirical correction factor for the naive chars/4 'tokens in' estimate (issue #417).

    Every usage-<ts>.json written by a run that actually extracted documents now carries both
    what was estimated for those documents at queue time (``totals.est_input_tokens``, the same
    chars/4 heuristic ``scan_queue`` uses, summed by `orchestrate._compact_result`) and what
    extraction really consumed (``totals.input_tokens``) — their ratio is how far off the
    heuristic ran, against this vault's own recent documents rather than a fixed global guess.
    Averaged over the last `max_runs` such files (a blend here, unlike `cost_estimate`'s
    deliberate low/high range for dollars: this feeds a single displayed token count, not a
    range).

    Only files carrying `est_input_tokens` are extraction runs — a standalone `watchdog finalize`
    never sets it (nothing was extracted), so it's silently skipped without a separate task-based
    filter. A vault with no such history yet (first run since this shipped, or a vault that has
    only ever run standalone finalizes) returns None, and callers fall back to the raw heuristic
    rather than fabricate a correction. Not backend-aware: a queue about to extract on a different
    provider than produced this history gets the same correction as any other — consistent with
    `cost_estimate`'s own $/token ratio, which has never been backend-filtered either.
    """
    from watchdog.pipeline import orchestrate
    ratios = []
    for uf in reversed(orchestrate.usage_files(vault)):
        try:
            totals = json.loads(uf.read_text(encoding="utf-8")).get("totals", {})
        except (OSError, json.JSONDecodeError):
            continue
        est, actual = totals.get("est_input_tokens"), totals.get("input_tokens")
        if est and actual:
            ratios.append(actual / est)
        if len(ratios) >= max_runs:
            break
    return sum(ratios) / len(ratios) if ratios else None


def cost_estimate(vault: Path, queue_files: list[dict], backend: str | None,
                   max_runs: int = 3) -> dict:
    """Pre-flight token/cost estimate for a queue (#269): the queue's own `est_tokens` (already
    computed by `scan_queue`, and calibrated against this vault's own extraction history — #417,
    see `_tokens_calibration`) times this vault's own $/token history, so a batch's rough cost is
    visible at the confirm prompt instead of discovered mid-run. The $/token ratio is read fresh
    from each of the last `max_runs` usage-*.json files (not averaged into one number) so the
    result can be presented as a range — extraction output varies with document density, and a
    single false-precise figure would undercut the trust this is meant to build.

    Subscription auth (``claude-agent-sdk``) never gets a dollar figure: there's no real billing
    to project, only a session-limit fraction this can't estimate honestly from token counts
    alone. It still gets the calibrated token estimate, though — that's what a subscriber budgets
    a session window against. With no usage history yet (first run), the token estimate alone is
    returned — no invented dollar figure.
    """
    documents = len(queue_files)
    pages = sum(f.get("page_count") or 0 for f in queue_files)
    raw_tokens = sum(f.get("est_tokens") or 0 for f in queue_files)
    calibration = _tokens_calibration(vault, max_runs) if documents else None
    est_tokens = round(raw_tokens * calibration) if calibration else raw_tokens
    result = {"documents": documents, "pages": pages, "est_tokens": est_tokens,
              "cost_low": None, "cost_high": None, "runs_used": 0}
    if backend == "claude-agent-sdk" or not documents:
        return result

    from watchdog.pipeline import orchestrate
    files = orchestrate.usage_files(vault)
    ratios = []
    for uf in files[-max_runs:]:
        try:
            totals = json.loads(uf.read_text(encoding="utf-8")).get("totals", {})
        except (OSError, json.JSONDecodeError):
            continue
        input_tokens, cost_usd = totals.get("input_tokens") or 0, totals.get("cost_usd")
        if input_tokens > 0 and cost_usd:
            ratios.append(cost_usd / input_tokens)

    if ratios:
        result["cost_low"] = est_tokens * min(ratios)
        result["cost_high"] = est_tokens * max(ratios)
        result["runs_used"] = len(ratios)
    return result


def finalize_cost_estimate(vault: Path, backend: str | None, max_runs: int = 3) -> dict:
    """Pre-flight cost estimate for `watchdog finalize` (issue #417, a #403 follow-up).

    `cost_estimate` above prices an ingest queue's *documents*; finalize's real work is
    reconciliation + synthesis over the staged post-ingest corpus already sitting in
    `.watchdog/tmp/` (`result_<sha>.json` + `notes_<sha>.md`) — #403 made synthesis read that
    staged corpus directly, which is what makes a token estimate here newly possible at all.
    'Tokens in' is the same chars/4 heuristic `scan_queue` applies to queued documents, just
    pointed at the staged corpus instead.

    The $/token ratio can't reuse `cost_estimate`'s own loop: a `run()` ingest's usage file is
    extraction-dominated and would misprice a lone `watchdog finalize` in either direction. Only
    usage-<ts>.json files written by a *standalone* finalize qualify — every one of their calls
    has to fall in `orchestrate.FINALIZE_TASKS`, which is only true when finalize ran on its own
    (an ingest's own finalize tail shares its run's single usage file with extraction, so it
    never qualifies). A vault that's never run a standalone `watchdog finalize` gets the doc/token
    counts with no dollar figure, the same "not enough history yet" treatment `cost_estimate`
    gives a first-run vault. Subscription auth (`claude-agent-sdk`) never gets a dollar figure
    either, for the same reason `cost_estimate` withholds one (D72): no real billing to project.
    """
    tmp = vault / ".watchdog" / "tmp"
    results = sorted(tmp.glob("result_*.json"))
    docs = len(results)
    est_tokens = 0
    for p in results:
        try:
            est_tokens += len(p.read_text(encoding="utf-8")) // 4
        except OSError:
            continue
    for p in tmp.glob("notes_*.md"):
        try:
            est_tokens += len(p.read_text(encoding="utf-8")) // 4
        except OSError:
            continue
    result = {"docs": docs, "est_tokens": est_tokens,
              "cost_low": None, "cost_high": None, "runs_used": 0}
    if backend == "claude-agent-sdk" or not docs:
        return result

    from watchdog.pipeline import orchestrate
    ratios = []
    for uf in reversed(orchestrate.usage_files(vault)):
        try:
            data = json.loads(uf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        calls = data.get("calls") or []
        if not calls or any(c.get("task") not in orchestrate.FINALIZE_TASKS for c in calls):
            continue   # empty, or shares a usage file with extraction/classification
        totals = data.get("totals", {})
        input_tokens, cost_usd = totals.get("input_tokens") or 0, totals.get("cost_usd")
        if input_tokens > 0 and cost_usd:
            ratios.append(cost_usd / input_tokens)
        if len(ratios) >= max_runs:
            break

    if ratios:
        result["cost_low"] = est_tokens * min(ratios)
        result["cost_high"] = est_tokens * max(ratios)
        result["runs_used"] = len(ratios)
    return result


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
    lock_file = vault / ".watchdog" / "registry" / ".ingest-lock"
    state_file = vault / ".watchdog" / "ingest-state.json"

    queue_files = scan_queue(vault)
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

    # Fresh run — clear the post-ingest inputs (per-doc results and scratchpads) left by a
    # prior ingest so the finalizer gate + briefing see only this run's documents. Skipped when
    # merging into a pending batch (wipe_pending=False), so this run's documents accumulate onto
    # it and they finalize together.
    backup_dir = None
    if wipe_pending:
        tmp = vault / ".watchdog" / "tmp"
        about_to_wipe = [*tmp.glob("result_*.json"), *tmp.glob("notes_*.md")]
        # A no-op on an ordinary ingest (nothing left over from a prior run to wipe) — this
        # only produces a backup when the discard choice is actually throwing something away.
        backup_dir = _snapshot(vault, "ingest-discard", about_to_wipe)
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
        "section_token_threshold": _section_token_threshold(extractor_model),
        "backup_dir": str(backup_dir) if backup_dir else None,
    }
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state
