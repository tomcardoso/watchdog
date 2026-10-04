"""Pipeline defaults, defined once.

Every place that applies or *displays* one of these — the CLI's flag help, `watchdog configure`,
the orchestrator's own signature defaults, `watchdog auth`'s status table — reads it from here.
They used to be repeated as literals, and drifted: `watchdog configure` reported
`extract_concurrency = 5` while the runtime used 20.
"""

EXTRACTOR_MODEL = "sonnet"
CLASSIFIER_MODEL = "haiku"
FINALIZER_MODEL = "haiku"

EXTRACTOR_EFFORT = "medium"
CLASSIFIER_EFFORT = "low"

# Metered-provider default (#493, D162). `watchdog setup`/`watchdog auth` lower it to
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
