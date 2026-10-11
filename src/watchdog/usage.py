"""Token, cost and latency of an investigation's runs, as Activity → Usage shows them.

Reads `.watchdog/registry/usage/usage-<ts>.json` (D50) and groups calls by stage (classifier,
extractor, finalizer, plus Ask Claude and web research sessions). Cost comes from each record, as
computed by `model_client` at call time."""

import json
import re
from pathlib import Path

from watchdog.model_catalog import display_name
from watchdog.pipeline.json_io import _read_json, _read_json_or

# task name -> stage bucket.
STAGE = {
    "classify": "classifier",
    # `verify` (#535) buckets with the extractor because that is what it is: the same model, on
    # the same document, at the same stage — a processing run's spend has to read as one
    # number whether or not the pass ran.
    "extract": "extractor", "extract-section": "extractor", "verify": "extractor",
    "reconcile": "finalizer", "entity-synthesis": "finalizer", "timeline-dedup": "finalizer",
    "timeline-precision": "finalizer", "briefing": "finalizer", "request-dedup": "finalizer",
    "relationship-labels": "finalizer",
    # A conversation's turns (D296), recorded per session, never inside a processing run.
    "ask": "ask", "research": "research",
}


STAGE_ORDER = ("classifier", "extractor", "finalizer", "ask", "research")




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


def batch_lifecycle_note(call: dict) -> str:
    """`submitted -> ended -> collected` summary for a batch-collected stage — the middle and
    last of those routinely differ by hours under D52's submit-and-exit design, since collection
    happens whenever a later, unrelated processing run happens to notice the batch has
    ended, not right after it does."""
    submitted, ended, collected = (call.get("batch_submitted_at"), call.get("batch_ended_at"),
                                   call.get("batch_collected_at"))
    processed, idle = _batch_span(submitted, ended), _batch_span(ended, collected)
    return (
        f"batch {call.get('batch_id') or '?'} · submitted {submitted or '?'} · "
        f"ended {ended or '?'}" + (f" (processed {processed})" if processed else "") +
        f" · collected {collected or '?'}" + (f" (idle {idle})" if idle else "")
    )


def wall_span(calls: list[dict]) -> float | None:
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


def peak_concurrency(calls: list[dict]) -> int:
    """Highest number of `calls` with overlapping [start, end) intervals at any instant (#457) —
    what the "N calls ran concurrently" line used to report was just `len(calls)`, the stage's
    total call count, which overstates it for any run capped below that by `--concurrency`: 4
    calls that ran two at a time (`--concurrency 2`) both extend the wall-clock span past the
    fastest call's latency (so the "ran concurrently" line fires) and total 4, even though only
    2 were ever in flight together. A standard sweep over start/end events tracks the real peak
    instead. Calls with no `end_ts` (usage files written before #317's follow-up) are excluded,
    same as `wall_span`; returns 1 if none carry one (nothing to sweep, no overlap knowable)."""
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


def stage_models(calls: list[dict]) -> str:
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
CLAUDE_BACKENDS = ("claude-agent-sdk", "claude-api", "claude-batch")


def _backend_label(backend: str | None, auth_mode: str | None) -> str:
    """`claude-agent-sdk (subscription)` for a Claude backend, bare `deepseek` for the rest —
    auth mode is only interesting where it selected the backend."""
    if not backend:
        return "?"
    if backend in CLAUDE_BACKENDS and auth_mode:
        return f"{backend} ({auth_mode})"
    return backend


def stage_backends(calls: list[dict]) -> str:
    """Distinct `backend (auth)` labels across `calls`, first-seen order — the backend twin of
    `stage_models`. Joined rather than assumed single, since a finalizer stage can be split
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


def run_backends(calls: list[dict]) -> str:
    """Compact distinct `backend/auth` tags for one run — e.g. `sdk/sub` or `api/key, deepseek`.
    Claude backends carry the auth suffix; others don't (they are always api-key)."""
    seen = []
    for c in calls:
        backend = c.get("backend")
        tag = _short_backend(backend)
        if backend in CLAUDE_BACKENDS:
            tag = f"{tag}/{_short_auth(c.get('auth_mode'))}"
        if tag not in seen:
            seen.append(tag)
    return ", ".join(seen) or "—"


def subscription_note(calls: list[dict]) -> str | None:
    """Warning text when any Claude call in `calls` ran on subscription auth: `cost_usd` is then
    a list-price projection, not money that was billed. Presenting it as a plain dollar figure
    alongside metered runs is how a subscription arm gets mistaken for a priced one (#475)."""
    if any(c.get("backend") in CLAUDE_BACKENDS and c.get("auth_mode") == "subscription"
           for c in calls):
        return ("subscription auth — Claude costs below are list-price equivalents, "
                "not amounts billed")
    return None


def cost_by_key(calls: list[dict]) -> list[dict]:
    """Cost per labelled key that paid (#690, D290): `[{"label", "cost_usd", "calls"}]`, most
    expensive first. A call with no `key_label` (a subscription call, a local model, or a record
    from before labels) is grouped under None."""
    out: dict = {}
    for c in calls:
        row = out.setdefault(c.get("key_label"), {"label": c.get("key_label"), "cost_usd": 0.0, "calls": 0})
        row["cost_usd"] += c.get("cost_usd") or 0.0
        row["calls"] += 1
    return sorted(out.values(), key=lambda r: -r["cost_usd"])


_PAGE_RANGE_RE = re.compile(r"^pages (\d+)–(\d+)")


def pages_in_detail(detail: str | None) -> int | None:
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


def corpus_pages(vault: Path) -> tuple[int, int] | None:
    """(total_pages, document_count) covering every ingested document, not just the run being
    displayed — the right denominator for cost/page.

    Reads `.watchdog/extracted/*.json` (#457), the artifacts processing stages before any
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
                doc = _read_json(f).get("document", {})
            except (json.JSONDecodeError, OSError):
                continue
            pages += doc.get("page_count") or 0
            n += 1
    if n:
        return (pages, n)

    reg = vault / ".watchdog" / "registry" / "documents.json"
    docs = _read_json_or(reg, None)
    if docs is None:
        return None
    if not docs:
        return None
    pages = sum((v.get("page_count") or 0) for v in docs.values() if isinstance(v, dict))
    return (pages, len(docs))


def run_totals(calls: list[dict]) -> dict:
    t = dict(_ZERO_TOTALS, classifier_cost=0.0, extractor_cost=0.0, finalizer_cost=0.0)
    for c in calls:
        cost = c.get("cost_usd") or 0.0
        t["input_tokens"] += c["input_tokens"]
        t["output_tokens"] += c["output_tokens"]
        t["cache_read_tokens"] += c["cache_read_tokens"]
        t["cache_write_tokens"] += c["cache_write_tokens"]
        t["cost_usd"] += cost
        t["latency_s"] += c.get("latency_s") or 0.0
        stage_key = f"{STAGE.get(c['task'], c['task'])}_cost"
        if stage_key in t:
            t[stage_key] += cost
    return t

