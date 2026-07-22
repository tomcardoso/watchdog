"""`watchdog auth` — choose how model backends authenticate, and manage API keys.

Two auth modes (#118):
  - **subscription** — rely on Claude Code's own login (`~/.claude/.credentials.json`,
    the same credentials the `claude` CLI uses). No metered API billing. Intended for
    running watchdog locally under your own Claude subscription. Anthropic's terms
    restrict *distributing* SDK products that depend on claude.ai login.
  - **api-key** — use a metered Anthropic API key (`ANTHROPIC_API_KEY`, or one stored
    here).

`watchdog auth` (bare) shows the current settings and, on a terminal, offers an
interactive prompt to change them: pick a service, then either switch Claude's mode
(subscription/api-key) or store/replace/remove that provider's key. There is no
separate set/get/remove/use subcommand surface.

State lives in `~/.watchdog/credentials.json` (mode 0600): `{"mode", "keys": {...}}`.
The environment variable always takes precedence over a stored key.
"""

import json
import os
import stat
import subprocess
import sys
from getpass import getpass
from pathlib import Path

from watchdog.cmd import base
from watchdog.cmd.base import _BOLD, _CYAN, _DIM, _GREEN, _RESET, _YELLOW
from watchdog.interactive import CANCELLED, confirm, pick

# Providers whose keys watchdog manages. `anthropic` covers both the Claude
# Agent SDK and the Claude API backends — they share ANTHROPIC_API_KEY. The
# OpenAI-compatible providers (#125) each carry their own key, used by the
# matching `model_client` backend independent of the Claude auth mode.
#
# `local` and `openrouter` (#380) additionally carry a **user-supplied base URL**
# (`base_url_key`/`base_url_env`; `default_base_url` when the provider has a fixed
# endpoint) rather than the fixed base URLs `model_client._OPENAI_BASE` hard-codes for
# openai/deepseek/gemini — every mainstream local runner (Ollama, LM Studio, llama.cpp's
# server, vLLM) and OpenRouter itself speak the same OpenAI-compatible wire format, so the
# only two things that vary are the endpoint and whether a key is needed. `requires_key`
# (default True when absent) marks `local` as the one provider that can run with no key at
# all — most local runners don't check for one.
_PROVIDERS: dict[str, dict] = {
    "anthropic": {
        "label":  "Anthropic — Claude API / Agent SDK",
        "env":    "ANTHROPIC_API_KEY",
        "prefix": "sk-ant-",
    },
    "openai": {
        "label":  "OpenAI — Chat Completions",
        "env":    "OPENAI_API_KEY",
        "prefix": "sk-",
    },
    "deepseek": {
        "label":  "DeepSeek — Chat Completions",
        "env":    "DEEPSEEK_API_KEY",
        "prefix": "sk-",
    },
    "gemini": {
        "label":  "Google Gemini — Chat Completions",
        "env":    "GEMINI_API_KEY",
        "prefix": None,  # Google key formats vary (AI Studio, Vertex AI, ...) — no reliable fixed prefix
    },
    "local": {
        "label":  "Local / self-hosted — OpenAI-compatible endpoint",
        "env":    "LOCAL_API_KEY",
        "prefix": None,
        "requires_key": False,
        "base_url_key": "local_base_url",
        "base_url_env": "LOCAL_BASE_URL",
        "default_base_url": None,   # must be supplied — no sensible one-size-fits-all default
    },
    "openrouter": {
        "label":  "OpenRouter — routes to many hosted models",
        "env":    "OPENROUTER_API_KEY",
        "prefix": "sk-or-",
        "base_url_key": "openrouter_base_url",
        "base_url_env": "OPENROUTER_BASE_URL",
        "default_base_url": "https://openrouter.ai/api/v1",
    },
}


def _credentials_path() -> Path:
    return base.WATCHDOG_HOME / "credentials.json"


def _claude_code_creds_path() -> Path:
    return Path.home() / ".claude" / ".credentials.json"


# macOS Claude Code stores its OAuth login as a Keychain generic-password item
# under this service (account = the macOS username), not in the credentials file.
_KEYCHAIN_SERVICE = "Claude Code-credentials"


def _keychain_has_claude_creds() -> bool:
    """macOS: is the Claude Code login present in the Keychain?

    Queries attributes only (no `-w`/`-g`), so it does not read the secret or
    trigger an access prompt.
    """
    if sys.platform != "darwin":
        return False
    try:
        return subprocess.run(
            ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE],
            capture_output=True, timeout=5,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def claude_code_logged_in() -> bool:
    """Best-effort check that the `claude` CLI is logged in.

    Checks the credentials file (Linux / older setups) and the macOS Keychain item
    the Agent SDK reads. The definitive test is still an SDK call succeeding.
    """
    return _claude_code_creds_path().exists() or _keychain_has_claude_creds()


def _load_state() -> dict:
    """Return {"mode", "keys"}. `mode` is None until set by setup or `auth use`."""
    path = _credentials_path()
    if not path.exists():
        return {"mode": None, "keys": {}}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        sys.exit("Error: credentials file is corrupt; remove it and re-add your keys.")
    data.setdefault("mode", None)
    data.setdefault("keys", {})
    return data


def _save_state(state: dict) -> None:
    base.WATCHDOG_HOME.mkdir(parents=True, exist_ok=True)
    path = _credentials_path()
    path.write_text(json.dumps(state, indent=2) + "\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600 — owner read/write only


def _mask(key: str) -> str:
    if len(key) <= 8:
        return "…" + key[-2:] if len(key) > 2 else "(set)"
    return f"{key[:10]}…{key[-4:]}"


def get_api_key(provider: str = "anthropic") -> str | None:
    """Resolve a provider's API key: environment variable first, then stored."""
    meta = _PROVIDERS.get(provider)
    if meta and os.environ.get(meta["env"]):
        return os.environ[meta["env"]]
    return _load_state()["keys"].get(provider)


def provider_requires_key(provider: str) -> bool:
    """Whether `provider` needs an API key to run — false only for `local` (#380), where most
    self-hosted runners don't check for one. Every other provider defaults to requiring one."""
    return _PROVIDERS.get(provider, {}).get("requires_key", True)


def _load_config() -> dict:
    from watchdog.cmd.base import CONFIG_FILE
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def _save_config(config: dict) -> None:
    from watchdog.cmd.base import CONFIG_FILE, WATCHDOG_HOME
    WATCHDOG_HOME.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")
    os.chmod(CONFIG_FILE, stat.S_IRUSR | stat.S_IWUSR)   # mirrors setup.py's _persist (#304)


def get_base_url(provider: str) -> str | None:
    """Resolve a provider's OpenAI-compatible base URL (#380): its env var override first, then
    the `watchdog configure` key named in `base_url_key`, then the provider's fixed default
    (None for a provider like `local` that has none — the caller must supply one)."""
    meta = _PROVIDERS.get(provider, {})
    env_name = meta.get("base_url_env")
    if env_name and os.environ.get(env_name):
        return os.environ[env_name].rstrip("/")
    base_key = meta.get("base_url_key")
    if base_key:
        value = _load_config().get(base_key)
        if value:
            return value.rstrip("/")
    return meta.get("default_base_url")


def provider_ready(provider: str) -> bool:
    """Whether `provider` has everything it needs to run a call: a base URL if it requires a
    user-supplied one, and an API key unless it's the rare provider that doesn't need one."""
    meta = _PROVIDERS.get(provider, {})
    if meta.get("base_url_key") and not get_base_url(provider):
        return False
    return not provider_requires_key(provider) or bool(get_api_key(provider))


def resolve_auth(provider: str = "anthropic") -> dict:
    """How a model backend should authenticate for this run — the resolver #118 calls.

    Returns one of:
      {"mode": "api-key", "key": "<key>"}   → pass the key (metered)
      {"mode": "subscription"}              → pass no key; the SDK uses Claude Code's login
      {"mode": "none", "reason": "<why>"}   → nothing configured; caller errors with guidance
    """
    mode = _load_state().get("mode")

    if mode is None:
        return {"mode": "none", "reason": "auth not configured — run `watchdog setup`"}
    if mode == "subscription":
        return {"mode": "subscription"}
    # api-key
    key = get_api_key(provider)
    return {"mode": "api-key", "key": key} if key else {
        "mode": "none", "reason": "api-key mode is set but no key is configured — run `watchdog auth`"}


# ── model routing → provider (for the status display) ──────────────────────────

def _ingest_stage_provider(value: str | None) -> str:
    """Best-effort `[backend:]model` config value → provider name, for the status display
    only — not validated the way `cmd/ingest.py`'s `_resolve_stage` is; it just needs to
    answer "which provider is this stage pointed at" (#325)."""
    if not value or ":" not in value:
        return "anthropic"          # bare Claude tier (haiku/sonnet/opus)
    backend = value.split(":", 1)[0]
    from watchdog.model_client import _BACKEND_PROVIDER
    return _BACKEND_PROVIDER.get(backend, "anthropic")


_INGEST_STAGES = (("classifier_model", "haiku"), ("extractor_model", "sonnet"), ("finalizer_model", "haiku"))


# ── command surface ───────────────────────────────────────────────────────────

def _status() -> None:
    state = _load_state()
    mode = state.get("mode")
    meta = _PROVIDERS["anthropic"]

    print()
    print(f"  {_BOLD}Model access{_RESET}  {_DIM}{_credentials_path()}{_RESET}")
    print()

    # Claude Code: powers the interactive investigation commands (always) and is the
    # ingestion default unless a stage is routed elsewhere below.
    print(f"  {_BOLD}Claude Code{_RESET}  {_DIM}— interactive commands, and ingestion by default{_RESET}")
    if mode is None:
        print(f"  {_YELLOW}Not configured.{_RESET}")
        print(f"  {_DIM}Answer the prompt below, or run{_RESET} {_CYAN}watchdog setup{_RESET}{_DIM}.{_RESET}")
    elif mode == "subscription":
        cc = claude_code_logged_in()
        cc_str = f"{_GREEN}detected{_RESET}" if cc else f"{_YELLOW}not detected{_RESET}"
        print(f"  {_DIM}mode{_RESET}  {_CYAN}subscription{_RESET}  {_DIM}(Claude Code login {_RESET}{cc_str}{_DIM}){_RESET}")
        if os.environ.get(meta["env"]):
            print(f"  {_YELLOW}Warning:{_RESET} ${meta['env']} is set — the Agent SDK uses it before the")
            print(f"  {_DIM}subscription login, so runs would be metered. Unset it to use the subscription.{_RESET}")
    else:  # api-key
        key = get_api_key()
        if key:
            where = f"${meta['env']}" if os.environ.get(meta["env"]) else "stored"
            print(f"  {_DIM}mode{_RESET}  {_CYAN}api-key{_RESET}  {_DIM}({_RESET}{_CYAN}{_mask(key)}{_RESET}{_DIM}, {where}){_RESET}")
        else:
            print(f"  {_DIM}mode{_RESET}  {_CYAN}api-key{_RESET}  {_YELLOW}(no key set — add one below){_RESET}")
    print()

    # Ingestion: which provider each pipeline stage is actually routed to, and whether that
    # provider is ready — the thing that actually determines whether `watchdog ingest` will
    # work, independent of Claude's mode above (#325).
    from watchdog.cmd.base import CONFIG_FILE
    config: dict = {}
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text())
        except json.JSONDecodeError:
            config = {}

    print(f"  {_BOLD}Ingestion{_RESET}  {_DIM}— classifier / extractor / finalizer{_RESET}")
    for stage_key, default in _INGEST_STAGES:
        value = config.get(stage_key) or default
        provider = _ingest_stage_provider(value)
        ready = (mode == "subscription" or bool(get_api_key("anthropic"))) if provider == "anthropic" \
            else provider_ready(provider)
        mark = f"{_GREEN}✓{_RESET}" if ready else f"{_YELLOW}✗{_RESET}"
        label = stage_key[: -len("_model")]
        print(f"  {mark} {_DIM}{label:<11}{_RESET}{_CYAN}{value}{_RESET}  {_DIM}({provider}){_RESET}")
    print()

    # Every non-Claude provider with a key available, whether or not a stage is routed to it.
    # Listing only the unrouted ones (as this did before) meant a key in active use — the one
    # backing a ✓ above — never showed its masked value anywhere, so there was no way to check
    # *which* key was in play without opening the wizard.
    shown_providers = {_ingest_stage_provider(config.get(k) or d) for k, d in _INGEST_STAGES}
    keyed = [(p, get_api_key(p)) for p in _PROVIDERS if p != "anthropic"]
    keyed = [(p, key) for p, key in keyed if key]
    if keyed:
        print(f"  {_DIM}Provider keys{_RESET}")
        for p, key in keyed:
            where = f"${_PROVIDERS[p]['env']}" if os.environ.get(_PROVIDERS[p]["env"]) else "stored"
            status = "in use" if p in shown_providers else "unused"
            print(f"  {_DIM}{p:<13}{_RESET}{_CYAN}{_mask(key)}{_RESET} {_DIM}({where}, {status}){_RESET}")
        print()

    # Providers with a user-supplied base URL (local, openrouter — #380), whichever is set.
    urled = [(p, get_base_url(p)) for p, m in _PROVIDERS.items() if m.get("base_url_key")]
    urled = [(p, u) for p, u in urled if u]
    if urled:
        print(f"  {_DIM}Base URLs{_RESET}")
        for p, u in urled:
            print(f"  {_DIM}{p:<13}{_RESET}{_CYAN}{u}{_RESET}")
        print()


def prompt_and_store_key(provider: str, state: dict) -> bool:
    """Prompt for a non-Claude provider's API key, warn on an unexpected prefix, store it, and
    confirm with the masked value. Returns True if a key was stored.

    The single implementation of "ask for a key and save it" — shared by setup's extra-provider
    offer, the metered-ingestion wizard, `watchdog auth`'s key editor, and the check that runs
    when a model is picked from a provider with no key yet. Mutates and persists `state`.

    A provider that doesn't require one (`local` — #380, most self-hosted runners don't check)
    still goes through this same prompt, worded as optional, so leaving it blank is a normal,
    silent outcome rather than something that reads as declining a required step.
    """
    meta = _PROVIDERS[provider]
    optional = not meta.get("requires_key", True)
    hint = " (hidden, optional — Enter to skip)" if optional else " (hidden)"
    try:
        key = getpass(f"  Paste {meta['label']} API key{hint}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not key:
        print(f"  {_DIM}No key entered — skipped.{_RESET}")
        return False
    if meta["prefix"] and not key.startswith(meta["prefix"]):
        print(f"  {_YELLOW}!{_RESET}  Key doesn't start with '{meta['prefix']}' — storing it anyway.")
    state.setdefault("keys", {})[provider] = key
    _save_state(state)
    print(f"  {_GREEN}✓{_RESET}  {meta['label']} key stored ({_mask(key)}).")
    return True


def prompt_and_store_base_url(provider: str) -> bool:
    """Prompt for a provider's OpenAI-compatible base URL and persist it to `watchdog configure`'s
    `base_url_key` (#380) — the same config.json a user could set directly with `watchdog
    configure local_base_url <url>`. Returns True if a URL was stored."""
    meta = _PROVIDERS[provider]
    example = "http://localhost:11434/v1" if provider == "local" else meta.get("default_base_url", "")
    try:
        url = input(f"  Base URL for {meta['label']} (e.g. {example}): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not url:
        print(f"  {_DIM}No URL entered — skipped.{_RESET}")
        return False
    config = _load_config()
    config[meta["base_url_key"]] = url.rstrip("/")
    _save_config(config)
    print(f"  {_GREEN}✓{_RESET}  Base URL stored: {_CYAN}{config[meta['base_url_key']]}{_RESET}")
    return True


def ensure_provider_key(value: str) -> None:
    """Prompt for whatever a just-chosen `[backend:]model` needs and doesn't have yet: a base
    URL for a provider like `local`/`openrouter` that requires a user-supplied one (#380), and an
    API key for a provider that requires one (skipped for `local`, which usually needs none).

    Picking, say, `gemini:gemini-2.5-flash` for a stage used to leave that stage silently
    unusable until the user separately remembered to run `watchdog auth` — the failure only
    surfaced mid-ingest. Asking here keeps "pick a model" and "be able to run it" together.
    A Claude tier, or a provider that's already fully configured, is a no-op.
    """
    provider = _ingest_stage_provider(value)
    if provider == "anthropic" or provider_ready(provider):
        return
    print()
    meta = _PROVIDERS.get(provider, {})
    if meta.get("base_url_key") and not get_base_url(provider):
        prompt_and_store_base_url(provider)
    if provider_requires_key(provider) and not get_api_key(provider):
        prompt_and_store_key(provider, _load_state())


def _apply_anthropic_choice(state: dict, choice: str, *, show_detection: bool = True) -> bool:
    """Apply a Claude access choice ("1"=subscription, "2"=api-key) and save state. Returns
    whether anything was printed, so a caller that prints its own follow-up right after a
    picker closes knows whether it still needs a separating blank line or one was already
    produced.

    `show_detection` controls whether the subscription branch reports login detection —
    set it False when the caller already showed that status right before the picker, so it
    isn't printed twice."""
    meta = _PROVIDERS["anthropic"]
    printed = False

    if choice == "1":
        state["mode"] = "subscription"
        _save_state(state)
        if show_detection:
            if claude_code_logged_in():
                print(f"  {_GREEN}✓{_RESET}  Claude Code login detected.")
            else:
                print(f"  {_YELLOW}!{_RESET}  Claude Code login not detected — run {_CYAN}claude{_RESET} to log in.")
            printed = True
        if os.environ.get(meta["env"]):
            print(f"  {_YELLOW}!{_RESET}  ${meta['env']} is set and the SDK uses it first — unset it to avoid metering.")
            printed = True
    else:
        printed = True
        print(f"  {_DIM}Create a key at{_RESET} {_CYAN}https://platform.claude.com/{_RESET} {_DIM}→ API keys.{_RESET}")
        try:
            key = getpass("  Paste your Anthropic API key (hidden): ").strip()
        except (EOFError, KeyboardInterrupt):
            key = ""
        state["mode"] = "api-key"
        if key:
            if meta["prefix"] and not key.startswith(meta["prefix"]):
                print(f"  {_YELLOW}!{_RESET}  Key doesn't start with '{meta['prefix']}' — storing it anyway.")
            state["keys"]["anthropic"] = key
            _save_state(state)
            print(f"\n  {_GREEN}✓{_RESET}  API key stored ({_mask(key)}).")
        else:
            _save_state(state)
            print(f"\n  {_YELLOW}!{_RESET}  No key entered — mode set to api-key but no key stored yet.")

    return printed


def _ask_anthropic_mode() -> str | None:
    """Arrow-key/numbered picker for the Claude access mode — shared by `watchdog setup` and
    `watchdog auth`. Returns "1" (subscription), "2" (api-key), or None if cancelled."""
    items = [
        "Claude Code subscription " + _DIM + "— use your existing `claude` login; not metered" + _RESET,
        "Claude API key " + _DIM + "— metered billing" + _RESET,
    ]
    result = pick(items, 0)
    if result is CANCELLED:
        return None
    return "2" if result == 1 else "1"


def setup_auth_interactive(interactive: bool | None = None) -> None:
    """Interactive auth setup for `watchdog setup`.

    Watchdog needs Claude Code to run: the interactive investigation commands
    (/watchdog-query, /watchdog-surface, ...) always run on Claude, and it's the ingestion
    default too. This sets up Claude access first (subscription or API key), and — on a
    subscription — warns that ingesting more than a few documents can be token-heavy for a
    Pro plan's session limits and offers to route ingestion to another provider instead —
    a cheaper metered API, or a local/self-hosted model (#380) — walking through picking
    that provider's models for the three ingest stages if so (#325). Persists the choice;
    skips cleanly off a terminal. `interactive` is overridable for testing.
    """
    if interactive is None:
        interactive = sys.stdin.isatty()

    print()
    print(f"  {_BOLD}Set up model access{_RESET}")
    print(f"  {_DIM}Watchdog needs Claude Code to run — it powers the interactive investigation{_RESET}")
    print(f"  {_DIM}commands and is the default for ingestion too.{_RESET}")

    if not interactive:
        print(f"  {_DIM}Non-interactive — set this later with{_RESET} {_CYAN}watchdog auth{_RESET}{_DIM}.{_RESET}")
        return

    if claude_code_logged_in():
        print(f"  {_GREEN}✓{_RESET}  Claude Code subscription login detected.")
    else:
        print(f"  {_DIM}No Claude Code login detected — run{_RESET} {_CYAN}claude{_RESET}{_DIM} "
              f"first if you have a subscription.{_RESET}")

    choice = _ask_anthropic_mode()
    if choice is None:
        print()
        return

    state = _load_state()
    printed = _apply_anthropic_choice(state, choice, show_detection=False)

    if choice == "1":  # subscription
        lead = "\n" if printed else ""
        print(f"{lead}  {_YELLOW}Note:{_RESET} {_DIM}ingesting more than a few documents can be token-heavy for a "
              f"Pro subscription's session limits. See{_RESET}")
        print(f"  {_CYAN}docs/configuration.md{_RESET} {_DIM}(\"Model backends\") for cheaper "
              f"alternatives — OpenAI, DeepSeek, Gemini, OpenRouter — or a local/self-hosted model.{_RESET}")
        if confirm("\n  Route ingestion to another provider instead of your subscription?", default=False):
            _setup_metered_ingestion(state)
        else:
            _maybe_tune_concurrency_for_subscription()
    else:
        _offer_extra_providers(state)

    print(f"\n  {_DIM}Tune which model runs each stage anytime with{_RESET} {_CYAN}watchdog configure{_RESET} "
          f"{_DIM}(extractor_model, finalizer_model, extractor_effort, …).{_RESET}")


_SUBSCRIPTION_CONCURRENCY = 3


def _maybe_tune_concurrency_for_subscription() -> bool:
    """Lower `extract_concurrency` when `watchdog setup` lands on Claude subscription auth and
    ingestion stays on it (issue #400): every concurrent extraction on that path shares one
    Claude Code session, and the built-in default of 5 reliably throttles it — one call in a
    5-way batch was observed running at ~1/5 the normal token rate. Only touches the setting
    when it has never been explicitly configured — a prior `watchdog configure
    extract_concurrency` (including an earlier auto-tune) is left alone, since that's a
    deliberate choice this shouldn't silently overwrite. Returns whether anything was printed,
    for the caller's blank-line bookkeeping."""
    config: dict = {}
    if base.CONFIG_FILE.exists():
        try:
            config = json.loads(base.CONFIG_FILE.read_text())
        except json.JSONDecodeError:
            config = {}
    if "extract_concurrency" in config:
        return False

    config["extract_concurrency"] = _SUBSCRIPTION_CONCURRENCY
    base.WATCHDOG_HOME.mkdir(parents=True, exist_ok=True)
    base.CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")
    os.chmod(base.CONFIG_FILE, stat.S_IRUSR | stat.S_IWUSR)
    print(f"\n  {_GREEN}✓{_RESET}  Detected Claude subscription auth — set {_BOLD}extract_concurrency{_RESET} "
          f"to {_BOLD}{_SUBSCRIPTION_CONCURRENCY}{_RESET}.")
    print(f"  {_DIM}Concurrent extractions share one Claude Code session's rate limit; raise it back "
          f"with{_RESET} {_CYAN}watchdog configure extract_concurrency{_RESET}{_DIM}.{_RESET}")
    return True


def _setup_metered_ingestion(state: dict) -> None:
    """Pick a non-Claude provider, store its key, and set classifier/extractor/finalizer_model
    to that provider's models — the metered-ingestion path offered from `watchdog setup` when
    Claude is on a subscription (#325)."""
    extras = [p for p in _PROVIDERS if p != "anthropic"]
    items = [_PROVIDERS[p]["label"] for p in extras]
    result = pick(items, 0, title="Which service for ingestion?")
    if result is CANCELLED:
        return
    provider = extras[result]
    meta = _PROVIDERS[provider]

    print()
    if meta.get("base_url_key") and not get_base_url(provider):
        if not prompt_and_store_base_url(provider):
            return   # local/openrouter can't run without one — nothing to fall back to

    if not provider_requires_key(provider):
        pass   # e.g. local — most self-hosted runners need no key at all
    elif os.environ.get(meta["env"]) or state["keys"].get(provider):
        print(f"  {_GREEN}✓{_RESET}  {meta['label']} key already available.")
    elif not prompt_and_store_key(provider, state):
        return

    from watchdog.cmd.base import CONFIG_FILE, WATCHDOG_HOME
    from watchdog.cmd.setup import _pick_model_interactive
    config: dict = {}
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text())
        except json.JSONDecodeError:
            config = {}

    print(f"\n  {_BOLD}Pick default models for ingestion{_RESET} "
          f"{_DIM}(change anytime with watchdog configure){_RESET}")
    for key, label in (("classifier_model", "Classifier"), ("extractor_model", "Extractor"),
                       ("finalizer_model", "Finalizer")):
        print(f"\n  {label}")
        value = _pick_model_interactive(config.get(key), only_provider=provider)
        if value:
            config[key] = value

    WATCHDOG_HOME.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")
    os.chmod(CONFIG_FILE, stat.S_IRUSR | stat.S_IWUSR)
    print(f"\n  {_GREEN}✓{_RESET}  Ingestion routed to {_BOLD}{provider}{_RESET}.")


def _offer_extra_providers(state: dict) -> None:
    """Optionally configure additional providers during setup — a key for the OpenAI-compatible
    ones, or a base URL (and, for OpenRouter, a key) for local/self-hosted (#380).

    Skippable and provider-neutral — Watchdog still runs on Claude by default; these just let a
    user route a stage to another provider later via a `backend:model` config value."""
    extras = [p for p in _PROVIDERS if p != "anthropic"]
    if not extras:
        return
    labels = ", ".join(_PROVIDERS[p]["label"].split(" — ")[0] for p in extras)
    print(f"\n  {_BOLD}Other model providers?{_RESET} {_DIM}(optional — {labels}){_RESET}")
    print(f"  {_DIM}Route a stage to a cheaper, alternative, or local/self-hosted provider. Skip to stay on Claude.{_RESET}")
    for p in extras:
        if provider_ready(p):
            continue                                   # already available
        if not confirm(f"  Configure {_PROVIDERS[p]['label']}?", default=False):
            continue
        meta = _PROVIDERS[p]
        if meta.get("base_url_key") and not get_base_url(p):
            prompt_and_store_base_url(p)
        if provider_requires_key(p) and not (os.environ.get(meta["env"]) or state["keys"].get(p)):
            prompt_and_store_key(p, state)


def _choose_provider_interactive() -> str | None:
    """Ask whether to change anything and, if so, which service. "Done" is the first row rather
    than a separate y/n prompt in front of the picker — one keypress to leave `watchdog auth`
    instead of two. Returns the provider key, or None if the user chose "Done" or cancelled."""
    providers = list(_PROVIDERS)
    items = ["Done — nothing to change"] + [_PROVIDERS[p]["label"] for p in providers]
    result = pick(items, 0, title="Change something?")
    if result is CANCELLED or result == 0:
        return None
    return providers[result - 1]


def _pick_anthropic_mode_interactive(state: dict) -> None:
    print()
    print(f"  {_BOLD}{_PROVIDERS['anthropic']['label']}{_RESET}")
    choice = _ask_anthropic_mode()
    if choice is None:
        print()
        return
    _apply_anthropic_choice(state, choice)
    print()


def _pick_base_url_provider_interactive(provider: str) -> None:
    """Set, replace, or remove a provider's user-supplied base URL (#380 — `local`, `openrouter`)."""
    meta = _PROVIDERS[provider]
    base_key = meta["base_url_key"]
    existing = get_base_url(provider)

    if os.environ.get(meta.get("base_url_env") or ""):
        print(f"  {_YELLOW}Note:{_RESET} ${meta['base_url_env']} is set in your environment and "
              f"takes precedence over a configured value.")

    if existing:
        print(f"  Current base URL: {_CYAN}{existing}{_RESET}")
        items = ["Replace it", "Delete it", "Keep it"]
        result = pick(items, 2, title="What would you like to do?")
        if result is CANCELLED or result == 2:
            return
        if result == 1:
            config = _load_config()
            config.pop(base_key, None)
            _save_config(config)
            print(f"  {_GREEN}Removed:{_RESET} base URL for {_BOLD}{provider}{_RESET}\n")
            return

    prompt_and_store_base_url(provider)


def _pick_key_provider_interactive(provider: str, state: dict) -> None:
    """Set the provider's base URL if it needs a user-supplied one (#380), then store, replace,
    or remove its API key — skipped for a provider that doesn't require one (`local`) unless the
    user wants to add one anyway (some self-hosted gateways do check for one)."""
    meta = _PROVIDERS[provider]
    existing = state["keys"].get(provider)

    print()
    print(f"  {_BOLD}{meta['label']}{_RESET}")

    if meta.get("base_url_key"):
        _pick_base_url_provider_interactive(provider)
        print()

    if os.environ.get(meta["env"]):
        print(f"  {_YELLOW}Note:{_RESET} ${meta['env']} is set in your environment and "
              f"takes precedence over a stored key.")

    if not provider_requires_key(provider) and not existing:
        if not confirm(f"  {meta['label']} doesn't require a key — add one anyway?", default=False):
            print()
            return

    if existing:
        print(f"  Current key: {_CYAN}{_mask(existing)}{_RESET} {_DIM}(stored){_RESET}")
        items = ["Replace the stored key", "Delete the stored key", "Cancel"]
        result = pick(items, 0, title="What would you like to do?")
        if result is CANCELLED or result == 2:
            return
        if result == 1:
            del state["keys"][provider]
            _save_state(state)
            print(f"  {_GREEN}Removed:{_RESET} stored key for {_BOLD}{provider}{_RESET}\n")
            return

    prompt_and_store_key(provider, state)
    print()


def cmd_auth(args) -> None:
    """`watchdog auth` — show current settings, then interactively change one.

    Non-interactive (no tty) just prints status, mirroring `watchdog configure`.
    """
    _status()

    if not sys.stdin.isatty():
        return

    provider = _choose_provider_interactive()
    if provider is None:
        return

    state = _load_state()
    if provider == "anthropic":
        _pick_anthropic_mode_interactive(state)
    else:
        _pick_key_provider_interactive(provider, state)
