"""Single source of truth for every model Watchdog knows about — ids, pricing, context
windows, and OpenAI reasoning capability — loaded from model_catalog.yaml. No
watchdog-internal imports: both model_client.py and cmd/base.py depend on this without
risking an import cycle (model_client.py already imports watchdog.cmd.auth, which
imports watchdog.cmd.base).
"""

import importlib.resources

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

# Claude pricing, USD/token: (input, output, cache_write_5m, cache_read).
_PRICING = {
    m["id"]: (float(m["input"]), float(m["output"]), float(m["cache_write"]), float(m["cache_read"]))
    for m in _CATALOG["models"] if m["provider"] == "anthropic"
}

# OpenAI-compatible pricing, USD/token: (input, output, cached_input).
_OPENAI_PRICING = {
    m["id"]: (float(m["input"]), float(m["output"]), float(m["cached_input"]))
    for m in _CATALOG["models"] if m["provider"] != "anthropic"
}

# (substring/prefix, value) pairs, most-specific-first — see model_catalog.yaml's own comments.
_CONTEXT_WINDOW_FALLBACK = [(row[0], int(row[1])) for row in _CATALOG["context_window_fallback"]]
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


def all_models() -> list[dict]:
    """Every catalog model's id/name/provider/input/output list price, USD per token — for
    projecting a cost estimate across the whole catalog (#469) rather than a single resolved
    model. Order matches `model_catalog.yaml`."""
    return [{"id": m["id"], "name": m.get("name", m["id"]), "provider": m["provider"],
             "input": float(m["input"]), "output": float(m["output"])} for m in _CATALOG["models"]]
