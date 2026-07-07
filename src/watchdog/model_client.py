"""ModelClient — an adapter over two Claude backends for the ingest pipeline's
reasoning calls (#118 Workstream 1).

Backends:
  - **claude-api** — the raw Messages API (`anthropic` SDK) with structured outputs.
    Lightweight; the workhorse for plain structured-reasoning calls. Needs a metered
    API key.
  - **claude-agent-sdk** — the full Claude Code agent loop. Heavier (per-call preamble)
    but the only backend that can use the **subscription** login, and the one to use
    when a step genuinely needs tools.

  - **openai / deepseek** — OpenAI-compatible Chat Completions backends (any service
    speaking that wire format, selected by base URL). Each uses its own provider API key
    (`watchdog auth set openai|deepseek`), independent of the Claude auth mode (#125).

Routing: subscription auth can only use claude-agent-sdk; api-key auth defaults to
claude-api (cheaper) and may use either. A per-task policy or an explicit `backend=`
overrides the default (and selects a non-Claude provider). Every call validates the
returned JSON against the schema, retries on the same model on failure, and reports
usage/cost/latency. The abstract `effort` knob is mapped to each provider's native
reasoning control by a per-provider policy, so providers differ without special-casing
the shared path.

The model is invoked for *reasoning only* — callers pass a fully-formed prompt and a
schema and get validated structured output back. Deterministic work stays in Python.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from functools import partial

from watchdog.cmd import auth

# Tier name → model id. The configured model is used as-is — no automatic escalation.
_MODEL_IDS = {
    "haiku":  "claude-haiku-4-5",
    "sonnet": "claude-sonnet-4-6",
    "opus":   "claude-opus-4-8",
}


def resolve_model_id(model: str) -> str:
    """Tier name (haiku/sonnet/opus) → API model id, or a raw id returned as-is."""
    return _MODEL_IDS.get(model, model)

# USD per token: (input, output, cache_write_5m, cache_read). Used to price claude-api
# usage (the Agent SDK reports its own cost). Update when pricing changes.
_PRICING = {
    "claude-opus-4-8":   (5e-6, 25e-6, 6.25e-6, 0.50e-6),
    "claude-sonnet-4-6": (3e-6, 15e-6, 3.75e-6, 0.30e-6),
    "claude-haiku-4-5":  (1e-6,  5e-6, 1.25e-6, 0.10e-6),
}

DEFAULT_TIER = "sonnet"
_API_MAX_TOKENS = 8000
# Extraction output is large; give it more room. Other tasks use the default. The briefing's
# arrays (what_was_ingested/connections/leads/anomalies/emerging_patterns/open_questions) scale
# with batch size, so it gets the same higher ceiling as extraction — a truncated briefing is a
# JSON parse failure, not a partial result (#296).
_TASK_MAX_TOKENS = {"extract": 16000, "extract-section": 16000, "briefing": 16000}

_SYSTEM_PROMPT = (
    "You are a precise extraction engine for an investigative-records pipeline. "
    "Respond with ONLY a single JSON object that conforms to the provided schema — "
    "no prose, no markdown fences, no explanation."
)

# Reasoning-effort levels for the per-stage `effort` knob (D36) — an abstract intent the
# pipeline passes down. Each provider maps it to its own native control or ignores it; the
# shared call path stays provider-agnostic so new backends (#125, D37) slot in without touching it.
_EFFORT_LEVELS = ("low", "medium", "high")
# Claude models that reject `output_config.effort` with a 400 (Haiku-tier).
_EFFORT_UNSUPPORTED = {"claude-haiku-4-5"}
# OpenAI-compatible models that accept `reasoning_effort` (substring match on the id). A chat
# model 400s on it, so the knob is dropped for anything not matching.
_OPENAI_REASONING_MARKERS = ("reasoner", "gpt-5", "o1", "o3", "o4")


def _claude_effort(model_id: str, effort: str) -> str | None:
    """Claude: `high` ≡ the model default (omit it), and Haiku-tier rejects effort entirely."""
    if effort == "high" or model_id in _EFFORT_UNSUPPORTED:
        return None
    return effort


def _openai_effort(model_id: str, effort: str) -> str | None:
    """OpenAI-compatible: `reasoning_effort` is accepted only by reasoning models — drop it
    elsewhere. Unlike Claude, `high` is not the default, so it is passed through."""
    mid = model_id.lower()
    return effort if any(m in mid for m in _OPENAI_REASONING_MARKERS) else None


def _no_effort(model_id: str, effort: str) -> str | None:
    """DeepSeek exposes thinking per-model (the reasoner thinks by default), not via a portable
    knob — so the abstract effort intent maps to nothing here."""
    return None


# provider → the function mapping the abstract effort intent to that provider's native value.
_EFFORT_POLICY = {
    "anthropic": _claude_effort,
    "openai":    _openai_effort,
    "deepseek":  _no_effort,
}


def _resolve_effort(provider: str, model_id: str, effort: str | None) -> str | None:
    """Translate the abstract effort intent into the provider's native value, or None to omit.

    Each provider's policy owns its own semantics — which models accept a knob, and whether a
    level is a no-op default — so the shared call path never hard-codes one provider (#125)."""
    if not effort:
        return None
    policy = _EFFORT_POLICY.get(provider)
    return policy(model_id, effort) if policy else None


# Model context windows in tokens, for provider-aware sectioning (#321): the larger a model's
# window, the more of a document it can read in one extraction call before sectioning pays off.
# Keyed by a substring of the resolved model id, **most specific first** (dict order is honoured),
# so `deepseek-v4` wins over the legacy `deepseek` fallback. Anything unmatched gets a
# conservative default. These are the vendors' published windows, not Watchdog's per-call budget —
# the sectioning policy reserves headroom from them (see `pipeline/section.py`).
#
# Windows are per-model, so add a more specific row above a broader one whenever a model diverges.
# The `claude` row is a shared fallback only because every Claude tier Watchdog resolves today
# (Haiku 4.5, Sonnet 4.6, Opus 4.8) has the same 200K *usable* window — Sonnet's 1M is beta-gated
# behind a request header `_api_complete_async` does not send, so 200K is the correct figure, not
# just a conservative one. A future tier whose standard window differs (e.g. a Sonnet that ships
# 1M by default) gets its own `claude-sonnet-N` row above this fallback.
_CONTEXT_WINDOWS = {
    "deepseek-v4": 1_000_000,   # DeepSeek V4 flash/pro
    "deepseek":      128_000,   # legacy deepseek-chat/reasoner
    "gpt-5":         400_000,
    "gpt-4":         128_000,
    "o1":            200_000,
    "o3":            200_000,
    "o4":            200_000,
    "claude":        200_000,   # shared fallback: Haiku 4.5 / Sonnet 4.6 / Opus 4.8 usable window
}
_DEFAULT_CONTEXT_WINDOW = 128_000


def context_window(model: str | None) -> int:
    """Token context window for a stage's model, for provider-aware sectioning (#321).

    `model` may be a tier name (haiku/sonnet/opus), a raw provider id (`deepseek-v4-flash`,
    `gpt-5-mini`), or None for the default tier — resolved first, then matched against the
    substring table. Unlisted ids fall back to a conservative default rather than raising, so a
    new or misspelled id degrades to safe (small-chunk) sectioning instead of an overrun."""
    model_id = resolve_model_id(model or DEFAULT_TIER).lower()
    for marker, window in _CONTEXT_WINDOWS.items():
        if marker in model_id:
            return window
    return _DEFAULT_CONTEXT_WINDOW


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

# `prompt` is normally a plain string, but a caller that wants prompt caching (A1) — currently
# just the extraction/section builders — can pass a list of Anthropic content blocks instead,
# with `cache_control` on the block marking the end of the cacheable prefix. Only `_api_complete_async`
# understands blocks natively (the Messages API accepts either shape for `content`); every other
# backend needs a plain string, so it flattens first.
def _flatten_prompt(prompt: str | list[dict]) -> str:
    """Join a content-block prompt back into plain text for backends that don't support
    structured content (`claude-agent-sdk`, OpenAI-compatible). A no-op for a plain string."""
    if isinstance(prompt, str):
        return prompt
    return "\n".join(b.get("text", "") for b in prompt)


async def _agent_query(prompt: str, model: str, env: dict | None,
                       effort: str | None = None) -> dict:
    from claude_agent_sdk import query, ClaudeAgentOptions

    opts = dict(
        model=model,
        system_prompt=_SYSTEM_PROMPT,
        allowed_tools=[],       # reasoning only — no tools
        setting_sources=[],     # don't load .claude configs; trims the preamble
        max_turns=1,            # single completion, no agent loop
        env=env or {},
    )
    if effort:                  # only set when overriding the model default (D36)
        opts["effort"] = effort
    options = ClaudeAgentOptions(**opts)
    out = {"text": "", "cost_usd": None, "usage": None}
    api_status = None        # ResultMessage.api_error_status (e.g. 429) since CLI v2.1.110
    is_error = False
    notice = ""              # human-readable limit notice, if the CLI emitted one as text
    rejected = False         # the CLI emitted a rate-limit event with status "rejected"
    resets_at = None
    try:
        async for message in query(prompt=prompt, options=options):
            name = type(message).__name__
            if name == "ResultMessage":
                out["text"] = getattr(message, "result", "") or ""
                out["cost_usd"] = getattr(message, "total_cost_usd", None)
                out["usage"] = getattr(message, "usage", None)
                is_error = bool(getattr(message, "is_error", False))
                api_status = getattr(message, "api_error_status", None)
            elif name in ("RateLimitEvent", "RateLimitInfo"):
                info = getattr(message, "rate_limit_info", message)
                if getattr(info, "status", None) == "rejected":
                    rejected = True
                    resets_at = getattr(info, "resets_at", None)
            # The session-limit message ("You've hit your session limit · resets …")
            # arrives as a text block; capture it so we can show the real reason.
            for block in getattr(message, "content", None) or []:
                t = getattr(block, "text", "") or ""
                if t and any(h in t.lower() for h in _RATE_LIMIT_HINTS):
                    notice = t.strip()
    except Exception as e:
        # The SDK raises on a CLI error result via two paths — a ProcessError rewritten to
        # "Claude Code returned an error result: …", or a {type:error} stream message. If it
        # was a rate limit, raise a typed, actionable error instead of an opaque one.
        if rejected or _looks_like_rate_limit(api_status, notice, out["text"], str(e)):
            raise RateLimitError(notice or "Claude rate/usage limit reached", resets_at=resets_at) from e
        raise
    if rejected or (is_error and _looks_like_rate_limit(api_status, notice, out["text"])):
        raise RateLimitError(notice or "Claude rate/usage limit reached", resets_at=resets_at)
    return out


async def _agent_complete_async(prompt: str | list[dict], model_id: str, schema: dict,
                                api_key: str | None, max_tokens: int | None = None,
                                effort: str | None = None) -> dict:
    """Claude Agent SDK backend. Works in either auth mode (key via env, or subscription).

    `max_tokens` is accepted for a uniform backend signature but unused — the agent's
    output is bounded by max_turns, not a token cap. The agent SDK has no `cache_control`
    knob (A1), so a content-block prompt is flattened to plain text here.
    """
    full = f"{_flatten_prompt(prompt)}\n\nReturn JSON matching this schema:\n{json.dumps(schema)}"
    env = {"ANTHROPIC_API_KEY": api_key} if api_key else None
    return await _agent_query(full, model_id, env, effort)


def _api_cost(model_id: str, usage) -> float | None:
    rates = _PRICING.get(model_id)
    if not rates:
        return None
    inp, outp, cw, cr = rates
    g = lambda name: getattr(usage, name, 0) or 0
    return (g("input_tokens") * inp + g("output_tokens") * outp
            + g("cache_creation_input_tokens") * cw + g("cache_read_input_tokens") * cr)


def _batch_cost(model_id: str, usage) -> float | None:
    """Message Batches pricing is a flat 50% off every standard per-token rate, including
    cache read/write (#214) — so batch cost is just the normal API cost at half price."""
    cost = _api_cost(model_id, usage)
    return cost * 0.5 if cost is not None else None


async def _api_complete_async(prompt: str | list[dict], model_id: str, schema: dict,
                              api_key: str | None, max_tokens: int,
                              effort: str | None = None) -> dict:
    """Raw Claude Messages API backend with structured outputs.

    `prompt` may be a plain string or a list of Anthropic content blocks with a
    `cache_control` breakpoint (A1) — the Messages API's `content` field accepts either
    shape natively, so no conversion is needed here."""
    import anthropic

    # `effort` composes with the structured-output `format` inside the one output_config dict.
    output_config = {"format": {"type": "json_schema", "schema": schema}}
    if effort:
        output_config["effort"] = effort
    try:
        resp = await anthropic.AsyncAnthropic(api_key=api_key).messages.create(
            model=model_id,
            max_tokens=max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_config=output_config,
        )
    except anthropic.RateLimitError as e:   # 429 — surface as the shared typed error
        raise RateLimitError(str(e) or "Claude API rate limit reached") from e
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
    usage = resp.usage
    usage_dict = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
    return {"text": text, "usage": usage_dict, "cost_usd": _api_cost(model_id, usage)}


# OpenAI-compatible (Chat Completions) pricing: model id → (input $/tok, output $/tok).
# DeepSeek figures are the cache-miss input rate; cache-hit input discounts are not modelled yet
# (a v1 simplification), so cost is a slight over-estimate when prompts hit cache. Verify against
# the provider before relying on absolute figures: https://api-docs.deepseek.com/quick_start/pricing
_OPENAI_PRICING = {
    "deepseek-v4-flash": (0.14e-6,  0.28e-6),
    "deepseek-v4-pro":   (0.435e-6, 0.87e-6),
}


def _openai_cost(model_id: str, usage: dict | None) -> float | None:
    rates = _OPENAI_PRICING.get(model_id)
    if not rates or not usage:
        return None
    inp, outp = rates
    return ((usage.get("prompt_tokens", 0) or 0) * inp
            + (usage.get("completion_tokens", 0) or 0) * outp)


# DeepSeek V4 collapsed thinking/non-thinking into a single model id switched by a request param
# (the old deepseek-chat/deepseek-reasoner split is deprecated). Watchdog keeps the choice inside
# the model token — `deepseek-v4-flash` (non-thinking) vs `deepseek-v4-flash-thinking` — so it rides
# the existing `[backend:]model` grammar with no extra provider-specific knob. The backend strips
# this marker, uses the bare id for the request + cost lookup, and sends DeepSeek's explicit toggle.
# The provider default is thinking-ENABLED, so we always send the toggle to pin the intended mode.
# Toggle shape (OpenAI format): {"thinking": {"type": "enabled"|"disabled"}}. Docs:
# https://api-docs.deepseek.com/guides/thinking_mode  (D88)
_DEEPSEEK_THINKING_SUFFIX = "-thinking"


def _split_deepseek_thinking(model_id: str) -> tuple[str, bool]:
    """(bare model id, thinking?) from a DeepSeek model token — strips a `-thinking` marker.
    Non-thinking is the default (bare id), so extraction stays cheap unless thinking is opted in."""
    if model_id.endswith(_DEEPSEEK_THINKING_SUFFIX):
        return model_id[: -len(_DEEPSEEK_THINKING_SUFFIX)], True
    return model_id, False


async def _openai_complete_async(prompt: str | list[dict], model_id: str, schema: dict,
                                 api_key: str | None, max_tokens: int,
                                 effort: str | None = None, *, base_url: str) -> dict:
    """OpenAI-compatible Chat Completions backend — OpenAI, DeepSeek, and any service that
    speaks the same wire format (selected by `base_url`).

    Structured output is requested via JSON mode plus the schema appended to the prompt, then
    validated by the shared `acomplete_json` shell — the portable path across providers, since
    full `json_schema` mode is not universal. `effort` arrives already resolved to the
    provider's native value (or None) and is sent as `reasoning_effort` (#125). No provider-
    agnostic cache_control equivalent is wired here (A1 is Claude-only), so a content-block
    prompt is flattened to plain text.

    DeepSeek carries an optional `-thinking` marker on the model id; it is stripped here and
    translated into DeepSeek's explicit thinking toggle (default off — see #320)."""
    import httpx

    is_deepseek = "deepseek" in base_url
    thinking: bool | None = None
    if is_deepseek:
        model_id, thinking = _split_deepseek_thinking(model_id)

    body = {
        "model": model_id,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},   # "JSON" must appear in the prompt below
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",
             "content": f"{_flatten_prompt(prompt)}\n\nReturn JSON matching this schema:\n{json.dumps(schema)}"},
        ],
    }
    if effort:
        body["reasoning_effort"] = effort
    if thinking is not None:   # DeepSeek: pin the mode explicitly (provider defaults to enabled)
        body["thinking"] = {"type": "enabled" if thinking else "disabled"}
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=600) as client:
        resp = await client.post(url, headers=headers, json=body)
    if resp.status_code == 429:
        raise RateLimitError(f"{base_url} rate limit reached")
    resp.raise_for_status()
    data = resp.json()
    choices = data.get("choices") or []
    text = (choices[0].get("message", {}).get("content") or "") if choices else ""
    usage = data.get("usage")
    return {"text": text, "usage": usage, "cost_usd": _openai_cost(model_id, usage)}


# OpenAI-compatible base URLs (the `/chat/completions` path is appended per request).
_OPENAI_BASE = {
    "openai":   "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com",
}

_ABACKENDS = {
    "claude-api":       _api_complete_async,
    "claude-agent-sdk": _agent_complete_async,
    "openai":   partial(_openai_complete_async, base_url=_OPENAI_BASE["openai"]),
    "deepseek": partial(_openai_complete_async, base_url=_OPENAI_BASE["deepseek"]),
}

# backend name → the auth provider whose key it uses (and whose effort policy applies).
_BACKEND_PROVIDER = {
    "claude-api":       "anthropic",
    "claude-agent-sdk": "anthropic",
    "claude-batch":     "anthropic",
    "openai":           "openai",
    "deepseek":         "deepseek",
}

# Selectable backend names (public — the CLI validates a stage's `backend:model` against this).
BACKENDS = tuple(_BACKEND_PROVIDER)
# Backends that take a Claude tier name (haiku/sonnet/opus); the rest take a raw provider id.
CLAUDE_BACKENDS = ("claude-api", "claude-agent-sdk", "claude-batch")

# claude-batch is a real, selectable backend (CLI validation, auth resolution) but is never
# dispatched through the single-call acomplete_json path — the Message Batches API is
# submit-many/poll/collect, handled entirely by orchestrate._run_batch + pipeline.batch_extract
# (#214). Deliberately excluded from _ABACKENDS; this set exists only to turn a misrouted call
# (e.g. a classifier/finalizer stage accidentally set to claude-batch) into a clear error instead
# of the generic "unknown backend" one.
_BATCH_ONLY_BACKENDS = {"claude-batch"}


def _resolve_backend_auth(requested: str | None) -> tuple[str, str, str | None, str]:
    """Resolve (backend, provider, api_key, auth_mode) for a call.

    Claude backends consult the subscription/api-key mode (`auth.resolve_auth`); other providers
    use their stored API key directly (`auth.get_api_key`), independent of the Claude mode — so a
    user with only an OpenAI/DeepSeek key can run those backends without configuring Claude (#125).
    With no explicit backend, defaults among the Claude backends by auth mode (unchanged)."""
    chosen = requested
    if chosen in _BATCH_ONLY_BACKENDS:
        raise ModelError(f"'{chosen}' is a batch-mode-only backend — it cannot be used for a "
                         "single-call task; it's only valid as extractor_model (#214)")
    if chosen is not None and chosen not in _ABACKENDS:
        raise ModelError(f"unknown backend '{chosen}'")
    provider = _BACKEND_PROVIDER[chosen] if chosen else "anthropic"

    if provider == "anthropic":
        resolved = auth.resolve_auth()
        if resolved["mode"] == "none":
            raise ModelError(resolved.get("reason", "no auth configured — run `watchdog setup`"))
        auth_mode = resolved["mode"]
        api_key = resolved.get("key")           # None in subscription mode
        if chosen is None:
            chosen = "claude-agent-sdk" if auth_mode == "subscription" else "claude-api"
        if chosen == "claude-api" and not api_key:
            raise ModelError(
                "the claude-api backend needs an API key, but auth mode is "
                f"'{auth_mode}' — use `watchdog auth use api-key`, or the claude-agent-sdk backend")
        return chosen, provider, api_key, auth_mode

    api_key = auth.get_api_key(provider)
    if not api_key:
        raise ModelError(f"the {chosen} backend needs an API key — run `watchdog auth set {provider}`")
    return chosen, provider, api_key, "api-key"


# ── public entry point ────────────────────────────────────────────────────────

async def acomplete_json(*, task: str, prompt: str | list[dict], schema: dict, model: str | None = None,
                         backend: str | None = None, max_retries: int = 1,
                         effort: str | None = None) -> ModelResult:
    """Get schema-valid JSON for a reasoning task (async — the orchestrator awaits this).

    `prompt` is normally a string; a caller may instead pass a list of Anthropic content
    blocks with a `cache_control` breakpoint (A1) to make part of the prompt cache-eligible —
    only `claude-api` uses it natively, other backends flatten it to text (`_flatten_prompt`).
    `model` may be a tier name (haiku/sonnet/opus) or a raw model id; omit it for the
    per-task default. `backend` forces a backend ('claude-api', 'claude-agent-sdk',
    'openai', 'deepseek'); omit it to route by auth mode. `effort` (`low`/`medium`/`high`)
    is an abstract reasoning-depth intent — each provider maps it to its own native control
    or ignores it (D36, #125). On invalid/unparseable output the call retries on the **same**
    model (up to `max_retries` extra attempts) — never escalating — then raises.
    """
    chosen, provider, api_key, auth_mode = _resolve_backend_auth(backend)
    backend_fn = _ABACKENDS[chosen]
    max_tokens = _TASK_MAX_TOKENS.get(task, _API_MAX_TOKENS)

    requested = model or DEFAULT_TIER
    model_id = resolve_model_id(requested)
    effort_arg = _resolve_effort(provider, model_id, effort)   # provider-native value or None

    start = time.monotonic()
    total_cost = 0.0
    last_err = "no attempts made"
    attempts = 0
    for _ in range(max_retries + 1):
        attempts += 1
        try:
            out = await backend_fn(prompt, model_id, schema, api_key, max_tokens, effort_arg)
        except (RateLimitError, ModelError):
            raise
        except Exception as e:                  # any backend/transport failure → typed error
            raise ModelError(f"{chosen} backend error: {e}") from e
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
