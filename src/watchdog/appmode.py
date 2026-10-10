"""Whether this process is running under the desktop app, and wording that depends on it.

The app's worker processes run with `WATCHDOG_APP=1` (`gui/jobs.py`, `worker.py`). Their output is
shown in Activity's job logs and in the Add documents dialog, so a line there must not tell the
reader to type a command, press a key or pass a flag (D267, issue #729). `hint(terminal=…, app=…)`
is the one place a message says both: the terminal wording while the command line still exists,
the app wording (or nothing, when the app has no equivalent) under the app. Inside an operation,
`watchdog.ops.hint` asks the operation's reporter instead (D298).
"""

from __future__ import annotations

import os


def under_app() -> bool:
    return os.environ.get("WATCHDOG_APP") == "1"


def hint(terminal: str, app: str | None = "") -> str:
    """`terminal` at a terminal; `app` under the app. `app=None` or `""` drops the text."""
    if under_app():
        return app or ""
    return terminal
