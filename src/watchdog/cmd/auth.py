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

State lives in `~/.watchdog/credentials.json` (mode 0600): `{"mode", "keys": {...}}`, where a
provider holds one key or several labelled ones (#690, D290). Under the desktop app each key is an
encrypted blob only the app can open (`watchdog.keystore`, D295); this process sees the keys the app
gave it, and a terminal command sees none and must use the environment variable. An investigation can choose which
labelled key it bills (`.watchdog/settings.json` in its folder); `resolve_key` is the one place a
key is chosen. The environment variable always takes precedence over a stored key.
"""

import json
import os
import subprocess
import sys
from getpass import getpass
from pathlib import Path

from watchdog import defaults, keystore
from watchdog.cmd import base
from watchdog.cmd.base import _BOLD, _CYAN, _DIM, _GREEN, _RESET, _YELLOW
from watchdog.interactive import CANCELLED, confirm, pick
from watchdog.pipeline.json_io import _read_json, _read_json_or, write_private_json

# Providers whose keys watchdog manages. `anthropic` covers both Claude backends (one
# ANTHROPIC_API_KEY); each OpenAI-compatible provider has its own key, independent of the Claude
# auth mode. `local` and `openrouter` also take a user-supplied base URL (`base_url_key`/
# `base_url_env`, `default_base_url` where there is one), and `requires_key: False` lets `local`
# run without a key, since most local runners don't check for one (D139).
_PROVIDERS: dict[str, dict] = {
    "anthropic": {
        "label":  "Anthropic — Claude API / Agent SDK",
        "env":    "ANTHROPIC_API_KEY",
        "prefix": "sk-ant-",
    },
    "openai": {
        "label":  "OpenAI",
        "env":    "OPENAI_API_KEY",
        "prefix": "sk-",
    },
    "deepseek": {
        "label":  "DeepSeek",
        "env":    "DEEPSEEK_API_KEY",
        "prefix": "sk-",
    },
    "gemini": {
        "label":  "Google Gemini",
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
        data = _read_json(path)
    except json.JSONDecodeError:
        sys.exit("Error: credentials file is corrupt; remove it and re-add your keys.")
    data.setdefault("mode", None)
    data.setdefault("keys", {})
    return data


def _save_state(state: dict) -> None:
    base.WATCHDOG_HOME.mkdir(parents=True, exist_ok=True)
    path = _credentials_path()
    write_private_json(path, state)


def _mask(key: str) -> str:
    if len(key) <= 8:
        return "…" + key[-2:] if len(key) > 2 else "(set)"
    return f"{key[:10]}…{key[-4:]}"


# ── labelled keys (#690, D290) ──────────────────────────────────────────────────
# A provider can hold several keys, each with a stable id and a short label ("Personal",
# "Globe"), one of them the default. On disk `keys[provider]` is either the old single string
# (read as one key labelled "Default", id "default") or {"default": <id>, "items": [{"id",
# "label", "key"}, ...]}. A provider whose only key is that "Default" one is written back as
# the plain string, so someone who never labels a key keeps a file older versions can read.
#
# Under the app every secret (that single string, or an item's "key") is an encrypted blob
# instead (D295). In memory an item is {"id", "label", "key", "stored", "masked"}: `stored` is what
# the file holds and is written back unchanged, `key` the usable key, or None when the stored blob
# wasn't given to this process ("locked"), and `masked` what a display shows.

DEFAULT_LABEL = "Default"
DEFAULT_ID = "default"
LABEL_MAX = 40


class KeyChoiceError(Exception):
    """An investigation chose a labelled key this computer doesn't have (deleted, or never added
    here because the folder came from someone else). Raised before any model call: falling back
    to another key would bill another account (D290)."""


class KeyLockedError(KeyChoiceError):
    """The key that would pay is stored encrypted by the app and this process wasn't given it: a
    terminal command, or an app process the app couldn't decrypt the key for (D295). A
    `KeyChoiceError`, so every path that stops before a model call for a missing key stops here
    too, and none falls back to another key."""


def _secret(stored) -> tuple[str | None, str | None] | None:
    """A stored secret as `(key, masked)`, `key` None when it is an encrypted blob this process
    wasn't given. None when `stored` isn't a secret at all."""
    if isinstance(stored, str):
        return (stored, _mask(stored)) if stored else None
    if keystore.is_blob(stored):
        key = keystore.reveal(stored)
        masked = stored.get("masked") if isinstance(stored.get("masked"), str) else None
        return key, masked or (_mask(key) if key else "(encrypted)")
    return None


def _item(kid: str, label: str, stored) -> dict | None:
    secret = _secret(stored)
    if secret is None:
        return None
    return {"id": kid, "label": label, "key": secret[0], "stored": stored, "masked": secret[1]}


def _key_items(state: dict, provider: str) -> tuple[list[dict], str | None]:
    """A provider's stored keys as `[{"id", "label", "key", "stored", "masked"}]` and the
    default's id. `key` is None for an encrypted key this process wasn't given (D295)."""
    raw = (state.get("keys") or {}).get(provider)
    if isinstance(raw, str) or keystore.is_blob(raw):
        item = _item(DEFAULT_ID, DEFAULT_LABEL, raw)
        return ([item], DEFAULT_ID) if item else ([], None)
    if not isinstance(raw, dict):
        return [], None
    items = [it for it in (_item(str(i["id"]), str(i.get("label") or i["id"]), i.get("key"))
                           for i in raw.get("items") or [] if isinstance(i, dict) and i.get("id"))
             if it is not None]
    ids = [i["id"] for i in items]
    default = raw.get("default") if raw.get("default") in ids else (ids[0] if ids else None)
    return items, default


def _put_key_items(state: dict, provider: str, items: list[dict], default: str | None) -> None:
    keys = state.setdefault("keys", {})
    if not items:
        keys.pop(provider, None)
    elif len(items) == 1 and items[0]["id"] == DEFAULT_ID and items[0]["label"] == DEFAULT_LABEL:
        keys[provider] = items[0]["stored"]
    else:
        ids = [i["id"] for i in items]
        keys[provider] = {"default": default if default in ids else ids[0],
                          "items": [{"id": i["id"], "label": i["label"], "key": i["stored"]} for i in items]}


def _has_blobs(state: dict) -> bool:
    return any(i["stored"] is not None and not isinstance(i["stored"], str)
               for p in _PROVIDERS for i in _key_items(state, p)[0])


APP_STORES_KEYS = ("Keys on this computer are stored by the Watchdog app, encrypted, so the command "
                   "line can't change them. Add or replace keys in the app, under Settings → Models & keys.")


def _seal(state: dict, key: str, enc: dict | None) -> dict | str:
    """What the file stores for a new or replaced `key` (D295): the app's encrypted blob when it
    sent one, else the key itself — but never plaintext beside the app's encrypted keys, or when
    the app has said it encrypts. `remember`s the key, so this process can use it at once."""
    if enc is not None:
        if not keystore.is_blob(enc):
            raise ValueError("The key arrived in a form Watchdog doesn't recognise, so it wasn't saved.")
        blob = {"enc": enc["enc"], "data": enc["data"], "masked": _mask(key)}
        keystore.remember(blob, key)
        return blob
    if keystore.encrypting():
        raise ValueError("Watchdog couldn't encrypt this key, so it wasn't saved. Restart Watchdog "
                         "and try again.")
    if keystore.status()["store"] is None and _has_blobs(state):
        raise ValueError(APP_STORES_KEYS)
    return key


def clean_label(label, provider: str, items: list[dict], *, except_id: str | None = None) -> str:
    """A key's label, validated: plain text, at most `LABEL_MAX` characters, unique (ignoring
    case) among the provider's other keys. Raises ValueError with a message fit to show."""
    text = " ".join(str(label or "").split())
    if not text:
        raise ValueError("Give the key a name, such as Personal or Work.")
    if len(text) > LABEL_MAX:
        raise ValueError(f"Keep the name to {LABEL_MAX} characters or fewer.")
    if any(not ch.isprintable() for ch in text):
        raise ValueError("The name can only contain plain text.")
    if any(i["label"].casefold() == text.casefold() and i["id"] != except_id for i in items):
        raise ValueError(f"There is already a {_PROVIDERS.get(provider, {}).get('label', provider).split(' — ')[0]} "
                         f"key named “{text}”.")
    return text


def _new_key_id(items: list[dict]) -> str:
    import secrets
    taken = {i["id"] for i in items}
    while True:
        kid = "k-" + secrets.token_hex(4)
        if kid not in taken:
            return kid


def list_keys(provider: str, state: dict | None = None) -> list[dict]:
    """A provider's stored keys, masked: `[{"id", "label", "masked", "default"}]`. The key itself
    never leaves this module through here."""
    items, default = _key_items(state if state is not None else _load_state(), provider)
    return [{"id": i["id"], "label": i["label"], "masked": i["masked"], "default": i["id"] == default,
             "encrypted": not isinstance(i["stored"], str), "locked": i["key"] is None}
            for i in items]


def add_key(provider: str, label: str, key: str, *, make_default: bool = False,
            enc: dict | None = None) -> str:
    """Store a new labelled key for `provider` and return its id. The provider's first key
    becomes its default. `enc` is the app's encrypted form of `key` (D295)."""
    key = (key or "").strip()
    if not key:
        raise ValueError("Paste a key.")
    state = _load_state()
    items, default = _key_items(state, provider)
    text = clean_label(label, provider, items)
    kid = DEFAULT_ID if not items and text == DEFAULT_LABEL else _new_key_id(items)
    stored = _seal(state, key, enc)
    items.append({"id": kid, "label": text, "key": key, "stored": stored, "masked": _mask(key)})
    _put_key_items(state, provider, items, kid if (make_default or default is None) else default)
    _save_state(state)
    return kid


def _find(items: list[dict], key_id: str) -> dict:
    for i in items:
        if i["id"] == key_id:
            return i
    raise ValueError("That key is no longer stored on this computer.")


def rename_key(provider: str, key_id: str, label: str) -> None:
    state = _load_state()
    items, default = _key_items(state, provider)
    item = _find(items, key_id)
    item["label"] = clean_label(label, provider, items, except_id=key_id)
    _put_key_items(state, provider, items, default)
    _save_state(state)


def replace_key(provider: str, key_id: str, key: str, *, enc: dict | None = None) -> None:
    """Replace the secret of an existing labelled key, keeping its id and label (so every
    investigation that chose it keeps billing the account it names)."""
    key = (key or "").strip()
    if not key:
        raise ValueError("Paste a key.")
    state = _load_state()
    items, default = _key_items(state, provider)
    _find(items, key_id).update(key=key, stored=_seal(state, key, enc), masked=_mask(key))
    _put_key_items(state, provider, items, default)
    _save_state(state)


def set_default_key(provider: str, key_id: str) -> None:
    state = _load_state()
    items, _default = _key_items(state, provider)
    _find(items, key_id)
    _put_key_items(state, provider, items, key_id)
    _save_state(state)


def delete_key(provider: str, key_id: str) -> None:
    """Remove one labelled key. When it was the default, the first remaining key becomes the
    default; an investigation that chose the deleted key stops at its next run (D290)."""
    state = _load_state()
    items, default = _key_items(state, provider)
    _find(items, key_id)
    items = [i for i in items if i["id"] != key_id]
    _put_key_items(state, provider, items, default if default != key_id else None)
    _save_state(state)


def store_default_key(state: dict, provider: str, key: str, *, enc: dict | None = None) -> None:
    """Set the provider's default key to `key` — the one-key-per-provider path the setup wizard,
    `watchdog auth` and the app's single "Replace" use. A provider with no key gets one labelled
    "Default"; otherwise the default key's secret is replaced and its label kept. Mutates
    `state`; the caller saves it. `enc` is the app's encrypted form of `key` (D295); raises
    ValueError when the key may not be written as plain text."""
    items, default = _key_items(state, provider)
    secret = {"key": key, "stored": _seal(state, key, enc), "masked": _mask(key)}
    if default is None:
        items, default = [{"id": DEFAULT_ID, "label": DEFAULT_LABEL, **secret}], DEFAULT_ID
    else:
        _find(items, default).update(secret)
    _put_key_items(state, provider, items, default)


def has_stored_key(state: dict, provider: str) -> bool:
    return bool(_key_items(state, provider)[0])


def _env_label(provider: str) -> str:
    return f"{_PROVIDERS[provider]['env']} (environment)"


# ── the investigation's choice ─────────────────────────────────────────────────
# `.watchdog/settings.json` inside the investigation's folder, so the choice travels with it:
# {"schema_version": 1, "keys": {"<provider>": {"id": "<key id>", "label": "<label>"}}}. The
# key itself is never written there. The label is kept so a computer that doesn't have the id
# (a shared folder) can still name the account it should bill — and match a key of that label.

_SETTINGS_VERSION = 1

# The investigation whose choices apply when a caller doesn't name one — set by a run's entry
# point (`orchestrate._begin_usage_run`, `recheck.run`) in its own process. The app's sidecar,
# which serves several investigations, never sets it and always passes `vault=` explicitly.
_scope_vault: Path | None = None


def use_investigation(vault: Path | None) -> None:
    """Make `vault`'s key choices apply to every key lookup in this process that names no
    investigation (a run's model calls). None clears it."""
    global _scope_vault
    _scope_vault = Path(vault) if vault is not None else None


def investigation_settings_path(vault: Path) -> Path:
    return Path(vault) / ".watchdog" / "settings.json"


def _read_investigation_settings(vault: Path) -> dict:
    data = _read_json_or(investigation_settings_path(vault), {})
    return data if isinstance(data, dict) else {}


def investigation_keys(vault: Path) -> dict[str, dict]:
    """The investigation's chosen key per provider: `{provider: {"id", "label"}}`. A provider
    absent here uses its default key."""
    raw = _read_investigation_settings(vault).get("keys")
    out: dict[str, dict] = {}
    if isinstance(raw, dict):
        for provider, choice in raw.items():
            if provider in _PROVIDERS and isinstance(choice, dict) and (choice.get("id") or choice.get("label")):
                out[provider] = {"id": choice.get("id"), "label": choice.get("label")}
    return out


def choose_key(vault: Path, provider: str, key_id: str | None) -> None:
    """Record which of this computer's keys the investigation uses for `provider`; None returns
    it to the default. Only the id and label are written, never the key."""
    if provider not in _PROVIDERS:
        raise ValueError(f"“{provider}” isn't a model provider Watchdog knows.")
    data = _read_investigation_settings(vault)
    keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
    if key_id is None:
        keys.pop(provider, None)
    else:
        items, _default = _key_items(_load_state(), provider)
        item = _find(items, key_id)
        keys[provider] = {"id": item["id"], "label": item["label"]}
    data["schema_version"] = _SETTINGS_VERSION
    data["keys"] = keys
    path = investigation_settings_path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _match_choice(items: list[dict], choice: dict) -> dict | None:
    """The stored key an investigation's choice names: by id, else by label (ignoring case) —
    a shared folder's choice names the account, and this computer's key of that name is it."""
    for i in items:
        if choice.get("id") and i["id"] == choice["id"]:
            return i
    label = (choice.get("label") or "").casefold()
    for i in items:
        if label and i["label"].casefold() == label:
            return i
    return None


def missing_key_message(provider: str, label: str | None) -> str:
    name = _PROVIDERS.get(provider, {}).get("label", provider).split(" — ")[0]
    who = f"the {name} key named “{label}”" if label else f"a {name} key"
    return (f"This investigation is set to bill {who}, which isn't stored on this computer. "
            f"Add a key with that name under Settings → Models & keys, or choose another key "
            f"under Billing on this investigation's Overview. Nothing has been sent.")


def locked_key_message(provider: str, label: str | None) -> str:
    meta = _PROVIDERS.get(provider, {})
    name = meta.get("label", provider).split(" — ")[0]
    from watchdog.appmode import under_app
    if under_app():
        which = f"the {name} key named “{label}”" if label and label != DEFAULT_LABEL else f"your {name} key"
        return (f"Watchdog couldn't read {which} from this computer's secure storage. Restart "
                f"Watchdog; if this keeps happening, add the key again under Settings → Models & "
                f"keys. Nothing has been sent.")
    return (f"Your {name} key is stored by the Watchdog app, encrypted, and the command line can't "
            f"read it. To use the command line, set {meta.get('env', 'the key')} in your "
            f"environment. Nothing has been sent.")


def _usable(provider: str, item: dict) -> dict:
    if item["key"] is None:
        raise KeyLockedError(locked_key_message(provider, item["label"]))
    return item


def resolve_key(provider: str = "anthropic", vault: Path | None = None) -> dict:
    """The one resolver for which key pays for `provider` in an investigation (D290):
    `{"key", "id", "label", "source"}` with `source` one of `env`, `chosen`, `default`, `none`.

    The environment variable overrides everything, as before. Otherwise the investigation's
    choice applies (`vault`, or the process's `use_investigation` scope); a choice this computer
    can't satisfy raises `KeyChoiceError` rather than falling back to another account. With no
    choice, the provider's default key."""
    meta = _PROVIDERS.get(provider)
    if meta and os.environ.get(meta["env"]):
        return {"key": os.environ[meta["env"]], "id": None, "label": _env_label(provider), "source": "env"}
    items, default = _key_items(_load_state(), provider)
    target = vault if vault is not None else _scope_vault
    choice = investigation_keys(target).get(provider) if target is not None else None
    if choice:
        item = _match_choice(items, choice)
        if item is None:
            raise KeyChoiceError(missing_key_message(provider, choice.get("label")))
        item = _usable(provider, item)
        return {"key": item["key"], "id": item["id"], "label": item["label"], "source": "chosen"}
    for i in items:
        if i["id"] == default:
            i = _usable(provider, i)
            return {"key": i["key"], "id": i["id"], "label": i["label"], "source": "default"}
    return {"key": None, "id": None, "label": None, "source": "none"}


def secrets_for_run(vault: Path | None) -> dict[str, str]:
    """The encrypted keys a `watchdog` job in `vault` may use, as `{blob data: key}` for
    `keystore.read_stdin` (D295): per provider, only the key the investigation resolves to (its
    choice, else the default), and nothing where the environment variable overrides it or the key
    is stored in plain text (the job reads that from the file, as before). A job outside an
    investigation gets nothing."""
    if vault is None:
        return {}
    state = _load_state()
    out: dict[str, str] = {}
    for p, meta in _PROVIDERS.items():
        if os.environ.get(meta["env"]):
            continue
        items, default = _key_items(state, p)
        choice = investigation_keys(vault).get(p)
        item = _match_choice(items, choice) if choice else next((i for i in items if i["id"] == default), None)
        if item and item["key"] and keystore.is_blob(item["stored"]):
            out[item["stored"]["data"]] = item["key"]
    return out


def get_api_key(provider: str = "anthropic", vault: Path | None = None) -> str | None:
    """Resolve a provider's API key: environment variable first, then the investigation's chosen
    key, then the default (`resolve_key`). Raises `KeyChoiceError` for a chosen key that's gone."""
    return resolve_key(provider, vault)["key"]


def default_key(provider: str, state: dict | None = None) -> str | None:
    """The key a provider uses with no investigation's choice in play: the environment variable,
    else the stored default. For status displays, which describe this computer, not a run."""
    meta = _PROVIDERS.get(provider)
    if meta and os.environ.get(meta["env"]):
        return os.environ[meta["env"]]
    items, default = _key_items(state if state is not None else _load_state(), provider)
    return next((i["key"] for i in items if i["id"] == default), None)


def default_masked(provider: str, state: dict | None = None) -> str | None:
    """`default_key`, masked — also for a default key that is stored encrypted and locked here."""
    meta = _PROVIDERS.get(provider)
    if meta and os.environ.get(meta["env"]):
        return _mask(os.environ[meta["env"]])
    items, default = _key_items(state if state is not None else _load_state(), provider)
    return next((i["masked"] for i in items if i["id"] == default), None)


def label_for_key(provider: str, key: str | None) -> str | None:
    """The label of the key whose secret is `key` — what usage records name as the account that
    paid (never the key). Derived from the key actually sent, so it can't disagree with it."""
    if not key:
        return None
    meta = _PROVIDERS.get(provider)
    if meta and os.environ.get(meta["env"]) == key:
        return _env_label(provider)
    for i in _key_items(_load_state(), provider)[0]:
        if i["key"] == key:
            return i["label"]
    return None


def check_investigation_keys(vault: Path, providers) -> None:
    """Raise `KeyChoiceError` if any of `providers` has a chosen key this computer lacks — the
    check a run makes before its first model call. An environment variable satisfies it."""
    for p in dict.fromkeys(providers):
        if p in _PROVIDERS:
            resolve_key(p, vault)


def run_providers(backends) -> list[str]:
    """The providers whose keys a run on `backends` would send (None is a bare Claude tier). A
    Claude stage counts only in api-key mode: on a subscription no key is sent."""
    from watchdog.model_client import provider_for_backend
    mode = _load_state().get("mode")
    out = []
    for b in backends:
        p = provider_for_backend(b)
        if p == "anthropic" and mode != "api-key":
            continue
        if p not in out:
            out.append(p)
    return out


def check_run_keys(vault: Path, backends) -> None:
    """Raise `KeyChoiceError` before a run's first model call when the investigation chose a key
    this computer lacks for a provider the run would use (D290)."""
    check_investigation_keys(vault, run_providers(backends))


def key_users(provider: str, key_id: str) -> list[str]:
    """Names of the registered investigations on this computer that chose this key — what the
    app warns about before a key is deleted. Investigations it doesn't know of aren't listed."""
    from watchdog.cmd.base import load_projects
    try:
        projects = load_projects()
    except Exception:
        return []
    items, _default = _key_items(_load_state(), provider)
    names = []
    for info in projects.values():
        try:
            choice = investigation_keys(Path(info["path"])).get(provider)
        except Exception:
            continue
        if choice and (_match_choice(items, choice) or {}).get("id") == key_id:
            names.append(info.get("name") or Path(info["path"]).name)
    return sorted(names)


def billing_summary(vault: Path, providers) -> list[dict]:
    """Which key will pay for each of `providers` in this investigation, for the app's notices:
    `[{"provider", "provider_label", "label", "source", "missing", "message", "several"}]`.
    Never includes the key."""
    state = _load_state()
    out = []
    for p in dict.fromkeys(providers):
        if p not in _PROVIDERS:
            continue
        row = {"provider": p, "provider_label": _PROVIDERS[p]["label"].split(" — ")[0],
               "label": None, "source": "none", "missing": False, "message": None,
               "several": len(_key_items(state, p)[0]) > 1}
        try:
            r = resolve_key(p, vault)
            row.update(label=r["label"], source=r["source"])
        except KeyChoiceError as e:
            choice = investigation_keys(vault).get(p) or {}
            row.update(label=choice.get("label"), source="chosen", missing=True, message=str(e))
        out.append(row)
    return out


def provider_requires_key(provider: str) -> bool:
    """Whether `provider` needs an API key to run — false only for `local` (#380), where most
    self-hosted runners don't check for one. Every other provider defaults to requiring one."""
    return _PROVIDERS.get(provider, {}).get("requires_key", True)


def _load_config() -> dict:
    from watchdog.cmd.base import CONFIG_FILE
    if not CONFIG_FILE.exists():
        return {}
    return _read_json_or(CONFIG_FILE, {}, catch=(json.JSONDecodeError,))


def _save_config(config: dict) -> None:
    from watchdog.cmd.base import CONFIG_FILE, WATCHDOG_HOME
    WATCHDOG_HOME.mkdir(parents=True, exist_ok=True)
    write_private_json(CONFIG_FILE, config)


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
    return not provider_requires_key(provider) or bool(default_key(provider))


def resolve_auth(provider: str = "anthropic", vault: Path | None = None) -> dict:
    """How a model backend should authenticate for this run — the resolver #118 calls.

    Returns one of:
      {"mode": "api-key", "key": "<key>"}   → pass the key (metered)
      {"mode": "subscription"}              → pass no key; the SDK uses Claude Code's login
      {"mode": "none", "reason": "<why>"}   → nothing configured; caller errors with guidance
    """
    mode = _load_state().get("mode")

    if mode is None:
        return {"mode": "none", "reason": "No model provider is set up. Sign in or add a key under Settings → Models & keys."}
    if mode == "subscription":
        return {"mode": "subscription"}
    # api-key — the investigation's chosen Anthropic key, or the default (D290)
    key = get_api_key(provider, vault)
    return {"mode": "api-key", "key": key} if key else {
        "mode": "none", "reason": "api-key mode is set but no key is configured — run `watchdog settings auth`"}


# ── model routing → provider (for the status display) ──────────────────────────

def _ingest_stage_provider(value: str | None) -> str:
    """Best-effort `[backend:]model` config value → provider name, for the status display
    only — not validated the way `cmd/ingest.py`'s `_resolve_stage` is; it just needs to
    answer "which provider is this stage pointed at" (#325)."""
    backend, _ = defaults.split_backend_model(value or "")
    if backend is None:
        return "anthropic"          # bare Claude tier (haiku/sonnet/opus)
    from watchdog.model_client import provider_for_backend
    return provider_for_backend(backend)


_INGEST_STAGES = (("classifier_model", defaults.CLASSIFIER_MODEL),
                  ("extractor_model", defaults.EXTRACTOR_MODEL),
                  ("finalizer_model", defaults.FINALIZER_MODEL))


# ── command surface ───────────────────────────────────────────────────────────

def status_data() -> dict:
    """What `watchdog auth` reports, as data — the printer below and the desktop app both read it.

    `claude` is Claude Code's own access mode (`logged_in` only in subscription mode, `key_masked`
    and `key_source` only in api-key mode). `stages` says which provider each pipeline stage is
    routed to and whether it can run; `keys` lists every provider with a key available (masked —
    the key itself never leaves this function); `base_urls` the user-supplied endpoints."""
    state = _load_state()
    mode = state.get("mode")
    meta = _PROVIDERS["anthropic"]
    env_set = bool(os.environ.get(meta["env"]))

    claude = {"mode": mode, "logged_in": None, "env_key_set": env_set,
              "key_masked": None, "key_source": None}
    if mode == "subscription":
        claude["logged_in"] = claude_code_logged_in()
    elif mode is not None:   # api-key
        masked = default_masked("anthropic", state)
        if masked:
            claude["key_masked"] = masked
            claude["key_source"] = "env" if env_set else "stored"

    # Ingestion: which provider each pipeline stage is actually routed to, and whether that
    # provider is ready — the thing that actually determines whether `watchdog dig`/`watchdog
    # bark` will work, independent of Claude's mode above (#325).
    from watchdog.cmd.base import CONFIG_FILE
    config: dict = {}
    if CONFIG_FILE.exists():
        config = _read_json_or(CONFIG_FILE, {}, catch=(json.JSONDecodeError,))

    stages = []
    for stage_key, default in _INGEST_STAGES:
        value = config.get(stage_key) or default
        provider = _ingest_stage_provider(value)
        ready = (mode == "subscription" or bool(default_key("anthropic", state))) if provider == "anthropic" \
            else provider_ready(provider)
        stages.append({"stage": stage_key[: -len("_model")], "config_key": stage_key,
                       "value": value, "provider": provider, "ready": ready})

    # Every provider with a key available, whether or not a stage is routed to it — the
    # definitive list of what's stored, Anthropic included (#482). A stored Anthropic key is
    # only ever actually used while Claude Code mode above is api-key, so it's flagged inactive
    # rather than silently vanishing from the list while on subscription.
    shown_providers = {_ingest_stage_provider(config.get(k) or d) for k, d in _INGEST_STAGES}
    keys = []
    for p in _PROVIDERS:
        masked = default_masked(p, state)
        if not masked:
            continue
        if p == "anthropic" and mode != "api-key":
            status = f"inactive — {mode} mode" if mode else "inactive — not configured"
        else:
            status = "in use" if p in shown_providers else "unused"
        env = bool(os.environ.get(_PROVIDERS[p]["env"]))
        keys.append({"provider": p, "masked": masked, "status": status,
                     "source": "env" if env else "stored",
                     # Stored encrypted by the app and not readable by this process (D295): a
                     # terminal command, which must use the environment variable instead.
                     "locked": not env and default_key(p, state) is None,
                     "labelled": list_keys(p, state)})

    # Providers with a user-supplied base URL (local, openrouter — #380), whichever is set.
    base_urls = [{"provider": p, "url": u}
                 for p, u in ((p, get_base_url(p)) for p, m in _PROVIDERS.items() if m.get("base_url_key"))
                 if u]
    return {"claude": claude, "stages": stages, "keys": keys, "base_urls": base_urls}


def _status() -> None:
    d = status_data()
    claude = d["claude"]
    mode = claude["mode"]
    meta = _PROVIDERS["anthropic"]

    print()
    print(f"  {_BOLD}Model access{_RESET}  {_DIM}{_credentials_path()}{_RESET}")
    print()

    # Claude Code's own access mode — subscription login vs. a metered API key. Whether a
    # stored Anthropic key is actually in play is reported down in Providers, not here (#482):
    # it depends on this mode, so it belongs next to the other providers' key status, not
    # duplicated in both places.
    print(f"  {_BOLD}Claude Code{_RESET}")
    if mode is None:
        print(f"  {_YELLOW}Not configured.{_RESET}")
        print(f"  {_DIM}Answer the prompt below, or run{_RESET} {_CYAN}watchdog setup{_RESET}{_DIM}.{_RESET}")
    elif mode == "subscription":
        cc_str = f"{_GREEN}detected{_RESET}" if claude["logged_in"] else f"{_YELLOW}not detected{_RESET}"
        print(f"  {_DIM}mode{_RESET}  {_CYAN}subscription{_RESET}  {_DIM}(Claude Code login {_RESET}{cc_str}{_DIM}){_RESET}")
        if claude["env_key_set"]:
            print(f"  {_YELLOW}Warning:{_RESET} ${meta['env']} is set — the Agent SDK uses it before the")
            print(f"  {_DIM}subscription login, so runs would be metered. Unset it to use the subscription.{_RESET}")
    else:  # api-key
        if claude["key_masked"]:
            where = f"${meta['env']}" if claude["key_source"] == "env" else "stored"
            print(f"  {_DIM}mode{_RESET}  {_CYAN}api-key{_RESET}  {_DIM}({_RESET}{_CYAN}{claude['key_masked']}{_RESET}{_DIM}, {where}){_RESET}")
        else:
            print(f"  {_DIM}mode{_RESET}  {_CYAN}api-key{_RESET}  {_YELLOW}(no key set — add one below){_RESET}")
    print()

    print(f"  {_BOLD}Ingestion{_RESET}  {_DIM}— classifier / extractor / finalizer{_RESET}")
    for st in d["stages"]:
        mark = f"{_GREEN}✓{_RESET}" if st["ready"] else f"{_YELLOW}✗{_RESET}"
        # Anthropic is the one provider with a mode of its own — name it inline here so a
        # Claude-routed stage's actual billing (subscription vs. metered) is visible without
        # cross-referencing the Claude Code section above.
        detail = f"{st['provider']} · {mode}" if st["provider"] == "anthropic" and mode else st["provider"]
        print(f"  {mark} {_DIM}{st['stage']:<11}{_RESET}{_CYAN}{st['value']}{_RESET}  {_DIM}({detail}){_RESET}")
    print()

    if d["keys"]:
        print(f"  {_DIM}Providers{_RESET}")
        for k in d["keys"]:
            where = f"${_PROVIDERS[k['provider']]['env']}" if k["source"] == "env" else (
                "stored by the app, encrypted" if k.get("locked") else "stored")
            print(f"  {_DIM}{k['provider']:<13}{_RESET}{_CYAN}{k['masked']}{_RESET} {_DIM}({where}, {k['status']}){_RESET}")
            if len(k["labelled"]) > 1:   # several labelled keys (#690): name each, mark the default
                for item in k["labelled"]:
                    tag = " — default" if item["default"] else ""
                    print(f"  {'':<13}{_DIM}{item['label']}  {item['masked']}{tag}{_RESET}")
        if any(k.get("locked") for k in d["keys"]):
            print(f"  {_YELLOW}Note:{_RESET} {_DIM}keys stored by the Watchdog app are encrypted and the command line "
                  f"can't read them; set the provider's environment variable (such as{_RESET} "
                  f"{_CYAN}ANTHROPIC_API_KEY{_RESET}{_DIM}) to use one here.{_RESET}")
        print()

    if d["base_urls"]:
        print(f"  {_DIM}Base URLs{_RESET}")
        for b in d["base_urls"]:
            print(f"  {_DIM}{b['provider']:<13}{_RESET}{_CYAN}{b['url']}{_RESET}")
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
    try:
        store_default_key(state, provider, key)
    except ValueError as e:   # the app stores this computer's keys, encrypted (D295)
        print(f"  {_YELLOW}!{_RESET}  {e}")
        return False
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

    Picking, say, `gemini:gemini-3.5-flash-lite` for a stage used to leave that stage silently
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
    if provider_requires_key(provider) and not default_key(provider):
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
            try:
                store_default_key(state, "anthropic", key)
            except ValueError as e:   # the app stores this computer's keys, encrypted (D295)
                print(f"\n  {_YELLOW}!{_RESET}  {e}")
                return printed
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
    """Interactive auth setup for `watchdog setup`, in three steps that no longer nest one
    provider's setup inside another's (#690 design pass — the old flow gated everything behind
    a Claude subscription/API-key choice, with other providers only reachable as an "escape
    hatch" from it, which read as assuming Claude even for someone who never intended to use it
    for ingestion):

    1. Claude Code access (subscription or API key) — not a preference, a hard requirement: the
       interactive investigation commands run inside Claude Code, on Claude, always. Nothing
       about ingestion is mentioned here.
    2. Ingestion provider — a flat, equal-weight pick that includes Claude as one option among
       the rest (`_choose_ingestion_provider`), not a default with everything else framed as an
       alternative. Picking Claude while on subscription auth surfaces the token/session-limit
       cost as a consequence of that specific combination, not as a gate everyone passes through.
    3. Other providers, optional — add a key for a provider you're not routing ingestion to,
       just to have on hand for a later `watchdog configure` stage override.

    Persists each choice as it's made; skips cleanly off a terminal. `interactive` is overridable
    for testing."""
    if interactive is None:
        interactive = sys.stdin.isatty()

    print()
    print(f"  {_BOLD}Claude Code access{_RESET}")
    print(f"  {_DIM}Claude Code powers Watchdog's interactive investigation session — how{_RESET}")
    print(f"  {_DIM}should it sign in?{_RESET}")

    if not interactive:
        print(f"  {_DIM}Non-interactive — set this later with{_RESET} {_CYAN}watchdog settings auth{_RESET}{_DIM}.{_RESET}")
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
    _apply_anthropic_choice(state, choice, show_detection=False)

    _choose_ingestion_provider(state)
    _offer_extra_providers(state)

    print(f"\n  {_DIM}Tune which model runs each stage anytime with{_RESET} {_CYAN}watchdog settings{_RESET} "
          f"{_DIM}(extractor_model, finalizer_model, extractor_effort, …).{_RESET}")


_SUBSCRIPTION_CONCURRENCY = defaults.SUBSCRIPTION_CONCURRENCY


def _maybe_tune_concurrency_for_subscription() -> bool:
    """Lower `extract_concurrency` when Claude subscription auth is chosen and ingestion stays
    on it — from `watchdog setup` (issue #400) or a later mode switch via `watchdog auth`
    (#493): every concurrent extraction on that path shares one Claude Code session, and the
    built-in default of 5 reliably throttles it — one call in a 5-way batch was observed
    running at ~1/5 the normal token rate. Only touches the setting when it has never been
    explicitly configured — a prior `watchdog configure extract_concurrency` (including an
    earlier auto-tune) is left alone, since that's a deliberate choice this shouldn't silently
    overwrite. Returns whether anything was printed, for the caller's blank-line bookkeeping."""
    config: dict = {}
    if base.CONFIG_FILE.exists():
        config = _read_json_or(base.CONFIG_FILE, {}, catch=(json.JSONDecodeError,))
    if "extract_concurrency" in config:
        return False

    config["extract_concurrency"] = _SUBSCRIPTION_CONCURRENCY
    base.WATCHDOG_HOME.mkdir(parents=True, exist_ok=True)
    write_private_json(base.CONFIG_FILE, config)
    print(f"\n  {_GREEN}✓{_RESET}  Detected Claude subscription auth — {_BOLD}extract_concurrency{_RESET} "
          f"automatically set to {_BOLD}{_SUBSCRIPTION_CONCURRENCY}{_RESET}.")
    print(f"  {_DIM}Concurrent extractions share one Claude Code session's rate limit; raise it back "
          f"with{_RESET} {_CYAN}watchdog settings extract_concurrency{_RESET}{_DIM}.{_RESET}")
    return True


def _maybe_restore_concurrency_from_subscription() -> bool:
    """Undo the tune above when switching away from subscription auth (#493 follow-up): once
    ingestion is on a metered key, extraction no longer shares one Claude Code session's rate
    limit, so a still-active auto-tuned `extract_concurrency: 3` would otherwise silently cap
    throughput forever — the "never overwrite a deliberate value" guard above means nothing else
    would ever raise it back on its own. Only clears the setting when it's still exactly
    `_SUBSCRIPTION_CONCURRENCY`; a coincidentally-matching manual `watchdog configure` value of 3
    is indistinguishable from the auto-tune and is left alone, same as the tune itself never
    overwrites a pre-existing value."""
    config = _load_config()
    if config.get("extract_concurrency") != _SUBSCRIPTION_CONCURRENCY:
        return False

    del config["extract_concurrency"]
    _save_config(config)
    from watchdog.cmd.ingest import _DEFAULT_EXTRACT_CONCURRENCY
    print(f"\n  {_GREEN}✓{_RESET}  {_BOLD}extract_concurrency{_RESET} reset to the metered default "
          f"({_BOLD}{_DEFAULT_EXTRACT_CONCURRENCY}{_RESET}).")
    return True


def _choose_ingestion_provider(state: dict) -> None:
    """Step 2 of `watchdog setup`'s auth flow (#690 design pass): which provider handles
    ingestion (classifier/extractor/finalizer_model), asked as a flat, equal-weight pick that
    includes Claude alongside every other provider — not a default the rest are framed as an
    escape hatch from. Independent of the Claude Code access step above: picking Claude here
    while that step chose subscription auth is what triggers the token/session-limit cost note
    and the concurrency auto-tune, as a consequence of that specific combination rather than a
    gate everyone sees regardless of what they actually want."""
    print(f"\n  {_BOLD}Ingestion{_RESET}")
    print(f"  {_DIM}Which provider should handle ingestion — classifying, extracting, and{_RESET}")
    print(f"  {_DIM}synthesizing documents (watchdog dig/watchdog bark)? Independent of the{_RESET}")
    print(f"  {_DIM}login above; pick whichever you actually want to use.{_RESET}")

    extras = [p for p in _PROVIDERS if p != "anthropic"]
    items = ["Claude"] + [_PROVIDERS[p]["label"].split(" — ")[0] for p in extras]
    result = pick(items, 0, title="Ingestion provider?")
    if result is CANCELLED:
        return
    if result == 0:
        _route_ingestion_to_claude(state)
        return
    _route_ingestion_to_provider(state, extras[result - 1])


def _route_ingestion_to_claude(state: dict) -> None:
    """Claude picked for ingestion in `_choose_ingestion_provider`. Nothing to configure — the
    default classifier/extractor/finalizer_model values already point at bare Claude tiers — but
    on subscription auth this is the one combination worth a heads-up: ingesting more than a few
    documents that way can burn through a Pro plan's session limits fast (issue #400), so it also
    triggers the same concurrency auto-tune `_maybe_tune_concurrency_for_subscription` applies
    from `watchdog auth`'s later mode switch (#493)."""
    mode = state.get("mode")
    print(f"\n  {_GREEN}✓{_RESET}  Ingestion set to Claude, via your {_BOLD}{mode}{_RESET} login.")
    if mode == "subscription":
        print(f"  {_YELLOW}Note:{_RESET} {_DIM}ingesting more than a few documents this way can burn through "
              f"session limits fast. See{_RESET} {_CYAN}docs/configuration.md{_RESET} {_DIM}(\"Model backends\") "
              f"for cheaper alternatives — OpenAI's GPT-5.6 Luna benchmarked best on real filings (see{_RESET} "
              f"{_CYAN}docs/benchmarks.md{_RESET}{_DIM}) — or switch anytime with{_RESET} "
              f"{_CYAN}watchdog settings extractor_model{_RESET}{_DIM}.{_RESET}")
        _maybe_tune_concurrency_for_subscription()
    else:
        # Claude ingestion under api-key mode is metered, not subscription-bound, so any stale
        # auto-tune from a prior subscription run needs undoing here too — not just when
        # ingestion routes away from Claude entirely (_route_ingestion_to_provider's own call).
        _maybe_restore_concurrency_from_subscription()


def route_stages_to_model(config: dict, provider: str, value: str) -> None:
    """Point classifier/extractor/finalizer_model at `value` (a `provider:model` id) and apply
    each stage's generic schema-default effort wherever the model supports it. Mutates `config`;
    the caller persists it. Shared by the interactive setup and the desktop app's first-run
    setup, so both route a provider's models identically."""
    from watchdog.cmd.setup import _CONFIGURE_KEYS
    from watchdog.model_client import effort_supported

    config["classifier_model"] = value
    config["extractor_model"] = value
    config["finalizer_model"] = value

    model_id = value.removeprefix(f"{provider}:")
    for key in ("classifier_effort", "extractor_effort", "finalizer_effort"):
        default_effort = _CONFIGURE_KEYS[key]["default"]
        if effort_supported(provider, model_id, default_effort):
            config[key] = default_effort


def _route_ingestion_to_provider(state: dict, provider: str) -> None:
    """A non-Claude provider picked for ingestion in `_choose_ingestion_provider`: store its key
    (or base URL, for local/self-hosted and OpenRouter — #380), then route
    classifier/extractor/finalizer_model to one model from it. Asks for the model once rather
    than once per stage, since the common case is the same model everywhere; `watchdog configure
    <key>` remains the way to route an individual stage to something else afterward.

    Also applies each stage's generic schema-default effort level (classifier low, extractor
    medium, finalizer high — the task's own shape, not a per-model tuned recommendation; see
    D225) wherever the chosen model actually supports it. A model-specific tuned effort (e.g.
    Luna's benchmarked `high` for extraction) is a `watchdog configure extractor_effort` step
    away, same as before."""
    meta = _PROVIDERS[provider]
    label = meta["label"].split(" — ")[0]

    print()
    if meta.get("base_url_key") and not get_base_url(provider):
        if not prompt_and_store_base_url(provider):
            return   # local/openrouter can't run without one — nothing to fall back to

    if not provider_requires_key(provider):
        pass   # e.g. local — most self-hosted runners need no key at all
    elif os.environ.get(meta["env"]) or has_stored_key(state, provider):
        print(f"  {_GREEN}✓{_RESET}  {label} key already available.")
    elif not prompt_and_store_key(provider, state):
        return

    from watchdog.cmd.base import CONFIG_FILE, WATCHDOG_HOME
    from watchdog.cmd.setup import _pick_model_interactive
    config: dict = {}
    if CONFIG_FILE.exists():
        config = _read_json_or(CONFIG_FILE, {}, catch=(json.JSONDecodeError,))

    print(f"\n  {_BOLD}Pick a model for ingestion{_RESET} "
          f"{_DIM}(used for classifying, extracting, and finalizing — route an individual stage"
          f" elsewhere anytime with watchdog settings){_RESET}")
    value = _pick_model_interactive(config.get("extractor_model"), only_provider=provider)
    if value:
        route_stages_to_model(config, provider, value)

    WATCHDOG_HOME.mkdir(parents=True, exist_ok=True)
    write_private_json(CONFIG_FILE, config)
    print(f"\n  {_GREEN}✓{_RESET}  Ingestion routed to {_BOLD}{provider}{_RESET}.")
    _maybe_restore_concurrency_from_subscription()


def _offer_extra_providers(state: dict) -> None:
    """Step 3 of `watchdog setup`'s auth flow: optionally add a key for a provider you're not
    routing ingestion to right now, just to have on hand for a later `watchdog configure` stage
    override. A repeatable pick-one-then-loop, not a march through every provider in turn asking
    yes/no one at a time — the old shape meant a user who wanted just one provider still had to
    sit through prompts for every other one, including a base-URL prompt for local/OpenRouter
    they had no interest in (#690 design pass).

    Runs after `_choose_ingestion_provider` regardless of what that step picked — including
    Claude — since this is about keys to have on hand, not about ingestion routing."""
    extras = [p for p in _PROVIDERS if p != "anthropic"]
    if not any(not provider_ready(p) for p in extras):
        return

    print(f"\n  {_BOLD}Other providers?{_RESET} {_DIM}(optional — add a key to have on hand for later){_RESET}")
    while True:
        remaining = [p for p in extras if not provider_ready(p)]
        if not remaining:
            return
        items = [_PROVIDERS[p]["label"].split(" — ")[0] for p in remaining] + ["Done"]
        result = pick(items, len(remaining), title="Add a provider key?")
        if result is CANCELLED or result == len(remaining):
            return

        provider = remaining[result]
        meta = _PROVIDERS[provider]
        print(f"\n  {_BOLD}{meta['label'].split(' — ')[0]}{_RESET}")
        if meta.get("base_url_key") and not get_base_url(provider):
            prompt_and_store_base_url(provider)
        if provider_requires_key(provider) and not (os.environ.get(meta["env"]) or has_stored_key(state, provider)):
            prompt_and_store_key(provider, state)


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
    if choice == "1" and _ingest_stage_provider(_load_config().get("extractor_model")) == "anthropic":
        # Switching to subscription here (#493) needs the same auto-tune `watchdog setup`
        # applies (#400) — extraction stays on subscription far more often via this later
        # `watchdog auth` switch than via the initial setup wizard.
        _maybe_tune_concurrency_for_subscription()
    elif choice == "2":
        # Mirror image (#493 follow-up): switching back off subscription should undo a still-
        # active auto-tune, or it silently caps a metered key at 3 forever.
        _maybe_restore_concurrency_from_subscription()
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
    label or remove its API keys — skipped for a provider that doesn't require one (`local`)
    unless the user wants to add one anyway (some self-hosted gateways do check for one).

    Several labelled keys per provider (#690) get the minimum here — add one with a name, pick
    the default, delete one; naming and choosing them per investigation is the app's job."""
    meta = _PROVIDERS[provider]
    items, default = _key_items(state, provider)

    print()
    print(f"  {_BOLD}{meta['label']}{_RESET}")

    if meta.get("base_url_key"):
        _pick_base_url_provider_interactive(provider)
        print()

    if os.environ.get(meta["env"]):
        print(f"  {_YELLOW}Note:{_RESET} ${meta['env']} is set in your environment and "
              f"takes precedence over a stored key.")

    if not provider_requires_key(provider) and not items:
        if not confirm(f"  {meta['label']} doesn't require a key — add one anyway?", default=False):
            print()
            return

    if items:
        for i in items:
            tag = f" {_DIM}(default){_RESET}" if i["id"] == default and len(items) > 1 else ""
            print(f"  {i['label']}: {_CYAN}{i['masked']}{_RESET}{tag}")
        options = ["Replace the default key", "Delete a key", "Add another key, with a name"]
        if len(items) > 1:
            options.append("Choose the default key")
        options.append("Cancel")
        result = pick(options, 0, title="What would you like to do?")
        if result is CANCELLED or options[result] == "Cancel":
            return
        choice = options[result]
        if choice == "Add another key, with a name":
            _add_labelled_key_interactive(provider)
            return
        if choice in ("Delete a key", "Choose the default key"):
            which = items[0] if len(items) == 1 else None
            if which is None:
                picked = pick([i["label"] for i in items], 0, title="Which key?")
                if picked is CANCELLED:
                    return
                which = items[picked]
            if choice == "Delete a key":
                delete_key(provider, which["id"])
                print(f"  {_GREEN}Removed:{_RESET} the {_BOLD}{which['label']}{_RESET} key for {provider}\n")
            else:
                set_default_key(provider, which["id"])
                print(f"  {_GREEN}✓{_RESET}  {_BOLD}{which['label']}{_RESET} is now the default {provider} key.\n")
            return

    prompt_and_store_key(provider, state)
    print()


def _add_labelled_key_interactive(provider: str) -> None:
    meta = _PROVIDERS[provider]
    try:
        label = input("  Name for this key (e.g. Personal, Work): ").strip()
        key = getpass(f"  Paste the {meta['label']} API key (hidden): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    try:
        add_key(provider, label, key)
    except ValueError as e:
        print(f"  {_YELLOW}!{_RESET}  {e}\n")
        return
    print(f"  {_GREEN}✓{_RESET}  {meta['label']} key {_BOLD}{label}{_RESET} stored ({_mask(key)}).\n")


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
