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


# ── where the warning says the text goes ─────────────────────────────────────

def test_the_warning_names_a_cloud_provider(monkeypatch):
    w = ing._public_records_warning(2, ["claude-agent-sdk", "openai", None])
    assert "cloud AI models (Anthropic and OpenAI)" in w and "cannot be undone" in w
    assert "2 documents will be sent" in w


def test_the_warning_for_a_server_on_this_computer_does_not_say_cloud(monkeypatch):
    monkeypatch.setenv("LOCAL_BASE_URL", "http://localhost:11434/v1")
    w = ing._public_records_warning(1, ["local", "local", "local"])
    assert "cloud AI model" not in w
    assert "the model server on this computer (localhost:11434)" in w
    assert "Public records only" in w and "public, or presumptively public" in w


def test_the_warning_for_a_server_elsewhere_names_its_address(monkeypatch):
    monkeypatch.setenv("LOCAL_BASE_URL", "http://192.168.1.20:8000/v1")
    d = ing.send_destination(["local"])
    assert d["local"] == "the model server at 192.168.1.20:8000" and not d["on_this_computer"]
    assert "cannot be undone" in ing._public_records_warning(1, ["local"])


def test_a_mixed_run_names_both_destinations(monkeypatch):
    monkeypatch.setenv("LOCAL_BASE_URL", "http://127.0.0.1:1234/v1")
    d = ing.send_destination(["local", "gemini", "local"])
    assert d["short"] == "a cloud AI model (Google) and the model server on this computer (127.0.0.1:1234)"
    assert not d["on_this_computer"]


@pytest.mark.parametrize("host, loopback", [
    ("localhost", True), ("LOCALHOST", True), ("127.0.0.1", True), ("127.8.9.1", True),
    ("::1", True), ("0.0.0.0", True), ("192.168.1.20", False), ("10.0.0.2", False),
    ("127.0.0.1.example.com", False), ("127.attacker.net", False), ("", False),
])
def test_only_a_loopback_address_counts_as_this_computer(host, loopback):
    assert ing._is_loopback(host) is loopback


def test_a_run_on_this_computer_passes_the_gate_and_says_so():
    rep = CollectingReporter()
    with using(rep):
        assert ing._confirm_public_records(2, local_only="Processing runs on x.") is True
    assert rep.text.strip() == "Processing runs on x."


@pytest.mark.parametrize("url, backends, note", [
    ("http://localhost:11434/v1", ["local", "local", "local"],
     "Processing runs on the model server on this computer (localhost:11434); nothing is sent "
     "to a cloud service."),
    ("http://[::1]:8080/v1", ["local"],
     "Processing runs on the model server on this computer ([::1]:8080); nothing is sent to a "
     "cloud service."),
    ("http://192.168.1.20:8000/v1", ["local"], None),
    ("http://localhost:11434/v1", ["local", "openai", "local"], None),
    ("http://localhost:11434/v1", ["local", None], None),    # a bare Claude tier is the cloud
    (None, ["claude-api"], None),
])
def test_local_only_note(monkeypatch, url, backends, note):
    if url:
        monkeypatch.setenv("LOCAL_BASE_URL", url)
    else:
        monkeypatch.delenv("LOCAL_BASE_URL", raising=False)
    assert ing.local_only_note(backends) == note


def test_an_acknowledged_run_says_where_it_sends():
    rep = CollectingReporter()
    with using(rep):
        ing._confirm_public_records(2, skip_warning=True, destination="the model server at x")
    assert "Sending 2 documents to the model server at x." in rep.text


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


def test_the_add_operation_honours_the_limit_the_preflight_prices(vault, monkeypatch):
    """The Add dialog's Limit was counted and priced by the preflight but dropped by the `add`
    operation, which then sent the whole queue (C2). It now reaches the run."""
    from watchdog import ops
    from watchdog.gui.api.ingest import run_options
    seen = []
    monkeypatch.setattr(ing, "preprocess", lambda *a, **k: None)
    monkeypatch.setattr(ing, "process", lambda _v, opts, *, finalize: seen.append(opts) or {})
    (vault / ".watchdog" / "registry").mkdir(parents=True)
    assert ops.validate("add", {"limit": 5, "skip_warning": True}) == {"limit": 5, "skip_warning": True}
    ops.run("add", {"limit": 5, "skip_warning": True}, CollectingReporter(), vault)
    assert seen[0].limit == 5 == run_options({"limit": 5}).limit


def test_a_limited_run_sends_only_the_next_n_queued_documents(vault):
    """The run's own selection (`limit_queue`) is the preflight's: the first N still to send."""
    from watchdog.pipeline.ingest_setup import limit_queue, needs_extraction
    queue = [{"sha256": c * 64} for c in "abcdef"]
    (vault / ".watchdog" / "extracted").mkdir(parents=True)
    (vault / ".watchdog" / "extracted" / f"{'a' * 64}.json").write_text("{}")
    picked = limit_queue(vault, queue, 2)
    assert [q["sha256"][0] for q in picked] == ["b", "c"]
    assert picked == needs_extraction(vault, queue)[:2]
