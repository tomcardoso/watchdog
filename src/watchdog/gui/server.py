"""`python -m watchdog.gui.server` — the desktop app's backend process.

Protocol: one JSON object per line on stdin and stdout, UTF-8.

    app → server   {"id": 7, "method": "vault.summary", "params": {"vault": "/path"}}
    server → app   {"id": 7, "result": {...}}
                   {"id": 7, "error": {"message": "...", "code": "error", "data": null}}
    server → app   {"event": "job.log", "data": {...}}          (unsolicited)

Requests are handled concurrently on a thread pool, so a slow search never blocks a quick
registry read; responses can therefore arrive out of order and are matched by `id`.

Watchdog's library code prints progress freely. The real stdout is kept for the protocol and
`sys.stdout` is pointed at stderr, so a stray `print()` anywhere can never corrupt a message; the
app logs stderr for diagnostics.
"""

from __future__ import annotations

import importlib
import json
import os
import pkgutil
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

from watchdog.gui import rpc

PROTOCOL_VERSION = 1


def load_api() -> None:
    """Import every module in `watchdog.gui.api` so their `@method` registrations run."""
    import watchdog.gui.api as api_pkg
    for mod in pkgutil.iter_modules(api_pkg.__path__):
        importlib.import_module(f"watchdog.gui.api.{mod.name}")


def handle(request: dict) -> dict | None:
    """Run one request and return its response message (None for a notification without id)."""
    rid = request.get("id")
    name = request.get("method")
    params = request.get("params") or {}
    fn = rpc.METHODS.get(name)
    try:
        if fn is None:
            raise rpc.RpcError(f"Unknown method: {name}", code="unknown_method")
        if not isinstance(params, dict):
            raise rpc.RpcError("params must be an object", code="bad_params")
        result = fn(**params)
        return {"id": rid, "result": rpc.jsonable(result)}
    except rpc.RpcError as e:
        return {"id": rid, "error": {"message": str(e), "code": e.code, "data": rpc.jsonable(e.data)}}
    except SystemExit as e:
        # Library code reused from the CLI reports user errors with sys.exit("Error: …").
        msg = e.code if isinstance(e.code, str) else f"exited with status {e.code}"
        return {"id": rid, "error": {"message": _clean_exit_message(msg), "code": "exit", "data": None}}
    except TypeError as e:
        # Almost always a wrong or missing parameter name from the app.
        traceback.print_exc(file=sys.stderr)
        return {"id": rid, "error": {"message": f"{name}: {e}", "code": "bad_params", "data": None}}
    except Exception as e:  # noqa: BLE001 — every failure must reach the app as a response
        traceback.print_exc(file=sys.stderr)
        return {"id": rid, "error": {"message": f"{type(e).__name__}: {e}", "code": "internal",
                                     "data": None}}


def _clean_exit_message(msg: str) -> str:
    msg = msg.strip()
    return msg[len("Error:"):].strip() if msg.startswith("Error:") else msg


def main() -> None:
    proto_out = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr
    os.environ.setdefault("NO_COLOR", "1")

    write_lock = threading.Lock()

    def write(msg: dict) -> None:
        line = json.dumps(msg, ensure_ascii=False)
        with write_lock:
            proto_out.write(line + "\n")
            proto_out.flush()

    rpc.set_writer(write)
    load_api()
    write({"event": "server.ready", "data": {"protocol": PROTOCOL_VERSION, "pid": os.getpid(),
                                             "methods": sorted(rpc.METHODS)}})

    pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="rpc")

    def run(req: dict) -> None:
        resp = handle(req)
        if resp is not None and req.get("id") is not None:
            write(resp)

    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            print(f"gui-server: unreadable request: {line[:200]}", file=sys.stderr)
            continue
        if req.get("method") == "server.shutdown":
            break
        pool.submit(run, req)

    # stdin closed (the app quit) — stop background work the api modules registered.
    for hook in list(SHUTDOWN_HOOKS):
        try:
            hook()
        except Exception:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
    pool.shutdown(wait=False, cancel_futures=True)


# Callables run when the app disconnects (jobs kill their subprocesses, chat closes sessions).
SHUTDOWN_HOOKS: list = []


if __name__ == "__main__":
    main()
