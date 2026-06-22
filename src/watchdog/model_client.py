"""ModelClient — an adapter over two Claude backends for the ingest pipeline's
reasoning calls (#118 Workstream 1).

Backends:
  - **claude-api** — the raw Messages API (`anthropic` SDK) with structured outputs.
    Lightweight; the workhorse for plain structured-reasoning calls. Needs a metered
    API key.
  - **claude-agent-sdk** — the full Claude Code agent loop. Heavier (per-call preamble)
    but the only backend that can use the **subscription** login, and the one to use
    when a step genuinely needs tools.

Routing: subscription auth can only use claude-agent-sdk; api-key auth defaults to
claude-api (cheaper) and may use either. A per-task policy or an explicit `backend=`
overrides the default. Every call validates the returned JSON against the schema,
retries on the same model on failure, and reports usage/cost/latency.

The model is invoked for *reasoning only* — callers pass a fully-formed prompt and a
schema and get validated structured output back. Deterministic work stays in Python.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass

from watchdog.cmd import auth

# Tier name → model id. The configured model is used as-is — no automatic escalation.
_MODEL_IDS = {
    "haiku":  "claude-haiku-4-5",
    "sonnet": "claude-sonnet-4-6",
    "opus":   "claude-opus-4-8",
}

# USD per token: (input, output, cache_write_5m, cache_read). Used to price claude-api
# usage (the Agent SDK reports its own cost). Update when pricing changes.
_PRICING = {
    "claude-opus-4-8":   (5e-6, 25e-6, 6.25e-6, 0.50e-6),
    "claude-sonnet-4-6": (3e-6, 15e-6, 3.75e-6, 0.30e-6),
    "claude-haiku-4-5":  (1e-6,  5e-6, 1.25e-6, 0.10e-6),
}

# Per-task overrides, populated as the pipeline's tasks are defined (Workstream 3).
_TASK_TIERS:    dict[str, str] = {}
_TASK_BACKENDS: dict[str, str] = {}
DEFAULT_TIER = "sonnet"
_API_MAX_TOKENS = 8000
# Extraction output is large; give it more room. Other tasks use the default.
_TASK_MAX_TOKENS = {"extract": 16000, "extract-section": 16000}

_SYSTEM_PROMPT = (
    "You are a precise extraction engine for an investigative-records pipeline. "
    "Respond with ONLY a single JSON object that conforms to the provided schema — "
    "no prose, no markdown fences, no explanation."
)


class ModelError(RuntimeError):
    """The model could not return schema-valid JSON, or the chosen backend can't run."""


class RateLimitError(RuntimeError):
    """A provider rate/usage limit was hit — e.g. the Claude subscription session limit.

    Deliberately **not** a :class:`ModelError`: a rate limit is a session-wide, transient
    condition, not a per-document failure. It must propagate past extraction's retry +
    sectioning fallback all the way to the orchestrator, which stops the batch cleanly and
    leaves unfinished documents queued for resume (rather than quarantining a good doc).
    """

    def __init__(self, message: str, *, resets_at=None):
        super().__init__(message)
        self.resets_at = resets_at


# Substrings that mark a rate/usage-limit error in a result, notice, or exception text.
_RATE_LIMIT_HINTS = ("rate_limit", "rate limit", "session limit", "usage limit",
                     "too many requests")


def _looks_like_rate_limit(api_status, *texts: str) -> bool:
    """True if an HTTP 429, or any of the given texts reads like a rate-limit notice."""
    if api_status == 429:
        return True
    blob = " ".join(t for t in texts if t).lower()
    return any(h in blob for h in _RATE_LIMIT_HINTS)


@dataclass
class ModelResult:
    parsed: dict
    text: str
    model: str
    backend: str
    auth_mode: str
    usage: dict | None = None
    cost_usd: float | None = None
    latency_s: float = 0.0
    attempts: int = 1


# ── JSON handling ─────────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict | None:
    """Pull a JSON object out of model text, tolerating ``` fences and trailing prose."""
    text = (text or "").strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start:i + 1])
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


def _validate(obj: dict, schema: dict) -> list[str]:
    """Return schema-validation error messages (empty list = valid)."""
    import jsonschema
    return [e.message for e in jsonschema.Draft202012Validator(schema).iter_errors(obj)]


# ── backends: each is (prompt, model_id, schema, api_key) -> {text, usage, cost_usd} ──

async def _agent_query(prompt: str, model: str, env: dict | None) -> dict:
    from claude_agent_sdk import query, ClaudeAgentOptions

    options = ClaudeAgentOptions(
        model=model,
        system_prompt=_SYSTEM_PROMPT,
        allowed_tools=[],       # reasoning only — no tools
        setting_sources=[],     # don't load .claude configs; trims the preamble
        max_turns=1,            # single completion, no agent loop
        env=env or {},
    )
    out = {"text": "", "cost_usd": None, "usage": None}
    api_status = None        # ResultMessage.api_error_status (e.g. 429) since CLI v2.1.110
    is_error = False
    notice = ""              # human-readable limit notice, if the CLI emitted one as text
    try:
        async for message in query(prompt=prompt, options=options):
            if type(message).__name__ == "ResultMessage":
                out["text"] = getattr(message, "result", "") or ""
                out["cost_usd"] = getattr(message, "total_cost_usd", None)
                out["usage"] = getattr(message, "usage", None)
                is_error = bool(getattr(message, "is_error", False))
                api_status = getattr(message, "api_error_status", None)
            # The session-limit message ("You've hit your session limit · resets …")
            # arrives as a text block; capture it so we can show the real reason.
            for block in getattr(message, "content", None) or []:
                t = getattr(block, "text", "") or ""
                if t and any(h in t.lower() for h in _RATE_LIMIT_HINTS):
                    notice = t.strip()
    except Exception as e:
        # The SDK rewrites a CLI is_error result into a RuntimeError on the non-zero exit
        # (often the only signal: "Claude Code returned an error result: success"). If it
        # was a rate limit, raise a typed, actionable error instead of an opaque one.
        if _looks_like_rate_limit(api_status, notice, out["text"], str(e)):
            raise RateLimitError(notice or "Claude rate/usage limit reached") from e
        raise
    if is_error and _looks_like_rate_limit(api_status, notice, out["text"]):
        raise RateLimitError(notice or "Claude rate/usage limit reached")
    return out


async def _agent_complete_async(prompt: str, model_id: str, schema: dict,
                                api_key: str | None, max_tokens: int | None = None) -> dict:
    """Claude Agent SDK backend. Works in either auth mode (key via env, or subscription).

    `max_tokens` is accepted for a uniform backend signature but unused — the agent's
    output is bounded by max_turns, not a token cap.
    """
    full = f"{prompt}\n\nReturn JSON matching this schema:\n{json.dumps(schema)}"
    env = {"ANTHROPIC_API_KEY": api_key} if api_key else None
    return await _agent_query(full, model_id, env)


def _api_cost(model_id: str, usage) -> float | None:
    rates = _PRICING.get(model_id)
    if not rates:
        return None
    inp, outp, cw, cr = rates
    g = lambda name: getattr(usage, name, 0) or 0
    return (g("input_tokens") * inp + g("output_tokens") * outp
            + g("cache_creation_input_tokens") * cw + g("cache_read_input_tokens") * cr)


async def _api_complete_async(prompt: str, model_id: str, schema: dict,
                              api_key: str | None, max_tokens: int) -> dict:
    """Raw Claude Messages API backend with structured outputs."""
    import anthropic

    try:
        resp = await anthropic.AsyncAnthropic(api_key=api_key).messages.create(
            model=model_id,
            max_tokens=max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
    except anthropic.RateLimitError as e:   # 429 — surface as the shared typed error
        raise RateLimitError(str(e) or "Claude API rate limit reached") from e
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
    usage = resp.usage
    usage_dict = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
    return {"text": text, "usage": usage_dict, "cost_usd": _api_cost(model_id, usage)}


_ABACKENDS = {"claude-api": _api_complete_async, "claude-agent-sdk": _agent_complete_async}


def _choose_backend(task: str, requested: str | None, auth_mode: str, has_key: bool) -> str:
    """Pick a backend, honoring an explicit request, the task policy, then auth mode."""
    backend = requested or _TASK_BACKENDS.get(task) or (
        "claude-agent-sdk" if auth_mode == "subscription" else "claude-api")
    if backend not in _ABACKENDS:
        raise ModelError(f"unknown backend '{backend}'")
    if backend == "claude-api" and not has_key:
        raise ModelError(
            "the claude-api backend needs an API key, but auth mode is "
            f"'{auth_mode}' — use `watchdog auth use api-key`, or the claude-agent-sdk backend")
    return backend


# ── public entry point ────────────────────────────────────────────────────────

async def acomplete_json(*, task: str, prompt: str, schema: dict, model: str | None = None,
                         backend: str | None = None, max_retries: int = 1) -> ModelResult:
    """Get schema-valid JSON for a reasoning task (async — the orchestrator awaits this).

    `model` may be a tier name (haiku/sonnet/opus) or a raw model id; omit it for the
    per-task default. `backend` forces 'claude-api' or 'claude-agent-sdk'; omit it to
    route by auth mode. On invalid/unparseable output the call retries on the **same**
    model (up to `max_retries` extra attempts) — never escalating — then raises.
    """
    resolved = auth.resolve_auth()
    if resolved["mode"] == "none":
        raise ModelError(resolved.get("reason", "no auth configured — run `watchdog setup`"))
    api_key = resolved.get("key")           # None in subscription mode
    auth_mode = resolved["mode"]

    chosen = _choose_backend(task, backend, auth_mode, has_key=bool(api_key))
    backend_fn = _ABACKENDS[chosen]
    max_tokens = _TASK_MAX_TOKENS.get(task, _API_MAX_TOKENS)

    requested = model or _TASK_TIERS.get(task, DEFAULT_TIER)
    model_id = _MODEL_IDS.get(requested, requested)   # tier name → id, or a raw id as-is

    start = time.monotonic()
    total_cost = 0.0
    last_err = "no attempts made"
    attempts = 0
    for _ in range(max_retries + 1):
        attempts += 1
        out = await backend_fn(prompt, model_id, schema, api_key, max_tokens)
        if out.get("cost_usd"):
            total_cost += out["cost_usd"]

        parsed = _extract_json(out["text"])
        if parsed is None:
            last_err = "response was not valid JSON"
        else:
            errors = _validate(parsed, schema)
            if not errors:
                return ModelResult(
                    parsed=parsed, text=out["text"], model=model_id, backend=chosen,
                    auth_mode=auth_mode, usage=out.get("usage"),
                    cost_usd=round(total_cost, 6) or None,
                    latency_s=round(time.monotonic() - start, 3), attempts=attempts,
                )
            last_err = "; ".join(errors[:3])

    raise ModelError(
        f"task '{task}' failed JSON validation after {attempts} attempt(s) "
        f"on {chosen}: {last_err}")


def complete_json(**kwargs) -> ModelResult:
    """Sync wrapper around :func:`acomplete_json` for non-async callers and tests."""
    return asyncio.run(acomplete_json(**kwargs))
