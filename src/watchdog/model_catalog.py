"""Single source of truth for every model Watchdog knows about — ids, pricing, context
windows, and OpenAI reasoning capability — loaded from model_catalog.yaml. No
watchdog-internal imports: both model_client.py and cmd/base.py depend on this without
risking an import cycle (model_client.py already imports watchdog.cmd.auth, which
imports watchdog.cmd.base).
"""

import importlib.resources
from datetime import UTC, datetime, time

import yaml


def _load() -> dict:
    text = (importlib.resources.files("watchdog") / "model_catalog.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


_CATALOG = _load()
_MODELS = {m["id"]: m for m in _CATALOG["models"]}

# Tier name -> API model id. `tier` in the YAML is either a scalar (one alias) or a list (a model
# with more than one selectable name, e.g. Sonnet 4.6's `[sonnet, sonnet-4.6]`) — normalize both
# shapes to a list of aliases before building the lookup.
def _tier_aliases(entry: dict) -> list[str]:
    tier = entry.get("tier")
    if tier is None:
        return []
    return [tier] if isinstance(tier, str) else list(tier)


_MODEL_IDS = {alias: m["id"] for m in _CATALOG["models"] for alias in _tier_aliases(m)}

# DeepSeek V4 collapsed thinking/non-thinking into a single model id switched by a request param,
# and Watchdog keeps the choice inside the model token — `deepseek-v4-flash` (non-thinking) vs
# `deepseek-v4-flash-thinking` — so it rides the existing `[backend:]model` grammar with no extra
# provider-specific knob (D88). The marker is Watchdog's own grammar, not a real catalog id, so it
# lives here, with the catalog that has to normalize it away before every lookup; `model_client.py`
# imports both names rather than keeping a second copy that could drift. See that module's
# `_openai_complete_async` for the wire toggle this resolves to.
_DEEPSEEK_THINKING_SUFFIX = "-thinking"


def _split_deepseek_thinking(model_id: str) -> tuple[str, bool]:
    """(bare model id, thinking?) from a DeepSeek model token — strips a `-thinking` marker.
    Non-thinking is the default (bare id), so extraction stays cheap unless thinking is opted in."""
    if model_id.endswith(_DEEPSEEK_THINKING_SUFFIX):
        return model_id[: -len(_DEEPSEEK_THINKING_SUFFIX)], True
    return model_id, False

# Claude pricing, USD/token: (input, output, cache_write_5m, cache_read).
_PRICING = {
    m["id"]: (float(m["input"]), float(m["output"]), float(m["cache_write"]), float(m["cache_read"]))
    for m in _CATALOG["models"] if m["provider"] == "anthropic"
}

# OpenAI-compatible pricing, USD/token: (input, output, cached_input, cache_write). `cache_write`
# is 0.0 for the vast majority — every provider and family that writes to its prompt cache for
# free. Only the GPT-5.6 family and later charge for a write (1.25x uncached input, #586/D195),
# and only those entries declare the field.
_OPENAI_PRICING = {
    m["id"]: (float(m["input"]), float(m["output"]), float(m["cached_input"]),
              float(m.get("cache_write", 0.0)))
    for m in _CATALOG["models"] if m["provider"] != "anthropic"
}

# (substring/prefix, value) pairs, most-specific-first — see model_catalog.yaml's own comments.
_CONTEXT_WINDOW_FALLBACK = [(row[0], int(row[1])) for row in _CATALOG["context_window_fallback"]]
_MAX_OUTPUT_TOKENS_FALLBACK = [(row[0], int(row[1]))
                               for row in _CATALOG["max_output_tokens_fallback"]]
_REASONING_FALLBACK = [(row[0], bool(row[1])) for row in _CATALOG["reasoning_fallback"]]


def resolve_model_id(model: str) -> str:
    """Tier name (haiku/sonnet/opus) -> API model id, or a raw id returned as-is."""
    return _MODEL_IDS.get(model, model)


def display_name(model_id: str) -> str:
    """Pretty display form of a model id, for the CLI/usage logging — e.g. 'gpt-5.6-terra' ->
    'GPT-5.6 Terra'. Falls back to the raw id itself for anything uncatalogued (a local/OpenRouter
    model, or an old id no longer in the catalog) rather than raising or guessing."""
    entry = _MODELS.get(model_id.lower())
    return entry["name"] if entry and "name" in entry else model_id


def catalog_context_window(model_id: str) -> int | None:
    """Explicit context window for a known catalog model id, or None if uncatalogued."""
    entry = _MODELS.get(model_id.lower())
    return entry.get("context_window") if entry else None


def catalog_max_output_tokens(model_id: str) -> int | None:
    """Explicit single-response output cap for a known catalog model id (#598), or None if
    uncatalogued."""
    entry = _MODELS.get(model_id.lower())
    return entry.get("max_output_tokens") if entry else None


def catalog_long_context_threshold(model_id: str) -> int | None:
    """Real-token input length at/above which this model bills at a higher rate (#555), or None
    when it prices flat at every length. See `model_catalog.yaml`'s `long_context_threshold`
    comment — including the caveat that these figures still need a vendor citation."""
    entry = _MODELS.get(model_id.lower())
    return entry.get("long_context_threshold") if entry else None


def fallback_max_output_tokens(model_id: str) -> int | None:
    """Substring-matched single-response output cap for an uncatalogued model (#598), or None (no
    match). Extends the per-family flats the catalog already documents to ids not listed yet — see
    `model_catalog.yaml`'s `max_output_tokens_fallback` comment for which families are deliberately
    left out."""
    mid = model_id.lower()
    for marker, cap in _MAX_OUTPUT_TOKENS_FALLBACK:
        if marker in mid:
            return cap
    return None


def fallback_context_window(model_id: str) -> int | None:
    """Substring-matched context window for an uncatalogued model, or None (no match)."""
    mid = model_id.lower()
    for marker, window in _CONTEXT_WINDOW_FALLBACK:
        if marker in mid:
            return window
    return None


def catalog_is_reasoning(model_id: str) -> bool | None:
    """Explicit reasoning flag for a known catalog model id, or None if uncatalogued."""
    entry = _MODELS.get(model_id.lower())
    return bool(entry["reasoning"]) if entry and "reasoning" in entry else None


def fallback_is_reasoning(model_id: str) -> bool:
    """Prefix-matched reasoning-model guess for an uncatalogued model (default: chat)."""
    mid = model_id.lower()
    for prefix, reasoning in _REASONING_FALLBACK:
        if mid.startswith(prefix):
            return reasoning
    return False


def catalog_needs_thinking_param(model_id: str) -> bool:
    """Whether `model_id` ships with Anthropic's extended thinking OFF by default, so
    `model_client.py` must send `thinking` explicitly to turn it on (#635, D206). False (the
    correct default in both directions) for a model that's already on by default, and for every
    uncatalogued or non-Claude id — never consulted there."""
    entry = _MODELS.get(model_id.lower())
    return bool(entry.get("thinking", False)) if entry else False


# Claude tiers that ship extended thinking on by default (no `thinking` catalog flag needed —
# see that field's own comment in model_catalog.yaml). Kept private: catalog_has_reasoning is
# the only thing that should need this list.
_THINKING_BY_DEFAULT_TIERS = {"sonnet-5", "opus-5"}


def catalog_has_reasoning(model_id: str) -> bool:
    """Whether `model_id` has a private reasoning channel it can use before committing to its
    visible answer — an OpenAI reasoning model (`reasoning: true`) or a Claude model with
    `thinking` engaged, whether sent explicitly (`thinking: true`, #635) or on by default (Sonnet
    5, Opus 5). Feeds the extraction scaffold's branch (#570): a model with a channel gets a
    compact nudge into it, one without gets the explicit step-by-step form written into the
    visible completion instead, via `document.plan` (see extract_instructions.md).

    A DeepSeek `-thinking` id counts too (D217): thinking mode returns `reasoning_content`
    alongside `content`, which is a private channel by any reading, and the marker has to be
    stripped for the entry to be found at all. Before that, every DeepSeek thinking call was handed
    the explicit `document.plan` scaffold built for models with *no* channel — paying for a visible
    plan while also thinking privately. The provider check keeps the stripping honest: a
    non-DeepSeek id that merely ends in `-thinking` is not granted a channel by its name.

    False — the conservative default — for every other catalogued model (Haiku, DeepSeek's plain
    ids, Gemini) and for any uncatalogued id: never assume a channel that isn't confirmed."""
    bare, deepseek_thinking = _split_deepseek_thinking(model_id)
    entry = _MODELS.get(bare.lower())
    if not entry:
        return False
    if deepseek_thinking and entry.get("provider") == "deepseek":
        return True
    if entry.get("reasoning") or entry.get("thinking"):
        return True
    tier = entry.get("tier")
    tiers = {tier} if isinstance(tier, str) else set(tier or [])
    return bool(tiers & _THINKING_BY_DEFAULT_TIERS)


def catalog_cache_breakpoints(model_id: str) -> bool:
    """Whether a model needs EXPLICIT prompt-cache breakpoints on the wire (#586, D195) — true
    for the OpenAI GPT-5.6 family and later, false for everything else, including every
    uncatalogued id.

    False is the correct default in both directions, which is why this returns a bool rather than
    the `None`-for-uncatalogued shape the other capability lookups use: an earlier-family OpenAI
    model caches by longest-prefix fallback with no breakpoint sent, and a local/OpenRouter model
    behind an arbitrary runner may reject an unknown body field outright."""
    entry = _MODELS.get(model_id.lower())
    return bool(entry.get("cache_breakpoints", False)) if entry else False


def catalog_effort_levels(model_id: str) -> set[str] | None:
    """Explicit set of `effort` levels a known catalog model id accepts (#518), or None if
    uncatalogued. Source of truth for effort capability across every provider — real per-model
    coverage (e.g. Claude Sonnet 4.6 takes `max` but not `xhigh`), not a per-provider flag. A
    catalogued model with no `effort_levels` field (Claude Haiku, DeepSeek) has none at all,
    which `entry.get(..., [])` already expresses without a special case."""
    entry = _MODELS.get(model_id.lower())
    return set(entry.get("effort_levels", [])) if entry else None


def catalog_tokenizer_ratio(model_id: str) -> float | None:
    """Explicit tokenizer-ratio multiplier for a known catalog model id (#574), or None if
    uncatalogued or the model doesn't declare one — the vast majority, which behave exactly as
    the chars/4 `est_tokens` heuristic (`pipeline/section.py`) already assumes. Only Claude 4.7+
    models (Opus 4.8, Sonnet 5) currently declare a value; see `model_catalog.yaml`'s
    `tokenizer_ratio` field comment for the source and rationale. Callers wanting a safe default
    for "no declared ratio" should treat None as 1.0 (`model_client.tokenizer_ratio` does this)."""
    entry = _MODELS.get(model_id.lower())
    return float(entry["tokenizer_ratio"]) if entry and "tokenizer_ratio" in entry else None


def _parse_hhmm(value: str) -> time:
    """`"01:00"` -> `time(1, 0)`. Raises on anything else, at import: a price window nobody can
    parse would otherwise silently price every call at the base rate, which is the direction that
    under-charges."""
    hh, _, mm = str(value).partition(":")
    return time(int(hh), int(mm))


# model id -> [(start, end, multiplier), ...], UTC, in declaration order. Empty for the vast
# majority of models, which price the same around the clock — see model_catalog.yaml's
# `price_periods` comment for the shape and why the base rates are the cheap ones.
_PRICE_PERIODS = {
    m["id"]: [(_parse_hhmm(w["from"]), _parse_hhmm(w["to"]), float(w["multiplier"]))
              for w in m["price_periods"]]
    for m in _CATALOG["models"] if m.get("price_periods")
}


def _in_window(now: time, start: time, end: time) -> bool:
    """Half-open `[start, end)` membership, wrapping midnight when `start > end` (`22:00`->
    `02:00` is 22:00-23:59 plus 00:00-01:59). A window with `start == end` matches nothing rather
    than everything: an empty span is the reading that can't accidentally double every rate."""
    if start < end:
        return start <= now < end
    if start > end:
        return now >= start or now < end
    return False


def price_multiplier(model_id: str, at: datetime | None = None) -> float:
    """What `model_id`'s per-token rates are multiplied by right now (or at `at`) — 1.0 for every
    model that prices flat around the clock, and a declared window's multiplier while the UTC
    clock sits inside it (D217). See `model_catalog.yaml`'s `price_periods` comment.

    `at` is interpreted in UTC: an aware datetime is converted, a naive one is assumed to already
    be UTC (the shape `datetime.now(UTC)` produces, which is the default). Windows are matched in
    declaration order and the first hit wins, so overlapping windows resolve to the earlier one
    rather than compounding."""
    windows = _PRICE_PERIODS.get(model_id.lower())
    if not windows:
        return 1.0
    moment = at or datetime.now(UTC)
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC)
    now = moment.time()
    for start, end, multiplier in windows:
        if _in_window(now, start, end):
            return multiplier
    return 1.0


def all_models() -> list[dict]:
    """Every catalog model's id/name/provider/input/output list price, USD per token — for
    projecting a cost estimate across the whole catalog (#469) rather than a single resolved
    model. Order matches `model_catalog.yaml`."""
    return [{"id": m["id"], "name": m.get("name", m["id"]), "provider": m["provider"],
             "input": float(m["input"]), "output": float(m["output"])} for m in _CATALOG["models"]]
