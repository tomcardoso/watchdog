"""What the backend keeps of the environment it was started with (D409).

The app starts the backend, and through it every job, Claude Code and the model libraries, with
an allowlisted environment (`gui/src/main/childEnv.ts`): nothing in the shell the app came from
decides who pays for a model call or where a key is sent. The app marks that environment with
`WATCHDOG_APP`. Under that mark, `scrub` removes again, from this process's own environment, every
variable a provider, a cloud platform or Claude Code reads for credentials or an endpoint: a
backstop for a variable the allowlist let through by mistake. Started any other way (a benchmark
script, a test), nothing is removed, and the provider's environment variable still supplies a key
where an investigation chose none (D408)."""

from __future__ import annotations

import os

APP_MARKER = "WATCHDOG_APP"

# Families of credentials and endpoints: Anthropic and Claude Code, every other provider Watchdog
# supports, the cloud platforms Claude Code can bill through, and Hugging Face's token.
_PREFIXES = ("ANTHROPIC_", "CLAUDE_", "OPENAI_", "DEEPSEEK_", "GEMINI_", "GOOGLE_", "OPENROUTER_",
             "AWS_", "AZURE_", "CLOUDSDK_", "VERTEX_", "BEDROCK_")
_NAMES = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")


def under_app() -> bool:
    return os.environ.get(APP_MARKER) == "1"


def _provider_names() -> set[str]:
    from watchdog import auth
    names = set(auth.SUBSCRIPTION_ENV_CONFLICTS)
    for meta in auth.PROVIDERS.values():
        names.update(v for k, v in meta.items() if k in ("env", "base_url_env") and v)
    return names


def scrubbed(name: str) -> bool:
    """Whether `scrub` removes `name`."""
    up = name.upper()
    return (up.startswith(_PREFIXES) or up in _NAMES
            or up in {n.upper() for n in _provider_names()})


def scrub() -> list[str]:
    """Under the app, remove every credential or endpoint variable from this process's
    environment (and so from every process it starts); the names removed, sorted. Elsewhere,
    nothing."""
    if not under_app():
        return []
    gone = sorted(k for k in os.environ if scrubbed(k))
    for k in gone:
        os.environ.pop(k, None)
    return gone
