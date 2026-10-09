"""Undo merge (D280): a merge is split back exactly, by what its log entry recorded."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from watchdog.pipeline import entity_facts, entity_notes, merge_entities, merge_log, merge_undo
from watchdog.pipeline.identity import pair_id

SRC = Path(__file__).resolve().parent.parent / "src"


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    root = tmp_path_factory.mktemp("demo-undo")
    vault = root / "vault"
    env = {**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"}
    proc = subprocess.run([sys.executable, "-m", "watchdog.gui.demo", str(vault), "--home", str(root / "home")],
                          env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return vault


@pytest.fixture
def vault(demo, tmp_path):
    copy = tmp_path / "v"
    shutil.copytree(demo, copy)
    entity_facts.clear_cache()
    return copy


def _entities(vault):
    return json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())


def _facts(vault, eid):
    entity_facts.clear_cache()
    return {f["id"] for f in entity_facts.FactIndex(vault).facts_for(eid)}


def _merge(vault, rule):
    return next(m for m in merge_log.load(vault)["merges"] if m.get("rule") == rule)


def test_undo_splits_a_rule_merge_back_exactly(vault):
    m = _merge(vault, "same-identifier")
    keep = m["keep"]["id"]
    before = _facts(vault, keep)
    out = merge_undo.undo(vault, m["id"], by="Test Reporter")
    split = out["split_id"]
    ents = _entities(vault)
    assert split in ents and keep in ents
    kept, moved = _facts(vault, keep), _facts(vault, split)
    assert kept | moved == before and moved and kept
    assert not kept & moved                         # no fact was about both before the merge
    # Each moved fact is one the merged record's documents tagged with the merged id.
    changed = {c["sha"] for c in m["undo"]["changes"]}
    assert set(ents[split]["appears_in"]) <= changed | set((m["undo"].get("entry") or {}).get("appears_in") or [])
    # The log records the undo, the pair is never merged automatically again, and merges.md says so.
    logged = next(x for x in merge_log.load(vault)["merges"] if x["id"] == m["id"])
    assert logged["undone"]["by"] == "Test Reporter" and logged["undone"]["split_id"] == split
    from watchdog.pipeline import resolutions
    assert pair_id(keep, split) in resolutions.resolved_ids(vault)
    assert "**Undone**" in (vault / "merges.md").read_text()
    # Both notes exist and are what a rebuild gives.
    notes = {p: p.read_text() for p in (vault / "entities").rglob("*.md")}
    entity_notes.rebuild(vault, index_search=False)
    assert notes == {p: p.read_text() for p in (vault / "entities").rglob("*.md")}
    # Undoing twice is refused, with the reason.
    with pytest.raises(merge_undo.UndoRefused, match="already been undone"):
        merge_undo.undo(vault, m["id"])


def test_undo_of_a_reporter_merge_restores_the_record_and_leaves_notes_on_the_survivor(vault):
    ents = _entities(vault)
    keep, other = "leonard-pike", "dana-whitcombe"     # two committed people, merged by hand
    facts_keep, facts_other = _facts(vault, keep), _facts(vault, other)
    merge_entities.run(vault, keep, other)
    assert other not in _entities(vault)
    assert _facts(vault, keep) == facts_keep | facts_other           # the log leads old tags here
    note = vault / f"{ents[keep]['note_path']}.md"
    note.write_text(note.read_text().replace(
        "<!-- Journalist annotations — never overwritten by ingestion. -->", "Written after the merge."))

    m = next(x for x in merge_log.load(vault)["merges"] if x["merged"]["id"] == other
             and x["decided_by"] == "reporter")
    out = merge_undo.undo(vault, m["id"])
    assert out["split_id"] == other
    after = _entities(vault)
    assert after[other]["name"] == ents[other]["name"]
    assert set(after[other]["appears_in"]) == set(ents[other]["appears_in"])
    assert _facts(vault, keep) == facts_keep and _facts(vault, other) == facts_other
    assert "Written after the merge." in note.read_text()               # notes stay on the survivor
    # Every relationship, on every record, points where it pointed before the merge.
    def links(reg):
        return {eid: sorted({((r.get("relationship") or "").lower(), r.get("target_id"),
                              bool(r.get("is_reverse"))) for r in e.get("roles") or []})
                for eid, e in reg.items()}
    assert links(after) == links(ents)
    other_note = (vault / f"{after[other]['note_path']}.md").read_text()
    assert "merged_into" not in other_note and "## Facts" in other_note


def test_undo_is_refused_when_it_cannot_be_done_correctly(vault):
    data = merge_log.load(vault)
    old = dict(data["merges"][0])
    old["undo"] = {"extracted_id": "x"}                       # logged before D280
    assert "earlier version" in merge_undo.check(vault, old)
    inside = dict(old, undo={"version": 2, "available": False, "reason": "Merged inside one document."})
    assert merge_undo.check(vault, inside) == "Merged inside one document."
    gone = dict(data["merges"][0], keep={"id": "no-such-entity"})
    assert "merged into another" in merge_undo.check(vault, gone)
    # A document processed again since the merge: the recorded items no longer match.
    m = _merge(vault, "same-identifier")
    sha = m["undo"]["changes"][0]["sha"]
    path = vault / ".watchdog" / "extracted" / f"{sha}.json"
    art = json.loads(path.read_text())
    for e in art["entities"]:
        e["id"] = "something-else"
    path.write_text(json.dumps(art))
    assert "processed again" in merge_undo.check(vault, m)


def test_every_demo_merge_is_either_undoable_or_refused_with_a_reason(vault):
    ents = _entities(vault)
    reasons = [merge_undo.check(vault, m, ents) for m in merge_log.load(vault)["merges"]]
    assert reasons.count(None) >= 40
    assert all(r is None or r.endswith(".") for r in reasons)


def test_main_reports_a_refusal(vault, capsys):
    assert merge_undo.main(["merge:nope", "--vault", str(vault)]) == 1
    assert "not in this investigation's merge log" in capsys.readouterr().err


def test_the_app_starts_undo_as_a_job_and_refuses_with_the_reason(vault, monkeypatch):
    from tests.gui_support import call, call_error
    from watchdog.gui import jobs
    started = []
    monkeypatch.setattr(jobs.MANAGER, "start", lambda v, args, label, kind, argv=None: (
        started.append((args, argv)) or type("J", (), {"to_dict": lambda self: {"id": "j"}})()))
    monkeypatch.setattr("watchdog.gui.vaultio.require_granted", lambda p: None)
    log = call("review.mergeLog", vault=str(vault))
    ok = next(m for m in log["merges"] if m["undo_available"])
    refused = next(m for m in log["merges"] if not m["undo_available"])
    assert refused["undo_reason"]
    assert call("jobs.undoMerge", vault=str(vault), id=ok["id"]) == {"id": "j"}
    assert started[0][0] == ["undo-merge", ok["id"]]
    assert started[0][1][-2:] == ["watchdog.pipeline.merge_undo", ok["id"]]
    assert call_error("jobs.undoMerge", vault=str(vault), id=refused["id"])["code"] == "cannot_undo"
    call("jobs.rebuildNotes", vault=str(vault))
    assert started[-1][0] == ["rebuild-notes"]
