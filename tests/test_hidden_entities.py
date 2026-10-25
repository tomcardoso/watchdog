"""Removing an entity from an investigation (#753, D326): a lasting list the views leave out,
never an edit to what a source says."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from watchdog import ops
from watchdog.ops import CollectingReporter
from watchdog.pipeline import entity_facts, hidden, history, leads, merge_entities, merge_log, merge_undo

from tests.gui_support import call
from tests.test_write_vault import make_extraction, make_vault

SRC = Path(__file__).resolve().parent.parent / "src"


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    root = tmp_path_factory.mktemp("demo-hidden")
    vault = root / "vault"
    env = {**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"}
    proc = subprocess.run([sys.executable, "-m", "watchdog.gui.demo", str(vault), "--home", str(root / "home")],
                          env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return vault


@pytest.fixture
def vault(demo, tmp_path, monkeypatch):
    copy = tmp_path / "v"
    shutil.copytree(demo, copy)
    monkeypatch.setattr("watchdog.config.HOME", tmp_path / "home")
    entity_facts.clear_cache()
    return copy


def _entities(vault):
    return json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())


def _hide(vault, *ids):
    return ops.run("hide-entities", {"ids": list(ids)}, CollectingReporter(), vault)


def _unhide(vault, *ids):
    return ops.run("unhide-entities", {"ids": list(ids)}, CollectingReporter(), vault)


# ── the operation and its record ──────────────────────────────────────────────────────────

def test_hide_records_who_when_and_what_and_bring_back_reverses_it(vault, monkeypatch):
    monkeypatch.setattr("watchdog.pipeline.verification.reporter_name", lambda: "Test Reporter")
    rep = CollectingReporter()
    out = ops.run("hide-entities", {"ids": ["port-calder-city-hall"]}, rep, vault)
    assert out["hidden"] == ["port-calder-city-hall"] and "Bring back" in rep.text
    rec = json.loads(hidden.path(vault).read_text())
    assert rec["schema_version"] == 1
    entry = rec["hidden"]["port-calder-city-hall"]
    assert entry["by"] == "Test Reporter" and entry["at"]
    assert entry["name"] == "Port Calder City Hall" and entry["type"] == "place"
    # Hiding again changes nothing; the history records one version with a plain label.
    assert _hide(vault, "port-calder-city-hall")["hidden"] == []
    rows = history.file_history(vault, ".watchdog/registry/hidden.json")["versions"]
    assert [r["label"] for r in rows] == ["Removed from the investigation: Port Calder City Hall (by you)"]

    listed = call("vault.hiddenEntities", vault=str(vault))["items"]
    assert [(i["id"], i["exists"], i["by"]) for i in listed] == [("port-calder-city-hall", True, "Test Reporter")]
    ent = call("vault.entity", vault=str(vault), id="port-calder-city-hall")   # still opens
    assert ent["hidden"]["by"] == "Test Reporter"

    assert _unhide(vault, "port-calder-city-hall")["shown"] == ["port-calder-city-hall"]
    assert hidden.hidden_ids(vault) == frozenset()
    assert call("vault.entity", vault=str(vault), id="port-calder-city-hall")["hidden"] is None
    assert any(r["id"] == "port-calder-city-hall" for r in call("vault.entities", vault=str(vault)))


def test_history_labels_say_what_was_removed_and_brought_back():
    assert history.label({"kind": "hide", "names": ["A", "B"], "count": 3}) == \
        "Removed from the investigation: A, B and 1 more (by you)"
    assert history.label({"kind": "unhide", "names": ["A"], "count": 1}) == "Brought back: A (by you)"
    assert "hidden.json" in history.REGISTRY_FILES


def test_an_unknown_id_or_an_empty_list_is_refused(vault):
    with pytest.raises(SystemExit, match="no entity “nobody-at-all”"):
        _hide(vault, "nobody-at-all")
    with pytest.raises(SystemExit, match="at least one"):
        _hide(vault)
    assert not hidden.path(vault).exists()
    assert _unhide(vault, "nobody-at-all")["not_listed"] == ["nobody-at-all"]


def test_a_locked_registry_is_refused_as_a_merge_is(vault, monkeypatch):
    """The registry lock (D258) is the merge's lock: where it gives up (Windows, after ~10 s of
    contention with a run's commit) both operations refuse with the same advice."""
    import contextlib

    @contextlib.contextmanager
    def busy(_dir, name=".write-lock"):
        raise OSError("lock held")
        yield

    monkeypatch.setattr("watchdog.pipeline.write_vault._registry_lock", busy)
    with pytest.raises(SystemExit, match="wait for that to finish"):
        _hide(vault, "pier-9")
    with pytest.raises(SystemExit, match="wait for that to finish"):
        ops.run("merge-entities", {"keep": "pier-9", "merge": "calder-harbour"}, CollectingReporter(), vault)
    assert not hidden.path(vault).exists()


# ── every view leaves a removed entity out ────────────────────────────────────────────────

def test_a_removed_entity_drops_out_of_every_view_and_count(vault):
    v = str(vault)
    before = call("vault.summary", vault=v)
    graph = call("vault.graph", vault=v)
    assert any(n["id"] == "pier-9" for n in graph["nodes"])
    assert any("pier-9" in (e["source"], e["target"]) for e in graph["edges"])
    assert before["possible_same"] == 1
    exact = call("search.query", vault=v, query="Leonard Pike", top=20)["exact"]
    assert any(h["kind"] == "entity" and h["path"].endswith("leonard-pike") for h in exact)

    # Pier 9 carries contradictions; Tomasz Wieczorek's second record is half of the one
    # "possible same" pair; the bond is the only entity naming an unprofiled surety company.
    _hide(vault, "pier-9", "tomasz-wieczorek-2", "performance-bond-pb-88213", "leonard-pike")

    graph = call("vault.graph", vault=v)
    gone = {"pier-9", "tomasz-wieczorek-2", "performance-bond-pb-88213", "leonard-pike"}
    assert not gone & {n["id"] for n in graph["nodes"]}
    assert not any(gone & {e["source"], e["target"]} for e in graph["edges"])
    assert not gone & {r["id"] for r in call("vault.entities", vault=v)}

    s = call("vault.summary", vault=v)
    assert s["totals"]["entities"] == before["totals"]["entities"] - 4
    assert s["hidden_entities"] == 4
    assert not gone & {r["id"] for r in s["top_entities"]}
    assert s["possible_same"] == 0
    assert s["contradictions"] < before["contradictions"]
    assert s["leads"] == before["leads"] - 1          # the surety company is no longer a lead

    found = leads.scan(vault)
    assert "pier-9" not in {c["id"] for c in found["contradictions"]}
    assert "harbourgate-surety-company" not in {u["id"] for u in found["unprofiled"]}
    items = call("review.items", vault=v)
    assert items["counts"]["merges"] == 0
    assert not any(i["title"].startswith("Pier 9 —") for i in items["items"])

    # Search keeps the documents that name him and drops only his entity note.
    exact = call("search.query", vault=v, query="Leonard Pike", top=20)["exact"]
    assert exact and not any(h["kind"] == "entity" and h["path"].endswith("leonard-pike") for h in exact)
    assert any(h["kind"] == "document" for h in exact)
    batch = call("search.batch", vault=v, terms=["Leonard Pike"])["terms"][0]
    assert not any(h["kind"] == "entity" and (h["note"] or "").endswith("leonard-pike") for h in batch["hits"])

    # Bringing them back restores every view.
    _unhide(vault, *gone)
    after = call("vault.summary", vault=v)
    assert {k: after[k] for k in ("contradictions", "leads", "possible_same")} == \
        {k: before[k] for k in ("contradictions", "leads", "possible_same")}
    assert after["totals"]["entities"] == before["totals"]["entities"]


def test_a_removed_entitys_relationships_stay_on_the_other_entitys_page(vault):
    other = call("vault.entity", vault=str(vault), id="northgate-civil-works")
    targets = {r["target_id"] for r in other["relationships"]}
    assert "performance-bond-pb-88213" in targets
    note = vault / "entities" / "organization" / "northgate-civil-works.md"
    text = note.read_text()
    _hide(vault, "performance-bond-pb-88213")
    after = call("vault.entity", vault=str(vault), id="northgate-civil-works")
    assert {r["target_id"] for r in after["relationships"]} == targets
    assert note.read_text() == text


def test_find_leads_skips_a_hidden_entity_and_its_targets():
    reg = {"a": {"name": "A", "appears_in": ["1", "2", "3"], "roles": []},
           "b": {"name": "B", "roles": [{"target_id": "ghost", "relationship": "owns"}],
                 "contradictions": ["> [!contradiction] x\n> y"]},
           "c": {"name": "C", "roles": [{"target_id": "gone", "relationship": "pays"}]}}
    open_ = leads.find_leads(reg)
    assert {u["id"] for u in open_["unprofiled"]} == {"ghost", "gone"} and open_["isolated"]
    hid = leads.find_leads(reg, hidden=frozenset({"a", "b", "gone"}))
    assert hid["unprofiled"] == [] and hid["isolated"] == [] and hid["contradictions"] == []


# ── lasting: re-processing and the identity code ──────────────────────────────────────────

def test_a_removed_id_stays_hidden_when_processing_recreates_it(tmp_path):
    from watchdog.pipeline.write_vault import run
    vault = make_vault(tmp_path)
    (vault / "incoming" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)
    hidden.run_hide(vault, ["acme-corp"], by="Test Reporter")

    # The record goes from the registry (as when its documents are taken out), then the same
    # document is processed again and produces the same id.
    reg = vault / ".watchdog" / "registry" / "entities.json"
    ents = json.loads(reg.read_text())
    del ents["acme-corp"]
    reg.write_text(json.dumps(ents))
    assert hidden.hidden_ids(vault) == {"acme-corp"}
    (vault / "incoming" / "test-doc.pdf").write_text("dummy")
    run(make_extraction(tmp_path), vault)
    assert "acme-corp" in json.loads(reg.read_text())
    assert hidden.hidden_ids(vault) == {"acme-corp"}

    from watchdog.gui import vaultio
    assert "acme-corp" not in {r["id"] for r in vaultio.sorted_entity_rows(vault)}
    assert "acme-corp" not in leads.find_leads(json.loads(reg.read_text()),
                                               hidden=hidden.hidden_ids(vault))["unprofiled"]
    # The document note still lists its facts about it.
    assert "Revenue was $1M." in next((vault / "documents").glob("*.md")).read_text()


def test_a_record_folded_into_a_hidden_one_stays_hidden(vault):
    _hide(vault, "pier-9")
    merge_entities.run(vault, "pier-9", "calder-harbour")
    assert hidden.hidden_ids(vault) == {"pier-9"}
    assert "calder-harbour" not in _entities(vault)
    assert not {"pier-9", "calder-harbour"} & {n["id"] for n in call("vault.graph", vault=str(vault))["nodes"]}


def test_merging_a_hidden_record_into_a_visible_one_keeps_the_survivor_visible_and_undo_restores(vault):
    keep, other = "leonard-pike", "dana-whitcombe"
    _hide(vault, other)
    out = merge_entities.run(vault, keep, other)
    assert out["survivor_hidden"] is False
    assert hidden.hidden_ids(vault) == frozenset()
    assert keep in {r["id"] for r in call("vault.entities", vault=str(vault))}
    m = next(x for x in merge_log.load(vault)["merges"] if x["merged"]["id"] == other
             and x["decided_by"] == "reporter")
    assert m["undo"]["hidden"]["name"] == "Dana Whitcombe"

    split = merge_undo.undo(vault, m["id"])["split_id"]
    assert split == other and hidden.hidden_ids(vault) == {other}
    assert other not in {r["id"] for r in call("vault.entities", vault=str(vault))}
    assert keep in {r["id"] for r in call("vault.entities", vault=str(vault))}


def test_undoing_a_merge_into_a_hidden_survivor_leaves_the_split_record_visible(vault):
    keep, other = "leonard-pike", "dana-whitcombe"
    _hide(vault, keep)
    merge_entities.run(vault, keep, other)
    m = next(x for x in merge_log.load(vault)["merges"] if x["merged"]["id"] == other
             and x["decided_by"] == "reporter")
    assert "hidden" not in m["undo"]
    merge_undo.undo(vault, m["id"])
    assert hidden.hidden_ids(vault) == {keep}
    assert other in {r["id"] for r in call("vault.entities", vault=str(vault))}
