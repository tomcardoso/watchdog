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
    """Total tokens the model actually processed as input for a run's calls (issue #470).

    On Anthropic, plain `input_tokens` badly undercounts once prompt caching is in play: a cache
    hit or write moves the bulk of a call's input volume into `cache_read_tokens`/
    `cache_write_tokens` instead, and those are reported as counts *additional* to
    `input_tokens` — a sectioned extraction carrying a growing shared prefix can show
    `input_tokens` in the single digits while the real content processed is orders of magnitude
    larger. (The archived `claude-agent-sdk` arm is the extreme case: 18 input tokens against
    230,155 cache-write tokens for the same six documents.) The naive chars/4 estimate this is
    compared against counts a document's full text once regardless of how many cached/fresh calls
    served it, so the comparison point needs the same full-volume accounting.

    **Every other provider reports the opposite convention** (#617): OpenAI's `prompt_tokens`
    already *includes* `prompt_tokens_details.cached_tokens`, and DeepSeek's already includes
    `prompt_cache_hit_tokens` — the cached count is a breakdown OF the input total, not an
    addition to it. Summing there double-counts every cached token. Measured on the archived
    benchmark corpus this inflated `gpt-5.4-mini`'s apparent tokens-per-est-token by 12.6% (its
    fitted ratio read 1.110 summed against 0.914 on `input_tokens` alone — and the *unsummed* fit
    is the better one, r² 0.839 vs 0.747, which is the tell that the extra tokens were double
    counting rather than signal).

    So `backend` selects the convention. It is optional and defaults to the summing form, because
    the run-level `totals` dict this is also called with has no single backend to key off — a run
    may mix providers — and Anthropic is both the default backend and the only one where the
    naive form is catastrophically wrong rather than mildly so. Pass it whenever the caller has a
    single call's record in hand, as `_model_tokenizer_calibration` does."""
    # Local import for the same circular-import reason `_model_tokenizer_calibration` has one:
    # `model_client.tokenizer_ratio` reaches back into this module.
    from watchdog.model_client import provider_for_backend
    inp = totals.get("input_tokens") or 0
    if backend is not None and provider_for_backend(backend) != "anthropic":
        return inp
    return inp + (totals.get("cache_read_tokens") or 0) + (totals.get("cache_write_tokens") or 0)


def _tokens_calibration(vault: Path, max_runs: int = 3) -> float | None:
    """Empirical correction factor for the naive chars/4 'tokens in' estimate (issue #417).

    Every usage-<ts>.json written by a run that actually extracted documents now carries both
    what was estimated for those documents at queue time (``totals.est_input_tokens``, the same
    chars/4 heuristic ``scan_queue`` uses, summed by `orchestrate._compact_result`) and what
    extraction really consumed (`_real_input_tokens`, #470) — their ratio is how far off the
    heuristic ran, against this vault's own recent documents rather than a fixed global guess.
    Averaged over the last `max_runs` such files (a blend here, unlike `cost_estimate`'s
    deliberate low/high range for dollars: this feeds a single displayed token count, not a
    range).

    Only files carrying `est_input_tokens` are extraction runs — a standalone `watchdog bark`
    never sets it (nothing was extracted), so it's silently skipped without a separate task-based
    filter. A vault with no such history yet (first run since this shipped, or a vault that has
    only ever run standalone finalizes) returns None, and callers fall back to the raw heuristic
    rather than fabricate a correction. Not backend-aware: a queue about to extract on a different
    provider than produced this history gets the same correction as any other — consistent with
    `cost_estimate`'s own $/token ratio, which has never been backend-filtered either.

    Not model-tokenizer-aware either (#574): this ratio already absorbs whatever tokenizer the
    extraction model(s) behind the last `max_runs` history files actually used, since it's derived
    empirically from their real `input_tokens`. That only stays correct while the extractor
    doesn't cross the Claude tokenizer boundary (old tokenizer through Sonnet 4.6, new tokenizer
    from Opus 4.8/Sonnet 5 on, #574) between the history and the upcoming run — a vault that
    switches its extractor model across that boundary gets a calibration ratio computed against
    the *other* tokenizer for one run's worth of history, same as switching providers already
    does. Not scoped to the extractor model per history file to correct for this — would need
    grouping calibration runs by model, a bigger change than this fix's scope.
    """
    from watchdog.pipeline import orchestrate
    ratios = []
    for uf in reversed(orchestrate.usage_files(vault)):
        try:
            totals = json.loads(uf.read_text(encoding="utf-8")).get("totals", {})
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
    """Empirical, per-model correction factor for `model_client.tokenizer_ratio` (#606 Part B),
    the same idea as `_tokens_calibration` above but scoped to one model/backend and pooled at
    the individual-call level rather than the whole-run level.

    `_tokens_calibration` compares a whole run's `totals.est_input_tokens` to its real input
    tokens — one ratio per run, mixing every model that run happened to use. That's fine for a
    single vault-wide "how far off is the naive heuristic" display, but the tokenizer ratio this
    feeds (`model_client.tokenizer_ratio`) is a property of one specific model's tokenizer, not of
    a run, so a run-level average would blend models with genuinely different tokenizers into one
    number. Each individual usage record already carries its own `model`/`backend` (`orchestrate.
    _record_usage`) — this scans those directly instead of `totals`.

    `model`/`backend` are resolved to the catalog id the same way `tokenizer_ratio`/
    `context_window` do (`resolve_model_id(model or DEFAULT_TIER)`), since that's what a record's
    own `model` field stores (`ModelResult.model`/`r.model` — the resolved id, not a raw tier
    name).

    Pooled across records, not averaged per file: matching records for one model can be sparse —
    a single file's extraction batch may contain only one or two calls to a given model — so
    summing estimated and actual tokens across every matching record before dividing gives a
    single stable ratio, rather than letting a file with few matches skew a per-file average as
    much as a file with many. Scans `orchestrate.usage_files(vault)` most-recent-file-first (like
    `_tokens_calibration`), stopping once `max_records` matching records have been collected —
    recent history reflects the model's current tokenizer, and there's no reason to keep scanning
    once there's enough to be stable.

    Pools `extract` and `extract-section` calls together: the tokenizer ratio corrects for how
    many real tokens a chunk of text turns into, a property of the model's tokenizer and the text
    alone — not of which task sent that text — so a whole-document `extract` call and a single
    section's `extract-section` call are equally valid evidence for the same ratio.

    Divides by `est_prompt_tokens`, NOT `est_input_tokens` (#617, D197). This is the bug the
    original #606 Part B version shipped with: `est_input_tokens` covers the document text only,
    while the provider's reported input tokens cover the entire rendered prompt — schema,
    extraction instructions, record skill, carry-forward entities, harvested candidates and the
    document alike. Dividing one by the other measured
    `tokenizer_ratio x (1 + prompt_overhead / document_tokens)`, and the bias was not even
    constant: it shrank as sections grew, so a vault whose history happened to be section-heavy
    calibrated to a larger ratio than a whole-document-heavy one for the same model and the same
    tokenizer, and shrank its sectioning budget accordingly. Measured against corpus-v1 the
    scaffolding is 7,200-9,300 real tokens per call, which read a true 1.09 Claude ratio as 1.41
    and a true sub-1.0 GPT-5.4-nano ratio as 2.19.

    One residue survives on `claude-agent-sdk` specifically. `est_prompt_tokens` measures the
    prompt *this code* renders; the SDK then prepends its own system prompt and CLI preamble,
    which we never see and therefore never estimate. Archived history puts that at roughly 930
    real tokens per call — the three Claude backends fit the same slope (1.089-1.091) over the
    same documents and differ only in intercept: 8,393 `claude-api`, 8,516 `claude-batch`, 9,326
    `claude-agent-sdk`. So an SDK-backed vault calibrates a few per cent high, the same kind of
    contamination this function was fixed for, an order of magnitude smaller. Left alone on
    purpose: the ratio is scoped by `(model, backend)`, so the inflated figure is only ever
    applied to SDK calls — which really do send those extra tokens — and a slightly more
    conservative budget on the backend that sends more is the right direction. It is not evidence
    about the tokenizer, though, which is why `model_catalog.yaml`'s constants come from
    `count_tokens` (backend-free by construction) rather than from this.

    Records written before #617 carry no `est_prompt_tokens` and are skipped rather than being
    silently mixed in on the old denominator. A vault whose history is entirely pre-#617
    therefore calibrates to None and falls back to the catalog constant — which is now itself a
    measured figure (`model_catalog.yaml`, `benchmarks/tokenizer_ratio.py`), so the cold-start
    fallback is real data rather than a vendor sentence, and the contaminated ratio stops being
    preferred over it.

    Returns `None` (not a noisy one-or-two-sample ratio) when fewer than `min_records` matching
    records were found — a model tried once or twice in this vault shouldn't override the catalog
    constant on a whim; callers fall back to it instead, exactly as `_tokens_calibration` falls
    back to the raw chars/4 heuristic with no history at all. A model with no history yet trusts
    the static catalog ratio until enough real calls accumulate to say otherwise."""
    from watchdog.model_catalog import resolve_model_id
    from watchdog.model_client import DEFAULT_TIER
    from watchdog.pipeline import orchestrate
    model_id = resolve_model_id(model or DEFAULT_TIER)
    estimated: list[float] = []
    actual: list[float] = []
    for uf in reversed(orchestrate.usage_files(vault)):
        try:
            records = json.loads(uf.read_text(encoding="utf-8")).get("calls", [])
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
            data = json.loads(uf.read_text(encoding="utf-8"))
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
    """Price `est_tokens` (already the calibrated 'tokens in' figure) against every model in
    `model_catalog.yaml` at that model's own list price (#469) — unlike `cost_estimate`'s $/token
    ratio (drawn from this vault's history of the model that actually ran), a model that has
    never run here has no $/token history of its own to draw on, so this instead uses the
    catalog's published per-token rates directly, scaled by `output_ratio` for the output side.
    Priced as if every input token were a cache miss — the simplest apples-to-apples comparison,
    and a deliberate overestimate rather than a guessed-at cache hit rate that would vary by
    model and usage pattern. Every catalog model is included, including Claude tiers, regardless
    of whether this vault is on subscription auth: this is list price for comparison, not a
    projection of what you would actually be billed. Returns `[]` when there's no usage history
    yet to derive `output_ratio` from — no dollar figure is invented from nothing.

    `est_tokens` is a single figure projected uniformly across every catalog model (#469's own
    design), which crosses the Claude tokenizer boundary (#574): it is calibrated (D135) against
    whichever model actually produced this vault's usage history, on the old tokenizer by default
    (the flat chars/4 heuristic itself, and Sonnet 4.6 remaining the default extractor). Each
    row's own `tokenizer_ratio` is applied here so a new-tokenizer model (Opus 4.8, Sonnet 5) is
    priced against its own higher real token count rather than the old-tokenizer figure verbatim
    — accurate as long as the calibration source stayed on the old tokenizer, same caveat
    `_tokens_calibration` documents for the single-model estimate."""
    if output_ratio is None:
        return []
    from watchdog.model_catalog import all_models, catalog_tokenizer_ratio
    rows = []
    for m in all_models():
        model_tokens = est_tokens * (catalog_tokenizer_ratio(m["id"]) or 1.0)
        model_output = model_tokens * output_ratio
        rows.append({"id": m["id"], "name": m["name"], "provider": m["provider"],
                      "cost": model_tokens * m["input"] + model_output * m["output"]})
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

    ``usage_files``, when given, replaces this vault's own history as the $/token ratio source
    (issue #478) — for a vault that is guaranteed to have none yet by design (every benchmark arm
    is a freshly seeded vault, `BENCHMARKING.md`), the caller can instead supply usage files
    archived from a prior run of the same model/effort/backend combination elsewhere.
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
    files = orchestrate.usage_files(vault) if usage_files is None else usage_files
    ratios = []
    for uf in files[-max_runs:]:
        try:
            totals = json.loads(uf.read_text(encoding="utf-8")).get("totals", {})
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
    """Pre-flight cost estimate for `watchdog bark` (issue #417, a #403 follow-up).

    `cost_estimate` above prices an ingest queue's *documents*; finalize's real work is
    reconciliation + synthesis over the staged post-ingest corpus already sitting in
    `.watchdog/tmp/` (`result_<sha>.json` + `notes_<sha>.md`) — #403 made synthesis read that
    staged corpus directly, which is what makes a token estimate here newly possible at all.
    'Tokens in' is the same chars/4 heuristic `scan_queue` applies to queued documents, just
    pointed at the staged corpus instead.

    The $/token ratio can't reuse `cost_estimate`'s own loop: a `run()` ingest's usage file is
    extraction-dominated and would misprice a lone `watchdog bark` in either direction. Only
    usage-<ts>.json files written by a *standalone* finalize qualify — every one of their calls
    has to fall in `orchestrate.FINALIZE_TASKS`, which is only true when finalize ran on its own
    (an ingest's own finalize tail shares its run's single usage file with extraction, so it
    never qualifies). A vault that's never run a standalone `watchdog bark` gets the doc/token
    counts with no dollar figure, the same "not enough history yet" treatment `cost_estimate`
    gives a first-run vault. Subscription auth (`claude-agent-sdk`) never gets a dollar figure
    either, for the same reason `cost_estimate` withholds one (D72): no real billing to project.

    ``usage_files``, when given, replaces this vault's own history (issue #478) — see
    `cost_estimate`'s own note on the same parameter.
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
    files = orchestrate.usage_files(vault) if usage_files is None else usage_files
    ratios = []
    for uf in reversed(files):
        try:
            data = json.loads(uf.read_text(encoding="utf-8"))
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
