"""A small live-region renderer for long-running CLI commands (#151).

Owns a block of rows at the bottom of the terminal that are redrawn in place — one row per
in-flight unit of work — while finished/failed lines and notes scroll up into the permanent
scrollback above. No TUI dependency: a handful of ANSI cursor escapes, matching the app's
hand-rolled palette (see the CLI style guide in CLAUDE.md / cmd.base).

When stdout is not a TTY (CI, piped, `tee`), it degrades to append-only printing — every
`update`/`finish`/`note` just prints its line — so logs stay readable and unchanged.

Reusable: any command with concurrent or long-running steps can drive one of these. Callers
pass fully-formatted lines (including the 2-space indent and colour codes); the region only
truncates live rows to the terminal width so the redraw math stays one physical line per row.
"""

import re
import shutil
import sys

_ANSI = re.compile(r"\033\[[0-9;]*m")
_RESET = "\033[0m"


def _truncate(line: str, width: int) -> str:
    """Clip a line to `width` visible columns, preserving ANSI codes (zero visible width)
    and closing with a reset so colour never bleeds past the cut."""
    if width <= 0:
        return line
    out: list[str] = []
    visible = 0
    i = 0
    while i < len(line):
        m = _ANSI.match(line, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        if visible >= width - 1:
            out.append("…")
            out.append(_RESET)
            return "".join(out)
        out.append(line[i])
        visible += 1
        i += 1
    return "".join(out)


class LiveRegion:
    """A redraw-in-place region of keyed rows with a scrollback of finished lines above it."""

    def __init__(self, stream=None, *, enabled: bool | None = None):
        self.stream = stream if stream is not None else sys.stdout
        self.enabled = self.stream.isatty() if enabled is None else enabled
        self._rows: dict[str, str] = {}
        self._order: list[str] = []
        self._rendered = 0          # live rows currently drawn (== physical lines, post-truncation)

    # ── public API ────────────────────────────────────────────────────────────

    def update(self, key: str, tty_line: str, plain_line: str | None = None) -> None:
        """Add or mutate the in-flight row for `key`. Non-TTY prints `plain_line` (or `tty_line`)."""
        if not self.enabled:
            self._print(plain_line if plain_line is not None else tty_line)
            return
        if key not in self._rows:
            self._order.append(key)
        self._rows[key] = tty_line
        self._render()

    def finish(self, key: str, line: str) -> None:
        """Settle `key`: print `line` permanently above the region and drop its live row."""
        if not self.enabled:
            self._print(line)
            return
        self._emit_above(line)
        if key in self._rows:
            self._order.remove(key)
            del self._rows[key]
        self._render()

    def note(self, line: str) -> None:
        """Print a permanent line above the live region (a log entry not tied to a row)."""
        if not self.enabled:
            self._print(line)
            return
        self._emit_above(line)
        self._render()

    def stop(self) -> None:
        """Leave the final region on screen and flush. Call once when work is done."""
        if self.enabled:
            self.stream.flush()

    # ── rendering ─────────────────────────────────────────────────────────────

    def _print(self, line: str) -> None:
        self.stream.write(line + "\n")
        self.stream.flush()

    def _clear(self) -> None:
        """Move to the top of the live region and erase it; cursor ends where it began."""
        if self._rendered:
            self.stream.write(f"\033[{self._rendered}A\033[J")
        self._rendered = 0

    def _render(self) -> None:
        self._clear()
        width = shutil.get_terminal_size((80, 24)).columns
        for key in self._order:
            self.stream.write(_truncate(self._rows[key], width) + "\n")
        self._rendered = len(self._order)
        self.stream.flush()

    def _emit_above(self, line: str) -> None:
        self._clear()
        self.stream.write(line + "\n")
