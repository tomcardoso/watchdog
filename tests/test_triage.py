"""Start here, the judgement/tidy-up split, what a batch added and Review's last-opened time (D327).

Every name below is invented."""

import json
import re

from watchdog import triage
from watchdog.pipeline import hidden, resolutions
from watchdog.review import open_items

from tests.gui_support import call
from tests.test_write_vault import make_vault

pytest_plugins = ["tests.gui_support"]


def _term(t):
    return {"term": t, "regex": re.compile(rf"(?<!\w){re.escape(t)}(?!\w)", re.IGNORECASE)}


def _c(rid, entity, docs, title=None):
    return {"kind": "contradictions", "rid": rid, "title": title or f"{entity} — a conflict",
            "note": None, "entity": entity, "doc_count": docs}


def _lead(rid, entity, docs, related=(), title=None):
    return {"kind": "leads", "rid": rid, "title": title or f"{entity} — named but never profiled",
            "note": None, "entity": entity, "related": list(related), "doc_count": docs}


def _alert(rid, term, entity=None, title=None):
    return {"kind": "alerts", "rid": rid, "title": title or f"`{term}` in {rid}.pdf", "note": None,
            "term": term, "entity": entity, "doc_count": 1}


def _rids(ranked):
    return [r["rid"] for r in ranked]


# ── rank: each signal ──────────────────────────────────────────────────────────────────────────

def test_a_watch_list_term_outranks_everything_else():
    items = [_c("c:hub", "hub", 3), _lead("l:plain", "orla-vance", 1)]
    names = {"orla-vance": ["Orla Vance", "O. Vance"], "hub": ["Hub Ltd."]}
    ranked = triage.rank(items, terms=[_term("O. Vance")], names=names, degree={"hub": 9})
    assert _rids(ranked) == ["l:plain", "c:hub"]
    assert ranked[0]["reason"].startswith("On your watch list: “O. Vance”")


def test_an_alert_is_tied_to_its_term():
    ranked = triage.rank([_c("c:1", "x", 2), _alert("a:1", "Quayside")])
    assert _rids(ranked) == ["a:1", "c:1"]
    assert ranked[0]["reason"] == "On your watch list: “Quayside”"


def test_the_most_connected_entities_and_main_subjects_rank_next():
    items = [_c("c:edge", "edge", 2), _c("c:hub", "hub", 2), _c("c:subject", "subj", 2)]
    degree = {"hub": 5, "edge": 1, "subj": 0}
    ranked = triage.rank(items, names={"hub": ["Hub Ltd."], "subj": ["Pier 4"]}, degree=degree,
                         subjects={"subj": 3})
    assert _rids(ranked) == ["c:hub", "c:subject", "c:edge"]
    assert "Hub Ltd., one of the most connected entities" in ranked[0]["reason"]
    assert "Pier 4, the main subject of 3 documents" in ranked[1]["reason"]


def test_a_related_entity_counts_for_a_lead():
    items = [_lead("l:a", "ghost-a", 1), _lead("l:b", "ghost-b", 1, related=["hub"])]
    assert _rids(triage.rank(items, degree={"hub": 4}))[0] == "l:b"


def test_only_the_top_entities_by_degree_are_central():
    degree = {f"e{i:02d}": 20 - i for i in range(15)}
    central, _ = triage.central_entities(degree, {})
    assert central == {f"e{i:02d}" for i in range(triage.CENTRAL_TOP)}
    assert triage.central_entities({"lonely": 1}, {"once": 1}) == (set(), set())


def test_a_contradiction_between_documents_outranks_one_within_a_document():
    ranked = triage.rank([_c("c:within", "x", 1), _c("c:across", "y", 2)])
    assert _rids(ranked) == ["c:across", "c:within"]
    assert ranked[0]["reason"] == "2 documents disagree"
    assert ranked[1]["reason"] == "Two passages of one document disagree"


def test_a_lead_named_in_several_documents_outranks_one_named_in_one():
    ranked = triage.rank([_lead("l:one", "a", 1), _lead("l:three", "b", 3)])
    assert _rids(ranked) == ["l:three", "l:one"]
    assert [r["reason"] for r in ranked] == ["Named in 3 documents", "Named in one document"]


def test_signals_are_ordered_watch_then_central_then_contradiction_then_lead():
    items = [_lead("l:multi", "a", 4), _c("c:cross", "b", 2), _c("c:hub", "hub", 1),
             _lead("l:watched", "w", 1)]
    ranked = triage.rank(items, terms=[_term("Wren Hollis")], names={"w": ["Wren Hollis"]},
                         degree={"hub": 6})
    assert _rids(ranked) == ["l:watched", "c:hub", "c:cross", "l:multi"]


# ── rank: ties, limits and removed entities ───────────────────────────────────────────────────

def test_ties_do_not_depend_on_input_order():
    items = [_lead("l:b", "b", 1, title="Beta — x"), _c("c:z", "z", 1, title="Zed — y"),
             _lead("l:a", "a", 1, title="Alpha — x"), _c("c:a", "a2", 1, title="Alpha — y")]
    first = _rids(triage.rank(items))
    assert first == _rids(triage.rank(list(reversed(items))))
    assert first == ["c:a", "c:z", "l:a", "l:b"]   # kind, then title


def test_more_documents_break_a_tie_within_a_tier():
    ranked = triage.rank([_c("c:2", "a", 2), _c("c:5", "b", 5)])
    assert _rids(ranked) == ["c:5", "c:2"]


def test_the_slice_has_a_limit_and_one_alert_per_term():
    items = [_alert(f"a:{i}", "Quayside") for i in range(5)] + [_alert("a:other", "Wharf 9")]
    items += [_c(f"c:{i}", f"e{i}", 2) for i in range(10)]
    ranked = triage.rank(items, limit=6)
    assert len(ranked) == 6
    alerts = [r for r in ranked if r["kind"] == "alerts"]
    assert {r["rid"] for r in alerts} == {"a:0", "a:other"}


def test_one_kind_takes_at_most_half_while_others_wait_and_fills_otherwise():
    items = [_c(f"c:{i}", f"e{i}", 3) for i in range(10)] + [_lead("l:1", "x", 1), _lead("l:2", "y", 1)]
    ranked = triage.rank(items, limit=7)
    kinds = [r["kind"] for r in ranked]
    # Four contradictions (half of seven, rounded) and both leads, then the share is relaxed
    # to fill the slice with the next contradiction; the slice is still shown in rank order.
    assert kinds == ["contradictions"] * 5 + ["leads"] * 2
    only = triage.rank([_c(f"c:{i}", f"e{i}", 3) for i in range(10)], limit=7)
    assert len(only) == 7


def test_tidy_up_kinds_are_never_in_the_slice():
    items = [{"kind": "duplicates", "rid": "d:1", "title": "x"}, {"kind": "merges", "rid": "s:1", "title": "y"}]
    assert triage.rank(items) == []


def test_a_removed_entity_never_appears_or_gives_a_reason():
    items = [_c("c:gone", "gone", 3), _lead("l:named-by-gone", "ghost", 1, related=["gone"]),
             _alert("a:1", "Quayside", entity="gone")]
    ranked = triage.rank(items, names={"gone": ["Gone Inc."]}, degree={"gone": 9},
                         subjects={"gone": 5}, hidden={"gone"})
    assert _rids(ranked) == ["l:named-by-gone"]
    assert "Gone" not in ranked[0]["reason"]


def test_each_result_carries_what_the_app_shows():
    (r,) = triage.rank([_c("c:1", "a", 2)])
    assert set(r) == {"rid", "kind", "title", "note", "reason"}


# ── graph inputs ───────────────────────────────────────────────────────────────────────────────

def test_graph_inputs_count_neighbours_and_main_subjects():
    entities = {
        "a": {"name": "Ada Corp", "aliases": ["Ada"], "roles": [{"target_id": "b"}, {"target_id": "c"},
                                                                 {"target_id": "b"}, {"target_id": "gone"}]},
        "b": {"name": "Bo", "roles": [{"target_id": "a", "is_reverse": True}]},
        "gone": {"name": "Gone", "roles": [{"target_id": "a"}]},
    }
    documents = {"1": {"morgue_path": "morgue/a/letter/x.pdf"}, "2": {"morgue_path": "morgue/a/memo/y.pdf"},
                 "3": {"morgue_path": "morgue/unknown/document/z.pdf"}, "4": {}}
    names, degree, subjects = triage.graph_inputs(entities, documents, {"gone"})
    assert names["a"] == ["Ada Corp", "Ada"]
    assert degree == {"a": 2, "b": 1}
    assert subjects == {"a": 2}


# ── judgement and tidy-up ──────────────────────────────────────────────────────────────────────

def test_groups_split_judgement_from_tidy_up():
    counts = {"contradictions": 3, "leads": 4, "alerts": 2, "disputed": 1,
              "near_duplicates": 5, "possible_same": 6, "incoming": 99}
    assert triage.groups(counts) == {"judgement": 10, "tidy_up": 11}
    assert triage.groups({}) == {"judgement": 0, "tidy_up": 0}


def test_the_overview_summary_carries_both_groups_and_the_slice(rich_vault):
    s = call("vault.summary", vault=str(rich_vault))
    assert s["judgement"] == s["contradictions"] + s["leads"] + s["alerts"] + s["verification"]["disputed"]
    assert s["tidy_up"] == s["near_duplicates"] + s["possible_same"]
    assert s["start_here"] and all(i["kind"] in triage.JUDGEMENT for i in s["start_here"])
    assert all(i["reason"] for i in s["start_here"])
    assert s["since_review"] is None                     # Review never opened


# ── items: the inputs Review attaches ─────────────────────────────────────────────────────────

def _entities(vault, ents):
    (vault / ".watchdog" / "registry" / "entities.json").write_text(json.dumps(ents))


CROSS = ("> [!contradiction] Price of the lot\n> - **$1** — [[documents/deed-a|Deed A]], p. 1\n"
         "> - **$2** — [[documents/deed-b#^f-1a|Deed B]], p. 2")
WITHIN = ("> [!contradiction] Date of the sale\n> - **May** — [[documents/deed-a|Deed A]], p. 1\n"
          "> - **June** — [[documents/deed-a|Deed A]], p. 4")


def test_contradictions_count_the_documents_they_cite(tmp_path):
    vault = make_vault(tmp_path)
    _entities(vault, {"lot-7": {"name": "Lot 7", "type": "place", "note_path": "entities/place/lot-7",
                                "contradictions": [CROSS, WITHIN], "appears_in": ["x"]}})
    items = open_items(vault, ("contradictions",))
    assert [(i["entity"], i["doc_count"]) for i in items] == [("lot-7", 2), ("lot-7", 1)]


def test_an_unprofiled_lead_names_who_mentions_it(tmp_path):
    vault = make_vault(tmp_path)
    _entities(vault, {
        "ida-marsh": {"name": "Ida Marsh", "type": "person", "roles": [
            {"relationship": "director of", "target_id": "ghost-co", "source_sha256": "s1"}]},
        "removed-co": {"name": "Removed Co", "type": "organization", "roles": [
            {"relationship": "owns", "target_id": "ghost-co", "source_sha256": "s2"}]},
    })
    (item,) = [i for i in open_items(vault, ("leads",)) if i["entity"] == "ghost-co"]
    assert item["related"] == ["ida-marsh", "removed-co"]


def test_start_here_on_a_vault_leaves_removed_entities_out(tmp_path):
    vault = make_vault(tmp_path)
    _entities(vault, {
        "kept": {"name": "Kept Ltd.", "type": "organization", "note_path": "entities/organization/kept",
                 "contradictions": [CROSS], "appears_in": ["a"]},
        "gone": {"name": "Gone Ltd.", "type": "organization", "note_path": "entities/organization/gone",
                 "contradictions": [WITHIN], "appears_in": ["a"]},
    })
    store = hidden.load(vault)
    store["hidden"]["gone"] = {"at": "2026-10-01T00:00:00", "by": "test", "name": "Gone Ltd.", "type": "organization"}
    hidden.save(vault, store)
    assert [i["title"].split(" — ")[0] for i in triage.start_here(vault)] == ["Kept Ltd."]


# ── what a batch added ─────────────────────────────────────────────────────────────────────────

def test_added_between_is_by_kind_and_none_without_a_before():
    after = {"contradictions": ["c1", "c2"], "leads": ["l1"]}
    assert triage.added_between({"contradictions": ["c1"]}, after) == {
        "contradictions": ["c2"], "leads": ["l1"], "alerts": [], "duplicates": [], "merges": []}
    assert triage.added_between(None, after) is None


def _briefing(vault, stem):
    (vault / "briefings").mkdir(exist_ok=True)
    (vault / "briefings" / f"{stem}.md").write_text("# Briefing\n")
    side = vault / ".watchdog" / "briefings"
    side.mkdir(parents=True, exist_ok=True)
    (side / f"{stem}.json").write_text(json.dumps({"version": 1, "status": "Where things stand."}))
    return vault / "briefings" / f"{stem}.md"


def _contradiction(vault, eid, callouts):
    path = vault / ".watchdog" / "registry" / "entities.json"
    ents = json.loads(path.read_text())
    ents[eid] = {"name": eid.title(), "type": "organization", "note_path": f"entities/organization/{eid}",
                 "contradictions": callouts, "appears_in": ["a"]}
    path.write_text(json.dumps(ents))


def test_record_batch_counts_what_each_batch_added(tmp_path):
    vault = make_vault(tmp_path)
    _contradiction(vault, "alpha", [CROSS])
    first = _briefing(vault, "2026-10-01-09-00")
    rec = triage.record_batch(vault, "briefings/2026-10-01-09-00.md")
    assert len(rec["added"]["contradictions"]) == 1        # the first briefing: all of it is new
    assert triage.batch_delta(vault, first)["added"]["contradictions"] == 1

    _contradiction(vault, "beta", [WITHIN])
    second = _briefing(vault, "2026-10-02-09-00")
    triage.record_batch(vault, "briefings/2026-10-02-09-00.md")
    delta = triage.batch_delta(vault, second)
    assert delta["briefing"] == "2026-10-02-09-00"
    assert delta["added"] == {"contradictions": 1, "leads": 0, "alerts": 0, "duplicates": 0, "merges": 0}
    status = json.loads((vault / ".watchdog" / "briefings" / "2026-10-02-09-00.json").read_text())["status"]
    assert status == "Where things stand."                  # the rest of the sidecar is kept


def test_a_handled_item_brought_back_is_not_new(tmp_path):
    vault = make_vault(tmp_path)
    _contradiction(vault, "alpha", [CROSS])
    _briefing(vault, "2026-10-01-09-00")
    triage.record_batch(vault, "briefings/2026-10-01-09-00.md")
    rid = open_items(vault, ("contradictions",))[0]["rid"]
    resolutions.resolve(vault, [rid])
    resolutions.unresolve(vault, [rid])
    second = _briefing(vault, "2026-10-02-09-00")
    triage.record_batch(vault, "briefings/2026-10-02-09-00.md")
    assert triage.batch_delta(vault, second)["added"]["contradictions"] == 0


def test_no_delta_after_a_briefing_written_before_the_record(tmp_path):
    vault = make_vault(tmp_path)
    _briefing(vault, "2026-10-01-09-00")                    # an older briefing: no `review` record
    second = _briefing(vault, "2026-10-02-09-00")
    rec = triage.record_batch(vault, "briefings/2026-10-02-09-00.md")
    assert rec["added"] is None
    assert triage.batch_delta(vault, second) is None
    assert triage.batch_delta(vault, None) is None


def test_a_run_records_what_its_batch_added(tmp_path, wdg_home):
    """The demo builds through the real pipeline: two batches, each with a record."""
    from watchdog import overview
    from watchdog.gui import demo
    vault = demo.build(tmp_path / "demo")
    latest, earlier = overview.run_briefings(vault)[:2]
    assert triage.batch_delta(vault, earlier) and triage.batch_delta(vault, latest)
    side = vault / ".watchdog" / "briefings"
    added = set()
    for b in (earlier, latest):
        rec = json.loads((side / f"{b.stem}.json").read_text())["review"]
        added |= {rid for ids in rec["added"].values() for rid in ids}
    # The checks and loose ends the demo adds after its runs are not Review items, so between
    # them the two batches added everything now open (a lead the second batch profiled is gone).
    assert {rid for ids in triage.open_ids(vault).values() for rid in ids} <= added


# ── Review's last-opened time ──────────────────────────────────────────────────────────────────

def test_mark_opened_returns_the_previous_time(tmp_path):
    vault = make_vault(tmp_path)
    assert triage.last_opened(vault) is None
    assert triage.mark_opened(vault, "2026-10-01T10:00:00+00:00") == {
        "opened_at": "2026-10-01T10:00:00+00:00", "previous": None}
    assert triage.mark_opened(vault, "2026-10-03T10:00:00+00:00")["previous"] == "2026-10-01T10:00:00+00:00"
    assert triage.last_opened(vault) == "2026-10-03T10:00:00+00:00"
    assert not (vault / ".watchdog" / "registry" / triage.SEEN_FILE).exists()   # not a registry file


def test_new_since_counts_only_open_items_from_later_batches(tmp_path):
    vault = make_vault(tmp_path)
    _contradiction(vault, "alpha", [CROSS])
    _briefing(vault, "2026-10-01-09-00")
    rec1 = triage.record_batch(vault, "briefings/2026-10-01-09-00.md")
    _set_at(vault, "2026-10-01-09-00", "2026-10-01T09:00:00+00:00")
    _contradiction(vault, "beta", [WITHIN])
    _briefing(vault, "2026-10-05-09-00")
    triage.record_batch(vault, "briefings/2026-10-05-09-00.md")
    _set_at(vault, "2026-10-05-09-00", "2026-10-05T09:00:00+00:00")
    assert rec1

    assert triage.new_since(vault, None) is None
    seen = triage.new_since(vault, "2026-10-03T00:00:00+00:00")
    assert seen["added"]["contradictions"] == 1
    assert triage.new_since(vault, "2026-09-01T00:00:00+00:00")["added"]["contradictions"] == 2
    beta = [i["rid"] for i in open_items(vault, ("contradictions",)) if i["entity"] == "beta"]
    resolutions.resolve(vault, beta)
    assert triage.new_since(vault, "2026-10-03T00:00:00+00:00")["added"]["contradictions"] == 0


def _set_at(vault, stem, at):
    p = vault / ".watchdog" / "briefings" / f"{stem}.json"
    data = json.loads(p.read_text())
    data["review"]["at"] = at
    p.write_text(json.dumps(data))


def test_review_opened_and_new_labels_through_the_app(tmp_path):
    vault = make_vault(tmp_path)
    _contradiction(vault, "alpha", [CROSS])
    _briefing(vault, "2026-10-01-09-00")
    triage.record_batch(vault, "briefings/2026-10-01-09-00.md")
    _set_at(vault, "2026-10-01-09-00", "2026-10-01T09:00:00+00:00")

    first = call("review.opened", vault=str(vault))
    assert first["previous"] is None
    r = call("review.items", vault=str(vault), since="2026-09-30T00:00:00+00:00")
    assert [i["new"] for i in r["items"]] == [True]
    assert [i["rid"] for i in r["start_here"]] == [r["items"][0]["rid"]]
    r = call("review.items", vault=str(vault), since=first["opened_at"])
    assert [i["new"] for i in r["items"]] == [False]
    assert call("review.opened", vault=str(vault))["previous"] == first["opened_at"]
    s = call("vault.summary", vault=str(vault))
    assert s["since_review"]["added"]["contradictions"] == 0
    assert s["batch"]["added"]["contradictions"] == 1
