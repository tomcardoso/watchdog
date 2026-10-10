"""The reporter's Notes on saved pages survive a concurrent Ask Claude session write (D296).

A session reads a page, the reporter saves a note in the app, and the session writes the page
back with what it read: without the hooks, that write drops the note. Each test simulates one
interleaving of the app's save and a session's Write with the hook payloads Claude Code sends."""

import json
from pathlib import Path

import pytest

from watchdog.pipeline import page_notes

from tests.gui_support import call

pytest_plugins = ["tests.gui_support"]   # the rich_vault / wdg_home fixtures

PAGE = """---
title: Who paid
---

# Who paid

The harbour authority paid twice.

## Notes

<!-- Journalist annotations — never overwritten. -->
"""


@pytest.fixture
def page(rich_vault):
    p = rich_vault / "queries" / "who-paid.md"
    p.parent.mkdir(exist_ok=True)
    p.write_text(PAGE, encoding="utf-8")
    return p


def _payload(vault: Path, file: Path, content: str | None = None, session: str = "s1") -> dict:
    tool_input = {"file_path": str(file)}
    if content is not None:
        tool_input["content"] = content
    return {"session_id": session, "cwd": str(vault), "hook_event_name": "PreToolUse",
            "tool_name": "Write", "tool_input": tool_input}


def _session_write(vault, file, content, between=None) -> str | None:
    """A session's Write as Claude Code runs it: the PreToolUse hook, the write, then the
    PostToolUse hook. `between` runs after the first hook and before the write."""
    payload = _payload(vault, file, content)
    page_notes.before_edit(vault, payload)
    if between:
        between()
    file.write_text(content, encoding="utf-8")
    return page_notes.after_edit(vault, payload)


def _save(vault, text):
    assert call("vault.saveNotes", vault=str(vault), path="queries/who-paid", text=text) == {"ok": True}


def test_a_session_writing_back_a_stale_copy_does_not_lose_the_reporters_note(rich_vault, page):
    stale = page.read_text()                                   # the session reads the page
    _save(rich_vault, "Call the harbour authority's CFO.")     # the reporter saves a note in the app
    rewritten = stale.replace("paid twice.", "paid twice, in 2023 and 2024.")
    message = _session_write(rich_vault, page, rewritten)      # the session writes what it read

    text = page.read_text()
    assert "paid twice, in 2023 and 2024." in text, "the session's own change is kept"
    assert page_notes.section(text).strip() == "Call the harbour authority's CFO."
    assert message and "## Notes" in message


def test_an_app_save_between_the_hook_and_the_write_is_kept(rich_vault, page):
    stale = page.read_text()
    rewritten = stale.replace("paid twice.", "paid three times.")
    _session_write(rich_vault, page, rewritten,
                   between=lambda: _save(rich_vault, "Saved while Claude was writing."))
    text = page.read_text()
    assert "paid three times." in text
    assert page_notes.section(text).strip() == "Saved while Claude was writing."


def test_an_app_save_reads_a_session_write_that_lands_mid_save(rich_vault, page):
    """The compare-and-swap: a page that changed after the app read it is merged again."""
    raced = []

    def merge(old):
        if not raced:
            raced.append(True)
            page.write_text(old.replace("paid twice.", "paid twice (session)."), encoding="utf-8")
        from watchdog.gui.api.vault import replace_notes_body
        return replace_notes_body(old, "my note", "<!-- -->")

    assert page_notes.app_save(rich_vault, "queries/who-paid.md", page, merge)
    text = page.read_text()
    assert "paid twice (session)." in text and page_notes.section(text).strip() == "my note"


def test_a_session_edit_that_keeps_the_notes_changes_nothing(rich_vault, page):
    _save(rich_vault, "keep me")
    current = page.read_text()
    assert _session_write(rich_vault, page, current.replace("twice", "two times")) is None
    assert page_notes.section(page.read_text()).strip() == "keep me"


def test_notes_edited_outside_the_app_before_the_session_write_are_kept(rich_vault, page):
    _save(rich_vault, "old app note")
    stale = page.read_text()
    # The reporter edits the Notes in Obsidian, after the app save; the session read before that.
    page.write_text(page_notes.with_section(stale, "edited in Obsidian"), encoding="utf-8")
    _session_write(rich_vault, page, stale)
    assert page_notes.section(page.read_text()).strip() == "edited in Obsidian"


def test_a_new_page_or_a_page_outside_queries_and_wiki_is_left_alone(rich_vault, page):
    new = rich_vault / "wiki" / "new-thread.md"
    new.parent.mkdir(exist_ok=True)
    assert _session_write(rich_vault, new, "# New\n\n## Notes\n\n<!-- x -->\n") is None
    assert "<!-- x -->" in new.read_text()
    other = rich_vault / "scratch.md"
    other.write_text("## Notes\n\nmine\n")
    assert _session_write(rich_vault, other, "## Notes\n\nchanged\n") is None
    assert "changed" in other.read_text()


def test_the_sessions_hooks_run_end_to_end(rich_vault, page):
    """The app session's PreToolUse/PostToolUse callbacks (`gui/chat.py`, D299) with the payload
    Claude Code sends: the same `run_hook` the vault's command hooks ran."""
    import asyncio
    from watchdog.gui import chat
    stale = page.read_text()
    _save(rich_vault, "a note")
    payload = _payload(rich_vault, page, stale)
    pre, post = chat._page_notes("pre"), chat._page_notes("post")

    assert asyncio.run(pre(payload, "id", None)) == {}
    page.write_text(stale, encoding="utf-8")
    out = asyncio.run(post(payload, "id", None))
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "Notes" in out["hookSpecificOutput"]["additionalContext"]
    assert page_notes.section(page.read_text()).strip() == "a note"
    assert asyncio.run(post(payload, "id", None)) == {}, "a second post with no snapshot changes nothing"


def test_a_bad_payload_never_fails_the_hook():
    assert page_notes.run_hook("post", "not json") is None
    assert page_notes.run_hook("pre", json.dumps({"cwd": "/nonexistent"})) is None
