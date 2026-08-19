"""Colour constants and the live-progress region — CLI-facing output shared by both the `cmd/*.py`
commands and the pipeline modules that print their own progress (`pipeline/orchestrate.py`,
`pipeline/preprocess_batch.py`) without needing to depend on the `cmd` package (#636). `cmd/base.py`
and `cmd/live.py` re-export everything here so existing `from watchdog.cmd.base import ...` /
`from watchdog.cmd.live import ...` call sites keep working unchanged.
"""

import contextlib
import os
import re
import shutil
import sys
import threading


def _color_enabled() -> bool:
    """Whether to emit ANSI colour codes at all (#499). `NO_COLOR` (any non-empty value) forces
    them off; otherwise follow whether stdout is a real terminal. Gated on stdout specifically,
    not stderr — the standard simplification, and it means a piped stdout also de-colours the few
    warnings this module prints to stderr (see D174).

    `FORCE_COLOR` is deliberately *not* honoured: Claude Code sets `FORCE_COLOR=3` in the
    environment it hands to shell commands, so honouring it would force colour back on for the
    exact reader this gate exists to protect — a session piping `watchdog status` into its own
    context and paying tokens for the escape bytes (D174)."""
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


_COLOR = _color_enabled()

_BOLD   = "\033[1m" if _COLOR else ""
_DIM    = "\033[2m" if _COLOR else ""
_CYAN   = "\033[0;36m" if _COLOR else ""
_YELLOW = "\033[0;33m" if _COLOR else ""
_GREEN  = "\033[0;32m" if _COLOR else ""
_RESET  = "\033[0m" if _COLOR else ""


# ── Live-progress region (#151) ──────────────────────────────────────────────
#
# Owns a block of rows at the bottom of the terminal that are redrawn in place — one row per
# in-flight unit of work — while finished/failed lines and notes scroll up into the permanent
# scrollback above. No TUI dependency: a handful of ANSI cursor escapes, matching the app's
# hand-rolled palette above.
#
# When stdout is not a TTY (CI, piped, `tee`), it degrades to append-only printing — every
# `update`/`finish`/`note` just prints its line — so logs stay readable and unchanged.
#
# Reusable: any command with concurrent or long-running steps can drive one of these. Callers
# pass fully-formatted lines (including the 2-space indent and colour codes); the region only
# truncates live rows to the terminal width so the redraw math stays one physical line per row.

_ANSI = re.compile(r"\033\[[0-9;]*m")
# Deliberately independent of the _color_enabled() gate above (#499, D174): this closes clipped
# colour spans inside the live-progress region itself, whose own cursor escapes are already gated
# separately on `self.stream.isatty()` below — and tests/test_live.py asserts the literal escape
# byte. Named distinctly from the gated `_RESET` above so the two constants — gated and
# unconditional — can't collide now that both live in this module.
_LIVE_RESET = "\033[0m"


def _terminal_width(stream) -> int:
    """Physical column count for `stream`'s real fd, queried straight from the OS. Deliberately
    NOT `shutil.get_terminal_size()`, which checks the `COLUMNS` env var first — that can go
    stale (e.g. after a resize) and silently disagree with the real terminal, which corrupts the
    live region's redraw math: a row that wraps onto an extra physical line the region didn't
    account for leaves stale duplicate text on screen after the next redraw."""
    try:
        return os.get_terminal_size(stream.fileno()).columns
    except (OSError, ValueError, AttributeError):
        return shutil.get_terminal_size((80, 24)).columns


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
            out.append(_LIVE_RESET)
            return "".join(out)
        out.append(line[i])
        visible += 1
        i += 1
    return "".join(out)


class LiveRegion:
    """A redraw-in-place region of keyed rows with a scrollback of finished lines above it.

    Thread-safe: a single internal lock serialises every public call, so concurrent workers
    (e.g. chew's ThreadPoolExecutor, #158) can drive one region without interleaved redraws or
    torn output lines. Single-threaded callers (ingest's asyncio loop) pay only an uncontended
    lock acquire per call."""

    def __init__(self, stream=None, *, enabled: bool | None = None):
        self.stream = stream if stream is not None else sys.stdout
        self.enabled = self.stream.isatty() if enabled is None else enabled
        self._rows: dict[str, str] = {}
        self._order: list[str] = []
        self._pinned: set[str] = set()   # keys always rendered last, e.g. chew's summary
                                          # progress bar (#158) — anchored at the bottom instead
                                          # of wherever it happened to be inserted, so it doesn't
                                          # appear sandwiched between finished and in-flight rows
        self._rendered = 0          # live rows currently drawn (== physical lines, post-truncation)
        self._lock = threading.Lock()

    # ── public API ────────────────────────────────────────────────────────────

    def update(self, key: str, tty_line: str, plain_line: str | None = None, *,
              pin: bool = False) -> None:
        """Add or mutate the in-flight row for `key`. Non-TTY prints `plain_line` (or `tty_line`).
        `pin=True` keeps this row rendered last among live rows regardless of insertion order —
        for a persistent summary row that should stay anchored at the bottom while individual
        in-flight rows come and go above it."""
        with self._lock:
            if not self.enabled:
                self._print(plain_line if plain_line is not None else tty_line)
                return
            if key not in self._rows:
                self._order.append(key)
            if pin:
                self._pinned.add(key)
            self._rows[key] = tty_line
            self._render()

    def finish(self, key: str, line: str) -> None:
        """Settle `key`: print `line` permanently above the region and drop its live row."""
        with self._lock:
            if not self.enabled:
                self._print(line)
                return
            self._emit_above(line)
            if key in self._rows:
                self._order.remove(key)
                del self._rows[key]
            self._pinned.discard(key)
            self._render()

    def note(self, line: str) -> None:
        """Print a permanent line above the live region (a log entry not tied to a row)."""
        with self._lock:
            if not self.enabled:
                self._print(line)
                return
            self._emit_above(line)
            self._render()

    def stop(self) -> None:
        """Leave the final region on screen and flush. Call once when work is done."""
        with self._lock:
            if self.enabled:
                self.stream.flush()

    @contextlib.contextmanager
    def capture_stderr(self):
        """While active, fold writes to `sys.stderr` into this region's own scrollback
        (`note`) instead of letting them land on the terminal directly (#419). The region's
        redraw math assumes it owns the screen rows below its last render; a third-party
        write (a library's warning, an unauthenticated-request notice) that lands directly on
        the terminal shifts the cursor by lines `_rendered` doesn't know about, so the next
        `_clear()` erases the wrong rows and leaves stale duplicate text on screen. Routing
        those writes through `note()` keeps the region's own bookkeeping authoritative
        regardless of what else writes to stderr mid-run. No-op when the region is disabled
        (no TTY, so there's no cursor math to protect)."""
        if not self.enabled:
            yield
            return
        real_stderr = sys.stderr
        tap = _StderrTap(self)
        sys.stderr = tap
        try:
            yield
        finally:
            tap.flush()
            sys.stderr = real_stderr

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
        width = _terminal_width(self.stream)
        ordered = [k for k in self._order if k not in self._pinned] + \
                  [k for k in self._order if k in self._pinned]
        for key in ordered:
            self.stream.write(_truncate(self._rows[key], width) + "\n")
        self._rendered = len(self._order)
        self.stream.flush()

    def _emit_above(self, line: str) -> None:
        self._clear()
        self.stream.write(line + "\n")


class _StderrTap:
    """Write-only stream that lines-buffer foreign stderr writes and hands each complete line
    to a `LiveRegion`'s `note()`, so they scroll into its permanent output instead of
    corrupting its redraw math (#419, see `LiveRegion.capture_stderr`)."""

    def __init__(self, region: LiveRegion):
        self._region = region
        self._buf = ""

    def write(self, s: str) -> int:
        if not s:
            return 0
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line:
                self._region.note(line)
        return len(s)

    def flush(self) -> None:
        if self._buf:
            self._region.note(self._buf)
            self._buf = ""

    def isatty(self) -> bool:
        return False
