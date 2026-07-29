"""`watchdog usage` — per-call token/cost/latency breakdown for ingest runs (#207, #317, #319).

Reads `.watchdog/registry/usage/usage-<ts>.json` (D50, relocated out of the flat Registry dir
in #319) — the Python orchestrator's own per-call telemetry, written after every ingest run
(`watchdog ingest`, or `watchdog dig`/`watchdog bark`) — and groups calls by stage (classifier / extractor / finalizer,
matching the CLI's own `--classifier-model` / `--extractor-model` / `--finalizer-model`
vocabulary). Extractor rows show the filename and page range (or section) each call covered.
Cost is read directly from each record — there is no local pricing table to keep in sync, since
`model_client` already computed `cost_usd` authoritatively at call time.

Formerly the standalone `scripts/analyze-session` dev tool; folded into the CLI (#319) so it's
usable without a repo checkout."""

import json
import re
import sys
from pathlib import Path

from watchdog.cmd.base import _DIM, _RESET, _YELLOW, _resolve_vault
from watchdog.model_catalog import display_name
from watchdog.pipeline.orchestrate import usage_files

# task name -> stage bucket, matching --classifier-model/--extractor-model/--finalizer-model.
_STAGE = {
    "classify": "classifier",
    "extract": "extractor", "extract-section": "extractor",
    "reconcile": "finalizer", "entity-synthesis": "finalizer", "timeline-dedup": "finalizer",
    "timeline-precision": "finalizer", "briefing": "finalizer",
}
_STAGE_ORDER = ("classifier", "extractor", "finalizer")

_ZERO_TOTALS = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
                "cache_write_tokens": 0, "cost_usd": 0.0, "latency_s": 0.0}


def _fmt(n: int) -> str:
    return f"{n:,}"


def _fmt_secs(s: float) -> str:
    return f"{s:.1f}s" if s < 60 else f"{s / 60:.1f}m"


_BATCH_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _batch_span(start: str | None, end: str | None) -> str | None:
    """Human duration between two batch-lifecycle timestamps (orchestrate.py's `_BATCH_TS_FMT`
    strings), or None if either is missing — `ended_at` predates this feature for any batch
    collected before it shipped, and `collected_at` is absent from a non-batch call entirely."""
    if not start or not end:
        return None
    import datetime
    secs = (datetime.datetime.strptime(end, _BATCH_TS_FMT)
           - datetime.datetime.strptime(start, _BATCH_TS_FMT)).total_seconds()
    return _fmt_secs(max(secs, 0))


def _batch_lifecycle_note(call: dict) -> str:
    """`submitted -> ended -> collected` summary for a batch-collected stage — the middle and
    last of those routinely differ by hours under D52's submit-and-exit design, since collection
    happens whenever a later, unrelated `watchdog dig` invocation happens to notice the batch has
    ended, not right after it does."""
    submitted, ended, collected = (call.get("batch_submitted_at"), call.get("batch_ended_at"),
                                   call.get("batch_collected_at"))
    processed, idle = _batch_span(submitted, ended), _batch_span(ended, collected)
    return (
        f"batch {call.get('batch_id') or '?'} · submitted {submitted or '?'} · "
        f"ended {ended or '?'}" + (f" (processed {processed})" if processed else "") +
        f" · collected {collected or '?'}" + (f" (idle {idle})" if idle else "")
    )


def _wall_span(calls: list[dict]) -> float | None:
    """Wall-clock elapsed across `calls` — max(end) − min(start), where each call's start is
    `end_ts − latency_s`. This is the real time the calls took together, which for a stage that
    ran concurrently is shorter than the summed per-call latency. Returns None when no call
    carries an `end_ts` (usage files written before #317's follow-up), so callers fall back to
    the summed call time alone."""
    ends = [c["end_ts"] for c in calls if c.get("end_ts")]
    if not ends:
        return None
    starts = [c["end_ts"] - (c.get("latency_s") or 0.0) for c in calls if c.get("end_ts")]
    return max(ends) - min(starts)


def _peak_concurrency(calls: list[dict]) -> int:
    """Highest number of `calls` with overlapping [start, end) intervals at any instant (#457) —
    what the "N calls ran concurrently" line used to report was just `len(calls)`, the stage's
    total call count, which overstates it for any run capped below that by `--concurrency`: 4
    calls that ran two at a time (`--concurrency 2`) both extend the wall-clock span past the
    fastest call's latency (so the "ran concurrently" line fires) and total 4, even though only
    2 were ever in flight together. A standard sweep over start/end events tracks the real peak
    instead. Calls with no `end_ts` (usage files written before #317's follow-up) are excluded,
    same as `_wall_span`; returns 1 if none carry one (nothing to sweep, no overlap knowable)."""
    events = []
    for c in calls:
        end = c.get("end_ts")
        if not end:
            continue
        start = end - (c.get("latency_s") or 0.0)
        events.append((start, 1))
        events.append((end, -1))
    if not events:
        return 1
    # End events (-1) sort before start events (1) at the same instant, so a call ending exactly
    # when another begins isn't counted as briefly overlapping.
    events.sort(key=lambda e: (e[0], e[1]))
    running = peak = 0
    for _, delta in events:
        running += delta
        peak = max(peak, running)
    return peak


def _stage_models(calls: list[dict]) -> str:
    """Distinct model names used across `calls`, in first-seen order — printed once next to the
    stage header rather than truncated into a per-row column (a per-row abbreviation like
    `_short_model`'s old 'claude-sonnet-4-6' -> 'sonnet' silently mangled non-Claude ids, e.g.
    'gemini-3.1-flash-lite' -> '3.1'). Shown via the catalog's pretty `display_name` (e.g.
    'GPT-5.6 Terra') rather than the raw id — a full name, not an abbreviation, so it doesn't
    reintroduce that same mangling; falls back to the raw id for anything uncatalogued. Calls
    within one stage share a model in the overwhelming majority of runs (one
    `--classifier-model`/etc. per invocation), but joining distinct values keeps this correct if
    that ever changes mid-run."""
    seen = []
    for c in calls:
        m = display_name(c.get("model")) if c.get("model") else "?"
        if m not in seen:
            seen.append(m)
    return ", ".join(seen)


def _short_auth(mode: str | None) -> str:
    """'subscription' -> 'sub', 'api-key' -> 'key' — which billing lane paid for the call."""
    if mode == "subscription":
        return "sub"
    if mode == "api-key":
        return "key"
    return "—"


# Claude is reachable through three backends with very different cost shapes — the agent SDK
# carries a larger prompt preamble and auto-caches with no `cache_control` knob, claude-api gets
# the explicit cache breakpoint, and claude-batch is half price — but a bare `sonnet` resolves to
# one of them by *auth mode*, silently. Nothing used to print which, so two runs of the "same"
# arm could differ by a third on input tokens with no visible cause (#475). Hence: name the
# backend wherever a model is named, and the auth mode with it for Claude, where it's the thing
# that did the choosing.
_CLAUDE_BACKENDS = ("claude-agent-sdk", "claude-api", "claude-batch")


def _backend_label(backend: str | None, auth_mode: str | None) -> str:
    """`claude-agent-sdk (subscription)` for a Claude backend, bare `deepseek` for the rest —
    auth mode is only interesting where it selected the backend."""
    if not backend:
        return "?"
    if backend in _CLAUDE_BACKENDS and auth_mode:
        return f"{backend} ({auth_mode})"
    return backend


def _stage_backends(calls: list[dict]) -> str:
    """Distinct `backend (auth)` labels across `calls`, first-seen order — the backend twin of
    `_stage_models`. Joined rather than assumed single, since a finalizer stage can be split
    across per-sub-stage overrides (D137)."""
    seen = []
    for c in calls:
        label = _backend_label(c.get("backend"), c.get("auth_mode"))
        if label not in seen:
            seen.append(label)
    return ", ".join(seen)


def _short_backend(backend: str | None) -> str:
    """Compact backend tag for the per-run comparison table: `sdk`/`api`/`batch` for the three
    Claude backends (where the distinction is the point), the provider name otherwise."""
    return {"claude-agent-sdk": "sdk", "claude-api": "api",
            "claude-batch": "batch"}.get(backend or "", backend or "—")


def _run_backends(calls: list[dict]) -> str:
    """Compact distinct `backend/auth` tags for one run — e.g. `sdk/sub` or `api/key, deepseek`.
    Claude backends carry the auth suffix; others don't (they are always api-key)."""
    seen = []
    for c in calls:
        backend = c.get("backend")
        tag = _short_backend(backend)
        if backend in _CLAUDE_BACKENDS:
            tag = f"{tag}/{_short_auth(c.get('auth_mode'))}"
        if tag not in seen:
            seen.append(tag)
    return ", ".join(seen) or "—"


def _subscription_note(calls: list[dict]) -> str | None:
    """Warning text when any Claude call in `calls` ran on subscription auth: `cost_usd` is then
    a list-price projection, not money that was billed. Presenting it as a plain dollar figure
    alongside metered runs is how a subscription arm gets mistaken for a priced one (#475)."""
    if any(c.get("backend") in _CLAUDE_BACKENDS and c.get("auth_mode") == "subscription"
           for c in calls):
        return ("subscription auth — Claude costs below are list-price equivalents, "
                "not amounts billed")
    return None


_PAGE_RANGE_RE = re.compile(r"^pages (\d+)–(\d+)")


def _pages_in_detail(detail: str | None) -> int | None:
    """Page count a call's own `detail` string covers (#457) — `"pages 1–36"` -> 36, `"pages
    15–34"` -> 20 (a section call's own range, not the whole document's). None for anything
    that isn't a page range: `"digest"`, `"part N of M"` (a section with no page markers to
    label by), or a finalizer detail (a date, a month, an entity/pair count)."""
    if not detail:
        return None
    m = _PAGE_RANGE_RE.match(detail)
    if not m:
        return None
    start, end = int(m.group(1)), int(m.group(2))
    return end - start + 1


def _corpus_pages(vault: Path) -> tuple[int, int] | None:
    """(total_pages, document_count) covering every ingested document, not just the run being
    displayed — the right denominator for cost/page.

    Reads `.watchdog/extracted/*.json` (#457), the artifacts `watchdog dig` stages before any
    `bark` call — present for a dig-only vault too, unlike the committed document registry this
    used to require exclusively, which stays empty until `bark` runs (never, for a vault that's
    dig-only by design, e.g. an extractor-only benchmark arm). `_commit_extracted` never deletes
    an extracted artifact once staged, so this also covers every already-barked document. Falls
    back to the registry only for a vault ingested before the dig/bark split (#403) shipped,
    which has committed documents but no `extracted/` directory at all."""
    extracted = vault / ".watchdog" / "extracted"
    pages, n = 0, 0
    if extracted.exists():
        for f in extracted.glob("*.json"):
            try:
                doc = json.loads(f.read_text(encoding="utf-8")).get("document", {})
            except (json.JSONDecodeError, OSError):
                continue
            pages += doc.get("page_count") or 0
            n += 1
    if n:
        return (pages, n)

    reg = vault / ".watchdog" / "registry" / "documents.json"
    if not reg.exists():
        return None
    try:
        docs = json.loads(reg.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not docs:
        return None
    pages = sum((v.get("page_count") or 0) for v in docs.values() if isinstance(v, dict))
    return (pages, len(docs))


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"Error: could not read {path}: {e}")


def _accumulate(totals: dict, add: dict) -> None:
    for k in _ZERO_TOTALS:
        totals[k] += add[k]


def _print_cost_per_page(vault: Path, total_cost: float) -> None:
    corpus = _corpus_pages(vault)
    print()
    if corpus and corpus[0] > 0:
        pages, n_docs = corpus
        print(f"  {'Corpus':<14} {_fmt(pages)} pages across {n_docs} document{'s' if n_docs != 1 else ''}")
        print(f"  {'Cost / page':<14} ${total_cost / pages:.4f}  (${total_cost:.4f} / {_fmt(pages)} pages)")
    else:
        print(f"  {'Cost / page':<14} unavailable — no extracted documents on disk for {vault}")
    print()


def _print_stage(calls: list[dict]) -> dict:
    """Print one row per call (highest cost first), a subtotal row, and return the subtotal.
    A call that needed more than one attempt (a schema-validation retry) gets a `×N` marker
    after its cost — each retry re-pays that call's tokens, so it's worth flagging inline
    rather than leaving an inflated cost/latency unexplained. A call that never returned valid
    JSON at all (`"failed": True`, D125) gets a further `✗ failed` marker — its tokens/cost are
    ordinary record fields, so they're already included in the subtotal below; the marker only
    makes the row identifiable."""
    name_w = max(min(28, max(len(c.get("filename") or c["task"]) for c in calls)), len("Filename"))
    detail_w = max(min(24, max(len(c.get("detail") or "—") for c in calls)), len("Detail"))
    effort_w = max(max(len(c.get("effort") or "—") for c in calls), len("Effort"))
    auth_w = max(max(len(_short_auth(c.get("auth_mode"))) for c in calls), len("Auth"))

    hdr = (f"  {'Filename':<{name_w}}  {'Detail':<{detail_w}}  {'Effort':<{effort_w}}  "
           f"{'Auth':<{auth_w}}  "
           f"{'Input':>8}  {'C.read':>8}  {'C.write':>8}  {'Output':>7}  {'Latency':>8}  "
           f"{'Cost':>8}  {'$/pg':>7}")
    print(hdr)
    print(f"  {'─' * (len(hdr) - 2)}")

    totals = dict(_ZERO_TOTALS)
    for c in sorted(calls, key=lambda c: -(c.get("cost_usd") or 0.0)):
        name = c.get("filename") or c["task"]
        detail = c.get("detail") or "—"
        effort = c.get("effort") or "—"
        auth = _short_auth(c.get("auth_mode"))
        cost = c.get("cost_usd") or 0.0
        latency = c.get("latency_s") or 0.0
        attempts = c.get("attempts") or 1
        trunc_name = (name[:name_w - 1] + "…") if len(name) > name_w else name
        trunc_detail = (detail[:detail_w - 1] + "…") if len(detail) > detail_w else detail
        retry_note = f"  ×{attempts}" if attempts > 1 else ""
        if c.get("failed"):
            retry_note += f"  {_YELLOW}✗ failed{_RESET}"
        # Per-call cost/page (#457), parsed from this call's own `detail` — None (shown as "—")
        # for anything that isn't a page range: a digest call, a section with no page markers,
        # or a finalizer detail (a date, a month, an entity/pair count).
        pages = _pages_in_detail(c.get("detail"))
        per_page = f"${cost / pages:.4f}" if pages else "—"
        # The claude-agent-sdk harness's own timing (#402): time actually spent in API requests,
        # vs. this row's wall-clock Latency figure — a large gap is the harness backing off
        # internally (throttled), not the model being slow. Only present for that backend, so
        # trail it after the fixed columns rather than widening Latency for every row.
        api_note = ""
        if c.get("api_ms") is not None:
            turns = c.get("num_turns")
            turns_note = f", {turns} turns" if turns and turns != 1 else ""
            api_note = f"  {_DIM}· api {_fmt_secs(c['api_ms'] / 1000)}{turns_note}{_RESET}"
        print(
            f"  {trunc_name:<{name_w}}  {trunc_detail:<{detail_w}}  "
            f"{effort:<{effort_w}}  {auth:<{auth_w}}  "
            f"{_fmt(c['input_tokens']):>8}  {_fmt(c['cache_read_tokens']):>8}  {_fmt(c['cache_write_tokens']):>8}  "
            f"{_fmt(c['output_tokens']):>7}  {_fmt_secs(latency):>8}  ${cost:>6.4f}  {per_page:>7}{retry_note}{api_note}"
        )
        totals["input_tokens"] += c["input_tokens"]
        totals["output_tokens"] += c["output_tokens"]
        totals["cache_read_tokens"] += c["cache_read_tokens"]
        totals["cache_write_tokens"] += c["cache_write_tokens"]
        totals["cost_usd"] += cost
        totals["latency_s"] += latency

    print(
        f"  {'Subtotal':<{name_w}}  {'':<{detail_w}}  {'':<{effort_w}}  {'':<{auth_w}}  "
        f"{_fmt(totals['input_tokens']):>8}  {_fmt(totals['cache_read_tokens']):>8}  "
        f"{_fmt(totals['cache_write_tokens']):>8}  {_fmt(totals['output_tokens']):>7}  "
        f"{_fmt_secs(totals['latency_s']):>8}  ${totals['cost_usd']:>7.4f}"
    )
    # When the stage's calls overlapped in time, the summed Latency column overstates how long
    # the stage actually took — show the wall-clock span so a concurrent stage's real duration
    # is visible (#317 follow-up). Skipped when calls ran back-to-back (span ≈ sum) or the usage
    # file predates end_ts.
    span = _wall_span(calls)
    if span is not None and len(calls) > 1 and totals["latency_s"] - span > 0.1:
        peak = _peak_concurrency(calls)
        print(f"  ↳ {_fmt_secs(span)} elapsed (wall-clock; {len(calls)} calls, "
              f"up to {peak} concurrent)")
    return totals


def _analyze_run(usage_file: Path, vault: Path) -> None:
    """Detailed breakdown of a single run: one section per stage, filenames/detail for each call."""
    data = _load(usage_file)
    calls = data.get("calls", [])

    W = 96
    print()
    print("━" * W)
    print(f"  Run  {usage_file.stem}" + (f"   vault: {vault.name}" if vault else ""))
    print("━" * W)

    if not calls:
        print("\n  (no model calls recorded for this run)\n")
        return

    by_stage: dict[str, list[dict]] = {}
    for c in calls:
        by_stage.setdefault(_STAGE.get(c["task"], c["task"]), []).append(c)

    grand = dict(_ZERO_TOTALS)
    n_calls = 0
    ordered = [s for s in _STAGE_ORDER if s in by_stage] + \
              [s for s in by_stage if s not in _STAGE_ORDER]
    for stage in ordered:
        stage_calls = by_stage[stage]
        print(f"\n  {stage.upper()}  ({len(stage_calls)} call{'s' if len(stage_calls) != 1 else ''})"
              f"  ·  model: {_stage_models(stage_calls)}"
              f"  ·  backend: {_stage_backends(stage_calls)}")
        if any(c.get("backend") == "local" for c in stage_calls):
            # A local model's $0 cost is real, but it isn't "free" the way it reads at a glance
            # (#380) — it's paid in wall-clock time instead of tokens. Latency is the real signal.
            print(f"  {_DIM}local model — no per-token cost; Latency is the real cost signal here{_RESET}")
        batch_call = next((c for c in stage_calls if c.get("batch_id")), None)
        if batch_call:
            print(f"  {_DIM}↳ {_batch_lifecycle_note(batch_call)}{_RESET}")
        totals = _print_stage(stage_calls)
        _accumulate(grand, totals)
        n_calls += len(stage_calls)

    print(f"\n  {'━' * (W - 2)}")
    total_span = _wall_span(calls)
    call_time = f"{_fmt_secs(grand['latency_s'])} call time"
    elapsed = f"  ·  {_fmt_secs(total_span)} elapsed" if total_span is not None else ""
    print(
        f"  TOTAL  ({n_calls} call{'s' if n_calls != 1 else ''})     "
        f"{_fmt(grand['input_tokens'])} in  ·  {_fmt(grand['cache_read_tokens'])} cache-read  ·  "
        f"{_fmt(grand['cache_write_tokens'])} cache-write  ·  {_fmt(grand['output_tokens'])} out  ·  "
        f"${grand['cost_usd']:.4f}  ·  {call_time}{elapsed}"
    )
    note = _subscription_note(calls)
    if note:
        print(f"  {_YELLOW}⚠{_RESET}  {_DIM}{note}{_RESET}")
    _print_cost_per_page(vault, grand["cost_usd"])


def _run_totals(calls: list[dict]) -> dict:
    t = dict(_ZERO_TOTALS, classifier_cost=0.0, extractor_cost=0.0, finalizer_cost=0.0)
    for c in calls:
        cost = c.get("cost_usd") or 0.0
        t["input_tokens"] += c["input_tokens"]
        t["output_tokens"] += c["output_tokens"]
        t["cache_read_tokens"] += c["cache_read_tokens"]
        t["cache_write_tokens"] += c["cache_write_tokens"]
        t["cost_usd"] += cost
        t["latency_s"] += c.get("latency_s") or 0.0
        stage_key = f"{_STAGE.get(c['task'], c['task'])}_cost"
        if stage_key in t:
            t[stage_key] += cost
    return t


def _analyze_all(vault: Path) -> None:
    """Compact per-run comparison table (every usage-<ts>.json in the vault) + a grand total."""
    files = usage_files(vault)

    print()
    print("━" * 108)
    print(f"  Vault  {vault.name}  ({len(files)} run{'s' if len(files) != 1 else ''})")
    print("━" * 108)

    rows = []
    all_calls = []
    for f in files:
        calls = _load(f).get("calls", [])
        all_calls.extend(calls)
        rows.append((f.stem, _run_totals(calls), len(calls), _run_backends(calls)))
    name_w = min(28, max(len(name) for name, _, _, _ in rows))
    # Backend is the column that explains an otherwise inexplicable cost gap between two runs of
    # the same model (#475), so it sits next to the run name rather than off the right edge.
    backend_w = max(max(len(b) for _, _, _, b in rows), len("Backend"))

    hdr = (f"  {'Run':<{name_w}}  {'Backend':<{backend_w}}  {'Calls':>5}  {'Classifier':>10}  "
           f"{'Extractor':>10}  {'Finalizer':>10}  "
           f"{'Input':>9}  {'C.read':>9}  {'C.write':>9}  {'Output':>8}  {'Time':>7}  {'Cost':>8}")
    print()
    print(hdr)
    print(f"  {'─' * (len(hdr) - 2)}")

    grand = dict(_ZERO_TOTALS, classifier_cost=0.0, extractor_cost=0.0, finalizer_cost=0.0)
    n_calls_total = 0
    for name, t, n_calls, backends in sorted(rows, key=lambda r: -r[1]["cost_usd"]):
        trunc = (name[:name_w - 1] + "…") if len(name) > name_w else name
        print(
            f"  {trunc:<{name_w}}  {backends:<{backend_w}}  {n_calls:>5}  "
            f"${t['classifier_cost']:>9.4f}  ${t['extractor_cost']:>9.4f}  "
            f"${t['finalizer_cost']:>9.4f}  {_fmt(t['input_tokens']):>9}  {_fmt(t['cache_read_tokens']):>9}  "
            f"{_fmt(t['cache_write_tokens']):>9}  {_fmt(t['output_tokens']):>8}  {_fmt_secs(t['latency_s']):>7}  "
            f"${t['cost_usd']:>7.4f}"
        )
        for k in grand:
            grand[k] += t[k]
        n_calls_total += n_calls

    print(f"  {'─' * (len(hdr) - 2)}")
    print(
        f"  {'TOTAL':<{name_w}}  {'':<{backend_w}}  {n_calls_total:>5}  "
        f"${grand['classifier_cost']:>9.4f}  "
        f"${grand['extractor_cost']:>9.4f}  ${grand['finalizer_cost']:>9.4f}  "
        f"{_fmt(grand['input_tokens']):>9}  {_fmt(grand['cache_read_tokens']):>9}  "
        f"{_fmt(grand['cache_write_tokens']):>9}  {_fmt(grand['output_tokens']):>8}  "
        f"{_fmt_secs(grand['latency_s']):>7}  ${grand['cost_usd']:>7.4f}"
    )
    note = _subscription_note(all_calls)
    if note:
        print(f"  {_YELLOW}⚠{_RESET}  {_DIM}{note}{_RESET}")
    _print_cost_per_page(vault, grand["cost_usd"])


def cmd_usage(args) -> None:
    _, info, vault = _resolve_vault(args.project)
    files = usage_files(vault)
    if not files:
        sys.exit(f"Error: no ingest runs recorded yet for {info['name']} — run `watchdog dig` first.")

    if args.all:
        _analyze_all(vault)
        return

    if args.run:
        matches = [f for f in files if args.run in f.stem]
        if not matches:
            sys.exit(f"Error: no run matching '{args.run}' found for {info['name']}")
        if len(matches) > 1:
            sys.exit(f"Ambiguous run — matches: {', '.join(f.stem for f in matches)}")
        _analyze_run(matches[0], vault)
        return

    _analyze_run(files[-1], vault)   # filenames sort chronologically
