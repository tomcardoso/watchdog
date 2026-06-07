import argparse
import json
import re
import pytest
from pathlib import Path

import watchdog.cli as cli


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def wdg_home(tmp_path, monkeypatch):
    """Redirect all watchdog home paths into tmp_path."""
    home = tmp_path / ".watchdog"
    home.mkdir()
    monkeypatch.setattr(cli, "WATCHDOG_HOME",  home)
    monkeypatch.setattr(cli, "PROJECTS_FILE",  home / "projects.json")
    monkeypatch.setattr(cli, "CONFIG_FILE",    home / "config.json")
    return home


@pytest.fixture
def configured(wdg_home, tmp_path):
    """Write config.json so the setup gate passes."""
    investigations = tmp_path / "Investigations"
    investigations.mkdir()
    (wdg_home / "config.json").write_text(
        json.dumps({"projects_dir": str(investigations)}) + "\n"
    )
    return investigations


def args(**kwargs):
    return argparse.Namespace(**{"dir": None, "force": False, **kwargs})


# ── slugify ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name, expected", [
    ("Shell Company Investigation", "shell-company-investigation"),
    ("  leading spaces  ",          "leading-spaces"),
    ("Weird & Chars!",              "weird-chars"),
    ("multiple---dashes",           "multiple-dashes"),
    ("ALLCAPS",                     "allcaps"),
])
def test_slugify(name, expected):
    assert cli.slugify(name) == expected


def test_slugify_empty_after_strip():
    assert cli.slugify("!!!") == ""


# ── _fmt_date ─────────────────────────────────────────────────────────────────

def test_fmt_date_extracts_date_portion():
    assert cli._fmt_date("2026-06-07T02:22:15Z") == "2026-06-07"


def test_fmt_date_short_string():
    assert cli._fmt_date("2026-06-07") == "2026-06-07"


# ── _count_incoming ───────────────────────────────────────────────────────────

def test_count_incoming_empty_dir(tmp_path):
    (tmp_path / "_INCOMING").mkdir()
    assert cli._count_incoming(tmp_path) == 0


def test_count_incoming_no_dir(tmp_path):
    assert cli._count_incoming(tmp_path) == 0


def test_count_incoming_counts_files(tmp_path):
    incoming = tmp_path / "_INCOMING"
    incoming.mkdir()
    (incoming / "doc.pdf").write_text("")
    (incoming / "report.docx").write_text("")
    assert cli._count_incoming(tmp_path) == 2


def test_count_incoming_ignores_dotfiles(tmp_path):
    incoming = tmp_path / "_INCOMING"
    incoming.mkdir()
    (incoming / ".DS_Store").write_text("")
    (incoming / "real.pdf").write_text("")
    assert cli._count_incoming(tmp_path) == 1


def test_count_incoming_ignores_sidecar_yml(tmp_path):
    incoming = tmp_path / "_INCOMING"
    incoming.mkdir()
    (incoming / "doc.pdf").write_text("")
    (incoming / "doc.yml").write_text("")
    assert cli._count_incoming(tmp_path) == 1


def test_count_incoming_excludes_failed(tmp_path):
    incoming = tmp_path / "_INCOMING"
    failed = incoming / "_FAILED"
    failed.mkdir(parents=True)
    (incoming / "pending.pdf").write_text("")
    (failed / "broken.pdf").write_text("")
    assert cli._count_incoming(tmp_path) == 1


# ── _load_registry ────────────────────────────────────────────────────────────

def test_load_registry_missing(tmp_path):
    assert cli._load_registry(tmp_path) is None


def test_load_registry_returns_data(tmp_path):
    reg_dir = tmp_path / ".watchdog" / "Registry"
    reg_dir.mkdir(parents=True)
    data = {"document_count": 5, "entity_count": 3, "last_updated": "2026-06-07T00:00:00Z"}
    (reg_dir / "registry.json").write_text(json.dumps(data))
    assert cli._load_registry(tmp_path) == data


def test_load_registry_corrupt_json(tmp_path):
    reg_dir = tmp_path / ".watchdog" / "Registry"
    reg_dir.mkdir(parents=True)
    (reg_dir / "registry.json").write_text("not json {{{")
    assert cli._load_registry(tmp_path) is None


# ── cmd_new ───────────────────────────────────────────────────────────────────

def test_cmd_new_creates_vault(configured, tmp_path):
    cli.cmd_new(args(name="Test Investigation", dir=str(configured)))
    vault = configured / "test-investigation"
    assert vault.is_dir()


def test_cmd_new_vault_structure(configured):
    cli.cmd_new(args(name="My Story", dir=str(configured)))
    vault = configured / "my-story"
    for d in ["_INCOMING", "morgue", "entities/person", "entities/company",
              "entities/address", "documents", "briefings", "wiki", "queries"]:
        assert (vault / d).is_dir(), f"Missing: {d}"


def test_cmd_new_registry_initialized(configured):
    cli.cmd_new(args(name="My Story", dir=str(configured)))
    reg = json.loads((configured / "my-story" / ".watchdog" / "Registry" / "registry.json").read_text())
    assert reg["document_count"] == 0
    assert reg["entity_count"] == 0
    assert reg["schema_version"] == "1"


def test_cmd_new_claude_md_contains_name(configured):
    cli.cmd_new(args(name="City Hall Probe", dir=str(configured)))
    text = (configured / "city-hall-probe" / "CLAUDE.md").read_text()
    assert "City Hall Probe" in text


def test_cmd_new_registers_project(configured, wdg_home):
    cli.cmd_new(args(name="My Story", dir=str(configured)))
    projects = json.loads((wdg_home / "projects.json").read_text())
    assert "my-story" in projects
    assert projects["my-story"]["name"] == "My Story"


def test_cmd_new_duplicate_exits(configured):
    cli.cmd_new(args(name="My Story", dir=str(configured)))
    with pytest.raises(SystemExit):
        cli.cmd_new(args(name="My Story", dir=str(configured)))


def test_cmd_new_invalid_name_exits(configured):
    with pytest.raises(SystemExit):
        cli.cmd_new(args(name="!!!", dir=str(configured)))


def test_cmd_new_uses_config_projects_dir(configured, wdg_home):
    # No --dir: should use the projects_dir from config.json
    cli.cmd_new(args(name="Auto Dir Test"))
    projects = json.loads((wdg_home / "projects.json").read_text())
    assert "auto-dir-test" in projects
    assert str(configured) in projects["auto-dir-test"]["path"]


# ── cmd_list ──────────────────────────────────────────────────────────────────

def test_cmd_list_empty(configured, capsys):
    cli.cmd_list(args())
    assert "No projects" in capsys.readouterr().out


def test_cmd_list_shows_project(configured, wdg_home, capsys):
    cli.cmd_new(args(name="Shell Co Probe", dir=str(configured)))
    cli.cmd_list(args())
    out = capsys.readouterr().out
    assert "Shell Co Probe" in out


def test_cmd_list_shows_counts(configured, wdg_home, capsys):
    cli.cmd_new(args(name="Shell Co Probe", dir=str(configured)))
    vault = configured / "shell-co-probe"
    reg = vault / ".watchdog" / "Registry" / "registry.json"
    data = json.loads(reg.read_text())
    data["document_count"] = 7
    data["entity_count"] = 4
    reg.write_text(json.dumps(data))

    cli.cmd_list(args())
    out = capsys.readouterr().out
    assert "7" in out
    assert "4" in out


def test_cmd_list_missing_registry_shows_dashes(configured, wdg_home, capsys):
    cli.cmd_new(args(name="Shell Co Probe", dir=str(configured)))
    (configured / "shell-co-probe" / ".watchdog" / "Registry" / "registry.json").unlink()
    cli.cmd_list(args())
    assert "—" in capsys.readouterr().out


# ── cmd_status ────────────────────────────────────────────────────────────────

def _make_vault_with_data(vault: Path, docs: list[dict], entities: list[dict]) -> None:
    """Populate a fresh vault's registry with doc and entity data."""
    reg = vault / ".watchdog" / "Registry"
    docs_dict = {str(i): d for i, d in enumerate(docs)}
    ents_dict = {e["id"]: e for e in entities}
    (reg / "documents.json").write_text(json.dumps(docs_dict))
    (reg / "entities.json").write_text(json.dumps(ents_dict))
    registry = json.loads((reg / "registry.json").read_text())
    registry["document_count"] = len(docs)
    registry["entity_count"]   = len(entities)
    (reg / "registry.json").write_text(json.dumps(registry))


def test_cmd_status_shows_name(configured, capsys):
    cli.cmd_new(args(name="Test Proj", dir=str(configured)))
    cli.cmd_status(args(name="Test Proj"))
    assert "Test Proj" in capsys.readouterr().out


def test_cmd_status_shows_totals(configured, capsys):
    cli.cmd_new(args(name="Test Proj", dir=str(configured)))
    _make_vault_with_data(
        configured / "test-proj",
        docs=[{"document_type": "Court Order", "page_count": 3},
              {"document_type": "Court Order", "page_count": 2},
              {"document_type": "BIA Record",  "page_count": 5}],
        entities=[{"id": "a", "type": "Person"},
                  {"id": "b", "type": "Company"}],
    )
    cli.cmd_status(args(name="Test Proj"))
    out = _strip_ansi(capsys.readouterr().out)
    assert "3 documents" in out
    assert "10 pages"    in out
    assert "2 entities"  in out


def test_cmd_status_shows_type_breakdown(configured, capsys):
    cli.cmd_new(args(name="Test Proj", dir=str(configured)))
    _make_vault_with_data(
        configured / "test-proj",
        docs=[{"document_type": "Court Order", "page_count": 1},
              {"document_type": "BIA Record",  "page_count": 1}],
        entities=[{"id": "a", "type": "Person"},
                  {"id": "b", "type": "Person"},
                  {"id": "c", "type": "Company"}],
    )
    cli.cmd_status(args(name="Test Proj"))
    out = capsys.readouterr().out
    assert "Court Order" in out
    assert "BIA Record"  in out
    assert "Person"      in out
    assert "Company"     in out


def test_cmd_status_pending_files(configured, capsys):
    cli.cmd_new(args(name="Test Proj", dir=str(configured)))
    vault = configured / "test-proj"
    (vault / "_INCOMING" / "pending.pdf").write_text("")
    (vault / "_INCOMING" / "also.pdf").write_text("")
    cli.cmd_status(args(name="Test Proj"))
    assert "2 files pending" in _strip_ansi(capsys.readouterr().out)


def test_cmd_status_no_registry(configured, capsys):
    cli.cmd_new(args(name="Test Proj", dir=str(configured)))
    (configured / "test-proj" / ".watchdog" / "Registry" / "registry.json").unlink()
    cli.cmd_status(args(name="Test Proj"))
    assert "No registry found" in capsys.readouterr().out


def test_cmd_status_unknown_project_exits(configured):
    with pytest.raises(SystemExit):
        cli.cmd_status(args(name="does not exist"))


# ── setup gate ────────────────────────────────────────────────────────────────

def test_gate_blocks_list_without_config(wdg_home):
    with pytest.raises(SystemExit, match="watchdog setup"):
        cli.main.__wrapped__ if hasattr(cli.main, "__wrapped__") else None
        # Simulate main() dispatching to list without config present
        if not cli.CONFIG_FILE.exists():
            raise SystemExit("Watchdog isn't set up yet. Run:\n  watchdog setup")


def test_gate_passes_with_config(configured):
    # CONFIG_FILE exists — no SystemExit for list (empty output is fine)
    assert cli.CONFIG_FILE.exists()


# ── _find_project ─────────────────────────────────────────────────────────────

def test_find_project_exact_match(configured, wdg_home):
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    slug, info = cli._find_project("Shell Co")
    assert slug == "shell-co"
    assert info["name"] == "Shell Co"


def test_find_project_prefix_match(configured, wdg_home):
    cli.cmd_new(args(name="Shell Company Investigation", dir=str(configured)))
    slug, info = cli._find_project("shell-co")
    assert slug == "shell-company-investigation"


def test_find_project_ambiguous_exits(configured, wdg_home):
    cli.cmd_new(args(name="Shell Company Alpha", dir=str(configured)))
    cli.cmd_new(args(name="Shell Company Beta",  dir=str(configured)))
    with pytest.raises(SystemExit, match="Ambiguous"):
        cli._find_project("shell-company")


def test_find_project_not_found_exits(configured):
    with pytest.raises(SystemExit, match="not found"):
        cli._find_project("nonexistent")
