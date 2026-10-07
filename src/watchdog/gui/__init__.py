"""The desktop app's Python side.

The Electron app (`gui/` at the repository root) starts `python -m watchdog.gui.server` and talks
to it over stdin/stdout, one JSON message per line. Reads are answered in-process from the vault's
files and registries; anything that changes vault state runs the real `watchdog` command as a
subprocess (`watchdog.gui.jobs`), so the app and the terminal behave identically. See
`gui/README.md` and `gui/API.md`.
"""
