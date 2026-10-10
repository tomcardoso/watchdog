"""`projects.*` handlers."""

import json

from tests.gui_support import call, call_error, register
from tests.test_write_vault import make_vault

pytest_plugins = ["tests.gui_support"]   # the rich_vault / wdg_home fixtures


def test_list_is_empty_without_a_registry(wdg_home):
    assert call("projects.list") == []
    assert call("projects.list", all=True) == []


def test_list_hides_archived_unless_asked(rich_vault, wdg_home, tmp_path):
    other = make_vault(tmp_path / "other")
    register(wdg_home, rich_vault, slug="zeta", name="Zeta Case")
    register(wdg_home, other, slug="alpha", name="alpha case", archived=True)
    assert [p["slug"] for p in call("projects.list")] == ["zeta"]
    assert [p["slug"] for p in call("projects.list", all=True)] == ["alpha", "zeta"]


def test_project_shape_and_stats(rich_vault, wdg_home):
    register(wdg_home, rich_vault)
    (p,) = call("projects.list")
    assert p == {
        "slug": "rich", "name": "Rich Case", "description": "A test case", "path": str(rich_vault),
        "archived": False, "created": "2026-01-02T03:04:05", "health": None, "access": True,
        "stats": {"documents": 3, "entities": 3, "last_ingest": "2026-03-03T08:00:00Z",
                  "incoming": 2, "awaiting": 2, "failed": 1, "history_bytes": p["stats"]["history_bytes"]},
    }
    from watchdog.pipeline import history
    assert p["stats"]["history_bytes"] == history.history_bytes(rich_vault)


def test_health_missing_not_a_vault_and_corrupt(rich_vault, wdg_home, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    corrupt = make_vault(tmp_path / "corrupt")
    (corrupt / ".watchdog" / "registry" / "registry.json").write_text("{not json")
    register(wdg_home, tmp_path / "gone", slug="gone", name="Gone")
    register(wdg_home, plain, slug="plain", name="Plain")
    register(wdg_home, corrupt, slug="corrupt", name="Corrupt")
    by_slug = {p["slug"]: p for p in call("projects.list")}
    assert by_slug["gone"]["health"] == "missing"
    assert by_slug["plain"]["health"] == "not_a_vault"
    assert by_slug["corrupt"]["health"] == "registry_corrupt"
    for slug in ("gone", "plain"):
        assert by_slug[slug]["stats"] == {"documents": 0, "entities": 0, "last_ingest": None,
                                          "incoming": 0, "awaiting": 0, "failed": 0,
                                          "history_bytes": None}


def test_a_vault_with_no_registry_yet_reads_as_empty(wdg_home, tmp_path):
    vault = tmp_path / "fresh"
    (vault / ".watchdog" / "queue").mkdir(parents=True)
    register(wdg_home, vault, slug="fresh", name="Fresh")
    p = call("projects.get", slug="fresh")
    assert p["health"] is None and p["stats"]["documents"] == 0 and p["stats"]["last_ingest"] is None


def test_get_exact_prefix_unknown_and_ambiguous(rich_vault, wdg_home, tmp_path):
    register(wdg_home, rich_vault, slug="shell-co", name="Shell Co")
    register(wdg_home, make_vault(tmp_path / "b"), slug="shell-bank", name="Shell Bank")
    assert call("projects.get", slug="shell-co")["name"] == "Shell Co"
    assert call("projects.get", slug="shell-c")["slug"] == "shell-co"
    assert call_error("projects.get", slug="shell")["code"] == "ambiguous"
    assert call_error("projects.get", slug="nope")["code"] == "not_found"
    assert call_error("projects.get", slug="")["code"] == "not_found"


def test_for_path(rich_vault, wdg_home, tmp_path):
    register(wdg_home, rich_vault)
    assert call("projects.forPath", path=str(rich_vault))["slug"] == "rich"
    assert call("projects.forPath", path=str(rich_vault) + "/")["slug"] == "rich"
    assert call("projects.forPath", path=str(tmp_path)) is None
    assert call("projects.forPath", path="") is None


def test_status(rich_vault, wdg_home):
    register(wdg_home, rich_vault)
    (rich_vault / ".watchdog" / ".preprocessing-lock").write_text("pid: cli\n")
    s = call("projects.status", slug="rich")
    assert s["project"]["slug"] == "rich"
    assert s["by_type"] == {"person": 2, "organization": 1}
    assert s["documents_by_type"] == {"Annual Report": 4}
    assert s["locks"] == {"chew": {"where": "unknown", "host": None, "started_at": None}, "ingest": None}
    assert s["pending_finalization"] == {"docs": 0, "entities": 0}
    assert s["size_bytes"] > 1000


def test_status_of_a_missing_folder_is_empty_not_an_error(wdg_home, tmp_path):
    register(wdg_home, tmp_path / "gone", slug="gone", name="Gone")
    s = call("projects.status", slug="gone")
    assert s["project"]["health"] == "missing" and s["by_type"] == {} and s["size_bytes"] == 0
    assert s["locks"] == {"chew": None, "ingest": None} and s["pending_finalization"] is None


def test_log_returns_the_last_lines(rich_vault, wdg_home):
    register(wdg_home, rich_vault)
    assert call("projects.log", slug="rich")["lines"][-1].endswith("OK report-one.pdf: 3p")
    assert len(call("projects.log", slug="rich", lines=2)["lines"]) == 2
    assert call("projects.log", slug="rich", lines=0)["lines"] == []


def test_log_of_a_vault_without_a_log(wdg_home, tmp_path):
    register(wdg_home, tmp_path / "gone", slug="gone", name="Gone")
    assert call("projects.log", slug="gone") == {"lines": []}


def test_doctor_reports_the_three_kinds_of_problem(rich_vault, wdg_home, tmp_path):
    old = make_vault(tmp_path / "old")
    (old / ".watchdog" / "registry" / "registry.json").write_text(json.dumps({"schema_version": "0"}))
    corrupt = make_vault(tmp_path / "corrupt")
    (corrupt / ".watchdog" / "registry" / "registry.json").write_text("{nope")
    register(wdg_home, rich_vault, slug="fine", name="A fine one")
    register(wdg_home, tmp_path / "gone", slug="gone", name="B gone")
    register(wdg_home, old, slug="old", name="C old")
    register(wdg_home, corrupt, slug="corrupt", name="D corrupt")
    issues = call("projects.doctor")["issues"]
    assert [(i["slug"], i["kind"]) for i in issues] == [
        ("gone", "missing"), ("old", "schema"), ("corrupt", "corrupt_registry")]
    gone = issues[0]
    assert gone["name"] == "B gone" and "Folder not found" in gone["problem"]
    assert "watchdog projects move gone" in gone["suggestion"]
    assert issues[1]["problem"] == "Schema v0 — current is v1"
    assert "registry.json" in issues[2]["problem"]


def test_doctor_with_nothing_registered(wdg_home):
    assert call("projects.doctor") == {"issues": []}
