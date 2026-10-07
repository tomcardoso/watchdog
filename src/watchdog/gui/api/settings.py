"""`settings.*`, `auth.*`, `skills.*`, `setup.check` — configuration, credentials and first-run
checks.

Settings and credentials are written the way the CLI writes them: `settings.set` runs the CLI's
own `_coerce_value` and `_persist` (so validation messages, side effects and the 0600 file mode
are identical), and `auth.*` use `cmd/auth`'s `_load_state`/`_save_state` and its concurrency
auto-tune helpers. Secrets never travel back to the app: a stored key is returned masked only.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from watchdog.gui.rpc import RpcError, method
from watchdog.gui.vaultio import strip_ansi

_EFFORTS = ["low", "medium", "high", "xhigh", "max"]
_MODEL_KEYS = {"classifier_model", "extractor_model", "finalizer_model",
               "finalizer_reconciliation_model", "finalizer_synthesis_model",
               "finalizer_timeline_model", "finalizer_briefing_model"}
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
    from watchdog.cmd import setup as setup_cmd
    from watchdog.pipeline.json_io import _read_json
    path = setup_cmd.CONFIG_FILE
    if not path.exists():
        return {}
    try:
        data = _read_json(path)
    except (OSError, ValueError):
        raise RpcError("The settings file is damaged. Fix or remove it, or run setup again.",
                       code="config_corrupt")
    return data if isinstance(data, dict) else {}


def _display(key: str, value, config: dict) -> str:
    from watchdog.cmd.setup import _display_value
    try:
        return strip_ansi(_display_value(key, value, config))
    except Exception:  # noqa: BLE001 — a display hint must never break the settings screen
        return str(value) if value is not None else "(not set)"


def _setting(key: str, meta: dict, config: dict) -> dict:
    kind = _kind(key, meta)
    value = config.get(key)
    choices = meta.get("choices") if kind in ("choice", "effort") else None
    return {
        "key": key,
        "short": meta.get("short") or "",
        "help": _help(meta),
        "default": meta.get("default"),
        "current": None if kind == "secret" else value,
        "display": _display(key, value, config),
        "kind": kind,
        "choices": list(choices) if choices else None,
        "is_set": value is not None and value != "",
    }


@method("settings.schema")
def schema() -> dict:
    from watchdog.cmd.setup import _CONFIGURE_KEYS, _CONFIGURE_SECTIONS

    config = _read_config()
    shown: set[str] = set()
    sections = []
    for title, blurb, keys in _CONFIGURE_SECTIONS:
        rows = [_setting(k, _CONFIGURE_KEYS[k], config) for k in keys if k in _CONFIGURE_KEYS]
        shown.update(k for k in keys if k in _CONFIGURE_KEYS)
        sections.append({"title": title, "blurb": blurb, "keys": rows})
    leftovers = [k for k in _CONFIGURE_KEYS if k not in shown]
    if leftovers:
        sections.append({"title": "Other", "blurb": "",
                         "keys": [_setting(k, _CONFIGURE_KEYS[k], config) for k in leftovers]})
    return {"sections": sections}


@method("settings.set")
def set_(key: str, value: str | None) -> dict:
    """Validate and store one setting exactly as `watchdog settings <key> <value>` does. An empty
    value clears a free-text or model setting back to its default."""
    from watchdog.cmd import setup as setup_cmd

    if key not in setup_cmd._CONFIGURE_KEYS:
        raise RpcError(f"“{key}” isn't a setting.", code="unknown_key")
    meta = setup_cmd._CONFIGURE_KEYS[key]
    config = _read_config()
    text = "" if value is None else (value if isinstance(value, str) else str(value))

    if text.strip() == "" and meta.get("type") in ("string", "str"):
        config.pop(key, None)
        setup_cmd._persist(config)
        return {"key": key, "value": None, "display": _display(key, None, config)}
    if text.strip() == "" and meta.get("type") == "path":
        raise RpcError(f"“{key}” needs a folder.", code="bad_value")

    try:
        shown = setup_cmd._coerce_value(config, key, text)
    except setup_cmd._ConfigError as e:
        raise RpcError(str(e), code="bad_value")
    setup_cmd._persist(config)
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
            return "Claude Code login not detected — run claude in a terminal to sign in."
        if claude["env_key_set"]:
            return ("ANTHROPIC_API_KEY is set in your environment, and the Agent SDK uses it before "
                    "the subscription login, so runs would be metered.")
        return None
    return None if claude["key_masked"] else "API-key mode is set but no key is stored."


def _auth_status() -> dict:
    from watchdog.cmd import auth as auth_cmd

    d = auth_cmd.status_data()
    claude = d["claude"]
    mode = claude["mode"]
    if mode == "subscription":
        logged_in = bool(claude["logged_in"])
    else:
        logged_in = bool(claude["key_masked"])
    providers = []
    for p, meta in auth_cmd._PROVIDERS.items():
        providers.append({
            "provider": p, "label": meta["label"], "env": meta["env"],
            "requires_key": auth_cmd.provider_requires_key(p),
            "base_url_setting": meta.get("base_url_key"),
            "base_url": auth_cmd.get_base_url(p) if meta.get("base_url_key") else None,
            "ready": auth_cmd.provider_ready(p) if p != "anthropic" else bool(
                mode == "subscription" or auth_cmd.get_api_key("anthropic")),
        })
    return {
        "claude": {"mode": mode, "logged_in": logged_in, "reason": _claude_reason(claude),
                   "env_key_set": claude["env_key_set"], "key_masked": claude["key_masked"],
                   "key_source": claude["key_source"]},
        "stages": [{**s, "billing": mode if s["provider"] == "anthropic" else None}
                   for s in d["stages"]],
        "keys": [{"provider": k["provider"], "masked": k["masked"],
                  "in_use": "inactive" if k["status"].startswith("inactive") else k["status"],
                  "detail": k["status"], "source": k["source"]} for k in d["keys"]],
        "base_urls": d["base_urls"],
        "providers": providers,
    }


@method("auth.status")
def auth_status() -> dict:
    return _auth_status()


def _check_provider(provider: str) -> dict:
    from watchdog.cmd.auth import _PROVIDERS
    if provider not in _PROVIDERS:
        raise RpcError(f"“{provider}” isn't a model provider Watchdog knows.", code="bad_params")
    return _PROVIDERS[provider]


def _prefix_warning(meta: dict, key: str) -> str | None:
    prefix = meta.get("prefix")
    if prefix and not key.startswith(prefix):
        return f"The key doesn't start with “{prefix}” — stored anyway."
    return None


@method("auth.setAnthropicMode")
def set_anthropic_mode(mode: str, key: str | None = None) -> dict:
    """Switch Claude between your subscription login and a metered API key, as `watchdog settings
    auth` does — including the concurrency tune a subscription needs and an API key undoes."""
    from watchdog.cmd import auth as auth_cmd

    if mode not in ("subscription", "api-key"):
        raise RpcError("mode must be “subscription” or “api-key”.", code="bad_params")
    meta = auth_cmd._PROVIDERS["anthropic"]
    state = auth_cmd._load_state()
    warning = None
    state["mode"] = mode
    if mode == "api-key" and key is not None and key.strip():
        state["keys"]["anthropic"] = key.strip()
        warning = _prefix_warning(meta, key.strip())
    auth_cmd._save_state(state)
    if mode == "subscription":
        if auth_cmd._ingest_stage_provider(auth_cmd._load_config().get("extractor_model")) == "anthropic":
            auth_cmd._maybe_tune_concurrency_for_subscription()
    else:
        auth_cmd._maybe_restore_concurrency_from_subscription()
    return {**_auth_status(), "warning": warning}


@method("auth.setKey")
def set_key(provider: str, key: str) -> dict:
    from watchdog.cmd import auth as auth_cmd

    meta = _check_provider(provider)
    if not isinstance(key, str) or not key.strip():
        raise RpcError("Paste a key, or use remove to delete the stored one.", code="bad_params")
    state = auth_cmd._load_state()
    state["keys"][provider] = key.strip()
    auth_cmd._save_state(state)
    return {**_auth_status(), "warning": _prefix_warning(meta, key.strip())}


@method("auth.deleteKey")
def delete_key(provider: str) -> dict:
    from watchdog.cmd import auth as auth_cmd

    _check_provider(provider)
    state = auth_cmd._load_state()
    if state["keys"].pop(provider, None) is not None:
        auth_cmd._save_state(state)
    return {**_auth_status(), "warning": None}


@method("auth.setBaseUrl")
def set_base_url(provider: str, url: str) -> dict:
    """Set (or, with an empty `url`, remove) the endpoint of a provider that takes one."""
    from watchdog.cmd import auth as auth_cmd

    meta = _check_provider(provider)
    base_key = meta.get("base_url_key")
    if not base_key:
        raise RpcError(f"{provider} doesn't take a base URL.", code="bad_params")
    text = (url or "").strip() if isinstance(url, str) else ""
    config = auth_cmd._load_config()
    if text:
        parts = urlsplit(text)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise RpcError("The base URL should look like https://host/v1.", code="bad_params")
        config[base_key] = text.rstrip("/")
    else:
        config.pop(base_key, None)
    auth_cmd._save_config(config)
    return {**_auth_status(), "warning": None}


# ── skills ───────────────────────────────────────────────────────────────────────

@method("skills.list")
def skills_list() -> dict:
    from watchdog import skills_catalog

    user_dir = Path(skills_catalog.USER_SKILLS_DIR)
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


@method("setup.check")
def setup_check() -> dict:
    from watchdog import setup_cmd
    from watchdog.cmd.base import CONFIG_FILE, load_config

    deps = []
    for binary, label, hint in setup_cmd._DEPS:
        ok = shutil.which(binary) is not None
        deps.append({"label": label, "ok": ok, "hint": None if ok else hint})
    try:
        from watchdog.pipeline import capture
        playwright = bool(capture.render_available())
    except Exception:  # noqa: BLE001
        playwright = False
    config = load_config()
    return {
        "deps": deps,
        "playwright": playwright,
        "gliner_model": _gliner_cached(),
        "projects_dir": config.get("projects_dir") or None,
        "config_exists": CONFIG_FILE.exists(),
    }
