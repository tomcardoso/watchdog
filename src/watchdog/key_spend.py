"""Spending limits per stored API key (D347): what each key has paid for in Watchdog on this
computer, across every investigation, and the limit the reporter set on it.

A key is named by its reference, `<provider>:<id>` (the stable id from `credentials.json`, never
the key itself and never its label, which can be renamed). Every call a stored key pays for, in
processing or in an Ask Claude or research session, appends one line to
`~/.watchdog/key_spend.jsonl`: `{"key", "ts", "usd"}`. That log, not the telemetry store (which
the reporter can switch off), is what a limit is counted against. Calls on the Claude
subscription and calls paid by a key set in the environment are not logged: neither has a
stored key to limit.

Limits live in `config.json` under `key_limits`: `{ref: {"usd": float, "since": iso}}`. A limit
counts what was spent since `since`, which is when it was set or last reset, so calls made before
a key had a limit never count towards it. Recorded cost only, never an estimate (D324).
"""

from __future__ import annotations

import datetime
import json
import os
import time

from watchdog import config

LEDGER_NAME = "key_spend.jsonl"
_TS_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"     # to the microsecond: a reset counts nothing before it


def ledger_path():
    return config.home() / LEDGER_NAME


def ref(provider: str, key_id: str) -> str:
    return f"{provider}:{key_id}"


def ref_for_label(provider: str, label: str | None) -> str | None:
    """The reference of the stored key named `label` (labels are unique per provider, D290)."""
    if not provider or not label:
        return None
    from watchdog import auth
    for item in auth.list_keys(provider, auth.load_state()):
        if item.get("label") == label:
            return ref(provider, item["id"])
    return None


def record(provider: str | None, label: str | None, usd, *, auth_mode: str | None = None) -> None:
    """Log one call's cost against the stored key that paid. Never raises: a failed write must not
    fail a run or a conversation."""
    try:
        if auth_mode == "subscription" or not usd or float(usd) <= 0:
            return
        key = ref_for_label(provider, label)
        if not key:
            return
        line = json.dumps({"key": key, "ts": time.time(), "usd": round(float(usd), 6)}) + "\n"
        path = ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # One short line per write, appended: concurrent writers (a run's worker, a session in
        # the app's backend) don't interleave inside a line.
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:  # noqa: BLE001
        pass


def spent(key: str, since: float | None = None) -> float:
    """What `key` has paid for since `since` (epoch seconds; None for all time)."""
    total = 0.0
    try:
        with ledger_path().open(encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("key") == key and (since is None or (row.get("ts") or 0) > since):
                    total += float(row.get("usd") or 0)
    except OSError:
        return 0.0
    return round(total, 6)


def _parse(stamp) -> float | None:
    for fmt in (_TS_FMT, "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.datetime.strptime(stamp, fmt).replace(tzinfo=datetime.timezone.utc).timestamp()
        except (TypeError, ValueError):
            continue
    return None


def _now_stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(_TS_FMT)


def _load_config() -> dict:
    """`config.json` for a write: a damaged file is refused with a sentence to show, never
    replaced (`config.load` would end the process instead)."""
    try:
        return config.load()
    except SystemExit:
        raise ValueError(f"The settings file is damaged. Fix or remove {config.path()}.") from None


def limits() -> dict:
    raw = config.read().get("key_limits")
    return raw if isinstance(raw, dict) else {}


def status(key: str) -> dict:
    """`{"usd", "since", "spent", "reached"}` for one key; `usd` None when it has no limit."""
    lim = limits().get(key) if isinstance(limits().get(key), dict) else {}
    usd = lim.get("usd") if isinstance(lim.get("usd"), (int, float)) and not isinstance(lim.get("usd"), bool) else None
    since = lim.get("since") if _parse(lim.get("since")) is not None else None
    used = spent(key, _parse(since)) if usd is not None else spent(key)
    return {"usd": float(usd) if usd is not None else None, "since": since, "spent": used,
            "reached": usd is not None and used >= usd}


def set_limit(key: str, value) -> float | None:
    """Set (or, with a blank value, remove) a key's limit, counting from now. Raises ValueError,
    with a sentence to show, for a value that isn't an amount (`spend.parse_usd`'s rules)."""
    from watchdog import spend
    usd = spend.parse_usd(value)
    cfg = _load_config()
    table = dict(cfg.get("key_limits") or {})
    if usd is None:
        table.pop(key, None)
    else:
        prev = table.get(key) if isinstance(table.get(key), dict) else {}
        table[key] = {"usd": usd, "since": prev.get("since") or _now_stamp()}
    if table:
        cfg["key_limits"] = table
    else:
        cfg.pop("key_limits", None)
    config.save(cfg)
    return usd


def reset(key: str) -> None:
    """Count the key's spending from now; earlier calls stay in the log but no longer count."""
    cfg = _load_config()
    table = dict(cfg.get("key_limits") or {})
    if isinstance(table.get(key), dict):
        table[key] = {**table[key], "since": _now_stamp()}
        cfg["key_limits"] = table
        config.save(cfg)


def stop_message(provider: str, label: str | None, key: str) -> str | None:
    """The reason to stop before another call this key would pay for, or None."""
    st = status(key)
    if not st["reached"]:
        return None
    names = {"anthropic": "Anthropic", "openai": "OpenAI", "deepseek": "DeepSeek",
             "gemini": "Google Gemini", "openrouter": "OpenRouter", "local": "local model"}
    name = f"{names.get(provider, provider)} key “{label or key}”"
    return (f"The {name} has reached its spending limit (${st['spent']:.2f} of ${st['usd']:.2f}). "
            "Raise or reset the limit under Settings → Models & keys, or choose another key.")


def check_key(provider: str, key_value: str | None) -> str | None:
    """`stop_message` for the stored key whose secret is `key_value`; None for an environment
    key, the subscription, or a key with no limit."""
    from watchdog import auth
    label = auth.label_for_key(provider, key_value)
    key = ref_for_label(provider, label)
    return stop_message(provider, label, key) if key else None


def forget(provider: str, key_id: str | None = None) -> None:
    """Drop the limit of a deleted key, or of every key of `provider` when `key_id` is None. Its
    logged spending stays (Usage still shows it); nothing counts against a key that is gone."""
    cfg = config.read()
    table = dict(cfg.get("key_limits") or {})
    prefix = f"{provider}:"
    keep = {k: v for k, v in table.items()
            if not (k == ref(provider, key_id) if key_id else k.startswith(prefix))}
    if keep != table:
        cfg = _load_config()
        if keep:
            cfg["key_limits"] = keep
        else:
            cfg.pop("key_limits", None)
        config.save(cfg)

