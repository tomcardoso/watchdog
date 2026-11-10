"""Merging every record of one name from Review in one go (D399): which pairs form a group, which
record is kept, records marked "Not the same" left out, a merge that fails, and undoing one.

Every name below is invented."""

import json

from watchdog.ops import CollectingReporter
from watchdog.ops.entities import undo_merge
from watchdog.ops.merge import merge_entity_group
from watchdog.pipeline import merge_log, resolutions
from watchdog.pipeline.identity import pair_id

from tests.gui_support import call
from tests.test_merge_entities import make_vault

pytest_plugins = ["tests.gui_support"]

BODY = "Port Calder Harbour Authority"


def _add(vault, eid, name, docs, etype="Public body"):
    reg = vault / ".watchdog" / "registry" / "entities.json"
    ents = json.loads(reg.read_text())
    ents[eid] = {"id": eid, "name": name, "type": etype, "aliases": [], "appears_in": docs,
                 "note_path": f"entities/public-body/{eid}", "roles": [], "timeline_events": [],
                 "date_first_seen": None, "date_last_updated": None}
    reg.write_text(json.dumps(ents))
    note = vault / "entities" / "public-body" / f"{eid}.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(f"---\nid: {eid}\nname: {name}\ntype: {etype}\n---\n\n# {name}\n\n## Notes\n\n")


def _candidates(vault, pairs):
    ents = json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())

    def side(eid):
        e = ents[eid]
        return {"id": eid, "name": e["name"], "type": e["type"], "aliases": [], "documents": [],
                "roles": [], "facts": []}
    cands = {pair_id(a, b): {"kind": "candidate", "id": pair_id(a, b), "status": "open",
                             "tier": "low", "rule": "same-name", "reason": "The same name.",
                             "a": side(a), "b": side(b)} for a, b in pairs}
    (vault / ".watchdog" / "registry" / "merges.json").write_text(
        json.dumps({"schema_version": 1, "merges": [], "candidates": cands}))


def _vault(tmp_path):
    """Four records of one public body, the one in most documents first, each paired with it as a
    run leaves a large same-name group; plus an unrelated pair and a same-name pair of two."""
    vault = make_vault(tmp_path)
    _add(vault, "harbour-1", BODY, ["sha-a", "sha-b"])
    _add(vault, "harbour-2", BODY, ["sha-a"])
    _add(vault, "harbour-3", f"{BODY}.", ["sha-b"])
    _add(vault, "harbour-4", BODY.upper(), ["sha-b"])
    _add(vault, "calder-council-1", "Calder Council", ["sha-a"])
    _add(vault, "calder-council-2", "Calder Council", ["sha-b"])
    _candidates(vault, [("harbour-2", "harbour-1"), ("harbour-1", "harbour-3"),
                        ("harbour-4", "harbour-1"), ("alice-smith", "a-smith-duplicate"),
                        ("calder-council-1", "calder-council-2")])
    return vault


def _entities(vault):
    return json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())


# ── grouping ───────────────────────────────────────────────────────────────────────────────────

def test_open_pairs_among_records_of_one_name_form_one_group(tmp_path, wdg_home):
    vault = _vault(tmp_path)
    (group,) = call("review.sameNameGroups", vault=str(vault))["groups"]
    assert group["size"] == 4 and group["name"] == BODY
    assert group["keep"]["id"] == "harbour-1"
    assert [m["id"] for m in group["merge"]] == ["harbour-3", "harbour-2", "harbour-4"]
    assert group["left_out"] == []
    assert group["rids"] == sorted([pair_id("harbour-1", "harbour-2"), pair_id("harbour-1", "harbour-3"),
                                    pair_id("harbour-1", "harbour-4")])


def test_a_name_with_only_two_records_or_two_different_names_is_not_a_group(tmp_path):
    vault = _vault(tmp_path)
    keys = [g["keep"]["id"] for g in merge_log.same_name_groups(vault)]
    assert keys == ["harbour-1"]
    # A record of the same name but another type does not join the group.
    _add(vault, "harbour-5", BODY, ["sha-a", "sha-b"], etype="Company")
    data = json.loads(merge_log.path(vault).read_text())
    data["candidates"][pair_id("harbour-5", "harbour-2")] = {
        **data["candidates"][pair_id("harbour-1", "harbour-2")], "id": pair_id("harbour-5", "harbour-2"),
        "a": {"id": "harbour-5"}, "b": {"id": "harbour-2"}}
    merge_log.path(vault).write_text(json.dumps(data))
    (group,) = merge_log.same_name_groups(vault)
    assert group["keep"]["id"] == "harbour-1" and group["size"] == 4


def test_the_kept_record_is_in_most_documents_then_has_the_longer_name(tmp_path):
    vault = _vault(tmp_path)
    # Equal documents: the longer name ("The …" folds to the same name) is kept.
    _add(vault, "harbour-1", BODY, ["sha-a"])
    _add(vault, "harbour-3", f"The {BODY}", ["sha-b"])
    (group,) = merge_log.same_name_groups(vault)
    assert group["keep"]["id"] == "harbour-3"
    assert group["name"] == f"The {BODY}"


# ── "Not the same" ─────────────────────────────────────────────────────────────────────────────

def _with_marks(tmp_path):
    """Five records of one name, harbour-1 kept; the order after it is harbour-3 (longer name),
    harbour-2, harbour-4, harbour-5. harbour-2 is marked Not the same as the kept record (it stays
    in the group through an open pair with harbour-3), and harbour-4 as harbour-3."""
    vault = _vault(tmp_path)
    _add(vault, "harbour-5", BODY, ["sha-a"])
    _candidates(vault, [("harbour-2", "harbour-3"), ("harbour-1", "harbour-3"),
                        ("harbour-4", "harbour-1"), ("harbour-5", "harbour-1")])
    resolutions.resolve(vault, [pair_id("harbour-1", "harbour-2"), pair_id("harbour-3", "harbour-4")],
                        label="review")
    return vault


def test_records_marked_not_the_same_are_left_out_of_the_group(tmp_path):
    vault = _with_marks(tmp_path)
    (group,) = merge_log.same_name_groups(vault)
    assert group["keep"]["id"] == "harbour-1"
    assert [m["id"] for m in group["merge"]] == ["harbour-3", "harbour-5"]
    assert group["size"] == 3
    assert [(r["id"], r["not_same_as"]["id"]) for r in group["left_out"]] == [
        ("harbour-2", "harbour-1"), ("harbour-4", "harbour-3")]


def test_a_group_with_fewer_than_three_records_left_is_not_offered(tmp_path):
    vault = _with_marks(tmp_path)
    resolutions.resolve(vault, [pair_id("harbour-1", "harbour-5")], label="review")
    assert merge_log.same_name_groups(vault) == []


def test_the_operation_never_merges_records_marked_not_the_same(tmp_path):
    vault = _with_marks(tmp_path)
    rep = CollectingReporter()
    # The app's list may predate the marks: the operation reads them again.
    out = merge_entity_group(rep, vault, keep="harbour-1",
                             merge=["harbour-3", "harbour-2", "harbour-4", "harbour-5"])
    assert [m["merge"] for m in out["merged"]] == ["harbour-3", "harbour-5"]
    assert [(r["id"], r["not_same_as"]) for r in out["left_out"]] == [
        ("harbour-2", "harbour-1"), ("harbour-4", "harbour-3")]
    ents = _entities(vault)
    assert {"harbour-2", "harbour-4"} <= set(ents)
    assert not {"harbour-3", "harbour-5"} & set(ents)
    assert any("Left out" in w for w in rep.warnings)


# ── merging the group ──────────────────────────────────────────────────────────────────────────

def test_the_group_is_merged_one_record_at_a_time_as_the_reporters_merges(tmp_path, wdg_home):
    from watchdog import ops
    vault = _vault(tmp_path)
    (group,) = merge_log.same_name_groups(vault)
    out = ops.run("merge-entity-group", {"keep": group["keep"]["id"],
                                         "merge": [m["id"] for m in group["merge"]]},
                  CollectingReporter(), vault)
    assert [m["merge"] for m in out["merged"]] == ["harbour-3", "harbour-2", "harbour-4"]
    assert out["failed"] == [] and out["left_out"] == []
    assert set(_entities(vault)["harbour-1"]["appears_in"]) == {"sha-a", "sha-b"}
    log = merge_log.load(vault)
    assert [(m["keep"]["id"], m["merged"]["id"]) for m in log["merges"]] == [
        ("harbour-1", "harbour-3"), ("harbour-1", "harbour-2"), ("harbour-1", "harbour-4")]
    assert {m["decided_by"] for m in log["merges"]} == {"reporter"}
    # The group's pairs are settled; the unrelated pairs stay open, and no group is left.
    assert merge_log.same_name_groups(vault) == []
    assert {c["id"] for c in merge_log.open_candidates(vault)} == {
        pair_id("alice-smith", "a-smith-duplicate"), pair_id("calder-council-1", "calder-council-2")}


def test_one_failed_merge_does_not_stop_the_rest(tmp_path, monkeypatch):
    from watchdog import ops
    from watchdog.ops import merge as merge_ops
    vault = _vault(tmp_path)
    real = merge_ops._merge_entities.run

    def flaky(v, keep, merge):
        if merge == "harbour-4":
            raise OSError("the registry is locked")
        return real(v, keep, merge)
    monkeypatch.setattr(merge_ops._merge_entities, "run", flaky)
    out = merge_entity_group(CollectingReporter(), vault, keep="harbour-1",
                             merge=["harbour-3", "harbour-4", "harbour-2"])
    assert [m["merge"] for m in out["merged"]] == ["harbour-3", "harbour-2"]
    (bad,) = out["failed"]
    assert bad["merge"] == "harbour-4" and "the registry is locked" in bad["error"]
    assert ops.exit_code("merge-entity-group", out) == 1
    ents = _entities(vault)
    assert "harbour-4" in ents and not {"harbour-3", "harbour-2"} & set(ents)


def test_one_merge_from_the_group_can_be_undone_on_its_own(tmp_path):
    vault = _vault(tmp_path)
    merge_entity_group(CollectingReporter(), vault, keep="harbour-1",
                       merge=["harbour-3", "harbour-4", "harbour-2"])
    last = merge_log.load(vault)["merges"][-1]
    assert last["merged"]["id"] == "harbour-2"
    undo_merge(CollectingReporter(), vault, id=last["id"])
    ents = _entities(vault)
    assert "harbour-2" in ents and not {"harbour-3", "harbour-4"} & set(ents)
    # The split record is now Not the same as the kept one, so the group never takes it again.
    assert pair_id("harbour-1", "harbour-2") in resolutions.resolved_ids(vault)
    others = [m for m in merge_log.load(vault)["merges"] if m["id"] != last["id"]]
    assert len(others) == 2 and all(not m.get("undone") for m in others)

