"""ModelClient — an adapter over two Claude backends for the ingest pipeline's
reasoning calls (#118 Workstream 1).

Backends:
  - **claude-api** — the raw Messages API (`anthropic` SDK) with structured outputs.
    Lightweight; the workhorse for plain structured-reasoning calls. Needs a metered
    API key.
  - **claude-agent-sdk** — the full Claude Code agent loop. Heavier (per-call preamble)
    but the only backend that can use the **subscription** login, and the one to use
    when a step genuinely needs tools.

  - **openai / deepseek / gemini** — OpenAI-compatible Chat Completions backends (any service
    speaking that wire format, selected by base URL). Each uses its own provider API key
    (stored via `watchdog auth`), independent of the Claude auth mode (#125).

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
from functools import lru_cache, partial

from watchdog import fixture_capture
from watchdog.cmd import auth
from watchdog.model_catalog import (
    _MODEL_IDS,  # noqa: F401 — re-exported for cmd/setup.py's interactive picker
    _OPENAI_PRICING,
    _PRICING,
    catalog_context_window,
    catalog_effort_levels,
    catalog_is_reasoning,
    fallback_context_window,
    fallback_is_reasoning,
    resolve_model_id,
)

DEFAULT_TIER = "sonnet"
_API_MAX_TOKENS = 8000
# Extraction output is large; give it more room. Other tasks use the default. The briefing's
# arrays (what_was_ingested/connections/leads/anomalies/emerging_patterns/open_questions) scale
# with batch size, so it gets the same higher ceiling as extraction — a truncated briefing is a
# JSON parse failure, not a partial result (#296).
# `verify` (#535) keeps the plain 8000 base rather than extraction's raised one: the verification
# pass emits only the facts an extraction missed, so its *answer* is short by construction and a
# bigger visible-output budget would buy nothing. It is listed here at all — rather than falling
# through to the same 8000 via `.get`'s default — because membership in this dict is what gates
# the reasoning reserve (#337/#354/#541, D168/D171): the pass runs at low effort precisely to hold
# reasoning tokens down, but "low" is not "none", and a CoT sharing 8000 tokens with the answer is
# exactly how that JSON gets truncated. Since the reserve is added to the base and scales with
# effort, `verify` on a reasoning model resolves to 8000 + the low-effort reserve — a short answer
# with room to think, not extraction's headroom for a long one.
_TASK_MAX_TOKENS = {"extract": 16000, "extract-section": 16000, "briefing": 16000,
                    "verify": _API_MAX_TOKENS}

_SYSTEM_PROMPT = (
    "You are a precise extraction engine for an investigative-records pipeline. "
    "Respond with ONLY a single JSON object that conforms to the provided schema — "
    "no prose, no markdown fences, no explanation."
)

# Reasoning-effort levels for the per-stage `effort` knob (D36) — an abstract intent the
# pipeline passes down. `model_catalog.yaml`'s `effort_levels` is the single source of truth for
# which of these a given model actually accepts (#518, D158) — real per-model coverage, not a
# per-provider flag: it varies within a provider (Claude Sonnet 4.6 takes `max` but not `xhigh`)
# as much as across providers. `_resolve_effort` below rejects an unsupported request loudly
# rather than silently dropping it or sending a request the API would reject.
_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
# OpenAI-provider model capabilities (#170). A *reasoning* model accepts `reasoning_effort` and
# requires the newer `max_completion_tokens` field — it 400s on `max_tokens`; a *chat* model is the
# exact reverse. Resolved from the model catalog (model_catalog.yaml's `reasoning` field, or the
# `reasoning_fallback` prefix table for an uncatalogued id) so it can't drift apart from pricing.
# An id matching nothing is treated as a chat model — the correctness-safe default that never sends
# an unsupported param. (DeepSeek is not consulted here: its thinking is a separate per-request
# toggle, and it uses the classic `max_tokens` field — see `_openai_complete_async`.)
def _openai_is_reasoning(model_id: str) -> bool:
    """Whether an OpenAI model is a reasoning model — accepts `reasoning_effort` and needs
    `max_completion_tokens` (rejecting `max_tokens`). Chat models are the reverse."""
    known = catalog_is_reasoning(model_id)
    return known if known is not None else fallback_is_reasoning(model_id)


def _effort_levels(provider: str, model_id: str) -> set[str]:
    """Which effort levels `provider`/`model_id` actually accepts. The catalog is authoritative
    for a known id; an uncatalogued id (a raw id typed past the CLI's tier validation, or a
    local/OpenRouter model with no catalog entry at all, #380) falls back to a conservative,
    provider-wide default rather than guessing per-model capability we don't have."""
    known = catalog_effort_levels(model_id)
    if known is not None:
        return known
    if provider == "openai":
        # low/medium/high only — xhigh/max aren't assumed for a reasoning model we don't
        # otherwise recognize, since only the catalogued GPT-5.6 family is confirmed for them.
        return {"low", "medium", "high"} if _openai_is_reasoning(model_id) else set()
    if provider == "gemini":
        return {"low", "medium", "high"}   # every current Gemini model, catalogued or not
    return set()   # anthropic (unreachable uncatalogued via the CLI), deepseek, local, openrouter


def _resolve_effort(provider: str, model_id: str, effort: str | None) -> str | None:
    """Translate the abstract effort intent into the provider's native value, or None to omit.

    `model_catalog.yaml`'s `effort_levels` (via `_effort_levels` above) is the single source of
    truth for whether `provider`/`model_id` accepts `effort` at all (#518, D158) — a request for
    an unsupported level fails loud with a clear error rather than being silently dropped or sent
    as a request the API would reject."""
    if not effort:
        return None
    if effort not in _effort_levels(provider, model_id):
        raise ModelError(f"effort '{effort}' is not supported for '{model_id}' on provider '{provider}'")
    if provider == "anthropic" and effort == "high":
        return None   # ≡ the model's own default (D36) — omit the param rather than send it
    return effort


def effort_supported(backend: str | None, model: str | None, effort: str) -> bool:
    """Whether `effort` is a level `backend`/`model` actually accepts (#518) — for a caller that
    wants to check support *before* calling, rather than let `acomplete_json` raise. Exists for
    exactly one case: `cmd/ingest.py` applies `medium` as `extractor_effort`'s implicit default
    (D26) only when the resolved extractor model supports it, so routing extraction to a model
    with no effort control (e.g. Haiku) doesn't turn a setting the user never touched into a hard
    failure — an *explicit* `--extractor-effort`/`finalizer_effort` request still goes straight to
    `_resolve_effort` and fails loud exactly as before if unsupported."""
    provider = provider_for_backend(backend)
    model_id = resolve_model_id(model or DEFAULT_TIER)
    return effort in _effort_levels(provider, model_id)


# Model context windows in tokens, for provider-aware sectioning (#321): the larger a model's
# window, the more of a document it can read in one extraction call before sectioning pays off.
# Resolved from the model catalog (model_catalog.yaml's `context_window` field per model, or the
# `context_window_fallback` substring table for an uncatalogued id). These are the vendors'
# published windows, not Watchdog's per-call budget — the sectioning policy reserves headroom
# from them (see `pipeline/section.py`). Anything unmatched gets the conservative default below.
_DEFAULT_CONTEXT_WINDOW = 128_000

# A self-hosted model's id (#380) is whatever the operator named it in their runner — it carries
# no vendor namespace to match against the catalog/fallback, and guessing from `_DEFAULT_CONTEXT_WINDOW`
# would be optimistic for the small/quantized models local runners typically serve. `local_context_window`
# (a `watchdog configure` key) lets an operator state their model's real window; absent that, this
# conservative default keeps sectioning aggressive rather than risking an overrun on an unknown model.
_LOCAL_DEFAULT_CONTEXT_WINDOW = 8_000


def _configured_local_context_window() -> int | None:
    config = {}
    try:
        from watchdog.cmd.base import CONFIG_FILE
        if CONFIG_FILE.exists():
            config = json.loads(CONFIG_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        config = {}
    value = config.get("local_context_window")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def context_window(model: str | None, backend: str | None = None) -> int:
    """Token context window for a stage's model, for provider-aware sectioning (#321).

    `model` may be a tier name (haiku/sonnet/opus), a raw provider id (`deepseek-v4-flash`,
    `gpt-5-mini`), or None for the default tier — resolved first, then looked up in the model
    catalog, then the substring fallback table. Unlisted ids fall back to a conservative default
    rather than raising, so a new or misspelled id degrades to safe (small-chunk) sectioning
    instead of an overrun.

    `backend == "local"` (#380) skips the catalog/fallback entirely — a self-hosted model's id has
    no relation to the vendor ids they're keyed on — in favour of the `local_context_window`
    config override, or a small conservative default when unset."""
    if backend == "local":
        return _configured_local_context_window() or _LOCAL_DEFAULT_CONTEXT_WINDOW
    model_id = resolve_model_id(model or DEFAULT_TIER)
    known = catalog_context_window(model_id)
    if known is not None:
        return known
    return fallback_context_window(model_id) or _DEFAULT_CONTEXT_WINDOW


class ModelError(RuntimeError):
    """The model could not return schema-valid JSON, or the chosen backend can't run.

    `usage`/`cost_usd`/`attempts`/`model`/`backend`/`auth_mode` (D125) are set only when the
    failure happened after at least one attempt actually called the model — the JSON-validation-
    failure path and the truncation path in `acomplete_json` — so the real spend on a failed call
    isn't lost. A backend/transport exception (raised before any usage exists) leaves these None."""

    def __init__(self, message: str, *, usage: dict | None = None, cost_usd: float | None = None,
                attempts: int = 0, model: str | None = None, backend: str | None = None,
                auth_mode: str | None = None):
        super().__init__(message)
        self.usage = usage
        self.cost_usd = cost_usd
        self.attempts = attempts
        self.model = model
        self.backend = backend
        self.auth_mode = auth_mode


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
    pruned: list[str] | None = None


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
    """Return schema-validation error messages, path-qualified (empty list = valid). Bare
    `e.message` (e.g. "None is not of type 'string'") is identical across every field of the
    same type gone wrong, which made a real live failure undiagnosable from the log alone (issue
    #490 follow-up) — three fields nulled at once produced the exact same three-line message
    whichever fields they were, with no way to tell which from the error text itself."""
    import jsonschema
    errors = []
    for e in jsonschema.Draft202012Validator(schema).iter_errors(obj):
        path = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in e.path)
        errors.append(f"{path.lstrip('.') or '$'}: {e.message}")
    return errors


def _prune_unknown(obj, schema, _path: str = "") -> list[str]:
    """Remove properties not declared in `schema`'s `properties`, but only inside an object
    schema that declares `"additionalProperties": False` — a free-form object (e.g.
    `file_metadata`, plain `{"type": "object"}`) is left untouched. Recurses through
    `properties` sub-schemas and array `items` schemas; mutates `obj` in place. Returns the
    dotted/bracketed path of every key removed (e.g. ``entities[3].roles[0].date``), so a
    caller can log schema drift instead of failing the whole document over a stray key (#412).
    """
    removed: list[str] = []
    if not isinstance(schema, dict):
        return removed
    if schema.get("type") == "object" and isinstance(obj, dict):
        properties = schema.get("properties") or {}
        if schema.get("additionalProperties") is False:
            for key in list(obj.keys()):
                if key not in properties:
                    removed.append(f"{_path}.{key}" if _path else key)
                    del obj[key]
        for key, sub_schema in properties.items():
            if key in obj:
                removed += _prune_unknown(obj[key], sub_schema, f"{_path}.{key}" if _path else key)
    elif schema.get("type") == "array" and isinstance(obj, list):
        items_schema = schema.get("items")
        if items_schema:
            for i, item in enumerate(obj):
                removed += _prune_unknown(item, items_schema, f"{_path}[{i}]")
    return removed


def _to_strict_schema(node):
    """Derive an OpenAI strict-mode (`strict: true`) compatible variant of a JSON schema (issue
    #479): every object's `required` becomes every one of its properties, since strict mode
    demands each key always be present. A property newly forced required this way — originally
    optional — is widened to a nullable union if it's a scalar (string/integer/etc.) type, the
    same `_NULLABLE_STR`-style convention `pipeline/schemas.py` already uses for "no value" —
    so the model expresses omission as an explicit `null` instead of being unable to omit at
    all. An optional array-typed property is left as a plain array: the model can already
    express "nothing here" as `[]` with no null union needed, and there are currently no
    optional object-typed properties in any schema this runs against. Recurses through
    `properties` and array `items`. Never mutates its input — every level is copied — since the
    schemas this runs against (`pipeline/schemas.py`'s module-level constants) are shared,
    reused objects."""
    if not isinstance(node, dict):
        return node
    node = dict(node)
    if node.get("type") == "object" and "properties" in node:
        original_required = set(node.get("required") or [])
        new_props = {}
        for key, sub in node["properties"].items():
            sub = _to_strict_schema(sub)
            if key not in original_required and sub.get("type") not in ("array", "object"):
                sub = _nullable_variant(sub)
            new_props[key] = sub
        node["properties"] = new_props
        node["required"] = list(new_props.keys())
        node["additionalProperties"] = False
    if "items" in node:
        node["items"] = _to_strict_schema(node["items"])
    return node


def _nullable_variant(sub: dict) -> dict:
    """Widen one property schema to also accept `null`, appending `None` to its `enum` list too
    if it has one (an enum without an explicit `null` member rejects a null value even once the
    `type` allows it) — the scalar half of `_to_strict_schema`."""
    sub = dict(sub)
    t = sub.get("type")
    if isinstance(t, list):
        if "null" not in t:
            sub["type"] = [*t, "null"]
    elif t is not None and t != "null":
        sub["type"] = [t, "null"]
    if "enum" in sub and None not in sub["enum"]:
        sub["enum"] = [*sub["enum"], None]
    return sub


def _strip_none(value):
    """Recursively drop `None`-valued dict keys — the inverse of `_to_strict_schema`'s nullable
    widening. OpenAI's strict mode forces every optional field to be present, using `null` for
    "no value"; every other backend (and `pipeline/schemas.py`'s own non-strict contract) omits
    the key entirely for the same meaning. Applied to a strict-mode response before it reaches
    the shared validate/prune path, so `schemas.py`'s actual schema — and every downstream
    reader's long-established omitted-vs-null handling — needs no OpenAI-specific carve-out."""
    if isinstance(value, dict):
        return {k: _strip_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_none(v) for v in value]
    return value


def _denormalize_strict_json(text: str) -> str:
    """Undo `_to_strict_schema`'s null-for-omitted-field convention on a raw OpenAI strict-mode
    response, before the shared `_extract_json`/`_validate`/`_prune_unknown` path sees it — so
    that path keeps validating against `schemas.py`'s actual (non-strict) schema unmodified,
    exactly as it does for every other backend. If the response isn't valid JSON (shouldn't
    happen under real wire-level enforcement, but nothing here should assume it), the text is
    returned unchanged and the ordinary invalid-JSON retry path handles it."""
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    return json.dumps(_strip_none(parsed))


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


@lru_cache(maxsize=1)
def _agent_supports_tools() -> bool:
    """Whether the installed `claude-agent-sdk` accepts `ClaudeAgentOptions(tools=…)`. The option
    postdates our `claude-agent-sdk>=0.1` floor, and `ClaudeAgentOptions` is a dataclass, so
    passing it blind would `TypeError` on an older SDK.

    Anything unexpected — an SDK that isn't installed, or whose `ClaudeAgentOptions` isn't a
    dataclass we can introspect — answers False: keep the older, costlier behaviour rather than
    risk a `TypeError` on every model call over a cost optimization."""
    import dataclasses
    try:
        from claude_agent_sdk import ClaudeAgentOptions
        return any(f.name == "tools" for f in dataclasses.fields(ClaudeAgentOptions))
    except Exception:
        return False


async def _agent_query(prompt: str, model: str, env: dict | None,
                       effort: str | None = None) -> dict:
    from claude_agent_sdk import query, ClaudeAgentOptions

    # This is a headless, single-turn completion call — claude.ai connectors are irrelevant
    # here regardless of auth mode. Opting out via this env var (rather than API-key auth
    # merely taking precedence over them) skips the CLI's connectors-eligibility check
    # entirely, which avoids the "connectors are disabled" stderr warning it otherwise prints
    # once per call under api-key auth (#491). The same reasoning extends to the CLI's own
    # telemetry/error-reporting/other-non-essential network traffic: none of it serves a
    # one-shot subprocess call over documents that are often privileged or confidential (#491).
    call_env = {
        "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        **(env or {}),
    }
    opts = dict(
        model=model,
        system_prompt=_SYSTEM_PROMPT,
        allowed_tools=[],       # nothing is auto-approved (see `tools` below — not the same knob)
        setting_sources=[],     # don't load .claude configs; trims the preamble
        max_turns=1,            # single completion, no agent loop
        env=call_env,
    )
    # `tools=[]` is what actually keeps the built-in tool suite out of the request. `allowed_tools`
    # only governs *auto-approval*: with it empty the model never calls a tool, but every Claude
    # Code tool stayed **defined** in the request — measured at ~11.2K tokens per call, billed at
    # the cache-write rate (1.25x), to describe tools this stage is forbidden to use. It went
    # unnoticed precisely because behaviour was correct; only the bill showed it. See D145.
    if _agent_supports_tools():
        opts["tools"] = []
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
                # `usage` is normally a dict, but never trust a third-party shape enough to crash
                # on it — fall back to a fresh dict so the harness-timing fields below always land.
                raw_usage = getattr(message, "usage", None)
                usage = dict(raw_usage) if isinstance(raw_usage, dict) else {}
                # Harness-level timing (#402): `duration_api_ms` is time actually spent in API
                # requests, vs. the message's own wall-clock `duration_ms` — the gap is
                # backoff/wait the harness did internally. `num_turns` is the internal request
                # count for the session. Together they let a throttled call (long gap, i.e. many
                # turns) be told apart from a genuinely slow one after the fact.
                api_ms = getattr(message, "duration_api_ms", None)
                if api_ms is not None:
                    usage["duration_api_ms"] = api_ms
                num_turns = getattr(message, "num_turns", None)
                if num_turns is not None:
                    usage["num_turns"] = num_turns
                out["usage"] = usage or None
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
                                effort: str | None = None, prefix: str | None = None) -> dict:
    """Claude Agent SDK backend. Works in either auth mode (key via env, or subscription).

    `max_tokens` is accepted for a uniform backend signature but unused — the agent's
    output is bounded by max_turns, not a token cap. The agent SDK has no `cache_control`
    knob (A1), so a content-block prompt is flattened to plain text here. `prefix` (response
    pagination, #343) is likewise unused — with no client-enforced output ceiling there is
    nothing to continue past, so this backend never truncates on max_tokens.
    """
    full = f"{_flatten_prompt(prompt)}\n\nReturn JSON matching this schema:\n{json.dumps(schema)}"
    env = {"ANTHROPIC_API_KEY": api_key} if api_key else None
    return await _agent_query(full, model_id, env, effort)


def _api_cost(model_id: str, usage) -> float | None:
    rates = _PRICING.get(model_id)
    if not rates:
        return None
    inp, outp, cw, cr = rates

    def g(name):
        return getattr(usage, name, 0) or 0

    return (g("input_tokens") * inp + g("output_tokens") * outp
            + g("cache_creation_input_tokens") * cw + g("cache_read_input_tokens") * cr)


def _batch_cost(model_id: str, usage) -> float | None:
    """Message Batches pricing is a flat 50% off every standard per-token rate, including
    cache read/write (#214) — so batch cost is just the normal API cost at half price."""
    cost = _api_cost(model_id, usage)
    return cost * 0.5 if cost is not None else None


async def _api_complete_async(prompt: str | list[dict], model_id: str, schema: dict,
                              api_key: str | None, max_tokens: int,
                              effort: str | None = None, prefix: str | None = None) -> dict:
    """Raw Claude Messages API backend with structured outputs.

    `prompt` may be a plain string or a list of Anthropic content blocks with a
    `cache_control` breakpoint (A1) — the Messages API's `content` field accepts either
    shape natively, so no conversion is needed here.

    `prefix` (response pagination, #343): when set, the partial output of a truncated call is
    prefilled as the assistant turn and the model continues it. Structured-output `format`
    enforcement is dropped on a continuation (constrained decoding can't resume mid-object) —
    the concatenation is validated by the shared shell — but `effort` is still carried so the
    same reasoning depth applies (I4). The returned `finish_reason` mirrors `stop_reason` so the
    shell can tell a max-token cut (`max_tokens`) from a natural stop."""
    import anthropic

    messages = [{"role": "user", "content": prompt}]
    kwargs: dict = {}
    if prefix is None:
        # `effort` composes with the structured-output `format` inside the one output_config dict.
        output_config = {"format": {"type": "json_schema", "schema": schema}}
        if effort:
            output_config["effort"] = effort
        kwargs["output_config"] = output_config
    else:
        # Continuation: prefill the partial (Anthropic rejects a trailing-whitespace assistant
        # turn, so rstrip — JSON ignores inter-token whitespace, so the concatenation is intact).
        messages.append({"role": "assistant", "content": prefix.rstrip()})
        if effort:
            kwargs["output_config"] = {"effort": effort}
    try:
        resp = await anthropic.AsyncAnthropic(api_key=api_key).messages.create(
            model=model_id,
            max_tokens=max_tokens,
            system=_SYSTEM_PROMPT,
            messages=messages,
            **kwargs,
        )
    except anthropic.RateLimitError as e:   # 429 — surface as the shared typed error
        raise RateLimitError(str(e) or "Claude API rate limit reached") from e
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
    usage = resp.usage
    usage_dict = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
    return {"text": text, "usage": usage_dict, "cost_usd": _api_cost(model_id, usage),
            "finish_reason": getattr(resp, "stop_reason", None)}


# OpenAI-compatible (Chat Completions) pricing: model id → (input, output, cached_input) $/tok,
# from the model catalog (model_catalog.yaml). `input` is the cache-MISS input rate; `cached_input`
# is the discounted rate applied to the part of the prompt served from the provider's context
# cache. `_openai_cost` splits prompt tokens into cached vs uncached from the provider's usage
# fields (OpenAI nests the count under `prompt_tokens_details.cached_tokens`; DeepSeek reports
# `prompt_cache_hit_tokens`), so a cache hit is priced at the lower rate rather than over-charged.


def _cached_input_tokens(usage: dict) -> int:
    """Prompt tokens served from the provider's context cache, normalised across usage shapes.
    OpenAI nests the count under `prompt_tokens_details.cached_tokens`; DeepSeek reports it as
    `prompt_cache_hit_tokens` (with `prompt_tokens` = hit + miss). Absent either field, treat it
    as no cache hit."""
    details = usage.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens")
    if cached is None:
        cached = usage.get("prompt_cache_hit_tokens")
    return cached or 0


def _fold_in_hidden_reasoning(usage: dict | None) -> dict | None:
    """Normalise a usage dict that reports thinking tokens only as a gap in `total_tokens`.

    Gemini's OpenAI-compatibility endpoint leaves its thinking tokens out of `completion_tokens`
    and reports no `completion_tokens_details.reasoning_tokens` at all — the only trace they were
    spent is `total_tokens` exceeding `prompt_tokens + completion_tokens` (#547; D171 flagged this
    as unconfirmed, a high-effort benchmark run then confirmed it: 27,147 prompt + 847 completion
    against a 43,131 total). Google bills those tokens at the output rate, so leaving them out
    under-charged every Gemini call in proportion to how hard the model thought.

    Folding the gap into `completion_tokens` is what makes `_openai_cost` charge for them, and
    recording it under `completion_tokens_details.reasoning_tokens` matches OpenAI's shape — where
    reasoning is a *subset* of `completion_tokens`, not a sibling of it — so `_record_usage`, the
    `watchdog usage` reasoning note, and `acomplete_json`'s truncation diagnostic all read it with
    no provider-specific branch. Returns `usage` untouched when there's no gap or the provider
    already reported a reasoning count."""
    if not usage:
        return usage
    details = usage.get("completion_tokens_details") or {}
    if details.get("reasoning_tokens"):
        return usage
    hidden = ((usage.get("total_tokens") or 0)
              - (usage.get("prompt_tokens") or 0)
              - (usage.get("completion_tokens") or 0))
    if hidden <= 0:
        return usage
    return {**usage,
            "completion_tokens": (usage.get("completion_tokens") or 0) + hidden,
            "completion_tokens_details": {**details, "reasoning_tokens": hidden}}


def _openai_cost(model_id: str, usage: dict | None) -> float | None:
    rates = _OPENAI_PRICING.get(model_id)
    if not rates or not usage:
        return None
    inp, outp, cached_rate = rates
    prompt = usage.get("prompt_tokens", 0) or 0
    cached = min(_cached_input_tokens(usage), prompt)   # cache hits are a subset of prompt tokens
    return ((prompt - cached) * inp
            + cached * cached_rate
            + (usage.get("completion_tokens", 0) or 0) * outp)


def _openai_batch_cost(model_id: str, usage: dict | None) -> float | None:
    """OpenAI's Batch API is 50% off every token — the same flat discount `_batch_cost` applies
    for Anthropic's Message Batches API (#530). No separate batch pricing table, and no
    cache-token modelling beyond what `_openai_cost` already does for a live call."""
    cost = _openai_cost(model_id, usage)
    return cost * 0.5 if cost is not None else None


# DeepSeek V4 collapsed thinking/non-thinking into a single model id switched by a request param
# (the old deepseek-chat/deepseek-reasoner split is deprecated). Watchdog keeps the choice inside
# the model token — `deepseek-v4-flash` (non-thinking) vs `deepseek-v4-flash-thinking` — so it rides
# the existing `[backend:]model` grammar with no extra provider-specific knob. The backend strips
# this marker, uses the bare id for the request + cost lookup, and sends DeepSeek's explicit toggle.
# The provider default is thinking-ENABLED, so we always send the toggle to pin the intended mode.
# Toggle shape (OpenAI format): {"thinking": {"type": "enabled"|"disabled"}}. Docs:
# https://api-docs.deepseek.com/guides/thinking_mode  (D88)
_DEEPSEEK_THINKING_SUFFIX = "-thinking"

# DeepSeek's reasoning mode caps chain-of-thought + final answer under one combined `max_tokens`
# (default 32K, max 64K: https://api-docs.deepseek.com/guides/reasoning_model). The flat per-task
# ceilings in `_TASK_MAX_TOKENS` starve the JSON output once the CoT eats into that same budget —
# fewer key facts, elided quotes (#337). Applied only to deepseek + `-thinking` + the large-output
# tasks; every other backend/task keeps its normal ceiling.
_DEEPSEEK_THINKING_MAX_TOKENS = 48000

# OpenAI reasoning models have the same starvation mode (#354): reasoning tokens and the visible
# answer share the one `max_completion_tokens` budget, so a ceiling too tight for the reasoning
# volume leaves the JSON truncated with zero visible output — the exact failure #337 fixed for
# DeepSeek thinking, confirmed live (benchmark 2026-08-03-1459): 0 visible characters, reasoning
# tokens == the entire completion. Applied only to reasoning models (per `_openai_is_reasoning`)
# on the large-output tasks; chat models keep the normal ceiling. Note this is the *wire* ceiling
# only — sectioning still plans against the base task budget, since the JSON itself can't count
# on the reasoning share (see `output_ceiling_for_sectioning`).
#
# Reasoning volume is almost entirely a function of `effort`, not a flat constant (same document,
# same task: ~3.8K tokens at low, ~12.6K at medium, 48K+ at high) — so the reserve added on top of
# the visible-answer budget (`_TASK_MAX_TOKENS`, 16K) scales with it. All five levels are listed
# explicitly rather than falling back past `high`: gpt-5.6-terra/luna genuinely accept
# `xhigh`/`max` (model_catalog.yaml), and a silent fallback to the medium reserve there would
# under-provision exactly the deepest-reasoning configurations. 128K is the gpt-5-mini class's
# documented output-token limit; every level below stays under it.
_OPENAI_REASONING_RESERVE = {
    "low": 16_000, "medium": 48_000, "high": 80_000, "xhigh": 100_000, "max": 100_000,
}
_OPENAI_REASONING_RESERVE_DEFAULT = 48_000   # effort unspecified -> treat as medium

# Gemini has the same starvation mode (#541, follow-up to #354/D167). Its OpenAI-compatibility
# endpoint maps `reasoning_effort` onto the native API's internal thinking budget, and that
# budget is deducted from the same `max_tokens` envelope as the visible answer — confirmed
# against Google's own thinking-model docs (ai.google.dev/gemini-api/docs/thinking) and
# corroborated by third-party reports of the resulting failure (a `MAX_TOKENS` finish reason with
# empty text once the budget is spent mid-thought, e.g. googleapis/python-genai#782,
# discuss.ai.google.dev's "finishReason: MAX_TOKENS - But Text is Empty"). Unlike OpenAI, every
# current Gemini model always gets an effort value (`_effort_levels` returns low/medium/high
# unconditionally — no chat-vs-reasoning split to gate on), and several models (2.5+ Flash/Pro)
# think by default even when `reasoning_effort` is omitted — so the reserve applies
# unconditionally on the large-output tasks, not behind an is-reasoning check. Sectioning still
# plans against the base task budget for the same reason as the OpenAI case (see
# `output_ceiling_for_sectioning`).
#
# The per-level split is a reasoned placeholder, not a measured one — #354's OpenAI numbers came
# from a real benchmark sweep at each effort level; #541 has no equivalent Gemini sweep yet (the
# only runs so far, `benchmarks/FINDINGS.md`'s gemini-flash/-flash-lite arms, were at `low`
# effort or no effort pinned, so they never exercised this path). Scaled down from the OpenAI
# reserve's low/medium/high shape to fit Gemini's documented output-token ceiling (65,536 for the
# 2.5/3.x families) rather than OpenAI's 128K-class limit. Re-tune with real per-effort reasoning-
# token counts the first time a high-effort Gemini sweep runs, the same way D108's original flat
# 48K guess for OpenAI got replaced by measured figures in D167.
_GEMINI_REASONING_RESERVE = {"low": 16_000, "medium": 32_000, "high": 48_000}
_GEMINI_REASONING_RESERVE_DEFAULT = 32_000   # effort unspecified -> treat as medium

# Transient-failure retry for the OpenAI-compatible backends (#354). The Anthropic SDK retries
# 429/5xx internally (2 attempts, backoff); this httpx path had none, so a single transient
# 502/529 from a provider failed the document outright — `acomplete_json` retries only on invalid
# JSON, not backend exceptions. 5xx only: a 429 must keep raising RateLimitError immediately so
# the orchestrator stops the batch cleanly instead of hammering a limited provider.
_TRANSIENT_RETRIES = 2
_TRANSIENT_BACKOFF_S = 2.0


def _split_deepseek_thinking(model_id: str) -> tuple[str, bool]:
    """(bare model id, thinking?) from a DeepSeek model token — strips a `-thinking` marker.
    Non-thinking is the default (bare id), so extraction stays cheap unless thinking is opted in."""
    if model_id.endswith(_DEEPSEEK_THINKING_SUFFIX):
        return model_id[: -len(_DEEPSEEK_THINKING_SUFFIX)], True
    return model_id, False


def _openai_response_format(base_url: str, schema: dict) -> dict:
    """The `response_format` for an OpenAI-compatible request — real `json_schema` structured
    output where it's safe, portable `json_object` mode elsewhere (D98/D151, issue #479).

    Gemini's OpenAI-compat endpoint honours `json_schema` with genuine wire-level enforcement,
    and its own schema engine treats `required` as an optional list rather than demanding every
    property (ai.google.dev/gemini-api/docs/structured-output) — matching schemas.py's
    omit-optional-fields design (e.g. `_KEY_FACT`'s `required` is just `["fact"]`) with no
    rewrite needed. OpenAI's own Structured Outputs mode is real too, but only in `strict` form,
    which demands every property be listed in `required` (nullable unions standing in for
    "optional") — directly conflicting with that same design, so it gets a mechanically-derived
    `_to_strict_schema` variant instead of the schema as-authored (D151) — repeated live gpt-nano
    failures under the weaker `json_object` mode (issue #490) made D98's original prompt-only
    tradeoff no longer worth it for OpenAI specifically. DeepSeek's JSON Output docs
    (api-docs.deepseek.com/guides/json_mode) document only `json_object` — no schema field at
    all, so it (and `local`/`openrouter`, D139 — no capability table for an arbitrary model)
    keep the schema-in-prompt path."""
    if "generativelanguage.googleapis.com" in base_url:
        return {"type": "json_schema", "json_schema": {"name": "watchdog_response", "schema": schema}}
    if base_url.rstrip("/") == _OPENAI_BASE["openai"]:
        return {"type": "json_schema",
                "json_schema": {"name": "watchdog_response", "strict": True,
                                 "schema": _to_strict_schema(schema)}}
    return {"type": "json_object"}


async def _openai_complete_async(prompt: str | list[dict], model_id: str, schema: dict,
                                 api_key: str | None, max_tokens: int,
                                 effort: str | None = None, prefix: str | None = None,
                                 *, base_url: str) -> dict:
    """OpenAI-compatible Chat Completions backend — OpenAI, DeepSeek, Gemini (via its
    OpenAI-compatibility endpoint, https://ai.google.dev/gemini-api/docs/openai), and any
    other service that speaks the same wire format (selected by `base_url`).

    Structured output is requested via `_openai_response_format` (real `json_schema` enforcement
    on Gemini; portable JSON-object mode + schema-in-prompt elsewhere, D98), then validated by
    the shared `acomplete_json` shell either way — that local validation is a safety net, not the
    only guard, once the provider itself enforces the schema. `effort` arrives already resolved
    to the provider's native value (or None) and is sent as `reasoning_effort` (#125). No
    provider-agnostic cache_control equivalent is wired here (A1 is Claude-only), so a
    content-block prompt is flattened to plain text.

    DeepSeek carries an optional `-thinking` marker on the model id; it is stripped here and
    translated into DeepSeek's explicit thinking toggle (default off — see #320).

    `prefix` (response pagination, #343): DeepSeek's chat-prefix-completion beta continues a
    truncated response — the partial output is appended as an assistant turn with `prefix: true`
    against the `/beta` base, and structured-output enforcement is dropped (the model is
    completing a partial object, not generating a fresh one). OpenAI and Gemini return a *new*
    assistant message rather than continuing the given one, so they never prefill
    (`supports_continuation=False` in `_BACKEND_META`) and always reach this with `prefix=None`. The returned
    `finish_reason` lets the shell distinguish a max-token cut (`length`) from a natural stop."""
    import ssl

    import httpx

    is_deepseek = "deepseek" in base_url
    is_openai = base_url.rstrip("/") == _OPENAI_BASE["openai"]
    is_gemini = base_url.rstrip("/") == _OPENAI_BASE["gemini"].rstrip("/")
    thinking: bool | None = None
    if is_deepseek:
        model_id, thinking = _split_deepseek_thinking(model_id)

    response_format = _openai_response_format(base_url, schema)
    user_content = _flatten_prompt(prompt)
    if response_format["type"] != "json_schema":   # schema not enforced by the API — spell it out
        user_content += f"\n\nReturn JSON matching this schema:\n{json.dumps(schema)}"

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    body = {"model": model_id, "messages": messages}
    path = "/chat/completions"
    if prefix is not None and is_deepseek:   # continuation via DeepSeek's prefix-completion beta
        messages.append({"role": "assistant", "content": prefix.rstrip(), "prefix": True})
        path = "/beta/chat/completions"
    else:
        body["response_format"] = response_format
    # OpenAI reasoning models reject `max_tokens` and require `max_completion_tokens`; chat models
    # and every other OpenAI-compatible provider (DeepSeek, Gemini, local, OpenRouter) take
    # `max_tokens`. Gated to the real OpenAI endpoint specifically (#380) — `_openai_is_reasoning`'s
    # prefix table (`gpt-5`, `o1`, `o3`, `o4`) is OpenAI's own naming convention, and a local model's
    # id has no relation to it (an operator could name a self-hosted model anything); sending
    # `max_completion_tokens` to a runner that doesn't recognize it would either be silently
    # ignored (defeating the max_tokens ceiling `output_ceiling_for_sectioning` relies on for
    # local/openrouter) or rejected outright.
    if is_openai and _openai_is_reasoning(model_id):
        body["max_completion_tokens"] = max_tokens
    else:
        body["max_tokens"] = max_tokens
    if effort:
        body["reasoning_effort"] = effort
    if thinking is not None:   # DeepSeek: pin the mode explicitly (provider defaults to enabled)
        body["thinking"] = {"type": "enabled" if thinking else "disabled"}
    url = base_url.rstrip("/") + path
    # `api_key` is None for a `local` backend with no key configured (#380) — most self-hosted
    # runners don't check for one, so omit the header rather than sending a literal "Bearer None".
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    # Verify via the OS trust store rather than the bundled certifi CA list — on a machine
    # running a TLS-inspecting corporate proxy, the proxy's root CA is trusted
    # by the OS but absent from certifi, which otherwise fails cert verification here.
    import truststore
    ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    async with httpx.AsyncClient(timeout=600, verify=ssl_context) as client:
        # Bounded retry on 5xx only (#354) — parity with the Anthropic SDK's built-in transient
        # retry. 429 is excluded: it raises RateLimitError below on the first response.
        for attempt in range(_TRANSIENT_RETRIES + 1):
            resp = await client.post(url, headers=headers, json=body)
            if resp.status_code < 500 or attempt == _TRANSIENT_RETRIES:
                break
            await asyncio.sleep(_TRANSIENT_BACKOFF_S * (attempt + 1))
    if resp.status_code == 429:
        raise RateLimitError(f"{base_url} rate limit reached")
    resp.raise_for_status()
    data = resp.json()
    choices = data.get("choices") or []
    text = (choices[0].get("message", {}).get("content") or "") if choices else ""
    if is_openai and response_format["type"] == "json_schema":
        text = _denormalize_strict_json(text)
    finish = choices[0].get("finish_reason") if choices else None
    usage = data.get("usage")
    if is_gemini:   # thinking tokens arrive only as a total_tokens gap (#547) — fold them in
        usage = _fold_in_hidden_reasoning(usage)
    return {"text": text, "usage": usage, "cost_usd": _openai_cost(model_id, usage),
            "finish_reason": finish}


# OpenAI-compatible base URLs (the `/chat/completions` path is appended per request).
_OPENAI_BASE = {
    "openai":   "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com",
    "gemini":   "https://generativelanguage.googleapis.com/v1beta/openai",
}


@dataclass(frozen=True)
class _BackendMeta:
    """Per-backend registration metadata — everything a new backend needs to slot in, in one
    place, instead of touching five separate tables (#453)."""
    provider: str
    base_url: str | None = None          # fixed URL (openai/deepseek/gemini)
    dynamic_base_url: bool = False       # user-supplied at call time (local, openrouter, #380)
    supports_continuation: bool = False  # can prefill+continue past a max-token cut (#343)
    enforces_max_tokens: bool = False    # wire enforces max_tokens with no way past it (#343)
    batch_only: bool = False             # selectable but never dispatched via acomplete_json (#214)


# backend name → its registration metadata. `local`/`openrouter` (#380) are OpenAI-compatible too,
# but their base URL is user-supplied (`watchdog configure local_base_url`/`openrouter_base_url`,
# or `watchdog auth`) rather than one of the fixed URLs above — `dynamic_base_url` marks them so
# `acomplete_json` partial-applies the resolved URL per call instead of at import time.
_BACKEND_META: dict[str, _BackendMeta] = {
    "claude-api":       _BackendMeta(provider="anthropic", supports_continuation=True,
                                      enforces_max_tokens=True),
    "claude-agent-sdk": _BackendMeta(provider="anthropic"),
    # claude-batch/openai-batch are real, selectable backends (CLI validation, auth resolution)
    # but are never dispatched through the single-call acomplete_json path — each provider's
    # Batch API is submit-many/poll/collect, handled entirely by orchestrate._run_batch +
    # pipeline.batch_extract (#214, #530). `batch_only` exists only to turn a misrouted call (e.g.
    # a classifier/finalizer stage accidentally set to a batch backend) into a clear error instead
    # of the generic "unknown backend" one.
    "claude-batch":     _BackendMeta(provider="anthropic", batch_only=True),
    "openai":     _BackendMeta(provider="openai",   base_url=_OPENAI_BASE["openai"],
                                enforces_max_tokens=True),
    "openai-batch":     _BackendMeta(provider="openai", batch_only=True),
    "deepseek":   _BackendMeta(provider="deepseek", base_url=_OPENAI_BASE["deepseek"],
                                supports_continuation=True, enforces_max_tokens=True),
    "gemini":     _BackendMeta(provider="gemini",   base_url=_OPENAI_BASE["gemini"],
                                enforces_max_tokens=True),
    "local":      _BackendMeta(provider="local",      dynamic_base_url=True, enforces_max_tokens=True),
    "openrouter": _BackendMeta(provider="openrouter", dynamic_base_url=True, enforces_max_tokens=True),
}

# Selectable backend names (public — the CLI validates a stage's `backend:model` against this).
BACKENDS = tuple(_BACKEND_META)
# Backends that take a Claude tier name (haiku/sonnet/opus); the rest take a raw provider id.
CLAUDE_BACKENDS = tuple(name for name, m in _BACKEND_META.items() if m.provider == "anthropic")
# Batch-mode-only backends (submit-many/poll/collect, valid only as extractor_model) — one per
# provider that offers a Batch API (#214, #530).
BATCH_BACKENDS = tuple(name for name, m in _BACKEND_META.items() if m.batch_only)

# claude-batch/openai-batch are excluded here (batch_only, no dispatch function) — see
# _BackendMeta above.
_ABACKENDS = {
    "claude-api":       _api_complete_async,
    "claude-agent-sdk": _agent_complete_async,
    "openai":     partial(_openai_complete_async, base_url=_BACKEND_META["openai"].base_url),
    "deepseek":   partial(_openai_complete_async, base_url=_BACKEND_META["deepseek"].base_url),
    "gemini":     partial(_openai_complete_async, base_url=_BACKEND_META["gemini"].base_url),
    "local":      _openai_complete_async,
    "openrouter": _openai_complete_async,
}


def provider_for_backend(backend: str | None) -> str:
    """Provider name for a backend; None (unresolved/default) maps to 'anthropic'."""
    if backend is None:
        return "anthropic"
    meta = _BACKEND_META.get(backend)
    return meta.provider if meta else "anthropic"


def _resolve_backend_auth(requested: str | None) -> tuple[str, str, str | None, str, str | None]:
    """Resolve (backend, provider, api_key, auth_mode, base_url) for a call.

    Claude backends consult the subscription/api-key mode (`auth.resolve_auth`); other providers
    use their stored API key directly (`auth.get_api_key`), independent of the Claude mode — so a
    user with only an OpenAI/DeepSeek key can run those backends without configuring Claude (#125).
    With no explicit backend, defaults among the Claude backends by auth mode (unchanged).

    `base_url` is only ever non-None for `local`/`openrouter` (#380), whose endpoint is
    user-supplied rather than one of the fixed URLs the other OpenAI-compatible backends bind at
    import time — every other backend gets `None` back since its base URL (if any) is already
    baked into `_ABACKENDS`."""
    chosen = requested
    meta = _BACKEND_META.get(chosen) if chosen is not None else None
    if meta and meta.batch_only:
        raise ModelError(f"'{chosen}' is a batch-mode-only backend — it cannot be used for a "
                         "single-call task; it's only valid as extractor_model (#214, #530)")
    if chosen is not None and meta is None:
        raise ModelError(f"unknown backend '{chosen}'")
    provider = meta.provider if chosen else "anthropic"

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
                f"'{auth_mode}' — run `watchdog auth` to switch to api-key mode, or use the claude-agent-sdk backend")
        return chosen, provider, api_key, auth_mode, None

    base_url = None
    if meta.dynamic_base_url:
        base_url = auth.get_base_url(provider)
        if not base_url:
            raise ModelError(
                f"the {chosen} backend needs a base URL — run "
                f"`watchdog configure {provider}_base_url <url>` (e.g. http://localhost:11434/v1)")

    api_key = auth.get_api_key(provider)
    if auth.provider_requires_key(provider) and not api_key:
        raise ModelError(f"the {chosen} backend needs an API key — run `watchdog auth` to add one")
    return chosen, provider, api_key, "api-key", base_url


# ── response pagination (#343) ────────────────────────────────────────────────
# A single extract call can need more output than the provider's fixed `max_tokens` allows,
# which would truncate the JSON mid-object. To guarantee no truncated extraction is ever
# accepted, the shell (a) detects the provider's truncation signal authoritatively — never
# inferring it from a parse failure, which would silently accept a truncated-but-parseable
# response — and (b) for backends that support prefill continuation, re-issues the call with
# the partial output prefilled and concatenates the halves until the model stops naturally.
# Backends that can't continue (openai/gemini return a new message, not a continuation; the
# agent SDK has no cap to hit) never paginate; a truncated result from them is rejected so the
# orchestrator falls back to bounded-output sectioning.

# Which backends support continuation (Claude assistant prefill; DeepSeek chat-prefix-completion
# beta) is recorded per-backend in `_BackendMeta.supports_continuation` above.
# Hard cap on continuation rounds so a pathological run can't loop forever; hitting it leaves the
# result flagged truncated → the orchestrator sections instead.
_MAX_CONTINUATIONS = 8
# finish_reason / stop_reason values that mean "cut off at max_tokens", across providers.
_TRUNCATION_FINISH = {"length", "max_tokens", "MAX_TOKENS"}


def _is_truncated(finish_reason) -> bool:
    """Whether a backend's finish_reason/stop_reason marks a max-token cut (not a natural stop)."""
    return bool(finish_reason) and str(finish_reason) in _TRUNCATION_FINISH


def _merge_usage(a: dict | None, b: dict | None) -> dict | None:
    """Sum the numeric token counts of two per-call usage dicts (continuation rounds), tolerating
    differing provider shapes and nested detail blocks — non-numeric or one-sided keys pass
    through so telemetry still reflects the aggregate cost/usage of a paginated call."""
    if a is None or b is None:
        return a if b is None else b
    merged = dict(a)
    for k, v in b.items():
        cur = merged.get(k)
        # bools are ints in Python; guard both sides so a flag (e.g. cache_hit) is never summed.
        if (isinstance(v, (int, float)) and isinstance(cur, (int, float))
                and not isinstance(v, bool) and not isinstance(cur, bool)):
            merged[k] = cur + v
        elif isinstance(v, dict) and isinstance(cur, dict):
            merged[k] = _merge_usage(cur, v)
        elif k not in merged:
            merged[k] = v
    return merged


def _task_max_tokens(task: str, backend: str, model_id: str, effort: str | None = None) -> int:
    """The output-token ceiling sent to the provider for a task/backend/model. Models whose
    chain-of-thought shares the output budget — DeepSeek thinking (#337), OpenAI reasoning
    models (#354), Gemini (#541) — get a higher ceiling on the large-output tasks so reasoning
    can't starve the JSON. For OpenAI and Gemini the extra reserve scales with `effort`
    (`_OPENAI_REASONING_RESERVE`/`_GEMINI_REASONING_RESERVE`), since reasoning volume is a
    function of effort, not task. `effort` defaults to None (treated as medium) so existing call
    sites — DeepSeek's own ceiling, `output_ceiling_for_sectioning` — keep working unchanged."""
    if task in _TASK_MAX_TOKENS:
        if backend == "deepseek" and model_id.endswith(_DEEPSEEK_THINKING_SUFFIX):
            return _DEEPSEEK_THINKING_MAX_TOKENS
        if backend == "openai" and _openai_is_reasoning(model_id):
            reserve = _OPENAI_REASONING_RESERVE.get(effort, _OPENAI_REASONING_RESERVE_DEFAULT)
            return _TASK_MAX_TOKENS[task] + reserve
        if backend == "gemini":
            reserve = _GEMINI_REASONING_RESERVE.get(effort, _GEMINI_REASONING_RESERVE_DEFAULT)
            return _TASK_MAX_TOKENS[task] + reserve
    return _TASK_MAX_TOKENS.get(task, _API_MAX_TOKENS)


def output_ceiling_for_sectioning(task: str, backend: str | None, model: str | None) -> int | None:
    """The per-call output-token ceiling that sectioning must keep a document under — or None
    when there is nothing to protect (#343). None is returned for the agent SDK (no enforced
    ceiling), for the prefill-continuation backends (claude-api, deepseek — pagination grows the
    output past the cap), and for an unresolved backend (`None` routes to a Claude backend, both
    of which are None-returning). openai, gemini, local, and openrouter (#380) return a real
    number: they enforce max_tokens yet can't continue, so a document whose estimated output
    would exceed the ceiling must be sectioned up front rather than truncating and relying on the
    reactive fallback."""
    meta = _BACKEND_META.get(backend)
    if meta is None or not meta.enforces_max_tokens or meta.supports_continuation:
        return None
    model_id = resolve_model_id(model or DEFAULT_TIER)
    if (backend == "openai" and _openai_is_reasoning(model_id)) or backend == "gemini":
        # The raised wire ceiling (#354, #541) is shared with chain-of-thought/thinking, so the
        # JSON itself can't count on more than the base task budget — plan sectioning against that.
        return _TASK_MAX_TOKENS.get(task, _API_MAX_TOKENS)
    return _task_max_tokens(task, backend, model_id)


async def _complete_with_pagination(backend_fn, backend: str, prompt, model_id: str, schema: dict,
                                    api_key: str | None, max_tokens: int, effort_arg,
                                    task: str | None = None) -> dict:
    """Call the backend, then continue a max-token-truncated response by prefilling its partial
    output and concatenating, until a natural stop or the continuation guard (#343). Returns the
    assembled `{text, usage, cost_usd, truncated}`; `truncated` is True only if the output was
    still capped after the last allowed round (or the backend can't continue), so the caller
    never accepts a partial extraction."""
    out = await backend_fn(prompt, model_id, schema, api_key, max_tokens, effort_arg)
    text = out.get("text") or ""
    usage = out.get("usage")
    cost = out.get("cost_usd") or 0.0
    rounds = 0
    while (_is_truncated(out.get("finish_reason")) and _BACKEND_META[backend].supports_continuation
           and rounds < _MAX_CONTINUATIONS):
        rounds += 1
        prefix = text
        out = await backend_fn(prompt, model_id, schema, api_key, max_tokens, effort_arg,
                               prefix=prefix)
        fixture_capture.capture("continuation", backend=backend, model_id=model_id, task=task,
                                round=rounds, prefix=prefix, continuation_text=out.get("text"))
        text += out.get("text") or ""
        usage = _merge_usage(usage, out.get("usage"))
        cost += out.get("cost_usd") or 0.0
    return {"text": text, "usage": usage, "cost_usd": cost,
            "truncated": _is_truncated(out.get("finish_reason"))}


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
    'openai', 'deepseek', 'local', 'openrouter'); omit it to route by auth mode. `effort`
    (`low`/`medium`/`high`) is an abstract reasoning-depth intent — each provider maps it to its
    own native control or ignores it (D36, #125). On invalid/unparseable output the call retries
    on the **same** model (up to `max_retries` extra attempts) — never escalating — then raises.
    """
    chosen, provider, api_key, auth_mode, base_url = _resolve_backend_auth(backend)
    backend_fn = _ABACKENDS[chosen]
    if _BACKEND_META[chosen].dynamic_base_url:
        backend_fn = partial(backend_fn, base_url=base_url)

    requested = model or DEFAULT_TIER
    model_id = resolve_model_id(requested)
    effort_arg = _resolve_effort(provider, model_id, effort)   # provider-native value or None

    max_tokens = _task_max_tokens(task, chosen, model_id, effort_arg)

    start = time.monotonic()
    total_cost = 0.0
    last_err = "no attempts made"
    attempts = 0
    pruned_all: list[str] = []
    agg_usage: dict | None = None
    for _ in range(max_retries + 1):
        attempts += 1
        try:
            out = await _complete_with_pagination(backend_fn, chosen, prompt, model_id, schema,
                                                  api_key, max_tokens, effort_arg, task)
        except (RateLimitError, ModelError):
            raise
        except Exception as e:                  # any backend/transport failure → typed error
            raise ModelError(f"{chosen} backend error: {e}") from e
        if out.get("cost_usd"):
            total_cost += out["cost_usd"]
        # Accumulate usage across every attempt (not just the one that ends up succeeding) so a
        # retried call's telemetry reflects everything actually spent (#412) — a failed attempt's
        # tokens were real spend, not free information.
        agg_usage = _merge_usage(agg_usage, out.get("usage"))

        if out.get("truncated"):
            # Authoritatively truncated at the provider's output ceiling and not recoverable by
            # continuation (backend can't prefill, or still capped after the guard). Never accept a
            # partial extraction even if it happens to parse (#343). Re-running the same whole-doc
            # call would only truncate again (truncation is deterministic in the prompt), so stop
            # retrying and report it — the orchestrator falls back to sectioning, which bounds each
            # call's output.
            #
            # Empty text (#354) is a different failure from a partial response: the whole output
            # budget went to invisible reasoning and nothing was ever written, so "the document
            # was too dense" is the wrong diagnosis — the fix is a lower extractor_effort or a
            # raised ceiling, not re-sectioning. Empty text is the signal (provider-agnostic,
            # always available); the reasoning-token count is folded in only when reported.
            #
            # Starvation also has a *partial*-text shape (#547): reasoning eats most of the
            # envelope, the model starts writing, and the ceiling cuts it a few hundred tokens in.
            # Text is non-empty, so the empty-text branch misses it and the caller was told to
            # re-section a document whose sections were already small enough. When the reported
            # reasoning share exceeds the visible answer, the ceiling was spent thinking however
            # much text made it out — say so rather than blaming the document's density.
            usage = out.get("usage") or {}
            reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
            visible = (usage.get("completion_tokens") or 0) - reasoning
            if not out.get("text"):
                budget = f"{reasoning:,}-token " if reasoning else ""
                last_err = (f"the model used its entire {budget}output budget on internal "
                            "reasoning and returned no answer — try a lower extractor_effort")
            elif reasoning > visible:
                last_err = (f"the model spent {reasoning:,} of its output budget on internal "
                            f"reasoning, leaving only {visible:,} tokens of answer before the "
                            "max-token ceiling cut it off — try a lower extractor_effort")
            else:
                last_err = "output truncated at the model's max-token ceiling"
            fixture_capture.capture("truncation", backend=chosen, model_id=model_id, task=task,
                                    text=out.get("text"), usage=out.get("usage"))
            break

        parsed = _extract_json(out["text"])
        if parsed is None:
            last_err = "response was not valid JSON"
            fixture_capture.capture("malformed_json", backend=chosen, model_id=model_id,
                                    task=task, text=out["text"])
        else:
            removed = _prune_unknown(parsed, schema)
            if removed:
                fixture_capture.capture("schema_drift", backend=chosen, model_id=model_id,
                                        task=task, removed=removed, text=out["text"])
            pruned_all += removed
            errors = _validate(parsed, schema)
            if not errors:
                return ModelResult(
                    parsed=parsed, text=out["text"], model=model_id, backend=chosen,
                    auth_mode=auth_mode, usage=agg_usage,
                    cost_usd=round(total_cost, 6) or None,
                    latency_s=round(time.monotonic() - start, 3), attempts=attempts,
                    pruned=pruned_all or None,
                )
            last_err = "; ".join(errors[:3])
            if len(errors) > 3:
                last_err += f" (+{len(errors) - 3} more)"

    # Every attempt's usage was real spend even though none produced valid JSON — attach it so
    # the caller can still record it (D125), instead of the failure burning tokens invisibly.
    raise ModelError(
        f"task '{task}' failed JSON validation after {attempts} attempt(s) "
        f"on {chosen}: {last_err}",
        usage=agg_usage, cost_usd=round(total_cost, 6) or None, attempts=attempts,
        model=model_id, backend=chosen, auth_mode=auth_mode)


def complete_json(**kwargs) -> ModelResult:
    """Sync wrapper around :func:`acomplete_json` for non-async callers and tests."""
    return asyncio.run(acomplete_json(**kwargs))
