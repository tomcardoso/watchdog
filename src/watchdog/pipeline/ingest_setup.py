"""
watchdog ingest — setup step for the Python ingest orchestrator (`pipeline/orchestrate.py`).

Called from `cmd/ingest.py` before extraction runs. Handles:
  1. Stale lock detection (>30 min) and re-acquisition
  2. Queue directory scan
  3. Writes .watchdog/ingest-state.json (present for the run's duration; a stale one
     signals an interrupted ingest to resume with `watchdog dig`)

Human workflow:
  watchdog chew    →  watchdog dig
  (OCR/docling)       (lock + queue + extract, all in-terminal)
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from watchdog.pipeline.backup import snapshot as _snapshot
from watchdog.pipeline.json_io import _read_json
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


def _real_input_tokens(totals: dict, backend: str | None = None) -> int:
    """Total tokens the model actually processed as input for a run's calls (#470).

    Anthropic reports a cache hit/write as counts *additional* to `input_tokens`, so they must be
    summed in or a sectioned extraction with a growing shared prefix reads as near-zero input.
    Every other provider reports the opposite convention (#617) — the cached count is already a
    breakdown OF `prompt_tokens`, so summing there double-counts. `backend` selects the
    convention; it defaults to the summing form since the run-level `totals` dict this is also
    called with may mix providers, and Anthropic is both the default and the only backend where
    the naive form is badly wrong rather than mildly so. Pass it when the caller has a single
    call's record in hand, as `_model_tokenizer_calibration` does."""
    # Local import for the same circular-import reason `_model_tokenizer_calibration` has one:
    # `model_client.tokenizer_ratio` reaches back into this module.
    from watchdog.model_client import provider_for_backend
    inp = totals.get("input_tokens") or 0
    if backend is not None and provider_for_backend(backend) != "anthropic":
        return inp
    return inp + (totals.get("cache_read_tokens") or 0) + (totals.get("cache_write_tokens") or 0)


def _tokens_calibration(vault: Path, max_runs: int = 3) -> float | None:
    """Empirical correction factor for the naive chars/4 'tokens in' estimate, averaged from this
    vault's own last `max_runs` extraction usage files rather than a fixed global guess (#417):
    the ratio of what `scan_queue` estimated to what extraction really consumed
    (`_real_input_tokens`, #470). Only files carrying `est_input_tokens` are extraction runs (a
    standalone `watchdog bark` never sets it), so those are skipped without a separate filter.
    Not backend- or model-tokenizer-aware (#574) — the ratio absorbs whatever tokenizer produced
    the history, so it drifts if the extractor model crosses the Claude tokenizer boundary
    between that history and the upcoming run. Returns None with no history to calibrate from;
    callers fall back to the raw heuristic rather than fabricate a correction."""
    from watchdog.pipeline import orchestrate
    ratios = []
    for uf in reversed(orchestrate.usage_files(vault)):
        try:
            totals = _read_json(uf).get("totals", {})
        except (OSError, json.JSONDecodeError):
            continue
        est, actual = totals.get("est_input_tokens"), _real_input_tokens(totals)
        if est and actual:
            ratios.append(actual / est)
        if len(ratios) >= max_runs:
            break
    return sum(ratios) / len(ratios) if ratios else None


def _model_tokenizer_calibration(vault: Path, model: str | None, backend: str | None,
                                 max_records: int = 20, min_records: int = 3) -> float | None:
    """Empirical, per-model correction factor for `model_client.tokenizer_ratio` (D190) — like
    `_tokens_calibration` above, but scoped to one (model, backend) pair and pooled at the
    individual-call level, since blending different models' tokenizers into one run-level ratio
    would be wrong. Scans usage records most-recent-first, pools `extract`/`extract-section`
    calls (the ratio is a property of the model and the text, not the task), and divides by
    `est_prompt_tokens` — not `est_input_tokens`, a since-fixed bug that read a true 1.09 Claude
    ratio as 1.41 by omitting the rest of the rendered prompt from the estimate (D198). Records
    written before that fix carry no `est_prompt_tokens` and are skipped rather than mixed in on
    the old denominator. `claude-agent-sdk` calibrates a few percent high regardless, since
    `est_prompt_tokens` can't see the SDK's own preamble (D198) — left uncorrected on purpose,
    since the ratio is scoped by `(model, backend)` and that backend really does send those extra
    tokens. Returns None below `min_records` matching calls, so a model tried once or twice
    doesn't override the catalog constant; callers fall back to it, same as `_tokens_calibration`
    falls back to the raw heuristic."""
    from watchdog.model_catalog import resolve_model_id
    from watchdog.model_client import DEFAULT_TIER
    from watchdog.pipeline import orchestrate
    model_id = resolve_model_id(model or DEFAULT_TIER)
    estimated: list[float] = []
    actual: list[float] = []
    for uf in reversed(orchestrate.usage_files(vault)):
        try:
            records = _read_json(uf).get("calls", [])
        except (OSError, json.JSONDecodeError):
            continue
        for record in records:
            if len(estimated) >= max_records:
                break
            if record.get("model") != model_id or record.get("backend") != backend:
                continue
            if record.get("task") not in ("extract", "extract-section"):
                continue
            est = record.get("est_prompt_tokens")
            act = _real_input_tokens(record, backend)
            if est and act:
                estimated.append(est)
                actual.append(act)
        if len(estimated) >= max_records:
            break
    if len(estimated) < min_records:
        return None
    return sum(actual) / sum(estimated)


def _output_token_ratio(vault: Path, max_runs: int, finalize_only: bool = False) -> float | None:
    """Backend/model-agnostic output:input token ratio from this vault's own recent usage
    history (#469) — how much a run tends to write for how much it reads, treated as a property
    of this vault's documents rather than of whichever model produced the history. Used to
    project an output-token estimate for a model that has never actually run in this vault, the
    same kind of extrapolation `_tokens_calibration` already makes for input tokens.
    `finalize_only` mirrors `finalize_cost_estimate`'s own task filter, keeping the two
    projections from mixing extraction-heavy and post-ingest-only usage files."""
    from watchdog.pipeline import orchestrate
    ratios = []
    for uf in reversed(orchestrate.usage_files(vault)):
        try:
            data = _read_json(uf)
        except (OSError, json.JSONDecodeError):
            continue
        if finalize_only:
            calls = data.get("calls") or []
            if not calls or any(c.get("task") not in orchestrate.FINALIZE_TASKS for c in calls):
                continue
        totals = data.get("totals", {})
        input_tokens, output_tokens = _real_input_tokens(totals), totals.get("output_tokens") or 0
        if input_tokens > 0 and output_tokens > 0:
            ratios.append(output_tokens / input_tokens)
        if len(ratios) >= max_runs:
            break
    return sum(ratios) / len(ratios) if ratios else None


def _catalog_cost_projection(est_tokens: int, output_ratio: float | None) -> list[dict]:
    """Price `est_tokens` against every model in `model_catalog.yaml` at that model's own list
    price (#469), scaled by `output_ratio` for the output side — unlike `cost_estimate`'s $/token
    ratio (drawn from this vault's history of a model that actually ran), a model with no run
    history here has no $/token of its own, so this prices every input token as a cache miss: a
    deliberate overestimate rather than a guessed hit rate. Every catalog model is included
    regardless of subscription auth — this is list price for comparison, not a real-billing
    projection. Returns `[]` with no usage history to derive `output_ratio` from — no dollar
    figure is invented. Each row's own `tokenizer_ratio` scales `est_tokens` so a new-tokenizer
    model (Opus 4.8, Sonnet 5) prices against its real token count rather than the old-tokenizer
    figure verbatim (#574, D135)."""
    if output_ratio is None:
        return []
    from watchdog.model_catalog import all_models, catalog_tokenizer_ratio, price_multiplier
    rows = []
    for m in all_models():
        model_tokens = est_tokens * (catalog_tokenizer_ratio(m["id"]) or 1.0)
        model_output = model_tokens * output_ratio
        # Priced at the rate in force right now (D217): a model with a time-of-day schedule costs
        # what it would cost to start the run at this moment, and carries the multiplier so the
        # caller can say so — a row silently 2x its neighbours' rate reads as a catalog error.
        multiplier = price_multiplier(m["id"])
        rows.append({"id": m["id"], "name": m["name"], "provider": m["provider"],
                      "price_multiplier": multiplier,
                      "cost": (model_tokens * m["input"] + model_output * m["output"]) * multiplier})
    rows.sort(key=lambda r: r["cost"])
    return rows


def cost_estimate_all_models(vault: Path, est_tokens: int, max_runs: int = 3) -> list[dict]:
    """`cost_estimate`'s queue projection, extended across every catalog model (#469) — a cost
    comparison across providers before committing to one. `est_tokens` should be `cost_estimate`'s
    own already-calibrated figure, so this projection starts from the same 'tokens in' number the
    single-model estimate already shows."""
    return _catalog_cost_projection(est_tokens, _output_token_ratio(vault, max_runs))


def finalize_cost_estimate_all_models(vault: Path, est_tokens: int, max_runs: int = 3) -> list[dict]:
    """`finalize_cost_estimate`'s staged-corpus projection, extended across every catalog model
    (#469) — same rationale as `cost_estimate_all_models`, scoped to standalone `watchdog bark`
    history (`finalize_only=True`) for the same reason `finalize_cost_estimate` itself excludes a
    combined dig+bark run's usage file."""
    return _catalog_cost_projection(est_tokens, _output_token_ratio(vault, max_runs, finalize_only=True))


def cost_estimate(vault: Path, queue_files: list[dict], backend: str | None,
                   max_runs: int = 3, usage_files: list[Path] | None = None) -> dict:
    """Pre-flight token/cost estimate for a queue (#269): the queue's calibrated `est_tokens`
    (`scan_queue`, `_tokens_calibration`, #417) times this vault's own $/token history, read
    fresh from the last `max_runs` usage files rather than averaged, so the result is a range —
    extraction output varies with document density, and a single false-precise figure would
    undercut the trust this is meant to build. Subscription auth (``claude-agent-sdk``) never
    gets a dollar figure, only the calibrated token estimate — there's no real billing to
    project. With no usage history yet, only the token estimate is returned.

    ``usage_files``, when given, replaces this vault's own history as the $/token ratio source
    (#478) — for a vault guaranteed to have none yet by design (every benchmark arm is a freshly
    seeded vault, `BENCHMARKING.md`), the caller can supply usage files archived from a prior run
    of the same model/effort/backend combination elsewhere."""
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
    files = orchestrate.usage_files(vault) if usage_files is None else usage_files
    ratios = []
    for uf in files[-max_runs:]:
        try:
            totals = _read_json(uf).get("totals", {})
        except (OSError, json.JSONDecodeError):
            continue
        input_tokens, cost_usd = _real_input_tokens(totals), totals.get("cost_usd")
        if input_tokens > 0 and cost_usd:
            ratios.append(cost_usd / input_tokens)

    if ratios:
        result["cost_low"] = est_tokens * min(ratios)
        result["cost_high"] = est_tokens * max(ratios)
        result["runs_used"] = len(ratios)
    return result


def finalize_cost_estimate(vault: Path, backend: str | None, max_runs: int = 3,
                           usage_files: list[Path] | None = None) -> dict:
    """Pre-flight cost estimate for `watchdog bark` (#417, a #403 follow-up) — prices the staged
    post-ingest corpus (`result_<sha>.json`/`notes_<sha>.md` in `.watchdog/tmp/`, readable
    directly since #403) with the same chars/4 heuristic `scan_queue` applies to queued
    documents. The $/token ratio only draws on usage files written by a *standalone* finalize
    (every call in `orchestrate.FINALIZE_TASKS`) — an ingest's own finalize tail shares its run's
    usage file with extraction and would misprice in either direction, so it's excluded. No
    dollar figure with no standalone-finalize history yet, or on subscription auth
    (`claude-agent-sdk`, D72) — same treatment `cost_estimate` gives.

    ``usage_files``, when given, replaces this vault's own history (#478) — see
    `cost_estimate`'s own note on the same parameter."""
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
    files = orchestrate.usage_files(vault) if usage_files is None else usage_files
    ratios = []
    for uf in reversed(files):
        try:
            data = _read_json(uf)
        except (OSError, json.JSONDecodeError):
            continue
        calls = data.get("calls") or []
        if not calls or any(c.get("task") not in orchestrate.FINALIZE_TASKS for c in calls):
            continue   # empty, or shares a usage file with extraction/classification
        totals = data.get("totals", {})
        input_tokens, cost_usd = _real_input_tokens(totals), totals.get("cost_usd")
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
    batch extraction (claude-batch or openai-batch, #530) still needs mutual exclusion
    (so two concurrent `watchdog ingest` invocations can't both try to collect the same
    batch) even when there's nothing new to chew.
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
        # `vault` is wired through so this benefits from #606 Part B's calibrated tokenizer
        # ratio when available. `extractor_model` itself is NOT wired to backend/effort here
        # (#606) — its only real caller (`cmd/ingest.py`'s `is_run(...)`) never passes
        # extractor_model/backend/effort at all, so this always resolves against the "sonnet"
        # default regardless of what's actually configured, a separate pre-existing gap nothing
        # currently reads back (`state["section_token_threshold"]` has no reader) — out of scope.
        "section_token_threshold": _section_token_threshold(extractor_model, vault=vault),
        "backup_dir": str(backup_dir) if backup_dir else None,
    }
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state
