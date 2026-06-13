import argparse
import json
import re
import pytest
from datetime import datetime, timezone
from pathlib import Path

import watchdog.cli as cli
import watchdog.cmd.base as _base
import watchdog.cmd.setup as _setup
import watchdog.cmd.vault as _vault


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def wdg_home(tmp_path, monkeypatch):
    """Redirect all watchdog home paths into tmp_path."""
    home = tmp_path / ".watchdog"
    home.mkdir()
    monkeypatch.setattr(_base,  "WATCHDOG_HOME",  home)
    monkeypatch.setattr(_base,  "PROJECTS_FILE",  home / "projects.json")
    monkeypatch.setattr(_base,  "CONFIG_FILE",    home / "config.json")
    monkeypatch.setattr(_setup, "WATCHDOG_HOME",  home)
    monkeypatch.setattr(_setup, "CONFIG_FILE",    home / "config.json")
    monkeypatch.setattr(cli,    "CONFIG_FILE",    home / "config.json")
    return home


@pytest.fixture
def configured(wdg_home, tmp_path, monkeypatch):
    """Write config.json so the setup gate passes."""
    investigations = tmp_path / "Investigations"
    investigations.mkdir()
    (wdg_home / "config.json").write_text(
        json.dumps({"projects_dir": str(investigations)}) + "\n"
    )
    monkeypatch.setattr("watchdog.cmd.vault._obsidian_config_path", lambda: tmp_path / "obsidian.json")
    return investigations


def args(**kwargs):
    return argparse.Namespace(**{"name": None, "dir": None, "force": False, "key": None, "value": None, "description": None, "name_flag": None, "project": None, "query": None, "text": None, **kwargs})


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


def test_count_incoming_not_fooled_by_failed_in_vault_path(tmp_path):
    # The vault path itself contains "_FAILED" — should still count files correctly.
    vault = tmp_path / "_FAILED_projects" / "investigation"
    incoming = vault / "_INCOMING"
    incoming.mkdir(parents=True)
    (incoming / "real.pdf").write_text("")
    assert cli._count_incoming(vault) == 1


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
              "entities/address", "documents", "briefings", "wiki", "queries",
              ".watchdog/queue",
              ".watchdog/staging"]:
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


def test_cmd_new_installs_skills_per_project(configured):
    cli.cmd_new(args(name="My Story", dir=str(configured)))
    commands_dir = configured / "my-story" / ".claude" / "commands"
    assert commands_dir.is_dir()
    skill_files = list(commands_dir.glob("*.md"))
    assert skill_files, "No skill files installed into vault .claude/commands/"


def test_cmd_new_uses_config_projects_dir(configured, wdg_home):
    # No --dir: should use the projects_dir from config.json
    cli.cmd_new(args(name="Auto Dir Test"))
    projects = json.loads((wdg_home / "projects.json").read_text())
    assert "auto-dir-test" in projects
    assert str(configured) in projects["auto-dir-test"]["path"]


def test_cmd_new_description_stored_in_registry(configured, wdg_home):
    cli.cmd_new(args(name="My Story", description="Tracking shell companies linked to city contracts", dir=str(configured)))
    projects = json.loads((wdg_home / "projects.json").read_text())
    assert projects["my-story"]["description"] == "Tracking shell companies linked to city contracts"


def test_cmd_new_description_no_context_file_created(configured):
    # context.md is written at ingest/context time, not at vault creation
    cli.cmd_new(args(name="My Story", description="Tracking shell companies linked to city contracts", dir=str(configured)))
    assert not (configured / "my-story" / "context.md").exists()


def test_cmd_new_no_description_no_context_file(configured):
    cli.cmd_new(args(name="My Story", dir=str(configured)))
    assert not (configured / "my-story" / "context.md").exists()
    assert "description" not in json.loads(
        (configured / "my-story" / ".watchdog" / "Registry" / "registry.json").read_text()
    )


def test_cmd_new_interactive_prompts(configured, wdg_home, monkeypatch):
    responses = iter(["Shell Game", "Tracking offshore accounts"])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))
    cli.cmd_new(args(dir=str(configured)))
    projects = json.loads((wdg_home / "projects.json").read_text())
    assert "shell-game" in projects
    assert projects["shell-game"]["description"] == "Tracking offshore accounts"


def test_cmd_new_name_flag(configured, wdg_home):
    cli.cmd_new(args(name_flag="Flag Name Test", dir=str(configured)))
    projects = json.loads((wdg_home / "projects.json").read_text())
    assert "flag-name-test" in projects



# ── Obsidian helpers ──────────────────────────────────────────────────────────

def test_register_obsidian_vault_writes_entry(tmp_path, monkeypatch):
    monkeypatch.setattr("watchdog.cmd.vault._obsidian_config_path", lambda: tmp_path / "obsidian.json")
    vault = tmp_path / "my-vault"
    cli._register_obsidian_vault(vault)
    data = json.loads((tmp_path / "obsidian.json").read_text())
    vaults = data["vaults"]
    assert len(vaults) == 1
    entry = next(iter(vaults.values()))
    assert entry["path"] == str(vault)
    assert isinstance(entry["ts"], int)


def test_register_obsidian_vault_appends_to_existing(tmp_path, monkeypatch):
    cfg = tmp_path / "obsidian.json"
    cfg.write_text(json.dumps({"vaults": {"aabbccdd11223344": {"path": "/other", "ts": 123}}}))
    monkeypatch.setattr("watchdog.cmd.vault._obsidian_config_path", lambda: cfg)
    cli._register_obsidian_vault(tmp_path / "new-vault")
    data = json.loads(cfg.read_text())
    assert len(data["vaults"]) == 2


def test_obsidian_registered_finds_vault(tmp_path, monkeypatch):
    cfg = tmp_path / "obsidian.json"
    vault = tmp_path / "my-vault"
    cfg.write_text(json.dumps({"vaults": {"abc123": {"path": str(vault), "ts": 0}}}))
    monkeypatch.setattr("watchdog.cmd.vault._obsidian_config_path", lambda: cfg)
    assert cli._obsidian_registered(vault) is True
    assert cli._obsidian_registered(tmp_path / "other") is False


def test_obsidian_config_path_windows(monkeypatch):
    monkeypatch.setattr("watchdog.cmd.vault.sys.platform", "win32")
    monkeypatch.setenv("APPDATA", "/win/appdata")
    p = cli._obsidian_config_path()
    assert "obsidian" in str(p)
    assert "appdata" in str(p).lower()


# ── cmd_obsidian ──────────────────────────────────────────────────────────────

def test_cmd_obsidian_opens_url(configured, monkeypatch):
    cli.cmd_new(args(name="My Story", dir=str(configured)))
    vault = configured / "my-story"
    calls = []
    monkeypatch.setattr("watchdog.cmd.vault.subprocess.run", lambda cmd, **kw: calls.append(cmd) or type("R", (), {"returncode": 0})())
    monkeypatch.setattr("watchdog.cmd.vault.sys.platform", "darwin")
    monkeypatch.setattr("watchdog.cmd.vault._obsidian_registered", lambda v: True)
    cli.cmd_obsidian(args(name="My Story"))
    assert len(calls) == 1
    assert calls[0][0] == "open"
    assert "obsidian://open?path=" in calls[0][1]


def test_cmd_obsidian_unregistered_prints_instructions(configured, monkeypatch, capsys):
    cli.cmd_new(args(name="My Story", dir=str(configured)))
    monkeypatch.setattr("watchdog.cmd.vault._obsidian_registered", lambda v: False)
    cli.cmd_obsidian(args(name="My Story"))
    out = capsys.readouterr().out
    assert "not registered" in out
    assert "Open folder as vault" in out


def test_cmd_obsidian_exits_on_failure(configured, monkeypatch):
    cli.cmd_new(args(name="My Story", dir=str(configured)))
    monkeypatch.setattr("watchdog.cmd.vault.subprocess.run", lambda cmd, **kw: type("R", (), {"returncode": 1})())
    monkeypatch.setattr("watchdog.cmd.vault.sys.platform", "darwin")
    monkeypatch.setattr("watchdog.cmd.vault._obsidian_registered", lambda v: True)
    with pytest.raises(SystemExit):
        cli.cmd_obsidian(args(name="My Story"))


def test_cmd_obsidian_infers_project_from_cwd(configured, monkeypatch):
    cli.cmd_new(args(name="My Story", dir=str(configured)))
    vault = configured / "my-story"
    calls = []
    monkeypatch.setattr("watchdog.cmd.vault.subprocess.run", lambda cmd, **kw: calls.append(cmd) or type("R", (), {"returncode": 0})())
    monkeypatch.setattr("watchdog.cmd.vault.sys.platform", "darwin")
    monkeypatch.setattr("watchdog.cmd.vault._obsidian_registered", lambda v: True)
    monkeypatch.chdir(vault)
    cli.cmd_obsidian(args(name=None))
    assert len(calls) == 1
    assert "obsidian://open?path=" in calls[0][1]


# ── cmd_open ──────────────────────────────────────────────────────────────────

def test_cmd_open_opens_vault_folder(configured, monkeypatch):
    cli.cmd_new(args(name="My Story", dir=str(configured)))
    vault = configured / "my-story"
    calls = []
    monkeypatch.setattr("watchdog.cmd.vault.subprocess.run", lambda cmd, **kw: calls.append(cmd) or type("R", (), {"returncode": 0})())
    monkeypatch.setattr("watchdog.cmd.vault.sys.platform", "darwin")
    cli.cmd_open(args(name="My Story"))
    assert len(calls) == 1
    assert calls[0] == ["open", str(vault)]


def test_cmd_open_infers_project_from_cwd(configured, monkeypatch):
    cli.cmd_new(args(name="My Story", dir=str(configured)))
    vault = configured / "my-story"
    calls = []
    monkeypatch.setattr("watchdog.cmd.vault.subprocess.run", lambda cmd, **kw: calls.append(cmd) or type("R", (), {"returncode": 0})())
    monkeypatch.setattr("watchdog.cmd.vault.sys.platform", "darwin")
    monkeypatch.chdir(vault)
    cli.cmd_open(args(name=None))
    assert len(calls) == 1
    assert calls[0] == ["open", str(vault)]


def test_cmd_open_exits_on_failure(configured, monkeypatch):
    cli.cmd_new(args(name="My Story", dir=str(configured)))
    monkeypatch.setattr("watchdog.cmd.vault.subprocess.run", lambda cmd, **kw: type("R", (), {"returncode": 1})())
    monkeypatch.setattr("watchdog.cmd.vault.sys.platform", "darwin")
    with pytest.raises(SystemExit):
        cli.cmd_open(args(name="My Story"))


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


def test_cmd_list_shows_description(configured, wdg_home, capsys):
    cli.cmd_new(args(name="Shell Co Probe", description="Offshore owners behind city land deals", dir=str(configured)))
    cli.cmd_list(args())
    assert "Offshore owners behind city land deals" in capsys.readouterr().out


def test_cmd_list_no_description_omits_line(configured, wdg_home, capsys):
    cli.cmd_new(args(name="Shell Co Probe", dir=str(configured)))
    cli.cmd_list(args())
    # Row renders without an extra description line — just check no blank stub
    out = capsys.readouterr().out
    assert "Shell Co Probe" in out
    assert "None" not in out


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


def test_cmd_status_shows_description(configured, capsys):
    cli.cmd_new(args(name="Test Proj", description="Offshore owners behind city land deals", dir=str(configured)))
    cli.cmd_status(args(name="Test Proj"))
    assert "Offshore owners behind city land deals" in capsys.readouterr().out


def test_cmd_status_no_description_omits_line(configured, capsys):
    cli.cmd_new(args(name="Test Proj", dir=str(configured)))
    cli.cmd_status(args(name="Test Proj"))
    assert "None" not in capsys.readouterr().out


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
    out = _strip_ansi(capsys.readouterr().out)
    assert "2 files" in out
    assert "_INCOMING/" in out


def test_cmd_status_shows_document_count(configured, capsys):
    cli.cmd_new(args(name="Test Proj", dir=str(configured)))
    _make_vault_with_data(
        configured / "test-proj",
        docs=[{"document_type": "Court Order", "page_count": 2}],
        entities=[],
    )
    cli.cmd_status(args(name="Test Proj"))
    out = _strip_ansi(capsys.readouterr().out)
    assert "1 documents" in out


def test_cmd_status_no_registry(configured, capsys):
    cli.cmd_new(args(name="Test Proj", dir=str(configured)))
    (configured / "test-proj" / ".watchdog" / "Registry" / "registry.json").unlink()
    cli.cmd_status(args(name="Test Proj"))
    assert "No registry found" in capsys.readouterr().out


def test_cmd_status_unknown_project_exits(configured):
    with pytest.raises(SystemExit):
        cli.cmd_status(args(name="does not exist"))


def test_cmd_status_corrupt_registry_exits(configured):
    cli.cmd_new(args(name="Test Proj", dir=str(configured)))
    reg = configured / "test-proj" / ".watchdog" / "Registry"
    (reg / "documents.json").write_text("not valid json {{{")
    with pytest.raises(SystemExit, match="corrupt"):
        cli.cmd_status(args(name="Test Proj"))


def test_cmd_status_infers_project_from_cwd(configured, monkeypatch, capsys):
    cli.cmd_new(args(name="Test Proj", dir=str(configured)))
    vault = configured / "test-proj"
    monkeypatch.chdir(vault)
    cli.cmd_status(args(name=None))
    assert "Test Proj" in capsys.readouterr().out


# ── cmd_validate_extraction ───────────────────────────────────────────────────

def _make_extraction(tmp_path, **overrides):
    base = {
        "document": {
            "sha256": "abc123",
            "filename": "test.pdf",
            "title": "Test Doc",
            "document_type": "Annual Report",
            "date_of_document": "2024-01-01",
            "page_count": 5,
            "source": None,
            "obtained": None,
            "near_duplicate_of": None,
            "summary": "A test document.",
            "key_facts": [{"fact": "Revenue was $1M.", "page": 1, "confidence": "high"}],
        },
        "entities": [
            {
                "id": "shell-co",
                "name": "Shell Co Ltd",
                "type": "Company",
                "aliases": [],
                "summary": "A company.",
                "timeline_events": [{"date": "2024-01-01", "event": "Founded.", "page": 1, "confidence": "high"}],
                "roles": [],
            }
        ],
        "morgue_entity_id": "shell-co",
        "morgue_document_type": "annual-report",
    }
    base.update(overrides)
    f = tmp_path / "extraction.json"
    f.write_text(json.dumps(base))
    return f


def _vault_tmp(tmp_path, monkeypatch):
    (tmp_path / ".watchdog").mkdir()
    monkeypatch.chdir(tmp_path)


def test_validate_extraction_valid(tmp_path, monkeypatch, capsys):
    _vault_tmp(tmp_path, monkeypatch)
    f = _make_extraction(tmp_path)
    cli.cmd_validate_extraction(args(file=str(f)))
    assert "ok" in capsys.readouterr().out


def test_validate_extraction_missing_sha256(tmp_path, monkeypatch, capsys):
    _vault_tmp(tmp_path, monkeypatch)
    f = _make_extraction(tmp_path)
    data = json.loads(f.read_text())
    data["document"]["sha256"] = ""
    f.write_text(json.dumps(data))
    with pytest.raises(SystemExit):
        cli.cmd_validate_extraction(args(file=str(f)))
    assert "sha256" in capsys.readouterr().out


def test_validate_extraction_missing_entity_id(tmp_path, monkeypatch, capsys):
    _vault_tmp(tmp_path, monkeypatch)
    f = _make_extraction(tmp_path)
    data = json.loads(f.read_text())
    data["entities"][0]["id"] = ""
    f.write_text(json.dumps(data))
    with pytest.raises(SystemExit):
        cli.cmd_validate_extraction(args(file=str(f)))
    assert "id" in capsys.readouterr().out


def test_validate_extraction_bad_confidence(tmp_path, monkeypatch, capsys):
    _vault_tmp(tmp_path, monkeypatch)
    f = _make_extraction(tmp_path)
    data = json.loads(f.read_text())
    data["document"]["key_facts"][0]["confidence"] = "very_sure"
    f.write_text(json.dumps(data))
    with pytest.raises(SystemExit):
        cli.cmd_validate_extraction(args(file=str(f)))
    assert "confidence" in capsys.readouterr().out


def test_validate_extraction_missing_morgue_entity_id(tmp_path, monkeypatch, capsys):
    _vault_tmp(tmp_path, monkeypatch)
    f = _make_extraction(tmp_path)
    data = json.loads(f.read_text())
    data["morgue_entity_id"] = ""
    f.write_text(json.dumps(data))
    with pytest.raises(SystemExit):
        cli.cmd_validate_extraction(args(file=str(f)))


def test_validate_extraction_invalid_json(tmp_path, monkeypatch):
    _vault_tmp(tmp_path, monkeypatch)
    f = tmp_path / "bad.json"
    f.write_text("not json {{{")
    with pytest.raises(SystemExit):
        cli.cmd_validate_extraction(args(file=str(f)))


# ── setup gate ────────────────────────────────────────────────────────────────

def test_gate_blocks_list_without_config(wdg_home, monkeypatch):
    import sys
    monkeypatch.setattr(sys, "argv", ["watchdog", "list"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1


def test_gate_passes_with_config(configured, monkeypatch, capsys):
    import sys
    monkeypatch.setattr(sys, "argv", ["watchdog", "list"])
    cli.main()
    assert "No projects" in capsys.readouterr().out


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


# ── cmd_configure ────────────────────────────────────────────────────────────

def test_configure_show_no_config(wdg_home, capsys):
    cli.cmd_configure(args())
    out = _strip_ansi(capsys.readouterr().out)
    assert "projects_dir" in out
    assert "ocr_languages" in out


def test_configure_show_existing_config(configured, capsys):
    cli.cmd_configure(args())
    out = _strip_ansi(capsys.readouterr().out)
    assert str(configured) in out


def test_configure_set_ocr_languages(wdg_home, capsys):
    cli.cmd_configure(args(key="ocr_languages", value="en-US,fr-FR"))
    config = json.loads((wdg_home / "config.json").read_text())
    assert config["ocr_languages"] == ["en-US", "fr-FR"]


def test_configure_set_ocr_languages_shown_on_display(wdg_home, capsys):
    cli.cmd_configure(args(key="ocr_languages", value="en-US,fr-FR"))
    capsys.readouterr()
    cli.cmd_configure(args())
    out = _strip_ansi(capsys.readouterr().out)
    assert "en-US" in out
    assert "fr-FR" in out


def test_configure_set_projects_dir(wdg_home, tmp_path, capsys):
    new_dir = tmp_path / "MyProjects"
    cli.cmd_configure(args(key="projects_dir", value=str(new_dir)))
    config = json.loads((wdg_home / "config.json").read_text())
    assert config["projects_dir"] == str(new_dir)
    assert new_dir.exists()


def test_configure_key_only_shows_value(wdg_home, capsys):
    cli.cmd_configure(args(key="ocr_languages", value="en-US,fr-FR"))
    capsys.readouterr()
    cli.cmd_configure(args(key="ocr_languages"))
    out = _strip_ansi(capsys.readouterr().out)
    assert "en-US" in out
    assert "fr-FR" in out


def test_configure_key_only_unset_shows_default(wdg_home, capsys):
    cli.cmd_configure(args(key="ocr_languages"))
    out = _strip_ansi(capsys.readouterr().out)
    assert "auto-detect" in out


def test_configure_unknown_key_exits(wdg_home):
    with pytest.raises(SystemExit, match="unknown key"):
        cli.cmd_configure(args(key="nonexistent_key", value="foo"))


# ── aliases ───────────────────────────────────────────────────────────────────

def test_about_prints_version_and_links(capsys, wdg_home):
    cli.cmd_about(None)
    out = _strip_ansi(capsys.readouterr().out)
    assert "Watchdog" in out
    assert "github.com/tomcardoso/watchdog" in out
    assert "issues" in out


@pytest.mark.parametrize("flag", ["-v", "--version"])
def test_version_flags_invoke_about(capsys, monkeypatch, flag):
    import sys
    monkeypatch.setattr(sys, "argv", ["watchdog", flag])
    cli.main()
    out = _strip_ansi(capsys.readouterr().out)
    assert "Watchdog" in out
    assert "github.com/tomcardoso/watchdog" in out


@pytest.mark.parametrize("alias,canonical", [
    ("init",     "new"),
    ("create",   "new"),
    ("ls",       "list"),
    ("info",     "status"),
    ("inspect",  "status"),
    ("version",  "about"),
    ("config",   "configure"),
    ("setting",  "configure"),
    ("settings", "configure"),
    ("find",      "search"),
    ("process",   "chew"),
    ("preprocess", "chew"),
    ("prep",      "chew"),
    ("remove",    "delete"),
    ("rm",        "delete"),
    ("mv",        "move"),
])
def test_aliases_remap_argv(alias, canonical, monkeypatch):
    import sys
    monkeypatch.setattr(sys, "argv", ["watchdog", alias])
    # The alias remap happens before argparse; after main() mutates sys.argv,
    # argv[1] should be the canonical command name.
    recorded = []

    original_main = cli.main

    def capturing_main():
        if len(sys.argv) >= 2 and sys.argv[1] in cli._ALIASES:
            sys.argv[1] = cli._ALIASES[sys.argv[1]]
        recorded.append(sys.argv[1])

    capturing_main()
    assert recorded == [canonical]


# ── configure — int / float keys ─────────────────────────────────────────────

def test_configure_set_garbled_threshold(wdg_home):
    cli.cmd_configure(args(key="garbled_threshold", value="0.6"))
    config = json.loads((wdg_home / "config.json").read_text())
    assert config["garbled_threshold"] == 0.6


def test_configure_float_invalid_exits(wdg_home):
    with pytest.raises(SystemExit, match="must be a number"):
        cli.cmd_configure(args(key="garbled_threshold", value="not-a-number"))


def test_configure_float_out_of_range_exits(wdg_home):
    with pytest.raises(SystemExit):
        cli.cmd_configure(args(key="garbled_threshold", value="1.5"))


def test_configure_float_below_range_exits(wdg_home):
    with pytest.raises(SystemExit):
        cli.cmd_configure(args(key="dup_threshold", value="-0.1"))


def test_configure_set_chunk_size(wdg_home):
    # Verifies string→int coercion: "20" must be stored as int 20, not string "20"
    cli.cmd_configure(args(key="chunk_size", value="20"))
    config = json.loads((wdg_home / "config.json").read_text())
    assert config["chunk_size"] == 20


def test_configure_int_invalid_exits(wdg_home):
    with pytest.raises(SystemExit, match="whole number"):
        cli.cmd_configure(args(key="chunk_size", value="3.5"))


def test_configure_int_below_min_exits(wdg_home):
    with pytest.raises(SystemExit):
        cli.cmd_configure(args(key="chunk_workers", value="0"))


# ── configure — int_or_auto keys ─────────────────────────────────────────────

def test_configure_int_or_auto_accepts_auto(wdg_home):
    cli.cmd_configure(args(key="chunk_workers", value="auto"))
    config = json.loads((wdg_home / "config.json").read_text())
    assert config["chunk_workers"] == "auto"


def test_configure_int_or_auto_accepts_integer(wdg_home):
    cli.cmd_configure(args(key="chunk_workers", value="4"))
    config = json.loads((wdg_home / "config.json").read_text())
    assert config["chunk_workers"] == 4


def test_configure_int_or_auto_invalid_exits(wdg_home):
    with pytest.raises(SystemExit, match="'auto' or a whole number"):
        cli.cmd_configure(args(key="chunk_workers", value="fast"))


def test_configure_chew_workers_accepts_auto(wdg_home):
    cli.cmd_configure(args(key="chew_workers", value="auto"))
    config = json.loads((wdg_home / "config.json").read_text())
    assert config["chew_workers"] == "auto"


# ── configure — bool keys ─────────────────────────────────────────────────────

def test_configure_set_table_structure_false(wdg_home):
    # Verifies string→bool coercion: "false" must be stored as Python False, not string
    cli.cmd_configure(args(key="table_structure", value="false"))
    config = json.loads((wdg_home / "config.json").read_text())
    assert config["table_structure"] is False


def test_configure_set_table_structure_accepts_variants(wdg_home, capsys):
    for truthy in ("true", "yes", "1", "on"):
        cli.cmd_configure(args(key="table_structure", value=truthy))
        config = json.loads((wdg_home / "config.json").read_text())
        assert config["table_structure"] is True, f"'{truthy}' should map to True"


def test_configure_bool_invalid_exits(wdg_home):
    with pytest.raises(SystemExit, match="true or false"):
        cli.cmd_configure(args(key="table_structure", value="maybe"))


# ── configure — enum keys ─────────────────────────────────────────────────────

def test_configure_set_ocr_engine(wdg_home, monkeypatch):
    monkeypatch.setattr(_setup, "_ensure_ocr_engine", lambda engine: None)
    cli.cmd_configure(args(key="ocr_engine", value="tesseract"))
    config = json.loads((wdg_home / "config.json").read_text())
    assert config["ocr_engine"] == "tesseract"


def test_configure_ocr_engine_invalid_exits(wdg_home):
    with pytest.raises(SystemExit, match="must be one of"):
        cli.cmd_configure(args(key="ocr_engine", value="badengine"))


# ── _ensure_ocr_engine ────────────────────────────────────────────────────────

def test_ensure_ocr_engine_noop_for_auto(monkeypatch):
    # auto and easyocr have no package to install — should return immediately
    called = []
    monkeypatch.setattr(_setup.subprocess, "run", lambda *a, **kw: called.append(a))
    cli._ensure_ocr_engine("auto")
    cli._ensure_ocr_engine("easyocr")
    assert called == []


def test_ensure_ocr_engine_skips_if_already_importable(monkeypatch):
    # Point to a module guaranteed to be importable so we can test the skip logic
    monkeypatch.setitem(_setup._OCR_ENGINE_PACKAGES, "tesseract", ("json", "fake-pkg"))
    calls = []
    monkeypatch.setattr(_setup.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    cli._ensure_ocr_engine("tesseract")
    assert calls == []  # json is importable, so no pip install should happen


def test_ensure_ocr_engine_installs_missing_package(monkeypatch):
    # Simulate missing package: __import__ raises ImportError, subprocess succeeds
    import types
    monkeypatch.setitem(
        _setup._OCR_ENGINE_PACKAGES, "tesseract", ("_no_such_pkg_xyz", "fake-pkg")
    )
    result = types.SimpleNamespace(returncode=0, stderr="")
    calls = []
    monkeypatch.setattr(_setup.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or result)
    cli._ensure_ocr_engine("tesseract")
    assert any("fake-pkg" in str(c) for c in calls)


def test_ensure_ocr_engine_apple_vision_non_mac_exits(monkeypatch):
    monkeypatch.setattr(_setup.sys, "platform", "linux")
    with pytest.raises(SystemExit, match="only available on macOS"):
        cli._ensure_ocr_engine("apple_vision")


def test_configure_show_all_includes_new_keys(wdg_home, capsys):
    cli.cmd_configure(args())
    out = _strip_ansi(capsys.readouterr().out)
    for key in ("garbled_threshold", "chunk_size", "chunk_workers",
                "chunk_timeout", "dup_threshold", "shingle_size",
                "table_structure", "embed_images", "ocr_engine"):
        assert key in out, f"'{key}' missing from configure output"


def test_configure_new_key_shows_default_when_unset(wdg_home, capsys):
    cli.cmd_configure(args(key="garbled_threshold"))
    out = _strip_ansi(capsys.readouterr().out)
    assert "0.75" in out


# ── configure — interactive mode ─────────────────────────────────────────────

class _FakeTTY:
    @staticmethod
    def isatty(): return True


def test_configure_interactive_yes_changes_value(wdg_home, monkeypatch, capsys):
    import sys
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    responses = iter(["y", "0.6"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    cli.cmd_configure(args(key="garbled_threshold"))
    config = json.loads((wdg_home / "config.json").read_text())
    assert config["garbled_threshold"] == 0.6


def test_configure_interactive_no_leaves_value_unchanged(wdg_home, monkeypatch):
    import sys
    (wdg_home / "config.json").write_text(
        json.dumps({"garbled_threshold": 0.75}) + "\n"
    )
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    cli.cmd_configure(args(key="garbled_threshold"))
    config = json.loads((wdg_home / "config.json").read_text())
    assert config.get("garbled_threshold") == 0.75


def test_configure_interactive_empty_input_no_change(wdg_home, monkeypatch):
    import sys
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    responses = iter(["y", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    cli.cmd_configure(args(key="chunk_size"))
    # No config.json written — nothing changed
    assert not (wdg_home / "config.json").exists()


def test_configure_non_tty_shows_value_without_prompt(wdg_home, capsys):
    """When stdin is not a TTY, key-only shows value without prompting."""
    (wdg_home / "config.json").write_text(
        json.dumps({"chunk_timeout": 600}) + "\n"
    )
    cli.cmd_configure(args(key="chunk_timeout"))
    out = _strip_ansi(capsys.readouterr().out)
    assert "600" in out


# ── setup — machine-aware defaults ───────────────────────────────────────────

def test_setup_writes_auto_for_worker_keys(tmp_path, monkeypatch):
    import watchdog.setup_cmd as sc

    home = tmp_path / ".watchdog"
    home.mkdir()
    monkeypatch.setattr(sc, "WATCHDOG_HOME", home)
    monkeypatch.setattr(sc, "CONFIG_FILE",   home / "config.json")

    investigations = tmp_path / "Investigations"
    investigations.mkdir()
    monkeypatch.setattr(sc, "_check_deps",       lambda: [])
    monkeypatch.setattr(sc, "_ask_projects_dir", lambda: investigations)
    monkeypatch.setattr(sc, "_detect_shell",     lambda: (None, None))

    sc.run()

    config = json.loads((home / "config.json").read_text())
    assert config["chunk_workers"] == "auto"
    assert config["chew_workers"] == "auto"
    assert config["projects_dir"] == str(investigations)


# ── cmd_unlock ────────────────────────────────────────────────────────────────

def _make_vault_with_lock(configured, timestamp_str):
    """Helper: register a project and write a lock file with the given timestamp."""
    vault = configured / "test-proj"
    lock_dir = vault / ".watchdog" / "Registry"
    lock_dir.mkdir(parents=True)
    lock_path = lock_dir / ".ingest-lock"
    lock_path.write_text(f"pid: claude-session\nstarted_at: {timestamp_str}\n")
    projects = {"test-proj": {"name": "Test Proj", "path": str(vault), "created": "2026-01-01T00:00:00Z"}}
    cli.save_projects(projects)
    return lock_path


def test_unlock_no_lock(configured, capsys):
    vault = configured / "test-proj"
    (vault / ".watchdog" / "Registry").mkdir(parents=True)
    cli.save_projects({"test-proj": {"name": "Test Proj", "path": str(vault), "created": "2026-01-01"}})
    cli.cmd_unlock(args(project="test-proj"))
    assert "nothing to do" in capsys.readouterr().out


def test_unlock_stale_lock_removed(configured, capsys):
    from datetime import timedelta
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    lock_path = _make_vault_with_lock(configured, old_ts)
    cli.cmd_unlock(args(project="test-proj"))
    assert not lock_path.exists()
    assert "Removed" in capsys.readouterr().out


def test_unlock_recent_lock_not_removed(configured, capsys):
    from datetime import timedelta
    recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    lock_path = _make_vault_with_lock(configured, recent_ts)
    cli.cmd_unlock(args(project="test-proj"))
    assert lock_path.exists()
    assert "recent" in capsys.readouterr().out


def test_unlock_recent_lock_force_removes(configured, capsys):
    from datetime import timedelta
    recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    lock_path = _make_vault_with_lock(configured, recent_ts)
    cli.cmd_unlock(args(project="test-proj", force=True))
    assert not lock_path.exists()
    assert "Removed" in capsys.readouterr().out


# ── cmd_delete ────────────────────────────────────────────────────────────────

def test_cmd_delete_removes_from_registry(configured, monkeypatch, capsys):
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    monkeypatch.setattr("builtins.input", lambda _: "y")
    cli.cmd_delete(args(name="Shell Co", purge=False))
    assert "shell-co" not in cli.load_projects()
    assert "Removed" in capsys.readouterr().out


def test_cmd_delete_purge_removes_files(configured, monkeypatch, capsys):
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    vault = configured / "shell-co"
    assert vault.exists()
    monkeypatch.setattr("builtins.input", lambda _: "y")
    cli.cmd_delete(args(name="Shell Co", purge=True))
    assert not vault.exists()
    assert "Deleted" in capsys.readouterr().out


def test_cmd_delete_cancelled(configured, monkeypatch, capsys):
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    monkeypatch.setattr("builtins.input", lambda _: "n")
    cli.cmd_delete(args(name="Shell Co", purge=False))
    assert "shell-co" in cli.load_projects()
    assert "Cancelled" in capsys.readouterr().out


def test_cmd_delete_removes_from_obsidian_registry(configured, monkeypatch, capsys):
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    vault = configured / "shell-co"
    cfg_path = _vault._obsidian_config_path()
    cfg_path.write_text(json.dumps({"vaults": {"abc123": {"path": str(vault), "ts": 0}}}))
    monkeypatch.setattr("builtins.input", lambda _: "y")
    cli.cmd_delete(args(name="Shell Co", purge=False))
    data = json.loads(cfg_path.read_text())
    assert not any(v.get("path") == str(vault) for v in data["vaults"].values())


# ── cmd_move ──────────────────────────────────────────────────────────────────

def test_cmd_move_updates_registry_when_already_moved(configured, capsys):
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    vault = configured / "shell-co"
    new_path = configured / "renamed"
    vault.rename(new_path)
    cli.cmd_move(args(name="Shell Co", path=str(new_path)))
    assert cli.load_projects()["shell-co"]["path"] == str(new_path)
    assert "Updated" in capsys.readouterr().out


def test_cmd_move_moves_files_when_src_exists(configured, capsys):
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    vault = configured / "shell-co"
    new_path = configured / "new-location"
    cli.cmd_move(args(name="Shell Co", path=str(new_path)))
    assert not vault.exists()
    assert new_path.exists()
    assert cli.load_projects()["shell-co"]["path"] == str(new_path)
    assert "Moved" in capsys.readouterr().out


def test_cmd_move_updates_obsidian_registry(configured, capsys):
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    vault = configured / "shell-co"
    new_path = configured / "new-location"
    cfg_path = _vault._obsidian_config_path()
    cfg_path.write_text(json.dumps({"vaults": {"abc123": {"path": str(vault), "ts": 0}}}))
    cli.cmd_move(args(name="Shell Co", path=str(new_path)))
    data = json.loads(cfg_path.read_text())
    paths = [v["path"] for v in data["vaults"].values()]
    assert str(new_path) in paths
    assert str(vault) not in paths


# ── cmd_archive / cmd_unarchive ───────────────────────────────────────────────

def test_cmd_archive_sets_flag(configured, capsys):
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    cli.cmd_archive(args(name="Shell Co"))
    assert cli.load_projects()["shell-co"].get("archived") is True
    assert "Archived" in capsys.readouterr().out


def test_cmd_unarchive_clears_flag(configured, capsys):
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    cli.cmd_archive(args(name="Shell Co"))
    cli.cmd_unarchive(args(name="Shell Co"))
    assert not cli.load_projects()["shell-co"].get("archived")
    assert "Unarchived" in capsys.readouterr().out


def test_cmd_list_hides_archived_by_default(configured, capsys):
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    cli.cmd_archive(args(name="Shell Co"))
    capsys.readouterr()  # flush setup output
    cli.cmd_list(args())
    out = capsys.readouterr().out
    assert "Shell Co" not in out
    assert "archived" in out


def test_cmd_list_all_shows_archived(configured, capsys):
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    cli.cmd_archive(args(name="Shell Co"))
    cli.cmd_list(args(**{"all": True}))
    assert "Shell Co" in capsys.readouterr().out


def test_cmd_list_shows_archived_hint_when_active_also_exist(configured, capsys):
    cli.cmd_new(args(name="Alpha", dir=str(configured)))
    cli.cmd_new(args(name="Beta",  dir=str(configured)))
    cli.cmd_archive(args(name="Beta"))
    cli.cmd_list(args())
    out = capsys.readouterr().out
    assert "Alpha" in out
    assert "1 archived" in out


# ── cmd_log ───────────────────────────────────────────────────────────────────

def test_cmd_log_no_log_file(configured, capsys):
    # log.md is not created at vault-creation time — cmd_log should handle its absence
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    cli.cmd_log(args(name="Shell Co", lines=None))
    assert "nothing has been ingested" in capsys.readouterr().out


def test_cmd_log_shows_content(configured, capsys):
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    (configured / "shell-co" / "log.md").write_text("## 2026-06-10\n- Ingested 3 files\n")
    cli.cmd_log(args(name="Shell Co", lines=None))
    out = capsys.readouterr().out
    assert "2026-06-10" in out
    assert "Ingested 3 files" in out


def test_cmd_log_lines_truncates(configured, capsys):
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    content = "\n".join(f"line {i}" for i in range(20))
    (configured / "shell-co" / "log.md").write_text(content)
    cli.cmd_log(args(name="Shell Co", lines=5))
    out = capsys.readouterr().out
    assert "line 19" in out
    assert "line 0" not in out


# ── cmd_chew with file argument ───────────────────────────────────────────────

def test_cmd_chew_with_specific_file(configured, monkeypatch):
    import watchdog.pipeline.preprocess_batch as ppb
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    vault = configured / "shell-co"
    f = vault / "_INCOMING" / "doc.pdf"
    f.write_bytes(b"")

    calls = []
    def fake_run_ingest(v, workers=None, chunk_workers=None, files=None):
        calls.append({"vault": v, "files": files})

    monkeypatch.setattr(ppb, "run_ingest", fake_run_ingest)
    monkeypatch.chdir(vault)
    cli.cmd_chew(args(file=str(f), chew_workers=None))
    assert len(calls) == 1
    assert calls[0]["files"] == [f]


# ── cmd_rename ────────────────────────────────────────────────────────────────

def test_cmd_rename_updates_registry_and_folder(configured, capsys):
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    vault = configured / "shell-co"
    cli.cmd_rename(args(project="Shell Co", name="Oil Co"))
    projects = cli.load_projects()
    assert "oil-co" in projects
    assert "shell-co" not in projects
    assert projects["oil-co"]["name"] == "Oil Co"
    assert (configured / "oil-co").exists()
    assert not vault.exists()
    assert "Renamed" in capsys.readouterr().out


def test_cmd_rename_updates_obsidian_registry(configured, capsys):
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    vault = configured / "shell-co"
    new_vault = configured / "oil-co"
    cfg_path = _vault._obsidian_config_path()
    cfg_path.write_text(json.dumps({"vaults": {"abc123": {"path": str(vault), "ts": 0}}}))
    cli.cmd_rename(args(project="Shell Co", name="Oil Co"))
    data = json.loads(cfg_path.read_text())
    paths = [v["path"] for v in data["vaults"].values()]
    assert str(new_vault) in paths
    assert str(vault) not in paths


def test_cmd_rename_blocked_by_chew_lock(configured, capsys):
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    vault = configured / "shell-co"
    (vault / ".watchdog" / ".chew-lock").write_text("started_at: 2026-01-01T00:00:00Z\npid: 99\n")
    with pytest.raises(SystemExit):
        cli.cmd_rename(args(project="Shell Co", name="Oil Co"))
    assert "shell-co" in cli.load_projects()


def test_cmd_rename_blocked_by_ingest_lock(configured, capsys):
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    vault = configured / "shell-co"
    (vault / ".watchdog" / "Registry" / ".ingest-lock").write_text("started_at: 2026-01-01T00:00:00Z\n")
    with pytest.raises(SystemExit):
        cli.cmd_rename(args(project="Shell Co", name="Oil Co"))


def test_cmd_rename_same_slug_updates_name_only(configured, capsys):
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    cli.cmd_rename(args(project="Shell Co", name="Shell Co"))
    projects = cli.load_projects()
    assert "shell-co" in projects
    assert (configured / "shell-co").exists()


# ── cmd_describe ─────────────────────────────────────────────────────────────

def test_cmd_describe_sets_description(configured, wdg_home, capsys):
    cli.cmd_new(args(name="My Story", dir=str(configured)))
    cli.cmd_describe(args(project="My Story", text="Offshore accounts linked to city contracts"))
    projects = cli.load_projects()
    assert projects["my-story"]["description"] == "Offshore accounts linked to city contracts"


def test_cmd_describe_updates_existing(configured, wdg_home, capsys):
    cli.cmd_new(args(name="My Story", description="old desc", dir=str(configured)))
    cli.cmd_describe(args(project="My Story", text="new desc"))
    assert cli.load_projects()["my-story"]["description"] == "new desc"


def test_cmd_describe_infers_project_from_cwd(configured, wdg_home, monkeypatch, capsys):
    cli.cmd_new(args(name="My Story", dir=str(configured)))
    monkeypatch.chdir(configured / "my-story")
    cli.cmd_describe(args(text="Inferred from cwd"))
    assert cli.load_projects()["my-story"]["description"] == "Inferred from cwd"


def test_cmd_describe_lone_positional_as_text_in_vault(configured, wdg_home, monkeypatch, capsys):
    cli.cmd_new(args(name="My Story", dir=str(configured)))
    monkeypatch.chdir(configured / "my-story")
    # "xyzzy-not-a-project" won't match any project slug, so it should be treated as text
    cli.cmd_describe(args(project="xyzzy-not-a-project"))
    assert cli.load_projects()["my-story"]["description"] == "xyzzy-not-a-project"


def test_cmd_describe_interactive(configured, wdg_home, monkeypatch, capsys):
    cli.cmd_new(args(name="My Story", dir=str(configured)))
    monkeypatch.chdir(configured / "my-story")
    monkeypatch.setattr("builtins.input", lambda _: "Interactive description")
    cli.cmd_describe(args())
    assert cli.load_projects()["my-story"]["description"] == "Interactive description"


def test_cmd_describe_outside_vault_no_project_exits(configured, wdg_home, capsys):
    with pytest.raises(SystemExit):
        cli.cmd_describe(args())


# ── aliases (rn) ──────────────────────────────────────────────────────────────

def test_alias_rn_maps_to_rename(monkeypatch):
    assert cli._ALIASES.get("rn") == "rename"


def test_cmd_chew_with_nonexistent_file_exits(configured, monkeypatch):
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    vault = configured / "shell-co"
    monkeypatch.chdir(vault)
    with pytest.raises(SystemExit, match="not found"):
        cli.cmd_chew(args(file="/no/such/file.pdf", chew_workers=None))


# ── _notify ───────────────────────────────────────────────────────────────────

def test_notify_no_op_on_non_darwin(monkeypatch):
    monkeypatch.setattr("watchdog.cmd.base.sys.platform", "linux")
    calls = []
    monkeypatch.setattr("watchdog.cmd.base.subprocess.run", lambda *a, **k: calls.append(a))
    cli._notify("title", "body")
    assert calls == []


def test_notify_calls_osascript_on_darwin(monkeypatch):
    monkeypatch.setattr("watchdog.cmd.base.sys.platform", "darwin")
    calls = []
    def fake_run(cmd, **kw):
        calls.append(cmd)
    monkeypatch.setattr("watchdog.cmd.base.subprocess.run", fake_run)
    cli._notify("Watchdog", "3 files chewed")
    assert len(calls) == 1
    assert calls[0][0] == "osascript"
    assert "3 files chewed" in calls[0][2]
