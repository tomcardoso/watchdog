"""Bulk tidy-up in Review (D328): merging and dismissing several possible same entities, calling
possible duplicates copies or different, and the document requests worked as a list.

Every name below is invented."""

import json

import pytest

from watchdog.ops import CollectingReporter
from watchdog.ops.merge import merge_entity_pairs
from watchdog.pipeline import merge_log, merge_undo, requests, resolutions
from watchdog.pipeline.identity import pair_id
from watchdog.review import duplicate_verdicts, open_items

from tests.gui_support import SHA2, SHA3, call, call_error
from tests.test_merge_entities import make_vault

pytest_plugins = ["tests.gui_support"]


def _candidates(vault, pairs):
    """Open "possible same" pairs in the merge log, as a run leaves them."""
    ents = json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())

    def side(eid):
        e = ents.get(eid) or {"name": eid, "type": "Person"}
        return {"id": eid, "name": e["name"], "type": e.get("type"), "aliases": [], "documents": [],
                "roles": [], "facts": []}
    cands = {pair_id(a, b): {"kind": "candidate", "id": pair_id(a, b), "status": "open", "tier": "low",
                             "rule": "initial", "reason": "One name is an initialled form of the other.",
                             "a": side(a), "b": side(b)} for a, b in pairs}
    (vault / ".watchdog" / "registry" / "merges.json").write_text(
        json.dumps({"schema_version": 1, "merges": [], "candidates": cands}))


def _entities(vault):
    return json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())


# ── Merge ticked ───────────────────────────────────────────────────────────────────────────────

def test_a_failure_partway_leaves_the_earlier_merges_made_and_says_which_failed(tmp_path):
    vault = make_vault(tmp_path)
    _candidates(vault, [("alice-smith", "a-smith-duplicate"), ("alice-smith", "bob-jones")])
    rep = CollectingReporter()
    out = merge_entity_pairs(rep, vault, keep=["alice-smith", "alice-smith", "alice-smith"],
                             merge=["a-smith-duplicate", "nobody-here", "bob-jones"])

    assert [(m["keep"], m["merge"]) for m in out["merged"]] == [("alice-smith", "a-smith-duplicate"),
                                                               ("alice-smith", "bob-jones")]
    (bad,) = out["failed"]
    assert bad["merge"] == "nobody-here" and "no entity" in bad["error"]
    assert out["skipped"] == []
    ents = _entities(vault)
    assert "a-smith-duplicate" not in ents and "bob-jones" not in ents
    assert "Merged 2 of 3 pairs; 1 could not be merged." in rep.text
    assert any("nobody-here" in w for w in rep.warnings)

    # Each merge went through the one merge path: logged on its own, as the reporter's, from the
    # pair it settled, and the pairs are no longer open.
    log = merge_log.load(vault)
    assert [(m["keep"]["id"], m["merged"]["id"]) for m in log["merges"]] == [
        ("alice-smith", "a-smith-duplicate"), ("alice-smith", "bob-jones")]
    assert {m["decided_by"] for m in log["merges"]} == {"reporter"}
    assert all(m["tier"] == "low" for m in log["merges"])
    assert merge_log.open_candidates(vault) == []
    # The most recent can be split back out at once; undoing it frees the one before.
    assert merge_undo.check(vault, log["merges"][-1]) is None


def test_the_exit_code_reports_a_failure_and_none_when_all_merged(tmp_path):
    from watchdog import ops
    assert ops.exit_code("merge-entity-pairs", {"merged": [{}], "failed": [{}]}) == 1
    assert ops.exit_code("merge-entity-pairs", {"merged": [{}], "failed": []}) == 0


def test_a_pair_whose_record_was_merged_earlier_in_the_batch_follows_it(tmp_path):
    vault = make_vault(tmp_path)
    out = merge_entity_pairs(CollectingReporter(), vault,
                             keep=["alice-smith", "a-smith-duplicate", "alice-smith"],
                             merge=["a-smith-duplicate", "bob-jones", "a-smith-duplicate"])
    # A. Smith joined Alice Smith first, so Bob Jones is merged into Alice Smith; the third pair
    # is already one record.
    assert [(m["keep"], m["merge"]) for m in out["merged"]] == [("alice-smith", "a-smith-duplicate"),
                                                               ("alice-smith", "bob-jones")]
    assert [(s["keep"], s["merge"]) for s in out["skipped"]] == [("alice-smith", "a-smith-duplicate")]
    assert out["failed"] == []


def test_the_batch_refuses_unmatched_lists(tmp_path):
    vault = make_vault(tmp_path)
    with pytest.raises(SystemExit):
        merge_entity_pairs(CollectingReporter(), vault, keep=["alice-smith"], merge=[])


def test_the_batch_is_an_operation_the_app_can_run(tmp_path):
    from watchdog import ops
    assert ops.schema()["merge-entity-pairs"]["params"]["keep"]["type"] == "list[str]"
    vault = make_vault(tmp_path)
    out = ops.run("merge-entity-pairs", {"keep": ["alice-smith"], "merge": ["a-smith-duplicate"]},
                  CollectingReporter(), vault)
    assert len(out["merged"]) == 1


# ── Not the same for ticked ────────────────────────────────────────────────────────────────────

def test_not_the_same_for_several_pairs_at_once_and_back(tmp_path, wdg_home):
    vault = make_vault(tmp_path)
    _candidates(vault, [("alice-smith", "a-smith-duplicate"), ("bob-jones", "acme-corp")])
    rids = [i["rid"] for i in open_items(vault, ("merges",))]
    assert len(rids) == 2
    assert sorted(call("review.resolve", vault=str(vault), rids=rids)["resolved"]) == sorted(rids)
    assert open_items(vault, ("merges",)) == []
    assert merge_log.open_candidates(vault) == []
    # Nothing was merged, and the pairs come back together.
    assert {"a-smith-duplicate", "bob-jones"} <= set(_entities(vault))
    call("review.unresolve", vault=str(vault), rids=rids)
    assert len(open_items(vault, ("merges",))) == 2


# ── These are copies / Different documents ─────────────────────────────────────────────────────

def _dup_rid(rich_vault):
    (item,) = [i for i in open_items(rich_vault, ("duplicates",))]
    return item["rid"]


def _row(rich_vault, sha):
    return next(d for d in call("vault.documents", vault=str(rich_vault)) if d["sha"] == sha)


def test_these_are_copies_hides_the_pair_and_keeps_the_flag(rich_vault):
    rid = _dup_rid(rich_vault)
    call("review.resolve", vault=str(rich_vault), rids=[rid], label="copies")
    assert open_items(rich_vault, ("duplicates",)) == []
    assert duplicate_verdicts(rich_vault) == {SHA3[:12]: "copies"}
    row = _row(rich_vault, SHA3)
    assert row["near_duplicate_of"] and row["duplicate_verdict"] == "copies"
    assert resolutions.load(rich_vault)["resolved"][rid]["label"] == "copies"
    # Neither outcome deletes anything.
    assert SHA3 in json.loads((rich_vault / ".watchdog" / "registry" / "documents.json").read_text())


def test_different_documents_clears_the_flag_until_brought_back(rich_vault):
    rid = _dup_rid(rich_vault)
    before = json.loads((rich_vault / ".watchdog" / "registry" / "documents.json").read_text())
    call("review.resolve", vault=str(rich_vault), rids=[rid], label="different")
    row = _row(rich_vault, SHA3)
    assert row["near_duplicate_of"] is None and row["duplicate_verdict"] is None
    doc = call("vault.document", vault=str(rich_vault), sha=SHA3)
    assert doc["near_duplicate_of"] is None and doc["duplicates"] == []
    # The registry keeps pre-processing's match, so bringing the item back restores the flag.
    assert json.loads((rich_vault / ".watchdog" / "registry" / "documents.json").read_text()) == before
    call("review.unresolve", vault=str(rich_vault), rids=[rid])
    assert _row(rich_vault, SHA3)["near_duplicate_of"]


def test_a_duplicate_handled_before_the_two_outcomes_reads_as_copies(rich_vault):
    rid = _dup_rid(rich_vault)
    call("review.resolve", vault=str(rich_vault), rids=[rid])
    assert duplicate_verdicts(rich_vault) == {SHA3[:12]: "copies"}


def test_only_a_duplicate_takes_a_copies_or_different_label(rich_vault):
    lead = "lead:unprofiled:ghost-ltd"
    assert call_error("review.resolve", vault=str(rich_vault), rids=[lead], label="copies")["code"] == "bad_params"
    assert call_error("review.resolve", vault=str(rich_vault), rids=[lead], label="whatever")["code"] == "bad_params"


# ── Document requests ──────────────────────────────────────────────────────────────────────────

def test_source_group_folds_the_wording_around_the_holder():
    g = requests.source_group
    assert g("City Clerk, by access to information request") == ("city clerk", "City Clerk")
    assert g("the city clerk") == ("city clerk", "City clerk")
    assert g("Harbourline Dredging Ltd., or the city under the contract")[0] == g("Harbourline Dredging Ltd.")[0]
    assert g("Harbourline Dredging Ltd.")[1] == "Harbourline Dredging Ltd."
    assert g("Land Titles Office (Westmarch)") == ("land titles office", "Land Titles Office")
    assert g("Provincial Archives — through a records request")[0] == "provincial archives"
    assert g(None) == ("", "") and g("  ") == ("", "")


def _req(rid, what, source, shas):
    return {"rid": rid, "what": what, "likely_source": source,
            "sources": [{"sha256": s, "filename": f"{s}.pdf"} for s in shas]}


def test_requests_sort_by_citing_documents_and_group_by_source():
    open_ = [
        _req("r:a", "Bylaw amendment minutes", "City Clerk", ["1"]),
        _req("r:b", "Berth lease renewal", "Harbour board, by request", ["1", "2", "3"]),
        _req("r:c", "Council vote record", "the City Clerk, access request", ["2", "3"]),
        _req("r:d", "Invoice 77", None, ["4", "5", "6", "7"]),
        _req("r:e", "Dredging survey", "Harbour Board", ["2", "2"]),   # one document twice
    ]
    rows, groups = requests.worklist(open_)
    assert [r["rid"] for r in rows] == ["r:d", "r:b", "r:c", "r:a", "r:e"]
    assert [r["cite_count"] for r in rows] == [4, 3, 2, 1, 1]
    assert [(g["key"], g["label"], g["count"], g["documents"]) for g in groups] == [
        ("harbour board", "Harbour Board", 2, 3),   # its top request is cited by 3; label ties go alphabetically
        ("city clerk", "City Clerk", 2, 3),
        ("", "", 1, 4),                              # no source given: always last
    ]
    assert {r["rid"]: r["source_key"] for r in rows}["r:c"] == "city clerk"


def test_the_requests_rpc_carries_the_groups(rich_vault):
    r = call("vault.requests", vault=str(rich_vault))
    (req,) = r["open"]
    assert req["cite_count"] == 1 and req["source_key"] == "registry"
    assert r["groups"] == [{"key": "registry", "label": "Registry", "count": 1, "documents": 1}]


def test_requests_are_handled_in_bulk_through_the_one_store(rich_vault):
    requests.record(rich_vault, [{"type": "Permit", "what": "Berth permit 12", "why_it_matters": "x",
                                  "likely_source": "Harbour Board"}],
                    sha256=SHA2, filename="report-two.pdf", document_note="documents/report-two")
    rids = [r["rid"] for r in call("vault.requests", vault=str(rich_vault))["open"]]
    assert len(rids) == 2
    call("review.resolve", vault=str(rich_vault), rids=rids)
    assert call("vault.requests", vault=str(rich_vault))["open"] == []
