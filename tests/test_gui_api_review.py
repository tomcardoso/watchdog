"""`review.*` handlers — the same items and resolution store as `watchdog review`."""

import json

from watchdog.cmd.review import open_items
from watchdog.pipeline import resolutions

from tests.gui_support import CALLOUT, call, call_error
from tests.test_write_vault import make_vault

pytest_plugins = ["tests.gui_support"]   # the rich_vault / wdg_home fixtures


def V(vault):
    return str(vault)


GHOST = "lead:unprofiled:ghost-ltd"


def test_items_match_the_terminal_review(rich_vault):
    r = call("review.items", vault=V(rich_vault))
    assert r["items"] == open_items(rich_vault)
    assert r["counts"] == {"contradictions": 1, "leads": 2, "alerts": 1, "duplicates": 1}
    first = r["items"][0]
    assert set(first) == {"kind", "rid", "title", "detail", "note"}
    assert first["title"] == "Jane Doe — Start date of Jane Doe's directorship"
    assert first["note"] == "entities/person/jane-doe"
    json.dumps(r)


def test_items_can_be_filtered_by_kind(rich_vault):
    r = call("review.items", vault=V(rich_vault), kinds=["alerts", "duplicates"])
    assert {i["kind"] for i in r["items"]} == {"alerts", "duplicates"}
    assert r["counts"] == {"alerts": 1, "duplicates": 1}
    assert call_error("review.items", vault=V(rich_vault), kinds=["gossip"])["code"] == "bad_params"


def test_items_on_an_empty_vault(tmp_path):
    r = call("review.items", vault=V(make_vault(tmp_path)))
    assert r == {"items": [], "counts": {"contradictions": 0, "leads": 0, "alerts": 0, "duplicates": 0}}


def test_resolve_writes_the_store_and_ticks_the_briefing(rich_vault):
    briefing = rich_vault / "briefings" / "2026-03-01-10-30.md"
    assert "- [ ] Check Ghost Ltd" in briefing.read_text()
    assert call("review.resolve", vault=V(rich_vault), rids=[GHOST]) == {"resolved": [GHOST]}
    assert resolutions.load(rich_vault)["resolved"][GHOST]["label"] == "review"
    assert "- [x] Check Ghost Ltd" in briefing.read_text()
    assert GHOST not in {i["rid"] for i in call("review.items", vault=V(rich_vault))["items"]}
    assert call("review.resolve", vault=V(rich_vault), rids=[GHOST]) == {"resolved": []}   # already handled


def test_resolve_several_kinds_at_once(rich_vault):
    items = call("review.items", vault=V(rich_vault))["items"]
    rids = [i["rid"] for i in items]
    assert sorted(call("review.resolve", vault=V(rich_vault), rids=rids)["resolved"]) == sorted(rids)
    assert call("review.items", vault=V(rich_vault))["items"] == []
    assert call("review.items", vault=V(rich_vault))["counts"] == {
        "contradictions": 0, "leads": 0, "alerts": 0, "duplicates": 0}


def test_unresolve_reopens_and_unticks(rich_vault):
    briefing = rich_vault / "briefings" / "2026-03-01-10-30.md"
    call("review.resolve", vault=V(rich_vault), rids=[GHOST])
    assert call("review.unresolve", vault=V(rich_vault), rids=[GHOST, "lead:isolated:nobody"]) == {
        "unresolved": [GHOST]}
    assert "- [ ] Check Ghost Ltd" in briefing.read_text()
    assert GHOST in {i["rid"] for i in call("review.items", vault=V(rich_vault))["items"]}


def test_resolved_lists_what_was_handled_newest_first(rich_vault):
    resolutions.resolve(rich_vault, ["alert:abc:def"], label="checkbox")
    store = resolutions.load(rich_vault)
    store["resolved"]["alert:abc:def"]["at"] = "2030-01-01T00:00:00"
    resolutions.save(rich_vault, store)
    items = call("review.resolved", vault=V(rich_vault))["items"]
    assert items[0] == {"rid": "alert:abc:def", "label": "checkbox", "resolved_at": "2030-01-01T00:00:00",
                        "kind": "alerts"}
    assert {i["kind"] for i in items} == {"alerts", "leads", "requests"}


def test_sync_imports_ticked_boxes_from_the_briefings(rich_vault):
    briefing = rich_vault / "briefings" / "2026-03-01-10-30.md"
    briefing.write_text(briefing.read_text().replace("- [ ] Check", "- [x] Check"))
    assert call("review.sync", vault=V(rich_vault)) == {"resolved": [GHOST], "unresolved": []}
    briefing.write_text(briefing.read_text().replace("- [x] Check", "- [ ] Check"))
    assert call("review.sync", vault=V(rich_vault)) == {"resolved": [], "unresolved": [GHOST]}


def test_rid_validation(rich_vault):
    for bad in ([], [""], [1], None, {"a": 1}):
        assert call_error("review.resolve", vault=V(rich_vault), rids=bad)["code"] == "bad_params"
    assert call_error("review.unresolve", vault=V(rich_vault), rids=[])["code"] == "bad_params"
    assert call("review.resolve", vault=V(rich_vault), rids=GHOST) == {"resolved": [GHOST]}   # a lone id is fine


def test_leads_is_the_full_sweep(rich_vault):
    r = call("review.leads", vault=V(rich_vault))
    assert [u["id"] for u in r["unprofiled"]] == ["ghost-ltd"] and r["unprofiled"][0]["mentioned_by"] == ["Acme Corp"]
    assert r["contradictions"][0]["callouts"][0]["text"] == CALLOUT
    assert r["inferred"][0]["claims"] == ["subsidiary of → Ghost Ltd"]
    assert r["isolated"] == [] and r["total"] == 3          # bob-roe's lead was resolved in the fixture
    resolutions.unresolve(rich_vault, [resolutions.lead_id("isolated", "bob-roe")])
    assert call("review.leads", vault=V(rich_vault))["isolated"][0]["id"] == "bob-roe"


def test_watchlist(rich_vault):
    r = call("review.watchlist", vault=V(rich_vault))
    assert r["terms"] == ["Ghost Ltd", "/Roe,?\\s+Bob/"] and r["text"].startswith("# Watch list")
    (rich_vault / "watchlist.md").unlink()
    assert call("review.watchlist", vault=V(rich_vault)) == {"terms": [], "text": ""}


def test_merge_preview(rich_vault):
    r = call("review.mergePreview", vault=V(rich_vault), keep="acme-corp", merge="jane-doe")
    assert r["keep"]["id"] == "acme-corp" and r["merge"]["id"] == "jane-doe"
    assert r["both_have_summary"] is True and r["type_mismatch"] is True
    r = call("review.mergePreview", vault=V(rich_vault), keep="jane-doe", merge="bob-roe")
    assert r["both_have_summary"] is False and r["type_mismatch"] is False
    assert call_error("review.mergePreview", vault=V(rich_vault), keep="jane-doe", merge="jane-doe")["code"] == "bad_params"
    assert call_error("review.mergePreview", vault=V(rich_vault), keep="jane-doe", merge="ghost")["code"] == "not_found"
