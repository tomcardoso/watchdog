"""The sample investigation (D303): the `sample-create` operation that builds "Port Calder
Waterfront (sample)" for new users, and the findings the app's guided tour points at.

The tour opens real things in the sample — a scanned page, an entity with checked facts, a
contradiction, a near-duplicate pair, a lead, files waiting to be added — so a change to the demo
story or the pipeline that removes one of them must fail here, not in front of a new user."""

import json
import socket
from pathlib import Path

import pytest

from watchdog import ops, projects, telemetry_db
from watchdog.ops import CollectingReporter
from watchdog.ops import sample as sample_op

pytest_plugins = ["tests.gui_support"]   # the wdg_home fixture


def _no_network(*_a, **_k):
    raise AssertionError("the sample tried to open a network connection")


def _home(mp: pytest.MonkeyPatch, root: Path) -> Path:
    """Watchdog's own folder, Obsidian's list and the usage database under `root`, with no keys
    and no Claude sign-in, and no network at all."""
    home = root / "wdg-home"
    home.mkdir(parents=True)
    investigations = root / "Investigations"
    investigations.mkdir()
    (home / "config.json").write_text(json.dumps({"projects_dir": str(investigations)}) + "\n")
    mp.setattr("watchdog.config.HOME", home)
    mp.setattr("watchdog.obsidian.config_path", lambda: root / "obsidian.json")
    mp.setattr(telemetry_db, "DB_PATH", root / "telemetry.db")
    mp.setattr(telemetry_db, "enabled", lambda: True)
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY",
                "OPENROUTER_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        mp.delenv(var, raising=False)
    mp.setattr("watchdog.auth.claude_code_logged_in", lambda: False)
    mp.setattr(socket.socket, "connect", _no_network)
    mp.setattr(socket, "create_connection", _no_network)
    return home


def _create(**params):
    rep = CollectingReporter()
    return ops.run("sample-create", params, rep), rep


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("sample")
    mp = pytest.MonkeyPatch()
    try:
        home = _home(mp, root)
        result, rep = _create()
        yield {"root": root, "home": home, "vault": Path(result["path"]), "result": result, "rep": rep}
    finally:
        telemetry_db.close()
        mp.undo()


def test_builds_the_labelled_sample_in_the_investigations_folder(built):
    vault, result = built["vault"], built["result"]
    assert vault.parent == built["root"] / "Investigations"
    assert result["name"] == "Port Calder Waterfront (sample)"
    entry = json.loads((built["home"] / "projects.json").read_text())[result["slug"]]
    assert entry["sample"] is True
    assert "every person and company in it is invented" in entry["description"]
    assert sample_op.is_sample_folder(vault)
    # Progress the job dock shows, one message per phase.
    assert [e["kind"] for e in built["rep"].events] == ["message"] * 5
    assert not built["rep"].warnings
    # The app owns the settings and folder-access files: the sample leaves them alone.
    assert not (built["home"] / "access.json").exists()
    assert json.loads((built["home"] / "config.json").read_text()) == {
        "projects_dir": str(built["root"] / "Investigations")}
    # Listed with Obsidian at its final path, like any investigation.
    assert str(vault) in (built["root"] / "obsidian.json").read_text()


def test_sample_costs_nothing_and_stays_out_of_the_usage_database(built):
    usage = sorted((built["vault"] / ".watchdog" / "registry" / "usage").glob("usage-*.json"))
    assert usage
    calls = [c for f in usage for c in json.loads(f.read_text())["calls"]]
    assert calls and all(c["cost_usd"] == 0 and c["backend"] == c["model"] == "sample" for c in calls)
    db = built["root"] / "telemetry.db"
    if db.exists():
        import sqlite3
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0


def test_every_finding_the_tour_points_at_exists(built):
    """The live check borrowed from #366: what each tour step opens is really in the sample."""
    from watchdog.gui.api.sample import tour_targets
    from watchdog.pipeline import text_positions
    vault = built["vault"]
    t = tour_targets(vault)
    assert all(v is not None for v in t.values()), {k for k, v in t.items() if v is None}

    # Overview: a briefing, which surfaces a contradiction and leads.
    briefings = sorted((vault / "briefings").glob("20*.md"))
    assert len(briefings) == 2
    assert list((vault / "briefings").glob("leads-*.md"))

    # A scanned PDF page with recognized text, holding the word the tour types into Find.
    scanned = t["scanned"]
    assert scanned["filename"].endswith(".pdf")
    boxes = text_positions.read(vault, scanned["sha"], [scanned["page"]])["boxes"][str(scanned["page"])]
    assert any(scanned["find"] in line[4].lower() for line in boxes["lines"])

    # An entity with a verified fact that carries its source passage, and a contradiction.
    from watchdog.gui.api import vault as vault_api
    page = vault_api.entity(str(vault), t["entity"]["id"])
    fact = next(f for f in page["facts"] if f["id"] == t["entity"]["fact"])
    assert fact["mark"]["status"] == "verified" and fact["passage"]
    assert page["contradiction_count"] >= 1

    # Review: an open contradiction, the near-duplicate pair and a lead.
    assert t["contradiction"]["rid"].startswith("contradiction:")
    assert t["duplicate"]["rid"].startswith("duplicate:")
    assert t["lead"]["rid"].startswith("lead:")

    # Search finds the exact wording the tour suggests.
    from watchdog.pipeline import fulltext
    assert fulltext.search(vault, t["search"], limit=5)

    # Files waiting in incoming, for the Add documents step, and the runs Usage lists.
    assert t["incoming"] == ["pier-9-change-order-3.pdf", "site-meeting-notes-2022-09-14.docx"]
    assert t["runs"] >= 2


def test_tour_rpc_and_status(built, monkeypatch):
    from tests.gui_support import call
    monkeypatch.setattr("watchdog.config.HOME", built["home"])
    status = call("sample.status")
    assert status == {"slug": built["result"]["slug"], "path": str(built["vault"]), "listed": True,
                      "exists": True, "archived": False}
    assert call("sample.tour", vault=str(built["vault"]))["entity"]["id"]
    listed = call("projects.list")
    assert [p["sample"] for p in listed] == [True]


def test_refuses_to_clobber_an_existing_sample(built, monkeypatch):
    monkeypatch.setattr("watchdog.config.HOME", built["home"])
    before = (built["vault"] / ".watchdog" / "sample.json").read_text()
    with pytest.raises(SystemExit) as e:
        _create()
    assert "already exists" in str(e.value.code)
    assert (built["vault"] / ".watchdog" / "sample.json").read_text() == before


def test_rebuild_replaces_only_the_sample(wdg_home, tmp_path, monkeypatch):
    investigations = tmp_path / "Investigations"
    investigations.mkdir()
    (wdg_home / "config.json").write_text(json.dumps({"projects_dir": str(investigations)}) + "\n")
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    from watchdog.ops import projects as projects_op
    mine = projects_op.create("Harbour contracts", parent=investigations)
    (mine / "context" / "notes.md").write_text("my notes")

    first, _ = _create()
    vault = Path(first["path"])
    (vault / "context" / "my-change.md").write_text("changed in the sample")
    second, rep = _create(rebuild=True)
    assert second["path"] == first["path"]
    assert not (vault / "context" / "my-change.md").exists()
    assert sample_op.is_sample_folder(vault)
    # The other investigation is untouched and still listed.
    assert (mine / "context" / "notes.md").read_text() == "my notes"
    assert set(projects.load()) == {"harbour-contracts", first["slug"]}
    assert any("Removing the old sample" in line for line in rep.lines)


def test_rebuild_never_deletes_a_folder_that_is_not_the_sample(wdg_home, tmp_path):
    investigations = tmp_path / "Investigations"
    investigations.mkdir()
    (wdg_home / "config.json").write_text(json.dumps({"projects_dir": str(investigations)}) + "\n")
    squatter = investigations / sample_op.slug()
    squatter.mkdir()
    (squatter / "keep.txt").write_text("mine")
    with pytest.raises(SystemExit):
        _create(rebuild=True)
    assert (squatter / "keep.txt").read_text() == "mine"

    # Listed as the sample, but the folder it names is not one: refused, nothing deleted.
    from watchdog.ops import projects as projects_op
    other = projects_op.create("Someone else", parent=tmp_path)
    listed = projects.load()
    listed["someone-else"]["sample"] = True
    projects.save(listed)
    with pytest.raises(SystemExit):
        _create(rebuild=True)
    assert other.exists()


def test_deleting_the_sample_works_like_any_investigation(wdg_home, tmp_path):
    investigations = tmp_path / "Investigations"
    investigations.mkdir()
    (wdg_home / "config.json").write_text(json.dumps({"projects_dir": str(investigations)}) + "\n")
    made, _ = _create()
    ops.run("projects-delete", {"slug": made["slug"], "purge": True}, CollectingReporter())
    assert not Path(made["path"]).exists()
    assert made["slug"] not in projects.load()
    # And it can be built again afterwards.
    again, _ = _create()
    assert Path(again["path"]).exists()
