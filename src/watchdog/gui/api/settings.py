"""`settings.*`, `auth.*`, `skills.*`, `setup.check` — configuration, credentials and first-run
checks.

`settings.set` validates through `watchdog.settings.coerce` and writes through `config.save` (the
file is owner-only), and `auth.*` use `watchdog.auth`'s state and its concurrency auto-tune. Secrets never travel back to the app: a stored key is returned masked only.

Under the app, the methods that take a key (`auth.setAnthropicMode`, `auth.setKey`, `auth.addKey`)
also receive `key_enc`, the key encrypted by the app's main process; only that blob is written to
disk (D295). The main process adds it; the renderer never sees or sends it.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

from watchdog.gui.rpc import RpcError, method

_EFFORTS = ["low", "medium", "high", "xhigh", "max"]
_MODEL_KEYS = {"classifier_model", "extractor_model", "finalizer_model",
               "finalizer_writing_model", "finalizer_housekeeping_model"}
_KINDS = {"bool": "bool", "int": "int", "float": "float", "path": "path"}


# ── settings ─────────────────────────────────────────────────────────────────────

def _kind(key: str, meta: dict) -> str:
    if meta.get("secret"):
        return "secret"
    t = meta.get("type")
    if t in _KINDS:
        return _KINDS[t]
    if t == "enum":
        return "effort" if key.endswith("_effort") else "choice"
    if key in _MODEL_KEYS:
        return "model"
    return "text"   # int_or_auto ("auto" or a number), lang_list, free strings


def _help(meta: dict) -> str:
    return "\n".join(line.strip() for line in (meta.get("help") or "").splitlines()).strip()


def _read_config() -> dict:
    from watchdog import config
    from watchdog.pipeline.json_io import _read_json
    path = config.path()
    if not path.exists():
        return {}
    try:
        data = _read_json(path)
    except (OSError, ValueError):
        raise RpcError(f"The settings file is damaged. Fix or remove {path}.",
                       code="config_corrupt")
    return config._upgrade(data) if isinstance(data, dict) else {}


def _display(key: str, value, config: dict) -> str:
    from watchdog.settings import display_value
    try:
        return display_value(key, value, config)
    except Exception:  # noqa: BLE001 — a display hint must never break the settings screen
        return str(value) if value is not None else "(not set)"


def _secret_settings() -> tuple[str, ...]:
    from watchdog.auth import SETTING_SECRETS
    return SETTING_SECRETS


def _setting(key: str, meta: dict, config: dict) -> dict:
    from watchdog.settings import default_value
    kind = _kind(key, meta)
    value = config.get(key)
    if key in _secret_settings():
        # Kept in credentials.json, encrypted (D341): only whether one is stored is shown.
        from watchdog import auth as auth_cmd
        value = "set" if auth_cmd.has_setting_secret(key) else None
    choices = meta.get("choices") if kind in ("choice", "effort") else None
    return {
        "key": key,
        "short": meta.get("short") or "",
        "help": help_text(_help(meta)),
        "default": default_value(key),
        "current": None if kind == "secret" else value,
        "display": _display(key, value, config),
        "kind": kind,
        "choices": list(choices) if choices else None,
        "is_set": value is not None and value != "",
    }


def help_text(text: str | None) -> str | None:
    """A setting's help as prose: the text is hard-wrapped, so wrapped lines are rejoined into
    paragraphs, keeping column-aligned lines (lists of models) as they are."""
    if not text:
        return text
    out: list[str] = []
    prose: list[str] = []

    def flush():
        if prose:
            out.append(" ".join(prose))
            prose.clear()

    for line in text.split("\n"):
        if not line.strip():
            flush()
            out.append("")
        elif re.search(r"\S {2,}\S", line):
            flush()
            out.append(line)
        else:
            prose.append(line.strip())
    flush()
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


@method("settings.schema")
def schema() -> dict:
    from watchdog.settings import MODEL_STEP_KEYS, SECTIONS as _CONFIGURE_SECTIONS, SETTINGS as _CONFIGURE_KEYS

    config = _read_config()
    shown: set[str] = set()
    sections = []
    for title, blurb, keys in _CONFIGURE_SECTIONS:
        rows = [_setting(k, _CONFIGURE_KEYS[k], config) for k in keys if k in _CONFIGURE_KEYS]
        shown.update(k for k in keys if k in _CONFIGURE_KEYS)
        sections.append({"title": title, "blurb": blurb, "keys": rows})
    # Listed for any reader of the schema, but shown on Models & keys rather than as a section.
    sections.append({"title": "Step models", "blurb": "", "hidden": True,
                     "keys": [_setting(k, _CONFIGURE_KEYS[k], config) for k in MODEL_STEP_KEYS]})
    shown.update(MODEL_STEP_KEYS)
    leftovers = [k for k in _CONFIGURE_KEYS if k not in shown]
    if leftovers:
        sections.append({"title": "Other", "blurb": "",
                         "keys": [_setting(k, _CONFIGURE_KEYS[k], config) for k in leftovers]})
    return {"sections": sections}


@method("settings.set")
def set_(key: str, value: str | None, value_enc: dict | None = None) -> dict:
    """Validate and store one setting. An empty value clears a free-text or model setting back to
    its default. A secret setting (the Wayback Machine keys, D341) goes to credentials.json,
    encrypted: `value_enc` is its sealed form, added by the app's main process."""
    from watchdog import config as config_file
    from watchdog import settings

    if key not in settings.SETTINGS:
        raise RpcError(f"“{key}” isn't a setting.", code="unknown_key")
    meta = settings.SETTINGS[key]
    config = _read_config()
    text = "" if value is None else (value if isinstance(value, str) else str(value))
    from watchdog import auth as auth_cmd
    if key in auth_cmd.SETTING_SECRETS:
        try:
            auth_cmd.store_setting_secret(key, text, enc=value_enc)
        except ValueError as e:
            raise RpcError(str(e), code="bad_value")
        set_now = auth_cmd.has_setting_secret(key)
        return {"key": key, "value": None, "display": _display(key, "set" if set_now else None, config)}

    if text.strip() == "" and meta.get("type") in ("string", "str"):
        config.pop(key, None)
        config_file.save(config)
        return {"key": key, "value": None, "display": _display(key, None, config)}
    if text.strip() == "" and meta.get("type") == "path":
        raise RpcError(f"“{key}” needs a folder.", code="bad_value")

    try:
        shown = settings.coerce(config, key, text)
    except settings.SettingError as e:
        raise RpcError(str(e), code="bad_value")
    config_file.save(config)
    secret = bool(meta.get("secret"))
    return {"key": key, "value": None if secret else config.get(key),
            "display": _display(key, config.get(key), config) if secret else shown}


@method("settings.models")
def models() -> dict:
    """Every catalogued model, as a picker needs it. `value` is what a model setting stores: the
    bare tier for a Claude model, `provider:id` for the rest."""
    from watchdog.model_catalog import (
        _CATALOG, _tier_aliases, catalog_context_window, catalog_effort_levels,
    )

    rows = []
    for m in _CATALOG["models"]:
        aliases = _tier_aliases(m)
        provider = m["provider"]
        efforts = catalog_effort_levels(m["id"]) or set()
        rows.append({
            "value": aliases[0] if aliases else f"{provider}:{m['id']}",
            "label": m.get("name") or m["id"],
            "id": m["id"],
            "provider": provider,
            # A bare Claude tier is routed by auth mode, so it names no backend of its own.
            "backend": None if provider == "anthropic" else provider,
            "input_per_mtok": float(m["input"]) * 1_000_000 if "input" in m else None,
            "output_per_mtok": float(m["output"]) * 1_000_000 if "output" in m else None,
            "context_window": catalog_context_window(m["id"]),
            "efforts": [e for e in _EFFORTS if e in efforts],
            "notes": m.get("notes") or None,
        })
    return {"models": rows, "efforts": list(_EFFORTS)}


# ── auth ─────────────────────────────────────────────────────────────────────────

def _claude_reason(claude: dict) -> str | None:
    mode = claude["mode"]
    if mode is None:
        return "Claude access isn't set up yet."
    if mode == "subscription":
        if not claude["logged_in"]:
            return "Claude is not signed in. Use Sign in under Settings → Models & keys."
        if claude["env_key_set"]:
            return ("ANTHROPIC_API_KEY is set in your environment, and the Agent SDK uses it before "
                    "the subscription login, so runs would be metered.")
        return None
    return None if claude["key_masked"] else "API-key mode is set but no key is stored."


def _session_reason(claude: dict) -> str | None:
    """What stops Ask Claude and Research when their billing differs from processing's (D318);
    when they follow it, `_claude_reason` already says."""
    if not claude["session_mode_own"] or claude["session_mode"] == claude["mode"]:
        return None
    if claude["session_mode"] == "subscription":
        if not claude["logged_in"]:
            return "Ask Claude is set to use your subscription, but Claude is not signed in."
        if claude["env_key_set"]:
            return ("ANTHROPIC_API_KEY is set in your environment, and Ask Claude uses it before "
                    "the subscription login, so questions would be metered.")
        return None
    return None if claude["key_masked"] else "Ask Claude is set to use an API key, but no key is stored."


def _auth_status() -> dict:
    from watchdog import auth as auth_cmd
    from watchdog import key_spend

    d = auth_cmd.status_data()
    state = auth_cmd.load_state()
    claude = d["claude"]
    mode = claude["mode"]
    if mode == "subscription":
        logged_in = bool(claude["logged_in"])
    else:
        logged_in = bool(claude["key_masked"])
    providers = []
    for p, meta in auth_cmd.PROVIDERS.items():
        providers.append({
            "provider": p, "label": meta["label"], "env": meta["env"],
            "requires_key": auth_cmd.provider_requires_key(p),
            "base_url_setting": meta.get("base_url_key"),
            "base_url": auth_cmd.get_base_url(p) if meta.get("base_url_key") else None,
            "ready": auth_cmd.provider_ready(p) if p != "anthropic" else bool(
                mode == "subscription" or auth_cmd.default_key("anthropic", state)),
        })
    return {
        "claude": {"mode": mode, "logged_in": logged_in, "reason": _claude_reason(claude),
                   # How Ask Claude, Research and setting the context are billed (D318), whether
                   # that is their own choice, whether Claude is signed in, and what stops them.
                   "session_mode": claude["session_mode"], "session_mode_own": claude["session_mode_own"],
                   "signed_in": bool(claude["logged_in"]), "session_reason": _session_reason(claude),
                   "env_key_set": claude["env_key_set"], "key_masked": claude["key_masked"],
                   "key_source": claude["key_source"],
                   # The default Anthropic key's name, when there is more than one to tell apart.
                   "key_label": next((k["label"] for k in auth_cmd.list_keys("anthropic", state) if k["default"]), None)
                   if len(auth_cmd.list_keys("anthropic", state)) > 1 and claude["key_source"] == "stored" else None},
        "stages": [{**s, "billing": mode if s["provider"] == "anthropic" else None}
                   for s in d["stages"]],
        "keys": [{"provider": k["provider"], "masked": k["masked"],
                  "in_use": "inactive" if k["status"].startswith("inactive") else k["status"],
                  "detail": k["status"], "source": k["source"]} for k in d["keys"]],
        "base_urls": d["base_urls"],
        "providers": providers,
        # Every stored key per provider, labelled and masked (#690); `users` names the registered
        # investigations that chose it, for the warning before a delete.
        "key_sets": {p: [{**k, "users": auth_cmd.key_users(p, k["id"]),
                          # The key's own spending limit and what it has paid for (D347).
                          "limit": key_spend.status(key_spend.ref(p, k["id"]))}
                         for k in auth_cmd.list_keys(p, state)]
                     for p in auth_cmd.PROVIDERS},
        "storage": _storage(state),
    }


def _storage(state: dict) -> dict:
    """Where this computer's keys are kept, for Settings → Models & keys (D295). `store` is
    "encrypted" (the app encrypts each key with the operating system's secure storage),
    "plaintext" (no usable secure storage: a 0600 file) or "unknown" (no app said: a backend run
    outside the app). `locked` counts stored keys this process can't read."""
    from watchdog import keystore
    from watchdog import auth as auth_cmd

    st = keystore.status()
    items = [i for p in auth_cmd.PROVIDERS for i in auth_cmd.key_items(state, p)[0]]
    return {"store": st["store"] or "unknown", "backend": st["backend"], "reason": st["reason"],
            "encrypted": sum(1 for i in items if not isinstance(i["stored"], str)),
            "plaintext": sum(1 for i in items if isinstance(i["stored"], str)),
            "locked": sum(1 for i in items if i["key"] is None)}


@method("auth.status")
def auth_status() -> dict:
    return _auth_status()


def _check_provider(provider: str) -> dict:
    from watchdog.auth import PROVIDERS
    if provider not in PROVIDERS:
        raise RpcError(f"“{provider}” isn't a model provider Watchdog knows.", code="bad_params")
    return PROVIDERS[provider]


def _prefix_warning(meta: dict, key: str) -> str | None:
    prefix = meta.get("prefix")
    if prefix and not key.startswith(prefix):
        return f"The key doesn't start with “{prefix}” — stored anyway."
    return None


@method("auth.setAnthropicMode")
def set_anthropic_mode(mode: str, key: str | None = None, key_enc: dict | None = None) -> dict:
    """Switch Claude between your subscription login and a metered API key, with the concurrency
    tune a subscription needs and an API key undoes."""
    from watchdog import auth as auth_cmd
    from watchdog import config as config_file

    if mode not in ("subscription", "api-key"):
        raise RpcError("mode must be “subscription” or “api-key”.", code="bad_params")
    meta = auth_cmd.PROVIDERS["anthropic"]
    state = auth_cmd.load_state()
    warning = None
    state["mode"] = mode
    if mode == "api-key" and key is not None and key.strip():
        _key_op(lambda: auth_cmd.store_default_key(state, "anthropic", key.strip(), enc=key_enc))
        warning = _prefix_warning(meta, key.strip())
    auth_cmd.save_state(state)
    if mode == "subscription":
        if auth_cmd.stage_provider(config_file.read().get("extractor_model")) == "anthropic":
            auth_cmd.tune_concurrency_for_subscription()
    else:
        auth_cmd.restore_concurrency_from_subscription()
    return {**_auth_status(), "warning": warning}


@method("auth.setSessionMode")
def set_session_mode(mode: str | None = None) -> dict:
    """Bill Ask Claude, Research and setting the context through the subscription or an API key,
    independently of processing (D318); None makes them follow processing again."""
    from watchdog import auth as auth_cmd

    if mode is not None and mode not in auth_cmd.SESSION_MODES:
        raise RpcError("mode must be “subscription”, “api-key” or empty.", code="bad_params")
    state = auth_cmd.load_state()
    if mode is None:
        state.pop("session_mode", None)
    else:
        state["session_mode"] = mode
    auth_cmd.save_state(state)
    return _auth_status()


@method("auth.setKey")
def set_key(provider: str, key: str, id: str | None = None, key_enc: dict | None = None) -> dict:
    """Store a provider's key. Without `id`, the default key's secret is replaced (a provider with
    none gets one named Default); with `id`, that labelled key's secret is."""
    from watchdog import auth as auth_cmd

    meta = _check_provider(provider)
    if not isinstance(key, str) or not key.strip():
        raise RpcError("Paste a key, or use remove to delete the stored one.", code="bad_params")
    if id:
        _key_op(lambda: auth_cmd.replace_key(provider, id, key, enc=key_enc))
    else:
        state = auth_cmd.load_state()
        _key_op(lambda: auth_cmd.store_default_key(state, provider, key.strip(), enc=key_enc))
        auth_cmd.save_state(state)
    adopted = auth_cmd.adopt_provider(provider)
    return {**_auth_status(), "warning": _prefix_warning(meta, key.strip()), "adopted": adopted}


def _key_op(fn):
    try:
        return fn()
    except ValueError as e:
        raise RpcError(str(e), code="bad_params") from None


@method("auth.deleteKey")
def delete_key(provider: str, id: str | None = None) -> dict:
    """Delete one labelled key (`id`), or, without `id`, every stored key for the provider."""
    from watchdog import auth as auth_cmd

    from watchdog import key_spend

    _check_provider(provider)
    if id:
        _key_op(lambda: auth_cmd.delete_key(provider, id))
    else:
        state = auth_cmd.load_state()
        if state["keys"].pop(provider, None) is not None:
            auth_cmd.save_state(state)
    try:
        key_spend.forget(provider, id)
    except ValueError:
        pass        # the key is deleted either way; a stale limit on a gone key counts nothing
    return {**_auth_status(), "warning": None}


@method("auth.addKey")
def add_key(provider: str, label: str, key: str, make_default: bool = False,
            key_enc: dict | None = None) -> dict:
    """Add another labelled key for a provider (#690). Its first key becomes the default."""
    from watchdog import auth as auth_cmd

    meta = _check_provider(provider)
    if not isinstance(key, str) or not key.strip():
        raise RpcError("Paste a key.", code="bad_params")
    kid = _key_op(lambda: auth_cmd.add_key(provider, label, key, make_default=bool(make_default), enc=key_enc))
    adopted = auth_cmd.adopt_provider(provider)
    return {**_auth_status(), "id": kid, "warning": _prefix_warning(meta, key.strip()), "adopted": adopted}


@method("auth.renameKey")
def rename_key(provider: str, id: str, label: str) -> dict:
    from watchdog import auth as auth_cmd

    _check_provider(provider)
    _key_op(lambda: auth_cmd.rename_key(provider, id, label))
    return {**_auth_status(), "warning": None}


@method("auth.setKeyLimit")
def set_key_limit(provider: str, id: str, usd=None) -> dict:
    """Set a stored key's spending limit, in US dollars (D347); blank removes it. It counts what
    the key pays for from now on, in every investigation on this computer."""
    from watchdog import auth as auth_cmd
    from watchdog import key_spend

    _check_provider(provider)
    if not any(k["id"] == id for k in auth_cmd.list_keys(provider)):
        raise RpcError("That key isn't stored on this computer.", code="not_found")
    _key_op(lambda: key_spend.set_limit(key_spend.ref(provider, id), usd))
    return {**_auth_status(), "warning": None}


@method("auth.resetKeyLimit")
def reset_key_limit(provider: str, id: str) -> dict:
    """Count a key's spending from now (D347); what it spent before stays in Usage."""
    from watchdog import key_spend

    _check_provider(provider)
    _key_op(lambda: key_spend.reset(key_spend.ref(provider, id)))
    return {**_auth_status(), "warning": None}


@method("auth.setDefaultKey")
def set_default_key(provider: str, id: str) -> dict:
    from watchdog import auth as auth_cmd

    _check_provider(provider)
    _key_op(lambda: auth_cmd.set_default_key(provider, id))
    return {**_auth_status(), "warning": None}


# ── the investigation's choice of key (D290) ─────────────────────────────────────

def _investigation_keys(v: Path) -> dict:
    from watchdog import auth as auth_cmd
    from watchdog import config as config_file

    state = auth_cmd.load_state()
    choices = auth_cmd.investigation_keys(v)
    config = config_file.load()
    used = {auth_cmd.stage_provider(auth_cmd.step_value(config, k, f, d)) for _n, k, f, d in auth_cmd.STEP_ROWS}
    used.add("anthropic")   # Ask Claude and Research always run on Claude
    rows = []
    for p, meta in auth_cmd.PROVIDERS.items():
        keys = auth_cmd.list_keys(p, state)
        summary = auth_cmd.billing_summary(v, [p])[0]
        choice = choices.get(p)
        rows.append({
            "provider": p, "provider_label": meta["label"].split(" — ")[0],
            "keys": [{"id": k["id"], "label": k["label"], "masked": k["masked"], "default": k["default"]}
                     for k in keys],
            "chosen": ({"id": choice.get("id"), "label": choice.get("label")} if choice else None),
            "resolved_label": summary["label"], "source": summary["source"],
            "missing": summary["missing"], "message": summary["message"],
            "env": bool(os.environ.get(meta["env"])), "used": p in used,
        })
    return {"providers": rows, "claude_mode": state.get("mode")}


@method("auth.investigationKeys")
def investigation_keys(vault: str) -> dict:
    """Per provider: this computer's keys (masked), the investigation's choice, and which key
    will actually pay — or why none can."""
    from watchdog.gui.vaultio import require_vault
    return _investigation_keys(require_vault(vault))


@method("auth.chooseKey")
def choose_key(vault: str, provider: str, id: str | None = None) -> dict:
    """Set which labelled key the investigation bills for `provider`; `id` null returns it to the
    default. Writes `.watchdog/settings.json` in the investigation's folder (D290)."""
    from watchdog import auth as auth_cmd
    from watchdog.gui.vaultio import require_vault

    _check_provider(provider)
    v = require_vault(vault)
    _key_op(lambda: auth_cmd.choose_key(v, provider, id or None))
    return _investigation_keys(v)


# ── the investigation's spending limit (D324) ─────────────────────────────────────

def _spending_limit(v: Path) -> dict:
    from watchdog import auth as auth_cmd
    from watchdog import spend
    from watchdog.ops import sample

    return {**spend.status(v), "claude_mode": auth_cmd.load_state().get("mode"),
            "sample": sample.is_sample_folder(v)}


@method("spend.limit")
def spending_limit(vault: str) -> dict:
    """The investigation's spending limit, the processing spend recorded since its last reset,
    what is left and whether it has been reached."""
    from watchdog.gui.vaultio import require_vault
    return _spending_limit(require_vault(vault))


@method("spend.setLimit")
def set_spending_limit(vault: str, usd: float | str | None = None) -> dict:
    """Set the limit in US dollars; null or blank removes it. Writes `.watchdog/settings.json` in
    the investigation's folder, beside the choice of key (D290)."""
    from watchdog import spend
    from watchdog.gui.vaultio import require_vault

    v = require_vault(vault)
    try:
        spend.set_limit(v, usd)
    except ValueError as e:
        raise RpcError(str(e), code="bad_value")
    return _spending_limit(v)


@method("spend.reset")
def reset_spending(vault: str) -> dict:
    """Count from now: record the reset time, so only spend recorded after it counts."""
    from watchdog import spend
    from watchdog.gui.vaultio import require_vault

    v = require_vault(vault)
    spend.reset(v)
    return _spending_limit(v)


@method("auth.setBaseUrl")
def set_base_url(provider: str, url: str) -> dict:
    """Set (or, with an empty `url`, remove) the endpoint of a provider that takes one."""
    from watchdog import config as config_file

    meta = _check_provider(provider)
    base_key = meta.get("base_url_key")
    if not base_key:
        raise RpcError(f"{provider} doesn't take a base URL.", code="bad_params")
    text = (url or "").strip() if isinstance(url, str) else ""
    config = config_file.read()
    if text:
        parts = urlsplit(text)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise RpcError("The base URL should look like https://host/v1.", code="bad_params")
        config[base_key] = text.rstrip("/")
    else:
        config.pop(base_key, None)
    config_file.save(config)
    return {**_auth_status(), "warning": None}


# ── skills ───────────────────────────────────────────────────────────────────────

@method("skills.list")
def skills_list() -> dict:
    from watchdog import skills_catalog

    user_dir = skills_catalog.user_skills_dir()
    out = []
    for name, path in skills_catalog.catalog().items():
        p = Path(path)
        try:
            description = skills_catalog._skill_descriptor(p.read_text(encoding="utf-8"))
        except OSError:
            description = ""
        try:
            is_user = p.resolve().parent == user_dir.resolve()
        except OSError:
            is_user = False
        out.append({"name": name, "description": description, "source": "user" if is_user else "package"})
    return {"skills": out, "user_dir": str(user_dir)}


@method("skills.read")
def skills_read(name: str) -> dict:
    from watchdog import skills_catalog

    canon = str(name or "").removesuffix(".md")
    if canon not in skills_catalog.catalog():
        raise RpcError(f"There's no record skill called “{canon}”.", code="not_found")
    return {"name": canon, "text": skills_catalog.read_skill(canon)}


# ── setup check ──────────────────────────────────────────────────────────────────

def _gliner_cached() -> bool:
    """Whether the GLiNER name-detection model is already downloaded (no import, no network)."""
    try:
        from huggingface_hub import constants
        cache = Path(constants.HF_HUB_CACHE)
    except Exception:  # noqa: BLE001
        home = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
        cache = Path(home) / "hub"
    return (cache / "models--urchade--gliner_multi-v2.1").is_dir()


# Programs Watchdog uses when they are installed: (command, label). None blocks the app: qpdf and
# Ghostscript are only fallbacks for damaged PDFs, Tesseract is one of several OCR engines (the
# engine ships its own), and Claude Code comes bundled with the engine. Settings explains each.
_HELPERS = [("qpdf", "qpdf"), ("gs", "ghostscript")]
if sys.platform != "darwin":
    _HELPERS.append(("tesseract", "Tesseract OCR"))
_HELPERS.append(("claude", "Claude Code"))


@method("setup.check")
def setup_check() -> dict:
    from watchdog import config

    deps = [{"label": label, "ok": shutil.which(binary) is not None, "hint": None, "required": False}
            for binary, label in _HELPERS]
    try:
        from watchdog.pipeline import capture
        playwright = bool(capture.render_available())
    except Exception:  # noqa: BLE001
        playwright = False
    return {
        "deps": deps,
        "playwright": playwright,
        "gliner_model": _gliner_cached(),
        "projects_dir": config.load().get("projects_dir") or None,
        "config_exists": config.path().exists(),
    }
