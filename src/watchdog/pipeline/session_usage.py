"""What an Ask Claude or Web research session costs, recorded where processing runs are (D296).

A session's turns end in the Agent SDK's result message, which carries the session's cost and
per-model tokens. Those are *cumulative* across a session's turns (the bundled Claude Code's own
schema: "each result carries the running total so far, so read the latest result rather than
summing across results"; a resumed session continues from its saved total, and `/clear` resets
it). `turn` turns two successive results into one turn's share; `record` appends that share to
the session's own usage file and to the telemetry store.

A session's file lives in `.watchdog/registry/usage/sessions/usage-<started>-<id>.json`, beside
the processing runs' files but not among them, so the processing cost estimates and "latest run"
never read a conversation's spend. It holds the same `calls`/`totals` shape, one call per turn,
with task `ask` or `research`, the session id and the label of the key that paid, never the key.
On a subscription the cost is recorded as the SDK reports it, with `auth_mode: subscription`, the
way subscription runs are: a list-price equivalent, shown as not billed.
"""

from __future__ import annotations

import datetime
import json
import os
import tempfile
import time
from pathlib import Path

TASKS = ("ask", "research")
LABELS = {"ask": "Ask Claude", "research": "Web research"}
BACKEND = "claude-agent-sdk"
# Where each credential the bundled Claude Code reports (`apiKeySource` in its init message)
# bills: a key Watchdog passed, or one Claude Code found, is metered; anything else is a login.
_KEY_SOURCES = ("ANTHROPIC_API_KEY", "apiKeyHelper", "/login managed key")
_TOKEN_KEYS = (("inputTokens", "input_tokens"), ("outputTokens", "output_tokens"),
               ("cacheReadInputTokens", "cache_read_tokens"),
               ("cacheCreationInputTokens", "cache_write_tokens"))


def task_for(mode: str) -> str:
    """The usage task a chat mode records as: `research` for Web research, `ask` otherwise
    (seeding `context.md` is an Ask Claude session too)."""
    return "research" if mode == "research" else "ask"


def auth_mode_from_source(api_key_source: str | None) -> str | None:
    if not api_key_source:
        return None
    return "api-key" if api_key_source in _KEY_SOURCES else "subscription"


def sessions_dir(vault: Path) -> Path:
    return Path(vault) / ".watchdog" / "registry" / "usage" / "sessions"


def files(vault: Path) -> list[Path]:
    """Every session usage file, oldest first."""
    d = sessions_dir(vault)
    return sorted(d.glob("usage-*.json"), key=lambda p: p.name) if d.is_dir() else []


# ── one turn's share of the running totals ──────────────────────────────────────────────────

def _models(model_usage) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name, u in (model_usage or {}).items() if isinstance(model_usage, dict) else []:
        if not isinstance(u, dict):
            continue
        row = {ours: int(u.get(theirs) or 0) for theirs, ours in _TOKEN_KEYS}
        row["cost_usd"] = float(u.get("costUSD") or 0.0)
        out[str(name)] = row
    return out


def snapshot(result) -> dict:
    """The running totals a result message carries: `{"cost_usd", "models": {name: tokens}}`."""
    return {"cost_usd": float(getattr(result, "total_cost_usd", None) or 0.0),
            "models": _models(getattr(result, "model_usage", None))}


def turn(previous: dict | None, result) -> tuple[dict, dict]:
    """`(this turn's share, the new running totals)`. `previous` is the last result's totals in
    this session (None for its first). When a total went down, the SDK started counting again
    (a `/clear`, or a resumed session that had no saved total), so the whole new total is this
    turn's."""
    now = snapshot(result)
    prev = previous if isinstance(previous, dict) else {"cost_usd": 0.0, "models": {}}
    pm = prev.get("models") or {}
    reset = now["cost_usd"] < float(prev.get("cost_usd") or 0.0) - 1e-9 or any(
        now["models"].get(m, {}).get(k, 0) < v
        for m, row in pm.items() for k, v in row.items() if k != "cost_usd")
    if reset:
        pm, base_cost = {}, 0.0
    else:
        base_cost = float(prev.get("cost_usd") or 0.0)
    models = {}
    for name, row in now["models"].items():
        before = pm.get(name, {})
        d = {k: row[k] - (before.get(k) or 0) for k in row}
        if any(d[k] > 0 for k in d if k != "cost_usd") or d["cost_usd"] > 0:
            models[name] = d
    share = {"cost_usd": max(0.0, now["cost_usd"] - base_cost), "models": models}
    return share, now


# ── recording ───────────────────────────────────────────────────────────────────────────────

def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _session_file(vault: Path, session_id: str) -> Path:
    tag = "".join(c for c in session_id if c.isalnum())[:12] or "session"
    existing = sorted(sessions_dir(vault).glob(f"usage-*-{tag}.json")) if sessions_dir(vault).is_dir() else []
    if existing:
        return existing[0]
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return sessions_dir(vault) / f"usage-{ts}-{tag}.json"


def call_record(share: dict, result, *, task: str, session_id: str, model: str | None,
                auth_mode: str | None, key_label: str | None) -> dict:
    """One turn as a usage call record, in the shape `orchestrate._record_usage` writes."""
    tokens = {k: sum(int(m.get(k) or 0) for m in share["models"].values())
              for _, k in _TOKEN_KEYS}
    usage = getattr(result, "usage", None)
    if not share["models"] and isinstance(usage, dict):
        # No per-model totals (an older Claude Code): the result's own usage, which is this
        # turn's main loop only, is the best count there is.
        tokens = {"input_tokens": int(usage.get("input_tokens") or 0),
                  "output_tokens": int(usage.get("output_tokens") or 0),
                  "cache_read_tokens": int(usage.get("cache_read_input_tokens") or 0),
                  "cache_write_tokens": int(usage.get("cache_creation_input_tokens") or 0)}
    models = sorted(share["models"], key=lambda m: -share["models"][m].get("cost_usd", 0.0))
    record = {
        "task": task, "model": models[0] if models else (model or "claude"), "backend": BACKEND,
        **tokens, "cost_usd": round(share["cost_usd"], 6), "attempts": 1,
        "latency_s": round((getattr(result, "duration_ms", 0) or 0) / 1000, 3),
        "effort": None, "auth_mode": auth_mode, "filename": None, "detail": None,
        "end_ts": time.time(), "session": session_id,
    }
    if len(models) > 1:
        record["models"] = models          # a turn that also used a helper model (compaction, a subagent)
    if getattr(result, "duration_api_ms", None) is not None:
        record["api_ms"] = result.duration_api_ms
    if getattr(result, "num_turns", None) is not None:
        record["num_turns"] = result.num_turns
    if getattr(result, "is_error", False):
        record["failed"] = True
    if key_label:
        record["key_label"] = key_label    # the label of the key that paid (D290), never the key
    return record


def record(vault: Path, *, session_id: str, mode: str, title: str, call: dict) -> Path:
    """Append one turn's record to the session's usage file and to the telemetry store. A
    telemetry failure is swallowed: recording must never break a conversation."""
    from watchdog.pipeline.orchestrate import _usage_totals
    path = _session_file(vault, session_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    calls = data.get("calls") if isinstance(data.get("calls"), list) else []
    calls.append(call)
    out = {"session": {"id": session_id, "mode": mode, "task": call["task"], "title": title},
           "calls": calls, "totals": _usage_totals(calls)}
    _write_atomic(path, json.dumps(out, ensure_ascii=False, indent=2))
    try:
        from watchdog import telemetry_db
        if telemetry_db.enabled():
            telemetry_db.record_call(call, vault=Path(vault), run_id=f"session-{session_id}",
                                     benchmark_arm_id=None, prompt_hash=None, config_snapshot=None)
    except Exception:  # noqa: BLE001
        pass
    return path


def session_info(path: Path) -> dict:
    """`{"id", "mode", "task", "title"}` from a session usage file (empty values when absent)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    info = data.get("session") if isinstance(data, dict) else None
    return info if isinstance(info, dict) else {}
