"""`LiveRegion` now lives in watchdog.terminal (#636) — a neutral module with no dependency on
this package, so pipeline modules (orchestrate.py, preprocess_batch.py) can use it without
importing from `cmd`. Re-exported here so existing `from watchdog.cmd.live import LiveRegion`
call sites, and tests/test_live.py's `watchdog.cmd.live.shutil.get_terminal_size` monkeypatch
(which needs `shutil` to still be a real attribute of this module), keep working unchanged.
"""

import shutil  # noqa: F401 — re-exported; see module docstring

from watchdog.terminal import LiveRegion, _StderrTap, _terminal_width, _truncate  # noqa: F401
