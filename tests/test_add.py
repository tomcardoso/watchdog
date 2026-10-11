"""Adding documents (`ops.ingest.add_documents`, D251), auto-approve (D263), and the Overview's
summary of what is waiting."""

import json

import pytest

import watchdog.ops.ingest as ing
from watchdog import overview
from watchdog.ops import CollectingReporter, using


# ── auto-approve gate (D263) ─────────────────────────────────────────────────

@pytest.mark.parametrize("config, expected", [
    ({}, False), ({"auto_approve": False}, False), ({"auto_approve": "true"}, False),
    ({"auto_approve_usd": 5}, False), ({"auto_approve": True}, True),
])
def test_auto_approve_is_on_only_when_explicitly_true(config, expected):
    assert ing._auto_approve_on(config) is expected


def test_an_approved_run_goes_ahead_without_the_acknowledgement():
    rep = CollectingReporter()
    with using(rep):
        assert ing._confirm_public_records(3, gate={"approve": True}, enabled=True) is True
    assert "Auto-approved" in rep.text and "subscription" in rep.text and "3 documents" in rep.text


@pytest.mark.parametrize("gate, reason", [
    ({"blocker": "a paid key is involved"}, "a paid key is involved"),
    (None, "could not be checked"),
    # a raw cost_estimate dict can never approve: its keys are not the gate's
    ({"cost_high": 0.01, "subscription": True}, "could not be checked"),
])
def test_an_unapproved_run_says_why_and_sends_nothing_without_the_acknowledgement(gate, reason):
    rep = CollectingReporter()
    with using(rep):
        assert ing._confirm_public_records(2, gate=gate, enabled=True) is False
    assert reason in rep.text and "Nothing was sent" in rep.text


def test_an_unapproved_run_goes_ahead_once_acknowledged():
    rep = CollectingReporter()
    with using(rep):
        assert ing._confirm_public_records(2, gate={"blocker": "x"}, enabled=True,
                                           skip_warning=True) is True
    assert "Sending 2 documents" in rep.text


def test_with_auto_approve_off_an_approving_verdict_still_needs_the_acknowledgement():
    rep = CollectingReporter()
    with using(rep):
        assert ing._confirm_public_records(2, gate={"approve": True}, enabled=False) is False
    assert "auto-approve" not in rep.text.lower()


def test_a_run_that_sends_nothing_passes_the_gate_silently():
    rep = CollectingReporter()
    with using(rep):
        assert ing._confirm_public_records(0) is True
    assert rep.lines == []


# ── adding documents ─────────────────────────────────────────────────────────

@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "probe"
    (v / ".watchdog" / "queue").mkdir(parents=True)
    (v / "incoming").mkdir()
    monkeypatch.setattr("watchdog.pipeline.research.pending_count", lambda vault: 0)
    return v


def _spy_add_stages(monkeypatch):
    calls = []
    monkeypatch.setattr(ing, "preprocess", lambda vault, *a, **k: calls.append("chew"))
    monkeypatch.setattr(ing, "process", lambda _v, opts, *, finalize: calls.append(("process", finalize)) or {})
    return calls


def test_add_copies_files_and_folders_then_runs_the_pipeline(vault, tmp_path, monkeypatch):
    calls = _spy_add_stages(monkeypatch)
    src = tmp_path / "downloads"
    (src / "sub").mkdir(parents=True)
    (src / "a.pdf").write_bytes(b"%PDF-1.4")
    (src / "a.pdf.yml").write_text("source: x\n")
    (src / "sub" / "b.txt").write_text("hello")
    single = tmp_path / "c.txt"
    single.write_text("one")

    rep = CollectingReporter()
    with using(rep):
        ing.add_documents(vault, [str(src), str(single)])

    incoming = sorted(p.name for p in (vault / "incoming").iterdir())
    assert incoming == ["a.pdf", "a.pdf.yml", "b.txt", "c.txt"]
    assert (src / "a.pdf").exists() and single.exists()          # originals stay put
    assert calls == ["chew", ("process", True)]
    assert "Copied 3 files" in rep.text


def test_add_with_nothing_incoming_skips_preprocessing(vault, monkeypatch):
    calls = _spy_add_stages(monkeypatch)
    ing.add_documents(vault, [])
    assert calls == [("process", True)]


def test_add_retry_requeues_failed_documents_first(vault, monkeypatch):
    _spy_add_stages(monkeypatch)
    failed = vault / ".watchdog" / "queue" / "_failed"
    failed.mkdir()
    (failed / f"{'a' * 64}.json").write_text("{}")
    ing.add_documents(vault, [], retry=True)
    assert (vault / ".watchdog" / "queue" / f"{'a' * 64}.json").exists()


def test_add_missing_path_exits(vault):
    with pytest.raises(SystemExit, match="not found"):
        ing.add_documents(vault, ["/no/such/file.pdf"])


def test_add_operation_outside_a_vault_exits(tmp_path):
    with pytest.raises(SystemExit, match="not a Watchdog investigation"):
        ing.add(CollectingReporter(), tmp_path)


# ── the Overview's summary ───────────────────────────────────────────────────

def test_overview_of_a_fresh_vault_has_nothing_waiting(vault):
    s = overview.summary(vault)
    assert not overview.has_work(s)
    assert s["briefing"] is None and s["headline"] is None


def test_overview_reports_briefing_waiting_items_and_work(vault):
    b = vault / "briefings"
    b.mkdir()
    (b / "2026-10-01-09-00.md").write_text("old")
    (b / "2026-10-03-16-12.md").write_text("new")
    (b / "leads-2026-10-03.md").write_text("not an ingest briefing")
    (b / "alerts-2026-10-03.md").write_text(
        "- [ ] **x** <!--wid:alert:abc1234:t1-->\n- [ ] **y** <!--wid:alert:def5678:t2-->\n")
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

    s = overview.summary(vault)
    assert s["briefing"].name == "2026-10-03-16-12.md"
    assert s["headline"] == "Contract went to a new firm [[documents/d#^f-abc|p. 2]]."
    assert (s["contradictions"], s["leads"], s["near_duplicates"], s["alerts"]) == (1, 1, 1, 1)
    assert s["incoming"] == 1
    assert overview.has_work(s)
