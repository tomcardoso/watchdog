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

# Providers whose keys watchdog manages. `anthropic` covers both the Claude
# Agent SDK and the Claude API backends — they share ANTHROPIC_API_KEY. The
# OpenAI-compatible providers (#125) each carry their own key, used by the
# matching `model_client` backend independent of the Claude auth mode.
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


# ── command surface ───────────────────────────────────────────────────────────

def _status() -> None:
    state = _load_state()
    mode = state.get("mode")
    meta = _PROVIDERS["anthropic"]

    print()
    print(f"  {_BOLD}Model access{_RESET}  {_DIM}{_credentials_path()}{_RESET}")
    print()

    if mode is None:
        print(f"  {_YELLOW}Not configured.{_RESET}")
        print(f"  {_DIM}Answer the prompt below, or run{_RESET} {_CYAN}watchdog setup{_RESET}{_DIM}.{_RESET}")
        print()
        return

    print(f"  {_DIM}Claude mode{_RESET}    {_CYAN}{mode}{_RESET}")

    if mode == "subscription":
        cc = claude_code_logged_in()
        cc_str = f"{_GREEN}detected{_RESET}" if cc else f"{_YELLOW}not detected{_RESET}"
        print(f"  {_DIM}Claude Code{_RESET}    {cc_str}")
        print()
        if os.environ.get(meta["env"]):
            print(f"  {_YELLOW}Warning:{_RESET} ${meta['env']} is set — the Agent SDK uses it before the")
            print(f"  {_DIM}subscription login, so runs would be metered. Unset it to use the subscription.{_RESET}")
        else:
            print(f"  {_DIM}Claude runs use your{_RESET} {_BOLD}Claude Code subscription{_RESET} {_DIM}(not metered).{_RESET}")
    else:  # api-key
        key = get_api_key()
        if key:
            where = f"${meta['env']}" if os.environ.get(meta["env"]) else "stored"
            print(f"  {_DIM}API key{_RESET}        {_CYAN}{_mask(key)}{_RESET} {_DIM}({where}){_RESET}")
            print()
            print(f"  {_DIM}Claude runs use a{_RESET} {_BOLD}metered API key{_RESET}{_DIM}.{_RESET}")
        else:
            print(f"  {_DIM}API key{_RESET}        {_YELLOW}(not set){_RESET}")
            print()
            print(f"  {_YELLOW}No key set.{_RESET} {_DIM}Add one below.{_RESET}")

    # Other (OpenAI-compatible) providers — shown only once a key exists for one (#125).
    others = [(p, get_api_key(p)) for p in _PROVIDERS if p != "anthropic"]
    if any(key for _, key in others):
        print()
        print(f"  {_DIM}Other providers{_RESET}")
        for p, key in others:
            if not key:
                continue
            where = f"${_PROVIDERS[p]['env']}" if os.environ.get(_PROVIDERS[p]["env"]) else "stored"
            print(f"  {_DIM}{p:<13}{_RESET}{_CYAN}{_mask(key)}{_RESET} {_DIM}({where}){_RESET}")
    print()


def _apply_anthropic_choice(state: dict, choice: str) -> None:
    """Apply a Claude access choice ("1"=subscription, "2"=api-key) and save state."""
    meta = _PROVIDERS["anthropic"]

    if choice == "1":
        state["mode"] = "subscription"
        _save_state(state)
        if claude_code_logged_in():
            print(f"\n  {_GREEN}✓{_RESET}  Claude Code login detected.")
        else:
            print(f"\n  {_YELLOW}!{_RESET}  Claude Code login not detected — run {_CYAN}claude{_RESET} to log in.")
        if os.environ.get(meta["env"]):
            print(f"  {_YELLOW}!{_RESET}  ${meta['env']} is set and the SDK uses it first — unset it to avoid metering.")
    else:
        print(f"\n  {_DIM}Create a key at{_RESET} {_CYAN}https://platform.claude.com/{_RESET} {_DIM}→ API keys.{_RESET}")
        try:
            key = getpass("  Paste your Anthropic API key (hidden): ").strip()
        except (EOFError, KeyboardInterrupt):
            key = ""
        state["mode"] = "api-key"
        if key:
            if not key.startswith(meta["prefix"]):
                print(f"  {_YELLOW}!{_RESET}  Key doesn't start with '{meta['prefix']}' — storing it anyway.")
            state["keys"]["anthropic"] = key
            _save_state(state)
            print(f"\n  {_GREEN}✓{_RESET}  API key stored ({_mask(key)}).")
        else:
            _save_state(state)
            print(f"\n  {_YELLOW}!{_RESET}  No key entered — mode set to api-key but no key stored yet.")


def setup_auth_interactive(interactive: bool | None = None) -> None:
    """Interactive auth setup for `watchdog setup`.

    Watchdog runs on Claude by default, so this sets up Claude access first (subscription or
    API key), then optionally stores keys for other model providers (OpenAI, DeepSeek) that
    individual stages can be routed to later. Persists the choice; skips cleanly off a
    terminal. `interactive` is overridable for testing.
    """
    if interactive is None:
        interactive = sys.stdin.isatty()

    print()
    print(f"  {_BOLD}Set up model access{_RESET}")
    print(f"  {_DIM}Watchdog uses Claude by default. Choose how to reach it:{_RESET}")
    print(f"    1. Claude Code subscription {_DIM}— use your existing `claude` login; not metered{_RESET}")
    print(f"    2. Claude API key {_DIM}— metered billing{_RESET}")
    print()

    if not interactive:
        print(f"  {_DIM}Non-interactive — set this later with{_RESET} {_CYAN}watchdog auth{_RESET}{_DIM}.{_RESET}")
        return

    try:
        choice = input("  Choice [1]: ").strip() or "1"
        while choice not in ("1", "2"):
            choice = input("  Enter 1 or 2: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    state = _load_state()
    _apply_anthropic_choice(state, choice)

    _offer_extra_providers(state)
    print(f"\n  {_DIM}Tune which model runs each stage anytime with{_RESET} {_CYAN}watchdog configure{_RESET} "
          f"{_DIM}(extractor_model, finalizer_model, extractor_effort, …).{_RESET}")


def _offer_extra_providers(state: dict) -> None:
    """Optionally store keys for additional (OpenAI-compatible) providers during setup.

    Skippable and provider-neutral — Watchdog still runs on Claude by default; these keys just
    let a user route a stage to another provider later via a `backend:model` config value."""
    extras = [p for p in _PROVIDERS if p != "anthropic"]
    if not extras:
        return
    labels = ", ".join(_PROVIDERS[p]["label"].split(" — ")[0] for p in extras)
    print(f"\n  {_BOLD}Other model providers?{_RESET} {_DIM}(optional — {labels}){_RESET}")
    print(f"  {_DIM}Add a key to route some stages to a cheaper/alternative provider. Skip to stay on Claude.{_RESET}")
    for p in extras:
        meta = _PROVIDERS[p]
        if os.environ.get(meta["env"]) or state["keys"].get(p):
            continue                                   # already available
        try:
            ans = input(f"  Add a {meta['label']} key? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if ans not in ("y", "yes"):
            continue
        try:
            key = getpass(f"  Paste {p} API key (hidden): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            continue
        if not key:
            print(f"  {_DIM}No key entered — skipped.{_RESET}")
            continue
        if not key.startswith(meta["prefix"]):
            print(f"  {_YELLOW}!{_RESET}  Key doesn't start with '{meta['prefix']}' — storing it anyway.")
        state["keys"][p] = key
        _save_state(state)
        print(f"  {_GREEN}✓{_RESET}  {p} key stored ({_mask(key)}).")


def _choose_provider_interactive() -> str | None:
    """Ask which provider to change. Returns the provider key, or None if cancelled/invalid."""
    providers = list(_PROVIDERS)
    print(f"  {_BOLD}Which service?{_RESET}")
    for i, p in enumerate(providers, 1):
        print(f"    {i}. {_PROVIDERS[p]['label']}")
    print()
    try:
        raw = input(f"  Choice [1-{len(providers)}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if not raw.isdigit() or not (1 <= int(raw) <= len(providers)):
        print(f"\n  {_YELLOW}Invalid choice — nothing changed.{_RESET}\n")
        return None
    return providers[int(raw) - 1]


def _pick_anthropic_mode_interactive(state: dict) -> None:
    print()
    print(f"  {_BOLD}{_PROVIDERS['anthropic']['label']}{_RESET}")
    print(f"    1. Claude Code subscription {_DIM}— use your existing `claude` login; not metered{_RESET}")
    print(f"    2. Claude API key {_DIM}— metered billing{_RESET}")
    print()
    try:
        choice = input("  Choice [1]: ").strip() or "1"
        while choice not in ("1", "2"):
            choice = input("  Enter 1 or 2: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    _apply_anthropic_choice(state, choice)
    print()


def _pick_key_provider_interactive(provider: str, state: dict) -> None:
    """Store, replace, or remove an OpenAI-compatible provider's key."""
    meta = _PROVIDERS[provider]
    existing = state["keys"].get(provider)

    print()
    print(f"  {_BOLD}{meta['label']}{_RESET}")
    if os.environ.get(meta["env"]):
        print(f"  {_YELLOW}Note:{_RESET} ${meta['env']} is set in your environment and "
              f"takes precedence over a stored key.")

    if existing:
        print(f"  Current key: {_CYAN}{_mask(existing)}{_RESET} {_DIM}(stored){_RESET}")
        try:
            ans = input("  [r]eplace, [d]elete, or [c]ancel? [c] ").strip().lower() or "c"
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if ans.startswith("d"):
            del state["keys"][provider]
            _save_state(state)
            print(f"\n  {_GREEN}Removed:{_RESET} stored key for {_BOLD}{provider}{_RESET}\n")
            return
        if not ans.startswith("r"):
            print()
            return

    try:
        key = getpass(f"  Paste {meta['label']} API key (hidden): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if not key:
        print(f"\n  {_DIM}No key entered — nothing changed.{_RESET}\n")
        return
    if not key.startswith(meta["prefix"]):
        print(f"\n  {_YELLOW}Warning:{_RESET} key doesn't start with '{meta['prefix']}' — storing it anyway.")
    state["keys"][provider] = key
    _save_state(state)
    print(f"\n  {_GREEN}Stored:{_RESET} {_BOLD}{provider}{_RESET} {_CYAN}{_mask(key)}{_RESET}")
    print()


def cmd_auth(args) -> None:
    """`watchdog auth` — show current settings, then interactively change one.

    Non-interactive (no tty) just prints status, mirroring `watchdog configure`.
    """
    _status()

    if not sys.stdin.isatty():
        return

    try:
        answer = input("  Change something? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if answer not in ("y", "yes"):
        return

    print()
    provider = _choose_provider_interactive()
    if provider is None:
        return

    state = _load_state()
    if provider == "anthropic":
        _pick_anthropic_mode_interactive(state)
    else:
        _pick_key_provider_interactive(provider, state)
