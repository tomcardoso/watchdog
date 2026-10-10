"""API keys encrypted by the desktop app, and the keys it hands this process (D295).

Under the app, each key in `~/.watchdog/credentials.json` is stored as an encrypted blob,
`{"enc": "safeStorage:v1", "data": "<base64>", "masked": "sk-ant-api…abcd"}`. Electron's
`safeStorage` in the app's main process made it, with a key the operating system holds for the
app (the macOS Keychain, Windows DPAPI, the Linux keyring). The file's structure (providers, key
ids, names, the default) stays readable; only the secrets are encrypted. `masked` is the same
masked form Settings always showed, so a status display needs no decryption.

Python never decrypts. The main process decrypts and gives this process the keys it needs, over a
private pipe, never through the environment or argv:

  - the app's backend receives every readable key with `secrets.provide` on its stdin (the RPC
    channel), right after it starts;
  - each `watchdog` job the backend starts receives only the keys its investigation resolves to,
    one per provider, as one JSON line on its stdin (`WATCHDOG_SECRETS=stdin`; `read_stdin`).

The keys live only in this module's memory, keyed by the blob's `data`, so `reveal(blob)` turns
a stored blob into the key it stands for, or None when this process wasn't given it. A command
run from a terminal is given nothing: `auth.resolve_key` then stops with a message pointing at
the environment variable, which still overrides every stored key.
"""

from __future__ import annotations

import json
import os
import sys
import threading

ENC_TAG = "safeStorage:v1"
SECRETS_ENV = "WATCHDOG_SECRETS"

_lock = threading.Lock()
_plain: dict[str, str] = {}
# `store`: "encrypted" when the app encrypts keys (a write must carry the app's blob), "plaintext"
# when the app said this computer has no usable secure storage, None when no app has said anything
# (a terminal command, a job, the test suite).
_status: dict = {"store": None, "backend": None, "reason": None, "unreadable": 0}


def is_blob(value) -> bool:
    return (isinstance(value, dict) and value.get("enc") == ENC_TAG
            and isinstance(value.get("data"), str) and bool(value.get("data")))


def reveal(blob) -> str | None:
    """The key a stored blob stands for, when this process was given it."""
    if not is_blob(blob):
        return None
    with _lock:
        return _plain.get(blob["data"])


def remember(blob: dict, key: str) -> None:
    """Keep a key the app has just encrypted, so this process can use it without a restart."""
    if is_blob(blob) and key:
        with _lock:
            _plain[blob["data"]] = key


def provide(keys: dict | None = None, *, store: str | None = None, backend: str | None = None,
            reason: str | None = None, unreadable: int = 0, replace: bool = True) -> None:
    """Take the keys the app decrypted (`{blob data: key}`) and what it says about storage."""
    clean = {str(d): str(k) for d, k in (keys or {}).items() if d and isinstance(k, str) and k}
    with _lock:
        if replace:
            _plain.clear()
        _plain.update(clean)
        if store is not None:
            _status.update(store=store, backend=backend, reason=reason, unreadable=int(unreadable or 0))


def forget() -> None:
    """Drop every key and the storage status (tests)."""
    with _lock:
        _plain.clear()
        _status.update(store=None, backend=None, reason=None, unreadable=0)


def encrypting() -> bool:
    """Whether the app encrypts keys on this computer, so a write must carry its blob."""
    return _status["store"] == "encrypted"


def status() -> dict:
    with _lock:
        return dict(_status)


def read_stdin() -> None:
    """A job started by the app with `WATCHDOG_SECRETS=stdin` reads its keys here: one JSON line,
    then end of input (anything that later reads stdin sees it closed, as before). The variable is
    removed so this process's own children don't wait for keys that never come."""
    if os.environ.pop(SECRETS_ENV, None) != "stdin":
        return
    try:
        line = sys.stdin.readline()
        data = json.loads(line) if line.strip() else {}
    except (OSError, ValueError):
        data = {}
    if isinstance(data, dict):
        provide(data.get("keys") if isinstance(data.get("keys"), dict) else {}, replace=True)


def child_input(keys: dict[str, str]) -> bytes:
    """The stdin line `read_stdin` expects."""
    return (json.dumps({"keys": keys}) + "\n").encode("utf-8")
