"""Method registry and event channel shared by every `watchdog.gui.api` module.

A module registers a handler with `@method("vault.summary")`. Handlers take keyword arguments
(the request's `params`) and return anything `json.dumps` can serialize, after `jsonable()`
converts `Path`s, sets and tuples. A handler signals a user-facing failure by raising `RpcError`;
any other exception is reported as an internal error with its message.

`emit(event, data)` pushes an unsolicited message to the app (job output, chat tokens). The
server installs the real writer at start-up; until then, and in tests, events go to
`_test_sink` when one is set and are otherwise dropped.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

METHODS: dict[str, Callable[..., Any]] = {}

_writer: Callable[[dict], None] | None = None
_writer_lock = threading.Lock()
_test_sink: list | None = None

# Callables run when the app disconnects. Kept here rather than in `server` because the server runs
# as `__main__`: a module that imports `watchdog.gui.server` gets a second copy of that module, and
# a hook appended to the copy's list would never run.
SHUTDOWN_HOOKS: list[Callable[[], Any]] = []


class RpcError(Exception):
    """A failure the app should show to the user as-is (bad input, missing vault, …)."""

    def __init__(self, message: str, code: str = "error", data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


def method(name: str):
    """Register the decorated function as RPC method `name`."""
    def deco(fn):
        if name in METHODS and METHODS[name] is not fn:
            raise RuntimeError(f"RPC method registered twice: {name}")
        METHODS[name] = fn
        return fn
    return deco


def set_writer(writer: Callable[[dict], None] | None) -> None:
    global _writer
    _writer = writer


def emit(event: str, data: Any = None) -> None:
    """Send an event to the app. Thread-safe; a no-op when nothing is listening."""
    msg = {"event": event, "data": jsonable(data)}
    if _test_sink is not None:
        _test_sink.append(msg)
        return
    if _writer is None:
        return
    with _writer_lock:
        _writer(msg)


def jsonable(value: Any) -> Any:
    """Recursively convert values `json` can't serialize: Path → str, set/tuple → list."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(v) for v in value]
    return value
