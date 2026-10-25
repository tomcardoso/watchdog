"""Bringing an investigation up to date (D336): the registry of migrations, the stamp, each first
migration on an investigation built the old way, resuming after a stop, and running twice. Every
name here is invented."""

import json
import re
from pathlib import Path

import pytest

from tests.gui_support import call, call_error, wdg_home  # noqa: F401  (fixture)
from tests.test_orchestrate import _stage_extracted
from tests.test_write_vault import make_vault
from watchdog import model_client
from watchdog.pipeline import (dates, entity_facts, history, merge_log, migrations, orchestrate,
                               verification)
from watchdog.vault_paths import processing_lock

REG = Path(".watchdog") / "registry"


def _ent(eid, name, typ, aliases=(), roles=()):
    return {"id": eid, "name": name, "type": typ, "aliases": list(aliases), "roles": list(roles),
            "timeline_events": []}


def _fact(text, tags, page=2, date=None):
    f = {"fact": text, "page": page, "basis": "stated", "entities": list(tags)}
    if date is not None:
        f["date"] = date
    return f


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _art(vault, sha):
    return _json(vault / ".watchdog" / "extracted" / f"{sha}.json")


def _reg(vault):
    return _json(vault / REG / "entities.json")


def old_vault(tmp_path):
    """An investigation committed before D329–D336: dates the old parser erased (""), a fact tag
    that is a near miss and one that needs judgement, relationship targets by unknown ids, a
    one-page text whose facts cite page 3, and two records with the same generic name."""
    vault = make_vault(tmp_path)
    _stage_extracted(vault, tmp_path / "a", "sha-a", "lease.pdf", overrides={
        "entities": [
            _ent("acme-corp", "Acme Corp", "organization", roles=[
                {"relationship": "tenant of", "target_id": "Fenmoor Harbour Board"},   # exact
                {"relationship": "audited by", "target_id": "brightwater-aud"}]),      # model
            _ent("fenmoor-harbour-board", "Fenmoor Harbour Board", "public-body", ["FHB"]),
            _ent("brightwater-audit-llp", "Brightwater Audit LLP", "organization"),
            _ent("city-council", "City Council", "public-body")],
        "morgue_entity_id": "acme-corp", "morgue_document_type": "lease",
        "document": {"page_count": 4, "date_of_document": "2021-06-01", "key_facts": [
            _fact("Acme Corp signed the lease on 3 March 2019.", ["acme corp"], date=""),
            _fact("Acme Corp paid rent to the board in 2019 and 2020.", ["acme-corp", "harbour"], date=""),
            _fact("Acme Corp was founded by its partners.", ["acme-corp"], date=""),
            _fact("The board met on 4 April 2020.", ["fenmoor-harbour-board"], date="2020-04-04"),
            _fact("The City Council approved the lease.", ["city-council"])]}})
    _stage_extracted(vault, tmp_path / "b", "sha-b", "minutes.txt", overrides={
        "entities": [_ent("city-council", "City Council", "public-body"),
                     _ent("acme-corp", "Acme Corp", "organization")],
        "morgue_entity_id": "city-council", "morgue_document_type": "minutes",
        "document": {"page_count": 1, "date_of_document": "2022-02-01", "key_facts": [
            _fact("The City Council heard Acme Corp's request.", ["city-council", "acme-corp"], page=3),
            _fact("The City Council deferred the vote.", ["city-council"], page=1)]}})
    orchestrate._batch_exact_fold(vault, ["sha-a", "sha-b"])
    orchestrate._commit_pending(vault, ["sha-a", "sha-b"])
    docs = _json(vault / REG / "documents.json")
    md = vault / Path(docs["sha-b"]["morgue_path"]).with_suffix(".md")
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text("<!-- PAGE 1 -->\n\nThe City Council heard Acme Corp's request for a longer lease. "
                  "The City Council deferred the vote.\n", encoding="utf-8")
    # Built before the stamp existed.
    migrations.stamp_path(vault).unlink()
    entity_facts.clear_cache()
    return vault


def _result(parsed):
    return model_client.ModelResult(parsed=parsed, text="", model="fake", backend="claude-api",
                                    auth_mode="api-key", usage={"input_tokens": 100, "output_tokens": 10},
                                    cost_usd=0.01, latency_s=0.0)


class Fake:
    """Every model call of the migrations, answered by task."""

    def __init__(self):
        self.tasks = []

    async def __call__(self, *, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        self.tasks.append(task)
        if task == "date-resolve":
            n = re.findall(r"^(\d+)\. printed date: none kept", prompt, re.M)
            return _result({"answers": [{"item": int(n[0]), "start": "2019", "end": "2020"}]})
        if task == "fact-tags":
            return _result({"answers": [{"item": 1, "match": "fenmoor-harbour-board"}]})
        if task == "role-targets":
            return _result({"answers": [{"item": 1, "match": "brightwater-audit-llp",
                                         "kind": "organization"}]})
        if task == "reconcile":
            return _result({"merges": [], "contradictions": [], "groups": [
                {"group": 0, "same": [{"members": [0, 1], "keep": 0, "reason": "one council"}],
                 "unsure": []}]})
        return _result({"answers": []})


@pytest.fixture
def fake(monkeypatch, wdg_home):  # noqa: F811
    f = Fake()
    monkeypatch.setattr(model_client, "acomplete_json", f)
    monkeypatch.setattr("watchdog.pipeline.entity_notes.index_notes", lambda *a, **k: None)
    monkeypatch.setattr("watchdog.gui.vaultio.require_granted", lambda p: None)
    return f


def _run(vault):
    return migrations.run(vault, say=lambda m: None, warn=lambda m: None, check_keys=False)


# ── the registry and the stamp ───────────────────────────────────────────────────────────

def test_every_migration_is_registered_once_with_what_the_app_shows():
    ids = [m.id for m in migrations.MIGRATIONS]
    assert len(ids) == len(set(ids)) and ids
    for m in migrations.MIGRATIONS:
        assert m.title and m.description and callable(m.estimate) and callable(m.apply)
        assert m.calls_model == (m.stage is not None)


def test_a_new_investigation_is_stamped_current(tmp_path, wdg_home):  # noqa: F811
    from watchdog.ops import projects as projects_op
    vault = projects_op.create("Harbour lease", parent=tmp_path)
    assert set(migrations.load(vault)["applied"]) == {m.id for m in migrations.MIGRATIONS}
    assert migrations.pending(vault) == []


def test_an_old_investigation_without_documents_is_stamped_at_its_first_commit(tmp_path):
    vault = make_vault(tmp_path)
    _stage_extracted(vault, tmp_path / "a", "sha-a", "a.pdf")
    orchestrate._commit_pending(vault)
    assert migrations.stamp_path(vault).exists() and migrations.pending(vault) == []


def test_an_old_investigation_has_every_step_pending_and_a_banner(tmp_path):
    vault = old_vault(tmp_path)
    st = migrations.status(vault)
    assert [s["id"] for s in st["pending"]] == [m.id for m in migrations.MIGRATIONS]
    assert st["banner"] is True
    migrations.dismiss_banner(vault)
    st = migrations.status(vault)
    assert st["banner"] is False and st["pending"]          # dismissed once, still in Maintenance


# ── the plan ─────────────────────────────────────────────────────────────────────────────

def test_the_plan_counts_each_step_without_writing(tmp_path, fake):
    vault = old_vault(tmp_path)
    before = {p.name: p.read_bytes() for p in (vault / ".watchdog" / "extracted").iterdir()}
    p = migrations.plan(vault)
    steps = {s["id"]: s for s in p["steps"]}
    assert steps["2026-10-free-fixes"]["calls"] == 0 and steps["2026-10-free-fixes"]["items"] == 2
    d = steps["2026-10-erased-dates"]
    assert d["items"] == 2 and d["calls"] == 1 and "1 date read by code" in d["detail"]
    assert "1 fact naming no year" in d["detail"]
    assert steps["2026-10-same-name-groups"]["calls"] == 1
    assert p["calls"] >= 3 and p["est_tokens"] > 0
    assert before == {p.name: p.read_bytes() for p in (vault / ".watchdog" / "extracted").iterdir()}
    assert fake.tasks == []


# ── each migration on the old investigation ──────────────────────────────────────────────

def test_the_migrations_bring_the_records_up_to_date(tmp_path, fake):
    vault = old_vault(tmp_path)
    # A reporter's mark on the fact whose page is corrected, and on one that is not.
    facts_b = verification.document_facts(vault, "sha-b")
    ids_b = verification.fact_ids("sha-b", facts_b)
    verification._mark(vault, ids_b[0], "verified", "checked the minutes")
    out = _run(vault)
    assert out["applied"] == len(migrations.MIGRATIONS) and out["pending"] == 0 and out["rebuilt"]
    assert migrations.pending(vault) == [] and migrations.status(vault)["banner"] is False

    a = _art(vault, "sha-a")["document"]["key_facts"]
    # Free fixes: the near-miss tag points at the entity.
    assert a[0]["entities"] == ["acme-corp"] and a[0]["extracted_entities"] == ["acme corp"]
    # Erased dates: one read by code from the text, one by the model (inferred), one left.
    assert (a[0]["date"], a[0]["date_text"], a[0]["date_recovery"]) == ("2019-03-03", "3 March 2019", "code")
    assert a[0].get("date_basis") is None
    assert (a[1]["date"], a[1]["date_end"], a[1]["date_basis"], a[1]["date_recovery"]) == \
        ("2019", "2020", "inferred", "model")
    assert a[2]["date"] == "" and "date_recovery" not in a[2]       # no year: never sent
    # Fact tags: the tag naming no entity was matched by the model.
    assert "fenmoor-harbour-board" in a[1]["entities"] and a[1]["entities_inferred"] == ["fenmoor-harbour-board"]

    b = _art(vault, "sha-b")["document"]["key_facts"]
    assert [f["page"] for f in b] == [1, 1]
    new_ids = verification.fact_ids("sha-b", verification.document_facts(vault, "sha-b"))
    marks = verification.marks(vault)
    assert ids_b[0] not in marks and marks[new_ids[0]]["note"] == "checked the minutes"
    assert marks[new_ids[0]]["page"] == 1
    assert verification.attach(marks[new_ids[0]], verification.document_facts(vault, "sha-b")[0])

    reg = _reg(vault)
    # Entity timelines follow the facts.
    events = {(e["date"], e["event"]) for e in reg["acme-corp"]["timeline_events"]}
    assert ("2019-03-03", "Acme Corp signed the lease on 3 March 2019.") in events
    assert ("2019", "Acme Corp paid rent to the board in 2019 and 2020.") in events
    board_events = {e["event"] for e in reg["fenmoor-harbour-board"]["timeline_events"]}
    assert "Acme Corp paid rent to the board in 2019 and 2020." in board_events
    # Relationship targets: one by code, one by the model, each with its reverse.
    fwd = {r["relationship"]: r for r in reg["acme-corp"]["roles"] if not r.get("is_reverse")}
    assert fwd["tenant of"]["target_id"] == "fenmoor-harbour-board"
    assert fwd["audited by"]["target_id"] == "brightwater-audit-llp"
    assert fwd["audited by"]["target_basis"] == "inferred"
    assert any(r.get("is_reverse") and r["target_id"] == "acme-corp"
               for r in reg["brightwater-audit-llp"]["roles"])
    # The same-name group was merged through the logged, undoable path.
    councils = [eid for eid, e in reg.items() if e.get("name") == "City Council" and not e.get("merged_into")]
    assert len(councils) == 1
    log = merge_log.load(vault)
    assert any(m.get("decided_by") == "model" for m in log["merges"])
    # The timeline holds the recovered dates, and the run is one history version.
    timeline_md = (vault / "timeline.md").read_text(encoding="utf-8")
    assert "signed the lease on 3 March 2019" in timeline_md
    assert "date inferred" in timeline_md
    v = history.versions(vault, limit=1)["versions"][0]
    assert v["label"] == f"Brought up to date with this version of Watchdog: {len(migrations.MIGRATIONS)} steps"
    assert not processing_lock(vault).exists()
    usage = [p for p in orchestrate.usage_files(vault)]
    assert usage and {"date-resolve", "fact-tags", "role-targets", "reconcile"} <= set(fake.tasks)


def test_running_twice_changes_nothing(tmp_path, fake):
    vault = old_vault(tmp_path)
    _run(vault)
    snap = {p: p.read_bytes() for p in (vault / ".watchdog" / "extracted").iterdir()}
    reg = (vault / REG / "entities.json").read_bytes()
    # Every step forced to run again: each finds nothing left to do and asks the model nothing.
    data = migrations.load(vault)
    data["applied"] = {}
    migrations._save(vault, data)
    n = len(fake.tasks)
    out = _run(vault)
    assert out["applied"] == len(migrations.MIGRATIONS)
    assert fake.tasks[n:] == []
    assert snap == {p: p.read_bytes() for p in (vault / ".watchdog" / "extracted").iterdir()}
    assert (vault / REG / "entities.json").read_bytes() == reg


def test_a_spending_stop_leaves_the_later_steps_pending_and_resumes(tmp_path, fake, monkeypatch):
    vault = old_vault(tmp_path)
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return "The spending limit is reached." if calls["n"] == 1 else None
    monkeypatch.setattr(orchestrate, "_spending_stop", stop)
    out = _run(vault)
    # The free step ran; the first model step stopped before it started.
    assert out["applied"] == 1 and out["spending_limit"]
    assert [m.id for m in migrations.pending(vault)][0] == "2026-10-erased-dates"
    assert history.versions(vault, limit=1)["versions"][0]["cause"].get("incomplete") is True
    assert "spending limit" in migrations.summary(out)
    out = _run(vault)
    assert out["applied"] == len(migrations.MIGRATIONS) - 1 and migrations.pending(vault) == []


def test_a_failed_call_keeps_the_step_pending(tmp_path, fake, monkeypatch):
    vault = old_vault(tmp_path)

    async def failing(**kw):
        if kw["task"] == "date-resolve":
            raise model_client.ModelError("the provider is down")
        return await fake(**kw)
    monkeypatch.setattr(model_client, "acomplete_json", failing)
    out = _run(vault)
    assert out["applied"] == 1
    a = _art(vault, "sha-a")["document"]["key_facts"]
    assert a[0]["date_recovery"] == "code"            # code's dates are kept
    assert "date_recovery" not in a[1]                # the model's is asked again next time
    assert migrations.pending(vault)[0].id == "2026-10-erased-dates"


def test_refused_while_a_run_holds_the_vault(tmp_path, fake):
    vault = old_vault(tmp_path)
    lock = processing_lock(vault)
    lock.write_text("pid: cli\nstarted_at: 2099-01-01T00:00:00Z\n")
    with pytest.raises(migrations.Busy):
        _run(vault)
    assert lock.exists() and len(migrations.pending(vault)) == len(migrations.MIGRATIONS)


def test_the_sample_and_an_empty_investigation_have_nothing_pending(tmp_path):
    vault = make_vault(tmp_path)
    assert migrations.pending(vault) == [] and migrations.status(vault)["banner"] is False


# ── dates read back from a fact's text ───────────────────────────────────────────────────

@pytest.mark.parametrize("text, want", [
    ("The board met on 3 March 2019 to approve the lease.", ("2019-03-03", "3 March 2019")),
    ("Le conseil a signé le 12 septembre 2019.", ("2019-09-12", "12 septembre 2019")),
    ("On March 3, 2019, the firm paid $5,000.", ("2019-03-03", "March 3, 2019")),
    ("The deal closed (3 March 2019).", ("2019-03-03", "3 March 2019")),
    ("Signed on 25/04/2019.", ("2019-04-25", "25/04/2019")),
    ("After 3 March 2019 the firm stopped trading.", dates.TO_PASS),
    ("Meetings were held on 3 and 7 June 2019.", dates.TO_PASS),
    ("Signed on 03/04/2019.", dates.TO_PASS),
    ("In 2019 the firm grew.", dates.TO_PASS),
    ("Paid in 2019 and 2020.", dates.TO_PASS),
    ("The firm was founded by two partners.", dates.NO_YEAR),
])
def test_find_in_text_accepts_exactly_one_full_date(text, want):
    assert dates.find_in_text(text) == want


# ── the app ──────────────────────────────────────────────────────────────────────────────

def test_the_app_shows_the_estimate_and_starts_the_job(tmp_path, fake, monkeypatch):
    from watchdog.gui import jobs
    vault = old_vault(tmp_path)
    monkeypatch.setattr("watchdog.auth.resolve_auth", lambda *a, **k: {"mode": "api-key"})
    st = call("migrations.status", vault=str(vault))
    assert st["banner"] is True and st["busy"] is False
    est = call("migrations.estimate", vault=str(vault))
    assert est["calls"] >= 3 and est["auth"]["ok"] and est["cost_high"] >= est["cost_low"] > 0
    assert [s["id"] for s in est["steps"]] == [m.id for m in migrations.MIGRATIONS]
    started = []
    monkeypatch.setattr(jobs.MANAGER, "start", lambda v, op, params, label, kind=None: (
        started.append((op, params, label)) or type("J", (), {"to_dict": lambda self: {"id": "j"}})()))
    assert call("jobs.migrate", vault=str(vault)) == {"id": "j"}
    assert started == [("migrate", {}, "Bring this investigation up to date")]
    assert call("migrations.dismissBanner", vault=str(vault))["banner"] is False
    monkeypatch.setattr("watchdog.auth.resolve_auth", lambda *a, **k: {"mode": "none", "reason": "No key."})
    assert call_error("jobs.migrate", vault=str(vault))["code"] == "auth_required"
    processing_lock(vault).write_text("pid: cli\nstarted_at: 2099-01-01T00:00:00Z\n")
    assert call_error("jobs.migrate", vault=str(vault))["code"] == "busy"


def test_the_operation_runs_and_says_what_it_did(tmp_path, fake):
    from watchdog import ops
    vault = old_vault(tmp_path)
    rep = ops.CollectingReporter()
    out = ops.run("migrate", {}, rep, vault)
    assert out["applied"] == len(migrations.MIGRATIONS)
    assert "Brought up to date" in rep.text and ops.exit_code("migrate", out) == 0
    rep = ops.CollectingReporter()
    ops.run("migrate", {}, rep, vault)
    assert "up to date: nothing needed doing" in rep.text


def test_a_moved_fact_id_rewrites_the_links_that_named_it():
    from watchdog.pipeline import citations, committed, resolutions
    old, new = "fact:1:abcdefabcdef:1111111111", "fact:1:abcdefabcdef:2222222222"
    callout = (f"> [!contradiction] Rent\n> - one — [[documents/lease#^{citations.block_id(old)}|Lease]], p. 3\n"
               "> - two — [[documents/minutes|Minutes]], p. 1")
    entities = {"acme-corp": {"contradictions": [callout],
                              "synthesis": {"summary": "x [f:1111]", "fact_refs": {"f:1111": old}}}}
    assert committed._move_links(entities, {old: (new, 1)}) == 1
    moved = entities["acme-corp"]["contradictions"][0]
    assert f"#^{citations.block_id(new)}|" in moved and citations.block_id(old) not in moved
    assert resolutions.contradiction_id(moved) == resolutions.contradiction_id(callout)   # still handled
    assert entities["acme-corp"]["synthesis"]["fact_refs"] == {"f:1111": new}
