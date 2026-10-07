"""Structured progress lines (`watchdog.progress`): written only when the app asks for them."""

import asyncio
import io
import json

from watchdog import progress
from watchdog.pipeline import orchestrate

from tests.test_orchestrate import _extraction, _mock, _queue_doc
from tests.test_write_vault import make_vault


def _capture(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr("sys.__stdout__", buf)
    return buf


def _events(buf) -> list[dict]:
    return [e for e in (progress.parse(ln) for ln in buf.getvalue().split("\n")) if e]


def test_nothing_is_written_when_unset(monkeypatch):
    monkeypatch.delenv("WATCHDOG_PROGRESS", raising=False)
    buf = _capture(monkeypatch)
    progress.emit("stage", stage="dig", done=0, total=1)
    assert buf.getvalue() == ""


def test_other_values_do_not_enable(monkeypatch):
    monkeypatch.setenv("WATCHDOG_PROGRESS", "0")
    buf = _capture(monkeypatch)
    progress.emit("message", text="hi")
    assert buf.getvalue() == ""


def test_line_format_and_parse(monkeypatch):
    monkeypatch.setenv("WATCHDOG_PROGRESS", "1")
    buf = _capture(monkeypatch)
    progress.emit("message", text="café")
    line = buf.getvalue()
    assert line.startswith("\x1eWDP ") and line.endswith("\n")
    assert json.loads(line[len("\x1eWDP "):]) == {"kind": "message", "text": "café"}
    assert progress.parse(line.rstrip("\n")) == {"kind": "message", "text": "café"}
    assert progress.parse("plain output") is None
    assert progress.parse("\x1eWDP not json") is None


def test_run_emits_doc_and_stage_events(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHDOG_PROGRESS", "1")
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    _mock(monkeypatch, extraction=_extraction())
    buf = _capture(monkeypatch)
    asyncio.run(orchestrate.run(vault))
    events = _events(buf)
    states = [e["state"] for e in events if e["kind"] == "doc"]
    assert states[:2] == ["started", "classified"] and states[-1] == "done"
    done = [e for e in events if e["kind"] == "doc" and e["state"] == "done"][0]
    assert done["detail"] == "1 facts" and done["filename"] == "test-doc.pdf"
    stages = [e["stage"] for e in events if e["kind"] == "stage"]
    for stage in ("dig", "fold", "commit", "contradictions", "synthesis", "timeline", "briefing",
                  "leads", "requests", "done"):
        assert stage in stages, stage
    assert stages[0] == "dig"
    assert [e for e in events if e["kind"] == "stage" and e["stage"] == "dig"][0]["total"] == 1


def test_run_output_unchanged_without_the_variable(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("WATCHDOG_PROGRESS", raising=False)
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    _mock(monkeypatch, extraction=_extraction())
    asyncio.run(orchestrate.run(vault))
    assert "\x1e" not in capsys.readouterr().out
