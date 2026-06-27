"""LiveRegion: in-place TTY status rows with append-only non-TTY fallback (#151)."""

import io

from watchdog.cmd.live import LiveRegion, _truncate


class _Stream(io.StringIO):
    """StringIO that can claim to be (or not be) a TTY."""
    def __init__(self, tty: bool):
        super().__init__()
        self._tty = tty
    def isatty(self) -> bool:
        return self._tty


# ── _truncate ──────────────────────────────────────────────────────────────────

def test_truncate_keeps_short_line_unchanged():
    assert _truncate("hello", 80) == "hello"


def test_truncate_clips_to_visible_width_with_ellipsis():
    out = _truncate("abcdefghij", 5)
    # width-1 visible chars, then ellipsis + reset
    assert out == "abcd…\x1b[0m"


def test_truncate_ignores_ansi_in_width_then_resets():
    colored = "\x1b[2mabcdefghij\x1b[0m"
    out = _truncate(colored, 5)
    assert out.startswith("\x1b[2m")          # opening code preserved
    assert "abcd" in out and "efgh" not in out  # only 4 visible chars kept
    assert out.endswith("\x1b[0m")


# ── non-TTY fallback ─────────────────────────────────────────────────────────────

def test_non_tty_appends_every_call_without_escapes():
    s = _Stream(tty=False)
    r = LiveRegion(s, enabled=None)
    assert r.enabled is False
    r.update("a", "  row-A-tty", "  classifying A")
    r.update("a", "  row-A-tty2", "  classified A")
    r.finish("a", "  OK A")
    r.note("  a note")
    out = s.getvalue()
    assert "\x1b[" not in out                  # no cursor escapes off a TTY
    assert out.splitlines() == ["  classifying A", "  classified A", "  OK A", "  a note"]


def test_non_tty_update_uses_tty_line_when_no_plain_given():
    s = _Stream(tty=False)
    r = LiveRegion(s, enabled=False)
    r.update("a", "  only-line")
    assert s.getvalue() == "  only-line\n"


# ── TTY in-place behavior ────────────────────────────────────────────────────────

def test_tty_first_update_writes_row_no_clear():
    s = _Stream(tty=True)
    r = LiveRegion(s, enabled=True)
    r.update("a", "row-A", "plain-A")
    out = s.getvalue()
    assert "row-A" in out
    assert "plain-A" not in out                # plain line is the non-TTY form only
    assert "\x1b[" not in out.split("row-A")[0]  # nothing to clear on the first draw


def test_tty_second_update_redraws_in_place():
    s = _Stream(tty=True)
    r = LiveRegion(s, enabled=True)
    r.update("a", "row-A1")
    r.update("a", "row-A2")
    out = s.getvalue()
    assert "\x1b[1A\x1b[J" in out              # moved up 1 line and cleared before redraw
    assert out.rstrip().endswith("row-A2")


def test_tty_finish_emits_permanent_line_and_drops_row():
    s = _Stream(tty=True)
    r = LiveRegion(s, enabled=True)
    r.update("a", "row-A")
    r.update("b", "row-B")
    r.finish("a", "OK A")
    out = s.getvalue()
    assert "OK A" in out
    assert "a" not in r._rows and "b" in r._rows  # only b remains live
    # after finishing, the live region should re-render just the surviving row
    assert out.rstrip().endswith("row-B")


def test_tty_note_prints_above_region():
    s = _Stream(tty=True)
    r = LiveRegion(s, enabled=True)
    r.update("a", "row-A")
    r.note("a scrollback note")
    out = s.getvalue()
    assert "a scrollback note" in out
    assert out.rstrip().endswith("row-A")      # region re-rendered below the note
    assert r._rendered == 1


def test_tty_truncates_rows_to_terminal_width(monkeypatch):
    monkeypatch.setattr("watchdog.cmd.live.shutil.get_terminal_size",
                        lambda fallback=(80, 24): __import__("os").terminal_size((10, 24)))
    s = _Stream(tty=True)
    r = LiveRegion(s, enabled=True)
    r.update("a", "0123456789ABCDEF")
    out = s.getvalue()
    assert "…\x1b[0m" in out                    # row was clipped to width
    assert "ABCDEF" not in out
