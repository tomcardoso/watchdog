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
    (stored via Settings → Models & keys), independent of the Claude auth mode (#125).

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
import contextvars
import hashlib
import json
import re
import time
import weakref
from dataclasses import dataclass
from functools import lru_cache, partial
from pathlib import Path

from watchdog import fixture_capture
from watchdog import auth
from watchdog.model_catalog import (
    _OPENAI_PRICING,
    _PRICING,
    catalog_cache_breakpoints,
    catalog_context_window,
    catalog_effort_levels,
    catalog_has_reasoning,
    catalog_is_reasoning,
    catalog_long_context_threshold,
    catalog_max_output_tokens,
    catalog_needs_thinking_param,
    catalog_tokenizer_ratio,
    fallback_context_window,
    fallback_is_reasoning,
    _split_deepseek_thinking,
    fallback_max_output_tokens,
    long_context_card,
    price_multiplier,
    rate_card,
    resolve_model_id,
)
from watchdog import config as user_config
from watchdog import defaults

# The model a helper assumes when its caller names none: the pipeline's own default
# (`defaults.EXTRACTOR_MODEL`), so there is one default, not two. Every run names its models
# (`ops.ingest.resolve_plan`); this is for callers outside a run, and tests.
DEFAULT_TIER = defaults.EXTRACTOR_MODEL

# The output-token envelope sent on the wire (D197): one per-model number from the catalogued
# `max_output_tokens`, less `_OUTPUT_HEADROOM`. An uncatalogued id falls back first to
# `max_output_tokens_fallback` (per-family caps: GPT-5.x 128,000, Gemini 65,536, DeepSeek V4
# 384,000), which matters for reasoning models whose thinking shares this budget, then to
# `_DEFAULT_MAX_OUTPUT_TOKENS` for a model matching no family — kept conservative, since a
# self-hosted model's real cap may be far lower.
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


# Input length at which a model starts billing at a higher rate, less the same 10% headroom as the
# output cap (D202). The headroom covers boundaries read from vendor prose rather than rate cards,
# and the prompt scaffolding (schema, instructions, skill, carry-forward, candidates) that rides on
# top of the document text sectioning sizes.
_LONG_CONTEXT_HEADROOM = _OUTPUT_HEADROOM


def long_context_input_cap(model: str | None, backend: str | None = None) -> int | None:
    """Max real input tokens a single call to `model` may plan for before it crosses into a higher
    pricing tier, or None when the model prices flat at every length (#555).

    `section.model_defaults` caps its text-sized threshold and budget at this, and
    `chunking.prompt_budget_chars` a post-processing chunk's data, the 10% left over covering the
    instructions that ride on top. Extraction plans the whole prompt under the line itself
    (`section.long_prompt_line`, D370). It is a cost control, not the pricing: a call that crosses
    the line anyway is priced at the upper card (`rate_card`, D366). Returns None for an uncatalogued id: we
    can't know a self-hosted or OpenRouter model's rate card, and inventing a boundary would shrink
    its sections for no reason. Unlike `_output_envelope` there is no family-fallback table —
    pricing tiers vary within a family (Haiku 5.5 is the only Claude model with one), so a
    substring guess would be an over-claim."""
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

# Saving model reasoning (D368): set for the duration of one `acomplete_json` call when its caller
# asked for the reasoning (the `save_reasoning` setting, or a benchmark run). A backend reads it to
# ask for and return what it can at no extra cost; with it unset, every request is byte-for-byte
# what it was before. A context variable, not a parameter, so the backends' shared signature (and
# every test stub of it) is unchanged, and concurrent calls under asyncio each see their own value.
_CAPTURE_REASONING: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "watchdog_capture_reasoning", default=False)


def _capturing() -> bool:
    return _CAPTURE_REASONING.get()


def _claude_thinking_param(model_id: str) -> dict | None:
    """The `thinking` value a Claude call sends: adaptive with a summarized display on a model
    whose thinking is off by default (#635), and, only while reasoning is being saved, on a model
    whose thinking is on by default too. Current Claude models return thinking blocks with empty
    text unless `display: "summarized"` is asked for; the display changes what comes back, not
    what the model does or what it is billed (D368)."""
    if catalog_needs_thinking_param(model_id):
        return _THINKING_ADAPTIVE
    if _capturing() and catalog_has_reasoning(model_id):
        return _THINKING_ADAPTIVE
    return None


def _join_reasoning(parts) -> str | None:
    text = "\n\n".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
    return text or None

# Reasoning-effort levels for the per-stage `effort` knob (D36) — an abstract intent the
# pipeline passes down. `model_catalog.yaml`'s `effort_levels` is the single source of truth for
# which of these a given model actually accepts (#518, D161) — real per-model coverage, not a
# per-provider flag: it varies across providers (Gemini stops at `high`) and has varied within
# one. `_resolve_effort` below rejects an unsupported request loudly
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
    for a known id; an uncatalogued id (a raw id typed in Settings, or a
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
    return set()   # anthropic (rarely uncatalogued), deepseek, local, openrouter


def _resolve_effort(provider: str, model_id: str, effort: str | None) -> str | None:
    """Translate the abstract effort intent into the provider's native value, or None to omit.

    `model_catalog.yaml`'s `effort_levels` (via `_effort_levels` above) is the single source of
    truth for whether `provider`/`model_id` accepts `effort` at all (#518, D161) — a request for
    an unsupported level fails loud with a clear error rather than being silently dropped or sent
    as a request the API would reject.

    A requested level is always sent, `high` on Claude included (D310): `high` used to be omitted
    as "the model's own default" (D36), but Haiku 5.5 and Opus 5.5 default to `medium`, so a `high`
    request there ran at `medium`."""
    if not effort:
        return None
    if effort not in _effort_levels(provider, model_id):
        raise ModelError(f"effort '{effort}' is not supported for '{model_id}' on provider '{provider}'")
    return effort


def effort_supported(backend: str | None, model: str | None, effort: str) -> bool:
    """Whether `effort` is a level `backend`/`model` actually accepts (#518) — for a caller that
    wants to check support *before* calling, rather than let `acomplete_json` raise. Exists for
    exactly one case: `ops/ingest.py` applies `medium` as `extractor_effort`'s implicit default
    (D26) only when the resolved extractor model supports it, so routing extraction to a model
    with no effort control (e.g. Haiku) doesn't turn a setting the user never touched into a hard
    failure — an *explicit* effort (a run option or a setting) still goes straight to
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
# (a Settings key) lets an operator state their model's real window; absent that, this
# conservative default keeps sectioning aggressive rather than risking an overrun on an unknown model.
_LOCAL_DEFAULT_CONTEXT_WINDOW = 8_000


def _configured_local_context_window() -> int | None:
    value = user_config.get("local_context_window")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def context_window(model: str | None, backend: str | None = None) -> int:
    """Token context window for a stage's model, for provider-aware sectioning (#321).

    `model` may be a tier name (haiku/sonnet), a raw provider id (`deepseek-flash`,
    `gpt-6-luna`), or None for the default tier — resolved first, then looked up in the model
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
    """Real tokens per chars/4 estimated token for a stage's model (D180, D198).

    Sectioning divides its window-derived threshold and budget by this, so it respects the model's
    real context window. Catalogue values are measured against corpus-v1
    (`benchmarks/tokenizer_ratio.py`); an uncatalogued id, or `backend == "local"`, gets 1.0. When
    `vault` is given, this vault's own measured ratio (`ingest_setup._model_tokenizer_calibration`)
    is preferred once it has enough history (D190). Imported locally to avoid a circular import."""
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
    """The model could not return schema-valid JSON, or the backend can't run.

    `usage`, `cost_usd`, `attempts`, `model`, `backend` and `auth_mode` are set when at least one
    attempt reached the model, so failed spend is still recorded (D125). `truncated` marks an
    authoritative max-token cut; `starved` narrows it to reasoning having used the output budget —
    re-splitting the input fixes the first but not the second. `refused` marks the provider
    declining the request (a safety stop): the same input is refused again, so it is never
    retried."""

    def __init__(self, message: str, *, usage: dict | None = None, cost_usd: float | None = None,
                attempts: int = 0, model: str | None = None, backend: str | None = None,
                auth_mode: str | None = None, truncated: bool = False, starved: bool = False,
                key_label: str | None = None, stop_reason: str | None = None,
                reasoning: str | None = None, refused: bool = False):
        super().__init__(message)
        self.key_label = key_label
        self.stop_reason = stop_reason
        self.reasoning = reasoning
        self.usage = usage
        self.cost_usd = cost_usd
        self.attempts = attempts
        self.model = model
        self.backend = backend
        self.auth_mode = auth_mode
        self.truncated = truncated
        self.starved = starved
        self.refused = refused


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


class ProviderAuthError(RuntimeError):
    """The provider refused the credentials or the account can't pay — a bad or revoked key
    (401/403), an exhausted credit balance or quota. Like :class:`RateLimitError` this is
    session-wide, not per-document: every later call would fail the same way, so the
    orchestrator stops the run and leaves documents queued instead of quarantining each one.
    Unlike a rate limit it never clears by waiting, so `--wait` doesn't sleep on it."""


class BillingRouteError(ProviderAuthError):
    """A call would not be billed the way the reporter chose (D406, D408): the base of
    :class:`MeteredBillingError`, and raised directly when a call made with an Anthropic key would
    be paid some other way. Halting, like any :class:`ProviderAuthError`."""


class MeteredBillingError(BillingRouteError):
    """A call meant for the Claude subscription would be, or was being, billed per token (#872,
    D406): Claude Code reported an API key as its credential, the environment names one, or the
    subscription's limit was reached and Claude Code reported that extra usage was paying. A
    :class:`ProviderAuthError`, so every stage halts on it as on a refused key.

    `overage` marks the one case where the call had already started on extra usage and may have
    been charged: the orchestrator records it in Usage, with `model`, `backend`, `auth_mode` and
    `usage` (the credential source) filled in by `_acomplete_json`. Its tokens are unknown, since
    the call ended before Claude Code reported them."""

    def __init__(self, message: str, *, overage: bool = False, usage: dict | None = None):
        super().__init__(message)
        self.overage = overage
        self.usage = usage
        self.model = self.backend = self.auth_mode = self.key_label = None


# Where the bundled Claude Code says its credential came from (`apiKeySource` in its init
# message): each of these is an API key, billed per token. `none` is a claude.ai login, a
# bearer token or a cloud provider, which the environment check below tells apart (#872).
METERED_KEY_SOURCES = frozenset({"ANTHROPIC_API_KEY", "apiKeyHelper", "/login managed key"})

# Once a subscription call has been found metered, every later one in this process is refused
# without running (D406): a job is its own worker process, so this lasts exactly one run, and it
# stops post-processing stages that degrade on a failed call from each spending again.
_metered_stop: str | None = None


def _stop_metered(message: str, **kw) -> MeteredBillingError:
    global _metered_stop
    _metered_stop = message
    return MeteredBillingError(message, **kw)


def _overage_in_use(info) -> bool:
    """Whether a rate-limit event says extra usage is paying for the request (#872). The CLI sends
    `isUsingOverage`/`overageInUse` in the event's raw form; a limit of type `overage` that is not
    a refusal means the same. A refused request is a rate limit, handled apart."""
    raw = getattr(info, "raw", None)
    raw = raw if isinstance(raw, dict) else {}
    if raw.get("isUsingOverage") is True or raw.get("overageInUse") is True:
        return True
    return (getattr(info, "rate_limit_type", None) == "overage"
            and getattr(info, "status", None) != "rejected")


_KEY_SOURCE_NOTE = ("Watchdog is set to use your Claude subscription, but Claude Code on this "
                    "computer is using an API key ({source}), which is billed per token. Watchdog "
                    "stopped before the call ran. Remove the key, or sign in again with your "
                    "Claude account under Settings → Models & keys.")
_WRONG_KEY_NOTE = ("Claude Code did not use the Anthropic key Watchdog gave it, but another "
                   "credential ({source}), so this call would not be billed to the key you chose. "
                   "Watchdog stopped before the call ran. Remove the other credential, or ask for "
                   "help with this message.")
_OVERAGE_NOTE = ("Your Claude subscription's usage limit has been reached, and Claude Code reports "
                 "that extra usage, which is billed, is now paying for calls. Watchdog stopped the "
                 "run; unfinished documents stay queued. Turn off extra usage in your Claude "
                 "account's settings, or wait until the limit has reset before adding documents "
                 "again: until then, the next run stops again at its first paid call. To let runs "
                 "carry on and pay for extra usage instead, turn on Allow extra usage under "
                 "Settings → Claude subscription.")


# Every failure a single model call can end in. Post-ingest stages catch all three and degrade
# (skip synthesis, keep the timeline untouched) rather than crash a run whose documents are saved.
CALL_FAILURES = (ModelError, RateLimitError, ProviderAuthError)

# Body text that marks an error as an account problem rather than a bad request or a rate limit:
# Anthropic's "credit balance is too low", OpenAI's `insufficient_quota` code (sent as a 429,
# which would otherwise read as a rate limit and have `--wait` sleep on it forever). Only explicit
# account signals (D259): ordinary per-minute rate limits also say "quota" and "billing" — Gemini's
# 429 reads "You exceeded your current quota, please check your plan and billing details", OpenAI's
# free-tier 429 links to its billing page — and must stay rate limits, which `--wait` sleeps on.
_BILLING_HINTS = ("credit balance", "insufficient_quota", "payment required")


def _looks_like_billing(*texts: str) -> bool:
    blob = " ".join(t for t in texts if t).lower()
    return any(h in blob for h in _BILLING_HINTS)


# Substrings that mark a rate/usage-limit error in a result, notice, or exception text.
_RATE_LIMIT_HINTS = ("rate_limit", "rate limit", "session limit", "usage limit",
                     "too many requests")


def _looks_like_rate_limit(api_status, *texts: str) -> bool:
    """True if an HTTP 429, or any of the given texts reads like a rate-limit notice."""
    if api_status == 429:
        return True
    blob = " ".join(t for t in texts if t).lower()
    return any(h in blob for h in _RATE_LIMIT_HINTS)


_AUTH_HINTS = ("invalid api key", "authentication_error", "invalid x-api-key", "/login",
               "oauth token has expired")


def _scrub(text: str, api_key: str | None) -> str:
    """`text` with the key that was sent masked out. A provider's error body is kept in the
    message for its reason, and the message reaches job logs, failure records and the app; a
    proxy or self-hosted server that echoes the Authorization header back must not carry the key
    with it (D295)."""
    if not api_key or not text or api_key not in text:
        return text
    from watchdog.auth import mask
    return text.replace(api_key, mask(api_key))


def _scrubbed(e: Exception, api_key: str | None) -> Exception:
    """`e` with the key masked out of its message, for errors raised past this module."""
    if api_key and any(isinstance(a, str) and api_key in a for a in e.args):
        e.args = tuple(_scrub(a, api_key) if isinstance(a, str) else a for a in e.args)
    return e


def _looks_like_auth_failure(api_status, *texts: str) -> bool:
    """True for a 401/403, a billing refusal, or CLI text reporting a bad or missing login."""
    if api_status in (401, 403):
        return True
    blob = " ".join(t for t in texts if t).lower()
    return any(h in blob for h in _AUTH_HINTS) or _looks_like_billing(blob)


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
    key_label: str | None = None    # the labelled key that paid (#690), never the key itself
    # The provider's own stop reason for the call's last request (`end_turn`, `max_tokens`,
    # `stop`, `length`, ...), and its reasoning text when it was asked for and returned (D368).
    stop_reason: str | None = None
    reasoning: str | None = None


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


def _strip_optional_nulls(value, schema):
    """`_strip_none`, reading the authored schema: a `null` is dropped only where the schema does
    not require the key. A key the schema requires and allows to be null (an extraction's
    `morgue_entity_id`, null when the document shows no subject, D393) keeps its null, where
    dropping it would fail validation as a missing required property (D400)."""
    if isinstance(value, dict):
        props = schema.get("properties") if isinstance(schema, dict) else None
        required = set(schema.get("required") or []) if isinstance(schema, dict) else set()
        out = {}
        for k, v in value.items():
            if v is None and k not in required:
                continue
            out[k] = _strip_optional_nulls(v, (props or {}).get(k))
        return out
    if isinstance(value, list):
        items = schema.get("items") if isinstance(schema, dict) else None
        return [_strip_optional_nulls(v, items) for v in value]
    return value


def _denormalize_strict_json(text: str, schema: dict | None = None) -> str:
    """Undo `_to_strict_schema`'s null-for-omitted-field convention on a raw strict-mode response
    (OpenAI's, and Claude's since D400), before the shared `_extract_json`/`_validate`/
    `_prune_unknown` path sees it — so that path keeps validating against `schemas.py`'s actual
    (non-strict) schema unmodified, exactly as it does for every other backend. With `schema`
    (the authored one) a null on a key that schema requires is kept. If the response isn't valid
    JSON, the text is returned unchanged and the ordinary invalid-JSON retry path handles it."""
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    return json.dumps(_strip_optional_nulls(parsed, schema) if schema is not None
                      else _strip_none(parsed))


def _claude_wire_schema(schema: dict) -> dict:
    """The schema a Claude request sends (D400, #804): `_to_strict_schema`'s every-property-required
    variant, with a nullable `enum` written as `anyOf` (Anthropic rejects an enum under a
    `["string", "null"]` type as an invalid schema).

    Claude's enforced output takes an object's properties only in their declared order. With an
    optional property the model can skip it, write a later one, and then has no way back: on one
    replayed extraction, 7 of 20 answers that skipped a fact's `page` ended after one or two facts
    or ran to the output limit, and none of 20 did once the property was required
    (`benchmarks/2026-11-10-enforced-json-replay-results.md`). Requiring every property removes
    the skip everywhere; "no value" is an explicit `null`, which `_denormalize_strict_json` drops
    from the answer again."""
    def enums(node):
        if isinstance(node, list):
            return [enums(x) for x in node]
        if not isinstance(node, dict):
            return node
        node = {k: enums(v) for k, v in node.items()}
        t = node.get("type")
        if "enum" in node and isinstance(t, list) and "null" in t:
            rest = [x for x in t if x != "null"]
            base = {**node, "type": rest[0] if len(rest) == 1 else rest,
                    "enum": [e for e in node["enum"] if e is not None]}
            return {"anyOf": [base, {"type": "null"}]}
        return node
    return enums(_to_strict_schema(schema))


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
    """A `prompt_cache_key` for OpenAI's Chat Completions API, derived from the FIRST
    `cache_control` breakpoint in a content-block prompt — None for a plain string or a block
    prompt with no breakpoint. Required by OpenAI for reliable cache matching on GPT-5.6+ (D181).
    Keyed on the first (run-stable instructions+skill), not the second breakpoint that
    `prompts._document_block` adds for the verify pass, since keying on the per-document one
    would scatter exactly the cross-document routing that can produce a hit. Only a routing
    hint, not a guarantee: two prompts with the same key but different `response_format` schemas
    (extract vs. verify) still can't share a cache entry (D181)."""
    if not isinstance(prompt, list):
        return None
    for i, block in enumerate(prompt):
        if "cache_control" in block:
            prefix = "\n".join(b.get("text", "") for b in prompt[:i + 1])
            digest = hashlib.sha256(prefix.encode("utf-8")).hexdigest()
            return f"wd-{digest[:24]}"
    return None


def _openai_cache_blocks(prompt: str | list[dict]) -> list[dict] | None:
    """The user message as OpenAI text parts, with `prompt_cache_breakpoint` on the block that ends the
    cacheable prefix (D195), or None when there is no breakpoint and the prompt goes flattened.

    GPT-5.6+ only: that family's implicit breakpoint sits at the end of the whole prompt, so without
    a marker only a byte-identical repeat would hit. Marks the first breakpoint only; the verify
    pass's second one diverges from extraction anyway (D181), and marking it would pay a write for
    nothing. The "\\n" between blocks keeps the text byte-identical to the flattened form."""
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
                       effort: str | None = None, subscription: bool = False) -> dict:
    """One single-turn Claude Code call. With `subscription`, the call must run on the Claude
    login (#872, D406): a key reported as the credential stops it before the request is made, and
    extra usage reported while it runs stops it there; either raises :class:`MeteredBillingError`
    and refuses every later subscription call in this process. The credential source Claude Code
    reported is returned in `usage["key_source"]`, so the run's record says what was checked."""
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
    if effort:                  # only set when a level was asked for (D36, D310)
        opts["effort"] = effort
    thinking = _claude_thinking_param(model)
    if thinking and _agent_supports_thinking():
        opts["thinking"] = thinking
    options = ClaudeAgentOptions(**opts)
    out = {"text": "", "cost_usd": None, "usage": None}
    thoughts: list[str] = []     # thinking blocks, kept only while reasoning is saved (D368)
    api_status = None        # ResultMessage.api_error_status (e.g. 429) since CLI v2.1.110
    is_error = False
    notice = ""              # human-readable limit notice, if the CLI emitted one as text
    rejected = False         # the CLI emitted a rate-limit event with status "rejected"
    resets_at = None
    key_source = None        # `apiKeySource` from the init message (#872)
    overage_paid = False     # extra usage paid for this call, as the reporter allowed (D410)
    try:
        async for message in query(prompt=prompt, options=options):
            name = type(message).__name__
            if name == "SystemMessage" and getattr(message, "subtype", None) == "init":
                data = getattr(message, "data", None)
                source = data.get("apiKeySource") if isinstance(data, dict) else None
                if isinstance(source, str):
                    key_source = source
                    if subscription and source in METERED_KEY_SOURCES:
                        raise _stop_metered(_KEY_SOURCE_NOTE.format(source=source))
                    if (not subscription and env and env.get("ANTHROPIC_API_KEY")
                            and source != "ANTHROPIC_API_KEY"):
                        # The key Watchdog passed is not what Claude Code will bill (D408).
                        raise BillingRouteError(_WRONG_KEY_NOTE.format(source=source))
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
                if isinstance(getattr(message, "stop_reason", None), str):
                    out["stop_reason"] = message.stop_reason   # recorded only: never read as a truncation
            elif name in ("RateLimitEvent", "RateLimitInfo"):
                info = getattr(message, "rate_limit_info", message)
                if subscription and _overage_in_use(info):
                    if not auth.extra_usage_allowed():
                        raise _stop_metered(_OVERAGE_NOTE, overage=True,
                                            usage={"key_source": key_source} if key_source else None)
                    overage_paid = True     # the reporter allowed it: carry on, recorded (D410)
                if getattr(info, "status", None) == "rejected":
                    rejected = True
                    resets_at = getattr(info, "resets_at", None)
            # The session-limit message ("You've hit your session limit · resets …")
            # arrives as a text block; capture it so we can show the real reason.
            for block in getattr(message, "content", None) or []:
                if _capturing() and isinstance(getattr(block, "thinking", None), str):
                    thoughts.append(block.thinking)
                t = getattr(block, "text", "") or ""
                if t and any(h in t.lower() for h in _RATE_LIMIT_HINTS):
                    notice = t.strip()
    except BillingRouteError:
        raise
    except Exception as e:
        # The SDK raises on a CLI error result via two paths — a ProcessError rewritten to
        # "Claude Code returned an error result: …", or a {type:error} stream message. If it
        # was a rate limit, raise a typed, actionable error instead of an opaque one.
        # `out["text"]` is scanned only when the result was flagged `is_error`: then it is the
        # CLI's own error string ("Invalid API key · Please run /login"), which the SDK's
        # rewritten exception leaves out. Otherwise it can be partial model output, i.e.
        # document content, which routinely says "billing" (D259). An explicit rate-limit
        # rejection event wins; then a billing refusal, then a rate limit (its notice can mention a login page, and misreading it as an
        # auth failure would stop the run instead of letting `--wait` sleep), then other auth.
        err_text = out["text"] if is_error else ""
        if rejected:
            raise RateLimitError(notice or "Claude rate/usage limit reached", resets_at=resets_at) from e
        if _looks_like_billing(str(e), err_text):
            raise ProviderAuthError(f"Claude account can't pay for this call: {err_text or e}") from e
        if _looks_like_rate_limit(api_status, notice, str(e), err_text):
            raise RateLimitError(notice or "Claude rate/usage limit reached", resets_at=resets_at) from e
        if _looks_like_auth_failure(api_status, str(e), err_text):
            raise ProviderAuthError(f"Claude could not authenticate: {err_text or e}") from e
        raise
    if not rejected and is_error and _looks_like_billing(out["text"]):
        raise ProviderAuthError(f"Claude account can't pay for this call: {out['text']}")
    if rejected or (is_error and _looks_like_rate_limit(api_status, notice, out["text"])):
        raise RateLimitError(notice or "Claude rate/usage limit reached", resets_at=resets_at)
    if is_error and _looks_like_auth_failure(api_status, out["text"]):
        raise ProviderAuthError(f"Claude could not authenticate: {out['text']}")
    if _capturing():
        out["reasoning"] = _join_reasoning(thoughts)
    if key_source is not None:
        out["usage"] = {**(out["usage"] or {}), "key_source": key_source}
    if overage_paid:
        out["usage"] = {**(out["usage"] or {}), "overage": True}
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
    subscription = not api_key
    if subscription:
        # Checked before every call, not only when a run starts (#872): a refused run stays
        # refused, and a credential in the environment is used by Claude Code ahead of the login.
        if _metered_stop:
            raise MeteredBillingError(_metered_stop)
        # Claude Code must report a claude.ai sign-in (D407): its `apiKeySource` says `none` for a
        # Console account's sign-in too, which bills the Console account. Kept a minute, so a run's
        # calls share one check; a subprocess, so it runs off the event loop.
        problem, method = await asyncio.to_thread(auth.subscription_check)
        if problem:
            raise _stop_metered(problem)
    else:
        conflict = auth.key_env_conflict()      # nothing may outrank the key (D408)
        if conflict:
            raise BillingRouteError(auth.key_env_message(conflict))
    full = f"{_flatten_prompt(prompt)}\n\nReturn JSON matching this schema:\n{json.dumps(schema)}"
    env = {"ANTHROPIC_API_KEY": api_key} if api_key else None
    try:
        out = await _agent_query(full, model_id, env, effort, subscription=subscription)
    except MeteredBillingError as e:
        if subscription and e.usage is not None:
            e.usage["auth_method"] = method
        raise
    if subscription:
        out["usage"] = {**(out.get("usage") or {}), "auth_method": method}
    return out


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


def _usage_get(usage, name: str):
    """A field of a usage report that may be the SDK's object or its dict form."""
    if isinstance(usage, dict):
        return usage.get(name)
    return getattr(usage, name, None)


def _anthropic_prompt_tokens(usage) -> int:
    """A Claude request's prompt length as Anthropic prices it (D366): "all of its input tokens,
    including cache reads and cache writes" — `input_tokens` alone is only the uncached part."""
    return sum(_usage_get(usage, k) or 0 for k in
               ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


def prompt_tokens(model_id: str, usage) -> int:
    """One call's prompt length as its provider prices it (D366): a Claude call's uncached input
    plus cache writes and reads, anyone else's `prompt_tokens` (which already includes the cached
    part). What the long-prompt guard after an extraction call compares with the line (D370)."""
    if not usage:
        return 0
    if model_id in _PRICING:
        return _anthropic_prompt_tokens(usage)
    return _usage_get(usage, "prompt_tokens") or _usage_get(usage, "input_tokens") or 0


# A 1-hour cache write costs 2x the input rate of whichever card applies, where a 5-minute write
# costs the card's `cache_write` (1.25x) — Anthropic's pricing page, "Prompt caching", fetched
# 2026-10-31. Watchdog only writes 5-minute entries; the split is read from `cache_creation` so a
# 1-hour write is never billed at the cheaper rate if one ever appears.
_ANTHROPIC_1H_WRITE = 2.0


def _api_cost(model_id: str, usage) -> float | None:
    """Cost of one Claude Messages API call (D366). The rate card is chosen by the request's whole
    prompt length (`_anthropic_prompt_tokens`), so a Haiku 5.5 request over 100,000 tokens is billed
    entirely at the long-prompt card, cache reads and writes included. Thinking tokens are already
    inside `output_tokens` and billed as output. `usage` is the SDK's `Usage` or its dict form."""
    rates = rate_card(model_id, _anthropic_prompt_tokens(usage))
    if not rates or model_id not in _PRICING:
        return None
    inp, outp, cw, cr = rates

    def g(name):
        return _usage_get(usage, name) or 0

    written = g("cache_creation_input_tokens")
    hour = min(_usage_get(_usage_get(usage, "cache_creation") or {}, "ephemeral_1h_input_tokens")
               or 0, written)
    # `price_multiplier` is 1.0 for every Claude tier (no Anthropic model prices by the clock) —
    # applied here anyway so the two cost functions stay one implementation of D217's rule rather
    # than one that models it and one that would have to be found and fixed later.
    return (g("input_tokens") * inp + g("output_tokens") * outp
            + (written - hour) * cw + hour * inp * _ANTHROPIC_1H_WRITE
            + g("cache_read_input_tokens") * cr) * price_multiplier(model_id)


def _batch_cost(model_id: str, usage) -> float | None:
    """Message Batches pricing is a flat 50% off every per-token rate of whichever card the
    request's prompt length selects, cache read/write included (#214, D366) — so batch cost is
    the normal API cost at half price."""
    cost = _api_cost(model_id, usage)
    return cost * 0.5 if cost is not None else None


# One Anthropic client per event loop and key, so concurrent calls share a connection pool instead
# of each opening its own. Keyed weakly by loop: a client's connections belong to the loop that
# opened them, and each `asyncio.run` gets a new one.
_ANTHROPIC_CLIENTS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _anthropic_client(anthropic_mod, api_key: str):
    loop = asyncio.get_running_loop()
    per_loop = _ANTHROPIC_CLIENTS.setdefault(loop, {})
    key = (anthropic_mod.AsyncAnthropic, api_key)
    if key not in per_loop:
        per_loop[key] = anthropic_mod.AsyncAnthropic(api_key=api_key)
    return per_loop[key]


async def _api_complete_async(prompt: str | list[dict], model_id: str, schema: dict,
                              api_key: str | None, max_tokens: int,
                              effort: str | None = None, prefix: str | None = None) -> dict:
    """Claude Messages API backend with structured output. `prompt` may be a string or content blocks
    carrying a `cache_control` breakpoint.

    `prefix` continues a truncated response by prefilling it as the assistant turn; structured-output
    enforcement and `thinking` are dropped on that call (constrained decoding can't resume
    mid-object, and Anthropic rejects prefill with thinking on), and the shell validates the joined
    result. `finish_reason` mirrors `stop_reason`.

    Uses streaming: the SDK refuses a non-streaming request once `max_tokens` exceeds about 21,333,
    well under the catalogued envelope, and a long silent non-streaming connection is what
    TLS-inspecting proxies drop. Don't switch back to `.create()` without bringing `max_tokens` under
    that limit."""
    import anthropic

    messages = [{"role": "user", "content": prompt}]
    kwargs: dict = {}
    if prefix is None:
        # `effort` composes with the structured-output `format` inside the one output_config dict.
        output_config = {"format": {"type": "json_schema", "schema": _claude_wire_schema(schema)}}
        if effort:
            output_config["effort"] = effort
        kwargs["output_config"] = output_config
        thinking = _claude_thinking_param(model_id)
        if thinking:
            kwargs["thinking"] = thinking
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
        async with _anthropic_client(anthropic, api_key).messages.stream(
            model=model_id,
            max_tokens=max_tokens,
            system=_SYSTEM_PROMPT,
            messages=messages,
            **kwargs,
        ) as stream:
            resp = await stream.get_final_message()
            headers = stream.response.headers
    except anthropic.RateLimitError as e:   # 429 — surface as the shared typed error
        if _looks_like_billing(str(e)):
            raise ProviderAuthError(f"Anthropic account can't pay for this call: {e}") from e
        raise RateLimitError(str(e) or "Claude API rate limit reached",
                             rate_limit=_rate_limit_headers(e.response.headers,
                                                            _ANTHROPIC_RATE_LIMIT_HEADERS)) from e
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
        raise ProviderAuthError(f"Anthropic rejected the API key: {e}") from e
    except anthropic.APIStatusError as e:
        # 402 is Anthropic's `billing_error`, whatever its message says — as on the
        # OpenAI-compatible path.
        if getattr(e, "status_code", None) == 402 or _looks_like_billing(str(e)):
            raise ProviderAuthError(f"Anthropic account can't pay for this call: {e}") from e
        raise
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
    usage = resp.usage
    usage_dict = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
    usage_dict = _fold_in_anthropic_thinking(usage_dict)
    out = {"text": text, "usage": usage_dict, "cost_usd": _api_cost(model_id, usage),
           "finish_reason": getattr(resp, "stop_reason", None),
           "rate_limit": _rate_limit_headers(headers, _ANTHROPIC_RATE_LIMIT_HEADERS)}
    if _capturing():
        out["reasoning"] = _join_reasoning(getattr(b, "thinking", None) for b in resp.content
                                           if getattr(b, "type", None) == "thinking")
    return out


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
    Usage reasoning note, and `acomplete_json`'s truncation diagnostic all read it with
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
    — the OpenAI-shaped key `orchestrate.py`, `usage.py`, and `telemetry_db.py` already read
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
    which is how DeepSeek states it and the only shape `price_periods` allows.

    The rate card is chosen by `prompt_tokens`, which on this API already includes the cached
    part (D366): a GPT-6 Luna or GPT-5.6 Luna request over 272,000 prompt tokens is billed
    entirely at the long-prompt card. Reasoning tokens are inside `completion_tokens` (Gemini's
    are folded in first by `_fold_in_hidden_reasoning`) and billed as output."""
    if not usage or model_id not in _OPENAI_PRICING:
        return None
    prompt = usage.get("prompt_tokens", 0) or 0
    inp, outp, cached_rate, write_rate = rate_card(model_id, prompt)
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


def reprice_long_prompt_record(record: dict) -> float | None:
    """What a stored usage record (`orchestrate._record_usage`'s shape) costs under D366's
    long-prompt card, or None when the record is not one D366 reprices: an uncatalogued model, a
    model priced flat at every length, a subscription call (its figure is Claude Code's, not
    ours), or a call whose prompt stayed at or under the line.

    A record sums its attempts' tokens (`acomplete_json` merges usage across retries), so the
    prompt length is judged per attempt, `attempts` copies of an even share — exact when each
    retry sent the same prompt, which a retry does. Continuation pages cannot be told apart in a
    record and are judged the same way. Batch calls (`-batch` backends) are priced at half."""
    model_id, backend = record.get("model") or "", record.get("backend") or ""
    tier = long_context_card(model_id)
    if tier is None or backend == "claude-agent-sdk" or record.get("auth_mode") == "subscription":
        return None
    attempts = max(1, int(record.get("attempts") or 1))
    inp = record.get("input_tokens") or 0
    read = record.get("cache_read_tokens") or 0
    write = record.get("cache_write_tokens") or 0
    out = record.get("output_tokens") or 0
    anthropic = model_id in _PRICING
    prompt = (inp + read + write) if anthropic else inp
    if prompt / attempts <= tier[0]:
        return None
    if anthropic:
        usage = {"input_tokens": inp / attempts, "output_tokens": out / attempts,
                 "cache_creation_input_tokens": write / attempts,
                 "cache_read_input_tokens": read / attempts}
        one = _api_cost(model_id, usage)
    else:
        usage = {"prompt_tokens": inp / attempts, "completion_tokens": out / attempts,
                 "prompt_tokens_details": {"cached_tokens": read / attempts,
                                           "cache_write_tokens": write / attempts}}
        one = _openai_cost(model_id, usage)
    if one is None:
        return None
    cost = one * attempts
    return cost * 0.5 if backend.endswith("-batch") else cost


# DeepSeek V4 collapsed thinking/non-thinking into a single model id switched by a request param
# (the old deepseek-chat/deepseek-reasoner split is deprecated). The backend strips the `-thinking`
# marker (`_split_deepseek_thinking`, owned by `model_catalog` since the catalog has to normalize it
# away before every lookup), uses the bare id for the request + cost lookup, and sends DeepSeek's
# explicit toggle. The provider default is thinking-ENABLED, so we always send the toggle to pin the
# intended mode. Toggle shape (OpenAI format): {"thinking": {"type": "enabled"|"disabled"}}. Docs:
# https://api-docs.deepseek.com/guides/thinking_mode  (D88)

# Transient-failure retry for the OpenAI-compatible backends (#354). The Anthropic SDK retries
# 429/5xx internally (2 attempts, backoff); this httpx path had none, so a single transient
# 502/529 from a provider failed the document outright — `acomplete_json` retries only on invalid
# JSON, not backend exceptions. 5xx only: a 429 must keep raising RateLimitError immediately so
# the orchestrator stops the batch cleanly instead of hammering a limited provider.
_TRANSIENT_RETRIES = 2
_TRANSIENT_BACKOFF_S = 2.0


def _openai_response_format(base_url: str, schema: dict) -> dict:
    """The `response_format` for an OpenAI-compatible request (D98, D151, D139).

    Gemini gets `json_schema` with the schema as authored (its engine accepts optional fields).
    OpenAI gets strict `json_schema` against `_to_strict_schema`'s derived variant, since strict mode
    requires every property. DeepSeek, `local` and `openrouter` get `json_object` with the schema in
    the prompt: DeepSeek's JSON mode takes no schema, and the other two route to arbitrary models."""
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
    """OpenAI-compatible Chat Completions backend (OpenAI, DeepSeek, Gemini, local, OpenRouter — chosen
    by `base_url`).

    Structured output via `_openai_response_format`, validated locally either way. `effort` arrives
    resolved and is sent as `reasoning_effort`. Content-block prompts are flattened, with cache hints
    where the provider supports them (`_openai_cache_blocks`, `prompt_cache_key`). DeepSeek's
    `-thinking` id suffix becomes its thinking toggle. `prefix` continues a truncated response — only
    DeepSeek supports it. `finish_reason` distinguishes a max-token cut from a natural stop."""
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
    # ignored (defeating the max_tokens ceiling for local/openrouter) or rejected outright.
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
        # Bounded retry on 5xx and on transport failures (a dropped connection, a connect timeout — not a read timeout) —
        # parity with the Anthropic SDK's built-in transient retry (#354). 429 is excluded: it
        # raises RateLimitError below on the first response.
        for attempt in range(_TRANSIENT_RETRIES + 1):
            try:
                resp = await client.post(url, headers=headers, json=body)
            except httpx.ReadTimeout:
                # The provider had the whole request and was generating for the full client
                # timeout: a retry would run (and likely bill) the same long call again (D259).
                raise
            except httpx.TransportError:
                if attempt == _TRANSIENT_RETRIES:
                    raise
                await asyncio.sleep(_TRANSIENT_BACKOFF_S * (attempt + 1))
                continue
            if resp.status_code < 500 or attempt == _TRANSIENT_RETRIES:
                break
            await asyncio.sleep(_TRANSIENT_BACKOFF_S * (attempt + 1))
    # `x-ratelimit-*-tokens` (#563) — captured on every response regardless of backend, not just
    # the real OpenAI endpoint: unlike `prompt_cache_key` (D181), sending these header *names*
    # isn't provider-specific, and `_rate_limit_headers` already returns None when a backend
    # doesn't send them, so a DeepSeek/Gemini/local/OpenRouter call just carries no rate_limit
    # rather than a misattributed one.
    rate_limit = _rate_limit_headers(resp.headers, _OPENAI_RATE_LIMIT_HEADERS)
    if resp.status_code >= 400:
        # The provider's own JSON error body is the only place the real reason lives ("invalid
        # model", "context length exceeded", "insufficient_quota") — keep it in the message.
        detail = _scrub((resp.text or "").strip(), api_key)[:500]
        if resp.status_code in (401, 403):
            raise ProviderAuthError(f"{base_url} rejected the API key (HTTP {resp.status_code}): {detail}")
        if resp.status_code == 402 or _looks_like_billing(detail):
            raise ProviderAuthError(f"{base_url} account can't pay for this call "
                                    f"(HTTP {resp.status_code}): {detail}")
        if resp.status_code == 429:
            raise RateLimitError(f"{base_url} rate limit reached", rate_limit=rate_limit)
        raise ModelError(f"{base_url} returned HTTP {resp.status_code}: {detail}")
    data = resp.json()
    choices = data.get("choices") or []
    text = (choices[0].get("message", {}).get("content") or "") if choices else ""
    if is_openai and response_format["type"] == "json_schema":
        text = _denormalize_strict_json(text, schema)
    finish = choices[0].get("finish_reason") if choices else None
    usage = data.get("usage")
    if is_gemini:   # thinking tokens arrive only as a total_tokens gap (#547) — fold them in
        usage = _fold_in_hidden_reasoning(usage)
    out = {"text": text, "usage": usage, "cost_usd": _openai_cost(model_id, usage),
           "finish_reason": finish, "rate_limit": rate_limit}
    if _capturing() and choices:
        # DeepSeek's thinking mode (and some OpenAI-compatible servers) return the reasoning as
        # `reasoning_content` beside the answer, unasked and at no extra cost (D368). OpenAI's and
        # Gemini's Chat Completions return none without a different request, so nothing is
        # captured for them.
        out["reasoning"] = _join_reasoning([(choices[0].get("message") or {}).get("reasoning_content")])
    return out


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
    batch_only: bool = False             # selectable but never dispatched via acomplete_json (#214)


# backend name → its registration metadata. `local`/`openrouter` (#380) are OpenAI-compatible too,
# but their base URL is user-supplied (the `local_base_url` setting/`openrouter_base_url`,
# or Settings → Models & keys) rather than one of the fixed URLs above — `dynamic_base_url` marks them so
# `acomplete_json` partial-applies the resolved URL per call instead of at import time.
_BACKEND_META: dict[str, _BackendMeta] = {
    "claude-api":       _BackendMeta(provider="anthropic", supports_continuation=True),
    "claude-agent-sdk": _BackendMeta(provider="anthropic"),
    # claude-batch/openai-batch are real, selectable backends (settings validation, auth resolution)
    # but are never dispatched through the single-call acomplete_json path — each provider's
    # Batch API is submit-many/poll/collect, handled entirely by orchestrate._run_batch +
    # pipeline.batch_extract (#214, #530). `batch_only` exists only to turn a misrouted call (e.g.
    # a classifier/finalizer stage accidentally set to a batch backend) into a clear error instead
    # of the generic "unknown backend" one.
    "claude-batch":     _BackendMeta(provider="anthropic", batch_only=True),
    "openai":     _BackendMeta(provider="openai",   base_url=_OPENAI_BASE["openai"]),
    "openai-batch":     _BackendMeta(provider="openai", batch_only=True),
    "deepseek":   _BackendMeta(provider="deepseek", base_url=_OPENAI_BASE["deepseek"],
                                supports_continuation=True),
    "gemini":     _BackendMeta(provider="gemini",   base_url=_OPENAI_BASE["gemini"]),
    "local":      _BackendMeta(provider="local",      dynamic_base_url=True),
    "openrouter": _BackendMeta(provider="openrouter", dynamic_base_url=True),
}

# Selectable backend names (public — Settings validates a stage's `backend:model` against this).
BACKENDS = tuple(_BACKEND_META)
# Backends that take a Claude tier name (haiku/sonnet); the rest take a raw provider id.
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
        try:
            resolved = auth.resolve_auth()
        except auth.KeyChoiceError as e:   # the investigation's chosen key is gone (D290)
            raise ModelError(str(e)) from None
        if resolved["mode"] == "none":
            raise ModelError(resolved.get("reason", "no auth configured" + " — set it up in Settings"))
        auth_mode = resolved["mode"]
        api_key = resolved.get("key")           # None in subscription mode
        if api_key:
            # Nothing in the environment may take priority over the chosen key, or send it to
            # another host, on either Claude backend (D408): the Anthropic SDK reads
            # ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN itself, and Claude Code more.
            conflict = auth.key_env_conflict()
            if conflict:
                raise BillingRouteError(auth.key_env_message(conflict))
        if chosen is None:
            chosen = "claude-agent-sdk" if auth_mode == "subscription" else "claude-api"
        if chosen == "claude-api" and not api_key:
            raise ModelError(
                "the claude-api backend needs an API key, but auth mode is "
                f"'{auth_mode}' — "
                + "switch to api-key mode in Settings, or use the claude-agent-sdk backend")
        return chosen, provider, api_key, auth_mode, None

    base_url = None
    if meta.dynamic_base_url:
        base_url = auth.get_base_url(provider)
        if not base_url:
            raise ModelError(
                f"the {chosen} backend needs a base URL — "
                + "set it in Settings (e.g. http://localhost:11434/v1)")

    try:
        api_key = auth.get_api_key(provider)
    except auth.KeyChoiceError as e:       # never fall back to another account (D290)
        raise ModelError(str(e)) from None
    if auth.provider_requires_key(provider) and not api_key:
        raise ModelError(f"the {chosen} backend needs an API key — "
                         + "add one in Settings")
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


# Stop reasons that mean the provider declined to answer: Anthropic's `refusal` (also what the
# Claude Code backend reports) and the OpenAI-compatible `content_filter`.
_REFUSAL_FINISH = {"refusal", "content_filter"}


def _is_refused(stop_reason) -> bool:
    """Whether a backend's stop reason marks the model declining the request (a safety stop)."""
    return bool(stop_reason) and str(stop_reason) in _REFUSAL_FINISH


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
    """The `max_tokens` sent on the wire for `backend`/`model_id`: the catalogued output cap under
    headroom, independent of task and effort (D197).

    A runaway guard, not a reservation — billing is on actual output. Using the model's real cap
    means reasoning tokens, which share the output budget, can't starve the visible answer.
    Sectioning does not consult it (D202)."""
    if backend == "deepseek":
        # `-thinking` is a Watchdog-only routing marker (D88), not a real catalog id — strip it
        # before consulting the catalog, the same normalization `_openai_complete_async` already
        # does before its own catalog/pricing lookups, so a thinking-mode call gets the model's
        # real 384,000 cap rather than falling through to the uncatalogued default.
        model_id, _ = _split_deepseek_thinking(model_id)
    return _output_envelope(model_id)


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
    reasoning = [out.get("reasoning")]
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
        reasoning.append(out.get("reasoning"))
    if backend == "claude-api":
        # The request asked for every property, null where there is no value (D400); a continued
        # answer is only whole JSON once joined, so the nulls are dropped here, not per call.
        text = _denormalize_strict_json(text, schema)
    return {"text": text, "usage": usage, "cost_usd": cost,
            "truncated": _is_truncated(out.get("finish_reason")),
            "rate_limit": out.get("rate_limit"),
            "stop_reason": out.get("finish_reason") or out.get("stop_reason"),
            "reasoning": _join_reasoning(reasoning)}


# ── public entry point ────────────────────────────────────────────────────────

async def acomplete_json(*, task: str, prompt: str | list[dict], schema: dict, model: str | None = None,
                         backend: str | None = None, max_retries: int = 1,
                         effort: str | None = None, capture_reasoning: bool = False) -> ModelResult:
    """See `_acomplete_json`. `capture_reasoning` (D368) asks the backend for the model's
    reasoning where it can return it at no extra cost, and puts it on the result (or the error);
    off, nothing sent changes."""
    token = _CAPTURE_REASONING.set(bool(capture_reasoning))
    try:
        return await _acomplete_json(task=task, prompt=prompt, schema=schema, model=model,
                                     backend=backend, max_retries=max_retries, effort=effort)
    finally:
        _CAPTURE_REASONING.reset(token)


async def _acomplete_json(*, task: str, prompt: str | list[dict], schema: dict, model: str | None = None,
                          backend: str | None = None, max_retries: int = 1,
                          effort: str | None = None) -> ModelResult:
    """Get schema-valid JSON for a reasoning task (async — the orchestrator awaits this).

    `prompt` is normally a string; a caller may instead pass a list of Anthropic content
    blocks with a `cache_control` breakpoint (A1) to make part of the prompt cache-eligible —
    only `claude-api` uses it natively, other backends flatten it to text (`_flatten_prompt`).
    `model` may be a tier name (haiku/sonnet) or a raw model id; omit it for the
    per-task default. `backend` forces a backend ('claude-api', 'claude-agent-sdk',
    'openai', 'deepseek', 'local', 'openrouter'); omit it to route by auth mode. `effort`
    (`low`/`medium`/`high`) is an abstract reasoning-depth intent — each provider maps it to its
    own native control or ignores it (D36, #125). On invalid/unparseable output the call retries
    on the **same** model (up to `max_retries` extra attempts) — never escalating — then raises.
    """
    chosen, provider, api_key, auth_mode, base_url = _resolve_backend_auth(backend)
    key_label = auth.label_for_key(provider, api_key)
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
    was_refused = False     # the provider declined the request: never retried
    was_starved = False     # #558: narrows was_truncated to the reasoning-starvation shape
    stop_reason = None      # the last request's own stop reason, recorded per call (D368)
    thoughts: list = []     # every attempt's reasoning, when asked for (D368)
    for _ in range(max_retries + 1):
        attempts += 1
        try:
            out = await _complete_with_pagination(backend_fn, chosen, prompt, model_id, schema,
                                                  api_key, max_tokens, effort_arg, task)
        except MeteredBillingError as e:
            # Say which call it was, so a call stopped on extra usage can be recorded (#872).
            e.model, e.backend, e.auth_mode, e.key_label = model_id, chosen, auth_mode, key_label
            raise _scrubbed(e, api_key)
        except (RateLimitError, ModelError, ProviderAuthError) as e:
            raise _scrubbed(e, api_key)
        except Exception as e:                  # any backend/transport failure → typed error
            raise ModelError(_scrub(f"{chosen} backend error: {e}", api_key)) from e
        if out.get("cost_usd"):
            total_cost += out["cost_usd"]
        # Accumulate usage across every attempt (not just the one that ends up succeeding) so a
        # retried call's telemetry reflects everything actually spent (#412) — a failed attempt's
        # tokens were real spend, not free information.
        agg_usage = _merge_usage(agg_usage, out.get("usage"))
        if out.get("rate_limit") is not None:
            last_rate_limit = out["rate_limit"]
        if out.get("stop_reason") is not None:
            stop_reason = str(out["stop_reason"])
        thoughts.append(out.get("reasoning"))

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

        if _is_refused(out.get("stop_reason")):
            # The provider declined this input (stop reason `refusal`, no answer). The same
            # input is declined again, so a retry only spends input tokens and the empty text
            # would be reported as malformed JSON — stop and say what happened.
            last_err = (f"the model declined to answer this request (stop reason: "
                        f"{stop_reason}) — retrying the same input will not help")
            was_refused = True
            fixture_capture.capture("refusal", backend=chosen, model_id=model_id, task=task,
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
                    pruned=pruned_all or None, rate_limit=last_rate_limit, key_label=key_label,
                    stop_reason=stop_reason, reasoning=_join_reasoning(thoughts),
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
        starved=was_starved, key_label=key_label, stop_reason=stop_reason,
        reasoning=_join_reasoning(thoughts), refused=was_refused)

