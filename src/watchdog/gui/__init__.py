"""The desktop app's Python side.

The Electron app (`gui/` at the repository root) starts `python -m watchdog.gui.server` and talks
to it over stdin/stdout, one JSON message per line. Reads are answered in-process from the vault's
files and registries; anything that changes vault state is an operation (`watchdog.ops`) run in
a worker process (`watchdog.gui.jobs`, `watchdog.worker`, D298). See `gui/README.md` and
`gui/API.md`.
"""
