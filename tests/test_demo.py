"""Tests for `watchdog demo` — the bundled sample investigation used for onboarding and as
a live smoke test (#273). Chew and ingest are monkeypatched throughout: no real OCR or model
calls happen in this suite."""

import argparse
import json
from pathlib import Path

import pytest

import watchdog.cli as cli
import watchdog.cmd.base as _base
import watchdog.cmd.demo as demo
import watchdog.cmd.setup as _setup
import watchdog.pipeline.preprocess_batch as ppb
from watchdog.cmd import ingest as ing


# ── Fixtures (mirrors tests/test_cli.py's wdg_home/configured pattern) ─────────

@pytest.fixture
def wdg_home(tmp_path, monkeypatch):
    home = tmp_path / ".watchdog"
    home.mkdir()
    monkeypatch.setattr(_base, "WATCHDOG_HOME", home)
    monkeypatch.setattr(_base, "PROJECTS_FILE", home / "projects.json")
    monkeypatch.setattr(_base, "CONFIG_FILE", home / "config.json")
    monkeypatch.setattr(_setup, "WATCHDOG_HOME", home)
    monkeypatch.setattr(_setup, "CONFIG_FILE", home / "config.json")
    monkeypatch.setattr(cli, "CONFIG_FILE", home / "config.json")
    return home


@pytest.fixture
def configured(wdg_home, tmp_path, monkeypatch):
    investigations = tmp_path / "Investigations"
    investigations.mkdir()
    (wdg_home / "config.json").write_text(
        json.dumps({"projects_dir": str(investigations)}) + "\n"
    )
    monkeypatch.setattr("watchdog.cmd.vault._obsidian_config_path", lambda: tmp_path / "obsidian.json")
    return investigations


def _fake_run_ingest_queues_one(v, workers=None, chunk_workers=None, files=None, show_ingest_hint=True):
    """Stand-in for the real chew pipeline (Docling/OCR) — simulates one document landing
    in the queue, without touching any document-conversion code."""
    queue = Path(v) / ".watchdog" / "queue"
    queue.mkdir(parents=True, exist_ok=True)
    (queue / "demo-doc.json").write_text("{}")


# ── vault creation + file copy ──────────────────────────────────────────────────

def test_demo_creates_and_registers_vault(configured, monkeypatch):
    monkeypatch.chdir(configured)
    monkeypatch.setattr(ppb, "run_ingest", lambda *a, **k: None)

    demo.cmd_demo(argparse.Namespace())

    projects = _base.load_projects()
    assert demo._DEMO_SLUG in projects
    assert projects[demo._DEMO_SLUG]["name"] == demo._DEMO_NAME
    vault = Path(projects[demo._DEMO_SLUG]["path"])
    assert vault.is_dir()
    assert (vault / ".watchdog").is_dir()


def test_demo_copies_documents_and_sidecars_but_not_provenance(configured, monkeypatch):
    monkeypatch.chdir(configured)
    monkeypatch.setattr(ppb, "run_ingest", lambda *a, **k: None)

    demo.cmd_demo(argparse.Namespace())

    vault = Path(_base.load_projects()[demo._DEMO_SLUG]["path"])
    incoming = vault / "_INCOMING"
    for name in demo._DEMO_FILES:
        assert (incoming / name).is_file(), f"missing {name}"
    assert not (incoming / "PROVENANCE.md").exists()
    # exactly the four demo files, nothing extra
    assert {p.name for p in incoming.iterdir()} == set(demo._DEMO_FILES)


def test_demo_chdirs_into_the_vault_before_chewing(configured, monkeypatch):
    monkeypatch.chdir(configured)
    seen_cwd = {}

    def fake_run_ingest(v, workers=None, chunk_workers=None, files=None, show_ingest_hint=True):
        seen_cwd["cwd"] = Path(".").resolve()

    monkeypatch.setattr(ppb, "run_ingest", fake_run_ingest)
    demo.cmd_demo(argparse.Namespace())

    vault = Path(_base.load_projects()[demo._DEMO_SLUG]["path"])
    assert seen_cwd["cwd"] == vault


# ── already exists ───────────────────────────────────────────────────────────

def test_demo_already_exists_is_graceful(configured, monkeypatch, capsys):
    monkeypatch.chdir(configured)
    cli.cmd_new(argparse.Namespace(name=demo._DEMO_NAME, name_flag=None, description=None, dir=None))

    def _boom(*a, **k):
        raise AssertionError("must not chew when the demo vault already exists")
    monkeypatch.setattr(ppb, "run_ingest", _boom)

    demo.cmd_demo(argparse.Namespace())

    out = capsys.readouterr().out
    assert "already exists" in out
    assert "watchdog status watchdog-demo" in out
    assert "watchdog delete watchdog-demo" in out


# ── chew/ingest reuse + the shared post-chew prompt ─────────────────────────────

def test_demo_invokes_chew(configured, monkeypatch):
    monkeypatch.chdir(configured)
    calls = []

    def fake_run_ingest(v, workers=None, chunk_workers=None, files=None, show_ingest_hint=True):
        calls.append(v)

    monkeypatch.setattr(ppb, "run_ingest", fake_run_ingest)
    demo.cmd_demo(argparse.Namespace())
    assert len(calls) == 1


def test_demo_declining_ingest_prints_hint_and_skips(configured, monkeypatch, capsys):
    monkeypatch.chdir(configured)
    monkeypatch.setattr(ppb, "run_ingest", _fake_run_ingest_queues_one)
    monkeypatch.setattr(ing, "_notify", lambda *a, **k: None)
    monkeypatch.setattr("builtins.input", lambda *a: "2")  # pick(): "Not now"

    def _boom(*a, **k):
        raise AssertionError("ingest must not run when the prompt is declined")
    monkeypatch.setattr(ing, "cmd_ingest", _boom)

    demo.cmd_demo(argparse.Namespace())

    assert "watchdog ingest" in capsys.readouterr().out


def test_demo_accepting_ingest_runs_it(configured, monkeypatch):
    monkeypatch.chdir(configured)
    monkeypatch.setattr(ppb, "run_ingest", _fake_run_ingest_queues_one)
    monkeypatch.setattr(ing, "_notify", lambda *a, **k: None)
    monkeypatch.setattr("builtins.input", lambda *a: "1")  # pick(): "Ingest now"

    seen = {}
    monkeypatch.setattr(
        ing, "cmd_ingest",
        lambda a, *, confirm=True, skip_preview=False: seen.update(confirm=confirm, skip_preview=skip_preview),
    )

    demo.cmd_demo(argparse.Namespace())

    assert seen == {"confirm": False, "skip_preview": True}
