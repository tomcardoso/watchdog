"""`watchdog auth` — choose how model backends authenticate, and manage API keys.

Two auth modes (#118):
  - **subscription** — rely on Claude Code's own login (`~/.claude/.credentials.json`,
    the same credentials the `claude` CLI uses). No metered API billing. Intended for
    running watchdog locally under your own Claude subscription. Anthropic's terms
    restrict *distributing* SDK products that depend on claude.ai login — see `_use`.
  - **api-key** — use a metered Anthropic API key (`ANTHROPIC_API_KEY`, or one stored
    here).

Default mode is **auto**: use an API key if one is available, otherwise fall back to
Claude Code's login — mirroring the Agent SDK's own resolution order.

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

_MODES = ("subscription", "api-key")

# Providers whose keys watchdog manages. `anthropic` covers both the Claude
# Agent SDK and the Claude API backends — they share ANTHROPIC_API_KEY.
_PROVIDERS: dict[str, dict] = {
    "anthropic": {
        "label":  "Anthropic — Claude API / Agent SDK",
        "env":    "ANTHROPIC_API_KEY",
        "prefix": "sk-ant-",
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
        "mode": "none", "reason": "api-key mode is set but no key is configured — run `watchdog auth set`"}


# ── command surface ───────────────────────────────────────────────────────────

def _status() -> None:
    state = _load_state()
    mode = state.get("mode")
    meta = _PROVIDERS["anthropic"]

    print()
    print(f"  {_BOLD}Authentication{_RESET}  {_DIM}{_credentials_path()}{_RESET}")
    print()

    if mode is None:
        print(f"  {_YELLOW}Not configured.{_RESET}")
        print(f"  {_DIM}Run{_RESET} {_CYAN}watchdog setup{_RESET} {_DIM}to choose how to authenticate "
              f"(or{_RESET} {_CYAN}watchdog auth use <mode>{_RESET}{_DIM}).{_RESET}")
        print()
        return

    print(f"  {_DIM}Mode{_RESET}           {_CYAN}{mode}{_RESET}")

    if mode == "subscription":
        cc = claude_code_logged_in()
        cc_str = f"{_GREEN}detected{_RESET}" if cc else f"{_YELLOW}not detected{_RESET}"
        print(f"  {_DIM}Claude Code{_RESET}    {cc_str}")
        print()
        if os.environ.get(meta["env"]):
            print(f"  {_YELLOW}Warning:{_RESET} ${meta['env']} is set — the Agent SDK uses it before the")
            print(f"  {_DIM}subscription login, so runs would be metered. Unset it to use the subscription.{_RESET}")
        else:
            print(f"  {_DIM}Runs use your{_RESET} {_BOLD}Claude Code subscription{_RESET} {_DIM}(not metered).{_RESET}")
    else:  # api-key
        key = get_api_key()
        if key:
            where = f"${meta['env']}" if os.environ.get(meta["env"]) else "stored"
            print(f"  {_DIM}API key{_RESET}        {_CYAN}{_mask(key)}{_RESET} {_DIM}({where}){_RESET}")
            print()
            print(f"  {_DIM}Runs use a{_RESET} {_BOLD}metered API key{_RESET}{_DIM}.{_RESET}")
        else:
            print(f"  {_DIM}API key{_RESET}        {_YELLOW}(not set){_RESET}")
            print()
            print(f"  {_YELLOW}No key set.{_RESET} {_DIM}Add one with{_RESET} {_CYAN}watchdog auth set{_RESET}{_DIM}.{_RESET}")
    print()


def setup_auth_interactive(interactive: bool | None = None) -> None:
    """Interactive auth picker for `watchdog setup`: choose subscription or API key.

    Persists the chosen mode (and key, for api-key). Skips cleanly when not run on
    a terminal. `interactive` is overridable for testing.
    """
    if interactive is None:
        interactive = sys.stdin.isatty()

    print()
    print(f"  {_BOLD}How should Watchdog authenticate to Claude?{_RESET}")
    print(f"    1. Claude Code subscription {_DIM}— use your existing `claude` login; not metered{_RESET}")
    print(f"    2. API key {_DIM}— metered billing{_RESET}")
    print()

    if not interactive:
        print(f"  {_DIM}Non-interactive — set this later with{_RESET} {_CYAN}watchdog auth use <mode>{_RESET}{_DIM}.{_RESET}")
        return

    try:
        choice = input("  Choice [1]: ").strip() or "1"
        while choice not in ("1", "2"):
            choice = input("  Enter 1 or 2: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    state = _load_state()
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
        return

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
        print(f"\n  {_YELLOW}!{_RESET}  No key entered — add one later with {_CYAN}watchdog auth set{_RESET}.")


def _use(mode: str | None) -> None:
    if mode not in _MODES:
        sys.exit(f"Error: mode must be one of: {', '.join(_MODES)}")
    state = _load_state()
    state["mode"] = mode
    _save_state(state)
    print(f"\n  {_GREEN}Auth mode:{_RESET} {_BOLD}{mode}{_RESET}\n")

    meta = _PROVIDERS["anthropic"]
    if mode == "subscription":
        if not claude_code_logged_in():
            print(f"  {_YELLOW}Note:{_RESET} Claude Code login not detected — run {_CYAN}claude{_RESET} to log in.")
        if os.environ.get(meta["env"]):
            print(f"  {_YELLOW}Warning:{_RESET} ${meta['env']} is set and the SDK uses it first — unset it to avoid metering.")
        print(f"  {_DIM}Subscription mode runs watchdog under your own Claude login. Anthropic's terms")
        print(f"  restrict distributing SDK products that rely on claude.ai login — for a shared/")
        print(f"  deployed tool, use api-key mode (metered) or seek Anthropic approval.{_RESET}\n")
    elif mode == "api-key" and not get_api_key():
        print(f"  {_DIM}No key set yet —{_RESET} {_CYAN}watchdog auth set{_RESET}{_DIM}.{_RESET}\n")


def _set(provider: str) -> None:
    meta = _PROVIDERS[provider]
    if os.environ.get(meta["env"]):
        print(f"\n  {_YELLOW}Note:{_RESET} ${meta['env']} is set in your environment and "
              f"will take precedence over a stored key.")
    try:
        key = getpass(f"\n  Paste {meta['label']} API key (hidden): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if not key:
        print(f"\n  {_DIM}No key entered — nothing changed.{_RESET}\n")
        return
    if not key.startswith(meta["prefix"]):
        print(f"\n  {_YELLOW}Warning:{_RESET} key doesn't start with '{meta['prefix']}' — storing it anyway.")
    state = _load_state()
    state["keys"][provider] = key
    _save_state(state)
    print(f"\n  {_GREEN}Stored:{_RESET} {_BOLD}{provider}{_RESET} {_CYAN}{_mask(key)}{_RESET}")
    if state.get("mode") == "subscription":
        print(f"  {_DIM}Auth mode is 'subscription', so this key won't be used. Switch with{_RESET} {_CYAN}watchdog auth use api-key{_RESET}{_DIM}.{_RESET}")
    print()


def _get(provider: str) -> None:
    meta = _PROVIDERS[provider]
    key = get_api_key(provider)
    print()
    if not key:
        print(f"  {_BOLD}{provider}{_RESET}  {_YELLOW}(not set){_RESET}")
        print(f"  {_DIM}Set it with:{_RESET} {_CYAN}watchdog auth set {provider}{_RESET}")
    else:
        where = f"${meta['env']}" if os.environ.get(meta["env"]) else "stored credential"
        print(f"  {_BOLD}{provider}{_RESET}  {_CYAN}{_mask(key)}{_RESET}  {_DIM}({where}){_RESET}")
    print()


def _remove(provider: str) -> None:
    state = _load_state()
    if provider not in state["keys"]:
        print(f"\n  {_DIM}No stored key for '{provider}'.{_RESET}\n")
        return
    del state["keys"][provider]
    _save_state(state)
    print(f"\n  {_GREEN}Removed:{_RESET} stored key for {_BOLD}{provider}{_RESET}\n")
    if os.environ.get(_PROVIDERS[provider]["env"]):
        print(f"  {_YELLOW}Note:{_RESET} ${_PROVIDERS[provider]['env']} is still set in your environment.\n")


def cmd_auth(args) -> None:
    action = getattr(args, "action", None)
    target = getattr(args, "target", None)

    if action in (None, "status"):
        _status()
        return
    if action == "use":
        _use(target)
        return

    provider = target or "anthropic"
    if provider not in _PROVIDERS:
        sys.exit(f"Error: unknown provider '{provider}'. Known: {', '.join(_PROVIDERS)}")
    {"set": _set, "get": _get, "remove": _remove}[action](provider)
