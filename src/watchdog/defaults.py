"""Pipeline defaults, defined once.

Every place that applies or *displays* one of these — Settings,
the orchestrator's own signature defaults, Settings → Models & keys's status table — reads it from here.
They used to be repeated as literals, and drifted: Settings reported
`extract_concurrency = 5` while the runtime used 20.
"""

EXTRACTOR_MODEL = "haiku"
CLASSIFIER_MODEL = "haiku"
FINALIZER_MODEL = "haiku"

# The model each provider's steps switch to when its first key is added and the default above
# has no way to run (D347): the provider's recommended model, as `[backend:]model`. Judgement
# from the October 2026 frontier matrix (D315): Haiku 5.5 led; Luna, Gemini 3.8 Flash and DeepSeek
# Flash were each their provider's best arm. OpenRouter and a local server have no default: the
# reporter names the model.
PROVIDER_MODELS = {
    "anthropic": "haiku",
    "openai": "openai:gpt-6-luna",
    "gemini": "gemini:gemini-3.8-flash",
    "deepseek": "deepseek:deepseek-flash",
}

EXTRACTOR_EFFORT = "high"
CLASSIFIER_EFFORT = "low"

# Metered-provider default (#493, D162). first-run setup/Settings → Models & keys lower it to
# SUBSCRIPTION_CONCURRENCY when ingestion stays on a Claude subscription.
EXTRACT_CONCURRENCY = 20
SUBSCRIPTION_CONCURRENCY = 3

CLASSIFY_PAGES = 5


def split_backend_model(raw: str) -> tuple[str | None, str]:
    """Split a stage's `[backend:]model` value at the *first* colon.

    Model ids can themselves contain colons — Ollama's `name:tag` form (`local:qwen3:32b`) — so
    splitting at the last colon read `local:qwen3` as the backend. A value with no colon is a
    bare Claude tier: `(None, value)`. Validation of either half is the caller's job."""
    backend, sep, model = raw.partition(":")
    return (backend or None, model) if sep else (None, raw)
