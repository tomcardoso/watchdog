"""Adding documents (D251, D298): the pre-processing, processing and post-processing operations,
the options one run can set, and how each stage's model is resolved from them and the settings.

`add` runs the whole flow; `chew`, `dig` and `bark` run one stage each (the app's Maintenance
screen). The app resolves the same `Plan` to show what a run will do before it starts one
(`gui/api/ingest.py`), so it can never show a plan a run would not follow.
"""

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from watchdog import config as config_mod
from watchdog import defaults
from watchdog.model_catalog import _MODEL_IDS
from watchdog.ops import op, require_vault, say
from watchdog.vault import count_failed, count_queued, render_template
from watchdog.vault_paths import incoming_dir, is_vault, processing_lock, processing_state

# Reasoning-effort levels for the per-stage effort knobs (D36). `high` is the model
# default, so an unset knob behaves as before (the orchestrator sends no effort param).
# `xhigh`/`max` (#518) are accepted here syntactically; `model_client._resolve_effort` is the
# source of truth for which provider/model actually supports which level, and rejects an
# unsupported one loudly — at any level, not just xhigh/max (D158).
_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


# Metered-provider default (#493) — clears the lowest published paid-tier RPM floor across
# Anthropic/OpenAI/DeepSeek with margin, given how long an extraction call actually takes (D162).
# Watchdog lowers this to `auth.SUBSCRIPTION_CONCURRENCY` (3) in config when processing stays on a
# Claude subscription, which throttles well below this value.
_DEFAULT_EXTRACT_CONCURRENCY = defaults.EXTRACT_CONCURRENCY


# Waiting out a rate limit (#271): cushion past the provider's reported reset time, since a resume attempted
# right at the boundary can still land inside the window.
_WAIT_BUFFER_SECONDS = 30


# Fallback sleep when RateLimitError carried no `resets_at` — true for the claude-api and
# OpenAI-compatible backends, which don't report a reset timestamp (only claude-agent-sdk does).
_WAIT_FALLBACK_SECONDS = 15 * 60


# Sleep in chunks, refreshing the lock after each. The heartbeat already stamps it from its own
# thread throughout; this is a second beat inside the age window (`ingest_setup.STALE_SECONDS`).
_WAIT_REFRESH_SECONDS = 5 * 60


def effort(flag_val, config_val, *, default=None, backend=None, model=None):
    """Resolve a per-stage effort knob (run option > setting > model-aware default > unset).

    `flag_val`/`config_val` are explicit choices (the run's own option or the setting), so an unsupported
    level for the resolved model fails loud downstream in `model_client._resolve_effort`, correctly, since it's exactly what was asked for. `default`
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
# to a different model than the finalizer as a whole, falling back to it when unset.
FINALIZER_STAGES = ("reconciliation", "synthesis", "timeline", "briefing")


def resolve_stage(flag_val, config_val, default=defaults.EXTRACTOR_MODEL) -> tuple[str | None, str]:
    """Resolve a stage's `[backend:]model` knob into (backend, model) (#125).

    Plain `sonnet`/`opus`/`haiku` → (None, tier): Claude, routed by auth mode (unchanged). A
    `backend:model` form selects a backend explicitly — `claude-api:opus` (a Claude tier), or
    `openai:gpt-5-mini` / `deepseek:deepseek-flash` (a raw provider model id). Carrying both in
    one value means a stage can never be half-configured."""
    from watchdog.model_client import BACKENDS, CLAUDE_BACKENDS
    raw = flag_val or config_val or default
    backend, model = defaults.split_backend_model(raw)
    if backend is not None and backend not in BACKENDS:
        sys.exit(f"Error: unknown backend '{backend}' — choose {', '.join(BACKENDS)}")
    if backend is None or backend in CLAUDE_BACKENDS:
        if model not in _MODEL_IDS:
            sys.exit(f"Error: unknown model '{model}' — choose sonnet, opus, or haiku, "
                     f"or a backend:model form like deepseek:deepseek-flash")
    elif not model:
        sample = "deepseek-flash" if backend == "deepseek" else "gpt-5-mini"
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
    line = (f"  {n} document{'s' if n != 1 else ''} · "
            f"~{pages} page{'s' if pages != 1 else ''} · "
            f"est. ~{_fmt_tokens(est['est_tokens'])} tokens in")
    if est["cost_low"] is not None:
        low, high = round(est["cost_low"]), round(est["cost_high"])
        cost = f"${low}" if low == high else f"${low}-{high}"
        runs = est["runs_used"]
        run_label = "last run" if runs == 1 else f"last {runs} runs"
        line += f" (~{cost} based on your {run_label})"
    return line


def _format_finalize_estimate(est: dict) -> str:
    """Render `ingest_setup.finalize_cost_estimate`'s result as post-processing's
    pre-flight line (#417), e.g. '3 documents staged · est. ~45K tokens in (~$0.18-0.24 based on
    your last 2 standalone finalizes)'. Costs here run far smaller than ingest's own line (a
    handful of reconciliation/synthesis calls, not a whole document's extraction), so the dollar
    figure keeps two decimal places instead of `_format_cost_estimate`'s whole dollars."""
    n = est["docs"]
    line = (f"  {n} document{'s' if n != 1 else ''} staged · "
            f"est. ~{_fmt_tokens(est['est_tokens'])} tokens in")
    if est["cost_low"] is not None:
        low, high = round(est["cost_low"], 2), round(est["cost_high"], 2)
        cost = f"${low}" if low == high else f"${low}-{high}"
        runs = est["runs_used"]
        run_label = "last standalone finalize" if runs == 1 else f"last {runs} standalone finalizes"
        line += f" (~{cost} based on your {run_label})"
    return line


def _format_all_models_estimate(rows: list[dict]) -> str:
    """Render `ingest_setup.cost_estimate_all_models`/`finalize_cost_estimate_all_models`'s
    per-model projection (#469) as a single cheapest-first table — one line per catalog model,
    rather than a separate estimate per model per pipeline stage, which is what keeps this
    readable as the catalog grows. `rows` is already sorted by `cost` ascending."""
    if not rows:
        return ("  Not enough usage history yet to project other models"
                + "; it needs a few finished runs" + ".")
    name_w = max(len(r["name"]) for r in rows)
    lines = ["  Projected list price by model, cheapest first (every input "
             "token priced as a cache miss — a rough ceiling, not what you'd actually pay with "
             "caching):"]
    for r in rows:
        cost = r["cost"]
        cost_s = f"${cost:.4f}" if cost < 1 else f"${cost:,.2f}"
        # A model priced by the clock (D217) is quoted at the rate in force right now, and says
        # which side of the schedule that is — otherwise the same command run two hours apart
        # returns two different numbers for the same model with nothing to explain the gap.
        rate = r.get("price_multiplier", 1.0)
        note = ""
        if rate > 1:
            note = "  (peak rate — this model is cheaper outside peak hours)"
        elif rate < 1:
            note = "  (off-peak rate — this model costs more during peak hours)"
        lines.append(f"    {r['name']:<{name_w}}  {r['provider']:<10}  {cost_s}{note}")
    return "\n".join(lines)


def _effective_extract_backend(extract_backend: str | None, auth_mode: str) -> str:
    """The backend that will actually serve extraction calls when `extract_backend` is unset
    (plain sonnet/opus/haiku) — mirrors `model_client`'s own subscription/api-key routing, so the
    cost estimate knows whether a dollar figure means anything (#269)."""
    return extract_backend or ("claude-agent-sdk" if auth_mode == "subscription" else "claude-api")


def _format_models_line(classify_backend, classify_model, extract_backend, extract_model,
                        post_backend, post_model, classify_effort=None, extract_effort=None,
                        post_effort=None, finalizer_overrides=None, concurrency=None,
                        is_dig=False) -> str:
    """The model each stage will run on, printed before a run starts.

    One row per stage, padded into a column. `finalizer_overrides` adds a row for each post-ingest
    stage that differs from the finalizer; `concurrency` adds a row only when the run sets it;
    `is_dig` drops the finalizer rows, since processing alone doesn't finalize."""
    def label(backend, model):
        return f"{backend}:{model}" if backend else model
    stages = [("classifier", classify_backend, classify_model, classify_effort),
              ("extractor", extract_backend, extract_model, extract_effort)]
    if not is_dig:
        stages.append(("finalizer", post_backend, post_model, post_effort))
        for stage in FINALIZER_STAGES:
            b = (finalizer_overrides or {}).get(f"{stage}_backend", post_backend)
            m = (finalizer_overrides or {}).get(f"{stage}_model", post_model)
            if (b, m) != (post_backend, post_model):
                stages.append((f"finalizer:{stage}", b, m, post_effort))
    width = max(len(name) for name, *_ in stages)
    if concurrency is not None:
        width = max(width, len("concurrency"))
    lines = []
    for name, b, m, effort in stages:
        suffix = f" (effort: {effort})" if effort else ""
        lines.append(f"  {name:<{width}} {label(b, m)}{suffix}")
    if concurrency is not None:
        lines.append(f"  {'concurrency':<{width}} {concurrency}")
    return "\n".join(lines)


def _require_investigation_keys(vault: Path, backends) -> None:
    """Stop before any model call when the investigation chose a labelled key this computer
    doesn't have for a provider the run would use — never bill another account instead (D290)."""
    from watchdog.auth import KeyChoiceError, check_run_keys
    try:
        check_run_keys(vault, backends)
    except KeyChoiceError as e:
        sys.exit(f"\n  Error: {e}\n")


def _into_incoming(vault: Path, f: Path, quiet: bool = False) -> Path:
    """The path pre-processing should read for a named file: `f` itself when it is already in
    `incoming/`, else a copy placed there. Chew *moves* what it processes into the vault, so
    chewing a file from anywhere else — `~/Downloads`, or the vault's own `context/` — would
    silently take it away from where the user keeps it."""
    incoming = incoming_dir(vault)
    if f.is_relative_to(incoming.resolve()):
        return f
    incoming.mkdir(exist_ok=True)
    dest = incoming / f.name
    n = 1
    while dest.exists():
        n += 1
        dest = incoming / f"{f.stem}-{n}{f.suffix}"
    shutil.copy2(f, dest)
    sidecar = f.with_name(f"{f.name}.yml")
    if sidecar.exists():
        shutil.copy2(sidecar, dest.with_name(f"{dest.name}.yml"))
    if not quiet:
        say(f"\n  Copied {f.name} into incoming/ — the original "
              f"stays where it is.")
    return dest


def _public_records_warning(n_docs: int) -> str:
    """The README's `## Public records only` warning (README.md:13-17), reworded for the
    gate at the point of no return (#426). Wording tracks the README section so the
    two never drift.

    The app shows it in the Add documents dialog before it starts a run."""
    return (
        f"\n  ⚠  Public records only\n\n"
        "  The extracted text of every queued document will be sent to a\n"
        "  cloud AI model. This cannot be undone. Use Watchdog only for\n"
        "  documents that are public, or presumptively public — never for\n"
        "  confidential source material, leaks, or anything that could\n"
        "  identify a source.\n\n"
        f"  {n_docs} document{'s' if n_docs != 1 else ''} will be sent to the model."
    )


def _auto_approve_on(config: dict) -> bool:
    """The `auto_approve` setting (D263): True only when it is explicitly on."""
    return config.get("auto_approve") is True


def _auto_approve_verdict(*, auth_mode: str | None, stages: list[str | None]) -> dict:
    """Whether this run may skip the public-records pause (D263): `{"approve": True}` only when
    the auth mode is the Claude subscription and every stage it calls — classify, extract and each
    finishing stage, given by its configured backend — runs on it, which has no per-run charge.
    Otherwise `{"blocker": reason}`. An explicit `claude-agent-sdk` stage is not enough on its
    own: on API-key auth that backend is handed the paid key. There is deliberately no dollar
    estimate: an estimate from past runs can come out low, and the pause it would skip is the only
    check on what leaves the computer."""
    if auth_mode == "subscription" and all(b in (None, "claude-agent-sdk") for b in stages):
        return {"approve": True}
    return {"blocker": "at least one step of this run uses a paid API key, and auto-approve "
                       "applies only when every step runs on your Claude subscription"}


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
    note = "" if exact else " (estimated — the provider didn't report a reset time)"
    stop = "Use Stop in Activity to stop"
    say(f"\n  Rate limit — resuming at {wake_at:%H:%M}"
          f"{note}. {stop}; finished documents are saved.\n")
    remaining = sleep_s
    while remaining > 0:
        chunk = min(remaining, _WAIT_REFRESH_SECONDS)
        time.sleep(chunk)
        remaining -= chunk
        refresh_lock(lock_file)


def _merge_summary(base: dict | None, new: dict) -> dict:
    """Fold one rate-limit wait's summary into the running total.

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


def _quarantine_notice(n: int) -> str:
    """The '_failed/ needs attention' line (#406), shared by the normal-completion summary,
    the stop message, and the empty-queue checks — same wording everywhere a quarantined
    document could otherwise go unmentioned."""
    return (f"{n} document{'s' if n != 1 else ''} need attention"
            + (f" — retry {'them' if n != 1 else 'it'} with Activity → Maintenance → "
                    f"Requeue failed documents."))


@contextlib.contextmanager
def _caffeinate():
    """Keep the machine from sleeping for the life of a real ingest run (#415) — unlike a
    network blip a retry can absorb, the OS suspending mid-run kills any active model call
    outright. Never a hard dependency, and a no-op everywhere else  — including when the platform's own
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
    were moved (0 if none were waiting): the requeue operation, and adding documents with
    `retry`."""
    failed_dir = vault / ".watchdog" / "queue" / "_failed"
    files = sorted(failed_dir.glob("*.json")) if failed_dir.exists() else []
    if not files:
        return 0
    queue_dir = vault / ".watchdog" / "queue"
    for f in files:
        f.replace(queue_dir / f.name)
    return len(files)


def exit_code_for(result) -> int:
    """Map a run's summary to the worker's exit code (#499 follow-up).

    2 means incomplete but resumable: the run stopped partway, vault state is consistent, and
    re-running continues the work — a rate limit, a pending batch, documents left cancelled/
    unstarted, or post-processing that didn't finish. Quarantined/failed documents
    alone stay 0: the run completed and set them aside for a requeue, which is a
    reported outcome, not an incomplete one. Anything that isn't a dict carrying one of these
    keys — `None`, or some other return value — is 0."""
    if not isinstance(result, dict):
        return 0
    if result.get("auth_error") or (result.get("post_ingest") or {}).get("auth_error"):
        return 1   # not resumable by re-running — the key or the account needs fixing first
    if result.get("rate_limited") or result.get("batch_pending"):
        return 2
    if result.get("cancelled") or any(r.get("status") == "cancelled" for r in result.get("results", [])):
        return 2
    if result.get("post_ingest_error") or (result.get("post_ingest") or {}).get("error"):
        return 2
    if result.get("error") or result.get("briefing_error"):
        return 2
    return 0


def _run_finalize(vault: Path, post_model: str, post_effort: str | None = None,
                  post_backend: str | None = None, skip_briefing: bool = False, finalizer_overrides: dict | None = None,
                  benchmark_arm_id: str | None = None) -> dict:
    """Take the run lock, run post-processing over the pending batch, report the outcome.

    `skip_briefing` (#410) passes straight through to `orchestrate.finalize` — reconciliation,
    synthesis, and the timeline still run; only the briefing model call is skipped.

    `finalizer_overrides` (#433) passes straight through to `orchestrate.finalize` — per-stage
    model/backend overrides for reconciliation, synthesis, timeline, and briefing."""
    from watchdog.pipeline import orchestrate
    from watchdog.pipeline.locks import acquire_or_take_stale, heartbeat, held, lock_contents, release_lock
    from watchdog.pipeline.ingest_setup import STALE_SECONDS, busy_message
    lock = processing_lock(vault)
    # Atomic acquisition (#257): the shared .processing-lock means a running ingest or a second
    # finalize is excluded without a check-then-write race; an abandoned lock is taken over (D293).
    if not acquire_or_take_stale(lock, lock_contents("post-processing"), STALE_SECONDS):
        sys.exit("Error: " + busy_message(held(lock, STALE_SECONDS)))
    stages = "entity reconciliation + synthesis + timeline" if skip_briefing else \
        "entity reconciliation + synthesis + timeline + briefing"
    say(f"Finishing the batch: {stages} (model: {post_model}).")
    try:
        import asyncio
        # #467: post-processing has no upper bound on how long reconciliation/synthesis/the briefing
        # take, the same failure mode _caffeinate() was added to guard extraction against (#415)
        # — without it, the machine sleeping mid-call kills a finalize outright.
        with _caffeinate(), heartbeat(lock):
            out = asyncio.run(orchestrate.finalize(vault, post_model=post_model, post_effort=post_effort,
                                                   post_backend=post_backend,
                                                   skip_briefing=skip_briefing,
                                                   finalizer_overrides=finalizer_overrides,
                                                   benchmark_arm_id=benchmark_arm_id))
    finally:
        release_lock(lock)

    if out.get("error") or out.get("briefing_error"):
        reason = out.get("error") or out.get("briefing_error")
        say(f"The batch didn't finish: {reason}.")
        say("Nothing is lost. Run post-processing again once the cause above is fixed "
            "(for a rate limit, once it resets).")
        return out
    n = out.get("synthesized", 0)
    parts = [f"{n} entit{'ies' if n != 1 else 'y'} synthesized"]
    if out.get("briefing"):
        parts.append(f"briefing {out['briefing']}")
    elif out.get("briefing_skipped"):
        parts.append("briefing skipped")
    say("Finished: " + ", ".join(parts) + ".")
    return out


def _is_hidden(path: Path, root: Path) -> bool:
    """A dotfile or a file inside a dot-folder below `root` (`.git/`, `.obsidian/`, macOS `._x`)."""
    return any(part.startswith(".") for part in path.relative_to(root).parts)


def _expand_paths(paths: list[str], vault: Path) -> list[Path]:
    """Files the reporter chose, with folders expanded to the files pre-processing reads in them.

    Never an investigation's own files: a path inside this vault is refused (files already in
    `incoming/` are picked up without being named), and a folder that contains this or any other
    vault — `add ~/Documents`, `add ~/Investigations` — has those vaults' files left out, so
    Watchdog's notes and registries are never read back in as source documents. Hidden files and
    folders are skipped."""
    from watchdog.pipeline.preprocess_batch import find_files
    vault = vault.resolve()
    out: list[Path] = []
    for raw in paths:
        p = Path(raw).expanduser().resolve()
        if not p.exists():
            sys.exit(f"Error: not found: {raw}")
        if p == vault or vault in p.parents:
            if p == incoming_dir(vault) or incoming_dir(vault) in p.parents:
                continue   # already waiting; added below without copying
            sys.exit(f"Error: {raw} is inside this investigation. Name files from outside it, "
                     f"or move them into incoming/.")
        if p.is_dir():
            found = [f for f in find_files([p]) if not _is_hidden(f, p)]
            vault_dirs: dict[Path, bool] = {}

            def in_a_vault(f: Path) -> bool:
                # This one or any other Watchdog investigation under `p` (`add ~/Investigations`).
                for d in f.parents:
                    if d == p.parent:
                        return False
                    if d not in vault_dirs:
                        vault_dirs[d] = d == vault or is_vault(d)
                    if vault_dirs[d]:
                        return True
                return False

            inside = [f for f in found if in_a_vault(f)]
            if inside:
                say(f"\n  Leaving out {len(inside)} file{'s' if len(inside) != 1 else ''} "
                      f"that belong to a Watchdog investigation's own folder.")
                found = [f for f in found if not in_a_vault(f)]
            if not found:
                say(f"\n  No supported files in {raw}")
            out.extend(found)
        elif p.name.endswith(".yml") or p.name.startswith("."):
            continue   # a sidecar travels with its document; hidden files are never documents
        else:
            out.append(p)
    return out


def requeue_message(n: int) -> str:
    if not n:
        return "No failed documents to requeue."
    return f"Requeued {n} document{'s' if n != 1 else ''}. Add documents again to retry them."


def _clean(result):
    """A run summary as JSON: paths and other objects become strings."""
    if result is None:
        return None
    return json.loads(json.dumps(result, default=str))



# ── a run's options and plan ─────────────────────────────────────────────────────────────

@dataclass
class RunOptions:
    """One run's choices, each overriding its setting for this run only; None means "use the
    setting". The app's Add documents and Maintenance forms set the first group; the benchmark
    harness alone sets the last three."""
    extractor_model: str | None = None
    classifier_model: str | None = None
    finalizer_model: str | None = None
    finalizer_reconciliation_model: str | None = None
    finalizer_synthesis_model: str | None = None
    finalizer_timeline_model: str | None = None
    finalizer_briefing_model: str | None = None
    extractor_effort: str | None = None
    classifier_effort: str | None = None
    finalizer_effort: str | None = None
    concurrency: int | None = None
    classify_pages: int | None = None
    skill: str | None = None
    limit: int | None = None          # extract only the next N queued documents (#696)
    verify: bool | None = None        # the verification pass (#535); None: the setting
    force: bool = False               # re-extract even where a staged extraction exists
    wait: bool = False                # wait out a rate limit and continue (#271)
    skip_briefing: bool = False       # post-processing without the briefing call (#410)
    skip_warning: bool = False        # the public-records acknowledgement was given (#426)
    # Benchmark harness only (#559, D278).
    max_rate_limit_waits: int | None = None
    benchmark_arm_id: str | None = None
    withhold_brief: bool = False


@dataclass
class Plan:
    """Every stage's resolved `(backend, model)` and effort for one run."""
    config: dict
    classify: tuple
    extract: tuple
    post: tuple
    classify_effort: str | None
    extract_effort: str | None
    post_effort: str | None
    overrides: dict = field(default_factory=dict)

    @property
    def backends(self) -> list:
        return [self.classify[0], self.extract[0], self.post[0],
                *(self.overrides.get(f"{s}_backend") for s in FINALIZER_STAGES)]

    @property
    def finalizer_backends(self) -> list:
        return [self.post[0], *(self.overrides.get(f"{s}_backend") for s in FINALIZER_STAGES)]


def resolve_finalizer_overrides(opts: RunOptions, config: dict, post_backend: str | None,
                                post_model: str) -> dict:
    """The four per-stage finalizer overrides (#433), each falling back to the finalizer itself:
    `<stage>_model`/`<stage>_backend` keys, the shape `orchestrate.finalize` takes."""
    default = f"{post_backend}:{post_model}" if post_backend else post_model
    overrides: dict = {}
    for stage in FINALIZER_STAGES:
        key = f"finalizer_{stage}_model"
        backend, model = resolve_stage(getattr(opts, key), config.get(key), default=default)
        overrides[f"{stage}_backend"] = backend
        overrides[f"{stage}_model"] = model
    return overrides


def resolve_plan(opts: RunOptions, config: dict | None = None) -> Plan:
    """Resolve every stage and effort from the run's options and the settings (a bad value stops
    with a message naming it)."""
    config = config_mod.load() if config is None else config
    extract = resolve_stage(opts.extractor_model, config.get("extractor_model"))
    post = resolve_stage(opts.finalizer_model, config.get("finalizer_model"),
                         default=defaults.FINALIZER_MODEL)
    classify = resolve_stage(opts.classifier_model, config.get("classifier_model"),
                             default=defaults.CLASSIFIER_MODEL)
    return Plan(
        config=config, classify=classify, extract=extract, post=post,
        classify_effort=effort(opts.classifier_effort, config.get("classifier_effort"),
                               default=defaults.CLASSIFIER_EFFORT, backend=classify[0],
                               model=classify[1]),
        extract_effort=effort(opts.extractor_effort, config.get("extractor_effort"),
                              default=defaults.EXTRACTOR_EFFORT, backend=extract[0],
                              model=extract[1]),
        post_effort=effort(opts.finalizer_effort, config.get("finalizer_effort")),
        overrides=resolve_finalizer_overrides(opts, config, post[0], post[1]),
    )


def _require_auth(backends) -> dict:
    """Claude's auth, when some stage is routed to Claude; `{"mode": None}` otherwise. A run
    configured entirely on another provider needs no Claude auth at all (#325)."""
    from watchdog.auth import resolve_auth
    from watchdog.model_client import CLAUDE_BACKENDS
    if not any(b is None or b in CLAUDE_BACKENDS for b in backends):
        return {"mode": None}
    a = resolve_auth()
    if a["mode"] == "none":
        sys.exit(f"Error: {a.get('reason', 'no model provider is set up')}")
    return a


def _resolve_pinned_skill(opts: RunOptions, config: dict) -> str | None:
    """The record skill pinned for every document (the run's option, else `default_skill`), as a
    file path. The value may be a skill's name or a path to a skill file."""
    from watchdog import skills_catalog
    value = opts.skill or config.get("default_skill")
    if not value:
        return None
    resolved = skills_catalog.resolve(value)
    if resolved:
        return resolved
    avail = ", ".join(skills_catalog.catalog()) or "(none available)"
    sys.exit(f"Error: there is no record skill “{value.removesuffix('.md')}” (not a known skill "
             f"or a file). Available: {avail}")


def _confirm_public_records(n_docs: int, *, skip_warning: bool = False, gate: dict | None = None,
                            enabled: bool = False) -> bool:
    """The point of no return before a run sends documents to a model (#426). The app shows the
    public-records warning and asks before it starts a run, then passes `skip_warning`; without
    it, nothing is sent. With `auto_approve` on (D263), a run whose `gate` verdict approves it
    goes ahead without the question. A run that sends nothing (`n_docs == 0`) passes silently."""
    if n_docs == 0:
        return True
    plural = "s" if n_docs != 1 else ""
    if enabled and (gate or {}).get("approve"):
        say(f"Auto-approved (every step on your Claude subscription): sending {n_docs} "
            f"document{plural} to a cloud AI model.")
        return True
    if skip_warning:
        say(f"Sending {n_docs} document{plural} to a cloud AI model.")
        return True
    if enabled:
        say(f"Not auto-approved: {(gate or {}).get('blocker') or 'this run could not be checked'}.")
    say("Nothing was sent: the public-records acknowledgement was not given.")
    return False


def _warn_pending_research(vault: Path) -> None:
    """Note web-research links still queued for download (#196), so they aren't lost."""
    from watchdog.pipeline import research
    n = research.pending_count(vault)
    if n:
        say(f"{n} research link{'s' if n != 1 else ''} queued but not downloaded; they stay "
            f"queued. Download them from Web research.")


# ── the runs ─────────────────────────────────────────────────────────────────────────────

def process(vault: Path, opts: RunOptions, *, finalize: bool) -> dict | None:
    """Process the queue: classify and extract each document, then, with `finalize`, run
    post-processing over the batch (adding documents, D251). Without it the extractions are
    staged and the batch waits for `finish` (processing only, D138). Returns the run's summary,
    or None when there was nothing to do or nothing was sent."""
    plan = resolve_plan(opts)
    config = plan.config
    extract_backend, extract_model = plan.extract
    post_backend, post_model = plan.post
    classify_backend, classify_model = plan.classify
    overrides = plan.overrides

    a = _require_auth(plan.backends)
    _require_investigation_keys(vault, plan.backends)
    try:
        concurrency = int(opts.concurrency or config.get("extract_concurrency")
                          or _DEFAULT_EXTRACT_CONCURRENCY)
    except (TypeError, ValueError):
        concurrency = _DEFAULT_EXTRACT_CONCURRENCY
    # extract_token_budget (#563): "auto" (default) lets admission control discover the ceiling
    # from the provider's rate-limit headers; a number pins it.
    raw_token_budget = config.get("extract_token_budget", "auto")
    try:
        token_budget = None if raw_token_budget == "auto" else int(raw_token_budget)
    except (TypeError, ValueError):
        token_budget = None
    try:
        classify_pages = int(opts.classify_pages or config.get("classify_pages") or defaults.CLASSIFY_PAGES)
    except (TypeError, ValueError):
        classify_pages = defaults.CLASSIFY_PAGES
    classify_pages = max(1, classify_pages)

    from watchdog.pipeline import orchestrate
    from watchdog.pipeline.ingest_setup import needs_extraction, scan_queue
    from watchdog.pipeline.ingest_setup import run as is_run

    # A prior run may have left a batch un-finalized: processing alone stages without finalizing,
    # and a rate limit or Stop can end post-processing partway. Nothing in it is ever thrown
    # away: the next finalize commits every staged extraction, whichever run that is.
    if orchestrate.has_pending_finalization(vault):
        new_docs = scan_queue(vault) if opts.force else needs_extraction(vault, scan_queue(vault))
        p = orchestrate.pending_finalization(vault)
        bits = []
        if p["docs"]:
            bits.append(f"{p['docs']} document{'s' if p['docs'] != 1 else ''}")
        if p["entities"]:
            bits.append(f"{p['entities']} entit{'ies' if p['entities'] != 1 else 'y'} to synthesize")
        detail = f" ({', '.join(bits)})" if bits else ""
        if not finalize:
            say(f"A previous batch is waiting to be finished{detail}; post-processing will finish "
                f"it together with this run.")
            if not new_docs:
                say("Nothing new to extract.")
                return None
        elif not new_docs:
            say(f"A previous batch is waiting to be finished{detail}; finishing it now.")
            return _run_finalize(vault, post_model, plan.post_effort, post_backend,
                                 skip_briefing=opts.skip_briefing, finalizer_overrides=overrides)
        else:
            say(f"A previous batch is waiting to be finished{detail}; it will be finished together "
                f"with the new documents.")

    from watchdog.model_client import BATCH_BACKENDS
    from watchdog.pipeline import batch_extract
    # A pending batch (#214, #530) must still be checked even with nothing newly queued.
    # force_lock so two concurrent runs can't both try to collect the same batch.
    batch_pending = extract_backend in BATCH_BACKENDS and batch_extract.read_state(vault) is not None

    result = is_run(vault, force_lock=batch_pending)
    if "error" in result:
        sys.exit(f"Error: {result['error']}")
    if result["total"] == 0 and not batch_pending:
        if count_failed(vault):
            say(f"{_quarantine_notice(count_failed(vault))} Nothing else is queued.")
        elif finalize:
            say("Nothing new to add. Choose files or folders in Add documents.")
        else:
            say("The queue is empty: pre-process some documents first.")
        return None

    only_shas = None
    if opts.limit is not None:
        from watchdog.pipeline.ingest_setup import limit_queue
        in_queue = len(result["queue_files"])
        result["queue_files"] = limit_queue(vault, result["queue_files"], opts.limit, force=opts.force)
        only_shas = [qf["sha256"] for qf in result["queue_files"]]
        held = in_queue - len(only_shas)
        say(f"Limit of {opts.limit}: extracting {len(only_shas)} of {in_queue} queued "
            f"document{'s' if in_queue != 1 else ''}"
            + (f"; {held} stay{'s' if held == 1 else ''} queued or already extracted" if held else "")
            + ".")
    # What this run will actually send to the model: queue files outlive extraction, so documents
    # already staged or committed are counted out unless `force` re-extracts them.
    to_send = result["queue_files"] if opts.force else needs_extraction(vault, result["queue_files"])
    q = len(to_send)
    if q:
        from watchdog.pipeline.ingest_setup import cost_estimate
        est = cost_estimate(vault, to_send, _effective_extract_backend(extract_backend, a["mode"]))
        say(_format_cost_estimate(est))
        say(_format_models_line(classify_backend, classify_model, extract_backend, extract_model,
                                post_backend, post_model, plan.classify_effort, plan.extract_effort,
                                plan.post_effort, overrides, concurrency=opts.concurrency,
                                is_dig=not finalize))
    elif batch_pending:
        say("Checking on a pending batch extraction…")

    pinned_skill = _resolve_pinned_skill(opts, config)
    if pinned_skill:
        say(f"Skill pinned: {Path(pinned_skill).stem}; classification skipped.")

    if (classify_backend in BATCH_BACKENDS or post_backend in BATCH_BACKENDS
            or any(overrides.get(f"{s}_backend") in BATCH_BACKENDS for s in FINALIZER_STAGES)):
        sys.exit(f"Error: a batch model ({', '.join(BATCH_BACKENDS)}) can only extract, not "
                 f"classify or post-process.")
    if extract_backend == "claude-batch" and a["mode"] != "api-key":
        # No pinned-skill requirement (D144): each document resolves its own skill before the
        # batch is built. claude-batch needs a metered key; openai-batch (#530) has no
        # subscription mode, so ordinary key resolution covers it.
        sys.exit("Error: Claude's batch extraction needs a Claude API key, not a subscription. "
                 "Switch to an API key in Settings.")
    # The verification pass (#535): the run's option beats the setting, the setting beats off.
    verify = bool(config.get("verify_extraction", False)) if opts.verify is None else opts.verify
    if verify and extract_backend in BATCH_BACKENDS:
        # The pass re-reads a document right after extracting it, out of the prompt cache that
        # call just wrote; a batch's results come back hours later with neither left.
        sys.exit(f"Error: verification isn't supported with {extract_backend}: it re-reads a "
                 f"document immediately after extracting it, and a batch's results come back "
                 f"hours later. Turn verification off, or choose another extraction model.")
    wait = opts.wait
    if finalize and extract_backend not in BATCH_BACKENDS:
        wait = True          # adding documents sees a run through: a rate limit pauses it
    if wait and extract_backend in BATCH_BACKENDS:
        sys.exit(f"Error: waiting for a rate limit isn't supported with {extract_backend}: a batch "
                 f"already runs in the background; add documents again later to collect it.")

    def _release_lock() -> None:
        from watchdog.pipeline.locks import release_lock
        lock = processing_lock(vault)
        release_lock(lock)
        if not lock.exists():       # another run took it over: its state file is its own
            processing_state(vault).unlink(missing_ok=True)

    auto = _auto_approve_on(config)
    gate = (_auto_approve_verdict(auth_mode=a["mode"], stages=plan.backends) if auto and q else None)
    if not _confirm_public_records(q, skip_warning=opts.skip_warning, gate=gate, enabled=auto):
        _release_lock()
        return None

    import asyncio
    log_path = vault / "log.md"
    if not log_path.exists():
        log_path.write_text(render_template("log.md"))

    if opts.force:
        say("Re-extracting even where a saved extraction or a committed note already exists.")
    if not finalize:
        say("Processing only, stopping after extraction. Finish the batch later under Activity → "
            "Maintenance → Post-processing.")
    if extract_backend in BATCH_BACKENDS:
        sync_backend = "claude-api" if extract_backend == "claude-batch" else "openai"
        say(f"{extract_backend}: sectioned documents (if any) extract via {sync_backend} now; the "
            f"rest submit as one batch and finish later.")
    else:
        say(f"Extracting (up to {concurrency} at a time). Large documents can take several minutes "
            f"each; a long pause on one is normal, not a stall. Use Stop in Activity to stop; "
            f"finished documents are kept.")
    lock_file = processing_lock(vault)
    from watchdog.pipeline.locks import heartbeat
    try:
        summary = None
        wait_count = 0
        # The loop's only exit condition is a rate limit hit during extraction, plus the
        # benchmark harness's bound on waits (#559), which leaves `rate_limited: True` in place
        # so the caller can tell the run stopped short.
        with _caffeinate(), heartbeat(lock_file):
            while True:
                iter_summary = asyncio.run(orchestrate.run(
                    vault, concurrency=concurrency, extract_model=extract_model, post_model=post_model,
                    classify_model=classify_model, classify_pages=classify_pages, pinned_skill=pinned_skill,
                    extract_effort=plan.extract_effort, post_effort=plan.post_effort,
                    classify_effort=plan.classify_effort, extract_backend=extract_backend,
                    post_backend=post_backend, classify_backend=classify_backend, wait=wait,
                    skip_finalize=not finalize, force=opts.force, skip_briefing=opts.skip_briefing,
                    finalizer_overrides=overrides, verify=verify, extract_token_budget=token_budget,
                    benchmark_arm_id=opts.benchmark_arm_id, only_shas=only_shas,
                    withhold_brief=opts.withhold_brief))
                summary = _merge_summary(summary, iter_summary)
                if not (wait and iter_summary.get("rate_limited")):
                    break
                if opts.max_rate_limit_waits is not None and wait_count >= opts.max_rate_limit_waits:
                    break
                wait_count += 1
                _wait_for_rate_limit(lock_file, iter_summary.get("rate_limit_resets_at"))
    except KeyboardInterrupt:
        # Fallback only: orchestrate.run traps SIGINT itself and returns a cancelled summary.
        # This catches a stop just before or after that window, on platforms where asyncio can't
        # install a SIGINT handler (Windows' Proactor loop), and a stop during post-processing,
        # which runs after that handler is torn down (#406).
        _release_lock()
        say("Stopped. Documents that finished before the stop are saved; the one in progress may "
            "be incomplete. Add documents again to resume.")
        if count_failed(vault):
            say(_quarantine_notice(count_failed(vault)))
        sys.exit(130)
    finally:
        _release_lock()
    # How many waits a partial benchmark arm took (#559); 0 on a run that never rate-limited.
    summary["wait_count"] = wait_count
    _print_summary(summary)
    return summary


def _print_summary(summary: dict) -> None:
    ext, skip, fail = summary["extracted"], summary["skipped"], summary["failed"]
    n_cancelled = sum(1 for r in summary["results"] if r.get("status") == "cancelled")
    if summary.get("auth_error"):
        headline = "Stopped: the provider refused the credentials or account."
    elif summary.get("rate_limited"):
        headline = "Paused by a rate limit."
    elif summary.get("cancelled"):
        headline = "Stopped."
    elif summary.get("batch_pending") and not summary["results"]:
        headline = "Batch extraction pending."
    else:
        headline = "Done."
    say(f"{headline} {ext} extracted"
        f"{f', {skip} skipped' if skip else ''}{f', {fail} failed' if fail else ''}"
        f"{f', {n_cancelled} not started' if n_cancelled else ''}.")
    if summary.get("rate_limited") and summary.get("stop_message"):
        say(summary["stop_message"])
    for r in summary["results"]:
        name = r.get("filename") or r.get("sha256", "?")
        if r["status"] == "ok":
            say(f"  ✓ {name}  {r.get('entity_count', 0)} entities")
        elif r["status"] == "skipped":
            say(f"  – {name}  already extracted")
        elif r["status"] != "cancelled":
            say(f"  ✗ {name}  {r.get('reason', '')}")
    if summary.get("quarantined"):
        say(_quarantine_notice(summary["quarantined"]))
    pi_error = summary.get("post_ingest_error") or (summary.get("post_ingest") or {}).get("error")
    if pi_error:
        say(f"Post-processing didn't finish: {pi_error}.")
        if (summary.get("post_ingest") or {}).get("commit_skipped"):
            # Reconciliation failed before the commit pass (#403): nothing was written yet.
            say("Nothing was written to the investigation yet. Run post-processing again "
                "(Activity → Maintenance → Post-processing) once the cause above is fixed "
                "(for a rate limit, once it resets).")
        else:
            say("Documents are saved with their extracted claims. Run post-processing "
                "(Activity → Maintenance → Post-processing) to complete the summaries and the "
                "briefing.")
    elif (summary.get("post_ingest") or {}).get("briefing_skipped"):
        say("Briefing skipped; entities summarized and the timeline rebuilt.")
    usage = summary.get("usage")
    if usage:
        cost = f" · ~${usage['cost_usd']:.4f}" if usage.get("cost_usd") else ""
        say(f"{ext} doc{'s' if ext != 1 else ''} · {usage['input_tokens']:,} in / "
            f"{usage['output_tokens']:,} out tokens{cost}")
    if summary.get("batch_pending"):
        say("A batch extraction is in flight. Add documents again later to check on it and "
            "collect the results.")
    elif summary.get("cancelled"):
        say("Add documents again to process the remaining ones.")
    elif summary.get("finalize_skipped"):
        say(f"Extraction staged ({ext} document{'s' if ext != 1 else ''}); finish it under "
            f"Activity → Maintenance → Post-processing.")


def finish(vault: Path, opts: RunOptions) -> dict | None:
    """Post-processing for an already-extracted batch: entity reconciliation, summaries, the
    timeline and the briefing. For a batch a rate limit or a stop left half-done, or one
    processing staged on its own."""
    from watchdog.pipeline import orchestrate
    if not orchestrate.has_pending_finalization(vault):
        say("Nothing to finish: process documents first.")
        return None
    plan = resolve_plan(opts)
    _require_auth(plan.finalizer_backends)
    _require_investigation_keys(vault, plan.finalizer_backends)
    return _run_finalize(vault, plan.post[1], plan.post_effort, plan.post[0],
                         skip_briefing=opts.skip_briefing, finalizer_overrides=plan.overrides,
                         benchmark_arm_id=opts.benchmark_arm_id)


def preprocess(vault: Path, paths: list[str] = (), *, chew_workers: int | None = None,
               chunk_workers: int | None = None) -> None:
    """Pre-processing: read the named files and folders (copying any from outside into
    `incoming/`), or everything in `incoming/`, on this computer. Never calls a model."""
    from watchdog.pipeline.preprocess_batch import find_files, run_ingest
    incoming = incoming_dir(vault)
    if paths:
        # A path already in incoming/ is read where it is; anything else is copied in.
        inc = incoming.resolve()
        inside, outside = [], []
        for raw in paths:
            p = Path(raw).expanduser().resolve()
            (inside if p == inc or inc in p.parents else outside).append(p)
        files: list[Path] = []
        for p in inside:
            if not p.exists():
                sys.exit(f"Error: {p} was not found.")
            files += find_files([p]) if p.is_dir() else [p]
        files += [_into_incoming(vault, f) for f in _expand_paths([str(p) for p in outside], vault)]
        if not files:
            say("No supported files to read.")
            return
        run_ingest(vault, workers=chew_workers, chunk_workers=chunk_workers, files=files)
        return
    if not incoming.is_dir():
        sys.exit("Error: this investigation has no incoming folder.")
    run_ingest(vault, workers=chew_workers, chunk_workers=chunk_workers)


def add_documents(vault: Path, paths: list[str] = (), opts: RunOptions | None = None, *,
                  retry: bool = False, chew_workers: int | None = None,
                  chunk_workers: int | None = None) -> dict | None:
    """Adding documents (D251): copy the named files in, pre-process, process and post-process
    in one run. With no paths it picks up whatever is waiting: files in `incoming/`, queued
    documents, a pending batch. `retry` first puts documents that failed processing back in the
    queue. It stops only for the public-records acknowledgement and for an auth or billing
    failure; a rate limit pauses the run until it resets."""
    opts = opts or RunOptions()
    _warn_pending_research(vault)
    if retry:
        n = _requeue_failed(vault)
        if n:
            say(f"Retrying {n} document{'s' if n != 1 else ''} that failed before.")
    files = _expand_paths(list(paths), vault)
    copied = sum(1 for f in files if _into_incoming(vault, f, quiet=True) != f)
    if copied:
        say(f"Copied {copied} file{'s' if copied != 1 else ''} into incoming/; the originals stay "
            f"where they are.")
    from watchdog.pipeline.preprocess_batch import find_files
    incoming = incoming_dir(vault)
    if incoming.is_dir() and find_files([incoming]):
        preprocess(vault, chew_workers=chew_workers, chunk_workers=chunk_workers)
    return process(vault, opts, finalize=True)


# ── operations (D298) ────────────────────────────────────────────────────────────────────

@op("chew", engine="add")
def chew(rep, vault: Path, *, paths: list[str] = (), chew_workers: int | None = None,
         chunk_workers: int | None = None) -> dict:
    """Pre-processing on its own. Never calls a model and never continues into processing."""
    require_vault(vault)
    _warn_pending_research(vault)
    before = count_queued(vault)
    preprocess(vault, list(paths), chew_workers=chew_workers, chunk_workers=chunk_workers)
    return {"queued": count_queued(vault) - before}


@op("add", engine="add", secrets=True, exit_code=lambda r: exit_code_for(r))
def add(rep, vault: Path, *, paths: list[str] = (), retry: bool = False, skip_warning: bool = False,
        extractor_model: str | None = None, classifier_model: str | None = None,
        finalizer_model: str | None = None, finalizer_reconciliation_model: str | None = None,
        finalizer_synthesis_model: str | None = None, finalizer_timeline_model: str | None = None,
        finalizer_briefing_model: str | None = None, extractor_effort: str | None = None,
        classifier_effort: str | None = None, finalizer_effort: str | None = None,
        concurrency: int | None = None, classify_pages: int | None = None, skill: str | None = None,
        verify: bool | None = None, wait: bool = False, skip_briefing: bool = False,
        chew_workers: int | None = None, chunk_workers: int | None = None) -> dict | None:
    """Adding documents. The app shows the public-records acknowledgement before it starts this
    and passes `skip_warning` once it has; without it nothing is sent."""
    require_vault(vault)
    opts = RunOptions(
        extractor_model=extractor_model, classifier_model=classifier_model,
        finalizer_model=finalizer_model, finalizer_reconciliation_model=finalizer_reconciliation_model,
        finalizer_synthesis_model=finalizer_synthesis_model,
        finalizer_timeline_model=finalizer_timeline_model,
        finalizer_briefing_model=finalizer_briefing_model, extractor_effort=extractor_effort,
        classifier_effort=classifier_effort, finalizer_effort=finalizer_effort,
        concurrency=concurrency, classify_pages=classify_pages, skill=skill, verify=verify,
        wait=wait, skip_briefing=skip_briefing, skip_warning=skip_warning)
    return _clean(add_documents(vault, list(paths), opts, retry=retry, chew_workers=chew_workers,
                                chunk_workers=chunk_workers))


@op("dig", engine="add", secrets=True, exit_code=lambda r: exit_code_for(r))
def dig(rep, vault: Path, *, skip_warning: bool = False, limit: int | None = None, force: bool = False,
        extractor_model: str | None = None, classifier_model: str | None = None,
        extractor_effort: str | None = None, classifier_effort: str | None = None,
        concurrency: int | None = None, classify_pages: int | None = None, skill: str | None = None,
        verify: bool | None = None, wait: bool = False) -> dict | None:
    """Processing only: extract the queue and stage the results; post-processing is left for
    `bark`."""
    require_vault(vault)
    opts = RunOptions(
        extractor_model=extractor_model, classifier_model=classifier_model,
        extractor_effort=extractor_effort, classifier_effort=classifier_effort,
        concurrency=concurrency, classify_pages=classify_pages, skill=skill, verify=verify,
        wait=wait, limit=limit, force=force, skip_warning=skip_warning)
    return _clean(process(vault, opts, finalize=False))


@op("bark", engine="add", secrets=True, exit_code=lambda r: exit_code_for(r))
def bark(rep, vault: Path, *, finalizer_model: str | None = None,
         finalizer_reconciliation_model: str | None = None, finalizer_synthesis_model: str | None = None,
         finalizer_timeline_model: str | None = None, finalizer_briefing_model: str | None = None,
         finalizer_effort: str | None = None, skip_briefing: bool = False) -> dict | None:
    """Post-processing: finish the staged batch."""
    require_vault(vault)
    opts = RunOptions(
        finalizer_model=finalizer_model, finalizer_reconciliation_model=finalizer_reconciliation_model,
        finalizer_synthesis_model=finalizer_synthesis_model,
        finalizer_timeline_model=finalizer_timeline_model,
        finalizer_briefing_model=finalizer_briefing_model, finalizer_effort=finalizer_effort,
        skip_briefing=skip_briefing)
    return _clean(finish(vault, opts))


@op("requeue", engine="add", kind="action")
def requeue(rep, vault: Path) -> dict:
    """Put documents that failed processing back in the queue."""
    require_vault(vault)
    n = _requeue_failed(vault)
    rep.log(requeue_message(n))
    return {"requeued": n}
