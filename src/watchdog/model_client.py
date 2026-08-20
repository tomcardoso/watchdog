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
import hashlib
import json
import re
import time
from dataclasses import dataclass
from functools import lru_cache, partial
from pathlib import Path

from watchdog import fixture_capture
from watchdog.cmd import auth
from watchdog.model_catalog import (
    _MODEL_IDS,  # noqa: F401 — re-exported for cmd/setup.py's interactive picker
    _OPENAI_PRICING,
    _PRICING,
    catalog_cache_breakpoints,
    catalog_context_window,
    catalog_effort_levels,
    catalog_is_reasoning,
    catalog_long_context_threshold,
    catalog_max_output_tokens,
    catalog_needs_thinking_param,
    catalog_tokenizer_ratio,
    fallback_context_window,
    fallback_is_reasoning,
    fallback_max_output_tokens,
    price_multiplier,
    resolve_model_id,
)
from watchdog.pipeline.json_io import _read_json_or

DEFAULT_TIER = "sonnet"

# The output-token envelope sent to the provider on the wire (#598): a single per-model number
# derived from the catalogued `max_output_tokens` cap, rather than a hand-picked per-task base
# plus a bolted-on per-provider reasoning reserve (see `_output_envelope`/`_wire_max_tokens`
# below for the rationale). `_OUTPUT_HEADROOM` leaves margin under the provider's own documented
# ceiling — it applies to every cap below, including the fallbacks, since margin against a cap we
# inferred rather than read is if anything more warranted, not less.
#
# An uncatalogued id (a raw id typed past the CLI's tier validation, or a local/OpenRouter model
# with no catalog entry) is resolved in two more steps before giving up. First
# `max_output_tokens_fallback`, which extends the per-family flats the catalog already documents
# (GPT-5.x's 128,000, Gemini's 65,536, DeepSeek V4's 384,000) to ids not listed yet — the normal
# state of affairs a few months after this catalog was last updated. That table is load-bearing
# rather than a nicety: for a *reasoning* model the chain-of-thought and the visible answer share
# this one budget, and `pipeline/section.py` inverts the resulting ceiling into an *input* budget,
# so a too-small cap doesn't merely truncate — it collapses the section budget and shreds a
# document into many tiny sections, each re-paying the full prompt overhead (#598).
# Only then `_DEFAULT_MAX_OUTPUT_TOKENS`, for a model matching no known family at all: we have no
# idea what a self-hosted model's real cap is, and it could sit far below a frontier model's, so
# this stays at the historical hand-picked figure rather than guessing upward.
_OUTPUT_HEADROOM = 0.10
_DEFAULT_MAX_OUTPUT_TOKENS = 16_000   # unknown-family model: the historical hand-picked value


def _output_envelope(model_id: str) -> int:
    """The output-token envelope for `model_id`, less `_OUTPUT_HEADROOM`: the catalogued
    `max_output_tokens` cap, else the substring-matched family cap, else
    `_DEFAULT_MAX_OUTPUT_TOKENS`. Note the headroom applies to all three — see the constants
    above, and `model_catalog.yaml`'s `max_output_tokens_fallback` comment for which families
    are deliberately excluded from the middle step."""
    cap = (catalog_max_output_tokens(model_id)
           or fallback_max_output_tokens(model_id)
           or _DEFAULT_MAX_OUTPUT_TOKENS)
    return int(cap * (1 - _OUTPUT_HEADROOM))


# Input length at/above which a model bills at a higher rate, less the same 10% headroom
# `_OUTPUT_HEADROOM` takes off `max_output_tokens` — for the same reason, and it is deliberately
# the same figure rather than a second tunable. The headroom absorbs two unknowns at once: the
# catalogued boundaries are read off vendor prose ("roughly double above ~272K") rather than a
# cited rate card, and every call carries prompt scaffolding — schema, extraction instructions,
# record skill, carry-forward entities, harvested candidates — on top of the document text that
# `section.py` sizes. The archive's smallest real input on a metered backend is 7,707-8,773 tokens,
# which is a *floor*: carry-forward and harvested candidates both grow with document length, and
# the field that would measure the real figure per call (`est_prompt_tokens`, #617) is on zero
# archived records because no run has happened since it landed. 10% of 272,000 is 27,200 tokens of
# slack, comfortably over that floor and the only thing standing between a long document and a 2x
# bill (#555, D202).
_LONG_CONTEXT_HEADROOM = _OUTPUT_HEADROOM


def long_context_input_cap(model: str | None, backend: str | None = None) -> int | None:
    """Max real input tokens a single call to `model` may plan for before it crosses into a higher
    pricing tier, or None when the model prices flat at every length (#555).

    `section.py` clamps both the sectioning threshold and the per-section budget to this, so no
    extraction call is ever *planned* past the boundary — which is also what keeps `--estimate-all`
    honest, since it prices every model at one flat rate. Returns None for an uncatalogued id: we
    can't know a self-hosted or OpenRouter model's rate card, and inventing a boundary would shrink
    its sections for no reason. Unlike `_output_envelope` there is no family-fallback table —
    pricing tiers vary within a family (gpt-5.4-mini shares gpt-5.4's tokenizer but not its window,
    and so never approaches the boundary), so a substring guess would be an over-claim."""
    threshold = catalog_long_context_threshold(resolve_model_id(model or DEFAULT_TIER))
    return int(threshold * (1 - _LONG_CONTEXT_HEADROOM)) if threshold else None


_SYSTEM_PROMPT = (
    "You are a precise extraction engine for an investigative-records pipeline. "
    "Respond with ONLY a single JSON object that conforms to the provided schema — "
    "no prose, no markdown fences, no explanation."
)

# Sent on Claude models that ship thinking off by default (`catalog_needs_thinking_param`, #635,
# D206) — `adaptive` lets the model decide per-call whether/how deeply to think rather than
# forcing a fixed budget, and `summarized` display keeps the (billed) thinking output bounded to
# a summary rather than the full trace. Never sent on a continuation retry (I4-adjacent): Anthropic
# rejects an assistant-turn prefill while thinking is on, and `_api_complete_async`'s pagination
# path prefills the truncated partial to resume it — see that function's `prefix is not None`
# branch, which omits this deliberately.
_THINKING_ADAPTIVE = {"type": "adaptive", "display": "summarized"}

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
    if provider == "deepseek":
        # `-thinking` is a Watchdog-only routing marker (D88), not a real catalog id — strip it
        # before the catalog lookup, the same normalization `_wire_max_tokens` does. Without this
        # the thinking variants would read as uncatalogued and reject every level, when thinking
        # mode is the only mode where DeepSeek's effort knob does anything: `reasoning_effort`
        # tunes how deeply the model thinks, so a plain (thinking-disabled) id has no effort to
        # take, and says so here rather than accepting a level the request would then contradict.
        model_id, thinking = _split_deepseek_thinking(model_id)
        if not thinking:
            return set()
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
    from watchdog.cmd.base import CONFIG_FILE
    config = _read_json_or(CONFIG_FILE, {})
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


def tokenizer_ratio(model: str | None, backend: str | None = None,
                    vault: Path | None = None) -> float:
    """Actual-tokens-per-estimated-token multiplier for a stage's model, for provider-aware
    sectioning (#574, remeasured #617). `pipeline/section.py`'s chars/4 `est_tokens` heuristic was
    calibrated against Claude's *old* tokenizer, so on a model whose tokenizer differs it
    mis-counts a document's real token footprint; `section.model_defaults` divides its
    window-derived threshold/budget by this ratio so sectioning respects the model's real context
    window rather than the heuristic's count.

    Every catalogued value is now measured against corpus-v1 rather than quoted from a vendor
    (#617, D198) — `benchmarks/tokenizer_ratio.py`, via each provider's free token counter where
    one exists (Anthropic, Gemini) and a billed differential probe where none does (OpenAI,
    DeepSeek). Four tokenizers cover all fifteen catalogued models: 0.93 Claude through Sonnet
    4.6, 1.28 Claude 4.7+ (Opus 4.8, Sonnet 5 — the vendor's "~30% more" measured at ~37% on our
    text), 0.91 Gemini, 0.80 GPT-5.x, 0.81 DeepSeek V4. All but Claude 4.7+ sit below 1.0, i.e.
    chars/4 over-estimates most real tokenizers on this corpus.

    1.0 (no correction) only for a model with no catalog entry to declare one — an id shipped
    after this catalog was last updated, a self-hosted/OpenRouter model an operator named
    themselves — and for `backend == "local"` for the same reason.

    `vault` (#606 Part B), when given, prefers this vault's own empirically-measured ratio —
    `pipeline.ingest_setup._model_tokenizer_calibration`, computed from real est/actual token
    pairs already recorded per model in this vault's usage history — over the catalog constant,
    since a vault's own documents are better evidence for its own sectioning than a benchmark
    corpus is. Falls back to the catalog value when `vault` is None or the vault doesn't yet have
    enough matching history to calibrate from (a cold-start model, a vault whose history all
    predates #617's `est_prompt_tokens` field, or any caller with no vault context, e.g.
    `watchdog configure`'s preview). Imported locally (not at module level) to avoid a circular
    import — `ingest_setup` already imports `pipeline.section`, which needs to reach this
    function."""
    if backend == "local":
        return 1.0
    model_id = resolve_model_id(model or DEFAULT_TIER)
    if vault is not None:
        from watchdog.pipeline import ingest_setup
        calibrated = ingest_setup._model_tokenizer_calibration(vault, model, backend)
        if calibrated is not None:
            return calibrated
    return catalog_tokenizer_ratio(model_id) or 1.0


class ModelError(RuntimeError):
    """The model could not return schema-valid JSON, or the chosen backend can't run.

    `usage`/`cost_usd`/`attempts`/`model`/`backend`/`auth_mode` (D125) are set only when the
    failure happened after at least one attempt actually called the model — the JSON-validation-
    failure path and the truncation path in `acomplete_json` — so the real spend on a failed call
    isn't lost. A backend/transport exception (raised before any usage exists) leaves these None.

    `truncated` (#540) flags the specific case of an authoritative max-token cut (see the
    `out.get("truncated")` branch below) — a structured signal a caller can act on (e.g. the
    orchestrator's section-level re-split fallback) instead of matching on `last_err`'s message
    text, which is prose meant for a human and free to change (#547 already changed one of these
    strings once).

    `starved` (#558) is set only alongside `truncated` and narrows it further: the max-token cut
    happened because reasoning consumed the whole output budget before an answer was written (or
    outweighed it), not because the answer itself overflowed. The two need different recovery —
    re-splitting the input helps truncation but does nothing for starvation, since a smaller
    input still gets the same reasoning envelope — so a caller must be able to tell them apart
    rather than applying one recovery to both."""

    def __init__(self, message: str, *, usage: dict | None = None, cost_usd: float | None = None,
                attempts: int = 0, model: str | None = None, backend: str | None = None,
                auth_mode: str | None = None, truncated: bool = False, starved: bool = False):
        super().__init__(message)
        self.usage = usage
        self.cost_usd = cost_usd
        self.attempts = attempts
        self.model = model
        self.backend = backend
        self.auth_mode = auth_mode
        self.truncated = truncated
        self.starved = starved


class RateLimitError(RuntimeError):
    """A provider rate/usage limit was hit — e.g. the Claude subscription session limit.

    Deliberately **not** a :class:`ModelError`: a rate limit is a session-wide, transient
    condition, not a per-document failure. It must propagate past extraction's retry +
    sectioning fallback all the way to the orchestrator, which stops the batch cleanly and
    leaves unfinished documents queued for resume (rather than quarantining a good doc).

    `rate_limit` (#563) is the provider's own rate-limit headers off the 429 response itself
    (`_rate_limit_headers`) — the last-seen limit/remaining/reset the provider actually enforced,
    ground truth for the orchestrator's stop message rather than a number reconstructed from our
    own usage records. None when the backend has no such headers (`claude-agent-sdk`'s
    session-limit detection reads a CLI transcript, not an HTTP response)."""

    def __init__(self, message: str, *, resets_at=None, rate_limit: dict | None = None):
        super().__init__(message)
        self.resets_at = resets_at
        self.rate_limit = rate_limit


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
    rate_limit: dict | None = None


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


def _prompt_cache_key(prompt: str | list[dict]) -> str | None:
    """A `prompt_cache_key` for OpenAI's Chat Completions API (#562), derived from the FIRST
    `cache_control` breakpoint in a content-block prompt — None for a plain string or a block
    prompt with no breakpoint at all.

    OpenAI's prompt-caching guide is explicit that this parameter matters: "On GPT-5.6 models
    and later model families, you must set `prompt_cache_key` to use the more reliable matching
    for both implicit and explicit caching," and it should be held "consistently across requests
    that share long, common prefixes." Without it, routing falls back to a hash of roughly the
    first 256 tokens, which is why cache_read_tokens measured 0 across every OpenAI call in a
    fresh benchmark run despite extract/extract-section sharing a ~3,300-token prefix (#562).

    First breakpoint, not last: `build_extract_prompt`/`build_section_prompt` place two
    breakpoints — the first after the skill block (blocks 1+2: run-stable instructions + brief +
    domain skill, genuinely shared across every call in a run that uses that skill), the second
    (added by `prompts._document_block` only when the verification pass is on) after the
    per-document text, which is unique to one call. Keying on the second would give nearly every
    request its own key and scatter exactly the cross-document, same-skill routing that can
    actually produce a hit; keying on the first groups calls by what they truly share.

    This is only a routing hint, not a cache guarantee: OpenAI still matches on the actual prefix
    hash first, so two prompts with the same key but different `response_format` schemas (e.g.
    extract vs. verify, each with its own JSON schema serialized ahead of the system message)
    still cannot share a cache entry — see D181."""
    if not isinstance(prompt, list):
        return None
    for i, block in enumerate(prompt):
        if "cache_control" in block:
            prefix = "\n".join(b.get("text", "") for b in prompt[:i + 1])
            digest = hashlib.sha256(prefix.encode("utf-8")).hexdigest()
            return f"wd-{digest[:24]}"
    return None


def _openai_cache_blocks(prompt: str | list[dict]) -> list[dict] | None:
    """The user message's `content` as OpenAI text parts, with an explicit
    `prompt_cache_breakpoint` on the block that ends the cacheable prefix (#586, D195) — or None
    when this prompt has no breakpoint to mark and should be sent flattened, exactly as before.

    Only for the GPT-5.6 family and later, which changed the caching contract: the service places
    one implicit breakpoint at the latest user message and, per OpenAI's prompt-caching guide,
    "unlike earlier models, it does not automatically fall back to the longest matching unmarked
    prefix before that breakpoint." Since `_openai_complete_async` sends the whole prompt as a
    single user message, that implicit breakpoint lands at the very end of it — so an unmarked
    request can only hit on a byte-identical whole-prompt repeat, and never on the run-stable
    instructions+skill head that `prompts.py` goes to such lengths to keep at the front. That is
    exactly what the archives show: across 440 recorded `gpt-5.6-luna` calls, every hit had
    `input - cache_read == 3` (a whole-prompt replay in a self-consistency benchmark) and not one
    was a partial prefix hit, while `gpt-5.4-mini` — an earlier family, longest-prefix fallback
    still in effect — cached partially all along, `digest` included (#586).

    Marks the FIRST breakpoint only, matching `_prompt_cache_key`'s choice and for the same
    reason: it is the boundary of what calls genuinely share. `prompts._document_block`'s second
    breakpoint (the verification pass, #535) is deliberately left unmarked here — extraction and
    verification each serialize their own structured-output schema ahead of the system message,
    so their prefixes diverge before either reaches the document text and no marking of the
    document block could make them share one (D181). Leaving it unmarked also avoids paying the
    1.25x write on a document's text that nothing will read back.

    A plain-string prompt, or a block prompt with no breakpoint at all, returns None: there is
    nothing to mark, and the caller must not then switch the request into explicit mode — doing
    so would disable the implicit breakpoint and remove even the whole-prompt caching such a call
    has today.

    The `"\\n"` appended to every block but the last is what `_flatten_prompt`'s `"\\n".join`
    puts between them. Adjacent text parts are concatenated with no separator of their own, so
    carrying the newline in the part's own text keeps the rendered prompt byte-identical to the
    flattened form this replaces — this change is meant to alter the request's cache metadata and
    nothing the model actually reads."""
    if not isinstance(prompt, list):
        return None
    marked = None
    for i, block in enumerate(prompt):
        if "cache_control" in block:
            marked = i
            break
    if marked is None:
        return None
    last = len(prompt) - 1
    return [{"type": "text", "text": b.get("text", "") + ("" if i == last else "\n")}
            | ({"prompt_cache_breakpoint": {"mode": "explicit"}} if i == marked else {})
            for i, b in enumerate(prompt)]


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


@lru_cache(maxsize=1)
def _agent_supports_thinking() -> bool:
    """Whether the installed `claude-agent-sdk` accepts `ClaudeAgentOptions(thinking=…)` (#635,
    D206) — same guard shape as `_agent_supports_tools` above, for the same reason: the option
    postdates our SDK floor, so passing it blind would `TypeError` on an older install. Anything
    unexpected answers False, leaving the Agent SDK backend at its pre-#635 behaviour (thinking
    off) rather than risking a crash on every agent-backend call."""
    import dataclasses
    try:
        from claude_agent_sdk import ClaudeAgentOptions
        return any(f.name == "thinking" for f in dataclasses.fields(ClaudeAgentOptions))
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
    if catalog_needs_thinking_param(model) and _agent_supports_thinking():
        opts["thinking"] = _THINKING_ADAPTIVE
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


# Response headers, per provider, that report a rate-limit window's ceiling/remaining/reset
# (#563) — captured on every call (success or 429) as ground truth for what the provider
# actually counted against its per-minute budget, rather than a figure reconstructed from our
# own usage records. Both map to the same three-field shape (`limit_tokens`/`remaining_tokens`/
# `reset_tokens`) since both providers expose a combined tokens-per-minute figure at these names
# — OpenAI's `x-ratelimit-*-tokens` directly, Anthropic's `anthropic-ratelimit-tokens-*` as "the
# most restrictive limit currently in effect" per its docs. `reset` is left as the provider's own
# raw string (OpenAI: a duration like "6m0s"; Anthropic: an RFC 3339 timestamp) — the two formats
# aren't reconciled into one shape, since nothing here needs to compute with it yet.
_OPENAI_RATE_LIMIT_HEADERS = {"limit_tokens": "x-ratelimit-limit-tokens",
                              "remaining_tokens": "x-ratelimit-remaining-tokens",
                              "reset_tokens": "x-ratelimit-reset-tokens"}
_ANTHROPIC_RATE_LIMIT_HEADERS = {"limit_tokens": "anthropic-ratelimit-tokens-limit",
                                 "remaining_tokens": "anthropic-ratelimit-tokens-remaining",
                                 "reset_tokens": "anthropic-ratelimit-tokens-reset"}


def _rate_limit_headers(headers, mapping: dict) -> dict | None:
    """Pull `mapping`'s header names out of a response's `headers` (case-insensitive on a real
    httpx.Headers), normalising `limit_tokens`/`remaining_tokens` to int. None (not an empty
    dict) when none of the three are present, so a caller can test truthiness uniformly."""
    out: dict = {}
    for key, header_name in mapping.items():
        val = headers.get(header_name)
        if val is None:
            continue
        out[key] = int(val) if key != "reset_tokens" else val
    return out or None


def _api_cost(model_id: str, usage) -> float | None:
    rates = _PRICING.get(model_id)
    if not rates:
        return None
    inp, outp, cw, cr = rates

    def g(name):
        return getattr(usage, name, 0) or 0

    # `price_multiplier` is 1.0 for every Claude tier (no Anthropic model prices by the clock) —
    # applied here anyway so the two cost functions stay one implementation of D217's rule rather
    # than one that models it and one that would have to be found and fixed later.
    return (g("input_tokens") * inp + g("output_tokens") * outp
            + g("cache_creation_input_tokens") * cw
            + g("cache_read_input_tokens") * cr) * price_multiplier(model_id)


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
    same reasoning depth applies (I4). `thinking` (#635, D206) is likewise dropped on a
    continuation: Anthropic rejects prefilling the assistant turn while thinking is on, and this
    is exactly what a continuation does. The initial attempt still thinks; only the retry-to-
    resume-a-truncation path loses it, same shape as the `format` drop above. The returned
    `finish_reason` mirrors `stop_reason` so the shell can tell a max-token cut (`max_tokens`)
    from a natural stop.

    Streams rather than calling `.create()` (#598). The Anthropic SDK refuses a *non-streaming*
    request once `3600 * max_tokens / 128_000 > 600`, i.e. `max_tokens > 21,333`
    (`Anthropic._calculate_nonstreaming_timeout`, anthropic 0.116.0) — comfortably below the
    catalogued envelope this now sends (115,200 on Sonnet 4.6). Overriding the client's timeout
    instead of switching to streaming would defeat the reason the guard exists in the first
    place: a non-streaming call holds one silent connection open for the whole generation, and a
    TLS-inspecting corporate proxy between here and the API will drop a long-idle one — trading a
    self-healing truncation (caught by `finish_reason`, recovered by continuation) for a dropped
    connection. Streaming keeps bytes flowing instead, so the connection stays alive for the
    proxy. Do not revert this to `.create()` without re-deriving `max_tokens` back under 21,333.
    `_MAX_CONTINUATIONS` pagination (below) stays as a safety net either way — it just stops
    being a *routine* cost now that the wire ceiling is the model's real cap, not a fraction of
    it."""
    import anthropic

    messages = [{"role": "user", "content": prompt}]
    kwargs: dict = {}
    if prefix is None:
        # `effort` composes with the structured-output `format` inside the one output_config dict.
        output_config = {"format": {"type": "json_schema", "schema": schema}}
        if effort:
            output_config["effort"] = effort
        kwargs["output_config"] = output_config
        if catalog_needs_thinking_param(model_id):
            kwargs["thinking"] = _THINKING_ADAPTIVE
    else:
        # Continuation: prefill the partial (Anthropic rejects a trailing-whitespace assistant
        # turn, so rstrip — JSON ignores inter-token whitespace, so the concatenation is intact).
        messages.append({"role": "assistant", "content": prefix.rstrip()})
        if effort:
            kwargs["output_config"] = {"effort": effort}
    try:
        # `.messages.stream(...)` (#598, superseding `.with_raw_response.create(...)` — see the
        # streaming note above) issues the actual HTTP request inside `__aenter__`, so the
        # RateLimitError catch has to wrap the whole `async with`, not just an inner call.
        # `AsyncMessageStream.response`/`.get_final_message()` (#563) map 1:1 onto the old
        # `.headers`/`.parse()`, so the rate-limit-header capture below is unchanged.
        async with anthropic.AsyncAnthropic(api_key=api_key).messages.stream(
            model=model_id,
            max_tokens=max_tokens,
            system=_SYSTEM_PROMPT,
            messages=messages,
            **kwargs,
        ) as stream:
            resp = await stream.get_final_message()
            headers = stream.response.headers
    except anthropic.RateLimitError as e:   # 429 — surface as the shared typed error
        raise RateLimitError(str(e) or "Claude API rate limit reached",
                             rate_limit=_rate_limit_headers(e.response.headers,
                                                            _ANTHROPIC_RATE_LIMIT_HEADERS)) from e
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
    usage = resp.usage
    usage_dict = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
    usage_dict = _fold_in_anthropic_thinking(usage_dict)
    return {"text": text, "usage": usage_dict, "cost_usd": _api_cost(model_id, usage),
            "finish_reason": getattr(resp, "stop_reason", None),
            "rate_limit": _rate_limit_headers(headers, _ANTHROPIC_RATE_LIMIT_HEADERS)}


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


def cache_write_tokens(usage: dict | None) -> int:
    """Prompt tokens WRITTEN to the provider's context cache, billable (#586, D195). OpenAI
    reports this alongside `cached_tokens` under `prompt_tokens_details`, as `cache_write_tokens`
    — but only for the GPT-5.6 family and later, the only OpenAI models that charge for a write.
    Every earlier family, and every other OpenAI-compatible provider, writes for free and reports
    nothing here, which reads as 0.

    Public (no leading underscore) because `pipeline/orchestrate._record_usage` needs the same
    normalisation for the usage log that `_openai_cost` needs for billing — the two drifting
    apart is exactly what left OpenAI cache reads logged as 0 while `cost_usd` already discounted
    them (#495)."""
    details = (usage or {}).get("prompt_tokens_details") or {}
    return details.get("cache_write_tokens") or 0


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


def _fold_in_anthropic_thinking(usage_dict: dict) -> dict:
    """Break Claude's thinking-token count out into `completion_tokens_details.reasoning_tokens`
    — the OpenAI-shaped key `orchestrate.py`, `cmd/usage.py`, and `telemetry_db.py` already read
    with no provider-specific branch (same target shape `_fold_in_hidden_reasoning` above
    produces for Gemini). The anthropic SDK's `Usage` reports thinking tokens under a
    *different* key, `output_tokens_details.thinking_tokens` — since #635 started sending
    `thinking`, every Claude call's reasoning-token count has been silently dropped here, not
    because the API doesn't report it but because nothing looked in the right place.

    Unlike Gemini's fold, this never adjusts `output_tokens`: Anthropic already includes
    thinking tokens in that total (`_api_cost` bills it correctly and unconditionally), so this
    is purely surfacing an existing total's breakdown, not recovering a gap. Returns `usage_dict`
    unchanged when the model didn't think (thinking off, or off by default with nothing sent) or
    `thinking_tokens` is 0/absent."""
    thinking = (usage_dict.get("output_tokens_details") or {}).get("thinking_tokens")
    if not thinking:
        return usage_dict
    return {**usage_dict, "completion_tokens_details": {"reasoning_tokens": thinking}}


def _openai_cost(model_id: str, usage: dict | None) -> float | None:
    """Cost of one OpenAI-compatible call. Prompt tokens split three ways — read from cache (the
    discounted `cached_input` rate), written to cache, and plain uncached input. All three are
    subsets of `prompt_tokens`, and reads and writes are disjoint: a token served from cache was
    not written by this request.

    A write is only carved out for a model that declares a `cache_write` rate — the GPT-5.6 family
    and later, where the write REPLACES the normal input charge at 1.25x. Everywhere else a write
    carries no additional fee, which is not the same as being free: those tokens are still
    ordinary input and must stay in the uncached remainder at the full input rate. (No earlier
    family reports a write count at all, so this is a guard against a provider that starts to,
    not a live path.)

    Clamping keeps a provider reporting an unexpected combination of counters from driving the
    uncached remainder negative and under-charging the call.

    The whole figure is then scaled by `price_multiplier` (D217) — 1.0 for every model that
    prices flat, and 2.0 for a DeepSeek call landing in one of its peak windows. It multiplies the
    finished cost rather than each rate because the multiplier applies to every column equally,
    which is how DeepSeek states it and the only shape `price_periods` allows."""
    rates = _OPENAI_PRICING.get(model_id)
    if not rates or not usage:
        return None
    inp, outp, cached_rate, write_rate = rates
    prompt = usage.get("prompt_tokens", 0) or 0
    cached = min(_cached_input_tokens(usage), prompt)   # cache hits are a subset of prompt tokens
    written = min(cache_write_tokens(usage), prompt - cached) if write_rate else 0
    return ((prompt - cached - written) * inp
            + cached * cached_rate
            + written * write_rate
            + (usage.get("completion_tokens", 0) or 0) * outp) * price_multiplier(model_id)


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
    content-block prompt is flattened to plain text — but the first breakpoint's position (before
    flattening) is still reused to derive a `prompt_cache_key` for the real OpenAI endpoint, per
    `_prompt_cache_key` (#562).

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

    # Explicit cache breakpoints, GPT-5.6 family and later only (#586, D195). `content` becomes an
    # array of text parts so the prefix boundary can be marked on the wire; `prompt_cache_options.
    # mode = "explicit"` then suppresses the implicit end-of-prompt breakpoint, which would
    # otherwise keep writing the whole prompt — document text included — at 1.25x for a cache
    # nothing ever reads back. Gated to the real OpenAI endpoint for the same reason
    # `prompt_cache_key` is: a DeepSeek/Gemini-compat/local/OpenRouter server isn't guaranteed to
    # ignore body fields it doesn't recognize. The schema-in-prompt append above can't collide with
    # this — it only runs when `response_format` isn't `json_schema`, and the real OpenAI endpoint
    # always uses `json_schema` (`_openai_response_format`).
    cache_blocks = _openai_cache_blocks(prompt) if is_openai and catalog_cache_breakpoints(model_id) else None
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": cache_blocks if cache_blocks is not None else user_content},
    ]
    body = {"model": model_id, "messages": messages}
    if cache_blocks is not None:
        body["prompt_cache_options"] = {"mode": "explicit"}
    # Gated to the real OpenAI endpoint (#562): only OpenAI documents `prompt_cache_key`, and
    # DeepSeek/Gemini-compat/local/OpenRouter servers aren't guaranteed to ignore an unknown
    # body field.
    if is_openai:
        cache_key = _prompt_cache_key(prompt)
        if cache_key is not None:
            body["prompt_cache_key"] = cache_key
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
    # `x-ratelimit-*-tokens` (#563) — captured on every response regardless of backend, not just
    # the real OpenAI endpoint: unlike `prompt_cache_key` (D181), sending these header *names*
    # isn't provider-specific, and `_rate_limit_headers` already returns None when a backend
    # doesn't send them, so a DeepSeek/Gemini/local/OpenRouter call just carries no rate_limit
    # rather than a misattributed one.
    rate_limit = _rate_limit_headers(resp.headers, _OPENAI_RATE_LIMIT_HEADERS)
    if resp.status_code == 429:
        raise RateLimitError(f"{base_url} rate limit reached", rate_limit=rate_limit)
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
            "finish_reason": finish, "rate_limit": rate_limit}


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


def _wire_max_tokens(backend: str, model_id: str) -> int:
    """The `max_tokens` ceiling sent to the provider on the wire for `backend`/`model_id` — task-
    and effort-independent (#598).

    `max_tokens` is a runaway guard, not a reservation: billing is on actual output, so there is
    no cost to sending the model's real envelope on every call regardless of what the call is for
    or how hard it's asked to think. The old per-task base (`_TASK_MAX_TOKENS`, 16,000) plus a
    per-provider reasoning "reserve" bolted on top of it (DeepSeek thinking #337, OpenAI reasoning
    models #354, Gemini #541) existed only because that base was itself a hardcoded guess shared
    with chain-of-thought — every provider catalogued here draws reasoning/thinking tokens from
    the SAME output budget as the visible answer (see `model_catalog.yaml`'s `max_output_tokens`
    comment), so a ceiling sized only for the visible JSON starves reasoning the moment it grows.
    Deriving the ceiling from the model's real catalogued cap removes that starvation mode at its
    root, for every backend, instead of layering a per-provider patch on top of it.

    Reasoning volume itself is not a function of effort alone — archived telemetry fit against
    input length shows it also scales steeply with *input size* (up to ~2.3 tokens of thinking
    per input token at high effort, roughly 12x the visible-output rate) — but that no longer
    matters anywhere: the wire ceiling is the model's own envelope regardless, and as of #555
    nothing reasons about how much of it reasoning will consume. `pipeline/section.py`'s
    *input*-side sizing used to invert that estimate into a section budget; it no longer consults
    this function at all (see D202), so this envelope is now purely a runaway guard on the wire."""
    if backend == "deepseek":
        # `-thinking` is a Watchdog-only routing marker (D88), not a real catalog id — strip it
        # before consulting the catalog, the same normalization `_openai_complete_async` already
        # does before its own catalog/pricing lookups, so a thinking-mode call gets the model's
        # real 384,000 cap rather than falling through to the uncatalogued default.
        model_id, _ = _split_deepseek_thinking(model_id)
    return _output_envelope(model_id)


def output_ceiling_for_sectioning(backend: str | None, model: str | None) -> int | None:
    """The per-call output-token ceiling that sectioning must keep a document under — or None
    when there is nothing to protect (#343). None is returned for the agent SDK (no enforced
    ceiling), for the prefill-continuation backends (claude-api, deepseek — pagination grows the
    output past the cap), and for an unresolved backend (`None` routes to a Claude backend, both
    of which are None-returning). openai, gemini, local, and openrouter (#380) return a real
    number: they enforce max_tokens yet can't continue, so a document whose estimated output
    would exceed the ceiling must be sectioned up front rather than truncating and relying on the
    reactive fallback.

    The ceiling no longer varies by task or effort (#598) — it is the same per-model wire envelope
    `_wire_max_tokens` sends on every call for this backend/model, so a caller sizing input against
    predicted output uses the one real number that output actually has to fit."""
    meta = _BACKEND_META.get(backend)
    if meta is None or not meta.enforces_max_tokens or meta.supports_continuation:
        return None
    model_id = resolve_model_id(model or DEFAULT_TIER)
    return _wire_max_tokens(backend, model_id)


async def _complete_with_pagination(backend_fn, backend: str, prompt, model_id: str, schema: dict,
                                    api_key: str | None, max_tokens: int, effort_arg,
                                    task: str | None = None) -> dict:
    """Call the backend, then continue a max-token-truncated response by prefilling its partial
    output and concatenating, until a natural stop or the continuation guard (#343). Returns the
    assembled `{text, usage, cost_usd, truncated, rate_limit}`; `truncated` is True only if the
    output was still capped after the last allowed round (or the backend can't continue), so the
    caller never accepts a partial extraction. `rate_limit` (#563) isn't accumulated like `usage`
    — it's a point-in-time snapshot off whichever call ran last, which is the freshest one."""
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
            "truncated": _is_truncated(out.get("finish_reason")),
            "rate_limit": out.get("rate_limit")}


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

    max_tokens = _wire_max_tokens(chosen, model_id)

    start = time.monotonic()
    total_cost = 0.0
    last_err = "no attempts made"
    attempts = 0
    pruned_all: list[str] = []
    agg_usage: dict | None = None
    last_rate_limit: dict | None = None   # #563: freshest snapshot, not accumulated like usage
    was_truncated = False   # #540: whether the final failure was a truncation specifically
    was_starved = False     # #558: narrows was_truncated to the reasoning-starvation shape
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
        if out.get("rate_limit") is not None:
            last_rate_limit = out["rate_limit"]

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
                was_starved = True
            elif reasoning > visible:
                last_err = (f"the model spent {reasoning:,} of its output budget on internal "
                            f"reasoning, leaving only {visible:,} tokens of answer before the "
                            "max-token ceiling cut it off — try a lower extractor_effort")
                was_starved = True
            else:
                last_err = "output truncated at the model's max-token ceiling"
            was_truncated = True
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
                    pruned=pruned_all or None, rate_limit=last_rate_limit,
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
        model=model_id, backend=chosen, auth_mode=auth_mode, truncated=was_truncated,
        starved=was_starved)


def complete_json(**kwargs) -> ModelResult:
    """Sync wrapper around :func:`acomplete_json` for non-async callers and tests."""
    return asyncio.run(acomplete_json(**kwargs))
