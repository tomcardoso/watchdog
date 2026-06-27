"""Bare `watchdog` guided driver (#132): offer context → chew → ingest by pending state."""

import argparse

import pytest

import watchdog.cmd.ingest as ing


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """A minimal vault with the dirs the guided driver inspects, set as cwd."""
    v = tmp_path / "probe"
    (v / ".watchdog" / "queue").mkdir(parents=True)
    (v / "_CONTEXT").mkdir()
    (v / "_INCOMING").mkdir()
    monkeypatch.chdir(v)
    monkeypatch.setattr(ing, "load_projects", lambda: {})
    return v


@pytest.fixture
def spies(monkeypatch):
    """Replace the three stage helpers with call recorders. (cmd_context offers the Claude
    Code launch for the context stage; declining there returns and falls through to chew.)"""
    calls = []
    monkeypatch.setattr(ing, "cmd_context",
                        lambda *a, **k: calls.append("context"))
    monkeypatch.setattr(ing, "_run_preprocess",
                        lambda *a, **k: calls.append("chew"))
    monkeypatch.setattr(ing, "_offer_ingest",
                        lambda *a, **k: calls.append("ingest"))
    return calls


def _queue(v, n=1):
    for i in range(n):
        (v / ".watchdog" / "queue" / f"{i:064d}.json").write_text("{}")


def test_nothing_pending_offers_nothing(vault, spies, capsys):
    ing.cmd_guided(argparse.Namespace())
    assert spies == []
    assert "Nothing pending" in capsys.readouterr().out


def test_context_offered_when_context_dir_has_files_and_no_context_md(vault, spies):
    (vault / "_CONTEXT" / "prior-story.txt").write_text("x")
    ing.cmd_guided(argparse.Namespace())
    assert spies == ["context"]


def test_context_skipped_when_context_md_already_exists(vault, spies):
    (vault / "_CONTEXT" / "prior-story.txt").write_text("x")
    (vault / "context.md").write_text("# seeded")
    ing.cmd_guided(argparse.Namespace())
    assert "context" not in spies


def test_chew_offered_when_incoming_has_files(vault, spies):
    (vault / "_INCOMING" / "doc.pdf").write_text("x")
    ing.cmd_guided(argparse.Namespace())
    assert spies == ["chew"]


def test_ingest_offered_when_queue_non_empty(vault, spies):
    _queue(vault)
    ing.cmd_guided(argparse.Namespace())
    assert spies == ["ingest"]


def test_full_pipeline_offers_all_stages_in_order(vault, spies):
    (vault / "_CONTEXT" / "prior.txt").write_text("x")
    (vault / "_INCOMING" / "doc.pdf").write_text("x")
    _queue(vault)
    ing.cmd_guided(argparse.Namespace())
    assert spies == ["context", "chew", "ingest"]
