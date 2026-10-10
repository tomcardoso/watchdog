"""`watchdog add`, auto-approve (D263), and the bare-`watchdog` home screen (D251)."""

import watchdog.cmd.ingest as cmd_ing
import argparse
import json
import re

import pytest

import watchdog.cmd.home as home
import watchdog.ops.ingest as ing

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(s: str) -> str:
    return _ANSI.sub("", s)


def _no_prompt(*a, **k):
    raise AssertionError("prompted when it should not have")


# ── auto-approve gate (D263) ─────────────────────────────────────────────────

@pytest.mark.parametrize("config, expected", [
    ({}, False), ({"auto_approve": False}, False), ({"auto_approve": "true"}, False),
    ({"auto_approve_usd": 5}, False), ({"auto_approve": True}, True),
])
def test_auto_approve_is_on_only_when_explicitly_true(config, expected):
    assert ing._auto_approve_on(config) is expected


def test_an_approved_run_goes_ahead_without_asking(monkeypatch, capsys):
    monkeypatch.setattr(ing.interactive, "pick", _no_prompt)
    assert ing._confirm_public_records(3, gate={"approve": True}, enabled=True) is True
    out = _plain(capsys.readouterr().out)
    assert "Auto-approved" in out and "subscription" in out and "3 documents" in out


@pytest.mark.parametrize("gate, reason", [
    ({"blocker": "a paid key is involved"}, "a paid key is involved"),
    (None, "could not be checked"),
    # a raw cost_estimate dict can never approve: its keys are not the gate's
    ({"cost_high": 0.01, "subscription": True}, "could not be checked"),
])
def test_an_unapproved_run_says_why_and_asks(monkeypatch, capsys, gate, reason):
    asked = []
    monkeypatch.setattr(ing.interactive, "pick", lambda *a, **k: asked.append(1) or 0)
    assert ing._confirm_public_records(2, gate=gate, enabled=True) is True
    assert asked
    out = _plain(capsys.readouterr().out)
    assert reason in out and "Public records only" in out


def test_with_auto_approve_off_an_approving_verdict_still_asks(monkeypatch, capsys):
    asked = []
    monkeypatch.setattr(ing.interactive, "pick", lambda *a, **k: asked.append(1) or 1)
    assert ing._confirm_public_records(2, gate={"approve": True}, enabled=False) is False
    assert asked
    assert "auto-approve" not in _plain(capsys.readouterr().out).lower()


# ── watchdog add ─────────────────────────────────────────────────────────────

@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "probe"
    (v / ".watchdog" / "queue").mkdir(parents=True)
    (v / "incoming").mkdir()
    monkeypatch.chdir(v)
    monkeypatch.setattr("watchdog.pipeline.research.pending_count", lambda vault: 0)
    return v


def _spy_add_stages(monkeypatch):
    calls = []
    monkeypatch.setattr(ing, "_run_preprocess", lambda vault, **k: calls.append("chew"))
    monkeypatch.setattr(ing, "_ingest", lambda a, _v, **k: calls.append(("ingest", a.command)) or {})
    return calls


def test_add_copies_files_and_folders_then_runs_the_pipeline(vault, tmp_path, monkeypatch, capsys):
    calls = _spy_add_stages(monkeypatch)
    src = tmp_path / "downloads"
    (src / "sub").mkdir(parents=True)
    (src / "a.pdf").write_bytes(b"%PDF-1.4")
    (src / "a.pdf.yml").write_text("source: x\n")
    (src / "sub" / "b.txt").write_text("hello")
    single = tmp_path / "c.txt"
    single.write_text("one")

    cmd_ing.cmd_add(argparse.Namespace(paths=[str(src), str(single)], retry=False))

    incoming = sorted(p.name for p in (vault / "incoming").iterdir())
    assert incoming == ["a.pdf", "a.pdf.yml", "b.txt", "c.txt"]
    assert (src / "a.pdf").exists() and single.exists()          # originals stay put
    assert calls == ["chew", ("ingest", "add")]
    assert "Copied 3 files" in _plain(capsys.readouterr().out)


def test_add_with_nothing_incoming_skips_chew(vault, monkeypatch):
    calls = _spy_add_stages(monkeypatch)
    cmd_ing.cmd_add(argparse.Namespace(paths=[], retry=False))
    assert calls == [("ingest", "add")]


def test_add_retry_requeues_failed_documents_first(vault, monkeypatch):
    _spy_add_stages(monkeypatch)
    failed = vault / ".watchdog" / "queue" / "_failed"
    failed.mkdir()
    (failed / f"{'a' * 64}.json").write_text("{}")
    cmd_ing.cmd_add(argparse.Namespace(paths=[], retry=True))
    assert (vault / ".watchdog" / "queue" / f"{'a' * 64}.json").exists()


def test_add_missing_path_exits(vault):
    with pytest.raises(SystemExit, match="not found"):
        cmd_ing.cmd_add(argparse.Namespace(paths=["/no/such/file.pdf"], retry=False))


def test_add_outside_a_vault_exits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="not inside a Watchdog project"):
        cmd_ing.cmd_add(argparse.Namespace(paths=[], retry=False))


# ── home screen ──────────────────────────────────────────────────────────────

def test_home_on_a_fresh_vault_has_nothing_waiting(vault):
    s = home.summary(vault)
    assert not home.has_work(s)
    out = _plain(home.render("Probe", s))
    assert "No briefing yet" in out and "Waiting on you" not in out


def test_home_reports_briefing_waiting_items_and_work(vault):
    b = vault / "briefings"
    b.mkdir()
    (b / "2026-10-01-09-00.md").write_text("old")
    (b / "2026-10-03-16-12.md").write_text("new")
    (b / "leads-2026-10-03.md").write_text("not an ingest briefing")
    (b / "alerts-2026-10-03.md").write_text(
        "- [ ] **x** <!--wid:alert:abc1234:t1-->\n- [ ] **y** <!--wid:alert:def5678:t2-->\n")
    # A retired hot.md (D285) is never read: the headline comes from the latest briefing's sidecar.
    (vault / "hot.md").write_text("# Hot cache\n\n## Investigation status\n\nAn old hot.md line.\n")
    side = vault / ".watchdog" / "briefings"
    side.mkdir(parents=True)
    (side / "2026-10-01-09-00.json").write_text(json.dumps({"status": "An older status."}))
    (side / "2026-10-03-16-12.json").write_text(json.dumps(
        {"status": "Contract went to a new firm [[documents/d#^f-abc|p. 2]]."}))
    reg = vault / ".watchdog" / "registry"
    reg.mkdir()
    (reg / "resolutions.json").write_text(json.dumps({"resolved": {"alert:def5678:t2": {}}}))
    (reg / "documents.json").write_text(json.dumps({"s1": {"near_duplicate_of": "s2"}, "s2": {}}))
    (reg / "entities.json").write_text(json.dumps({
        "acme": {"name": "Acme", "roles": [{"target_id": "ghost", "target_name": "Ghost Co"}],
                 "contradictions": ["> [!contradiction] date\n> a vs b"], "appears_in": ["s1"]}}))
    (vault / "incoming" / "new.pdf").write_bytes(b"x")

    s = home.summary(vault)
    assert s["briefing"].name == "2026-10-03-16-12.md"
    assert s["headline"] == "Contract went to a new firm [[documents/d#^f-abc|p. 2]]."
    assert (s["contradictions"], s["leads"], s["near_duplicates"], s["alerts"]) == (1, 1, 1, 1)
    assert home.has_work(s)
    out = _plain(home.render("Probe", s))
    assert "old hot.md" not in out and "[[" not in out
    for text in ("Contract went to a new firm (p. 2).", "contradiction", "open lead",
                 "possible duplicate document", "watch-list hit", "file in incoming/",
                 "watchdog add", 'watchdog ask "…"'):
        assert text in out


def test_home_offers_add_only_when_there_is_work(vault, monkeypatch):
    import sys
    monkeypatch.setattr(home, "load_projects", lambda: {})
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(home.interactive, "confirm", _no_prompt)
    assert home.cmd_home(argparse.Namespace()) is None          # nothing waiting: no prompt

    (vault / "incoming" / "new.pdf").write_bytes(b"x")
    monkeypatch.setattr(home.interactive, "confirm", lambda *a, **k: False)
    assert home.cmd_home(argparse.Namespace()) is None          # declined
