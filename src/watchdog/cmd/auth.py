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
import sys
from getpass import getpass
from pathlib import Path

from watchdog.cmd import base
from watchdog.cmd.base import _BOLD, _CYAN, _DIM, _GREEN, _RESET, _YELLOW

_MODES = ("auto", "subscription", "api-key")

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


def claude_code_logged_in() -> bool:
    """Best-effort check that the `claude` CLI is logged in.

    The credentials file exists after an OAuth login. On macOS the CLI may instead
    store credentials in the Keychain, so a False here does not prove logged-out —
    the definitive test is an Agent SDK call succeeding without an API key.
    """
    return _claude_code_creds_path().exists()


def _load_state() -> dict:
    path = _credentials_path()
    if not path.exists():
        return {"mode": "auto", "keys": {}}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        sys.exit("Error: credentials file is corrupt; remove it and re-add your keys.")
    data.setdefault("mode", "auto")
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
    mode = _load_state().get("mode", "auto")
    key = get_api_key(provider)

    if mode == "api-key":
        return {"mode": "api-key", "key": key} if key else {
            "mode": "none", "reason": "api-key mode is set but no key is configured"}
    if mode == "subscription":
        return {"mode": "subscription"}
    # auto — mirror the SDK's own order: key wins, else Claude Code login.
    if key:
        return {"mode": "api-key", "key": key}
    if claude_code_logged_in():
        return {"mode": "subscription"}
    return {"mode": "none", "reason": "no API key set and Claude Code is not logged in"}


# ── command surface ───────────────────────────────────────────────────────────

def _status() -> None:
    state = _load_state()
    mode = state.get("mode", "auto")
    resolved = resolve_auth()

    print()
    print(f"  {_BOLD}Authentication{_RESET}  {_DIM}{_credentials_path()}{_RESET}")
    print()
    print(f"  {_DIM}Mode{_RESET}           {_CYAN}{mode}{_RESET}"
          + (f" {_DIM}→ {resolved['mode']}{_RESET}" if mode == "auto" else ""))

    key, source = (get_api_key(), None)
    meta = _PROVIDERS["anthropic"]
    if os.environ.get(meta["env"]):
        source = f"from ${meta['env']}"
    elif _load_state()["keys"].get("anthropic"):
        source = "stored"
    key_str = f"{_CYAN}{_mask(key)}{_RESET} {_DIM}({source}){_RESET}" if key else f"{_YELLOW}(not set){_RESET}"
    print(f"  {_DIM}API key{_RESET}        {key_str}")

    cc = claude_code_logged_in()
    cc_str = f"{_GREEN}detected{_RESET}" if cc else f"{_YELLOW}not detected{_RESET} {_DIM}(or stored in the macOS Keychain){_RESET}"
    print(f"  {_DIM}Claude Code{_RESET}    {cc_str}")
    print()

    if resolved["mode"] == "none":
        print(f"  {_YELLOW}No usable auth:{_RESET} {resolved['reason']}.")
        print(f"  {_DIM}Run{_RESET} {_CYAN}watchdog auth set{_RESET} {_DIM}(API key) or{_RESET} {_CYAN}claude{_RESET} {_DIM}(subscription login).{_RESET}")
    elif mode == "subscription" and os.environ.get(meta["env"]):
        print(f"  {_YELLOW}Warning:{_RESET} ${meta['env']} is set — the Agent SDK uses it before the")
        print(f"  {_DIM}subscription login, so runs would be metered despite subscription mode.{_RESET}")
    else:
        billed = "metered API key" if resolved["mode"] == "api-key" else "Claude Code subscription (not metered)"
        print(f"  {_DIM}Runs will authenticate with:{_RESET} {_BOLD}{billed}{_RESET}")
    print()


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
            print(f"  {_YELLOW}Note:{_RESET} Claude Code login not detected — run {_CYAN}claude{_RESET} to log in")
            print(f"  {_DIM}(or it may be in the macOS Keychain, which can't be checked here).{_RESET}")
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
