"""How model backends authenticate, and the API keys Watchdog stores.

Two modes for Claude (#118):
  - **subscription**: rely on Claude Code's own login (`~/.claude/.credentials.json`, or the macOS
    Keychain). No metered API billing.
  - **api-key**: use a metered Anthropic API key.

State lives in `~/.watchdog/credentials.json` (mode 0600): `{"mode", "keys": {provider: {"default":
<id>, "items": [{"id", "label", "key"}, ...]}}}`. A provider can hold several labelled keys, one
of them the default (#690, D290). Each key is an encrypted blob only the app's main process can
open (`watchdog.keystore`, D295), or plain text on a computer with no usable secure storage; this
process sees only the keys the app gave it. An investigation can choose which labelled key it bills
(`.watchdog/settings.json` in its folder); `resolve_key` is the one place a key is chosen. The
provider's environment variable, when set, takes precedence over every stored key.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from watchdog import config, defaults, keystore
from watchdog.pipeline.json_io import _read_json, _read_json_or, write_private_json

# Providers whose keys watchdog manages. `anthropic` covers both Claude backends (one
# ANTHROPIC_API_KEY); each OpenAI-compatible provider has its own key, independent of the Claude
# auth mode. `local` and `openrouter` also take a user-supplied base URL (`base_url_key`/
# `base_url_env`, `default_base_url` where there is one), and `requires_key: False` lets `local`
# run without a key, since most local runners don't check for one (D139).
PROVIDERS: dict[str, dict] = {
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


def credentials_path() -> Path:
    return config.home() / "credentials.json"


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


def load_state() -> dict:
    """Return {"mode", "keys"}. `mode` is None until set by setup or `auth use`."""
    path = credentials_path()
    if not path.exists():
        return {"mode": None, "keys": {}}
    try:
        data = _read_json(path)
    except json.JSONDecodeError:
        sys.exit("Error: credentials file is corrupt; remove it and re-add your keys.")
    data.setdefault("mode", None)
    data.setdefault("keys", {})
    return data


def save_state(state: dict) -> None:
    config.home().mkdir(parents=True, exist_ok=True)
    path = credentials_path()
    write_private_json(path, state)


def mask(key: str) -> str:
    if len(key) <= 8:
        return "…" + key[-2:] if len(key) > 2 else "(set)"
    return f"{key[:10]}…{key[-4:]}"


# ── labelled keys (#690, D290) ──────────────────────────────────────────────────
# A provider can hold several keys, each with a stable id and a short label ("Personal",
# "Globe"), one of them the default. In memory an item is {"id", "label", "key", "stored",
# "masked"}: `stored` is what the file holds and is written back unchanged, `key` the usable key,
# or None when an encrypted key wasn't given to this process ("locked"), and `masked` what a
# display shows.

DEFAULT_LABEL = "Default"


DEFAULT_ID = "default"


LABEL_MAX = 40


class KeyChoiceError(Exception):
    """An investigation chose a labelled key this computer doesn't have (deleted, or never added
    here because the folder came from someone else). Raised before any model call: falling back
    to another key would bill another account (D290)."""


class KeyLockedError(KeyChoiceError):
    """The key that would pay is stored encrypted by the app and this process wasn't given it: a
    process the app couldn't decrypt the key for (D295). A
    `KeyChoiceError`, so every path that stops before a model call for a missing key stops here
    too, and none falls back to another key."""


def _secret(stored) -> tuple[str | None, str | None] | None:
    """A stored secret as `(key, masked)`, `key` None when it is an encrypted blob this process
    wasn't given. None when `stored` isn't a secret at all."""
    if isinstance(stored, str):
        return (stored, mask(stored)) if stored else None
    if keystore.is_blob(stored):
        key = keystore.reveal(stored)
        masked = stored.get("masked") if isinstance(stored.get("masked"), str) else None
        return key, masked or (mask(key) if key else "(encrypted)")
    return None


def _item(kid: str, label: str, stored) -> dict | None:
    secret = _secret(stored)
    if secret is None:
        return None
    return {"id": kid, "label": label, "key": secret[0], "stored": stored, "masked": secret[1]}


def key_items(state: dict, provider: str) -> tuple[list[dict], str | None]:
    """A provider's stored keys as `[{"id", "label", "key", "stored", "masked"}]` and the
    default's id. `key` is None for an encrypted key this process wasn't given (D295)."""
    raw = (state.get("keys") or {}).get(provider)
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
    else:
        ids = [i["id"] for i in items]
        keys[provider] = {"default": default if default in ids else ids[0],
                          "items": [{"id": i["id"], "label": i["label"], "key": i["stored"]} for i in items]}


def _seal(state: dict, key: str, enc: dict | None) -> dict | str:
    """What the file stores for a new or replaced `key` (D295): the app's encrypted blob when it
    sent one, else the key itself (a computer with no usable secure storage), but never plain text
    when the app has said it encrypts. `remember`s the key, so this process can use it at once."""
    if enc is not None:
        if not keystore.is_blob(enc):
            raise ValueError("The key arrived in a form Watchdog doesn't recognise, so it wasn't saved.")
        blob = {"enc": enc["enc"], "data": enc["data"], "masked": mask(key)}
        keystore.remember(blob, key)
        return blob
    if keystore.encrypting():
        raise ValueError("Watchdog couldn't encrypt this key, so it wasn't saved. Restart Watchdog "
                         "and try again.")
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
        raise ValueError(f"There is already a {PROVIDERS.get(provider, {}).get('label', provider).split(' — ')[0]} "
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
    items, default = key_items(state if state is not None else load_state(), provider)
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
    state = load_state()
    items, default = key_items(state, provider)
    text = clean_label(label, provider, items)
    kid = DEFAULT_ID if not items and text == DEFAULT_LABEL else _new_key_id(items)
    stored = _seal(state, key, enc)
    items.append({"id": kid, "label": text, "key": key, "stored": stored, "masked": mask(key)})
    _put_key_items(state, provider, items, kid if (make_default or default is None) else default)
    save_state(state)
    return kid


def find_key(items: list[dict], key_id: str) -> dict:
    for i in items:
        if i["id"] == key_id:
            return i
    raise ValueError("That key is no longer stored on this computer.")


def rename_key(provider: str, key_id: str, label: str) -> None:
    state = load_state()
    items, default = key_items(state, provider)
    item = find_key(items, key_id)
    item["label"] = clean_label(label, provider, items, except_id=key_id)
    _put_key_items(state, provider, items, default)
    save_state(state)


def replace_key(provider: str, key_id: str, key: str, *, enc: dict | None = None) -> None:
    """Replace the secret of an existing labelled key, keeping its id and label (so every
    investigation that chose it keeps billing the account it names)."""
    key = (key or "").strip()
    if not key:
        raise ValueError("Paste a key.")
    state = load_state()
    items, default = key_items(state, provider)
    find_key(items, key_id).update(key=key, stored=_seal(state, key, enc), masked=mask(key))
    _put_key_items(state, provider, items, default)
    save_state(state)


def set_default_key(provider: str, key_id: str) -> None:
    state = load_state()
    items, _default = key_items(state, provider)
    find_key(items, key_id)
    _put_key_items(state, provider, items, key_id)
    save_state(state)


def delete_key(provider: str, key_id: str) -> None:
    """Remove one labelled key. When it was the default, the first remaining key becomes the
    default; an investigation that chose the deleted key stops at its next run (D290)."""
    state = load_state()
    items, default = key_items(state, provider)
    find_key(items, key_id)
    items = [i for i in items if i["id"] != key_id]
    _put_key_items(state, provider, items, default if default != key_id else None)
    save_state(state)


def store_default_key(state: dict, provider: str, key: str, *, enc: dict | None = None) -> None:
    """Set the provider's default key to `key` — the one-key-per-provider path the setup wizard,
    the app's single "Replace" uses. A provider with no key gets one labelled
    "Default"; otherwise the default key's secret is replaced and its label kept. Mutates
    `state`; the caller saves it. `enc` is the app's encrypted form of `key` (D295); raises
    ValueError when the key may not be written as plain text."""
    items, default = key_items(state, provider)
    secret = {"key": key, "stored": _seal(state, key, enc), "masked": mask(key)}
    if default is None:
        items, default = [{"id": DEFAULT_ID, "label": DEFAULT_LABEL, **secret}], DEFAULT_ID
    else:
        find_key(items, default).update(secret)
    _put_key_items(state, provider, items, default)


def _env_label(provider: str) -> str:
    return f"{PROVIDERS[provider]['env']} (environment)"


# ── the investigation's choice ─────────────────────────────────────────────────
# `.watchdog/settings.json` inside the investigation's folder, so the choice travels with it:
# {"schema_version": 1, "keys": {"<provider>": {"id": "<key id>", "label": "<label>"}}}. The
# key itself is never written there. The label is kept so a computer that doesn't have the id
# (a shared folder) can still name the account it should bill, and match a key of that label.

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
            if provider in PROVIDERS and isinstance(choice, dict) and (choice.get("id") or choice.get("label")):
                out[provider] = {"id": choice.get("id"), "label": choice.get("label")}
    return out


def choose_key(vault: Path, provider: str, key_id: str | None) -> None:
    """Record which of this computer's keys the investigation uses for `provider`; None returns
    it to the default. Only the id and label are written, never the key."""
    if provider not in PROVIDERS:
        raise ValueError(f"“{provider}” isn't a model provider Watchdog knows.")
    data = _read_investigation_settings(vault)
    keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
    if key_id is None:
        keys.pop(provider, None)
    else:
        items, _default = key_items(load_state(), provider)
        item = find_key(items, key_id)
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
    name = PROVIDERS.get(provider, {}).get("label", provider).split(" — ")[0]
    who = f"the {name} key named “{label}”" if label else f"a {name} key"
    return (f"This investigation is set to bill {who}, which isn't stored on this computer. "
            f"Add a key with that name under Settings → Models & keys, or choose another key "
            f"under Billing on this investigation's Overview. Nothing has been sent.")


def locked_key_message(provider: str, label: str | None) -> str:
    name = PROVIDERS.get(provider, {}).get("label", provider).split(" — ")[0]
    which = f"the {name} key named “{label}”" if label and label != DEFAULT_LABEL else f"your {name} key"
    return (f"Watchdog couldn't read {which} from this computer's secure storage. Restart "
            f"Watchdog; if this keeps happening, add the key again under Settings → Models & "
            f"keys. Nothing has been sent.")


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
    meta = PROVIDERS.get(provider)
    if meta and os.environ.get(meta["env"]):
        return {"key": os.environ[meta["env"]], "id": None, "label": _env_label(provider), "source": "env"}
    items, default = key_items(load_state(), provider)
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


def secrets_for_run(vault: Path | None, providers=None) -> dict[str, str]:
    """The encrypted keys a job in `vault` may use, as `{blob data: key}` for
    `keystore.read_stdin` (D295): per provider, only the key the investigation resolves to (its
    choice, else the default), and nothing where the environment variable overrides it or the key
    is stored in plain text (the job reads that from the file, as before). With `providers`, only
    those providers' keys: the ones the job's models use (`gui/jobs.run_providers_for`). A job
    outside an investigation gets nothing."""
    if vault is None:
        return {}
    state = load_state()
    out: dict[str, str] = {}
    for p, meta in PROVIDERS.items():
        if providers is not None and p not in providers:
            continue
        if os.environ.get(meta["env"]):
            continue
        items, default = key_items(state, p)
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
    meta = PROVIDERS.get(provider)
    if meta and os.environ.get(meta["env"]):
        return os.environ[meta["env"]]
    items, default = key_items(state if state is not None else load_state(), provider)
    return next((i["key"] for i in items if i["id"] == default), None)


def default_masked(provider: str, state: dict | None = None) -> str | None:
    """`default_key`, masked — also for a default key that is stored encrypted and locked here."""
    meta = PROVIDERS.get(provider)
    if meta and os.environ.get(meta["env"]):
        return mask(os.environ[meta["env"]])
    items, default = key_items(state if state is not None else load_state(), provider)
    return next((i["masked"] for i in items if i["id"] == default), None)


def label_for_key(provider: str, key: str | None) -> str | None:
    """The label of the key whose secret is `key` — what usage records name as the account that
    paid (never the key). Derived from the key actually sent, so it can't disagree with it."""
    if not key:
        return None
    meta = PROVIDERS.get(provider)
    if meta and os.environ.get(meta["env"]) == key:
        return _env_label(provider)
    for i in key_items(load_state(), provider)[0]:
        if i["key"] == key:
            return i["label"]
    return None


def check_investigation_keys(vault: Path, providers) -> None:
    """Raise `KeyChoiceError` if any of `providers` has a chosen key this computer lacks — the
    check a run makes before its first model call. An environment variable satisfies it."""
    for p in dict.fromkeys(providers):
        if p in PROVIDERS:
            resolve_key(p, vault)


def run_providers(backends) -> list[str]:
    """The providers whose keys a run on `backends` would send (None is a bare Claude tier). A
    Claude stage counts only in api-key mode: on a subscription no key is sent."""
    from watchdog.model_client import provider_for_backend
    mode = load_state().get("mode")
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
    from watchdog.projects import load as load_projects
    try:
        projects = load_projects()
    except Exception:
        return []
    items, _default = key_items(load_state(), provider)
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
    state = load_state()
    out = []
    for p in dict.fromkeys(providers):
        if p not in PROVIDERS:
            continue
        row = {"provider": p, "provider_label": PROVIDERS[p]["label"].split(" — ")[0],
               "label": None, "source": "none", "missing": False, "message": None,
               "several": len(key_items(state, p)[0]) > 1}
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
    return PROVIDERS.get(provider, {}).get("requires_key", True)


def get_base_url(provider: str) -> str | None:
    """Resolve a provider's OpenAI-compatible base URL (#380): its env var override first, then
    the setting named in `base_url_key`, then the provider's fixed default
    (None for a provider like `local` that has none — the caller must supply one)."""
    meta = PROVIDERS.get(provider, {})
    env_name = meta.get("base_url_env")
    if env_name and os.environ.get(env_name):
        return os.environ[env_name].rstrip("/")
    base_key = meta.get("base_url_key")
    if base_key:
        value = config.read().get(base_key)
        if value:
            return value.rstrip("/")
    return meta.get("default_base_url")


def provider_ready(provider: str) -> bool:
    """Whether `provider` has everything it needs to run a call: a base URL if it requires a
    user-supplied one, and an API key unless it's the rare provider that doesn't need one."""
    meta = PROVIDERS.get(provider, {})
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
    mode = load_state().get("mode")

    if mode is None:
        return {"mode": "none", "reason": "No model provider is set up. Sign in or add a key under Settings → Models & keys."}
    if mode == "subscription":
        return {"mode": "subscription"}
    # api-key — the investigation's chosen Anthropic key, or the default (D290)
    key = get_api_key(provider, vault)
    return {"mode": "api-key", "key": key} if key else {
        "mode": "none", "reason": "Claude is set to use an API key, but no key is stored. Add one under Settings → Models & keys."}


def stage_provider(value: str | None) -> str:
    """Best-effort `[backend:]model` config value → provider name, for the status display
    only — not validated the way `ops/ingest.py`'s `resolve_stage` is; it just needs to
    answer "which provider is this stage pointed at" (#325)."""
    backend, _ = defaults.split_backend_model(value or "")
    if backend is None:
        return "anthropic"          # bare Claude tier (haiku/sonnet)
    from watchdog.model_client import provider_for_backend
    return provider_for_backend(backend)


INGEST_STAGES = (("classifier_model", defaults.CLASSIFIER_MODEL),
                  ("extractor_model", defaults.EXTRACTOR_MODEL),
                  ("finalizer_model", defaults.FINALIZER_MODEL))


def status_data() -> dict:
    """What Settings → Models & keys shows, as data.

    `claude` is Claude Code's own access mode (`logged_in` only in subscription mode, `key_masked`
    and `key_source` only in api-key mode). `stages` says which provider each pipeline stage is
    routed to and whether it can run; `keys` lists every provider with a key available (masked —
    the key itself never leaves this function); `base_urls` the user-supplied endpoints."""
    state = load_state()
    mode = state.get("mode")
    meta = PROVIDERS["anthropic"]
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

    # Which provider each pipeline stage is routed to, and whether that provider is ready:
    # what decides whether processing will run, independent of Claude's mode above (#325).
    cfg = config.read()

    stages = []
    for stage_key, default in INGEST_STAGES:
        value = cfg.get(stage_key) or default
        provider = stage_provider(value)
        ready = (mode == "subscription" or bool(default_key("anthropic", state))) if provider == "anthropic" \
            else provider_ready(provider)
        stages.append({"stage": stage_key[: -len("_model")], "config_key": stage_key,
                       "value": value, "provider": provider, "ready": ready})

    # Every provider with a key available, whether or not a stage is routed to it — the
    # definitive list of what's stored, Anthropic included (#482). A stored Anthropic key is
    # only ever actually used while Claude Code mode above is api-key, so it's flagged inactive
    # rather than silently vanishing from the list while on subscription.
    shown_providers = {stage_provider(cfg.get(k) or d) for k, d in INGEST_STAGES}
    keys = []
    for p in PROVIDERS:
        masked = default_masked(p, state)
        if not masked:
            continue
        if p == "anthropic" and mode != "api-key":
            status = f"inactive — {mode} mode" if mode else "inactive — not configured"
        else:
            status = "in use" if p in shown_providers else "unused"
        env = bool(os.environ.get(PROVIDERS[p]["env"]))
        keys.append({"provider": p, "masked": masked, "status": status,
                     "source": "env" if env else "stored",
                     # Stored encrypted and not readable by this process (D295).
                     "locked": not env and default_key(p, state) is None,
                     "labelled": list_keys(p, state)})

    # Providers with a user-supplied base URL (local, openrouter — #380), whichever is set.
    base_urls = [{"provider": p, "url": u}
                 for p, u in ((p, get_base_url(p)) for p, m in PROVIDERS.items() if m.get("base_url_key"))
                 if u]
    return {"claude": claude, "stages": stages, "keys": keys, "base_urls": base_urls}


SUBSCRIPTION_CONCURRENCY = defaults.SUBSCRIPTION_CONCURRENCY


def tune_concurrency_for_subscription() -> bool:
    """Lower `extract_concurrency` when Claude subscription auth is chosen and processing stays on
    it (#400, #493): every concurrent extraction on that path shares one Claude Code session, and
    the default of 5 reliably throttles it. Only when the setting was never set, so a deliberate
    value is left alone. Returns whether it changed the setting."""
    cfg = config.read()
    if "extract_concurrency" in cfg:
        return False
    cfg["extract_concurrency"] = SUBSCRIPTION_CONCURRENCY
    config.save(cfg)
    return True


def restore_concurrency_from_subscription() -> bool:
    """Undo the tune above when switching away from subscription auth (#493): on a metered key,
    extraction no longer shares one session's rate limit, so a still-active auto-tuned value would
    cap throughput forever. Only clears a value still exactly `SUBSCRIPTION_CONCURRENCY`.
    Returns whether it changed the setting."""
    cfg = config.read()
    if cfg.get("extract_concurrency") != SUBSCRIPTION_CONCURRENCY:
        return False
    del cfg["extract_concurrency"]
    config.save(cfg)
    return True


def route_stages_to_model(config: dict, provider: str, value: str) -> None:
    """Point classifier/extractor/finalizer_model at `value` (a `provider:model` id) and apply
    each stage's default effort (post-processing: 'high') wherever the model supports it. Mutates `config`;
    the caller persists it."""
    from watchdog.settings import SETTINGS
    from watchdog.model_client import effort_supported

    config["classifier_model"] = value
    config["extractor_model"] = value
    config["finalizer_model"] = value

    model_id = value.removeprefix(f"{provider}:")
    # Post-processing has no schema default (nothing is sent unless it is set); a model chosen
    # here for every stage gets 'high' for it, since reasoning helps the prose steps.
    stage_effort = {"classifier_effort": SETTINGS["classifier_effort"]["default"],
                    "extractor_effort": SETTINGS["extractor_effort"]["default"],
                    "finalizer_effort": "high"}
    for key, default_effort in stage_effort.items():
        if effort_supported(provider, model_id, default_effort):
            config[key] = default_effort

