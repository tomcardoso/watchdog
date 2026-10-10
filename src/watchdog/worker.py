"""`python -m watchdog.worker`: the process the desktop app runs each operation in (D298).

Protocol. The app starts this module with the investigation as its working folder (none for an
operation that isn't in one) and writes to its stdin:

    {"op": "<name>", "params": {…}}\\n          the operation and its parameters
    {"keys": {…}}\\n                             only with WATCHDOG_SECRETS=stdin (D295)

then closes it. The worker runs the operation with an `AppReporter`: plain log lines on stdout,
warnings and errors on stderr, `watchdog.progress` events (`\\x1eWDP {json}`) on stdout, and last
of all one `{"kind": "result", "op", "result"}` event carrying the operation's return value. It
exits 0, the operation's own code (2: stopped partway, a re-run resumes), 1 on an error (the
reason is the last stderr line), 64 on a request it can't read, or 130 when stopped by a signal.

Stop is Ctrl+C: SIGINT (CTRL_BREAK_EVENT on Windows), which the pipeline turns into its graceful
stop, keeping finished documents; a second Stop kills the process.
"""

from __future__ import annotations

import json
import os
import signal
import sys


def _read_request() -> dict:
    line = sys.stdin.readline()
    try:
        req = json.loads(line) if line.strip() else None
    except ValueError:
        req = None
    if not isinstance(req, dict) or not isinstance(req.get("op"), str):
        raise ValueError("The worker expects {\"op\": …, \"params\": {…}} on its first line.")
    return req


def main() -> int:
    os.environ["WATCHDOG_APP"] = "1"
    os.environ["WATCHDOG_PROGRESS"] = "1"
    os.environ.setdefault("NO_COLOR", "1")
    if os.name == "nt":
        # Stop arrives as CTRL_BREAK_EVENT in the job's own process group.
        signal.signal(signal.SIGBREAK, signal.default_int_handler)   # type: ignore[attr-defined]

    from watchdog import ops, progress
    from watchdog.keystore import read_stdin

    try:
        req = _read_request()
    except ValueError as e:
        sys.stderr.write(f"{e}\n")
        return 64
    read_stdin()      # the keys line, when the app hands this operation its keys (D295)
    name = req["op"]
    rep = ops.AppReporter()
    try:
        from watchdog.cmd.auth import KeyChoiceError
        try:
            result = ops.run(name, req.get("params") or {}, rep)
        except ops.OpError as e:
            rep.warn(str(e))
            return 64
        except KeyChoiceError as e:
            rep.warn(str(e))
            return 1
    except KeyboardInterrupt:
        rep.warn("Stopped.")
        return 130
    except SystemExit as e:
        if isinstance(e.code, str):
            rep.warn(e.code.strip().removeprefix("Error:").strip())
            return 1
        return int(e.code or 0)
    except ModuleNotFoundError as e:
        component = (e.name or "a required package").split(".")[0]
        rep.warn(f"A component this step needs ({component}) is missing from this installation "
                 f"of Watchdog, so it could not run. Reinstalling the app restores it.")
        return 1
    progress.write("result", op=name, result=result)
    return ops.exit_code(name, result)


if __name__ == "__main__":
    sys.exit(main())
