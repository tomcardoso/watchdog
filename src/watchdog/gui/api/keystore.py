"""`secrets.provide` — the app's main process hands this backend the keys it decrypted (D295).

Called by the main process alone, once the backend has started and before the app's window can
send anything; the main process refuses it from the window. The keys arrive over this process's
stdin, which only the main process holds, and are kept in memory (`watchdog.keystore`), never in
the environment or on disk. Nothing returns a key.
"""

from __future__ import annotations

from watchdog.gui.rpc import RpcError, method

_STORES = ("encrypted", "plaintext")


@method("secrets.provide")
def provide(store: str, keys: dict | None = None, backend: str | None = None,
            reason: str | None = None, unreadable: int = 0) -> dict:
    from watchdog import keystore

    if store not in _STORES:
        raise RpcError("store must be “encrypted” or “plaintext”.", code="bad_params")
    if keys is not None and not isinstance(keys, dict):
        raise RpcError("keys must be an object.", code="bad_params")
    keystore.provide(keys or {}, store=store, backend=backend, reason=reason,
                     unreadable=unreadable, replace=True)
    return {"ok": True, "keys": len(keys or {})}
