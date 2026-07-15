"""Shared interactive-input helpers for Watchdog's CLI: an arrow-key list picker and a
single-keypress y/n confirm, both falling back to a blocking numbered/`input()` prompt when
raw mode isn't available (non-tty, piped input, tests, Windows — no `termios`).

Extracted from setup.py's `_pick_skill_arrow`/`_wizard_menu`, which each duplicated the same
termios/tty raw-mode setup, escape-sequence parsing (arrows/j/k/Enter/q), and numbered-prompt
fallback (see DECISIONS D93) — every interactive prompt in the CLI now shares this one
implementation instead of hand-rolling "[y/N]"/"Choice [1]:"-style text.
"""

import os
import sys

from watchdog.cmd.base import _BOLD, _CYAN, _DIM, _RESET


class Header:
    """A non-selectable section-header row for `pick()`'s grouped menus — rendered dim and
    skipped by arrow navigation and the numbered fallback's counting. Wrap a label in
    `Header(...)` to mark it as one."""
    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text


CANCELLED = object()  # sentinel: user backed out of pick() (q/Ctrl-C in raw mode, or an
                       # empty/invalid answer in the numbered fallback). Never a valid list
                       # index — safe to compare against with `is`.


def _raw_stdin():
    """(fd, saved_termios_state) if stdin supports raw single-keypress reads, else (None,
    None) — on a non-tty, piped input, Windows (no `termios`), or a stdin object with no real
    file descriptor (e.g. pytest's captured stdin)."""
    try:
        import termios
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
    except Exception:
        return None, None
    return fd, old


def pick(items, current=0, *, title=None, hint="↑/↓ move · Enter select · q cancel"):
    """Arrow-key list picker over `items` — a list of row labels (plain strings are
    selectable rows; wrap a label in `Header(...)` to render it as a dimmed, non-selectable
    section title instead). `current` is the initial 0-based index among the *selectable*
    rows.

    Returns the absolute index into `items` of the chosen row, or `CANCELLED`.

    Tries raw single-keypress navigation on stdin first (↑/↓ or j/k to move, Enter to select,
    q/Ctrl-C to cancel). Falls back to a blocking numbered `input()` prompt when raw mode
    isn't available, or the terminal is shorter than the menu.
    """
    selectable = [i for i, it in enumerate(items) if not isinstance(it, Header)]
    if not selectable:
        return CANCELLED
    sel = max(0, min(current, len(selectable) - 1))

    fd, old = _raw_stdin()
    try:
        fits = os.get_terminal_size().lines >= len(items) + 4
    except OSError:
        fits = True

    if fd is None or not fits:
        return _numbered_fallback(items, selectable, title=title)

    import termios
    import tty

    header_lines = 1 if title else 0

    def render(first: bool) -> None:
        if not first:
            sys.stdout.write(f"\x1b[{1 + header_lines + len(items)}A\r")
        if title:
            sys.stdout.write(f"  {_BOLD}{title}{_RESET}\x1b[K\n")
        for i, it in enumerate(items):
            if isinstance(it, Header):
                sys.stdout.write(f"  {_DIM}{it.text}{_RESET}\x1b[K\n")
            elif i == selectable[sel]:
                sys.stdout.write(f"  {_CYAN}❯ {it}{_RESET}\x1b[K\n")
            else:
                sys.stdout.write(f"    {it}\x1b[K\n")
        sys.stdout.write("\x1b[K\n")                    # blank line between the menu and the hint
        sys.stdout.write(f"  {_DIM}{hint}{_RESET}\x1b[K")
        sys.stdout.flush()

    result = None
    print()
    try:
        tty.setcbreak(fd)
        render(first=True)
        while result is None:
            ch = sys.stdin.read(1)
            if ch in ("\r", "\n"):
                result = selectable[sel]
            elif ch in ("q", "\x03", ""):           # q, Ctrl-C, or EOF
                result = CANCELLED
            elif ch == "\x1b":                      # arrow-key escape sequence
                seq = sys.stdin.read(2)
                if seq == "[A":
                    sel = (sel - 1) % len(selectable)
                    render(False)
                elif seq == "[B":
                    sel = (sel + 1) % len(selectable)
                    render(False)
            elif ch == "k":
                sel = (sel - 1) % len(selectable)
                render(False)
            elif ch == "j":
                sel = (sel + 1) % len(selectable)
                render(False)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        # The blank spacer + move/select/cancel hint are only useful while a choice is being
        # made — erase both (the cursor sits at the end of the hint row, one row below the
        # spacer) instead of leaving them behind in the scrollback once the interaction is over.
        sys.stdout.write("\r\x1b[K\x1b[1A\x1b[K\n")
        sys.stdout.flush()

    return result


def _numbered_fallback(items, selectable, *, title=None):
    """Blocking numbered prompt used when raw mode isn't available. Returns the absolute
    index into `items` of the chosen row, or `CANCELLED`."""
    if title:
        print(f"\n  {_BOLD}{title}{_RESET}")
    else:
        print()
    for i, it in enumerate(items):
        if isinstance(it, Header):
            print(f"\n  {_BOLD}{it.text}{_RESET}")
        else:
            n = selectable.index(i) + 1
            print(f"    {_CYAN}{n:>2}{_RESET}  {it}")
    try:
        ans = input("\n  Number (Enter to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return CANCELLED
    if ans.isdigit() and 1 <= int(ans) <= len(selectable):
        return selectable[int(ans) - 1]
    return CANCELLED


def confirm(prompt: str, default: bool = True) -> bool:
    """Single-keypress y/n confirm. `prompt` should NOT include the "[Y/n]"/"[y/N]" suffix —
    it's appended based on `default`, matching the existing bracket convention.

    Tries a raw single keypress (y/Y/n/N commits instantly; Enter accepts `default`; any
    other key is ignored and the prompt keeps waiting) first, falling back to a blocking
    `input(...).strip().lower()`-style prompt — "" accepts `default`, "y"/"yes" is True,
    anything else is False — when raw mode isn't available (non-tty, piped input, tests).
    """
    suffix = "[Y/n]" if default else "[y/N]"
    fd, old = _raw_stdin()

    if fd is None:
        try:
            answer = input(f"{prompt} {suffix} ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        return default if answer == "" else answer in ("y", "yes")

    import termios
    import tty

    sys.stdout.write(f"{prompt} {suffix} ")
    sys.stdout.flush()
    result = None
    try:
        tty.setcbreak(fd)
        while result is None:
            ch = sys.stdin.read(1)
            if ch in ("y", "Y"):
                result = True
            elif ch in ("n", "N"):
                result = False
            elif ch in ("\r", "\n"):
                result = default
            elif ch in ("\x03", ""):                # Ctrl-C or EOF
                result = False
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    print("y" if result else "n")
    return result


def read_answer(prompt: str) -> str:
    """Read a short interactive answer for prompts that don't fit `confirm()`'s plain y/n
    shape (e.g. a tri-state "y/n/Enter-keeps-current" prompt, which needs to tell an explicit
    empty answer apart from invalid input).

    Raw mode: a single keypress, lowercased ("" for Enter, Ctrl-C, or EOF). Fallback: a full
    line via `input()`, stripped and lowercased — so multi-character answers ("yes", "true")
    still work off a TTY (piped input, tests).
    """
    fd, old = _raw_stdin()
    if fd is None:
        try:
            return input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return ""

    import termios
    import tty

    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    result = "" if ch in ("\r", "\n", "", "\x03") else ch.lower()
    print(result)
    return result
