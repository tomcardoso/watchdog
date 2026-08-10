"""Python ingest orchestrator (#118 Workstream 3).

Replaces the Claude Code `/watchdog-ingest` skill: Python drives the loop and calls the
model (`model_client.acomplete_json`) only for reasoning — classification, extraction,
and (in `_post_ingest`, added in a later phase) synthesis/timeline/briefing. Everything
mechanical reuses the existing deterministic pieces: `preflight.run`, `postflight.run`
(validates + writes the vault), and the registry write-lock inside `write_vault`.

Documents are extracted concurrently, bounded by a semaphore; the registry lock already
serializes the vault writes and `_reconcile_entity_ids` handles the parallel new-id race.
"""

import asyncio
import datetime
import hashlib
import json
import re
import signal
import sys
import time
from pathlib import Path

from watchdog import model_client, skills_catalog
from watchdog.cmd.base import _BOLD, _CYAN, _DIM, _GREEN, _RESET, _YELLOW
from watchdog.cmd.live import LiveRegion
from watchdog.pipeline import (
    abort, batch_extract, harvest, leads, merge, preflight, postflight, prompts, reconcile,
    requests, schemas, section, sidecar, synthesis_bundle, timeline, verify, watchlist,
)
from watchdog.pipeline.write_vault import _doc_slug

DEFAULT_CONCURRENCY = 5

# The finalizer stage's own `_call_model(task=...)` names (#417) — every call a *standalone*
# `watchdog bark` makes belongs to this set, which is what `ingest_setup.finalize_cost_estimate`
# uses to recognize a finalize-only usage-<ts>.json file: a `run()` ingest's own finalize tail
# shares that run's single usage file with extraction/classification, so it never qualifies.
FINALIZE_TASKS = {"reconcile", "entity-synthesis", "timeline-dedup", "timeline-precision", "briefing"}


# During extraction this holds the live status region (#151); per-document rows redraw in
# place and finished/failed lines + notes scroll above it. None outside extraction (and when
# stdout isn't a TTY), so `_say` falls back to plain append-only printing.
_board: LiveRegion | None = None

# The command a "re-run to resume/collect later" message should name for the current run (#441,
# D138). `cmd_ingest` owns which surface it is — `watchdog dig` for a `dig` run, bare `watchdog`
# for the guided walk or the deprecated `ingest` — and passes it as `run`'s `resume_hint`, which
# stashes it here so the extraction-side notices (rate-limit stop, batch submit/poll) point back
# at the right entry point rather than always saying `dig`. One run per process (behind the run
# lock), same single-run assumption `_board`/`_usage` already rely on.
_resume_hint: str = "watchdog dig"

# Per-call token/cost telemetry for the current run (A2) — a list of dicts, one per successful
# model call, accumulated by `_call_model`. None outside a `run`/standalone `finalize` call, so
# unit tests that exercise the per-document helpers directly (without going through either) don't
# need to know about it. `ModelResult.usage`/`cost_usd` were previously computed and discarded —
# this is the prerequisite for answering "how many tokens did this ingest spend, by stage?"
# without spelunking Claude Code session logs.
_usage: list[dict] | None = None

# Path of the current run's `usage-<ts>.partial.jsonl` (#407) — set alongside `_usage` by
# `_begin_usage_run`, mirrored into `_record_usage` so every call's record lands on disk the
# moment it completes, not just in the end-of-run `usage-<ts>.json`. None whenever `_usage` is.
_usage_partial_path: Path | None = None


def _say(msg: str) -> None:
    """Print a styled progress line to the terminal (indented per the CLI style guide).
    Routes through the live region as a scrollback note when one is active."""
    line = f"  {msg}"
    if _board is not None:
        _board.note(line)
    else:
        print(line, flush=True)


def _settle(sha: str, line: str) -> None:
    """Print a document's terminal line (OK / ✗), clearing its in-flight live row if present."""
    if _board is not None:
        _board.finish(sha, line)
    else:
        print(line, flush=True)


def _record_usage(task: str, *, model: str, backend: str, usage: dict | None,
                  cost_usd: float | None, attempts: int = 1, latency_s: float = 0.0,
                  effort: str | None = None, auth_mode: str | None = None,
                  filename: str | None = None, detail: str | None = None,
                  pruned: list[str] | None = None, failed: bool = False,
                  batch_meta: dict | None = None, rate_limit: dict | None = None) -> None:
    """Append one call's usage to the run-scoped `_usage` accumulator, if one is active.
    Tolerates both Anthropic-style (`input_tokens`/`output_tokens`) and OpenAI-compatible
    (`prompt_tokens`/`completion_tokens`) usage dicts (D37) — the latter's cache-token fields
    aren't modelled yet (matches `model_client._openai_cost`'s own v1 simplification).

    Takes explicit fields rather than a `model_client.ModelResult` so a batch-collected item
    (D52, no live model call at this point) can feed it too, not just `_call_model` (D64).
    `filename`/`detail` (e.g. a page range or section label) let a per-run usage file attribute
    each call to a document, not just a task name (#247). `latency_s` is per-call wall-clock
    time; batch-collected items have no live call to time, so they keep the 0.0 default (#317).
    `effort` records the reasoning-effort tier the call was made with (or None if the model/task
    doesn't take one), and `auth_mode` ("subscription"/"api-key") which billing lane paid for it
    — both surfaced per call by `watchdog usage` (#319). `end_ts` is the call's completion time
    (epoch seconds); paired with `latency_s` it gives each call a [start, end] interval, so
    `watchdog usage` can report wall-clock elapsed per stage — the real time a concurrently-run
    stage took — alongside the summed per-call time (#317 follow-up).

    `api_ms`/`num_turns` (#402) are the `claude-agent-sdk` harness's own timing — time actually
    spent in API requests vs. the call's wall-clock `latency_s`, and the internal request count.
    They're only added to the record when `usage` carries them, so records for every other
    backend stay exactly as they were — a large `latency_s` − `api_ms` gap is the signature of
    the harness throttling/backing off internally rather than the model itself being slow.

    `pruned` (D124): dotted/bracketed paths of any JSON keys the model emitted outside the
    schema and that `model_client._prune_unknown` removed rather than failing validation over.
    Only added when non-empty, so systematic schema drift stays visible in `watchdog usage`
    without cluttering every ordinary record.

    `failed` (D125): True when this record is a call that ultimately raised `ModelError` rather
    than returning — every attempt still spent real tokens, so it's recorded like any other call
    (and counted in the same subtotals) rather than vanishing from telemetry.

    `reasoning_tokens` (#354), read from `usage["completion_tokens_details"]["reasoning_tokens"]`
    when present (OpenAI reasoning models report it directly; Gemini's is reconstructed from its
    token counts by `model_client._fold_in_hidden_reasoning`, #547), is copied onto the record —
    the chain-of-thought share
    of `output_tokens`, which had been arriving on every such call since D108 and thrown
    away; it's the field that diagnosed the reasoning-starvation failure. Only added when it's
    non-zero — OpenAI reports a 0 here for its *chat* models, which carries no more information
    than the key's absence does — so every other backend's record shape is untouched.

    `usage["stop_reason"]`, when present (currently only `batch_extract.collect`'s items carry
    it), is copied to the record — a batch call's only truncation signal, since it has no
    continuation/repair path a live call gets. `batch_meta`
    (`{batch_id, submitted_at, ended_at, collected_at}`, from `_resume_batch`) is copied onto the
    record when given, so a batch-collected item's usage row carries its own full lifecycle
    instead of that living only in the transient `batch-pending.json` state that's deleted once
    collection succeeds.

    `rate_limit` (#563), when given, is the provider's own rate-limit response headers off this
    call (`model_client._rate_limit_headers` — `limit_tokens`/`remaining_tokens`/`reset_tokens`),
    copied onto the record so a run's usage file carries the provider's ground truth for what it
    counted against the caller's per-minute budget alongside the tokens we ourselves logged."""
    if _usage is None:
        return
    u = usage or {}
    record = {
        "task": task, "model": model, "backend": backend,
        "input_tokens": u.get("input_tokens", u.get("prompt_tokens", 0)) or 0,
        "output_tokens": u.get("output_tokens", u.get("completion_tokens", 0)) or 0,
        # Cache-read count, normalised across provider usage shapes: Anthropic reports
        # `cache_read_input_tokens`; OpenAI nests it under `prompt_tokens_details.cached_tokens`;
        # DeepSeek reports `prompt_cache_hit_tokens` (mirrors model_client._cached_input_tokens,
        # which does the same normalisation for cost calculation — this one was missed when the
        # OpenAI-compatible backends were added, so their real cache hits were logged as 0 even
        # though `cost_usd` already billed the discount correctly, issue #495).
        "cache_read_tokens": (u.get("cache_read_input_tokens")
                              or (u.get("prompt_tokens_details") or {}).get("cached_tokens")
                              or u.get("prompt_cache_hit_tokens") or 0),
        "cache_write_tokens": u.get("cache_creation_input_tokens", 0) or 0,
        "cost_usd": cost_usd, "attempts": attempts, "latency_s": latency_s, "effort": effort,
        "auth_mode": auth_mode, "filename": filename, "detail": detail, "end_ts": time.time(),
    }
    if u.get("duration_api_ms") is not None:
        record["api_ms"] = u["duration_api_ms"]
    if u.get("num_turns") is not None:
        record["num_turns"] = u["num_turns"]
    reasoning_tokens = (u.get("completion_tokens_details") or {}).get("reasoning_tokens")
    if reasoning_tokens:      # truthy, not `is not None`: OpenAI reports 0 here for chat models
        record["reasoning_tokens"] = reasoning_tokens
    if u.get("stop_reason"):
        record["stop_reason"] = u["stop_reason"]
    if pruned:
        record["pruned"] = pruned
    if failed:
        record["failed"] = True
    if batch_meta:
        record["batch_id"] = batch_meta.get("batch_id")
        record["batch_submitted_at"] = batch_meta.get("submitted_at")
        record["batch_ended_at"] = batch_meta.get("ended_at")
        record["batch_collected_at"] = batch_meta.get("collected_at")
    if rate_limit:
        record["rate_limit"] = rate_limit
    _usage.append(record)
    if _usage_partial_path is not None:
        # Durable per-call persistence (#407): appended synchronously, so a crash or a hard
        # interrupt between this call and the run's end-of-run `_write_usage` still leaves
        # this record on disk. `_record_usage` has no `await` in it, so no other call can
        # interleave with this write even under concurrent extraction.
        with open(_usage_partial_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


async def _call_model(*, task, prompt, schema, model=None, backend=None,
                      max_retries=1, effort=None, filename=None, detail=None,
                      vault: Path | None = None) -> "model_client.ModelResult":
    """Thin wrapper around `model_client.acomplete_json` that also records this call's usage
    (A2) — every reasoning call in the orchestrator goes through here instead of the client
    directly, so telemetry can't silently miss a call site. `filename`/`detail` are passed
    through to `_record_usage` unchanged (#247). `vault` (D124) is used only to log a WARN
    line to `ingest.log` when the call's JSON carried unexpected keys that were pruned rather
    than failing validation — pass it whenever a vault is in scope.

    A `ModelError` that carries usage/cost (D125 — the JSON-validation-failure and truncation
    paths in `acomplete_json`, the only ones where an attempt actually reached the model) is
    still recorded, flagged `failed=True`, before re-raising unchanged — every attempt spent
    real tokens even though none produced a usable result, and that spend used to vanish."""
    try:
        r = await model_client.acomplete_json(task=task, prompt=prompt, schema=schema, model=model,
                                              backend=backend, max_retries=max_retries, effort=effort)
    except model_client.ModelError as e:
        if e.usage is not None or e.cost_usd is not None:
            _record_usage(task, model=e.model, backend=e.backend, usage=e.usage, cost_usd=e.cost_usd,
                         attempts=e.attempts, effort=effort, auth_mode=e.auth_mode,
                         filename=filename, detail=detail, failed=True)
        raise
    _record_usage(task, model=r.model, backend=r.backend, usage=r.usage, cost_usd=r.cost_usd,
                 attempts=r.attempts, latency_s=r.latency_s, effort=effort, auth_mode=r.auth_mode,
                 filename=filename, detail=detail, pruned=r.pruned, rate_limit=r.rate_limit)
    if r.pruned and vault is not None:
        _log(vault, f"WARN {filename or task}: pruned unexpected JSON key(s) from model "
                    f"output: {', '.join(r.pruned)}")
    return r


def _recent_token_rate(records: list[dict], window_s: float = 60.0) -> int:
    """Total input+output tokens across calls whose `end_ts` (#563) falls within the last
    `window_s` seconds of the most recent record — a rolling-window tokens/min figure for
    "what was actually happening right before this run hit a rate limit", reconstructed the same
    way issue #563's own diagnosis was (from each call's completion time), not estimated from
    `extract_concurrency`. 0 for an empty list."""
    if not records:
        return 0
    latest = max(r["end_ts"] for r in records)
    cutoff = latest - window_s
    return sum(r["input_tokens"] + r["output_tokens"] for r in records if r["end_ts"] >= cutoff)


def _usage_totals(records: list[dict]) -> dict:
    return {
        "input_tokens": sum(r["input_tokens"] for r in records),
        "output_tokens": sum(r["output_tokens"] for r in records),
        "cache_read_tokens": sum(r["cache_read_tokens"] for r in records),
        "cache_write_tokens": sum(r["cache_write_tokens"] for r in records),
        # `reasoning_tokens` (#354) only appears on OpenAI reasoning-model records — `r.get(...)`
        # with a 0 default, not `r[...]`, so older records and every non-reasoning backend still
        # sum cleanly.
        "reasoning_tokens": sum(r.get("reasoning_tokens", 0) for r in records),
        "cost_usd": round(sum(r["cost_usd"] or 0.0 for r in records), 6) if records else None,
        "latency_s": round(sum(r.get("latency_s") or 0.0 for r in records), 3),
    }


def usage_files(vault: Path) -> list[Path]:
    """Every persisted `usage-<ts>.json` for this vault, oldest first (#319). Checks both the
    current location (`.watchdog/registry/usage/`) and the pre-move flat location
    (`.watchdog/registry/`) directly, so a vault ingested before that reorganization doesn't
    lose its older history — filenames sort chronologically regardless of which directory
    they're in, so the two sets merge correctly once combined."""
    reg_dir = vault / ".watchdog" / "registry"
    usage_dir = reg_dir / "usage"
    files = list(usage_dir.glob("usage-*.json")) if usage_dir.exists() else []
    files += list(reg_dir.glob("usage-*.json")) if reg_dir.exists() else []   # legacy location
    return sorted(files, key=lambda p: p.name)


def _write_usage_file(vault: Path, records: list[dict], ts: str,
                      est_input_tokens: int | None = None) -> str:
    """Write `records` to `.watchdog/registry/usage/usage-<ts>.json` for an explicit `ts`,
    shared by `_write_usage` (fresh timestamp, end of run) and `_consolidate_orphaned_usage`
    (the orphaned partial's own timestamp, #407).

    `est_input_tokens` (#417), when given, is added onto `totals` alongside the real
    `input_tokens` `_usage_totals` already computes — the naive chars/4 estimate summed across
    this run's successfully extracted documents (`_compact_result`'s own field), so a later
    `--estimate` can compare estimate to actual across this vault's own history the same way
    `cost_estimate` already does for dollars. Orphaned-partial recovery has no such per-run total
    to hand in (the individual call records don't carry it) and leaves it out."""
    usage_dir = vault / ".watchdog" / "registry" / "usage"
    usage_dir.mkdir(parents=True, exist_ok=True)
    relpath = f".watchdog/registry/usage/usage-{ts}.json"
    totals = _usage_totals(records)
    if est_input_tokens is not None:
        totals["est_input_tokens"] = est_input_tokens
    (vault / relpath).write_text(
        json.dumps({"calls": records, "totals": totals}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return relpath


def _write_usage(vault: Path, records: list[dict], est_input_tokens: int | None = None) -> str | None:
    """Persist this run's per-call token/cost telemetry to
    `.watchdog/registry/usage/usage-<ts>.json` (A2, relocated out of the flat Registry dir
    in #319 since this one accumulates a new file every run, unlike the fixed-size registries
    it used to sit alongside). Returns the vault-relative path, or None if the run made no
    model calls (e.g. an all-skipped batch)."""
    if not records:
        return None
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return _write_usage_file(vault, records, ts, est_input_tokens=est_input_tokens)


def _consolidate_orphaned_usage(vault: Path) -> None:
    """Fold any `usage-<ts>.partial.jsonl` left behind by a run that never reached its own
    end-of-run `_write_usage` (crash, kill -9, an interrupt during finalize — none of which
    `run()`'s own signal handling covers) into a real `usage-<ts>.json`, keyed by the partial's
    own timestamp so it sorts where that run actually happened (#407). Called once at the start
    of every top-level `run()`/standalone `finalize()`, before that call's own partial is opened,
    so `watchdog usage` never loses a run's spend just because it didn't finish cleanly."""
    usage_dir = vault / ".watchdog" / "registry" / "usage"
    if not usage_dir.exists():
        return
    for partial in sorted(usage_dir.glob("usage-*.partial.jsonl")):
        ts = partial.name[len("usage-"):-len(".partial.jsonl")]
        records = []
        try:
            for line in partial.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            records = []
        canonical = usage_dir / f"usage-{ts}.json"
        if records and not canonical.exists():
            _write_usage_file(vault, records, ts)
        partial.unlink(missing_ok=True)


def _begin_usage_run(vault: Path) -> None:
    """Start this run's usage accumulation (#407): first consolidate any orphaned partial from
    a previous aborted run, then open this run's own `usage-<ts>.partial.jsonl` that
    `_record_usage` appends each call's record to as it completes. Called by every top-level
    entry point — `run()` and a standalone `finalize()` — in place of the bare `_usage = []`
    this replaced."""
    global _usage, _usage_partial_path
    _consolidate_orphaned_usage(vault)
    _usage = []
    usage_dir = vault / ".watchdog" / "registry" / "usage"
    usage_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    _usage_partial_path = usage_dir / f"usage-{ts}.partial.jsonl"


def _end_usage_run(vault: Path, est_input_tokens: int | None = None) -> tuple[str | None, dict | None]:
    """Write this run's canonical `usage-<ts>.json` and remove its now-redundant partial
    (#407) — the counterpart to `_begin_usage_run`, called at every exit point that used to
    call `_write_usage` directly on a standalone/top-level accumulator. Returns the same
    `(usage_path, usage_totals)` pair those call sites assembled by hand.

    `est_input_tokens` (#417) is `run()`'s own sum of this run's successfully extracted
    documents' naive chars/4 estimate — passed through to `_write_usage`/`_write_usage_file` and
    mirrored onto the returned `totals` so both the persisted file and the in-memory summary
    agree. The two `finalize()` exit points never extract anything, so they call this with the
    default `None` and no such field appears."""
    global _usage, _usage_partial_path
    path = _write_usage(vault, _usage, est_input_tokens=est_input_tokens)
    totals = _usage_totals(_usage) if _usage else None
    if totals is not None and est_input_tokens is not None:
        totals["est_input_tokens"] = est_input_tokens
    if _usage_partial_path is not None:
        _usage_partial_path.unlink(missing_ok=True)
    _usage_partial_path = None
    _usage = None
    return path, totals


def latest_usage(vault: Path) -> dict | None:
    """The most recent ingest run's token/cost totals (F5, #222), or None if none exist yet —
    `watchdog status` surfaces this so a subscription user can see what a dump cost before
    deciding whether to kick off another one."""
    files = usage_files(vault)
    if not files:
        return None
    try:
        data = json.loads(files[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data.get("totals")


DEFAULT_CLASSIFY_PAGES = 5
# Per-section input budget when falling back to sectioning after a whole-doc extraction
# overruns the output ceiling. Small, so each section's output stays well under the cap;
# section.run caps it at half the document so a splittable doc yields ≥2 sections.
_FALLBACK_SECTION_TOKENS = 15_000
# Safety cap on classifier input: bounds a pathological single huge page; the page
# count (classify_pages) is the primary limiter, so keep this generous.
_CLASSIFY_EXCERPT_CHARS = 24000
# 24-bit RGB ints for Obsidian graph entity-type colour groups.
_GRAPH_PALETTE = [0x4C9A2A, 0xC0392B, 0x2980B9, 0x8E44AD, 0xD35400,
                  0x16A085, 0xF39C12, 0x7F8C8D, 0x2C3E50, 0xC2185B]


def _log(vault: Path, msg: str) -> None:
    log = vault / ".watchdog" / "registry" / "ingest.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except OSError:
        pass


def _update_graph_colours(vault: Path) -> None:
    """Give each entity-type folder a distinct colour group in Obsidian's graph view."""
    gpath = vault / ".obsidian" / "graph.json"
    edir = vault / "entities"
    if not gpath.exists() or not edir.exists():
        return
    try:
        graph = json.loads(gpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    groups = graph.setdefault("colorGroups", [])
    existing = {g.get("query") for g in groups}
    added = False
    for t in sorted(p.name for p in edir.iterdir() if p.is_dir()):
        query = f"path:entities/{t}"
        if query not in existing:
            groups.append({"query": query,
                           "color": {"a": 1, "rgb": _GRAPH_PALETTE[len(groups) % len(_GRAPH_PALETTE)]}})
            existing.add(query)
            added = True
    if added:
        gpath.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")


def _read_brief(vault: Path) -> str | None:
    p = vault / "context.md"
    return p.read_text(encoding="utf-8") if p.exists() else None


def _pages_text(pages: list[dict]) -> str:
    return "\n\n---\n\n".join(
        f"<!-- PAGE {pg['page']} -->\n\n{pg.get('markdown', '')}" for pg in pages
    )


def _candidates_checklist(text: str, *, vault: Path | None = None, sha: str | None = None,
                          filename: str | None = None, flow: str = "") -> str:
    """Tier 0 candidate harvest (#361/D123): deterministic spans (money, figure, percent,
    date, docket) plus optional local-NER entities, rendered as the per-page checklist injected
    into the extraction prompt. `text` carries the same `<!-- PAGE N -->` markers as `_pages_text`
    output (whole-document or one section's), so this works for both extraction paths.

    `vault`/`sha`/`filename`/`flow`, when given, mark this step on the document's live in-flight
    row before it runs and log its result to ingest.log after (#411) — GLiNER's local model load
    + inference (`harvest_entities`) is the one step that can silently run for minutes on a long
    document; without this the row just sat on "extracting…" the whole time, with no sign
    anything was happening. Callers pass these only from the two hot, per-document extraction
    paths (`_simple_extract`, `_extract_sectioned`) — the rarer batch-repair/submit paths omit
    them and get the old silent behaviour, since they have no live per-document row today."""
    if sha is not None:
        tty = f"{_DIM}→  {filename}  {flow}{' · ' if flow else ''}harvesting candidates…{_RESET}"
        plain = f"{_DIM}→  {filename}  harvesting candidates…{_RESET}"
        if _board is not None:
            _board.update(sha, f"  {tty}", f"  {plain}")
        else:
            _say(plain)
    candidates = harvest.harvest(text) + harvest.harvest_entities(harvest.split_pages(text))
    if sha is not None and vault is not None:
        n = len(candidates)
        _log(vault, f"HARVEST {filename}: {n} candidate{'s' if n != 1 else ''}")
    return harvest.format_checklist(candidates)


def _sidecar_skill(sidecar_text: str | None, *, filename: str) -> str | None:
    """A per-document record-skill pin from the sidecar's `skill:` field, resolved
    deterministically in Python — never shown to the classifier. Lets one ingest queue mix
    document types without `--skill` forcing a single pin across the whole run (D120: benchmarking
    a corpus that spans more than one skill needed this without a second `chew`/`ingest` pass per
    skill). `sidecar_text` is already filtered/allowlisted at chew time (pipeline/sidecar.py,
    D121) — nothing here reads `_INCOMING/` again."""
    value = sidecar.skill_pin(sidecar_text)
    if not value:
        return None
    resolved = skills_catalog.resolve(value)
    if not resolved:
        _say(f"  {_YELLOW}⚠{_RESET}  {filename}: sidecar pins unknown skill "
             f"'{value}' — classifying instead{_RESET}")
    return resolved


def _stamp_document(extraction: dict, *, sha: str, pf: dict, skill_label: str,
                    skill_text: str | None = None, extract_model: str | None = None,
                    extract_effort: str | None = None) -> None:
    """Stamp the deterministic, Python-owned document fields onto the extraction, before
    post-flight consumes it. Identity (sha256/filename/original_path/page_count) and provenance
    (source/obtained) are values the pipeline already holds — the model is no longer asked to
    echo them, so we set the authoritative values here. Stamping the sha in particular removes a
    latent failure mode: write_vault keys the vault write on `document.sha256`, so a model
    mis-transcription of the 64-char hash would desync the write from the queue/registry.

    `record_skill_hash`/`extract_model`/`extract_effort` (#268) are extraction provenance
    alongside `record_skill`: the skill's *content* can change (#68) without its filename
    changing, and per-run usage (D50) records model/effort per call but not per document — so
    without these, a vault can't answer "what produced this extraction" once skills or model
    config move on."""
    from watchdog.pipeline.write_vault import slugify
    doc = extraction.setdefault("document", {})
    doc["record_skill"] = skill_label
    doc["record_skill_hash"] = (
        hashlib.sha256(skill_text.encode("utf-8")).hexdigest()[:12] if skill_text else None)
    doc["extract_model"] = model_client.resolve_model_id(extract_model) if extract_model else None
    doc["extract_effort"] = extract_effort
    doc["sha256"] = sha
    doc["filename"] = pf["filename"]
    doc["original_path"] = pf.get("original_path")
    doc["page_count"] = pf.get("page_count") or len(pf["pages"])
    doc.update(sidecar.provenance(pf.get("sidecar")))
    # The filtered sidecar text itself (D121) — carried onto the document so write_vault can
    # re-materialize a .yml in morgue without ever reading _INCOMING/ again.
    doc["sidecar"] = pf.get("sidecar")
    # File-intrinsic embedded metadata (#369) — captured deterministically at chew time and
    # stamped here, same posture as sha256/filename above: a claim the file makes about itself,
    # never asked of the model.
    doc["file_metadata"] = pf.get("file_metadata") or {}
    # morgue_document_type is just the slug form of document_type — derive it deterministically
    # rather than asking the model for the same fact twice (it names the morgue folder).
    extraction["morgue_document_type"] = slugify(doc.get("document_type") or "") or "document"
    # morgue_entity_id is used raw as a morgue path segment (write_vault) — slugify the model's
    # value here too, so a value with spaces or an embedded path separator (e.g. "Acme Corp" or
    # "acme/subsidiary") can't produce a broken morgue directory layout or wikilinks.
    extraction["morgue_entity_id"] = slugify(extraction.get("morgue_entity_id") or "")


async def _classify(doc_excerpt: str, model: str, backend: str | None = None,
                    filename: str | None = None, sidecar: str | None = None,
                    vault: Path | None = None) -> str:
    r = await _call_model(
        task="classify", model=model, backend=backend, schema=schemas.CLASSIFY,
        prompt=prompts.build_classify_prompt(doc_excerpt, skills_catalog.build_index(), sidecar),
        filename=filename, vault=vault,
    )
    return r.parsed.get("skill") or "general-records.md"


def _briefing_facts(doc: dict) -> list[dict]:
    """Project key_facts down to what the briefing needs — the fact text and, when the fact is a
    datable occurrence, its date (for chronology). Drops page/basis/entities/quote, which are
    noise for narrative briefing. This is what now supplies the briefing its figures and timeline,
    in place of the scratchpad's hand-retyped 'Key figures'/'Chronological' sections (#150)."""
    out = []
    for f in doc.get("key_facts", []):
        item = {"fact": f["fact"]}
        if f.get("date"):
            item["date"] = f["date"]
        out.append(item)
    return out


def _compact_result(sha: str, filename: str, extraction: dict, near_dup: dict, cost: float | None,
                    written: dict, est_input_tokens: int | None = None) -> dict:
    """`written` is the writer's report of what it did with this document's entities — which ids
    were new to the registry and which were added to. That is a deterministic fact `write_vault`
    holds; extraction no longer guesses at it via `match_id` (#381/D118). At extraction time this
    is always `{}` — write_vault hasn't run yet (#403 phase 1) — and `_commit_pending` patches
    the real split onto the persisted result once the commit pass runs it.

    There is no `contradictions` key any more: a single document cannot see a conflict, so
    nothing at this stage has one to report. The briefing's contradiction flags are fed by the
    finalizer's reconciliation pass instead (`_post_ingest`).

    `est_input_tokens` (#417) is the naive chars/4 estimate (`section.est_tokens_from_pages`) for
    this document's own pages — carried alongside the real `cost_usd` so a run's usage totals can
    compare what was estimated against what extraction actually consumed, the same way `cost_usd`
    already lets `watchdog usage` compare estimate to spend.
    """
    entities = extraction.get("entities", [])
    doc = extraction.get("document", {})
    result = {
        "sha256": sha, "filename": filename, "status": "ok",
        "document_type": doc.get("document_type"),
        "record_skill": doc.get("record_skill"),
        "date": doc.get("date_of_document"),
        "entity_count": len(entities),
        "new_entities": written.get("new_entities", []),
        "updated_entities": written.get("updated_entities", []),
        "key_facts": _briefing_facts(doc),
        "near_dup_similarity": near_dup.get("top_similarity", 0.0),
        "cost_usd": cost,
    }
    if est_input_tokens is not None:
        result["est_input_tokens"] = est_input_tokens
    return result


def _write_postflight(vault: Path, sha: str, extraction: dict) -> tuple[bool, list[str], list[str]]:
    """Returns (ok, errors, warnings). Warnings are collected rather than printed here —
    the caller only knows once the document has actually finished (not discarded for a repair
    retry) whether they're worth surfacing, and where in the live region's output they belong
    (tucked under this document's own OK line, not wherever a concurrent document happens to
    be at the moment post-flight ran, #333 follow-up).

    Post-flight only stages the extraction now (#403 phase 1) — it no longer writes to the
    vault, so there is no writer's new/updated entity split to return here. That comes from the
    commit pass at finalize-start instead, which patches it onto the persisted
    `result_<sha>.json` directly (`_commit_pending`)."""
    tmp = vault / ".watchdog" / "tmp" / f"wdg_ex_{sha}.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(extraction, ensure_ascii=False, indent=2), encoding="utf-8")
    warnings: list[str] = []
    outcome = postflight.run(vault, tmp, warn=warnings.append)
    ok = "errors" not in outcome
    return ok, outcome.get("errors", []), warnings


def _append_repair_note(base, errors: list[str]):
    """Append the post-flight repair note to a prompt, whether it's a plain string or a
    content-block list (A1) — the note is inherently volatile (only exists on retry), so it
    never belongs inside the cacheable prefix regardless of representation."""
    note = ("\n\nThe previous extraction was rejected:\n" + "\n".join(errors)
            + "\nReturn a corrected JSON object.")
    if isinstance(base, list):
        return base + [{"type": "text", "text": note}]
    return base + note


def _verifier_effort() -> str | None:
    """The reasoning effort the verification pass runs at (#535) — `low` unless a vault has
    pinned `verifier_effort` in config. Read here rather than threaded from the CLI because
    there is no flag for it: the pass is turned on and off by `--verify`, and its effort is an
    advanced knob in the same family as `empty_extraction_min_words` (D153). Low is the default
    for a cost reason, not a quality one — gap-finding against a supplied list is a comparison,
    and output tokens are where the pass's cost actually lives (D172)."""
    from watchdog.cmd import base
    try:
        cfg = json.loads(base.CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    effort = cfg.get("verifier_effort", "low")
    return effort if effort in model_client._EFFORT_LEVELS else "low"


async def _verify_facts(vault, sha, base, extraction, *, model, backend, filename, detail):
    """One verification call over the document just extracted, then a deterministic merge of
    whatever it returns (#535). Returns the call's cost; `extraction` is mutated in place.

    `base` is the *extraction call's own prompt blocks*, unmodified — see
    `prompts.build_verify_prompt` for why the identical objects rather than a rebuild. The two
    calls are back-to-back on the same model, which is what makes the shared prefix a cache hit
    rather than a second full-price read of the document.

    Failure is never fatal, and the catch is deliberately broad: a verifier that errors, times
    out, or returns nothing usable leaves the extraction exactly as it was. This pass exists to
    add recall to a result that is already complete and about to be staged — letting a *new*
    optional call fail a document that already extracted cleanly would trade the thing being
    measured for the thing that already works. Two things still propagate: a rate limit, which
    is session-wide and has to stop the run as it would from any other call, and cancellation
    (a `BaseException`, so `except Exception` never sees it)."""
    doc = extraction.get("document", {})
    if _board is not None:
        _board.update(sha, f"  {_DIM}→  {filename}  {detail} · verifying…{_RESET}",
                      f"  {_DIM}→  {filename}  verifying…{_RESET}")
    prompt = prompts.build_verify_prompt(
        base, key_facts=doc.get("key_facts") or [], entities=extraction.get("entities") or [])
    try:
        r = await _call_model(task="verify", model=model, backend=backend, prompt=prompt,
                              schema=schemas.VERIFY, effort=_verifier_effort(),
                              filename=filename, detail=f"{detail} (verify)", vault=vault)
    except model_client.RateLimitError:
        raise
    except Exception as e:
        _log(vault, f"WARN {filename}: verification pass failed, keeping extraction as-is ({e})")
        return 0.0
    stats = verify.merge_candidates(extraction, r.parsed.get("missing_facts") or [])
    if stats["added"] or stats["suppressed"]:
        _log(vault, f"VERIFY {filename} [{detail}]: {stats['added']} fact(s) added, "
                    f"{stats['suppressed']} suppressed as duplicate or unusable")
    return r.cost_usd or 0.0


async def _simple_extract(vault, sha, pf, skill_text, brief, model, skill_label,
                          effort=None, backend=None, verify_pass=False):
    """Whole-document extraction, with one repair attempt if post-flight rejects.

    `verify_pass` (#535) adds the verification call between the model's answer and post-flight,
    so the facts it recovers go through the same validation, quote/figure grounding, and
    entity fan-out as any other fact — there is no second class of fact downstream."""
    text = _pages_text(pf["pages"])
    page_count = pf.get("page_count") or len(pf["pages"])
    filename = pf["filename"]
    flow = f"{page_count}p · {skill_label}"
    # GLiNER inference over every page can take minutes on a long document — run it off the
    # event loop so it doesn't stall every other concurrent document.
    candidates = await asyncio.to_thread(
        _candidates_checklist, text, vault=vault, sha=sha, filename=filename, flow=flow)
    # Restore the row to "extracting…" now that harvesting is done and the model call is next.
    if _board is not None:
        _board.update(sha, f"  {_DIM}→  {filename}  {flow} · extracting…{_RESET}",
                      f"  {_DIM}→  {filename}  extracting…{_RESET}")
    base = prompts.build_extract_prompt(
        pages_text=text,
        skill_text=skill_text, sidecar=pf.get("sidecar"), brief=brief,
        known_document_types=pf.get("known_document_types", []),
        file_metadata=pf.get("file_metadata", {}), processing=pf.get("processing", {}),
        candidates=candidates, cache_document=verify_pass,
    )
    cost, errors, extraction, scratchpad = 0.0, [], {}, ""
    for _ in range(2):
        p = base if not errors else _append_repair_note(base, errors)
        detail = f"pages 1–{page_count}" + (" (repair)" if errors else "")
        try:
            try:
                r = await _call_model(task="extract", model=model, backend=backend,
                                      prompt=p, schema=schemas.EXTRACTION, effort=effort,
                                      filename=pf["filename"], detail=detail, vault=vault)
            except model_client.ModelError as e:
                # Reasoning starvation isn't fixed by the caller's usual recovery — sectioning
                # bounds a section's own *input*, and starvation isn't input-driven (#558) — so
                # retry once here at the next effort level down before giving up on this attempt.
                # `_lower_effort` returns None (no retry) once there's nowhere lower to go.
                lower = _lower_effort(effort) if e.starved else None
                if lower is None:
                    raise
                r = await _call_model(task="extract", model=model, backend=backend,
                                      prompt=p, schema=schemas.EXTRACTION, effort=lower,
                                      filename=pf["filename"], detail=detail, vault=vault)
        except model_client.ModelError as e:
            # No valid JSON after the client's own retries — often output truncated on a
            # dense doc. Report failure so the caller can fall back to sectioning.
            return extraction, scratchpad, cost, False, [f"extraction returned no valid JSON ({e})"], []
        cost += r.cost_usd or 0.0
        extraction = r.parsed
        scratchpad = extraction.pop("scratchpad", "")
        _stamp_document(extraction, sha=sha, pf=pf, skill_label=skill_label,
                        skill_text=skill_text, extract_model=model, extract_effort=effort)
        if verify_pass:
            # Runs on the repair attempt too: the repaired extraction is the one being staged,
            # so it is the one whose gaps matter. The verify prompt is built from `base`
            # regardless, never the repair-noted prompt, so its prefix stays byte-identical to
            # the original extraction call's.
            cost += await _verify_facts(vault, sha, base, extraction, model=model, backend=backend,
                                        filename=pf["filename"], detail=f"pages 1–{page_count}")
        ok, errors, warnings = _write_postflight(vault, sha, extraction)
        if ok:
            return extraction, scratchpad, cost, True, [], warnings
    return extraction, scratchpad, cost, False, errors, []


# Cap on the observations text carried into the next section's prompt (A5) — only the most
# recent section's, not every prior section's concatenated, since observations are
# forward-looking briefing leads (D33), not extraction context the model needs preserved.
_CARRY_OBSERVATIONS_CHARS = 2000


def _carry_text(entities_seen: dict, observations: str) -> str:
    """Build the carry-forward block from the entity ids seen in *any* section so far (deduped,
    one line each — not the running string-concatenation A5 replaced, which re-listed an entity
    once per section it appeared in) plus only the just-produced section's observations."""
    lines = []
    if entities_seen:
        lines.append("Entities so far:")
        lines += [f"- {eid} | {e.get('name')} | {e.get('type')}" for eid, e in entities_seen.items()]
    if observations:
        lines.append("Observations:\n" + observations[-_CARRY_OBSERVATIONS_CHARS:])
    return "\n".join(lines) + "\n" if lines else ""


def _stitch_digest(doc: dict, page_count: int | None) -> str:
    """Deterministic fallback when the digest call fails or returns empty: an orientation line
    from title/type/page_count, then the first few facts as plain sentences. Degraded but
    valid — never worth a retry loop (#279)."""
    head = doc.get("title") or "Untitled document"
    dtype = doc.get("document_type") or ""
    line = f"{head} — {dtype}" if dtype else head
    if page_count:
        line += f", {page_count} pages"
    facts = [t.rstrip(".") + "." for f in doc.get("key_facts", [])[:8]
             if (t := (f.get("fact") or "").strip())]
    return (line + ". " + " ".join(facts)).strip() if facts else line + "."


async def _compose_digest(doc: dict, page_count: int | None, model: str, backend: str | None,
                          filename: str, skill_text: str | None, brief: str | None,
                          sidecar: str | None, vault: Path | None = None) -> tuple[str, float]:
    """One small model call composing the whole-document digest from the merged key_facts
    (#279) — no section call ever sees the whole document, so this runs once after merge, on the
    extractor tier (the same model that read the sections), so both digest paths — inline for a
    whole-doc extraction, post-merge here — use one model. It is handed the same context a
    whole-document extractor gets short of the raw text (filename, domain skill, brief, sidecar),
    with the merged key_facts standing in for the text. Falls back to the deterministic stitch
    on any model failure or an empty response; returns (summary, cost)."""
    prompt = prompts.build_digest_prompt(
        filename=filename, title=doc.get("title", ""), document_type=doc.get("document_type", ""),
        page_count=page_count, skill_text=skill_text, brief=brief, sidecar=sidecar,
        key_facts=_briefing_facts(doc))
    try:
        r = await _call_model(task="digest", model=model, backend=backend,
                              prompt=prompt, schema=schemas.DIGEST,
                              filename=filename, detail="digest", vault=vault)
        summary = (r.parsed.get("summary") or "").strip()
        if summary:
            return summary, r.cost_usd or 0.0
        return _stitch_digest(doc, page_count), r.cost_usd or 0.0
    except model_client.ModelError:
        return _stitch_digest(doc, page_count), 0.0


async def _extract_one_section(vault, sha, pf, skill_text, sec, *, is_first, carry, brief,
                               model, effort, backend, repair_errors=None, verify_pass=False):
    """One section's extract-section call. `repair_errors`, when given, appends the post-flight
    repair note to the prompt (#505) — used only for a targeted section-1 retry, never the
    normal per-section loop.

    `verify_pass` (#535) verifies each section against its own text, right after that section's
    call — not the merged document at the end. A section's text is the only thing the verifier
    can be given a byte-identical prefix for, and it is also the right unit: the misses this pass
    recovers are page-local, and a whole-document re-read would cost what sectioning exists to
    avoid. The section's cost is folded into the returned result so the checkpoint written by the
    caller records what the section actually cost, and a resumed run replays it without paying
    for verification again."""
    sec_text = (vault / sec["pages_path"]).read_text(encoding="utf-8")
    # Off the event loop — see the comment in `_simple_extract`.
    candidates = await asyncio.to_thread(
        _candidates_checklist, sec_text, vault=vault, sha=sha, filename=pf["filename"],
        flow=sec["label"])
    # Restore the row to "extracting…" now that harvesting is done and the model call is next.
    if _board is not None:
        _board.update(sha, f"  {_DIM}→  {pf['filename']}  {sec['label']} · extracting…{_RESET}",
                      f"  {_DIM}→  {pf['filename']}  extracting…{_RESET}")
    base = prompts.build_section_prompt(
        pages_text=sec_text,
        skill_text=skill_text, carry_forward=carry, section_label=sec["label"],
        is_first=is_first, brief=brief,
        known_document_types=pf.get("known_document_types", []),
        file_metadata=pf.get("file_metadata", {}), processing=pf.get("processing", {}),
        candidates=candidates, cache_document=verify_pass,
    )
    prompt, detail = base, sec["label"]
    if repair_errors:
        prompt = _append_repair_note(base, repair_errors)
        detail += " (repair)"
    # Marks where the section's model call — the thing that can silently run for minutes —
    # actually begins, so ingest.log doesn't read as though the HARVEST line just above it
    # (which finishes in seconds) is what the elapsed time belongs to (#556).
    _log(vault, f"SECTION {pf['filename']} [{detail}]: extracting…")
    r = await _call_model(task="extract-section", model=model, backend=backend,
                          prompt=prompt, schema=schemas.SECTION, effort=effort,
                          filename=pf["filename"], detail=detail, vault=vault)
    if verify_pass:
        r.cost_usd = (r.cost_usd or 0.0) + await _verify_facts(
            vault, sha, base, r.parsed, model=model, backend=backend,
            filename=pf["filename"], detail=sec["label"])
    return r


async def _merge_sectioned(parts, pf, sha, skill_label, skill_text, model, effort, backend, brief,
                           vault):
    """Merge + whole-document digest + stamping — the steps between having every section's raw
    output and being ready for post-flight. Split out so the section-1 repair retry (#505) can
    redo it without duplicating the digest/stamp calls."""
    extraction = merge.merge_extractions(parts)
    scratchpad = "\n".join(p["observations"] for p in parts if p.get("observations"))
    doc = extraction.setdefault("document", {})
    page_count = pf.get("page_count") or len(pf.get("pages", []))
    doc["summary"], digest_cost = await _compose_digest(
        doc, page_count, model, backend, pf["filename"],
        skill_text, brief, pf.get("sidecar"), vault=vault)
    _stamp_document(extraction, sha=sha, pf=pf, skill_label=skill_label,
                    skill_text=skill_text, extract_model=model, extract_effort=effort)
    return extraction, scratchpad, digest_cost


# Post-flight errors that only section 1 — the one section ever prompted to supply document
# metadata and the morgue fields (see build_section_prompt's `is_first` branch) — can fix. A
# later section's output was never going to repair these, so a repair retry only needs to
# re-call section 1, not the whole document (#505).
_SECTION1_OWNED_ERROR_PREFIXES = ("morgue_entity_id ", "morgue_document_type ")


def _repairable_by_section1(errors: list[str]) -> bool:
    return bool(errors) and all(e.startswith(_SECTION1_OWNED_ERROR_PREFIXES) for e in errors)


def _section_checkpoint_path(vault: Path, sha: str, index: int) -> Path:
    # Same naming convention section.run() uses for a section's raw text (section_{sha}_{idx}.md)
    # and the one abort.py's cleanup already globs for (#498) — reusing it means an explicit
    # `watchdog ingest-abort` sweeps these away for free with no changes needed there.
    return vault / ".watchdog" / "tmp" / f"section_ex_{sha}_{index:02d}.json"


def _load_section_checkpoints(vault: Path, sha: str, sections: list[dict]) -> list[dict]:
    """Replay already-completed sections for this sha from disk (#498), so a retry after a rate
    limit, Ctrl-C, or a failure doesn't re-pay for calls already made. Trusts only a contiguous
    prefix starting at index 1 whose checkpointed section metadata exactly matches the freshly
    computed plan at the same index — section.run() is deterministic, so an unchanged plan
    reproduces identical section dicts (label, pages_path); any mismatch (a changed budget/model/
    backend between attempts, or a different section count) means the plan moved, and everything
    from that point on is re-extracted rather than risk merging content across a different section
    boundary than the one it was actually extracted from."""
    checkpoints = []
    for i, sec in enumerate(sections, start=1):
        path = _section_checkpoint_path(vault, sha, i)
        if not path.exists():
            break
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            break
        if data.get("section") != sec:
            break
        checkpoints.append(data)
    return checkpoints


def _write_section_checkpoint(vault: Path, sha: str, sec: dict, parsed: dict, cost_usd: float,
                              parts: list[dict] | None = None) -> None:
    """`parts` (#540), when given, holds 2+ results from a single planned section that truncated
    and was re-split in half (see `_extract_sectioned`'s main loop) — checkpointed together under
    this ORIGINAL section's `index`/dict, so there is still exactly one checkpoint per planned
    section and `_load_section_checkpoints`' identity check (and the `len(checkpoints)` indexing
    into `sections` it enables, #498) keeps working. `parsed` is always the first (or only)
    result, kept so a checkpoint written before #540 still replays."""
    record = {"section": sec, "parsed": parsed, "cost_usd": cost_usd}
    if parts:
        record["parts"] = parts
    path = _section_checkpoint_path(vault, sha, sec["index"])
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


def _split_section_text(text: str, paginated: bool) -> list[str]:
    """Split one section's own text into two roughly-equal halves for the truncation re-split
    (#540, see `_extract_sectioned`). A multi-page section splits on a page boundary — reusing
    `section.run`'s own `"\\n\\n---\\n\\n"` join separator between per-page chunks, so no page is
    ever cut mid-markdown — falling back to a character midpoint for a single-page or
    non-paginated section (character windows have no page boundary to split on)."""
    if paginated:
        pages = text.split("\n\n---\n\n")
        if len(pages) > 1:
            mid = len(pages) // 2
            return ["\n\n---\n\n".join(pages[:mid]), "\n\n---\n\n".join(pages[mid:])]
    mid = len(text) // 2
    return [text[:mid], text[mid:]]


def _half_label(text: str, parent_label: str, i: int, n: int) -> str:
    """The section label for one half of a re-split section (#540). A paginated half names the
    pages it actually holds, read back off the `<!-- PAGE n -->` markers `section.run` embeds —
    inheriting the parent's "pages 27–40" would overstate each half's range in three places that
    matter: the prompt's own `section_label`, the candidate-harvest flow label, and the `detail`
    field on the usage row, which is what a later cost or truncation investigation reads. Falls
    back to the parent label plus a part number when there are no markers to read (a
    character-split, non-paginated section)."""
    pages = re.findall(r"<!-- PAGE (\d+) -->", text)
    if pages:
        return f"pages {pages[0]}–{pages[-1]}" if pages[0] != pages[-1] else f"page {pages[0]}"
    return f"{parent_label} (part {i}/{n})"


def _ordered_parts(section_parts: dict[int, list[dict]]) -> list[dict]:
    """Per-section results flattened into the reading order `_merge_sectioned` expects. Sections
    are keyed by their planned index (#540), so sorting by it restores document order regardless
    of how many parts each section contributed or which order they were filled in."""
    return [p for idx in sorted(section_parts) for p in section_parts[idx]]


def _resplit_section(vault: Path, sha: str, sec: dict) -> list[dict]:
    """On a truncated `extract-section` call (#540), split just this ONE section's page range in
    half rather than re-sectioning the whole document — the plan (`sections`) never changes, so
    checkpoint identity (#498) is unaffected; only this one section's execution is. Returns two
    synthetic section dicts, each pointing at its own half's text file, for the caller to run
    through `_extract_one_section` exactly as it would a planned section."""
    text = (vault / sec["pages_path"]).read_text(encoding="utf-8")
    halves = _split_section_text(text, sec.get("paginated", False))
    out = []
    for i, half in enumerate(halves, start=1):
        path = vault / ".watchdog" / "tmp" / f"section_{sha}_{sec['index']:02d}_{i}.md"
        path.write_text(half, encoding="utf-8")
        out.append({"index": sec["index"],
                    "label": _half_label(half, sec["label"], i, len(halves)),
                    "paginated": sec.get("paginated", False),
                    "pages_path": str(path.relative_to(vault))})
    return out


def _clear_section_checkpoints(vault: Path, sha: str) -> None:
    tmp = vault / ".watchdog" / "tmp"
    for p in tmp.glob(f"section_ex_{sha}_*.json"):
        p.unlink(missing_ok=True)


def _lower_effort(effort: str | None) -> str | None:
    """The effort level one step below `effort` in `model_client._EFFORT_LEVELS`, or `None` if
    there's nowhere lower to go (#558) — `effort` is already `"low"`, or the call was made with
    no effort at all, which is how a model with no effort knob (e.g. Haiku, DeepSeek) always
    reaches here. Either way the caller should give up rather than retry with the same effort."""
    levels = model_client._EFFORT_LEVELS
    if effort not in levels:
        return None
    idx = levels.index(effort)
    return levels[idx - 1] if idx > 0 else None


async def _extract_sectioned(vault, sha, pf, skill_text, plan, model, skill_label,
                             effort=None, backend=None, brief=None, verify_pass=False):
    """Sequential per-section extraction with carry-forward, then deterministic merge.

    Each section's result is checkpointed to disk as it completes (#498) and replayed on a retry
    that lands on the same plan, so a rate limit, Ctrl-C, or a failed post-flight doesn't discard
    already-paid-for sections. Checkpoints are cleared once post-flight finally succeeds.

    One repair attempt if post-flight rejects on morgue_entity_id/morgue_document_type — the
    fields only section 1 is ever asked to supply. Mirrors _simple_extract's repair loop, but
    re-calls just section 1 rather than re-running the whole document, since a later section's
    output could never have fixed these anyway (#505).

    A truncated extract-section call gets one re-split retry (#540): the whole-document fallback
    below `_extract_document`'s whole-doc branch never reached this already-sectioned path, so a
    truncation here used to fail the document outright with no recovery at all. `parts`/
    `entities_seen`/`carry` are rebuilt with `c.get("parts") or [c["parsed"]]` so a checkpoint
    written before #540 (single `parsed`, no `parts`) still replays unchanged.

    A *starved* extract-section call (#558) — reasoning ate the whole output budget, an input-size
    problem the re-split above can't fix — gets a different one-shot retry instead: the same
    section, re-run one effort level down via `_lower_effort`. Before #558 this shared the
    re-split's bound: `ModelError.truncated` alone couldn't tell the two failures apart, so a
    starved section was "recovered" by re-splitting into halves that starved again, and the
    document failed outright exactly as it would with no recovery at all."""
    sections = plan["sections"]
    checkpoints = _load_section_checkpoints(vault, sha, sections)
    # Keyed by planned-section index rather than a flat list (#540): a re-split section contributes
    # 2 results instead of 1, and the #505 repair below replaces one *section's* worth of output —
    # which a flat list can't express, since `parts[0] = …` would leave a re-split section 1's
    # second half behind, merging its content twice. Flattened in section order at merge time.
    section_parts: dict[int, list[dict]] = {
        c["section"]["index"]: (c.get("parts") or [c["parsed"]]) for c in checkpoints}
    section_cost: dict[int, float] = {
        c["section"]["index"]: c.get("cost_usd") or 0.0 for c in checkpoints}
    entities_seen: dict[str, dict] = {}
    for c in checkpoints:
        for parsed in c.get("parts") or [c["parsed"]]:
            for e in parsed.get("entities") or []:
                if e.get("id"):
                    entities_seen[e["id"]] = {"name": e.get("name"), "type": e.get("type")}
    carry = ""
    if checkpoints:
        last_parts = checkpoints[-1].get("parts") or [checkpoints[-1]["parsed"]]
        carry = _carry_text(entities_seen, last_parts[-1].get("observations") or "")

    for sec in sections[len(checkpoints):]:
        try:
            r = await _extract_one_section(vault, sha, pf, skill_text, sec,
                                           is_first=(sec["index"] == 1), carry=carry, brief=brief,
                                           model=model, effort=effort, backend=backend,
                                           verify_pass=verify_pass)
        except model_client.ModelError as e:
            if not e.truncated:
                raise   # a rate limit, auth failure, or genuine schema failure — not ours to fix
            if e.starved:
                # Reasoning consumed the whole output budget before an answer, not the section's
                # own input — re-splitting would just repeat the same failure on a smaller input
                # (#558), so retry once at the next effort level down instead, which is the
                # actual lever for reasoning volume. `_lower_effort` returns None when there's
                # nowhere lower to go (already `"low"`, or the model has no effort knob at all),
                # in which case this falls through and raises exactly as it would with no retry.
                lower = _lower_effort(effort)
                if lower is not None:
                    r = await _extract_one_section(vault, sha, pf, skill_text, sec,
                                                   is_first=(sec["index"] == 1), carry=carry,
                                                   brief=brief, model=model, effort=lower,
                                                   backend=backend, verify_pass=verify_pass)
                    section_cost[sec["index"]] = r.cost_usd or 0.0
                    section_parts[sec["index"]] = [r.parsed]
                    _write_section_checkpoint(vault, sha, sec, r.parsed, section_cost[sec["index"]])
                    for e2 in r.parsed.get("entities") or []:
                        if e2.get("id"):
                            entities_seen[e2["id"]] = {"name": e2.get("name"), "type": e2.get("type")}
                    carry = _carry_text(entities_seen, r.parsed.get("observations") or "")
                    continue
                raise
            # Split just this section's page range in half and run both halves through the same
            # call, in order, threading carry/entities exactly as the loop above does. Both
            # results are checkpointed together under the ORIGINAL section (`sec`) below, so the
            # plan never changes and #498's checkpoint identity keeps matching. Bounded to one
            # re-split: a half that also truncates raises here uncaught, failing the document
            # exactly as before #540.
            halves = _resplit_section(vault, sha, sec)
            half_parts: list[dict] = []
            half_cost = 0.0
            for i, half in enumerate(halves):
                hr = await _extract_one_section(
                    vault, sha, pf, skill_text, half,
                    is_first=(sec["index"] == 1 and i == 0), carry=carry, brief=brief,
                    model=model, effort=effort, backend=backend, verify_pass=verify_pass)
                half_cost += hr.cost_usd or 0.0
                half_parts.append(hr.parsed)
                for e2 in hr.parsed.get("entities") or []:
                    if e2.get("id"):
                        entities_seen[e2["id"]] = {"name": e2.get("name"), "type": e2.get("type")}
                carry = _carry_text(entities_seen, hr.parsed.get("observations") or "")
            section_cost[sec["index"]] = half_cost
            section_parts[sec["index"]] = half_parts
            _write_section_checkpoint(vault, sha, sec, half_parts[0], half_cost, parts=half_parts)
            continue

        section_cost[sec["index"]] = r.cost_usd or 0.0
        section_parts[sec["index"]] = [r.parsed]
        _write_section_checkpoint(vault, sha, sec, r.parsed, section_cost[sec["index"]])
        for e in r.parsed.get("entities") or []:
            if e.get("id"):
                entities_seen[e["id"]] = {"name": e.get("name"), "type": e.get("type")}
        carry = _carry_text(entities_seen, r.parsed.get("observations") or "")

    parts = _ordered_parts(section_parts)
    extraction, scratchpad, digest_cost = await _merge_sectioned(
        parts, pf, sha, skill_label, skill_text, model, effort, backend, brief, vault)
    cost = sum(section_cost.values()) + digest_cost
    ok, errors, warnings = _write_postflight(vault, sha, extraction)

    if not ok and _repairable_by_section1(errors):
        r = await _extract_one_section(vault, sha, pf, skill_text, sections[0],
                                       is_first=True, carry="", brief=brief,
                                       model=model, effort=effort, backend=backend,
                                       repair_errors=errors, verify_pass=verify_pass)
        idx = sections[0]["index"]
        section_cost[idx] = section_cost.get(idx, 0.0) + (r.cost_usd or 0.0)
        # Replaces every part section 1 contributed, not just the first: the repair re-ran the
        # whole section, so a re-split section 1's second half is now superseded, not still due.
        section_parts[idx] = [r.parsed]
        _write_section_checkpoint(vault, sha, sections[0], r.parsed, section_cost[idx])
        parts = _ordered_parts(section_parts)
        extraction, scratchpad, digest_cost = await _merge_sectioned(
            parts, pf, sha, skill_label, skill_text, model, effort, backend, brief, vault)
        cost = sum(section_cost.values()) + digest_cost
        ok, errors, warnings = _write_postflight(vault, sha, extraction)

    if ok:
        _clear_section_checkpoints(vault, sha)

    return extraction, scratchpad, cost, ok, errors, warnings


def _queued_filename(vault: Path, sha: str) -> str | None:
    """Best-effort filename for a sha from its queue descriptor (active or _failed/)."""
    for sub in ("", "_failed"):
        p = vault / ".watchdog" / "queue" / sub / f"{sha}.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8")).get("filename")
            except (OSError, json.JSONDecodeError):
                return None
    return None


def _fail(vault: Path, sha: str, filename: str, reason: str) -> dict:
    """Log the failure and clean up this doc's artifacts (queue → _failed/) for retry."""
    # Resolve the filename (the catch-all path has only the sha) *before* abort.run moves
    # the queue file, so the ✗ line and log name the file instead of a bare sha.
    name = filename or _queued_filename(vault, sha) or sha[:7]
    _settle(sha, f"  {_YELLOW}✗{_RESET}  {name}  {_DIM}{reason}{_RESET}")
    _log(vault, f"FAILED {name}: {reason}")
    # keep_section_checkpoints (#498): an automatic failure should still let a later retry resume
    # from whatever sections already succeeded, rather than re-paying for them from section 1.
    abort.run(vault, sha, keep_section_checkpoints=True)   # removes staging/section temp, moves the queue file to _failed/
    return {"sha256": sha, "filename": filename or name, "status": "failed", "reason": reason}


async def _resolve_skill(vault: Path, pf: dict, pinned_skill: str | None, classify_model: str,
                         classify_pages: int, classify_backend: str | None, *, filename: str,
                         on_classify=None) -> tuple[str, str]:
    """This document's record skill, as `(skill_text, skill_label)`, following D120's precedence:
    the document's own sidecar `skill:` pin, then a run-wide `--skill`/`default_skill`, then one
    cheap classify call.

    Shared by the synchronous per-document path (`_extract_document`) and the batch submit path
    (`_submit_batch`) so the precedence can't drift between them — it is the rule that lets one
    queue mix document types, and duplicating it once meant batch mode could only run on a
    homogeneous, run-wide-pinned batch (D144). `on_classify` is called just before the classify
    call so a caller with a live progress row can announce it; callers print the resolved label
    themselves, since the two paths word it differently."""
    doc_pinned_skill = _sidecar_skill(pf.get("sidecar"), filename=filename) or pinned_skill
    if doc_pinned_skill:
        return (Path(doc_pinned_skill).read_text(encoding="utf-8"), Path(doc_pinned_skill).stem)
    if on_classify is not None:
        on_classify()
    # Classify on the first N pages (page-aware, not a mid-page char cut); the char cap is a guard.
    excerpt = _pages_text(pf.get("pages", [])[:max(1, classify_pages)])[:_CLASSIFY_EXCERPT_CHARS]
    skill = await _classify(excerpt, classify_model, classify_backend, filename=filename,
                            sidecar=pf.get("sidecar"), vault=vault)
    return (skills_catalog.read_skill(skill), skill.removesuffix(".md"))


async def _extract_document(vault: Path, sha: str, brief: str | None,
                            extract_model: str, classify_model: str,
                            classify_pages: int = DEFAULT_CLASSIFY_PAGES,
                            pinned_skill: str | None = None,
                            extract_effort: str | None = None,
                            extract_backend: str | None = None,
                            classify_backend: str | None = None,
                            force: bool = False, verify_pass: bool = False) -> dict:
    pf = preflight.run(vault, sha)
    if pf.get("error"):
        return _fail(vault, sha, "", pf["error"])
    if force and (pf.get("already_extracted") or pf.get("already_staged")):
        # --force (#424): bypass both "already done" checks and pay for a fresh classify/extract
        # call even though a cached artifact (or a committed vault note) already exists — the
        # whole point of --force is to regenerate under a different model/effort/skill.
        _say(f"{_DIM}↻{_RESET}  {pf.get('filename')}  {_YELLOW}re-extracting (--force){_RESET}"
             f"{_DIM} — note will be replaced{_RESET}")
    elif pf.get("already_extracted"):
        _say(f"{_DIM}–  {pf.get('filename')}  already extracted — skipping{_RESET}")
        (vault / ".watchdog" / "queue" / f"{sha}.json").unlink(missing_ok=True)
        return {"sha256": sha, "filename": pf.get("filename"), "status": "skipped"}
    elif pf.get("already_staged"):
        # Extracted in a prior run but not yet committed (#403 phase 1) — no reason to spend a
        # classify/extract call again. The queue file must survive: the eventual commit pass
        # still needs it (write_vault._write_morgue_markdown, the corpus index). Its
        # result_<sha>.json is still on disk from that prior run, so it still feeds finalize.
        _say(f"{_DIM}–  {pf.get('filename')}  already extracted — pending commit{_RESET}")
        return {"sha256": sha, "filename": pf.get("filename"), "status": "skipped"}

    filename = pf["filename"]
    pages = pf.get("pages", [])
    page_count = pf.get("page_count") or len(pages)
    pg = f"{page_count}p"
    # Log the moment extraction begins (#317 follow-up). Documents extract concurrently, so a
    # START line per document makes the staggered starts visible — the later OK/FAILED line
    # marks completion, and the gap between them is that document's own extraction time (the
    # log is otherwise completion-ordered, which reads misleadingly like sequential work).
    _log(vault, f"START {filename}")

    # The prior-entity digest telemetry (#216) that used to print here is gone with the digest
    # itself (#381/D118): extraction no longer carries any vault context, so there is no longer a
    # per-document number to watch. What it measured — a per-page tax that grew with the vault —
    # is now paid once per ingest by the reconciliation call instead.

    def _step(tty: str, plain: str) -> None:
        """Mutate this document's single in-flight live row (TTY); append the plain transition
        line when there's no live region (non-TTY) — keeping logged output unchanged."""
        if _board is not None:
            _board.update(sha, f"  {tty}", f"  {plain}")
        else:
            _say(plain)

    # A sidecar's own `skill:` pin is more specific than a run-wide `--skill`, so it wins —
    # this is what lets one ingest queue mix skills without a second pass (D120). The precedence
    # itself lives in `_resolve_skill`, shared with the batch path (D144).
    classified = not (_sidecar_skill(pf.get("sidecar"), filename=filename) or pinned_skill)
    skill_text, skill_label = await _resolve_skill(
        vault, pf, pinned_skill, classify_model, classify_pages, classify_backend,
        filename=filename,
        on_classify=lambda: _step(
            f"{_DIM}→  {filename}  {pg} · classifying…{_RESET}",
            f"{_DIM}→  {filename}  classifying ({page_count} page{'s' if page_count != 1 else ''})…{_RESET}"))
    if classified:
        _step(f"{_DIM}→  {filename}  {pg} · {skill_label}{_RESET}",
              f"{_DIM}·  {filename}  classified ·{_RESET} {_CYAN}{skill_label}{_RESET}")

    flow = f"{pg} · {skill_label}"        # the accumulated in-flight prefix for this document's row

    plan = section.run(vault, sha, model=extract_model, backend=extract_backend, effort=extract_effort)
    if plan.get("sectioned"):
        n_sections = len(plan.get("sections", []))
        _step(f"{_DIM}→  {filename}  {flow} · extracting · {n_sections} sections…{_RESET}",
              f"{_DIM}→  {filename}  extracting · {n_sections} sections…{_RESET}")
        extraction, scratchpad, cost, ok, errors, warnings = await _extract_sectioned(
            vault, sha, pf, skill_text, plan, extract_model, skill_label, extract_effort,
            extract_backend, brief, verify_pass=verify_pass)
    else:
        _step(f"{_DIM}→  {filename}  {flow} · extracting…{_RESET}",
              f"{_DIM}→  {filename}  extracting…{_RESET}")
        extraction, scratchpad, cost, ok, errors, warnings = await _simple_extract(
            vault, sha, pf, skill_text, brief, extract_model, skill_label, extract_effort,
            extract_backend, verify_pass=verify_pass)
        # Whole-document extraction can overrun the model's output ceiling on entity-dense docs and
        # get authoritatively rejected as truncated (#343) — pagination handles the continuation-
        # capable backends, and openai/gemini are sized to fit up front, but a dense doc can still
        # exceed the estimate. Fall back to the sectioned path, which bounds per-call output, before
        # giving up. Covers single-page docs too (force-sectioning splits their text into character
        # windows); the ≥2-section guard skips a doc that can't be split (nothing to re-try).
        if not ok:
            fb = section.run(vault, sha, force_budget=_FALLBACK_SECTION_TOKENS)
            if fb.get("sectioned") and len(fb.get("sections", [])) > 1:
                n_sections = len(fb.get("sections", []))
                _step(f"{_DIM}↻  {filename}  {flow} · re-extracting in {n_sections} sections…{_RESET}",
                      f"{_DIM}↻  {filename}  whole-doc extraction rejected — "
                      f"re-extracting in {n_sections} sections…{_RESET}")
                whole_cost = cost
                extraction, scratchpad, cost, ok, errors, warnings = await _extract_sectioned(
                    vault, sha, pf, skill_text, fb, extract_model, skill_label, extract_effort,
                    extract_backend, brief, verify_pass=verify_pass)
                cost += whole_cost   # account for the failed whole-doc attempt

    if not ok:
        return _fail(vault, sha, filename, "post-flight rejected: " + "; ".join(errors[:3]))
    return _finish_extraction(vault, sha, filename, extraction, scratchpad, cost, pf, warnings,
                              skill_label=skill_label)



def _finish_extraction(vault: Path, sha: str, filename: str, extraction: dict, scratchpad: str,
                       cost: float, pf: dict, warnings: list[str] | None = None,
                       skill_label: str | None = None) -> dict:
    """Shared tail once an extraction has passed post-flight: settle-print, warnings, log,
    persist `result_<sha>.json`. Used by both the synchronous per-document path
    (`_extract_document`) and the batch-collect path (`_finish_batch_item`, #214) so a
    batch-extracted document produces an identical result shape to a synchronous one.

    `warnings` (post-flight's quote-verify/sanitization/coverage-gap messages, if any) are
    printed here — after the OK line, not when post-flight ran — so they're tucked visually
    under this document's own row instead of landing wherever a concurrently-extracting
    document happened to be at the time (#333 follow-up).

    `skill_label` (#411), when given, is the classified/pinned record skill — shown alongside the
    page count and entity count so the completion line answers "what kind of document was this,
    and how big" without a separate lookup.

    The queue file is deliberately *not* removed here any more (#403 phase 1): the vault has not
    been written yet at this point (post-flight only staged the extraction), and
    `write_vault._write_morgue_markdown` / corpus indexing still need to read it at commit time.
    It is removed by the commit pass instead (`_commit_extracted`), once write_vault has actually
    consumed it. `new_entities`/`updated_entities` are similarly not known yet — they come from
    the writer, which hasn't run — so `_compact_result` gets an empty split here; the commit pass
    patches the persisted result with the real one before `_post_ingest` reads it."""
    if scratchpad:
        (vault / ".watchdog" / "tmp" / f"notes_{sha}.md").write_text(scratchpad, encoding="utf-8")
    for stale in (vault / ".watchdog" / "tmp").glob(f"section_{sha}_*.md"):
        stale.unlink(missing_ok=True)
    # Belt-and-braces (#498): _extract_sectioned already clears its own checkpoints once
    # post-flight succeeds, but a document can also reach here via a different successful path
    # on a later retry (e.g. whole-document extraction succeeding where a prior sectioned attempt
    # had left partial checkpoints behind) — this guarantees none linger regardless of which path
    # this particular success came through.
    _clear_section_checkpoints(vault, sha)
    n_entities = len(extraction.get("entities", []))
    page_count = pf.get("page_count") or len(pf.get("pages", []))
    type_bit = f" · {skill_label}" if skill_label else ""
    _settle(sha, f"  {_GREEN}OK{_RESET}  {filename}  "
            f"{_DIM}{page_count}p · {n_entities} entit{'ies' if n_entities != 1 else 'y'}{type_bit}{_RESET}  "
            f"{_CYAN}documents/{_doc_slug(filename)}{_RESET}")
    _log(vault, f"OK {filename}: {page_count}p, {n_entities} entities{type_bit}")
    for msg in (warnings or []):
        _say(f"   {_YELLOW}⚠{_RESET}  {_DIM}{msg}{_RESET}")
        _log(vault, f"WARN {filename}: {msg}")
    result = _compact_result(sha, filename, extraction, pf.get("near_dup", {}), round(cost, 6), {},
                             est_input_tokens=section.est_tokens_from_pages(pf.get("pages", [])))
    # Persist the compact result so `watchdog bark` can run post-ingest from disk alone.
    (vault / ".watchdog" / "tmp" / f"result_{sha}.json").write_text(
        json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


# ── claude-batch: submit-many/poll/collect bulk extraction (#214) ────────────────────────────
#
# A fundamentally different flow from the concurrent per-document loop above: the Message
# Batches API is submit-many/poll/collect over minutes-to-24h, not one-await-per-document.
# `_run_batch` (called by `run`, not `_extract_document`) resumes a pending batch if one exists,
# otherwise splits the queue into sectioned documents (extracted synchronously via claude-api —
# a section's carry-forward depends on the previous section's result, so it can't be an
# independent batch request) and whole documents (submitted as one batch). Requires a pinned
# skill: classification is inherently one-document-at-a-time and not batchable.

async def _finish_batch_item(vault: Path, sha: str, item: dict | None, skill_text: str,
                             skill_label: str, brief: str | None, api_key: str,
                             model: str | None = None, effort: str | None = None,
                             force: bool = False, batch_meta: dict | None = None,
                             backend: str = "claude-batch") -> dict:
    """Turn one collected batch result into a finished document. `item` is `batch_extract.collect`'s
    per-sha entry (or None if the batch has no result for this sha at all). A batch response that
    didn't pass schema validation gets exactly one synchronous single-call repair attempt, on the
    same provider the batch itself ran on (`claude-api` for `claude-batch`, `openai` for
    `openai-batch`, #530) — not a whole new batch submission for a single document — mirroring
    `_simple_extract`'s own single-repair-attempt semantics. An OpenAI repair passes the batch's
    own model id through explicitly (an OpenAI backend has no tier-name default to fall back to
    the way `claude-api`'s `model=None` resolves to Claude's own default tier).

    `model` (the batch's resolved model id) is used both to attribute the batch-collected item's
    own usage (D64) — unlike every other extraction path, this one never calls `_call_model`
    itself when the batch result is already valid, so without this the batch's real token spend
    would silently never reach `usage-<ts>.json` — and, with `effort`, to stamp this document's
    extraction provenance (#268). `force` (#424) bypasses both "already done" skip checks below,
    same as `_extract_document` — the batch already ran, so bypassing just means the collected
    result is staged/committed instead of discarded. `batch_meta` (from `_resume_batch`) is
    passed straight through to `_record_usage` so this item's usage row carries the batch's own
    submitted/ended/collected lifecycle, not just this call's own token counts."""
    pf = preflight.run(vault, sha)
    if pf.get("error"):
        return _fail(vault, sha, "", pf["error"])
    if force and (pf.get("already_extracted") or pf.get("already_staged")):
        _say(f"{_DIM}↻{_RESET}  {pf.get('filename')}  {_YELLOW}re-extracting (--force){_RESET}"
             f"{_DIM} — note will be replaced{_RESET}")
    elif pf.get("already_extracted"):     # a retried collection pass after a partial rate limit
        filename = pf.get("filename")
        _say(f"{_DIM}–  {filename}  already extracted — skipping{_RESET}")
        (vault / ".watchdog" / "queue" / f"{sha}.json").unlink(missing_ok=True)
        return {"sha256": sha, "filename": filename, "status": "skipped"}
    elif pf.get("already_staged"):        # extracted already (#403 phase 1) — nothing left to do
        filename = pf.get("filename")
        _say(f"{_DIM}–  {filename}  already extracted — pending commit{_RESET}")
        return {"sha256": sha, "filename": filename, "status": "skipped"}

    filename = pf["filename"]
    page_count = pf.get("page_count") or len(pf.get("pages", []))
    if item is None:
        return _fail(vault, sha, filename, "batch result missing for this document")

    extraction, cost = item["parsed"], item.get("cost_usd") or 0.0
    if item.get("usage") is not None:
        # Every batch backend requires api-key auth (D52, #530) — never subscription — so this
        # is the one _record_usage call site where auth_mode is a known constant, not a live
        # result field.
        _record_usage("extract", model=model, backend=backend, usage=item["usage"],
                      cost_usd=item.get("cost_usd"), effort=effort, auth_mode="api-key",
                      filename=filename, detail=f"pages 1–{page_count}", batch_meta=batch_meta)
    if not item["ok"]:
        text = _pages_text(pf["pages"])
        # Off the event loop — see the comment in `_simple_extract`.
        candidates = await asyncio.to_thread(_candidates_checklist, text)
        prompt = prompts.build_extract_prompt(
            pages_text=text,
            skill_text=skill_text, sidecar=pf.get("sidecar"), brief=brief,
            known_document_types=pf.get("known_document_types", []),
            file_metadata=pf.get("file_metadata", {}), processing=pf.get("processing", {}),
            candidates=candidates)
        if item.get("error"):
            prompt = _append_repair_note(prompt, [item["error"]])
        is_anthropic = model_client.provider_for_backend(backend) == "anthropic"
        repair_backend = "claude-api" if is_anthropic else "openai"
        repair_model = None if is_anthropic else model
        try:
            r = await _call_model(task="extract", model=repair_model, backend=repair_backend,
                                  prompt=prompt, schema=schemas.EXTRACTION,
                                  filename=filename, detail=f"pages 1–{page_count} (repair)",
                                  vault=vault)
        except model_client.ModelError as e:
            return _fail(vault, sha, filename, f"batch result invalid and repair failed: {e}")
        extraction = r.parsed
        cost += r.cost_usd or 0.0

    scratchpad = extraction.pop("scratchpad", "") if isinstance(extraction, dict) else ""
    _stamp_document(extraction, sha=sha, pf=pf, skill_label=skill_label,
                    skill_text=skill_text, extract_model=model, extract_effort=effort)
    ok, errors, warnings = _write_postflight(vault, sha, extraction)
    if not ok:
        return _fail(vault, sha, filename, "post-flight rejected: " + "; ".join(errors[:3]))
    return _finish_extraction(vault, sha, filename, extraction, scratchpad, cost, pf, warnings,
                              skill_label=skill_label)



def _batch_skill(state: dict, sha: str, pinned_skill: str | None) -> tuple[str, str]:
    """The skill a batched document was submitted under, as `(skill_text, skill_label)`.

    Read from the state's per-sha `skills` map (D144). Falls back to the pre-D144 state shape's
    single run-wide `skill_label`, then to the run's own `--skill` pin, so a batch submitted by
    an older version is still collectable after an upgrade rather than stranded — a batch can sit
    in flight for up to 24h, which is easily long enough to span one."""
    label = (state.get("skills") or {}).get(sha) or state.get("skill_label")
    resolved = skills_catalog.resolve(label) if label else None
    if resolved:
        return Path(resolved).read_text(encoding="utf-8"), Path(resolved).stem
    # An unresolvable label — a pre-D144 state whose `skill_label` was a file-path stem, or a
    # user-local skill removed while the batch was in flight — falls back to this run's own pin
    # rather than failing a batch that has already been paid for.
    if pinned_skill:
        return Path(pinned_skill).read_text(encoding="utf-8"), Path(pinned_skill).stem
    raise model_client.ModelError(
        f"batch state names no usable skill for {sha[:7]} (label: {label or 'none'}) — "
        f"cannot rebuild its extraction prompt")


_BATCH_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _fmt_span(start_iso: str | None, end_iso: str | None) -> str | None:
    """Human duration between two batch-lifecycle timestamps (`_BATCH_TS_FMT` strings), or None
    if either is missing — `ended_at` is None until a batch finishes, and older persisted state
    (written before this) has no `submitted_at`."""
    if not start_iso or not end_iso:
        return None
    secs = int((datetime.datetime.strptime(end_iso, _BATCH_TS_FMT)
               - datetime.datetime.strptime(start_iso, _BATCH_TS_FMT)).total_seconds())
    m, s = divmod(max(secs, 0), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else (f"{m}m{s:02d}s" if m else f"{s}s")


def _batch_log_line(state: dict, st: dict, collected_at: str) -> str:
    """One-line `ingest.log` summary of a collected batch's full lifecycle: submitted (this
    run's own clock, persisted in batch state at submit time) -> ended (Anthropic's own record
    of when processing finished) -> collected (right now) — the middle and last of those
    routinely differ by hours under D52's submit-and-exit design, since collection happens in
    a later, unrelated invocation that only reacts once it notices `ended`. Also carries
    Anthropic's own request-count breakdown (succeeded/errored/etc.), so a partially-failed
    batch is visible here even though `_finish_batch_item` reports each failure separately too."""
    submitted, ended = state.get("submitted_at"), st.get("ended_at")
    counts = st.get("request_counts") or {}
    count_bits = ", ".join(f"{v} {k}" for k, v in counts.items() if v)
    processed, idle = _fmt_span(submitted, ended), _fmt_span(ended, collected_at)
    bits = [
        f"BATCH {state['batch_id']}",
        f"submitted {submitted or '?'}",
        f"ended {ended or '?'}" + (f" (processed {processed})" if processed else ""),
        f"collected {collected_at}" + (f" (idle {idle})" if idle else ""),
    ]
    if count_bits:
        bits.append(count_bits)
    return " · ".join(bits)


async def _resume_batch(vault: Path, state: dict, pinned_skill: str | None, brief: str | None,
                        api_key: str, force: bool = False) -> dict:
    """Check a pending batch's status; collect and write it if `ended`, otherwise report
    progress and return without touching the vault.

    `state["backend"]` names which provider's batch this is (#530) — defaults to `claude-batch`
    for state persisted before that field existed, so a batch already in flight across an
    upgrade still resumes correctly."""
    backend = state.get("backend", "claude-batch")
    st = await batch_extract.status(state["batch_id"], api_key, backend=backend)
    if st["processing_status"] != "ended":
        counts = st.get("request_counts", {})
        done = sum(v for k, v in counts.items() if k != "processing")
        _say(f"{_YELLOW}A batch extraction is still processing{_RESET}{_DIM} "
             f"({done}/{len(state['shas'])} finished so far) — re-run {_RESET}"
             f"{_CYAN}{_resume_hint}{_RESET}{_DIM} later to check again.{_RESET}")
        return {"results": [], "batch_pending": True}

    _say(f"{_DIM}→  batch {state['batch_id']} finished — collecting {len(state['shas'])} "
         f"document{'s' if len(state['shas']) != 1 else ''}…{_RESET}")
    collected = await batch_extract.collect(state["batch_id"], api_key, state["model"], backend=backend)
    collected_at = datetime.datetime.now(datetime.timezone.utc).strftime(_BATCH_TS_FMT)
    batch_meta = {"batch_id": state["batch_id"], "submitted_at": state.get("submitted_at"),
                 "ended_at": st.get("ended_at"), "collected_at": collected_at}

    results = []
    try:
        for sha in state["shas"]:
            skill_text, skill_label = _batch_skill(state, sha, pinned_skill)
            results.append(await _finish_batch_item(vault, sha, collected.get(sha), skill_text,
                                                     skill_label, brief, api_key,
                                                     model=state["model"], effort=state.get("effort"),
                                                     force=force, batch_meta=batch_meta,
                                                     backend=backend))
    except model_client.RateLimitError as e:
        # A repair-retry call (claude-api) hit a rate limit partway through collection. Leave
        # the batch state in place — already-written documents are safe (preflight's
        # already_extracted check skips them on the next pass) — so a later run finishes.
        _say(f"{_YELLOW}Rate limit reached during batch collection{_RESET}{_DIM} — {e} "
             f"{len(results)}/{len(state['shas'])} written; re-run {_RESET}"
             f"{_CYAN}{_resume_hint}{_RESET}{_DIM} to finish once it resets.{_RESET}")
        return {"results": results, "batch_pending": True}

    _log(vault, _batch_log_line(state, st, collected_at))
    batch_extract.clear_state(vault)
    return {"results": results, "batch_pending": False}


async def _submit_batch(vault: Path, shas: list[str], brief: str | None, extract_model: str,
                        pinned_skill: str | None, extract_effort: str | None, concurrency: int,
                        classify_model: str, classify_pages: int, classify_backend: str | None,
                        api_key: str, force: bool = False, backend: str = "claude-batch") -> dict:
    """Split the queue into sectioned (→ synchronous single-call backend, via the normal
    `_extract_document`) and whole-document (→ one batch submission) shas, run the former, then
    submit the latter and return — submit-and-exit, not blocking-poll (a batch can take up to
    24h; a *later* `watchdog dig` invocation collects it, see `_resume_batch`).

    Each document resolves its **own** skill through `_resolve_skill` (D144), so a batch may mix
    document types: the skill lives inside each request's own prompt blocks, which the Batches
    API treats as independent, so one submission carries them all. The per-sha skill is persisted
    in the batch state because collection happens in a *later* process, which has no other way to
    rebuild the right skill for a repair retry."""
    results: list[dict] = []
    batch_docs: list[dict] = []
    skills: dict[str, str] = {}          # sha -> skill label, persisted with the batch state
    sectioned_shas: list[str] = []
    whole_docs: list[tuple[str, dict]] = []
    for sha in shas:
        pf = preflight.run(vault, sha)
        if pf.get("error"):
            results.append(_fail(vault, sha, "", pf["error"]))
            continue
        if force and (pf.get("already_extracted") or pf.get("already_staged")):
            _say(f"{_DIM}↻{_RESET}  {pf.get('filename')}  {_YELLOW}re-extracting (--force){_RESET}"
                 f"{_DIM} — note will be replaced{_RESET}")
        elif pf.get("already_extracted"):
            _say(f"{_DIM}–  {pf.get('filename')}  already extracted — skipping{_RESET}")
            (vault / ".watchdog" / "queue" / f"{sha}.json").unlink(missing_ok=True)
            results.append({"sha256": sha, "filename": pf.get("filename"), "status": "skipped"})
            continue
        elif pf.get("already_staged"):    # extracted already (#403 phase 1) — don't resubmit
            _say(f"{_DIM}–  {pf.get('filename')}  already extracted — pending commit{_RESET}")
            results.append({"sha256": sha, "filename": pf.get("filename"), "status": "skipped"})
            continue
        if section.run(vault, sha, model=extract_model).get("sectioned"):
            sectioned_shas.append(sha)
        else:
            whole_docs.append((sha, pf))

    # Resolve every whole-document skill up front, concurrently. Batch mode exists for large
    # drops, and an unpinned batch classifies per document (D144) — awaiting those one at a time
    # inside the loop above would serialize hundreds of round-trips before anything is submitted.
    # A pinned or sidecar-pinned document short-circuits without a call, so a homogeneous batch
    # still makes none of these.
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _skill_for(pf: dict):
        async with sem:
            return await _resolve_skill(vault, pf, pinned_skill, classify_model, classify_pages,
                                        classify_backend, filename=pf["filename"])

    resolved = await asyncio.gather(*[_skill_for(pf) for _, pf in whole_docs])

    for (sha, pf), (skill_text, skill_label) in zip(whole_docs, resolved):
        text = _pages_text(pf["pages"])
        # Off the event loop — see the comment in `_simple_extract`.
        candidates = await asyncio.to_thread(_candidates_checklist, text)
        prompt = prompts.build_extract_prompt(
            pages_text=text,
            skill_text=skill_text, sidecar=pf.get("sidecar"), brief=brief,
            known_document_types=pf.get("known_document_types", []), cache_ttl="1h",
            file_metadata=pf.get("file_metadata", {}), processing=pf.get("processing", {}),
            candidates=candidates)
        batch_docs.append({"sha": sha, "prompt": prompt, "skill_label": skill_label})
        skills[sha] = skill_label

    if sectioned_shas:
        # A section's carry-forward depends on the previous section's result, so sectioned
        # extraction can't be an independent batch request on either provider — it always falls
        # back to that same provider's single-call backend (#530).
        sectioned_backend = ("claude-api" if model_client.provider_for_backend(backend) == "anthropic"
                             else "openai")
        _say(f"{_DIM}→  {len(sectioned_shas)} large document"
             f"{'s' if len(sectioned_shas) != 1 else ''} need sectioning — not batchable, "
             f"extracting via {sectioned_backend}{_RESET}")
        sem = asyncio.Semaphore(max(1, concurrency))

        async def _sectioned(sha: str) -> dict:
            async with sem:
                return await _extract_document(vault, sha, brief, extract_model, classify_model,
                                               classify_pages, pinned_skill, extract_effort,
                                               extract_backend=sectioned_backend,
                                               classify_backend=classify_backend, force=force)
        results.extend(await asyncio.gather(*[_sectioned(s) for s in sectioned_shas]))

    if not batch_docs:
        return {"results": results, "batch_pending": False}

    # Adjacent same-skill requests share the cached instructions+skill prefix (the `cache_control`
    # breakpoint sits on the skill block), so sorting by skill keeps a mixed batch's cache hit
    # rate close to a homogeneous one's. Correctness doesn't depend on it — only cost does.
    batch_docs.sort(key=lambda d: d["skill_label"])
    distinct = sorted({d["skill_label"] for d in batch_docs})
    labels = ", ".join(distinct) if len(distinct) <= 3 else f"{len(distinct)} skills"
    _say(f"{_DIM}→  submitting {len(batch_docs)} document"
         f"{'s' if len(batch_docs) != 1 else ''} as one batch ({labels})…{_RESET}")
    batch_id = await batch_extract.submit(vault, batch_docs, model=extract_model,
                                          effort=extract_effort, skills=skills,
                                          api_key=api_key, backend=backend)
    _say(f"{_GREEN}Batch submitted{_RESET}  {_CYAN}{batch_id}{_RESET}{_DIM} — this can take up "
         f"to a few hours (max 24h); re-run {_RESET}{_CYAN}{_resume_hint}{_RESET}{_DIM} later "
         f"to collect it.{_RESET}")
    return {"results": results, "batch_pending": True}


async def _run_batch(vault: Path, shas: list[str], brief: str | None, extract_model: str,
                     pinned_skill: str | None, extract_effort: str | None, concurrency: int,
                     classify_model: str, classify_pages: int,
                     classify_backend: str | None, backend: str = "claude-batch",
                     force: bool = False) -> dict:
    """Entry point for `run` when `extract_backend` is a batch-mode backend
    (`model_client.BATCH_BACKENDS`). Defense-in-depth guard beyond `cmd_ingest`'s own check — a
    programmatic caller that skips CLI validation still gets a clear error rather than a
    confusing downstream failure.

    A pinned skill is **not** required (D144): each document resolves its own skill before the
    batch is built, so a mixed-type drop batches fine. `pinned_skill` is still honoured as the
    run-wide default when a document has no sidecar pin of its own.

    Auth resolution branches by provider (#530): a Claude batch needs the subscription/api-key
    mode explicitly checked (the Batches API isn't available on subscription, D52) — every other
    provider in this codebase has no subscription mode at all, so it just needs its own stored
    key, the same resolution `_resolve_backend_auth` uses for a live call."""
    from watchdog.cmd import auth
    provider = model_client.provider_for_backend(backend)
    if provider == "anthropic":
        api_key = auth.resolve_auth().get("key")
        if not api_key:
            raise model_client.ModelError(
                "claude-batch requires api-key auth mode — switch to it with `watchdog auth`")
    else:
        api_key = auth.get_api_key(provider)
        if not api_key:
            raise model_client.ModelError(
                f"the {backend} backend needs an API key — run `watchdog auth` to add one")

    state = batch_extract.read_state(vault)
    if state is not None:
        return await _resume_batch(vault, state, pinned_skill, brief, api_key, force=force)
    return await _submit_batch(vault, shas, brief, extract_model, pinned_skill, extract_effort,
                               concurrency, classify_model, classify_pages, classify_backend,
                               api_key, force=force, backend=backend)


def _nudge_skill_pin(results: list) -> None:
    """If every successfully-extracted document in a run classified to the same record skill,
    tell the user they could have skipped classification with `--skill` (A4) — a batch of one
    filing type is the common case this tool targets, and the flag already exists but nothing
    surfaces that a run *was* homogeneous."""
    ok_skills = [r.get("record_skill") for r in results if r.get("status") == "ok"]
    distinct = {s for s in ok_skills if s}
    if len(ok_skills) > 1 and len(distinct) == 1:
        skill = next(iter(distinct))
        _say(f"{_DIM}All {len(ok_skills)} documents classified as {_RESET}{_CYAN}{skill}{_RESET}"
             f"{_DIM} — next time run {_RESET}{_CYAN}watchdog dig --skill {skill}{_RESET}"
             f"{_DIM} to skip classification.{_RESET}")


def _lines(items: list) -> str:
    return "\n".join(f"- {x}" for x in items) if items else "_None._"


def _load_entity_names(vault: Path) -> dict:
    """id -> display name from the registry manifest. Used to resolve briefing entity
    references back to display names deterministically (#342) — the model isn't reliable
    across all backends about avoiding the internal kebab-case id in prose. Tolerates a
    missing or unparseable manifest (returns {}, so callers just pass items through)."""
    manifest_file = vault / ".watchdog" / "registry" / "manifest.json"
    if not manifest_file.exists():
        return {}
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {eid: entry.get("name", eid) for eid, entry in manifest.items() if isinstance(entry, dict)}


def _resolve_names(items: list, names: dict) -> list:
    """Replace any item that, stripped of whitespace, exactly matches a known entity id with
    its display name. Everything else — prose sentences, unmatched ids — passes through
    unchanged. Exact-item match only; no substring rewriting inside prose (#342)."""
    return [names[x.strip()] if isinstance(x, str) and x.strip() in names else x for x in items]


def _fts_add_note_safe(vault: Path, note_path: str, kind: str, title: str, text: str) -> None:
    """Best-effort full-text index update (#109) — never fails the ingest run over it."""
    try:
        from watchdog.pipeline.fulltext import add_note
        add_note(vault, note_path, kind, title, text)
    except Exception as e:
        print(f"  Warning: full-text index update failed for {note_path}: {e}", file=sys.stderr)


def _write_briefing(vault: Path, b: dict, results: list, neardup_alerts: list,
                    contradiction_flags: list, n_new_requests: int = 0) -> str:
    # Resolve entity ids the model may have echoed instead of display names (#342) — deterministic
    # backstop on top of the prompt/schema instructions, since not every backend honours those.
    names = _load_entity_names(vault)
    what_was_ingested = _resolve_names(b.get("what_was_ingested", []), names)
    new_entities = _resolve_names(b.get("new_entities", []), names)
    connections = _resolve_names(b.get("connections", []), names)
    leads = _resolve_names(b.get("leads", []), names)
    anomalies = _resolve_names(b.get("anomalies", []), names)
    emerging_patterns = _resolve_names(b.get("emerging_patterns", []), names)
    open_questions = _resolve_names(b.get("open_questions", []), names)

    now = datetime.datetime.now()
    slug = now.strftime("%Y-%m-%d-%H-%M")
    n_new = len(new_entities)

    body = (
        f"---\ndate: {now.isoformat(timespec='seconds')}\nfiles_ingested: {len(results)}\n"
        f"new_entities: {n_new}\n---\n\n# Ingest briefing — {slug}\n\n"
        f"## What was ingested\n\n{_lines(what_was_ingested)}\n\n"
        f"## New entities\n\n{_lines(new_entities)}\n\n"
        f"## Connections to existing entities\n\n{_lines(connections)}\n\n"
        f"## Leads and follow-up ideas\n\n{_lines(leads)}\n\n"
        f"## Anomalies worth a closer look\n\n"
        f"{_lines(anomalies) if anomalies else 'Nothing flagged.'}\n"
    )
    if neardup_alerts:
        body += "\n## Near-duplicate alerts\n\n" + "\n".join(
            f"- {a['filename']}: {a['similarity']:.0%} similar to an existing document"
            for a in neardup_alerts) + "\n"
    if n_new_requests:
        body += (f"\n## Document requests\n\n"
                 f"- {n_new_requests} new document request"
                 f"{'s' if n_new_requests != 1 else ''} — see [[requests|requests.md]]\n")
    (vault / "briefings").mkdir(exist_ok=True)
    (vault / "briefings" / f"{slug}.md").write_text(body, encoding="utf-8")
    _fts_add_note_safe(vault, f"briefings/{slug}", "briefing", f"Briefing {slug}", body)

    hot_content = (
        f"# Hot cache\n\n*Last updated: {now.strftime('%Y-%m-%d')} — "
        f"[[briefings/{slug}|Briefing {slug}]]*\n\n"
        f"## Investigation status\n\n{b.get('investigation_status', '')}\n\n"
        f"## Recent additions\n\n{_lines(new_entities)}\n\n"
        f"## Emerging patterns\n\n{_lines(emerging_patterns)}\n\n"
        f"## Open questions\n\n{_lines(open_questions)}\n"
    )
    (vault / "hot.md").write_text(hot_content, encoding="utf-8")
    _fts_add_note_safe(vault, "hot", "hot", "Hot cache", hot_content)

    entry = (f"\n## {now.strftime('%Y-%m-%d %H:%M')} — Ingest\n\n"
             f"- **Files:** {len(results)} processed\n- **New entities:** {n_new}\n"
             f"- **Briefing:** [[briefings/{slug}|{slug}]]\n")
    if contradiction_flags:
        entry += f"- **Contradictions flagged:** {len(contradiction_flags)}\n"
    if _usage:   # (F5, #222) — _post_ingest's own calls (synthesis/timeline-dedup/briefing)
        totals = _usage_totals(_usage)   # are already recorded by the time this runs
        cost = f" · ~${totals['cost_usd']:.4f}" if totals.get("cost_usd") else ""
        entry += (f"- **Usage:** {totals['input_tokens']:,} in / "
                 f"{totals['output_tokens']:,} out tokens{cost}\n")
    log_path = vault / "log.md"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)
    _fts_add_note_safe(vault, "log", "log", "Run log", log_path.read_text(encoding="utf-8"))
    return f"briefings/{slug}.md"


def _valid_index(i, n: int) -> bool:
    return isinstance(i, int) and not isinstance(i, bool) and 0 <= i < n


def _select_kept(events: list[dict], groups) -> list[dict]:
    """Map the timeline-dedup model's `groups` back to the original event objects (deduped, in
    original order). Each surviving event carries the union of its own and its dropped duplicates'
    `entity_ids` (#237) — so entity attribution survives a cross-document collapse regardless of
    which restatement the model kept. An index the model never placed in any group is preserved
    as-is; falls back to all events when the output is unusable — dedup must never lose events. The
    originals carry the authoritative page/basis/source_sha256."""
    if not isinstance(groups, list):
        return events
    n = len(events)
    survivors: dict[int, list[int]] = {}   # keep index -> its duplicate indices
    for g in groups:
        if not isinstance(g, dict):
            continue
        ki = g.get("keep")
        if not _valid_index(ki, n) or ki in survivors:
            continue
        dups = g.get("duplicates")
        survivors[ki] = [di for di in dups if _valid_index(di, n) and di != ki] if isinstance(dups, list) else []
    dropped = {di for dups in survivors.values() for di in dups}

    kept: list[dict] = []
    for i, ev in enumerate(events):
        if i in survivors:
            merged = dict(ev)
            entity_ids = list(merged.get("entity_ids", []))
            for di in survivors[i]:
                for eid in events[di].get("entity_ids", []):
                    if eid not in entity_ids:
                        entity_ids.append(eid)
            if "entity_ids" in merged or entity_ids:
                merged["entity_ids"] = entity_ids
            kept.append(merged)
        elif i not in dropped:
            kept.append(ev)   # model never placed it — keep rather than lose it
    return kept or events


def _apply_request_dedup(vault: Path, open_: list[dict], groups) -> int:
    """Map the request-dedup model's `groups` (indices into `open_`, keep/duplicates like
    TIMELINE_DEDUP) onto rids and fold each group's duplicates into its `keep` via
    `requests.merge_duplicates` — same index-validation discipline as `_select_kept`: an index
    the model gets wrong (out of range, self-referential, already folded away by an earlier
    group in this same pass) is dropped rather than guessed at. Returns the total folded."""
    if not isinstance(groups, list):
        return 0
    n = len(open_)
    folded = 0
    for g in groups:
        if not isinstance(g, dict):
            continue
        ki = g.get("keep")
        if not _valid_index(ki, n):
            continue
        dups = g.get("duplicates")
        if not isinstance(dups, list):
            continue
        dup_rids = [open_[di]["rid"] for di in dups if _valid_index(di, n) and di != ki]
        if dup_rids:
            folded += requests.merge_duplicates(vault, open_[ki]["rid"], dup_rids)
    return folded


async def _post_ingest(vault: Path, results: list, brief: str | None, post_model: str,
                       post_effort: str | None = None, post_backend: str | None = None,
                       rec_result: dict | None = None, skip_briefing: bool = False,
                       finalizer_overrides: dict | None = None) -> dict:
    """`finalizer_overrides` (#433) may carry `synthesis_model`/`synthesis_backend`,
    `timeline_model`/`timeline_backend`, and `briefing_model`/`briefing_backend`, routing each
    of those three stages to a different model than the rest of post-ingest; each falls back to
    `post_model`/`post_backend` when absent from the dict (see `_reconcile_pre_commit`, which
    resolves reconciliation's own pair the same way)."""
    fo = finalizer_overrides or {}
    synthesis_model = fo.get("synthesis_model", post_model)
    synthesis_backend = fo.get("synthesis_backend", post_backend)
    timeline_model = fo.get("timeline_model", post_model)
    timeline_backend = fo.get("timeline_backend", post_backend)
    briefing_model = fo.get("briefing_model", post_model)
    briefing_backend = fo.get("briefing_backend", post_backend)
    request_dedup_model = fo.get("request_dedup_model", post_model)
    request_dedup_backend = fo.get("request_dedup_backend", post_backend)
    out: dict = {"synthesized": 0, "timeline_collisions": 0, "briefing": None,
                 "merged": [], "contradictions": []}
    print()
    _say(f"{_BOLD}Post-processing{_RESET}")

    # 0. Contradictions — the post-commit half of reconciliation (#381/D118). The merge half
    # (entity-duplicate resolution) now runs BEFORE the commit pass, over the staged batch
    # (`_reconcile_pre_commit`, called from `finalize` — #403 phase 3): a merge changes what a
    # contradiction would even be about, and folding it pre-commit means a same-batch duplicate
    # becomes a cheap staged id rewrite instead of post-commit note surgery. Contradictions still
    # wait until here, because `contradiction.run` validates both document slugs against
    # `registry/documents.json`, which only has this batch's documents once they are committed.
    # `rec_result` carries forward `_reconcile_pre_commit`'s merge remap — a contradiction may name
    # an entity a merge folded away moments before commit — and its raw (unapplied) merges/
    # contradictions for the briefing/log below.
    rec_result = rec_result or {}
    out["merged"] = rec_result.get("merged", [])
    contradiction_items = rec_result.get("contradictions") or []
    if contradiction_items:
        applied_contradictions = reconcile.apply_contradictions(
            vault, contradiction_items, rec_result.get("remap") or {},
            warn=lambda m: (_say(f"   {_YELLOW}⚠{_RESET}  {_DIM}{m}{_RESET}"),
                            _log(vault, f"WARN {m}")))
        out["contradictions"] = applied_contradictions
        for c in applied_contradictions:
            _say(f"   {_YELLOW}⚠{_RESET}  {_BOLD}{c['label']}{_RESET} {_DIM}—{_RESET} "
                 f"{c['entity_name']}  {_CYAN}{c['note_path']}{_RESET}")
            _log(vault, f"CONTRADICTION {c['entity_id']}: {c['label']}")

    # 1. Entity synthesis for multi-mention entities (Python builds + applies; model reconciles).
    batch_shas = [r["sha256"] for r in results if r.get("status") == "ok"]
    bundle = synthesis_bundle.build_bundle(vault, batch_shas)
    if bundle.get("entities"):
        _say(f"{_DIM}→  synthesizing {len(bundle['entities'])} multi-mention "
             f"entit{'ies' if len(bundle['entities']) != 1 else 'y'}…{_RESET}")
        try:
            r = await _call_model(
                task="entity-synthesis", model=synthesis_model, backend=synthesis_backend,
                schema=schemas.SYNTHESIS,
                prompt=prompts.build_synthesis_prompt(bundle), effort=post_effort, vault=vault)
        except (model_client.ModelError, model_client.RateLimitError) as e:
            # Synthesis is enrichment: leave the structured claims already in the notes
            # rather than crashing. The staged artifacts persist, so a later finalize redoes it.
            out["error"] = str(e)
            _say(f"{_YELLOW}synthesis skipped{_RESET}{_DIM} — {e}{_RESET}")
        else:
            res_path = vault / ".watchdog" / "tmp" / "synthesis-result.json"
            res_path.write_text(json.dumps(r.parsed, ensure_ascii=False), encoding="utf-8")
            out["synthesized"] = len(synthesis_bundle.apply_bundle(res_path, vault).get("applied", []))

    # 2. Timeline: promote pending, model-dedup any real collisions, rebuild timeline.md.
    _say(f"{_DIM}→  rebuilding timeline…{_RESET}")
    cols = timeline.collisions(vault)
    out["timeline_collisions"] = len(cols)
    for col in cols:
        canonical = vault / col["canonical"]
        raw_paths = [vault / r for r in col["raw"]]
        events: list[dict] = []
        for p in [canonical, *raw_paths]:
            for line in timeline._read_ndjson_lines(p):
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        if not events:
            continue
        try:
            r = await _call_model(
                task="timeline-dedup", model=timeline_model, backend=timeline_backend,
                schema=schemas.TIMELINE_DEDUP,
                prompt=prompts.build_timeline_dedup_prompt(col["date"], events), effort=post_effort,
                detail=col["date"], vault=vault)
            kept = _select_kept(events, r.parsed.get("groups"))
        except (model_client.ModelError, model_client.RateLimitError):
            # Dedup failed (e.g. rate limit): leave the canonical AND its raws untouched so the
            # next ingest retries this collision cleanly. Writing the canonical+raw union back
            # here would bake in duplicate rows that compound on every later run (#250).
            continue
        canonical.write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in kept) + "\n", encoding="utf-8")
        # The raws are now merged into the canonical — consume them so they aren't re-collided.
        for rp in raw_paths:
            rp.unlink(missing_ok=True)

    # 2b. Cross-precision reconciliation (#239, D63): date-keyed buckets never compare a
    # month-precision event against the specific day it restates. For each month holding both, one
    # focused model call folds coarse restatements into their day, unioning entity attribution.
    # Gated on a month carrying both precisions, so most ingests make zero extra calls.
    for grp in timeline.month_precision_groups(vault):
        try:
            r = await _call_model(
                task="timeline-precision", model=timeline_model, backend=timeline_backend,
                schema=schemas.TIMELINE_PRECISION_MATCH, effort=post_effort,
                prompt=prompts.build_timeline_precision_prompt(grp["month"], grp["coarse"], grp["precise"]),
                detail=grp["month"], vault=vault)
        except (model_client.ModelError, model_client.RateLimitError):
            continue   # leave the month untouched rather than risk a bad fold
        timeline.apply_precision_matches(vault, grp, r.parsed.get("matches") or [])
    n_dates, n_events = timeline.cmd_rebuild_timeline(vault, quiet=True)
    _say(f"{_DIM}   timeline.md · {n_dates} date{'s' if n_dates != 1 else ''}, "
         f"{n_events} event{'s' if n_events != 1 else ''}{_RESET}")

    # 3. Briefing + hot.md + log.md (model writes prose; Python writes the files).
    ok = [r for r in results if r.get("status") == "ok"]
    ok_shas = {r["sha256"] for r in ok}   # this run's committed documents (also used by 5b below)
    if skip_briefing:
        # #410: an intentional skip, not a failure — leave `briefing`/`briefing_error` alone so
        # this doesn't trip the "post-processing didn't finish" path (that's for a briefing that
        # was attempted and failed). hot.md and log.md's per-run entry are both written by
        # `_write_briefing`, so neither updates this run either — only the briefing model call
        # itself is skipped; synthesis and the timeline above already ran.
        out["briefing_skipped"] = True
        _say(f"{_DIM}→  briefing skipped{_RESET}{_DIM} (--skip-briefing){_RESET}")
    else:
        _say(f"{_DIM}→  writing briefing…{_RESET}")
        scratchpads = [p.read_text(encoding="utf-8")
                       for p in sorted((vault / ".watchdog" / "tmp").glob("notes_*.md"))]
        neardup_alerts = [{"filename": r["filename"], "similarity": r["near_dup_similarity"]}
                          for r in ok if r.get("near_dup_similarity", 0) >= 0.85]
        # Fed by the reconciliation pass (#381/D118), which is the only stage that can see a
        # conflict at all. This used to be scraped off the per-document extraction results, so
        # the count could only ever include conflicts the extractor happened to be positioned to
        # notice.
        contradiction_flags = [{"entity": c["entity_name"], "label": c["label"]}
                               for c in out["contradictions"]]
        # Deterministic pointer, not a model input (D111): count this run's open document
        # requests (recorded per-document into the ledger by write_vault, at extraction time) so
        # the briefing can point at requests.md without the requests themselves ever entering a
        # prompt.
        n_new_requests = len([
            r for r in requests.open_requests(vault)
            if any(s.get("sha256") in ok_shas for s in r.get("sources") or [])
        ])
        try:
            r = await _call_model(
                task="briefing", model=briefing_model, backend=briefing_backend, schema=schemas.BRIEFING,
                prompt=prompts.build_briefing_prompt(
                    brief=brief, results=ok, scratchpads=scratchpads,
                    neardup_alerts=neardup_alerts, contradiction_flags=contradiction_flags),
                effort=post_effort, vault=vault)
            out["briefing"] = _write_briefing(vault, r.parsed, ok, neardup_alerts, contradiction_flags,
                                              n_new_requests)
        except model_client.RateLimitError as e:
            out["briefing_error"] = str(e)
            _say(f"{_YELLOW}briefing skipped{_RESET}{_DIM} — {e}{_RESET}")
        except model_client.ModelError as e:
            # Extraction has already run through this same backend, so a briefing ModelError is
            # almost always an output-cap truncation: the briefing's arrays (what_was_ingested/
            # connections/leads/…) scale with batch size, so a big/dense batch can overrun even
            # the 16k-token ceiling and truncate the JSON. That's deterministic — a plain re-run
            # feeds the identical input into the identical ceiling and fails the same way (#296)
            # — so we fail loudly with the real remedy (a smaller batch) rather than retrying or
            # silently shipping a degraded briefing. Everything else (per-doc facts, entity
            # notes, timeline) is already on disk; only the synthesized briefing is lost, and the
            # pending batch can be discarded on the next ingest to unstick. Streaming (an
            # unbounded ceiling) is future work.
            out["briefing_error"] = str(e)
            _say(f"{_YELLOW}briefing not written{_RESET}{_DIM} — the model's output limit was exceeded "
                 f"(this batch is too large to summarize in one pass). Re-ingest it in smaller "
                 f"batches; everything else was written.{_RESET}")

    # 4. Watch-word scan (deterministic, no model; #165). Scans this run's documents against
    # the vault-root watchlist.md and writes briefings/alerts-<date>.md. No-op if the list is empty.
    hits = watchlist.scan(vault, results)
    alerts = watchlist.write_alerts(vault, hits)
    if alerts:
        relpath, n_terms, n_docs = alerts
        out["watchlist_alerts"] = relpath
        _say(f"{_YELLOW}⚠{_RESET}  {_BOLD}{len(hits)}{_RESET} watch-word match"
             f"{'es' if len(hits) != 1 else ''} {_DIM}({n_terms} term"
             f"{'s' if n_terms != 1 else ''} in {n_docs} document"
             f"{'s' if n_docs != 1 else ''}){_RESET} — {_CYAN}{relpath}{_RESET}")

    # 5. Lead sweep (deterministic, no model; #155). Whole-vault snapshot of entities named
    # but never profiled, recurring-but-unconnected entities, and unresolved contradictions.
    leads_data = leads.scan(vault)
    leads_relpath = leads.write_leads(vault, leads_data)
    if leads_relpath:
        n = leads.total(leads_data)
        out["leads"] = leads_relpath
        _say(f"{_YELLOW}⚠{_RESET}  {_BOLD}{n}{_RESET} lead{'s' if n != 1 else ''} "
             f"{_DIM}(named-but-unprofiled, isolated, contradictions){_RESET} — "
             f"{_CYAN}{leads_relpath}{_RESET}")

    # 5b. Document-request dedup (#416, D159). Exact-string matching at record time only
    # converges identical wording — a citation of the same real document phrased differently
    # stays a separate open entry. Skipped unless this run actually recorded a new request, so
    # most ingests make zero extra calls (same discipline as 2b's timeline-precision gate); when
    # it does run, the model sees every currently-open request (not just this run's), so an
    # older near-duplicate gets a chance to fold too.
    open_ = requests.open_requests(vault)
    added_new_requests = any(
        s.get("sha256") in ok_shas for r in open_ for s in r.get("sources") or [])
    if added_new_requests and len(open_) > 1:
        try:
            r = await _call_model(
                task="request-dedup", model=request_dedup_model, backend=request_dedup_backend,
                schema=schemas.REQUEST_DEDUP,
                prompt=prompts.build_request_dedup_prompt(open_), effort=post_effort, vault=vault)
            n_folded = _apply_request_dedup(vault, open_, r.parsed.get("groups"))
        except (model_client.ModelError, model_client.RateLimitError):
            n_folded = 0   # leave requests unmerged; a later run with new activity retries
        if n_folded:
            out["requests_folded"] = n_folded
            _say(f"   {_DIM}consolidated {n_folded} duplicate document request"
                 f"{'s' if n_folded != 1 else ''}{_RESET}")

    # 6. Document requests (deterministic Python from here — #365). Re-render requests.md from
    # the ledger write_vault populated per-document at extraction time and 5b's dedup pass just
    # folded; requests are never re-fed into any *other* model prompt.
    requests_relpath = requests.write_requests(vault)
    if requests_relpath:
        n = len(requests.open_requests(vault))
        out["requests"] = requests_relpath
        _say(f"{_YELLOW}⚠{_RESET}  {_BOLD}{n}{_RESET} open document request"
             f"{'s' if n != 1 else ''} {_DIM}(documents to go and get){_RESET} — "
             f"{_CYAN}{requests_relpath}{_RESET}")
    return out


def _load_results(vault: Path) -> list:
    """Load persisted per-doc results (used by a standalone finalize run)."""
    out = []
    for p in sorted((vault / ".watchdog" / "tmp").glob("result_*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    return out


# ── Commit pass (#403 phase 1) ───────────────────────────────────────────────────────────────
#
# Extraction stages its output (postflight.run) instead of writing to the vault; this pass
# runs a deterministic exact-name entity fold over every staged-but-uncommitted artifact
# (`_batch_exact_fold`, #403 phase 2), then replays the writer (write_vault.run) over each one in
# the same sorted order, before any post-ingest model call. It runs at the top of `finalize`, so
# it covers all three entry paths that call it: the tail of an ingest run (`watchdog ingest`,
# or `watchdog dig` + `watchdog bark`), a standalone `watchdog bark`, and a resumed run after a
# rate-limit stop.

def _batch_exact_fold(vault: Path, shas: list[str]) -> None:
    """Fold exact-name entity duplicates across a batch of staged extractions, before any of
    them commits to the vault (#403 phase 2).

    Documents extract in parallel from a pre-flight snapshot taken at launch, so two documents
    referencing the same real-world entity can coin different ids (e.g. 'ernst-and-young-inc'
    vs 'ernst-young-inc'). This used to be reconciled by `write_vault._reconcile_entity_ids`
    running per-document inside the registry lock, against a fresh read of the live registry —
    the one place that saw entities written by concurrent extraction tasks earlier in the batch.
    Now that commit is a separate, serial pass (phase 1), the same cross-document visibility is
    available up front: walk the given `shas` in order (the caller passes them pre-sorted, see
    D126) over a throwaway in-memory registry copy, reconciling each document's entities against
    it with the same `_reconcile_entity_ids` and then folding that document's (already-
    reconciled) entities into the copy with the same `_new_entity`/`_merge_entity` used at real
    commit time — so later documents in the batch match against earlier ones exactly as they did
    under the old per-document call. The real registry on disk is never touched here; the commit
    pass still does the actual writes. Mutates each staged `.watchdog/extracted/<sha>.json` in
    place (id remaps, alias appends, role-target remaps) so the commit pass that follows replays
    already-folded entities.

    `_add_reverse_role` is deliberately not simulated — it only appends roles onto entities
    already in the registry copy and never mints a new id, so it cannot affect name/type/alias
    matching for any later document.

    Also rewrites `morgue_entity_id` and every `document.key_facts[].entities` tag through the
    same remap (#513) — both name an entity id by the same convention as `entities[].id` but sit
    outside `_reconcile_entity_ids`'s own view, so without this they'd go stale whenever the
    entity a document is filed under (or a fact is tagged against) gets folded into a different
    canonical id later in the same batch.
    """
    from watchdog.pipeline.write_vault import _merge_entity, _new_entity, _reconcile_entity_ids

    # Freshly parsed from disk, so this is already an in-memory copy independent of the real
    # registry file — mutating it below (reconcile/merge) can never write through to disk.
    entities_path = vault / ".watchdog" / "registry" / "entities.json"
    pseudo_reg: dict = (
        json.loads(entities_path.read_text(encoding="utf-8")) if entities_path.exists() else {}
    )

    extracted_dir = vault / ".watchdog" / "extracted"
    for sha in shas:
        artifact_path = extracted_dir / f"{sha}.json"
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        entities = artifact.get("entities") or []

        remap = _reconcile_entity_ids(entities, pseudo_reg)
        if remap:
            if artifact.get("morgue_entity_id") in remap:
                artifact["morgue_entity_id"] = remap[artifact["morgue_entity_id"]]
            for fact in artifact.get("document", {}).get("key_facts", []):
                tags = fact.get("entities")
                if tags:
                    fact["entities"] = [remap.get(t, t) for t in tags]

        for entity in entities:
            eid = entity["id"]
            if eid in pseudo_reg:
                _merge_entity(pseudo_reg[eid], entity, sha)
            else:
                pseudo_reg[eid] = _new_entity(entity, sha)

        artifact_path.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _pending_commits(vault: Path, force_shas: list[str] | None = None) -> list[str]:
    """Every sha with a durable extraction artifact (`.watchdog/extracted/<sha>.json`) that is
    not yet a key in `registry/documents.json` — sorted, so the commit pass that consumes this
    runs in a fixed order regardless of which document happened to extract first.

    `force_shas` (#424) are included even when already a key in `registry/documents.json` — a
    `--force` re-extraction overwrote their staged artifact and needs `_commit_extracted` to
    replay over it again, which the plain "not yet committed" filter would otherwise exclude."""
    extracted_dir = vault / ".watchdog" / "extracted"
    if not extracted_dir.exists():
        return []
    documents_path = vault / ".watchdog" / "registry" / "documents.json"
    committed: set = set()
    if documents_path.exists():
        try:
            committed = set(json.loads(documents_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    committed -= set(force_shas or [])
    return sorted(p.stem for p in extracted_dir.glob("*.json") if p.stem not in committed)


def _commit_extracted(vault: Path, sha: str) -> dict | None:
    """Replay `write_vault.run` over one staged extraction artifact — the commit half of the
    #403 phase 1 split. Reads near-dup data from the queue file (still present — its deletion is
    deferred to here, since `write_vault._write_morgue_markdown` and the corpus indexer both
    still need to read it) and removes the queue file once the write succeeds. Returns
    write_vault's `{"new_entities", "updated_entities"}` split, or None if the artifact is
    missing (defensive; `_pending_commits` just listed it, so this should not happen in practice)
    or if the commit failed.

    A failure here is caught, not left to propagate — the same posture postflight.run used to
    take around this same call (it validates before staging, so a well-formed artifact should
    never trip write_vault, but a batch of several documents must not go uncommitted because one
    staged artifact turned out to be corrupt or malformed on disk). The artifact and queue file
    are left in place on failure, so the next finalize retries this sha rather than losing it."""
    extracted_path = vault / ".watchdog" / "extracted" / f"{sha}.json"
    if not extracted_path.exists():
        return None
    queue_file = vault / ".watchdog" / "queue" / f"{sha}.json"
    neardup_data: dict = {}
    if queue_file.exists():
        try:
            neardup_data = json.loads(queue_file.read_text(encoding="utf-8")).get("near_dup", {})
        except (OSError, json.JSONDecodeError):
            pass
    from watchdog.pipeline.write_vault import run as wv_run
    try:
        written = wv_run(extraction_path=extracted_path, vault_path=vault,
                         neardup_data=neardup_data, quiet=True)
    except SystemExit as e:
        _say(f"{_YELLOW}⚠{_RESET}  commit failed for {sha[:12]}…{_RESET}{_DIM} — {e}{_RESET}")
        _log(vault, f"WARN commit failed for {sha}: {e}")
        return None
    except Exception as e:
        _say(f"{_YELLOW}⚠{_RESET}  commit failed for {sha[:12]}…{_RESET}{_DIM} — {e}{_RESET}")
        _log(vault, f"WARN commit failed for {sha}: {e}")
        return None
    queue_file.unlink(missing_ok=True)
    return written


async def _reconcile_pre_commit(vault: Path, shas: list[str], post_model: str,
                                post_effort: str | None, post_backend: str | None,
                                finalizer_overrides: dict | None = None) -> dict:
    """Pre-commit reconciliation (#381/D118, #403 phase 3): entity-duplicate resolution over the
    staged batch unioned with the registry, before any of it is written to the vault. Runs before
    the commit pass (see `finalize`) because a confirmed merge between two of this batch's own
    documents is cheapest resolved as a staged id rewrite — write_vault then commits the two as
    one entity naturally, and no post-commit note surgery (redirect stub, backup) is ever needed.

    `finalizer_overrides` (#433) may carry `reconciliation_model`/`reconciliation_backend`,
    routing just this stage to a different model than the rest of post-ingest; each falls back
    to `post_model`/`post_backend` when absent from the dict (`.get`'s default), so an explicit
    `None` backend already resolved by the caller — "route by auth mode" — survives untouched.

    Returns ``{"merged": [...], "remap": {...}, "contradictions": [...], "error": str | None}``.
    `contradictions` are the model's raw (unapplied) items — `apply_contradictions` needs the
    committed vault to validate document slugs against, so it runs after the commit pass
    (`_post_ingest` step 0). On a `ModelError`/`RateLimitError`, `error` is set and nothing is
    applied — the caller (`finalize`) must not commit in that case: the staged JSON is the durable
    input now (not the fragment queue), so leaving it uncommitted is what lets a later
    `watchdog bark` retry the whole fold → reconcile → commit sequence cleanly.
    """
    fo = finalizer_overrides or {}
    reconciliation_model = fo.get("reconciliation_model", post_model)
    reconciliation_backend = fo.get("reconciliation_backend", post_backend)
    result: dict = {"merged": [], "remap": {}, "contradictions": [], "error": None}
    rec_bundle = reconcile.build_bundle(vault, shas)
    if not (rec_bundle["entities"] or rec_bundle["pairs"]):
        return result
    n_pairs, n_ents = len(rec_bundle["pairs"]), len(rec_bundle["entities"])
    # Sized and reported the same way the old #216 digest telemetry was — visibility now, so
    # a future cap/chunking decision (§8.5) comes from real bundle sizes, not a guess.
    rec_prompt = prompts.build_reconcile_prompt(rec_bundle)
    kb = len(rec_prompt) / 1024
    _say(f"{_DIM}→  reconciling · {n_ents} recurring entit{'ies' if n_ents != 1 else 'y'}, "
         f"{n_pairs} possible duplicate{'s' if n_pairs != 1 else ''} · {kb:.1f} KB…{_RESET}")
    try:
        r = await _call_model(
            task="reconcile", model=reconciliation_model, backend=reconciliation_backend,
            schema=schemas.RECONCILE, prompt=rec_prompt, effort=post_effort,
            detail=f"{n_ents} entities · {n_pairs} pairs · {kb:.1f} KB", vault=vault)
    except (model_client.ModelError, model_client.RateLimitError) as e:
        result["error"] = str(e)
        _say(f"{_YELLOW}reconciliation skipped{_RESET}{_DIM} — {e}{_RESET}")
        _log(vault, f"RECONCILE skipped: {e}")
        return result

    applied = reconcile.apply_merges(
        vault, shas, r.parsed, rec_bundle,
        warn=lambda m: (_say(f"   {_YELLOW}⚠{_RESET}  {_DIM}{m}{_RESET}"), _log(vault, f"WARN {m}")))
    result["merged"] = applied["merged"]
    result["remap"] = applied["remap"]
    result["contradictions"] = applied["contradictions"]
    for m in applied["merged"]:
        _say(f"   {_DIM}merged{_RESET} {m['merge_name']} {_DIM}→{_RESET} "
             f"{_BOLD}{m['keep_name']}{_RESET}  {_DIM}{m['reason']}{_RESET}")
        _log(vault, f"MERGED {m['merge_id']} into {m['keep_id']}: {m['reason']}")
    return result


def _commit_pending(vault: Path, shas: list[str] | None = None) -> dict:
    """Commit every staged-but-uncommitted extraction to the vault, in sorted sha order, before
    any post-ingest model call runs. Patches each committed document's persisted
    `result_<sha>.json` with the writer's new/updated entity split, if that file still exists —
    at extraction time `_finish_extraction` could not know it (write_vault hadn't run yet), so
    the briefing prompt would otherwise never see it (#150's `key_facts` still ride along either
    way).

    `shas`, when given (by `finalize`, which has already computed and exact-folded them ahead of
    its own pre-commit reconciliation pass), is used as-is — no redundant re-fold. Standalone
    callers (tests; a bare commit pass with no reconciliation) can omit it and get the old
    self-contained behaviour: compute the pending set and fold it here.

    Returns ``{"committed": n, "written": {sha: {"new_entities", "updated_entities"}, ...}}`` —
    the per-sha split is also returned (not just patched to disk) so `run()` can sync it onto
    its own in-memory `results` list, which is built before this pass ever runs and would
    otherwise keep reporting an empty new/updated split for the life of that call."""
    if shas is None:
        shas = _pending_commits(vault)
        if not shas:
            return {"committed": 0, "written": {}}
        _batch_exact_fold(vault, shas)
    elif not shas:
        return {"committed": 0, "written": {}}
    _say(f"{_DIM}→  committing {len(shas)} document{'s' if len(shas) != 1 else ''} "
         f"to the vault…{_RESET}")
    tmp_dir = vault / ".watchdog" / "tmp"
    written_map: dict[str, dict] = {}
    for sha in shas:
        written = _commit_extracted(vault, sha)
        if not written:
            continue
        written_map[sha] = written
        result_path = tmp_dir / f"result_{sha}.json"
        if not result_path.exists():
            continue
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        result["new_entities"] = written.get("new_entities", [])
        result["updated_entities"] = written.get("updated_entities", [])
        result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return {"committed": len(shas), "written": written_map}


def _clear_post_ingest_inputs(vault: Path) -> None:
    """Remove the per-run post-ingest inputs once they have been finalized cleanly."""
    tmp = vault / ".watchdog" / "tmp"
    for p in list(tmp.glob("result_*.json")) + list(tmp.glob("notes_*.md")):
        p.unlink(missing_ok=True)


def has_pending_finalization(vault: Path) -> bool:
    """True if an extracted-but-not-finalized batch is sitting in tmp (e.g. a rate-limited run)."""
    return any((vault / ".watchdog" / "tmp").glob("result_*.json"))


def pending_finalization(vault: Path) -> dict:
    """Best-effort counts for an extracted-but-not-finalized batch sitting in tmp.

    Entity count uses the same gate as `synthesis_bundle.build_bundle` — registry
    `appears_in >= 2` (D26) — over the entities mentioned in this batch's staged
    extractions (``.watchdog/extracted/<sha>.json``, one per ``result_<sha>.json``)."""
    tmp = vault / ".watchdog" / "tmp"
    results = list(tmp.glob("result_*.json"))
    entities = 0
    try:
        reg_path = vault / ".watchdog" / "registry" / "entities.json"
        registry = json.loads(reg_path.read_text(encoding="utf-8")) if reg_path.exists() else {}
        extracted_dir = vault / ".watchdog" / "extracted"
        touched: set = set()
        for p in results:
            sha = p.stem[len("result_"):]
            art = extracted_dir / f"{sha}.json"
            if not art.exists():
                continue
            artifact = json.loads(art.read_text(encoding="utf-8"))
            for e in artifact.get("entities") or []:
                if e.get("id"):
                    touched.add(e["id"])
        entities = sum(1 for eid in touched
                       if len(registry.get(eid, {}).get("appears_in", [])) >= 2)
    except (OSError, json.JSONDecodeError):
        pass
    return {"docs": len(results), "entities": entities}


async def finalize(vault: Path, *, post_model: str = "haiku", brief: str | None = None,
                   results: list | None = None, post_effort: str | None = None,
                   post_backend: str | None = None, force_shas: list[str] | None = None,
                   skip_briefing: bool = False, finalizer_overrides: dict | None = None) -> dict:
    """Reconcile, then commit every staged extraction to the vault, then run (or re-run)
    post-ingest over the current on-disk state: file contradictions, synthesize multi-mention
    entities, reconcile the timeline, and write the briefing/hot.md/log.

    Called at the tail of every ingest run (with this run's results in memory) and
    standalone by ``watchdog bark`` (reading persisted ``result_*.json``) to complete a
    post-ingest an earlier rate limit or interrupt left unfinished. On a clean pass the consumed
    inputs (fragments, results, scratchpads) are cleared; if any step failed they are left in
    place so a later finalize can retry.

    #403 phase 3 order: exact-name fold → pre-commit reconciliation (entity-duplicate resolution,
    over the staged batch — ``_reconcile_pre_commit``) → the commit pass (#403 phase 1,
    ``_commit_pending``, which replays ``write_vault.run`` over every
    ``.watchdog/extracted/<sha>.json`` not yet in the registry, in sorted sha order) → post-ingest
    (contradictions, synthesis, timeline, briefing). This is what covers this function's three
    entry paths (ingest's tail, standalone ``watchdog bark``, and a resumed run after a
    rate-limit stop) with one code path.

    If reconciliation itself fails (rate limit / model error), nothing in this batch commits —
    every staged artifact is left exactly as it was, still pending, so a later ``watchdog
    finalize`` retries the whole sequence rather than committing half-reconciled state.

    `force_shas` (#424) are shas that were force-re-extracted and already have a
    `registry/documents.json` entry from an earlier ingest — `_pending_commits` would otherwise
    treat them as already committed and silently drop them from this batch. Passing them here
    puts them back through the commit pass (`_commit_extracted` overwrites their existing document
    note and registry entry in place, the same replace-not-append path a repair retry of an
    already-committed document already relied on) and the reconciliation bundle, so their touched
    entities are re-synthesized along with the rest of this batch.

    `skip_briefing` (#410) skips only the briefing model call — synthesis and the timeline still
    run. Not an error, so it never sets `briefing_error`/leaves inputs pending the way a genuine
    briefing failure does.

    `finalizer_overrides` (#433) routes individual post-ingest stages to a different model than
    `post_model`/`post_backend` — see `_reconcile_pre_commit` and `_post_ingest` for the keys it
    accepts and how each falls back when absent.
    """
    global _usage
    standalone_usage = _usage is None   # not nested inside `run` — this call owns the usage file
    if standalone_usage:
        _begin_usage_run(vault)

    shas = _pending_commits(vault, force_shas=force_shas)
    rec_result: dict = {"merged": [], "remap": {}, "contradictions": [], "error": None}
    if shas:
        _batch_exact_fold(vault, shas)
        rec_result = await _reconcile_pre_commit(vault, shas, post_model, post_effort, post_backend,
                                                 finalizer_overrides=finalizer_overrides)

    if rec_result.get("error"):
        # Reconciliation runs before the commit pass (#403 phase 3), so its failure means nothing
        # in this batch was written — the staged artifacts are all still pending. `commit_skipped`
        # lets the caller say that accurately, rather than the post-commit "documents are saved"
        # message that fits a synthesis/briefing failure.
        out = {"synthesized": 0, "timeline_collisions": 0, "briefing": None,
               "merged": [], "contradictions": [], "error": rec_result["error"],
               "committed_writes": {}, "commit_skipped": True}
        if standalone_usage:
            out["usage_path"], out["usage"] = _end_usage_run(vault)
        return out

    commit_summary = _commit_pending(vault, shas)
    if brief is None:
        brief = _read_brief(vault)
    if results is None:
        results = _load_results(vault)
    out = await _post_ingest(vault, results, brief, post_model, post_effort, post_backend, rec_result,
                             skip_briefing=skip_briefing, finalizer_overrides=finalizer_overrides)
    # Surfaced so `run()` can sync the writer's new/updated split onto its own in-memory
    # `summary["results"]`, built before this pass ever runs (see `_commit_pending`'s docstring).
    out["committed_writes"] = commit_summary["written"]
    if not out.get("error") and not out.get("briefing_error"):
        _clear_post_ingest_inputs(vault)
    if standalone_usage:
        out["usage_path"], out["usage"] = _end_usage_run(vault)
    return out


async def run(vault: Path, *, concurrency: int = DEFAULT_CONCURRENCY,
              extract_model: str = "sonnet", post_model: str = "sonnet",
              classify_model: str = "haiku",
              classify_pages: int = DEFAULT_CLASSIFY_PAGES,
              pinned_skill: str | None = None,
              extract_effort: str | None = None, post_effort: str | None = None,
              extract_backend: str | None = None, post_backend: str | None = None,
              classify_backend: str | None = None, wait: bool = False,
              skip_finalize: bool = False, force: bool = False,
              skip_briefing: bool = False, finalizer_overrides: dict | None = None,
              resume_hint: str = "watchdog dig", verify: bool = False) -> dict:
    """Extract every queued document (bounded by `concurrency`), then post-ingest.

    `extract_model` drives extraction (whole-doc + section, and the sectioned-doc digest); `post_model` drives
    synthesis/timeline/briefing; `classify_model` the cheap document classifier,
    which sees the first `classify_pages` pages of each document. `pinned_skill`
    (a path to a skill file) skips classification and uses that skill for every document.
    `extract_effort`/`post_effort` (`low`/`medium`/`high`) tune reasoning depth for the
    extraction and post-ingest stages respectively (D36); classify has no effort knob.
    `*_backend` selects a non-default backend per stage (None → route by auth mode); a
    non-Claude backend's `*_model` is that provider's raw model id (D37).
    `wait` only changes the rate-limit notice text — the caller (`cmd_ingest`) owns the
    actual sleep-and-resume loop; this function always stops cleanly on a rate limit.
    `skip_finalize` (#384) stops the run after extraction — post-ingest (reconciliation,
    synthesis, timeline, briefing) is never called, and none of its inputs
    (`result_*.json`, `notes_*.md`) are cleared. That leaves
    `has_pending_finalization(vault)` True so a later `watchdog bark` — possibly with a
    different `--finalizer-model`, and possibly against a copy of the vault — can pick up
    exactly where extraction left off.
    `force` (#424) re-extracts every queued document even when a cached artifact or a committed
    vault note already exists for its sha — `cmd_ingest` always pairs this with
    `skip_finalize=True` so it can gate the overwrite of already-committed documents (via
    `finalize`'s own `force_shas`) before anything is recommitted.
    `skip_briefing` (#410) passes straight through to `finalize` — post-ingest still runs
    reconciliation, synthesis, and the timeline, it just skips the briefing model call.
    `finalizer_overrides` (#433) passes straight through to `finalize` — see its docstring for
    the per-stage model/backend keys it accepts.
    `resume_hint` (#441, D138) is the command a "re-run to resume/collect later" notice names —
    `cmd_ingest` passes `watchdog dig` for a `dig` run and bare `watchdog` for the guided walk or
    the deprecated `ingest`, so the extraction-side notices point back at the right entry point.
    `verify` (#535) adds a second, cheap read of each document (or section) that lists the
    material facts the extraction missed, merged deterministically by `pipeline.verify`. Off by
    default and unsupported on `claude-batch`, which is asynchronous by construction — a
    verification call needs the extraction it is verifying to already exist, and a batch's
    results arrive in a later process, hours later, with no cached prefix left to read.
    """
    queue_dir = vault / ".watchdog" / "queue"
    shas = [f.stem for f in sorted(queue_dir.glob("*.json"))] if queue_dir.exists() else []

    global _board, _usage, _resume_hint
    _resume_hint = resume_hint

    # claude-batch/openai-batch (#214, #530): submit-many/poll/collect, not one-await-per-document,
    # so it's a genuinely different flow — handled entirely by _run_batch (which also covers a
    # resumed pending batch even when `shas` is empty). Both branches converge on `results` /
    # `cancelled_flag` / `rate_limit_msg` / `extra_summary` and rejoin the shared tail below.
    if extract_backend in model_client.BATCH_BACKENDS:
        # Same defense-in-depth posture as `_run_batch`'s own auth guard: `cmd_ingest` already
        # refuses this combination, but a programmatic caller that skips CLI validation (a
        # benchmark arm pinning both) must not silently get an unverified run labelled as a
        # verified one — the whole point of the arm is that the label is true.
        if verify:
            raise ValueError(f"the verification pass is not supported with {extract_backend}: it "
                             f"re-reads a document immediately after extracting it, and a batch's "
                             f"results arrive hours later in a separate run")
        _begin_usage_run(vault)
        brief = _read_brief(vault)
        batch_out = await _run_batch(vault, shas, brief, extract_model, pinned_skill,
                                     extract_effort, concurrency, classify_model, classify_pages,
                                     classify_backend, backend=extract_backend, force=force)
        results = batch_out["results"]
        cancelled_flag = False
        rate_limit_msg = None
        rate_limit_resets_at = None
        extra_summary = {"batch_pending": batch_out.get("batch_pending", False)}
    else:
        if not shas:
            return {"results": [], "extracted": 0, "skipped": 0, "failed": 0}

        brief = _read_brief(vault)
        # Live status region for the concurrent extraction phase (#151): one in-place row per
        # in-flight document, finished/failed lines scrolling above. Auto-disables off a TTY,
        # where it degrades to the previous append-only output.
        _board = LiveRegion()
        _begin_usage_run(vault)
        sem = asyncio.Semaphore(max(1, concurrency))
        cancelled = asyncio.Event()
        stop_reason: dict = {}      # {"rate_limit": "<notice>"} when a limit stopped the batch
        tasks: list = []

        def _request_stop(rate_limit: str | None = None, resets_at: int | None = None) -> None:
            """Stop the batch once: flag it, record why, cancel in-flight work. Idempotent."""
            if cancelled.is_set():
                return
            cancelled.set()
            if rate_limit:
                stop_reason["rate_limit"] = rate_limit
                stop_reason["resets_at"] = resets_at
            for t in tasks:
                t.cancel()

        async def _guarded(sha: str) -> dict:
            if cancelled.is_set():                   # never started — leave queue file for resume
                return {"sha256": sha, "filename": "", "status": "cancelled"}
            async with sem:
                if cancelled.is_set():
                    return {"sha256": sha, "filename": "", "status": "cancelled"}
                try:
                    return await _extract_document(vault, sha, brief, extract_model, classify_model,
                                                   classify_pages, pinned_skill, extract_effort,
                                                   extract_backend, classify_backend, force=force,
                                                   verify_pass=verify)
                except model_client.RateLimitError as e:  # session-wide — stop, leave queued for resume
                    if not cancelled.is_set():
                        print()
                        _say(f"{_YELLOW}Rate limit reached{_RESET}{_DIM} — {e}{_RESET}")
                        # #563: ground the "lower extract_concurrency" advice below in the actual
                        # numbers instead of leaving it a guess — the tokens/min this run was
                        # sustaining right before the stop, and (when the provider sent them) the
                        # last-seen remaining/limit off the 429 itself.
                        detail_parts = []
                        rate = _recent_token_rate(_usage) if _usage else 0
                        if rate:
                            detail_parts.append(f"~{rate:,} tokens/min observed")
                        rl = e.rate_limit or {}
                        remaining, limit = rl.get("remaining_tokens"), rl.get("limit_tokens")
                        if remaining is not None and limit is not None:
                            detail_parts.append(f"provider reported {remaining:,}/{limit:,} tokens remaining")
                        elif limit is not None:
                            detail_parts.append(f"provider token limit {limit:,}/min")
                        if detail_parts:
                            _say(f"{_DIM}{'; '.join(detail_parts)} — consider a lower "
                                 f"{_RESET}{_CYAN}extract_concurrency{_RESET}{_DIM}.{_RESET}")
                        if wait:
                            _say(f"{_DIM}Stopping; finished documents are saved. "
                                 f"Waiting to resume automatically once it resets.{_RESET}")
                        else:
                            _say(f"{_DIM}Stopping; finished documents are saved. Re-run "
                                 f"{_RESET}{_CYAN}{_resume_hint}{_RESET}{_DIM} once it resets to continue.{_RESET}")
                        _request_stop(rate_limit=str(e), resets_at=e.resets_at)
                    return {"sha256": sha, "filename": "", "status": "cancelled"}
                except asyncio.CancelledError:       # ctrl+c mid-document — queue file stays
                    return {"sha256": sha, "filename": "", "status": "cancelled"}
                except Exception as e:               # one bad doc must not sink the batch
                    return _fail(vault, sha, "", f"unexpected error: {e}")

        # On ctrl+c, cancel in-flight work once and shut down cleanly instead of letting
        # KeyboardInterrupt tear through the event loop with a traceback. Finished documents
        # are already written to the vault; unfinished ones keep their queue file for resume.
        tasks[:] = [asyncio.ensure_future(_guarded(s)) for s in shas]

        async def _tick_elapsed() -> None:
            """Keep a pinned "Elapsed MM:SS" row at the bottom of the live region while
            extraction runs (#411) — a long pause on a dense document (minutes, not seconds,
            #398) otherwise reads as a stall rather than normal progress."""
            start = time.monotonic()
            while True:
                await asyncio.sleep(1)
                elapsed = int(time.monotonic() - start)
                _board.update("__elapsed__", f"  {_DIM}Elapsed {elapsed // 60:02d}:{elapsed % 60:02d}{_RESET}",
                              pin=True)

        # Only on a real TTY — LiveRegion.update() falls back to plain append-only printing
        # when disabled, which would spam a new line every second into logs/CI output.
        if _board.enabled:
            # Blank clearance line above the elapsed row (#456), same pattern as chew's own
            # pinned progress bar (preprocess_batch._SPACER_KEY) — registered once, before the
            # timer starts, since pinned rows render in insertion order among pinned keys.
            _board.update("__elapsed_spacer__", "", pin=True)
        timer_task = asyncio.ensure_future(_tick_elapsed()) if _board.enabled else None

        def _on_interrupt() -> None:
            if cancelled.is_set():
                return
            print()
            _say(f"{_YELLOW}Interrupted{_RESET}{_DIM} — finishing current writes, then stopping…{_RESET}")
            _request_stop()

        loop = asyncio.get_running_loop()
        handler_set = False
        try:
            loop.add_signal_handler(signal.SIGINT, _on_interrupt)
            handler_set = True
        except (NotImplementedError, RuntimeError):
            # e.g. non-main thread, or Windows' Proactor event loop, which never supports
            # add_signal_handler. There, every Ctrl+C falls through to cmd_ingest's plain
            # `except KeyboardInterrupt` instead of this module's graceful
            # finish-current-writes path — see that handler's comment for what differs.
            pass
        try:
            # capture_stderr (#419): a dependency running in one of these tasks' worker
            # threads (e.g. harvest's GLiNER load) can write straight to stderr mid-extraction;
            # left alone that corrupts the board's redraw math, duplicating in-flight rows.
            with _board.capture_stderr():
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            if timer_task is not None:
                timer_task.cancel()
                try:
                    await timer_task
                except asyncio.CancelledError:
                    pass
            if handler_set:
                loop.remove_signal_handler(signal.SIGINT)
            # Close the live region: extraction is done, so post-processing (sequential) and the
            # summary print plainly. _say falls back to plain printing once _board is None.
            _board.stop()
            _board = None

        results = [t.result() for t in tasks if t.done() and not t.cancelled()]
        cancelled_flag = cancelled.is_set()
        rate_limit_msg = stop_reason.get("rate_limit")
        rate_limit_resets_at = stop_reason.get("resets_at")
        extra_summary = {}

    def by_status(s):
        return sum(1 for r in results if r.get("status") == s)

    failed_dir = vault / ".watchdog" / "queue" / "_failed"
    quarantined = len(list(failed_dir.glob("*.json"))) if failed_dir.exists() else 0
    summary = {"results": results, "extracted": by_status("ok"),
               "skipped": by_status("skipped"), "failed": by_status("failed"),
               "cancelled": cancelled_flag,
               "rate_limited": bool(rate_limit_msg),
               "stop_message": rate_limit_msg,
               "rate_limit_resets_at": rate_limit_resets_at,
               "quarantined": quarantined, **extra_summary}
    if not pinned_skill:
        _nudge_skill_pin(results)
    if summary["extracted"] and not cancelled_flag and skip_finalize:
        # Extract-only (#384): leave the post-ingest inputs on disk untouched — a later
        # `watchdog bark` (possibly on a vault copy, possibly with a different
        # `--finalizer-model`) consumes them. `has_pending_finalization` is already True by
        # this point (`_finish_extraction` persisted `result_*.json` per document).
        summary["finalize_skipped"] = True
    elif summary["extracted"] and not cancelled_flag:
        try:
            # Finalize over the persisted per-doc results on disk (not just this run's in-memory
            # ones) so a merged batch — a prior pending run kept via wipe_pending=False — is
            # synthesized and briefed together with this run's documents.
            summary["post_ingest"] = await finalize(vault, post_model=post_model, brief=brief,
                                                    post_effort=post_effort, post_backend=post_backend,
                                                    skip_briefing=skip_briefing,
                                                    finalizer_overrides=finalizer_overrides)
            # `results` was built before finalize's commit pass ran write_vault, so each item's
            # new/updated entity split was still unknown at the time (#403 phase 1) — sync it in
            # now from what the commit pass actually did, so a caller reading `summary["results"]`
            # (not just the briefing, which already reads the patched result_<sha>.json) sees it.
            written_map = summary["post_ingest"].get("committed_writes") or {}
            for r in results:
                w = written_map.get(r.get("sha256"))
                if w:
                    r["new_entities"] = w.get("new_entities", [])
                    r["updated_entities"] = w.get("updated_entities", [])
            _update_graph_colours(vault)
        except Exception as e:   # post-ingest is enrichment — never let it crash a saved batch
            summary["post_ingest_error"] = str(e)
            print()
            _say(f"{_YELLOW}Post-processing incomplete{_RESET}{_DIM} — {e}{_RESET}")
            _say(f"{_DIM}Your {summary['extracted']} extracted document"
                 f"{'s are' if summary['extracted'] != 1 else ' is'} saved.{_RESET}")
    state = "rate-limited" if summary["rate_limited"] else ("cancelled" if cancelled_flag else "complete")
    _log(vault, f"INGEST {state} — {summary['extracted']} extracted, "
                f"{summary['skipped']} skipped, {summary['failed']} failed")
    # #417: sum of the naive chars/4 estimate over this run's successfully extracted documents,
    # so this run's usage file records both what was estimated and what extraction actually
    # consumed — the input a later `--estimate` calibrates against. None (not 0) when nothing
    # extracted, so a skipped/all-cached run doesn't masquerade as a zero-token calibration point.
    est_input_tokens = sum(r.get("est_input_tokens") or 0 for r in results
                           if r.get("status") == "ok") or None
    summary["usage_path"], summary["usage"] = _end_usage_run(vault, est_input_tokens=est_input_tokens)
    return summary
