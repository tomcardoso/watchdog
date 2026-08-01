import argparse
import contextlib
import json
import re
import pytest
from datetime import datetime, timezone
from pathlib import Path

import watchdog.cli as cli
import watchdog.cmd.base as _base
import watchdog.cmd.research as _research
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


def test_count_incoming_excludes_skipped(tmp_path):
    # A duplicate chew moves files to _SKIPPED/ — status must not count them as pending,
    # or the user is sent in a status -> chew -> status loop chasing files chew already
    # decided to skip (#255).
    incoming = tmp_path / "_INCOMING"
    skipped = incoming / "_SKIPPED"
    skipped.mkdir(parents=True)
    (incoming / "pending.pdf").write_text("")
    (skipped / "duplicate.pdf").write_text("")
    assert cli._count_incoming(tmp_path) == 1


def test_count_incoming_matches_find_files_exclusions(tmp_path):
    # _count_incoming (status) and find_files (chew) must exclude the same directories,
    # or a file can be simultaneously "pending" per status and invisible to chew (#255).
    from watchdog.pipeline.preprocess_batch import find_files, SKIP_DIRS

    incoming = tmp_path / "_INCOMING"
    for d in SKIP_DIRS:
        skip_dir = incoming / d
        skip_dir.mkdir(parents=True, exist_ok=True)  # case-insensitive filesystems collide _FAILED/_failed
        (skip_dir / "file.pdf").write_text("")
    (incoming / "pending.pdf").write_text("")

    assert cli._count_incoming(tmp_path) == 1
    assert len(find_files([incoming])) == 1


# ── _load_registry ────────────────────────────────────────────────────────────

def test_load_registry_missing(tmp_path):
    assert cli._load_registry(tmp_path) is None


def test_load_registry_returns_data(tmp_path):
    reg_dir = tmp_path / ".watchdog" / "registry"
    reg_dir.mkdir(parents=True)
    data = {"document_count": 5, "entity_count": 3, "last_updated": "2026-06-07T00:00:00Z"}
    (reg_dir / "registry.json").write_text(json.dumps(data))
    assert cli._load_registry(tmp_path) == data


def test_load_registry_corrupt_json(tmp_path):
    reg_dir = tmp_path / ".watchdog" / "registry"
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
    reg = json.loads((configured / "my-story" / ".watchdog" / "registry" / "registry.json").read_text())
    assert reg["document_count"] == 0
    assert reg["entity_count"] == 0
    assert reg["schema_version"] == "1"


def test_cmd_new_claude_md_in_dot_claude_not_root(configured):
    cli.cmd_new(args(name="City Hall Probe", dir=str(configured)))
    vault = configured / "city-hall-probe"
    # CLAUDE.md lives in .claude/ (Claude Code loads ./.claude/CLAUDE.md the same), keeping the root clean
    assert not (vault / "CLAUDE.md").exists()
    text = (vault / ".claude" / "CLAUDE.md").read_text()
    assert "City Hall Probe" in text


def test_cmd_new_creates_readme(configured):
    cli.cmd_new(args(name="City Hall Probe", dir=str(configured)))
    readme = (configured / "city-hall-probe" / "README.md").read_text()
    assert "City Hall Probe" in readme
    assert "github.com/tomcardoso/watchdog" in readme        # GitHub link
    assert "/issues" in readme                                # report-an-issue link
    assert "Public records only" in readme


def test_cmd_new_creates_watchlist(configured):
    cli.cmd_new(args(name="City Hall Probe", dir=str(configured)))
    watchlist = (configured / "city-hall-probe" / "watchlist.md").read_text()
    assert "Watch list" in watchlist
    assert "case-insensitive" in watchlist
    # comments/blank-only template parses to zero active terms
    from watchdog.pipeline import watchlist as wl
    assert wl.load_terms(configured / "city-hall-probe") == []


def test_cmd_new_creates_bases_dashboard(configured):
    cli.cmd_new(args(name="City Hall Probe", dir=str(configured)))
    vault = configured / "city-hall-probe"
    base = (vault / "dashboard.base").read_text()
    # native Bases file with the views built from existing note frontmatter
    assert "views:" in base
    assert "Most-mentioned entities" in base
    assert "appears_in.length" in base          # recurrence as a sortable column
    assert 'file.inFolder("entities/person")' in base
    # index.md is now a Bases landing page — no Dataview dependency anywhere
    index = (vault / "index.md").read_text()
    assert "dataview" not in index.lower()
    assert "dashboard" in index.lower()


def test_cmd_new_session_start_hook_loads_hot_md(configured):
    cli.cmd_new(args(name="City Hall Probe", dir=str(configured)))
    settings = json.loads(
        (configured / "city-hall-probe" / ".claude" / "settings.json").read_text()
    )
    hooks = settings["hooks"]["SessionStart"]
    matcher = hooks[0]["matcher"]
    # one hook covers fresh start, resume, and post-compaction reload
    assert "startup" in matcher and "resume" in matcher and "compact" in matcher
    cmd = hooks[0]["hooks"][0]["command"]
    assert "hot.md" in cmd
    # the queue-reminder hook is preserved alongside it
    assert "UserPromptSubmit" in settings["hooks"]


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


def test_projects_dir_falls_back_when_key_missing(wdg_home):
    # A config.json that exists but omits "projects_dir" (e.g. hand-edited to set only one
    # other knob) must fall back to the documented ~/Investigations default, not KeyError —
    # every command that resolves a vault path calls this, so a bare `config["projects_dir"]`
    # crashed the whole CLI on a config missing that one key.
    (wdg_home / "config.json").write_text(json.dumps({"extractor_model": "sonnet"}) + "\n")
    assert _base._projects_dir() == Path.home() / "Investigations"


@pytest.mark.parametrize("value", ["", None])
def test_projects_dir_falls_back_when_key_is_falsy(wdg_home, value):
    # An explicit "" or null is not "missing" as far as a bare .get(key, default) is concerned —
    # it's a genuine falsy value that must be treated the same as absent, or Path("").expanduser()
    # (the cwd) would become the silent vault-creation directory.
    (wdg_home / "config.json").write_text(json.dumps({"projects_dir": value}) + "\n")
    assert _base._projects_dir() == Path.home() / "Investigations"


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
        (configured / "my-story" / ".watchdog" / "registry" / "registry.json").read_text()
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
    calls = []
    monkeypatch.setattr("watchdog.cmd.vault.subprocess.run", lambda cmd, **kw: calls.append(cmd) or type("R", (), {"returncode": 0})())
    monkeypatch.setattr("watchdog.cmd.vault.sys.platform", "darwin")
    monkeypatch.setattr("watchdog.cmd.vault._obsidian_registered", lambda v: True)
    monkeypatch.setattr("watchdog.cmd.vault._obsidian_launch_epoch", lambda: None)
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
    monkeypatch.setattr("watchdog.cmd.vault._obsidian_launch_epoch", lambda: None)
    with pytest.raises(SystemExit):
        cli.cmd_obsidian(args(name="My Story"))


def test_cmd_obsidian_stale_launch_warns_to_restart(configured, monkeypatch, capsys):
    cli.cmd_new(args(name="My Story", dir=str(configured)))
    calls = []
    monkeypatch.setattr("watchdog.cmd.vault.subprocess.run", lambda cmd, **kw: calls.append(cmd) or type("R", (), {"returncode": 0})())
    monkeypatch.setattr("watchdog.cmd.vault.sys.platform", "darwin")
    monkeypatch.setattr("watchdog.cmd.vault._obsidian_registered", lambda v: True)
    # Obsidian launched (epoch 1000s) before the vault was registered (2000s) → stale.
    monkeypatch.setattr("watchdog.cmd.vault._obsidian_launch_epoch", lambda: 1000.0)
    monkeypatch.setattr("watchdog.cmd.vault._obsidian_vault_ts", lambda v: 2000_000)
    cli.cmd_obsidian(args(name="My Story"))
    out = capsys.readouterr().out
    assert "hasn't loaded this vault yet" in out
    assert "Quit Obsidian" in out
    assert calls == []  # URI not fired — it would have shown "Vault not found"


def test_cmd_obsidian_infers_project_from_cwd(configured, monkeypatch):
    cli.cmd_new(args(name="My Story", dir=str(configured)))
    vault = configured / "my-story"
    calls = []
    monkeypatch.setattr("watchdog.cmd.vault.subprocess.run", lambda cmd, **kw: calls.append(cmd) or type("R", (), {"returncode": 0})())
    monkeypatch.setattr("watchdog.cmd.vault.sys.platform", "darwin")
    monkeypatch.setattr("watchdog.cmd.vault._obsidian_registered", lambda v: True)
    monkeypatch.setattr("watchdog.cmd.vault._obsidian_launch_epoch", lambda: None)
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
    reg = vault / ".watchdog" / "registry" / "registry.json"
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
    (configured / "shell-co-probe" / ".watchdog" / "registry" / "registry.json").unlink()
    cli.cmd_list(args())
    assert "—" in capsys.readouterr().out


def _queue_file(vault: Path, sha: str) -> None:
    q = vault / ".watchdog" / "queue"
    q.mkdir(parents=True, exist_ok=True)
    (q / f"{sha}.json").write_text(json.dumps({"sha256": sha, "filename": f"{sha}.pdf", "page_count": 1}))


def _extracted_file(vault: Path, sha: str) -> None:
    e = vault / ".watchdog" / "extracted"
    e.mkdir(parents=True, exist_ok=True)
    (e / f"{sha}.json").write_text(json.dumps({"document": {"filename": f"{sha}.pdf"}}))


def test_cmd_list_shows_awaiting_dig_and_bark_columns(configured, wdg_home, capsys):
    """A dig-only vault (#461): the table gets a column for each pipeline stage rather than one
    ambiguous 'To ingest' count that conflated 'not yet dug' with 'dug, awaiting bark'. One
    queue file has no staged extraction (awaiting dig), the other already does (awaiting bark),
    so the two new columns must each show 1, not fall back to a shared/blended count."""
    cli.cmd_new(args(name="Shell Co Probe", dir=str(configured)))
    vault = configured / "shell-co-probe"
    _queue_file(vault, "sha_not_dug")
    _queue_file(vault, "sha_dug")
    _extracted_file(vault, "sha_dug")
    cli.cmd_list(args())
    lines = _strip_ansi(capsys.readouterr().out).splitlines()
    assert "To ingest" not in "\n".join(lines)
    header = next(line for line in lines if "Awaiting dig" in line)
    row = next(line for line in lines if "Shell Co Probe" in line)
    dig_start = header.index("Awaiting dig")
    bark_start = header.index("Awaiting bark")
    assert row[dig_start:dig_start + len("Awaiting dig")].strip() == "1"
    assert row[bark_start:bark_start + len("Awaiting bark")].strip() == "1"


def test_cmd_status_splits_awaiting_dig_and_awaiting_bark(configured, capsys):
    """Same split as `cmd_list` (#461), in `cmd_status`'s prose lines: a queue file persists
    until `bark` commits it, so a raw queue count alone can't distinguish 'chewed, not yet dug'
    from 'dug, awaiting bark' — two very different states for a vault that may stay dig-only."""
    cli.cmd_new(args(name="Test Proj", dir=str(configured)))
    vault = configured / "test-proj"
    _queue_file(vault, "sha_not_dug")
    _queue_file(vault, "sha_dug")
    _extracted_file(vault, "sha_dug")
    cli.cmd_status(args(name="Test Proj"))
    lines = _strip_ansi(capsys.readouterr().out).splitlines()
    dig_line = next(line for line in lines if "awaiting watchdog dig" in line)
    bark_line = next(line for line in lines if "awaiting watchdog bark" in line)
    assert "1 file" in dig_line
    assert "1 file" in bark_line


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
    reg = vault / ".watchdog" / "registry"
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


def test_cmd_status_warns_pending_research(configured, capsys):
    cli.cmd_new(args(name="Test Proj", dir=str(configured)))
    vault = configured / "test-proj"
    q = vault / ".watchdog" / "research" / "queue.tsv"
    q.parent.mkdir(parents=True, exist_ok=True)
    q.write_text("https://a.com\tA\nhttps://b.com\tB\n", encoding="utf-8")
    cli.cmd_status(args(name="Test Proj"))
    out = _strip_ansi(capsys.readouterr().out)
    assert "2 research URLs" in out
    assert "watchdog research-fetch" in out


def test_cmd_status_no_research_warning_when_none_pending(configured, capsys):
    cli.cmd_new(args(name="Test Proj", dir=str(configured)))
    cli.cmd_status(args(name="Test Proj"))
    assert "research URL" not in _strip_ansi(capsys.readouterr().out)


def test_cmd_status_shows_pending_batch_extraction(configured, capsys):
    """#214: a pending claude-batch extraction is surfaced the same way a pending
    finalization is — the journalist shouldn't have to remember it's in flight."""
    cli.cmd_new(args(name="Test Proj", dir=str(configured)))
    vault = configured / "test-proj"
    from watchdog.pipeline import batch_extract
    batch_extract.write_state(vault, {"batch_id": "batch_abc", "shas": ["a", "b", "c"],
                                      "model": "claude-sonnet-4-6", "skill_label": "s",
                                      "effort": None})
    cli.cmd_status(args(name="Test Proj"))
    out = _strip_ansi(capsys.readouterr().out)
    assert "Batch extraction pending" in out
    assert "3 documents" in out
    assert "batch_abc" in out
    assert "watchdog dig" in out


def test_cmd_status_no_batch_line_when_none_pending(configured, capsys):
    cli.cmd_new(args(name="Test Proj", dir=str(configured)))
    cli.cmd_status(args(name="Test Proj"))
    assert "Batch extraction pending" not in _strip_ansi(capsys.readouterr().out)


def test_cmd_status_shows_last_ingest_usage(configured, capsys):
    """#222: the user-facing half of A2's telemetry — watchdog status shows what the last
    ingest cost, without having to go spelunking in .watchdog/registry/."""
    cli.cmd_new(args(name="Test Proj", dir=str(configured)))
    vault = configured / "test-proj"
    reg = vault / ".watchdog" / "registry"
    (reg / "usage-20260101T000000Z.json").write_text(json.dumps({
        "calls": [], "totals": {"input_tokens": 41200, "output_tokens": 9600,
                                "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 1.87},
    }))
    cli.cmd_status(args(name="Test Proj"))
    out = _strip_ansi(capsys.readouterr().out)
    assert "Last ingest" in out
    assert "41,200 in" in out and "9,600 out" in out
    assert "$1.87" in out


def test_cmd_status_no_usage_line_before_any_ingest(configured, capsys):
    cli.cmd_new(args(name="Test Proj", dir=str(configured)))
    cli.cmd_status(args(name="Test Proj"))
    assert "Last ingest" not in _strip_ansi(capsys.readouterr().out)


def test_bare_watchdog_skip_briefing_flag_reaches_cmd_guided(configured, tmp_path, monkeypatch):
    """`watchdog --skip-briefing` with no subcommand (#410) parses `command=None` and
    `skip_briefing=True` on the same Namespace `cmd_guided` receives — it rides through
    unchanged to `cmd_ingest` via `_offer_ingest`, no separate plumbing needed."""
    import sys
    vault = tmp_path / "bare-vault"
    (vault / ".watchdog" / "queue").mkdir(parents=True)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(sys, "argv", ["watchdog", "--skip-briefing"])

    captured = []
    monkeypatch.setattr(cli, "cmd_guided", lambda a: captured.append(a))

    cli.main()

    assert len(captured) == 1
    assert captured[0].command is None
    assert captured[0].skip_briefing is True


def test_cmd_guided_warns_pending_research(configured, capsys, monkeypatch):
    cli.cmd_new(args(name="Test Proj", dir=str(configured)))
    vault = configured / "test-proj"
    q = vault / ".watchdog" / "research" / "queue.tsv"
    q.parent.mkdir(parents=True, exist_ok=True)
    q.write_text("https://a.com\n", encoding="utf-8")
    monkeypatch.chdir(vault)
    cli.cmd_guided(args())
    out = _strip_ansi(capsys.readouterr().out)
    assert "1 research URL" in out
    assert "watchdog research-fetch" in out


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
    (configured / "test-proj" / ".watchdog" / "registry" / "registry.json").unlink()
    cli.cmd_status(args(name="Test Proj"))
    assert "No registry found" in capsys.readouterr().out


def test_cmd_status_unknown_project_exits(configured):
    with pytest.raises(SystemExit):
        cli.cmd_status(args(name="does not exist"))


def test_cmd_status_corrupt_registry_exits(configured):
    cli.cmd_new(args(name="Test Proj", dir=str(configured)))
    reg = configured / "test-proj" / ".watchdog" / "registry"
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
            "key_facts": [{"fact": "Revenue was $1M.", "page": 1, "basis": "stated"}],
        },
        "entities": [
            {
                "id": "shell-co",
                "name": "Shell Co Ltd",
                "type": "Company",
                "aliases": [],
                "summary": "A company.",
                "timeline_events": [{"date": "2024-01-01", "event": "Founded.", "page": 1, "basis": "stated"}],
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


def test_validate_extraction_bad_basis(tmp_path, monkeypatch, capsys):
    _vault_tmp(tmp_path, monkeypatch)
    f = _make_extraction(tmp_path)
    data = json.loads(f.read_text())
    data["document"]["key_facts"][0]["basis"] = "very_sure"
    f.write_text(json.dumps(data))
    with pytest.raises(SystemExit):
        cli.cmd_validate_extraction(args(file=str(f)))
    assert "basis" in capsys.readouterr().out


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


def test_configure_persists_config_file_as_0600(wdg_home):
    """config.json holds secrets (wayback_access_key/wayback_secret_key) in plaintext, so every
    persist must chmod it 0600, matching auth._save_state (#304)."""
    cli.cmd_configure(args(key="projects_dir", value=str(wdg_home)))
    mode = (wdg_home / "config.json").stat().st_mode & 0o777
    assert mode == 0o600


def test_configure_corrects_preexisting_loose_permissions(wdg_home):
    """A pre-existing config.json written before this fix (e.g. 0644) is tightened to 0600 on
    the very next persist, not just newly created files."""
    config_path = wdg_home / "config.json"
    config_path.write_text("{}\n")
    config_path.chmod(0o644)

    cli.cmd_configure(args(key="projects_dir", value=str(wdg_home)))

    mode = config_path.stat().st_mode & 0o777
    assert mode == 0o600


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


# ── default_skill picker ───────────────────────────────────────────────────────

def test_configure_default_skill_noninteractive_set(wdg_home):
    cli.cmd_configure(args(key="default_skill", value="court-documents"))
    config = json.loads((wdg_home / "config.json").read_text())
    assert config["default_skill"] == "court-documents"


def test_configure_default_skill_interactive_set(wdg_home, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(_setup, "_pick_skill_arrow", lambda cat, cur: ("set", "legislation"))
    cli.cmd_configure(args(key="default_skill"))
    config = json.loads((wdg_home / "config.json").read_text())
    assert config["default_skill"] == "legislation"


def test_configure_default_skill_interactive_unset(wdg_home, monkeypatch):
    (wdg_home / "config.json").write_text(json.dumps({"default_skill": "legislation"}) + "\n")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(_setup, "_pick_skill_arrow", lambda cat, cur: ("unset", None))
    cli.cmd_configure(args(key="default_skill"))
    config = json.loads((wdg_home / "config.json").read_text())
    assert "default_skill" not in config


def test_configure_default_skill_interactive_cancel(wdg_home, monkeypatch):
    (wdg_home / "config.json").write_text(json.dumps({"default_skill": "legislation"}) + "\n")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(_setup, "_pick_skill_arrow", lambda cat, cur: ("cancel", None))
    cli.cmd_configure(args(key="default_skill"))
    config = json.loads((wdg_home / "config.json").read_text())
    assert config["default_skill"] == "legislation"


def test_configure_default_skill_custom_warns(wdg_home, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(_setup, "_pick_skill_arrow", lambda cat, cur: ("set", "not-a-real-skill"))
    cli.cmd_configure(args(key="default_skill"))
    out = _strip_ansi(capsys.readouterr().out)
    assert "isn't a known skill" in out
    config = json.loads((wdg_home / "config.json").read_text())
    assert config["default_skill"] == "not-a-real-skill"


def test_pick_skill_arrow_numbered_fallback(monkeypatch):
    # Under pytest, sys.stdin has no usable fd → the picker uses the numbered prompt.
    monkeypatch.setattr("builtins.input", lambda *a: "2")
    catalog = {"alpha": "/x/alpha.md", "beta": "/x/beta.md"}
    assert _setup._pick_skill_arrow(catalog, None) == ("set", "beta")


def test_pick_skill_arrow_numbered_fallback_unset(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "3")  # the "(unset …)" row
    catalog = {"alpha": "/x/alpha.md", "beta": "/x/beta.md"}
    assert _setup._pick_skill_arrow(catalog, None) == ("unset", None)


# ── model picker (classifier_model/extractor_model/finalizer_model) ──────────

def test_pick_model_interactive_claude_tier(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "2")   # Claude group: haiku, sonnet, opus
    assert _setup._pick_model_interactive(None) == "sonnet"


def test_pick_model_interactive_only_provider_filters_to_one_group(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "1")
    value = _setup._pick_model_interactive(None, only_provider="gemini")
    assert value.startswith("gemini:")


def test_pick_model_interactive_custom_free_text(monkeypatch):
    answers = iter(["18", "openai:my-custom-model"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    assert _setup._pick_model_interactive(None) == "openai:my-custom-model"


def test_pick_model_interactive_cancel_returns_none(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "")
    assert _setup._pick_model_interactive(None) is None


def test_edit_key_interactive_extractor_model_uses_picker(wdg_home, monkeypatch, capsys):
    import sys
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    monkeypatch.setattr("builtins.input", lambda prompt="": "1")  # Claude group: haiku
    config = {}
    _setup._edit_key_interactive(config, "extractor_model")
    assert config["extractor_model"] == "haiku"


# ── configure wizard ──────────────────────────────────────────────────────────

def test_wizard_menu_numbered_selects_first_key(wdg_home, monkeypatch):
    # Under pytest, sys.stdin has no usable fd → the menu uses the numbered prompt.
    monkeypatch.setattr("builtins.input", lambda *a: "1")
    key, sel = _setup._wizard_menu({}, 0)
    assert key == "projects_dir"  # first selectable key (Vaults section leads)
    assert sel == 0


def test_wizard_menu_numbered_quit_on_empty(wdg_home, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "")
    key, _ = _setup._wizard_menu({}, 0)
    assert key is None


def test_run_configure_wizard_edits_then_quits(wdg_home, monkeypatch):
    import sys
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    # menu hands back chunk_size once, then quits
    picks = iter([("chunk_size", 0), (None, 0)])
    monkeypatch.setattr(_setup, "_wizard_menu", lambda config, sel: next(picks))
    responses = iter(["y", "20"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))

    config = {}
    _setup._run_configure_wizard(config)

    assert config["chunk_size"] == 20
    saved = json.loads((wdg_home / "config.json").read_text())
    assert saved["chunk_size"] == 20


def test_edit_key_interactive_invalid_value_does_not_exit(wdg_home, monkeypatch, capsys):
    import sys
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    responses = iter(["y", "not-a-number"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    # Must NOT raise SystemExit — the wizard relies on this to keep going.
    _setup._edit_key_interactive({}, "garbled_threshold")
    out = _strip_ansi(capsys.readouterr().out)
    assert "must be a number" in out
    assert not (wdg_home / "config.json").exists()


def test_configure_bare_offers_wizard_on_tty(wdg_home, monkeypatch):
    import sys
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    called = []
    monkeypatch.setattr(_setup, "_run_configure_wizard", lambda config: called.append(True))
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    cli.cmd_configure(args())
    assert called == [True]


def test_configure_bare_skips_wizard_when_declined(wdg_home, monkeypatch):
    import sys
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    called = []
    monkeypatch.setattr(_setup, "_run_configure_wizard", lambda config: called.append(True))
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    cli.cmd_configure(args())
    assert called == []


def test_configure_bare_non_tty_does_not_prompt(wdg_home, monkeypatch):
    called = []
    monkeypatch.setattr(_setup, "_run_configure_wizard", lambda config: called.append(True))
    def _boom(*a, **k):
        raise AssertionError("should not prompt when stdin is not a TTY")
    monkeypatch.setattr("builtins.input", _boom)
    cli.cmd_configure(args())  # pytest stdin is not a TTY
    assert called == []


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
    ("telemetry", "usage"),
])
def test_aliases_remap_argv(alias, canonical, monkeypatch):
    import sys
    monkeypatch.setattr(sys, "argv", ["watchdog", alias])
    # The alias remap happens before argparse; after main() mutates sys.argv,
    # argv[1] should be the canonical command name.
    recorded = []

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


def test_configure_section_threshold_accepts_auto(wdg_home):
    cli.cmd_configure(args(key="section_token_threshold", value="auto"))
    config = json.loads((wdg_home / "config.json").read_text())
    assert config["section_token_threshold"] == "auto"


def test_display_value_section_auto_shows_resolved_number():
    # 'auto' (or unset) renders the concrete value it resolves to for the configured model.
    out = _setup._display_value("section_token_threshold", "auto", {"extractor_model": "sonnet"})
    assert "auto" in out and "120000" in out and "sonnet" in out
    # Unset (None) behaves the same as explicit 'auto'.
    out_unset = _setup._display_value("section_token_threshold", None, {})
    assert "auto" in out_unset and "120000" in out_unset


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


def test_configure_set_extractor_effort(wdg_home):
    cli.cmd_configure(args(key="extractor_effort", value="medium"))
    config = json.loads((wdg_home / "config.json").read_text())
    assert config["extractor_effort"] == "medium"


def test_configure_effort_invalid_exits(wdg_home):
    with pytest.raises(SystemExit, match="must be one of"):
        cli.cmd_configure(args(key="finalizer_effort", value="ludicrous"))


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
                "table_structure", "ocr_engine"):
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
    monkeypatch.setattr(sc, "_check_playwright", lambda: None)
    # Never let the setup flow reach the real GLiNER path here: with gliner absent it falls
    # back to a live `pip install` that pulls in PyTorch (a CI timeout), and with it present
    # it loads the actual model.
    monkeypatch.setattr(sc, "_ensure_gliner",    lambda: None)

    sc.run()

    config = json.loads((home / "config.json").read_text())
    assert config["chunk_workers"] == "auto"
    assert config["chew_workers"] == "auto"
    assert config["projects_dir"] == str(investigations)


# ── cmd_unlock ────────────────────────────────────────────────────────────────

def _make_vault_with_lock(configured, timestamp_str):
    """Helper: register a project and write a lock file with the given timestamp."""
    vault = configured / "test-proj"
    lock_dir = vault / ".watchdog" / "registry"
    lock_dir.mkdir(parents=True)
    lock_path = lock_dir / ".ingest-lock"
    lock_path.write_text(f"pid: claude-session\nstarted_at: {timestamp_str}\n")
    projects = {"test-proj": {"name": "Test Proj", "path": str(vault), "created": "2026-01-01T00:00:00Z"}}
    cli.save_projects(projects)
    return lock_path


def test_unlock_no_lock(configured, capsys):
    vault = configured / "test-proj"
    (vault / ".watchdog" / "registry").mkdir(parents=True)
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


def test_cmd_delete_purge_prints_backup_hint_when_registry_existed(configured, monkeypatch, capsys):
    """#270: --purge is a one-way delete, but the registry files are snapshotted first
    (a hedge against a partial failure, not a way to undo — the snapshot lives inside
    the vault being deleted, so a completed purge removes it too). cmd_new already
    seeds entities.json/documents.json/registry.json, so any real vault hits this path."""
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    vault = configured / "shell-co"
    monkeypatch.setattr("builtins.input", lambda _: "y")
    cli.cmd_delete(args(name="Shell Co", purge=True))
    out = _strip_ansi(capsys.readouterr().out)
    assert "Deleted" in out
    assert "snapshot" in out.lower()
    assert not vault.exists()   # the purge (and its backup dir) is fully gone


def test_cmd_delete_purge_no_backup_hint_when_registry_missing(configured, monkeypatch, capsys):
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    vault = configured / "shell-co"
    for name in ("entities.json", "documents.json", "registry.json", "manifest.json", "resolutions.json"):
        (vault / ".watchdog" / "registry" / name).unlink(missing_ok=True)
    monkeypatch.setattr("builtins.input", lambda _: "y")
    cli.cmd_delete(args(name="Shell Co", purge=True))
    out = _strip_ansi(capsys.readouterr().out)
    assert "snapshot" not in out.lower()


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
    def fake_run_ingest(v, workers=None, chunk_workers=None, files=None, show_ingest_hint=True):
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
    (vault / ".watchdog" / "registry" / ".ingest-lock").write_text("started_at: 2026-01-01T00:00:00Z\n")
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


# ── _offer_ingest (post-chew prompt) ──────────────────────────────────────────

def _vault_with_queue(configured):
    cli.cmd_new(args(name="Shell Co", dir=str(configured)))
    vault = configured / "shell-co"
    (vault / ".watchdog" / "queue").mkdir(parents=True, exist_ok=True)
    (vault / ".watchdog" / "queue" / "abc.json").write_text("{}")
    return vault


def test_offer_ingest_yes_runs_ingest_without_reconfirming(configured, monkeypatch):
    from watchdog.cmd import ingest as ing
    vault = _vault_with_queue(configured)
    monkeypatch.setattr("builtins.input", lambda *a: "1")   # pick(): "Ingest now"
    seen = {}
    monkeypatch.setattr(ing, "cmd_ingest",
                        lambda a, *, confirm=True, skip_preview=False: seen.update(
                            confirm=confirm, skip_preview=skip_preview))
    ing._offer_ingest(args(), vault)
    assert seen == {"confirm": False, "skip_preview": True}   # chew's prompt is the only confirmation


def test_offer_ingest_no_prints_hint_and_skips(configured, monkeypatch, capsys):
    from watchdog.cmd import ingest as ing
    vault = _vault_with_queue(configured)
    monkeypatch.setattr("builtins.input", lambda *a: "2")   # pick(): "Not now"
    def _boom(*a, **k):
        raise AssertionError("ingest must not run when declined")
    monkeypatch.setattr(ing, "cmd_ingest", _boom)
    ing._offer_ingest(args(), vault)
    assert "watchdog" in capsys.readouterr().out


def test_offer_ingest_eof_prints_hint(configured, monkeypatch, capsys):
    from watchdog.cmd import ingest as ing
    vault = _vault_with_queue(configured)
    def _eof(*a):
        raise EOFError
    monkeypatch.setattr("builtins.input", _eof)
    monkeypatch.setattr(ing, "cmd_ingest", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no ingest")))
    ing._offer_ingest(args(), vault)
    assert "watchdog" in capsys.readouterr().out


def test_offer_ingest_no_hints_dig_from_chew_context(configured, monkeypatch, capsys):
    """When `_offer_ingest` is reached from `watchdog chew` (manual control), the decline hint
    must point at `watchdog dig`, not the retired `watchdog ingest` or the guided bare walk
    (#441, D138)."""
    from watchdog.cmd import ingest as ing
    vault = _vault_with_queue(configured)
    monkeypatch.setattr("builtins.input", lambda *a: "2")   # pick(): "Not now"
    monkeypatch.setattr(ing, "cmd_ingest", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no ingest")))
    ing._offer_ingest(args(command="chew"), vault)
    assert "watchdog dig" in capsys.readouterr().out


def test_offer_ingest_shows_public_records_warning(configured, monkeypatch, capsys):
    """The guided (post-chew) offer must show the same warning as the direct path (#426) —
    it's the acknowledgement itself now, not a second confirmation stacked on top of it."""
    from watchdog.cmd import ingest as ing
    vault = _vault_with_queue(configured)
    monkeypatch.setattr("builtins.input", lambda *a: "1")   # numbered fallback: Acknowledge
    monkeypatch.setattr(ing, "cmd_ingest", lambda *a, **k: None)
    ing._offer_ingest(args(), vault)
    out = _strip_ansi(capsys.readouterr().out)
    assert "Public records only" in out
    assert "1 document will be sent to the model" in out


# ── Public-records warning gate (#426) ──────────────────────────────────────

def test_confirm_public_records_zero_docs_is_noop(monkeypatch):
    """Nothing new is being sent this call (e.g. only checking a pending claude-batch
    extraction) — no warning, no prompt."""
    from watchdog.cmd import ingest as ing
    monkeypatch.setattr(ing.interactive, "pick",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no pick when nothing is sent")))
    assert ing._confirm_public_records(0) is True


def test_confirm_public_records_skip_warning_prints_notice_no_pick(monkeypatch, capsys):
    from watchdog.cmd import ingest as ing
    monkeypatch.setattr(ing.interactive, "pick",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("--skip-warning must not prompt")))
    assert ing._confirm_public_records(3, skip_warning=True) is True
    out = _strip_ansi(capsys.readouterr().out)
    assert "3" in out and "cloud AI model" in out


def test_confirm_public_records_acknowledge_is_the_default(monkeypatch, capsys):
    from watchdog.cmd import ingest as ing
    monkeypatch.setattr("builtins.input", lambda *a: "1")   # numbered fallback: row 1 = Acknowledge (the default row)
    assert ing._confirm_public_records(6) is True
    out = _strip_ansi(capsys.readouterr().out)
    assert "Public records only" in out
    assert "6 documents will be sent to the model" in out


def test_confirm_public_records_cancel(monkeypatch):
    from watchdog.cmd import ingest as ing
    monkeypatch.setattr("builtins.input", lambda *a: "2")   # numbered fallback: Cancel
    assert ing._confirm_public_records(1) is False


def test_confirm_public_records_no_double_blank_line_before_menu(monkeypatch, capsys):
    """The warning's own trailing newline plus `print()`'s and `pick()`'s own leading blank line
    used to stack into two blank lines before the menu — the same anti-pattern #395 fixed
    elsewhere in the picker (#456 follow-up)."""
    from watchdog.cmd import ingest as ing
    monkeypatch.setattr("builtins.input", lambda *a: "1")
    ing._confirm_public_records(1)
    out = capsys.readouterr().out
    lines = out.split("\n")
    idx = next(i for i, line in enumerate(lines) if "will be sent to the model" in line)
    assert lines[idx + 1].strip() == ""       # exactly one blank line separates content from menu
    assert lines[idx + 2].strip() != ""       # not a second blank line


def test_cmd_ingest_confirm_cancel_never_calls_model(wdg_home, tmp_path, monkeypatch):
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)
    monkeypatch.setattr(ing.interactive, "pick", lambda *a, **k: 1)   # "Cancel"

    async def fake_run(*a, **k):
        raise AssertionError("model must not be called after Cancel")
    monkeypatch.setattr(orch_module, "run", fake_run)

    ing.cmd_ingest(args())   # confirm defaults True


def test_cmd_ingest_confirm_acknowledge_calls_model(wdg_home, tmp_path, monkeypatch):
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)
    monkeypatch.setattr(ing.interactive, "pick", lambda *a, **k: 0)   # "Acknowledge and ingest"

    calls = []
    async def fake_run(*a, **k):
        calls.append(k)
        return {"results": [{"sha256": "sha1", "filename": "a.pdf", "status": "ok", "entity_count": 1}],
                "extracted": 1, "skipped": 0, "failed": 0, "cancelled": False,
                "rate_limited": False, "stop_message": None, "rate_limit_resets_at": None,
                "quarantined": 0}
    monkeypatch.setattr(orch_module, "run", fake_run)

    ing.cmd_ingest(args())
    assert len(calls) == 1


def test_cmd_ingest_skip_warning_bypasses_pick_but_still_notifies(wdg_home, tmp_path, monkeypatch, capsys):
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)
    monkeypatch.setattr(ing.interactive, "pick",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("--skip-warning must not prompt")))

    calls = []
    async def fake_run(*a, **k):
        calls.append(k)
        return {"results": [{"sha256": "sha1", "filename": "a.pdf", "status": "ok", "entity_count": 1}],
                "extracted": 1, "skipped": 0, "failed": 0, "cancelled": False,
                "rate_limited": False, "stop_message": None, "rate_limit_resets_at": None,
                "quarantined": 0}
    monkeypatch.setattr(orch_module, "run", fake_run)

    ing.cmd_ingest(args(skip_warning=True))
    assert len(calls) == 1
    out = _strip_ansi(capsys.readouterr().out)
    assert "cloud AI model" in out


def test_ingest_parser_accepts_skip_warning(configured, monkeypatch):
    """`ingest --help` is intercepted by cmd/base.py's hand-maintained `_CMD_HELP` dict before
    argparse ever sees it (and that dict already omits --estimate/--force), so check the flag
    parses instead of grepping --help output."""
    import sys
    seen = {}
    monkeypatch.setattr(cli, "cmd_ingest", lambda a: seen.update(skip_warning=a.skip_warning))
    monkeypatch.setattr(sys, "argv", ["watchdog", "ingest", "--skip-warning"])
    cli.main()
    assert seen == {"skip_warning": True}


def test_dig_parser_accepts_skip_warning(configured, monkeypatch):
    import sys
    seen = {}
    monkeypatch.setattr(cli, "cmd_extract", lambda a: seen.update(skip_warning=a.skip_warning))
    monkeypatch.setattr(sys, "argv", ["watchdog", "dig", "--skip-warning"])
    cli.main()
    assert seen == {"skip_warning": True}


def test_extract_alias_still_accepts_skip_warning(configured, monkeypatch):
    """The deprecated `extract` alias remaps onto `dig` before argparse runs, so it must still
    parse `dig`'s own flags (#441, D138)."""
    import sys
    seen = {}
    monkeypatch.setattr(cli, "cmd_extract", lambda a: seen.update(skip_warning=a.skip_warning))
    monkeypatch.setattr(sys, "argv", ["watchdog", "extract", "--skip-warning"])
    cli.main()
    assert seen == {"skip_warning": True}


# ── classifier_model config ───────────────────────────────────────────────────

def test_classifier_model_is_a_configurable_key():
    from watchdog.cmd.setup import _CONFIGURE_KEYS
    entry = _CONFIGURE_KEYS["classifier_model"]
    assert entry["default"] == "haiku"
    # Now a free-form [backend:]model string (Claude tier or provider model), not an enum.
    assert entry["type"] == "string"


# ── _resolve_stage: [backend:]model parsing (#125) ─────────────────────────────

def test_resolve_stage_claude_tier_has_no_backend():
    from watchdog.cmd.ingest import _resolve_stage
    assert _resolve_stage(None, None, default="sonnet") == (None, "sonnet")
    assert _resolve_stage("opus", None) == (None, "opus")
    assert _resolve_stage(None, "haiku") == (None, "haiku")        # from config


def test_resolve_stage_explicit_claude_backend():
    from watchdog.cmd.ingest import _resolve_stage
    assert _resolve_stage("claude-api:opus", None) == ("claude-api", "opus")


def test_resolve_stage_claude_batch_backend():
    """claude-batch (#214) parses as a Claude-tier backend, same as claude-api/claude-agent-sdk."""
    from watchdog.cmd.ingest import _resolve_stage
    assert _resolve_stage("claude-batch:sonnet", None) == ("claude-batch", "sonnet")


def test_resolve_stage_non_claude_backend_keeps_raw_model():
    from watchdog.cmd.ingest import _resolve_stage
    assert _resolve_stage("deepseek:deepseek-v4-flash", None) == ("deepseek", "deepseek-v4-flash")
    assert _resolve_stage("openai:gpt-5-mini", None) == ("openai", "gpt-5-mini")


def test_resolve_stage_flag_beats_config():
    from watchdog.cmd.ingest import _resolve_stage
    assert _resolve_stage("openai:gpt-5-mini", "sonnet") == ("openai", "gpt-5-mini")


def test_resolve_stage_unknown_backend_exits():
    from watchdog.cmd.ingest import _resolve_stage
    with pytest.raises(SystemExit, match="unknown backend"):
        _resolve_stage("groq:llama", None)


def test_resolve_stage_non_claude_requires_model():
    from watchdog.cmd.ingest import _resolve_stage
    with pytest.raises(SystemExit, match="needs a model id"):
        _resolve_stage("deepseek:", None)


def test_resolve_stage_bare_non_tier_is_treated_as_claude_and_rejected():
    from watchdog.cmd.ingest import _resolve_stage
    # No backend prefix → interpreted as a Claude tier → invalid (use openai:gpt-5-mini instead).
    with pytest.raises(SystemExit, match="unknown model"):
        _resolve_stage("gpt-5-mini", None)


# ── _resolve_finalizer_overrides: per-stage --finalizer-<stage>-model (#433) ───

def test_resolve_finalizer_overrides_falls_back_to_finalizer_model_when_unset():
    """No per-stage flags/config: every stage resolves to exactly the aggregate finalizer's
    own (backend, model), not a hardcoded default."""
    from watchdog.cmd.ingest import _resolve_finalizer_overrides
    overrides = _resolve_finalizer_overrides(args(), {}, "openai", "gpt-5-mini")
    assert overrides == {
        "reconciliation_backend": "openai", "reconciliation_model": "gpt-5-mini",
        "synthesis_backend": "openai", "synthesis_model": "gpt-5-mini",
        "timeline_backend": "openai", "timeline_model": "gpt-5-mini",
        "briefing_backend": "openai", "briefing_model": "gpt-5-mini",
    }


def test_resolve_finalizer_overrides_flag_overrides_one_stage():
    """A single `--finalizer-<stage>-model` flag overrides only that stage; the others still
    fall back to the aggregate finalizer."""
    from watchdog.cmd.ingest import _resolve_finalizer_overrides
    overrides = _resolve_finalizer_overrides(
        args(finalizer_briefing_model="claude-api:opus"), {}, None, "haiku")
    assert overrides["briefing_backend"] == "claude-api"
    assert overrides["briefing_model"] == "opus"
    assert overrides["reconciliation_backend"] is None
    assert overrides["reconciliation_model"] == "haiku"
    assert overrides["synthesis_backend"] is None
    assert overrides["synthesis_model"] == "haiku"
    assert overrides["timeline_backend"] is None
    assert overrides["timeline_model"] == "haiku"


def test_resolve_finalizer_overrides_config_key_and_flag_precedence():
    """A config-file `finalizer_<stage>_model` sets the stage's default; a flag for the same
    stage still wins, matching `_resolve_stage`'s own flag-beats-config rule."""
    from watchdog.cmd.ingest import _resolve_finalizer_overrides
    config = {"finalizer_synthesis_model": "gemini:gemini-2.5-flash"}
    overrides = _resolve_finalizer_overrides(args(), config, None, "haiku")
    assert overrides["synthesis_backend"] == "gemini"
    assert overrides["synthesis_model"] == "gemini-2.5-flash"

    overrides = _resolve_finalizer_overrides(
        args(finalizer_synthesis_model="opus"), config, None, "haiku")
    assert overrides["synthesis_backend"] is None
    assert overrides["synthesis_model"] == "opus"


# ── cmd_ingest / cmd_finalize: Claude auth only required when a stage needs it (#325) ──

def test_cmd_ingest_does_not_require_claude_auth_when_all_stages_non_claude(wdg_home, tmp_path, monkeypatch):
    """A vault fully configured on another provider (e.g. Gemini) for all three stages must be
    able to ingest even when Claude itself has no auth configured at all."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd.ingest import cmd_ingest
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth",
                         lambda: {"mode": "none", "reason": "api-key mode is set but no key is configured"})
    (wdg_home / "config.json").write_text(json.dumps({
        "classifier_model": "gemini:gemini-2.5-flash",
        "extractor_model": "gemini:gemini-2.5-flash",
        "finalizer_model": "gemini:gemini-2.5-flash",
    }))

    class _Stop(Exception):
        pass

    monkeypatch.setattr("watchdog.pipeline.ingest_setup.run", lambda *a, **k: (_ for _ in ()).throw(_Stop()))

    with pytest.raises(_Stop):
        cmd_ingest(args(), confirm=False)


def test_cmd_ingest_still_requires_claude_auth_when_a_stage_uses_claude(wdg_home, tmp_path, monkeypatch):
    """The auth gate must still fire when at least one of the three stages is Claude-routed."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd.ingest import cmd_ingest
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth",
                         lambda: {"mode": "none", "reason": "auth not configured"})
    (wdg_home / "config.json").write_text(json.dumps({
        "classifier_model": "gemini:gemini-2.5-flash",
        "extractor_model": "sonnet",
        "finalizer_model": "gemini:gemini-2.5-flash",
    }))

    with pytest.raises(SystemExit, match="auth not configured"):
        cmd_ingest(args(), confirm=False)


def test_cmd_finalize_does_not_require_claude_auth_when_finalizer_non_claude(wdg_home, tmp_path, monkeypatch):
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth",
                         lambda: {"mode": "none", "reason": "api-key mode is set but no key is configured"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: True)
    (wdg_home / "config.json").write_text(json.dumps({"finalizer_model": "gemini:gemini-2.5-flash"}))

    class _Stop(Exception):
        pass

    monkeypatch.setattr(ing, "_run_finalize", lambda *a, **k: (_ for _ in ()).throw(_Stop()))

    with pytest.raises(_Stop):
        ing.cmd_finalize(args())


def test_cmd_finalize_threads_skip_briefing_to_run_finalize(wdg_home, tmp_path, monkeypatch):
    """`watchdog finalize --skip-briefing` (#410) reaches `_run_finalize` as `skip_briefing=True`,
    same as `ingest`'s own plumbing."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: True)

    calls = []
    monkeypatch.setattr(ing, "_run_finalize",
                        lambda *a, **k: (calls.append(k), {"synthesized": 0})[1])

    ing.cmd_finalize(args(skip_briefing=True))

    assert len(calls) == 1
    assert calls[0].get("skip_briefing") is True


# ── cmd_ingest: claude-batch validation guards (#214) ──────────────────────────

def _vault_with_queued_doc(tmp_path):
    from tests.test_write_vault import make_vault
    vault = make_vault(tmp_path)
    qdir = vault / ".watchdog" / "queue"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / "sha1.json").write_text(json.dumps({
        "sha256": "sha1", "filename": "a.pdf", "page_count": 1,
        "pages": [{"page": 1, "markdown": "text"}],
        "near_dup": {"near_duplicates": [], "top_similarity": 0.0},
    }))
    (vault / "_INCOMING").mkdir(exist_ok=True)
    return vault


def test_cmd_ingest_claude_batch_needs_no_pinned_skill(wdg_home, tmp_path, monkeypatch):
    """D144: claude-batch no longer requires a run-wide --skill. Each document resolves its own
    skill before the batch is built, so an unpinned mixed-type queue is legal — this used to
    exit with a "pinned skill" error before reaching the orchestrator at all."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd.ingest import cmd_ingest
    from watchdog.pipeline import orchestrate as orch_module
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    (wdg_home / "config.json").write_text(json.dumps({"extractor_model": "claude-batch:sonnet"}))

    seen = {}

    async def _fake_run(vault, **kwargs):
        seen.update(kwargs)
        return {"results": [], "extracted": 0, "skipped": 0, "failed": 0, "batch_pending": True}

    monkeypatch.setattr(orch_module, "run", _fake_run)
    cmd_ingest(args(), confirm=False)
    assert seen, "orchestrate.run was never reached — an earlier guard rejected the run"
    assert seen.get("pinned_skill") is None


def test_cmd_ingest_claude_batch_requires_api_key_auth(wdg_home, tmp_path, monkeypatch):
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd.ingest import cmd_ingest
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "subscription"})
    (wdg_home / "config.json").write_text(json.dumps({"extractor_model": "claude-batch:sonnet"}))
    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("SKILL")

    with pytest.raises(SystemExit, match="api-key auth"):
        cmd_ingest(args(skill=str(skill_file)), confirm=False)


def test_cmd_ingest_rejects_claude_batch_for_classifier_or_finalizer(wdg_home, tmp_path, monkeypatch):
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd.ingest import cmd_ingest
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    (wdg_home / "config.json").write_text(json.dumps({"classifier_model": "claude-batch:sonnet"}))

    with pytest.raises(SystemExit, match="only valid for extractor_model"):
        cmd_ingest(args(), confirm=False)


# ── cmd_ingest --wait (#271) ────────────────────────────────────────────────

def test_wait_seconds_uses_resets_at_plus_buffer(monkeypatch):
    from watchdog.cmd.ingest import _wait_seconds, _WAIT_BUFFER_SECONDS
    now = 1_000_000
    monkeypatch.setattr("time.time", lambda: now)
    seconds, exact = _wait_seconds(now + 100)
    assert exact is True
    assert seconds == 100 + _WAIT_BUFFER_SECONDS


def test_wait_seconds_falls_back_when_resets_at_missing():
    from watchdog.cmd.ingest import _wait_seconds, _WAIT_FALLBACK_SECONDS
    seconds, exact = _wait_seconds(None)
    assert exact is False
    assert seconds == _WAIT_FALLBACK_SECONDS


def test_wait_for_rate_limit_refreshes_lock_in_chunks(tmp_path, monkeypatch):
    """A wait longer than one refresh chunk sleeps in pieces, refreshing the held lock after
    each — so a wait that outlasts the 30-min staleness window never looks abandoned."""
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import locks

    lock_file = tmp_path / ".ingest-lock"
    lock_file.write_text("pid: cli\nstarted_at: 2000-01-01T00:00:00Z\n")

    slept = []
    monkeypatch.setattr(ing.time, "sleep", lambda s: slept.append(s))
    refreshed = []
    monkeypatch.setattr(locks, "refresh_lock", lambda lf: refreshed.append(lf))
    monkeypatch.setattr(ing, "_WAIT_REFRESH_SECONDS", 100)
    monkeypatch.setattr(ing, "_WAIT_FALLBACK_SECONDS", 250)

    ing._wait_for_rate_limit(lock_file, None)   # no resets_at -> fallback sleep

    assert slept == [100, 100, 50]
    assert refreshed == [lock_file, lock_file, lock_file]


def test_merge_summary_accumulates_across_cycles():
    from watchdog.cmd.ingest import _merge_summary
    first = {"results": [{"sha256": "a", "status": "cancelled"}, {"sha256": "b", "status": "ok"}],
             "extracted": 1, "skipped": 0, "failed": 0,
             "usage": {"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.01}}
    second = {"results": [{"sha256": "a", "status": "ok"}],
              "extracted": 1, "skipped": 0, "failed": 0, "rate_limited": False,
              "usage": {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.002}}
    merged = _merge_summary(first, second)
    assert merged["extracted"] == 2
    assert len(merged["results"]) == 2
    assert merged["usage"]["input_tokens"] == 110
    assert merged["usage"]["cost_usd"] == pytest.approx(0.012)
    assert merged["rate_limited"] is False   # non-accumulated fields reflect only the latest cycle


def test_merge_summary_dedupes_a_doc_retried_after_being_cancelled():
    """A doc cancelled by one cycle's rate limit keeps its queue file and is retried by the
    next cycle — its stale "cancelled" stub must not survive alongside its real outcome, or
    the final summary double-counts it (e.g. reporting a fully-extracted doc as also
    "not started")."""
    from watchdog.cmd.ingest import _merge_summary
    first = {"results": [{"sha256": "a", "status": "cancelled"}],
             "extracted": 0, "skipped": 0, "failed": 0}
    second = {"results": [{"sha256": "a", "status": "ok"}],
              "extracted": 1, "skipped": 0, "failed": 0}
    merged = _merge_summary(first, second)
    assert merged["results"] == [{"sha256": "a", "status": "ok"}]


def test_merge_summary_first_call_returns_new_unchanged():
    from watchdog.cmd.ingest import _merge_summary
    new = {"results": [], "extracted": 0, "skipped": 0, "failed": 0}
    assert _merge_summary(None, new) is new


def test_cmd_ingest_wait_rejects_claude_batch(wdg_home, tmp_path, monkeypatch):
    """--wait is submit-and-poll's opposite (block until done) — claude-batch already runs
    in the background, so the two don't compose (#271)."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd.ingest import cmd_ingest
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    (wdg_home / "config.json").write_text(json.dumps({"extractor_model": "claude-batch:sonnet"}))
    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("SKILL")

    with pytest.raises(SystemExit, match="isn't supported with claude-batch"):
        cmd_ingest(args(skill=str(skill_file), wait=True), confirm=False)

def test_cmd_ingest_wait_loops_until_rate_limit_clears(wdg_home, tmp_path, monkeypatch, capsys):
    """--wait resumes automatically after a rate limit: orchestrate.run is re-invoked once the
    (stubbed) wait completes, the lock is released at the end, and the printed summary reflects
    the total across both cycles, not just the last one."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)
    summaries = [
        {"results": [{"sha256": "sha1", "filename": "a.pdf", "status": "cancelled"}],
         "extracted": 0, "skipped": 0, "failed": 0, "cancelled": True,
         "rate_limited": True, "stop_message": "limit", "rate_limit_resets_at": None,
         "quarantined": 0},
        {"results": [{"sha256": "sha1", "filename": "a.pdf", "status": "ok", "entity_count": 2}],
         "extracted": 1, "skipped": 0, "failed": 0, "cancelled": False,
         "rate_limited": False, "stop_message": None, "rate_limit_resets_at": None,
         "quarantined": 0},
    ]
    calls = []

    async def fake_run(*a, **k):
        calls.append(k)
        return summaries[len(calls) - 1]
    monkeypatch.setattr(orch_module, "run", fake_run)

    waited = []
    monkeypatch.setattr(ing, "_wait_for_rate_limit",
                        lambda lock_file, resets_at: waited.append(resets_at))

    ing.cmd_ingest(args(wait=True), confirm=False)

    assert len(calls) == 2
    assert all(k.get("wait") is True for k in calls)
    assert waited == [None]
    assert not (vault / ".watchdog" / "registry" / ".ingest-lock").exists()
    out = capsys.readouterr().out
    assert "1" in out and "extracted" in out   # merged total, not the first cycle's 0
    assert "not started" not in out            # sha1's cycle-1 "cancelled" stub must not survive
                                                # alongside its cycle-2 "ok" outcome


def test_cmd_ingest_no_wait_stops_on_rate_limit_without_looping(wdg_home, tmp_path, monkeypatch):
    """Without --wait (the default), a rate-limited summary is reported once and orchestrate.run
    is not re-invoked — the opt-in flag must not change today's behavior (I4 spirit)."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)

    calls = []

    async def fake_run(*a, **k):
        calls.append(k)
        return {"results": [], "extracted": 0, "skipped": 0, "failed": 0, "cancelled": True,
                "rate_limited": True, "stop_message": "limit", "rate_limit_resets_at": None,
                "quarantined": 0}
    monkeypatch.setattr(orch_module, "run", fake_run)
    monkeypatch.setattr(ing, "_wait_for_rate_limit",
                        lambda *a, **k: pytest.fail("must not wait when --wait is absent"))

    ing.cmd_ingest(args(), confirm=False)

    assert len(calls) == 1
    assert calls[0].get("wait") is False


# ── cmd_ingest no_finalize plumbing (#384; the CLI surface is now `watchdog dig`, #425/#441) ──

def test_cmd_ingest_no_finalize_threads_skip_finalize_to_orchestrate_run(wdg_home, tmp_path, monkeypatch, capsys):
    """Setting `no_finalize` on args (as `cmd_extract` does) reaches `orchestrate.run` as
    `skip_finalize=True`, and the closing block tells the user how to finalize later instead of
    the usual "open a session" line."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)

    calls = []

    async def fake_run(*a, **k):
        calls.append(k)
        return {"results": [{"sha256": "sha1", "filename": "a.pdf", "status": "ok", "entity_count": 1}],
                "extracted": 1, "skipped": 0, "failed": 0, "cancelled": False,
                "rate_limited": False, "stop_message": None, "rate_limit_resets_at": None,
                "quarantined": 0, "finalize_skipped": True}
    monkeypatch.setattr(orch_module, "run", fake_run)

    ing.cmd_ingest(args(no_finalize=True), confirm=False)

    assert len(calls) == 1
    assert calls[0].get("skip_finalize") is True
    out = capsys.readouterr().out
    assert "watchdog bark" in out
    assert "Open a fresh Claude Code session" not in out


def test_cmd_ingest_wait_and_no_finalize_stops_once_queue_drains(wdg_home, tmp_path, monkeypatch):
    """`--wait` loops on a rate-limited *extraction*, not on finalize — with `no_finalize` set,
    finalize never runs at all, so once the queue finishes extracting cleanly the loop must stop
    after a single call rather than waiting for a finalize that will never happen."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)

    calls = []

    async def fake_run(*a, **k):
        calls.append(k)
        return {"results": [{"sha256": "sha1", "filename": "a.pdf", "status": "ok", "entity_count": 1}],
                "extracted": 1, "skipped": 0, "failed": 0, "cancelled": False,
                "rate_limited": False, "stop_message": None, "rate_limit_resets_at": None,
                "quarantined": 0, "finalize_skipped": True}
    monkeypatch.setattr(orch_module, "run", fake_run)
    monkeypatch.setattr(ing, "_wait_for_rate_limit",
                        lambda *a, **k: pytest.fail("must not wait — nothing rate-limited"))

    ing.cmd_ingest(args(wait=True, no_finalize=True), confirm=False)

    assert len(calls) == 1
    assert calls[0].get("wait") is True
    assert calls[0].get("skip_finalize") is True


# ── cmd_ingest --skip-briefing plumbing (#410) ───────────────────────────────

def test_cmd_ingest_skip_briefing_threads_to_orchestrate_run(wdg_home, tmp_path, monkeypatch, capsys):
    """`--skip-briefing` reaches `orchestrate.run` as `skip_briefing=True` — synthesis and the
    timeline still run (`finalize_skipped` stays False), only the briefing model call is
    skipped, so the closing block shows the usual "open a session" line."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)

    calls = []

    async def fake_run(*a, **k):
        calls.append(k)
        return {"results": [{"sha256": "sha1", "filename": "a.pdf", "status": "ok", "entity_count": 1}],
                "extracted": 1, "skipped": 0, "failed": 0, "cancelled": False,
                "rate_limited": False, "stop_message": None, "rate_limit_resets_at": None,
                "quarantined": 0, "finalize_skipped": False,
                "post_ingest": {"synthesized": 0, "timeline_collisions": 0, "briefing": None,
                                "briefing_skipped": True, "merged": [], "contradictions": []}}
    monkeypatch.setattr(orch_module, "run", fake_run)

    ing.cmd_ingest(args(skip_briefing=True), confirm=False)

    assert len(calls) == 1
    assert calls[0].get("skip_briefing") is True
    out = capsys.readouterr().out
    assert "Briefing skipped" in out
    assert "--skip-briefing" in out


# ── watchdog dig (#425, renamed from `extract` in #441/D138) ─────────────────

def test_ingest_parser_no_longer_accepts_no_finalize(monkeypatch, capsys):
    """`--no-finalize` is fully replaced by `watchdog dig` (#425) — the pre-1.0 app carries
    no deprecation period, so `ingest` must reject the flag outright."""
    import sys
    monkeypatch.setattr(sys, "argv", ["watchdog", "ingest", "--no-finalize"])
    with pytest.raises(SystemExit):
        cli.main()
    err = capsys.readouterr().err
    assert "unrecognized" in err


def test_cmd_extract_threads_skip_finalize_to_orchestrate_run(wdg_home, tmp_path, monkeypatch, capsys):
    """`watchdog dig` is `cmd_ingest` with finalization forced off: it must reach
    `orchestrate.run` with `skip_finalize=True` and print the "run watchdog bark" hint."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)
    monkeypatch.setattr(ing.interactive, "pick", lambda *a, **k: 0)   # "Ingest now"

    calls = []

    async def fake_run(*a, **k):
        calls.append(k)
        return {"results": [{"sha256": "sha1", "filename": "a.pdf", "status": "ok", "entity_count": 1}],
                "extracted": 1, "skipped": 0, "failed": 0, "cancelled": False,
                "rate_limited": False, "stop_message": None, "rate_limit_resets_at": None,
                "quarantined": 0, "finalize_skipped": True}
    monkeypatch.setattr(orch_module, "run", fake_run)

    ing.cmd_extract(args())

    assert len(calls) == 1
    assert calls[0].get("skip_finalize") is True
    out = _strip_ansi(capsys.readouterr().out)
    assert "watchdog bark" in out


def test_cmd_extract_sets_no_finalize_on_args(wdg_home, tmp_path, monkeypatch):
    """cmd_extract mutates the passed-in args to force no_finalize, then delegates to cmd_ingest
    unchanged — verified directly against cmd_ingest's call signature."""
    from watchdog.cmd import ingest as ing

    seen = {}
    monkeypatch.setattr(ing, "cmd_ingest",
                        lambda a, **kw: seen.update(no_finalize=a.no_finalize, **kw))
    a = args()
    ing.cmd_extract(a)
    assert seen == {"no_finalize": True, "non_interactive": False}


def test_dig_parser_rejects_finalizer_model(monkeypatch, capsys):
    """`dig` gets extraction-only flags — `--finalizer-model` belongs to `ingest`/`bark`,
    not `dig` (dig IS no-finalize, so there is nothing to finalize with)."""
    import sys
    monkeypatch.setattr(sys, "argv", ["watchdog", "dig", "--finalizer-model", "sonnet"])
    with pytest.raises(SystemExit):
        cli.main()
    err = capsys.readouterr().err
    assert "unrecognized" in err


def test_dig_parser_rejects_finalizer_stage_model(monkeypatch, capsys):
    """The per-stage finalizer overrides (#433) belong to `ingest`/`bark` too, for the same
    reason as the aggregate `--finalizer-model`."""
    import sys
    monkeypatch.setattr(sys, "argv", ["watchdog", "dig", "--finalizer-briefing-model", "sonnet"])
    with pytest.raises(SystemExit):
        cli.main()
    err = capsys.readouterr().err
    assert "unrecognized" in err


def test_ingest_parser_accepts_finalizer_stage_models(configured, monkeypatch):
    """All four `--finalizer-<stage>-model` flags parse on `ingest`."""
    import sys
    seen = {}
    monkeypatch.setattr(cli, "cmd_ingest", lambda a: seen.update(
        reconciliation=a.finalizer_reconciliation_model, synthesis=a.finalizer_synthesis_model,
        timeline=a.finalizer_timeline_model, briefing=a.finalizer_briefing_model))
    monkeypatch.setattr(sys, "argv", [
        "watchdog", "ingest",
        "--finalizer-reconciliation-model", "opus",
        "--finalizer-synthesis-model", "deepseek:deepseek-v4-flash",
        "--finalizer-timeline-model", "openai:gpt-5-mini",
        "--finalizer-briefing-model", "haiku",
    ])
    cli.main()
    assert seen == {
        "reconciliation": "opus", "synthesis": "deepseek:deepseek-v4-flash",
        "timeline": "openai:gpt-5-mini", "briefing": "haiku",
    }


def test_bark_parser_accepts_finalizer_stage_models(configured, monkeypatch):
    import sys
    seen = {}
    monkeypatch.setattr(cli, "cmd_finalize", lambda a: seen.update(
        briefing=a.finalizer_briefing_model))
    monkeypatch.setattr(sys, "argv", ["watchdog", "bark", "--finalizer-briefing-model", "opus"])
    cli.main()
    assert seen == {"briefing": "opus"}


def test_dig_command_appears_in_help(monkeypatch, capsys):
    """`watchdog dig --help` must work — i.e. `dig` is a registered subcommand."""
    import sys
    monkeypatch.setattr(sys, "argv", ["watchdog", "dig", "--help"])
    with pytest.raises(SystemExit):
        cli.main()
    out = capsys.readouterr().out
    assert "dig" in out
    assert "--extractor-model" in out


def test_extract_alias_warns_and_dispatches_to_dig(configured, monkeypatch, capsys):
    """`watchdog extract` (#441, D138) is a deprecated alias for `watchdog dig` — same flags,
    same function, but the CLI must warn before dispatching."""
    import sys
    seen = {}
    monkeypatch.setattr(cli, "cmd_extract", lambda a: seen.update(ran=True))
    monkeypatch.setattr(sys, "argv", ["watchdog", "extract"])
    cli.main()
    assert seen == {"ran": True}
    out = _strip_ansi(capsys.readouterr().out)
    assert "deprecated" in out
    assert "watchdog dig" in out


def test_finalize_alias_warns_and_dispatches_to_bark(configured, monkeypatch, capsys):
    """`watchdog finalize` (#441, D138) is a deprecated alias for `watchdog bark`."""
    import sys
    seen = {}
    monkeypatch.setattr(cli, "cmd_finalize", lambda a: seen.update(ran=True))
    monkeypatch.setattr(sys, "argv", ["watchdog", "finalize"])
    cli.main()
    assert seen == {"ran": True}
    out = _strip_ansi(capsys.readouterr().out)
    assert "deprecated" in out
    assert "watchdog bark" in out


def test_ingest_command_warns_deprecated(configured, monkeypatch, capsys):
    """`watchdog ingest` (#441, D138) still works during the deprecation window, but must warn
    and point at the guided walk / `dig`+`bark` — it has no single renamed successor."""
    import sys
    seen = {}
    monkeypatch.setattr(cli, "cmd_ingest", lambda a: seen.update(ran=True))
    monkeypatch.setattr(sys, "argv", ["watchdog", "ingest"])
    cli.main()
    assert seen == {"ran": True}
    out = _strip_ansi(capsys.readouterr().out)
    assert "deprecated" in out
    assert "watchdog dig" in out
    assert "watchdog bark" in out


def test_cmd_ingest_prints_backup_hint_when_discard_snapshotted(wdg_home, tmp_path, monkeypatch, capsys):
    """#270: when ingest_setup.run() reports a backup_dir (the discard choice actually threw
    something away), cmd_ingest must print a restore hint."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)
    backup_dir = vault / ".watchdog" / "backups" / "20260101T000000Z-ingest-discard"
    backup_dir.mkdir(parents=True)
    monkeypatch.setattr("watchdog.pipeline.ingest_setup.run", lambda *a, **k: {
        "lock_acquired": True, "total": 1,
        "queue_files": [{"path": "q", "sha256": "sha1", "filename": "a.pdf",
                          "document_type": None, "page_count": 1, "est_tokens": 10}],
        "backup_dir": str(backup_dir),
    })

    class _Stop(Exception):
        pass

    monkeypatch.setattr(ing, "_resolve_pinned_skill", lambda *a, **k: (_ for _ in ()).throw(_Stop()))
    with pytest.raises(_Stop):
        ing.cmd_ingest(args(), confirm=False)

    out = _strip_ansi(capsys.readouterr().out)
    assert "backup:" in out
    assert "ingest-discard" in out
    assert "undo" in out


def test_cmd_ingest_and_cmd_finalize_agree_on_finalizer_default(wdg_home, tmp_path, monkeypatch):
    """An unconfigured vault must finalize on the same model whether ingest finishes the
    batch itself or a separate `watchdog finalize` completes it (#253)."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})

    finalizer_defaults = {}
    orig_resolve = ing._resolve_stage

    class _Stop(Exception):
        pass

    def _boom(*a, **k):
        raise _Stop()

    # cmd_ingest: skip past the pending-finalization prompt, stop right after model
    # resolution (before the heavy ingest_setup/orchestrate pipeline runs).
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)
    calls = []
    monkeypatch.setattr(ing, "_resolve_stage",
                         lambda *a, **k: (calls.append((a, k)), orig_resolve(*a, **k))[1])
    monkeypatch.setattr("watchdog.pipeline.ingest_setup.run", _boom)
    with pytest.raises(_Stop):
        ing.cmd_ingest(args(), confirm=False)
    # Call order in cmd_ingest is extractor, finalizer, classifier.
    _, finalizer_kwargs = calls[1]
    finalizer_defaults["ingest"] = finalizer_kwargs.get("default")

    # cmd_finalize: needs a pending finalization to proceed past its early-return guard.
    calls.clear()
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: True)
    monkeypatch.setattr(ing, "_run_finalize", _boom)
    with pytest.raises(_Stop):
        ing.cmd_finalize(args())
    _, finalizer_kwargs = calls[0]
    finalizer_defaults["finalize"] = finalizer_kwargs.get("default")

    assert finalizer_defaults["ingest"] == finalizer_defaults["finalize"] == "haiku"


def test_pending_batch_dialog_flags_spend_and_safety(wdg_home, tmp_path, monkeypatch):
    """The pending-finalization dialog's "Finalize it now" and "Discard" options used to give
    no hint that one spends real money right now and the other is actually safe/non-destructive
    (#458) — a reader had to already know the internals to tell them apart."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: True)
    monkeypatch.setattr(orch_module, "pending_finalization", lambda v: {"docs": 1, "entities": 0})

    class _Stop(Exception):
        pass

    monkeypatch.setattr("watchdog.pipeline.ingest_setup.run",
                        lambda *a, **k: (_ for _ in ()).throw(_Stop()))

    captured = {}

    def _fake_pick(choices, *a, **k):
        captured["choices"] = choices
        return 0

    monkeypatch.setattr(ing.interactive, "pick", _fake_pick)

    with pytest.raises(_Stop):
        ing.cmd_ingest(args(), confirm=False)
    finalize_label, discard_label = captured["choices"][1], captured["choices"][2]
    assert "real model spend" in finalize_label
    assert "safe" in discard_label


def test_pending_batch_merge_label_reflects_dig_vs_ingest(wdg_home, tmp_path, monkeypatch):
    """`dig` never finalizes in the same run it's invoked from — its pending-finalization
    dialog must not claim merging "finalizes everything together"; only `watchdog bark` does
    that later. The old combined pipeline (bare `watchdog`/`ingest`) does finalize inline, so
    keeps the original wording (#456)."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: True)
    monkeypatch.setattr(orch_module, "pending_finalization", lambda v: {"docs": 1, "entities": 0})

    class _Stop(Exception):
        pass

    monkeypatch.setattr("watchdog.pipeline.ingest_setup.run",
                        lambda *a, **k: (_ for _ in ()).throw(_Stop()))

    captured = {}

    def _fake_pick(choices, *a, **k):
        captured["choices"] = choices
        return 0   # Merge

    monkeypatch.setattr(ing.interactive, "pick", _fake_pick)

    with pytest.raises(_Stop):
        ing.cmd_ingest(args(command="dig"), confirm=False)
    dig_merge_label = captured["choices"][0]
    assert "watchdog bark" in dig_merge_label
    assert "then finalize everything together" not in dig_merge_label

    captured.clear()
    with pytest.raises(_Stop):
        ing.cmd_ingest(args(), confirm=False)
    bare_merge_label = captured["choices"][0]
    assert "then finalize everything together" in bare_merge_label


def test_pending_batch_dialog_omits_finalize_for_dig(wdg_home, tmp_path, monkeypatch):
    """`watchdog dig` is documented to stop before finalization (#456) — its pending-finalization
    dialog must not offer "Finalize it now", since dig itself can never carry that out. The bare
    guided walk still offers all three options."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: True)
    monkeypatch.setattr(orch_module, "pending_finalization", lambda v: {"docs": 1, "entities": 0})

    class _Stop(Exception):
        pass

    monkeypatch.setattr("watchdog.pipeline.ingest_setup.run",
                        lambda *a, **k: (_ for _ in ()).throw(_Stop()))

    captured = {}

    def _fake_pick(choices, *a, **k):
        captured["choices"] = choices
        return len(choices) - 1   # discard, whatever index that lands on

    monkeypatch.setattr(ing.interactive, "pick", _fake_pick)

    # dig: only merge + discard, no "finalize it now".
    with pytest.raises(_Stop):
        ing.cmd_ingest(args(command="dig"), confirm=False)
    assert len(captured["choices"]) == 2
    assert not any("Finalize it now" in c for c in captured["choices"])
    assert "Discard" in captured["choices"][1]

    # bare guided walk: still all three, finalize included.
    captured.clear()
    with pytest.raises(_Stop):
        ing.cmd_ingest(args(), confirm=False)
    assert len(captured["choices"]) == 3
    assert any("Finalize it now" in c for c in captured["choices"])


# ── non_interactive: programmatic callers must never block on a human prompt (#494) ────────────

def test_cmd_ingest_non_interactive_refuses_pending_batch_instead_of_prompting(wdg_home, tmp_path, monkeypatch):
    """A programmatic caller (run_benchmark.py driving cmd_extract) has no human to answer the
    merge/discard/finalize pick — non_interactive=True must fail loud instead of blocking on it."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: True)
    monkeypatch.setattr(orch_module, "pending_finalization", lambda v: {"docs": 1, "entities": 0})

    def _boom(*a, **k):
        raise AssertionError("interactive.pick must not be called in non_interactive mode")
    monkeypatch.setattr(ing.interactive, "pick", _boom)

    with pytest.raises(SystemExit, match="non-interactive run"):
        ing.cmd_ingest(args(), confirm=False, non_interactive=True)


def test_cmd_extract_threads_non_interactive_through_to_cmd_ingest(wdg_home, tmp_path, monkeypatch):
    """`cmd_extract` (the `dig` entry point run_benchmark.py actually calls) must pass its
    `non_interactive` kwarg through rather than dropping it."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: True)
    monkeypatch.setattr(orch_module, "pending_finalization", lambda v: {"docs": 1, "entities": 0})

    def _boom(*a, **k):
        raise AssertionError("interactive.pick must not be called in non_interactive mode")
    monkeypatch.setattr(ing.interactive, "pick", _boom)

    with pytest.raises(SystemExit, match="non-interactive run"):
        ing.cmd_extract(args(command="dig"), non_interactive=True)


def test_cmd_ingest_non_interactive_skips_quarantine_requeue_offer(wdg_home, tmp_path, monkeypatch):
    """An empty active queue with documents parked in _failed/ normally offers to requeue and
    retry right there (#406) — a non-interactive caller must not block on that confirm either,
    and falls through to the same "nothing queued" outcome as declining it."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_failed_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)

    def _boom(*a, **k):
        raise AssertionError("interactive.confirm must not be called in non_interactive mode")
    monkeypatch.setattr(ing.interactive, "confirm", _boom)
    monkeypatch.setattr(orch_module, "run",
                        lambda *a, **k: pytest.fail("must not run — the requeue offer was never accepted"))

    ing.cmd_ingest(args(), confirm=False, non_interactive=True)

    assert (vault / ".watchdog" / "queue" / "_failed" / "shafail.json").exists()


def test_format_models_line_shows_concurrency_only_when_explicitly_set(monkeypatch):
    """`--concurrency` was silently dropped from the pre-run summary (#456) — it should only
    appear when the user actually passed it, not for a run left at the default/config value."""
    from watchdog.cmd import ingest as ing

    without = ing._format_models_line("haiku", "haiku", None, "sonnet", None, "haiku",
                                      "medium", None, None, concurrency=None)
    assert "concurrency" not in without

    with_value = ing._format_models_line("haiku", "haiku", None, "sonnet", None, "haiku",
                                         "medium", None, None, concurrency=2)
    assert "concurrency" in with_value
    assert "2" in with_value.splitlines()[-1]


def test_format_models_line_omits_finalizer_for_dig(monkeypatch):
    """`watchdog dig` always stops before finalization in the same run (#456) — showing which
    model would finalize is irrelevant noise there, unlike the bare guided walk or `ingest`,
    which do finalize inline and still need the row."""
    from watchdog.cmd import ingest as ing

    for_dig = ing._format_models_line("haiku", "haiku", None, "sonnet", None, "haiku",
                                      "medium", None, None, is_dig=True)
    assert "finalizer" not in for_dig

    not_dig = ing._format_models_line("haiku", "haiku", None, "sonnet", None, "haiku",
                                      "medium", None, None, is_dig=False)
    assert "finalizer" in not_dig


# ── ingest --estimate (#269) ────────────────────────────────────────────────────

def test_cmd_ingest_estimate_prints_and_exits_without_lock(wdg_home, tmp_path, monkeypatch, capsys):
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd.ingest import cmd_ingest
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})

    cmd_ingest(args(estimate=True), confirm=False)

    out = _strip_ansi(capsys.readouterr().out)
    assert "1 document" in out
    assert "tokens in" in out
    assert not (vault / ".watchdog" / "registry" / ".ingest-lock").exists()
    assert not (vault / ".watchdog" / "ingest-state.json").exists()


def test_cmd_ingest_estimate_empty_queue(wdg_home, tmp_path, monkeypatch, capsys):
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd.ingest import cmd_ingest
    from tests.test_write_vault import make_vault
    vault = make_vault(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})

    cmd_ingest(args(estimate=True), confirm=False)

    assert "nothing to estimate" in capsys.readouterr().out


def test_cmd_ingest_estimate_subscription_mode_shows_no_dollar_figure(wdg_home, tmp_path, monkeypatch, capsys):
    """Subscription auth never gets a dollar figure (#269) — there's no real billing to
    project, even if this vault happens to have usage history on disk."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd.ingest import cmd_ingest
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "subscription"})
    (vault / ".watchdog" / "registry" / "usage-20260101T000000Z.json").write_text(json.dumps({
        "calls": [], "totals": {"input_tokens": 1000, "output_tokens": 0,
                                 "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 5.0},
    }))

    cmd_ingest(args(estimate=True), confirm=False)

    out = capsys.readouterr().out
    assert "$" not in out
    assert "tokens in" in out


def test_cmd_ingest_estimate_api_key_with_usage_history_shows_dollar_range(wdg_home, tmp_path, monkeypatch, capsys):
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd.ingest import cmd_ingest
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    (vault / ".watchdog" / "registry" / "usage-20260101T000000Z.json").write_text(json.dumps({
        "calls": [], "totals": {"input_tokens": 1000, "output_tokens": 0,
                                 "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 5.0},
    }))

    cmd_ingest(args(estimate=True), confirm=False)

    out = _strip_ansi(capsys.readouterr().out)
    assert "$" in out
    assert "based on your last run" in out


# ── ingest/finalize --estimate-all (#469) ────────────────────────────────────────

def test_cmd_ingest_estimate_all_prints_per_model_table(wdg_home, tmp_path, monkeypatch, capsys):
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd.ingest import cmd_ingest
    from watchdog.model_catalog import all_models
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    (vault / ".watchdog" / "registry" / "usage-20260101T000000Z.json").write_text(json.dumps({
        "calls": [], "totals": {"input_tokens": 1000, "output_tokens": 500,
                                 "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 5.0},
    }))

    cmd_ingest(args(estimate=True, estimate_all=True), confirm=False)

    out = _strip_ansi(capsys.readouterr().out)
    assert "Projected list price by model" in out
    for m in all_models():
        assert m["name"] in out


def test_cmd_ingest_estimate_all_without_estimate_flag_still_projects(wdg_home, tmp_path, monkeypatch, capsys):
    """--estimate-all alone (no --estimate) still takes the read-only estimate path — a user
    shouldn't have to pass both flags to get the catalog comparison."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd.ingest import cmd_ingest
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    (vault / ".watchdog" / "registry" / "usage-20260101T000000Z.json").write_text(json.dumps({
        "calls": [], "totals": {"input_tokens": 1000, "output_tokens": 500,
                                 "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 5.0},
    }))

    class _Stop(Exception):
        pass
    monkeypatch.setattr("watchdog.pipeline.ingest_setup.run", lambda *a, **k: (_ for _ in ()).throw(_Stop()))

    cmd_ingest(args(estimate_all=True), confirm=False)   # must not reach ingest_setup.run

    out = _strip_ansi(capsys.readouterr().out)
    assert "Projected list price by model" in out


def test_cmd_ingest_estimate_all_no_usage_history_shows_hint(wdg_home, tmp_path, monkeypatch, capsys):
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd.ingest import cmd_ingest
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})

    cmd_ingest(args(estimate=True, estimate_all=True), confirm=False)

    out = _strip_ansi(capsys.readouterr().out)
    assert "Not enough usage history yet" in out


# ── finalize --estimate (#417) ──────────────────────────────────────────────────

def _vault_with_staged_finalize_corpus(tmp_path):
    """A vault with a pending (staged, not yet finalized) batch in `.watchdog/tmp/` —
    what `watchdog finalize --estimate` prices."""
    from tests.test_write_vault import make_vault
    vault = make_vault(tmp_path)
    tmp = vault / ".watchdog" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "result_sha1.json").write_text(json.dumps({"sha256": "sha1", "key_facts": "a" * 400}))
    (tmp / "notes_sha1.md").write_text("a" * 400)
    return vault


def test_cmd_finalize_estimate_prints_and_exits_without_lock(wdg_home, tmp_path, monkeypatch, capsys):
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    vault = _vault_with_staged_finalize_corpus(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})

    ing.cmd_finalize(args(estimate=True))

    out = _strip_ansi(capsys.readouterr().out)
    assert "1 document" in out
    assert "tokens in" in out
    assert not (vault / ".watchdog" / "registry" / ".ingest-lock").exists()


def test_cmd_finalize_estimate_nothing_pending(wdg_home, tmp_path, monkeypatch, capsys):
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from tests.test_write_vault import make_vault
    vault = make_vault(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})

    ing.cmd_finalize(args(estimate=True))

    assert "Nothing to finalize" in capsys.readouterr().out


def test_cmd_finalize_estimate_subscription_mode_shows_no_dollar_figure(wdg_home, tmp_path, monkeypatch, capsys):
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    vault = _vault_with_staged_finalize_corpus(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "subscription"})
    (vault / ".watchdog" / "registry" / "usage-20260101T000000Z.json").write_text(json.dumps({
        "calls": [{"task": "reconcile"}],
        "totals": {"input_tokens": 1000, "cost_usd": 5.0},
    }))

    ing.cmd_finalize(args(estimate=True))

    out = capsys.readouterr().out
    assert "$" not in out
    assert "tokens in" in out


def test_cmd_finalize_estimate_shows_dollar_range_from_standalone_history(wdg_home, tmp_path, monkeypatch, capsys):
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    vault = _vault_with_staged_finalize_corpus(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    (vault / ".watchdog" / "registry" / "usage-20260101T000000Z.json").write_text(json.dumps({
        "calls": [{"task": "reconcile"}, {"task": "briefing"}],
        "totals": {"input_tokens": 1000, "cost_usd": 5.0},
    }))

    ing.cmd_finalize(args(estimate=True))

    out = _strip_ansi(capsys.readouterr().out)
    assert "$" in out
    assert "based on your last standalone finalize" in out


def test_cmd_finalize_estimate_ignores_mixed_ingest_usage_history(wdg_home, tmp_path, monkeypatch, capsys):
    """A usage file from a full `watchdog ingest` run (extraction + finalize sharing one file)
    must not be mistaken for a standalone finalize's own $/token profile."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    vault = _vault_with_staged_finalize_corpus(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    (vault / ".watchdog" / "registry" / "usage-20260101T000000Z.json").write_text(json.dumps({
        "calls": [{"task": "extract"}, {"task": "reconcile"}],
        "totals": {"input_tokens": 500000, "cost_usd": 5.0, "est_input_tokens": 480000},
    }))

    ing.cmd_finalize(args(estimate=True))

    out = capsys.readouterr().out
    assert "$" not in out   # no standalone-finalize history to price against


def test_cmd_finalize_estimate_all_prints_per_model_table(wdg_home, tmp_path, monkeypatch, capsys):
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.model_catalog import all_models
    vault = _vault_with_staged_finalize_corpus(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    (vault / ".watchdog" / "registry" / "usage-20260101T000000Z.json").write_text(json.dumps({
        "calls": [{"task": "reconcile"}, {"task": "briefing"}],
        "totals": {"input_tokens": 1000, "output_tokens": 500, "cost_usd": 5.0},
    }))

    ing.cmd_finalize(args(estimate_all=True))

    out = _strip_ansi(capsys.readouterr().out)
    assert "Projected list price by model" in out
    for m in all_models():
        assert m["name"] in out


def test_cmd_finalize_estimate_all_no_standalone_history_shows_hint(wdg_home, tmp_path, monkeypatch, capsys):
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    vault = _vault_with_staged_finalize_corpus(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})

    ing.cmd_finalize(args(estimate_all=True))

    out = _strip_ansi(capsys.readouterr().out)
    assert "Not enough usage history yet" in out


# ── quarantined (_failed/) documents surfaced (#406) ──────────────────────────

def _vault_with_failed_doc(tmp_path):
    from tests.test_write_vault import make_vault
    vault = make_vault(tmp_path)
    failed_dir = vault / ".watchdog" / "queue" / "_failed"
    failed_dir.mkdir(parents=True, exist_ok=True)
    (failed_dir / "shafail.json").write_text(json.dumps({
        "sha256": "shafail", "filename": "bad.pdf", "page_count": 1,
        "pages": [{"page": 1, "markdown": "text"}],
        "near_dup": {"near_duplicates": [], "top_similarity": 0.0},
    }))
    (vault / "_INCOMING").mkdir(exist_ok=True)
    return vault


def test_cmd_ingest_estimate_empty_queue_with_failed_docs_mentions_requeue(wdg_home, tmp_path, monkeypatch, capsys):
    """--estimate stays read-only (#406): it must say a document is quarantined instead of the
    plain "queue is empty" — and must not move it out of _failed/ itself."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd.ingest import cmd_ingest
    vault = _vault_with_failed_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})

    cmd_ingest(args(estimate=True), confirm=False)

    out = _strip_ansi(capsys.readouterr().out)
    assert "need attention" in out
    assert "queue/_failed/" in out
    assert "watchdog requeue" in out
    assert "nothing to estimate" not in out
    assert (vault / ".watchdog" / "queue" / "_failed" / "shafail.json").exists()
    assert not (vault / ".watchdog" / "queue" / "shafail.json").exists()


def test_cmd_ingest_empty_queue_accepts_requeue_offer_and_continues(wdg_home, tmp_path, monkeypatch):
    """When the active queue is empty but _failed/ isn't, bare `watchdog ingest` offers to
    requeue right there (#406); accepting moves the document back into the active queue and lets
    the ingest proceed, instead of just pointing at `watchdog requeue` and stopping."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module
    from watchdog import interactive

    vault = _vault_with_failed_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)
    monkeypatch.setattr(interactive, "confirm", lambda *a, **k: True)

    calls = []

    async def fake_run(*a, **k):
        calls.append(k)
        return {"results": [{"sha256": "shafail", "filename": "bad.pdf", "status": "ok", "entity_count": 1}],
                "extracted": 1, "skipped": 0, "failed": 0, "cancelled": False,
                "rate_limited": False, "stop_message": None, "rate_limit_resets_at": None,
                "quarantined": 0}
    monkeypatch.setattr(orch_module, "run", fake_run)

    ing.cmd_ingest(args(), confirm=False)

    assert len(calls) == 1   # the ingest actually proceeded, not just printed a hint
    assert not (vault / ".watchdog" / "queue" / "_failed" / "shafail.json").exists()


def test_cmd_ingest_empty_queue_declines_requeue_offer_prints_hint(wdg_home, tmp_path, monkeypatch, capsys):
    """Declining the requeue offer leaves the document in _failed/ untouched and never runs an
    ingest — just the same hint `watchdog usage`/the normal summary already gives."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module
    from watchdog import interactive

    vault = _vault_with_failed_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)
    monkeypatch.setattr(interactive, "confirm", lambda *a, **k: False)
    monkeypatch.setattr(orch_module, "run",
                        lambda *a, **k: pytest.fail("must not run when the requeue offer is declined"))

    ing.cmd_ingest(args(), confirm=False)

    out = _strip_ansi(capsys.readouterr().out)
    assert "watchdog requeue" in out
    assert (vault / ".watchdog" / "queue" / "_failed" / "shafail.json").exists()


def test_cmd_ingest_requeue_offer_race_does_not_dead_end(wdg_home, tmp_path, monkeypatch, capsys):
    """If something else (e.g. a concurrent `watchdog requeue`) empties _failed/ between the
    count that triggered the offer and the confirmed requeue actually running, the final hint
    must not tell the user to run `watchdog requeue` — there's nothing left there for it to move
    (PR #437 review)."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module
    from watchdog import interactive

    vault = _vault_with_failed_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)
    monkeypatch.setattr(interactive, "confirm", lambda *a, **k: True)

    real_requeue_failed = ing._requeue_failed

    def racing_requeue_failed(v):
        (v / ".watchdog" / "queue" / "_failed" / "shafail.json").unlink()
        return real_requeue_failed(v)
    monkeypatch.setattr(ing, "_requeue_failed", racing_requeue_failed)

    ing.cmd_ingest(args(), confirm=False)

    out = _strip_ansi(capsys.readouterr().out)
    assert "watchdog requeue" not in out
    assert "Queue is empty" in out


def test_cmd_ingest_keyboard_interrupt_mentions_quarantined_docs(wdg_home, tmp_path, monkeypatch, capsys):
    """A Ctrl+C that propagates as a real `KeyboardInterrupt` — the only path a Ctrl+C during
    finalize's sequential post-processing takes (#406), since `orchestrate.run`'s own SIGINT
    handling only covers concurrent extraction — must still mention a document already
    quarantined earlier in the same run. Previously only the normal-completion summary did."""
    from watchdog.cmd import auth as auth_module
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    failed_dir = vault / ".watchdog" / "queue" / "_failed"
    failed_dir.mkdir(parents=True, exist_ok=True)
    (failed_dir / "shafail.json").write_text(json.dumps({"sha256": "shafail", "filename": "bad.pdf"}))
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)

    async def boom(*a, **k):
        raise KeyboardInterrupt()
    monkeypatch.setattr(orch_module, "run", boom)

    with pytest.raises(SystemExit) as exc_info:
        ing.cmd_ingest(args(), confirm=False)
    assert exc_info.value.code == 130

    out = _strip_ansi(capsys.readouterr().out)
    assert "Ingest cancelled" in out
    assert "watchdog requeue" in out
    assert "queue/_failed/" in out


# ── caffeinate (#415) ─────────────────────────────────────────────────────────

class _FakeProc:
    """Stand-in for `subprocess.Popen`'s return value, shared by the caffeinate tests below —
    tracks `terminate()`/`kill()`/`wait()` calls without touching a real process."""
    def __init__(self, pid=99999):
        self.pid = pid
        self.terminated = False
        self.killed = False
        self.waited = False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waited = True


def test_caffeinate_spawns_on_darwin_when_available(monkeypatch):
    from watchdog.cmd import ingest as ing

    monkeypatch.setattr("watchdog.cmd.ingest.sys.platform", "darwin")
    monkeypatch.setattr(ing.shutil, "which",
                        lambda name: "/usr/bin/caffeinate" if name == "caffeinate" else None)

    fake_proc = _FakeProc()
    calls = []

    def fake_popen(cmd, **kwargs):
        calls.append(cmd)
        return fake_proc
    monkeypatch.setattr(ing.subprocess, "Popen", fake_popen)

    with ing._caffeinate():
        assert calls == [["caffeinate", "-i", "-w", str(ing.os.getpid())]]
        assert not fake_proc.terminated
    assert fake_proc.terminated
    assert fake_proc.waited


def test_caffeinate_spawns_systemd_inhibit_on_linux_when_available(monkeypatch):
    """Linux has no macOS-style "watch this pid" flag, so `systemd-inhibit` wraps a placeholder
    command instead (#415) — killing the whole process group on the way out, not just the
    `systemd-inhibit` process itself, so that placeholder isn't left running as an orphan."""
    from watchdog.cmd import ingest as ing

    monkeypatch.setattr("watchdog.cmd.ingest.sys.platform", "linux")
    monkeypatch.setattr(ing.shutil, "which",
                        lambda name: "/usr/bin/systemd-inhibit" if name == "systemd-inhibit" else None)

    fake_proc = _FakeProc(pid=54321)
    calls = []

    def fake_popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return fake_proc
    monkeypatch.setattr(ing.subprocess, "Popen", fake_popen)
    killpg_calls = []
    monkeypatch.setattr(ing.os, "killpg", lambda pid, sig: killpg_calls.append((pid, sig)))

    with ing._caffeinate():
        assert len(calls) == 1
        cmd, kwargs = calls[0]
        assert cmd[0] == "systemd-inhibit"
        assert kwargs.get("start_new_session") is True
        assert not killpg_calls

    assert killpg_calls == [(54321, ing.signal.SIGTERM)]
    assert fake_proc.waited
    assert not fake_proc.terminated   # cleanup goes through the process group, not proc.terminate()


def test_caffeinate_systemd_inhibit_sigkills_the_group_on_timeout(monkeypatch):
    """If the group doesn't die from SIGTERM in time, the SIGKILL fallback must also target the
    whole process group — not just `proc.kill()` on the `systemd-inhibit` process itself, which
    would risk leaving the `sleep infinity` placeholder running as an orphan (PR #437 review)."""
    from watchdog.cmd import ingest as ing

    monkeypatch.setattr("watchdog.cmd.ingest.sys.platform", "linux")
    monkeypatch.setattr(ing.shutil, "which",
                        lambda name: "/usr/bin/systemd-inhibit" if name == "systemd-inhibit" else None)

    class _StuckFakeProc(_FakeProc):
        def __init__(self, pid):
            super().__init__(pid=pid)
            self.wait_calls = 0

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise ing.subprocess.TimeoutExpired(cmd="systemd-inhibit", timeout=timeout)

    fake_proc = _StuckFakeProc(pid=54321)
    monkeypatch.setattr(ing.subprocess, "Popen", lambda *a, **k: fake_proc)
    killpg_calls = []
    monkeypatch.setattr(ing.os, "killpg", lambda pid, sig: killpg_calls.append((pid, sig)))

    with ing._caffeinate():
        pass

    assert killpg_calls == [(54321, ing.signal.SIGTERM), (54321, ing.signal.SIGKILL)]
    assert not fake_proc.killed   # the group kill replaces proc.kill(), not alongside it
    assert fake_proc.wait_calls == 2


def test_caffeinate_noop_on_windows(monkeypatch):
    """Neither darwin's nor Linux's tool applies on Windows — there's no CLI equivalent worth
    shelling out to (#415), so this stays a pure no-op there."""
    from watchdog.cmd import ingest as ing

    monkeypatch.setattr("watchdog.cmd.ingest.sys.platform", "win32")
    monkeypatch.setattr(ing.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("must not spawn anything on Windows"))

    with ing._caffeinate():
        pass


def test_caffeinate_noop_on_linux_without_systemd_inhibit(monkeypatch):
    from watchdog.cmd import ingest as ing

    monkeypatch.setattr("watchdog.cmd.ingest.sys.platform", "linux")
    monkeypatch.setattr(ing.shutil, "which", lambda name: None)
    monkeypatch.setattr(ing.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("must not spawn when systemd-inhibit isn't on PATH"))

    with ing._caffeinate():
        pass


def test_caffeinate_noop_when_binary_missing(monkeypatch):
    from watchdog.cmd import ingest as ing

    monkeypatch.setattr("watchdog.cmd.ingest.sys.platform", "darwin")
    monkeypatch.setattr(ing.shutil, "which", lambda name: None)
    monkeypatch.setattr(ing.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("must not spawn when caffeinate isn't on PATH"))

    with ing._caffeinate():
        pass


def test_caffeinate_terminates_even_if_the_block_raises(monkeypatch):
    """The context manager's cleanup must run on an exception path too (e.g. the KeyboardInterrupt
    that stops an ingest, #415) — not just on a clean exit, and it must not swallow that
    exception (a bare `return` inside `finally` would)."""
    from watchdog.cmd import ingest as ing

    monkeypatch.setattr("watchdog.cmd.ingest.sys.platform", "darwin")
    monkeypatch.setattr(ing.shutil, "which", lambda name: "/usr/bin/caffeinate")

    fake_proc = _FakeProc()
    monkeypatch.setattr(ing.subprocess, "Popen", lambda *a, **k: fake_proc)

    with pytest.raises(KeyboardInterrupt):
        with ing._caffeinate():
            raise KeyboardInterrupt()
    assert fake_proc.terminated


def test_caffeinate_noop_does_not_swallow_exception(monkeypatch):
    """Same exception-safety property as above, but for the no-op path (`proc is None`) — this
    is the branch a bare `return` in `finally` would have silently broken."""
    from watchdog.cmd import ingest as ing

    monkeypatch.setattr("watchdog.cmd.ingest.sys.platform", "win32")

    with pytest.raises(ValueError):
        with ing._caffeinate():
            raise ValueError("boom")


def test_run_finalize_wraps_model_call_in_caffeinate(tmp_path, monkeypatch):
    """#467: `watchdog bark` had no `_caffeinate()` wrap at all, unlike `cmd_ingest`'s extraction
    loop — a bark run stopped none of the machine sleeping mid-call, the same failure mode #415
    guarded extraction against. `_run_finalize`'s model call must run inside the same guard."""
    from watchdog.cmd import ingest as ing
    from watchdog.pipeline import orchestrate as orch_module
    from tests.test_write_vault import make_vault
    vault = make_vault(tmp_path)

    calls = []

    @contextlib.contextmanager
    def fake_caffeinate():
        calls.append("enter")
        yield
        calls.append("exit")

    monkeypatch.setattr(ing, "_caffeinate", fake_caffeinate)

    async def fake_finalize(*a, **k):
        assert calls == ["enter"]   # the model call must run inside the caffeinate block
        return {"synthesized": 0}
    monkeypatch.setattr(orch_module, "finalize", fake_finalize)

    ing._run_finalize(vault, "haiku")

    assert calls == ["enter", "exit"]


# ── configure sections + default_skill ────────────────────────────────────────

def test_configure_sections_cover_every_key_exactly_once():
    from watchdog.cmd.setup import _CONFIGURE_KEYS, _CONFIGURE_SECTIONS
    grouped = [k for _, _, ks in _CONFIGURE_SECTIONS for k in ks]
    assert set(grouped) == set(_CONFIGURE_KEYS)     # nothing falls into "Other"
    assert len(grouped) == len(set(grouped))        # no key in two sections


def test_default_skill_is_a_configurable_key():
    from watchdog.cmd.setup import _CONFIGURE_KEYS
    assert "default_skill" in _CONFIGURE_KEYS


# ── show-skills ───────────────────────────────────────────────────────────────

def _patch_catalog(monkeypatch, tmp_path, names=("court-documents",)):
    from watchdog import skills_catalog as sc
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    for n in names:
        (pkg / f"{n}.md").write_text(f"---\ndescription: {n} stuff\n---\n# {n} body\n")
    monkeypatch.setattr(sc, "_package_records", lambda: pkg)
    monkeypatch.setattr(sc, "USER_SKILLS_DIR", tmp_path / "nouser")
    return pkg


def test_show_skills_lists_and_opens_github(tmp_path, monkeypatch, capsys):
    from watchdog.cmd import setup as st
    _patch_catalog(monkeypatch, tmp_path, names=("court-documents",))
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda u: opened.append(u))
    st.cmd_show_skills(args())
    out = capsys.readouterr().out
    assert "court-documents" in out and "github.com" in out
    assert opened and "github.com" in opened[0]


def test_show_skills_prints_one_skill(tmp_path, monkeypatch, capsys):
    from watchdog.cmd import setup as st
    _patch_catalog(monkeypatch, tmp_path, names=("court-documents",))
    st.cmd_show_skills(args(name="court-documents"))
    assert "court-documents body" in capsys.readouterr().out


def test_show_skills_unknown_exits(tmp_path, monkeypatch):
    from watchdog.cmd import setup as st
    _patch_catalog(monkeypatch, tmp_path, names=("court-documents",))
    with pytest.raises(SystemExit, match="no record skill"):
        st.cmd_show_skills(args(name="nope"))


# ── _resolve_pinned_skill (global catalog, returns paths) ─────────────────────

def _fake_catalog(monkeypatch, tmp_path, names=("general-records", "corporate-filings")):
    """Point the global skill catalog at a controlled package dir, no user dir."""
    from watchdog import skills_catalog as sc
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    for n in names:
        (pkg / f"{n}.md").write_text(f"# {n}\n")
    monkeypatch.setattr(sc, "_package_records", lambda: pkg)
    monkeypatch.setattr(sc, "USER_SKILLS_DIR", tmp_path / "nouser")
    return pkg


def test_resolve_pinned_skill_from_flag(tmp_path, monkeypatch):
    from watchdog.cmd import ingest as ing
    pkg = _fake_catalog(monkeypatch, tmp_path)
    assert ing._resolve_pinned_skill(args(skill="corporate-filings"), {}) == str(pkg / "corporate-filings.md")


def test_resolve_pinned_skill_strips_md_suffix(tmp_path, monkeypatch):
    from watchdog.cmd import ingest as ing
    pkg = _fake_catalog(monkeypatch, tmp_path)
    assert ing._resolve_pinned_skill(args(skill="corporate-filings.md"), {}) == str(pkg / "corporate-filings.md")


def test_resolve_pinned_skill_unknown_exits(tmp_path, monkeypatch):
    from watchdog.cmd import ingest as ing
    _fake_catalog(monkeypatch, tmp_path)
    with pytest.raises(SystemExit, match="not found"):
        ing._resolve_pinned_skill(args(skill="nope"), {})


def test_resolve_pinned_skill_from_config(tmp_path, monkeypatch):
    from watchdog.cmd import ingest as ing
    pkg = _fake_catalog(monkeypatch, tmp_path)
    assert ing._resolve_pinned_skill(args(), {"default_skill": "general-records"}) == str(pkg / "general-records.md")


def test_resolve_pinned_skill_none_classifies(tmp_path, monkeypatch):
    from watchdog.cmd import ingest as ing
    _fake_catalog(monkeypatch, tmp_path)
    assert ing._resolve_pinned_skill(args(), {}) is None


def test_resolve_pinned_skill_explicit_file_path(tmp_path, monkeypatch):
    from watchdog.cmd import ingest as ing
    _fake_catalog(monkeypatch, tmp_path)
    custom = tmp_path / "custom-skill.md"
    custom.write_text("body")
    assert ing._resolve_pinned_skill(args(skill=str(custom)), {}) == str(custom.resolve())


def test_resolve_pinned_skill_interactive_pick(tmp_path, monkeypatch):
    from watchdog.cmd import ingest as ing
    pkg = _fake_catalog(monkeypatch, tmp_path, names=("alpha", "beta"))  # sorted: alpha=1, beta=2
    monkeypatch.setattr("builtins.input", lambda *a: "2")
    assert ing._resolve_pinned_skill(args(skill=ing._PICK_SKILL), {}) == str(pkg / "beta.md")


def test_resolve_pinned_skill_interactive_enter_classifies(tmp_path, monkeypatch):
    from watchdog.cmd import ingest as ing
    _fake_catalog(monkeypatch, tmp_path, names=("alpha", "beta"))
    monkeypatch.setattr("builtins.input", lambda *a: "")
    assert ing._resolve_pinned_skill(args(skill=ing._PICK_SKILL), {}) is None


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


# ── search --json output (consumed by the watchdog-query semantic lane) ──────────

def test_build_search_json_shape():
    from watchdog.cmd.vault import _build_search_json
    passages = [{"type": "passage", "filename": "acme.pdf", "page": 3,
                 "text": "the kickback to Cyprus", "context": "Acme filing", "score": 0.51234}]
    notes = [{"type": "note", "note_path": "entities/person/jane",
              "preview": "Jane is a director", "score": 0.4}]
    out = _build_search_json("kickback", passages, notes)
    assert out["query"] == "kickback"
    # only the fields a consumer needs — internal type/context dropped, score rounded
    assert out["passages"][0] == {
        "filename": "acme.pdf", "page": 3, "text": "the kickback to Cyprus", "score": 0.5123}
    assert out["notes"][0]["note_path"] == "entities/person/jane"
    # round-trips as JSON
    assert json.loads(json.dumps(out, ensure_ascii=False))["passages"][0]["page"] == 3


def test_build_search_json_empty():
    from watchdog.cmd.vault import _build_search_json
    assert _build_search_json("nothing", [], []) == {
        "query": "nothing", "passages": [], "notes": [], "exact": [],
    }


# ── Wayback credential gate (#201) ─────────────────────────────────────────────

def _write_config(tmp_path, monkeypatch, data):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(data))
    monkeypatch.setattr(_research, "CONFIG_FILE", cfg)
    return cfg


def test_wayback_creds_none_when_disabled(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch,
                  {"wayback_access_key": "a", "wayback_secret_key": "s"})  # save flag off
    assert _research._wayback_creds() is None


def test_wayback_creds_none_when_keys_missing(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"wayback_save": True, "wayback_access_key": "a"})
    assert _research._wayback_creds() is None


def test_wayback_creds_returns_pair_when_enabled_and_set(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch,
                  {"wayback_save": True, "wayback_access_key": "a", "wayback_secret_key": "s"})
    assert _research._wayback_creds() == ("a", "s")


def test_wayback_creds_none_when_no_config(tmp_path, monkeypatch):
    monkeypatch.setattr(_research, "CONFIG_FILE", tmp_path / "missing.json")
    assert _research._wayback_creds() is None


def test_configure_masks_wayback_secret(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"projects_dir": str(tmp_path),
                               "wayback_save": True,
                               "wayback_access_key": "SUPERSECRETKEY"}))
    monkeypatch.setattr(_setup, "CONFIG_FILE", cfg)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    _setup.cmd_configure(argparse.Namespace(key=None, value=None))
    out = _strip_ansi(capsys.readouterr().out)
    assert "SUPERSECRETKEY" not in out       # never echoed back
    assert "wayback_access_key" in out and "(set)" in out


# ── Boolean config toggle (#201 follow-up) ─────────────────────────────────────

def test_edit_bool_toggle_on(wdg_home, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    config = {}
    _setup._edit_key_interactive(config, "wayback_save")
    assert config["wayback_save"] is True
    assert json.loads((wdg_home / "config.json").read_text())["wayback_save"] is True


def test_edit_bool_toggle_off(wdg_home, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    config = {"wayback_save": True}
    _setup._edit_key_interactive(config, "wayback_save")
    assert config["wayback_save"] is False


def test_edit_bool_enter_keeps_current(wdg_home, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *a: "")   # Enter
    config = {"wayback_save": True}
    _setup._edit_key_interactive(config, "wayback_save")
    assert config["wayback_save"] is True                  # unchanged
    assert "No change" in _strip_ansi(capsys.readouterr().out)


def test_edit_bool_rejects_freetext(wdg_home, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *a: "maybe")
    config = {"wayback_save": False}
    _setup._edit_key_interactive(config, "wayback_save")
    assert config["wayback_save"] is False                 # untouched
    assert "enter y or n" in _strip_ansi(capsys.readouterr().out)


# ── watchdog fetch (#197) ──────────────────────────────────────────────────────

def _fake_deposit_many(monkeypatch, captured):
    from watchdog.pipeline.research import Deposit

    def fake(vault, entries, **kw):
        captured["entries"] = entries
        captured["wayback"] = kw.get("wayback")
        return [Deposit(e["url"], vault / "_INCOMING" / "x.html") for e in entries]

    monkeypatch.setattr("watchdog.pipeline.research.deposit_many", fake)


def test_cmd_fetch_inline_urls(configured, monkeypatch, capsys):
    cli.cmd_new(args(name="Test Proj", dir=str(configured)))
    monkeypatch.chdir(configured / "test-proj")
    captured = {}
    _fake_deposit_many(monkeypatch, captured)
    cli.cmd_fetch(args(targets=["https://a.com/x", "https://b.com/y"], project=None))
    assert [e["url"] for e in captured["entries"]] == ["https://a.com/x", "https://b.com/y"]
    out = _strip_ansi(capsys.readouterr().out)
    assert "Downloaded 2 of 2" in out and "watchdog chew" in out


def test_cmd_fetch_reads_links_file(configured, monkeypatch):
    cli.cmd_new(args(name="Test Proj", dir=str(configured)))
    vault = configured / "test-proj"
    monkeypatch.chdir(vault)
    (vault / "links.txt").write_text("https://a.com\n# a comment\n\nhttps://b.com\tTitle B\n")
    captured = {}
    _fake_deposit_many(monkeypatch, captured)
    cli.cmd_fetch(args(targets=["links.txt"], project=None))
    assert [e["url"] for e in captured["entries"]] == ["https://a.com", "https://b.com"]
    assert captured["entries"][1]["title"] == "Title B"   # TSV columns preserved


def test_cmd_fetch_empty_file_errors(configured, monkeypatch):
    cli.cmd_new(args(name="Test Proj", dir=str(configured)))
    vault = configured / "test-proj"
    monkeypatch.chdir(vault)
    (vault / "empty.txt").write_text("# only a comment\n")
    with pytest.raises(SystemExit):
        cli.cmd_fetch(args(targets=["empty.txt"], project=None))


def test_cmd_fetch_passes_wayback_when_configured(configured, wdg_home, monkeypatch):
    cli.cmd_new(args(name="Test Proj", dir=str(configured)))
    monkeypatch.chdir(configured / "test-proj")
    cfg = wdg_home / "config.json"
    data = json.loads(cfg.read_text())
    data.update({"wayback_save": True, "wayback_access_key": "a", "wayback_secret_key": "s"})
    cfg.write_text(json.dumps(data))
    monkeypatch.setattr(_research, "CONFIG_FILE", cfg)
    captured = {}
    _fake_deposit_many(monkeypatch, captured)
    cli.cmd_fetch(args(targets=["https://a.com/x"], project=None))
    assert captured["wayback"] == ("a", "s")


# ── search snippet helpers ──────────────────────────────────────────────────

def test_search_query_terms_extracts_positive_words():
    assert _vault._search_query_terms("shell company -real estate") == ["company", "shell"]


def test_search_query_terms_ignores_single_letter_tokens():
    terms = _vault._search_query_terms("a shell company")
    assert "a" not in terms
    assert "shell" in terms and "company" in terms


def test_search_query_terms_empty_for_negative_only_query():
    assert _vault._search_query_terms("-real estate") == []


def test_highlight_snippet_bolds_matches_case_insensitively():
    out = _vault._highlight_snippet("The Shell company filed", ["shell"])
    assert f"{_vault._BOLD}Shell{_vault._RESET}" in out


def test_highlight_snippet_no_terms_returns_unchanged():
    text = "plain text"
    assert _vault._highlight_snippet(text, []) == text


def test_highlight_snippet_respects_word_boundaries():
    out = _vault._highlight_snippet("shellfish market", ["shell"])
    assert _vault._BOLD not in out


def test_windowed_snippet_returns_full_text_when_short():
    text = "short text"
    assert _vault._windowed_snippet(text, ["short"], 240) == text


def test_windowed_snippet_centers_on_match():
    text = "x" * 500 + "TARGET" + "y" * 500
    snippet = _vault._windowed_snippet(text, ["target"], 100)
    assert "TARGET" in snippet
    assert snippet.startswith("…")
    assert snippet.endswith("…")


def test_windowed_snippet_falls_back_to_start_when_no_match():
    text = "z" * 1000
    assert _vault._windowed_snippet(text, ["missing"], 100) == text[:100] + "…"


# ── cmd_search ────────────────────────────────────────────────────────────────

def _register_search_project(configured):
    vault = configured / "test-proj"
    vault.mkdir(parents=True)
    cli.save_projects({"test-proj": {"name": "Test Proj", "path": str(vault), "created": "2026-01-01"}})
    return vault


def _stub_search(passage=None, note=None):
    def fake(vault, query, **kw):
        if kw.get("scope") == "corpus":
            return [passage] if passage else []
        return [note] if note else []
    return fake


def test_search_highlights_matched_terms(configured, monkeypatch, capsys):
    _register_search_project(configured)
    monkeypatch.setattr("watchdog.pipeline.embed.index_stats", lambda vault: {"total": 1})
    monkeypatch.setattr("watchdog.pipeline.embed.search", _stub_search(
        passage={"filename": "doc.pdf", "page": 3, "text": "The shell company filed papers.", "score": 0.5}))

    cli.cmd_search(args(project="test-proj", query="shell company", top_n=5,
                         threshold=None, no_rerank=False, json=False, full=False))
    out = capsys.readouterr().out
    assert f"{_vault._BOLD}shell{_vault._RESET}" in out.lower()


def test_search_full_flag_skips_truncation(configured, monkeypatch, capsys):
    _register_search_project(configured)
    long_text = "word " * 100
    monkeypatch.setattr("watchdog.pipeline.embed.index_stats", lambda vault: {"total": 1})
    monkeypatch.setattr("watchdog.pipeline.embed.search", _stub_search(
        passage={"filename": "doc.pdf", "page": 1, "text": long_text, "score": 0.5}))

    cli.cmd_search(args(project="test-proj", query="word", top_n=5,
                         threshold=None, no_rerank=False, json=False, full=False))
    assert "…" in _strip_ansi(capsys.readouterr().out)

    cli.cmd_search(args(project="test-proj", query="word", top_n=5,
                         threshold=None, no_rerank=False, json=False, full=True))
    full_out = _strip_ansi(capsys.readouterr().out)
    assert "…" not in full_out
    assert long_text.strip() in full_out


def test_search_json_output_has_no_ansi_and_full_text(configured, monkeypatch, capsys):
    _register_search_project(configured)
    monkeypatch.setattr("watchdog.pipeline.embed.index_stats", lambda vault: {"total": 1})
    monkeypatch.setattr("watchdog.pipeline.embed.search", _stub_search(
        passage={"filename": "doc.pdf", "page": 1, "text": "shell company filed", "score": 0.5}))

    cli.cmd_search(args(project="test-proj", query="shell", top_n=5,
                         threshold=None, no_rerank=False, json=True, full=False))
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["passages"][0]["text"] == "shell company filed"
    assert "\x1b[" not in out


# ── cmd_search: exact-match (FTS) section (#109) ───────────────────────────────

def _stub_fts(hits=None):
    return lambda vault, query, **kw: (hits or [])


def test_search_shows_exact_matches_section(configured, monkeypatch, capsys):
    _register_search_project(configured)
    monkeypatch.setattr("watchdog.pipeline.embed.index_stats", lambda vault: {"total": 1})
    monkeypatch.setattr("watchdog.pipeline.embed.search", _stub_search())
    monkeypatch.setattr("watchdog.pipeline.fulltext.search", _stub_fts([
        {"kind": "corpus", "key": "sha1", "title": "doc.pdf", "path": "morgue/doc.pdf",
         "page": 4, "text": "the shell company filed papers"},
    ]))

    cli.cmd_search(args(project="test-proj", query="shell company", top_n=5,
                         threshold=None, no_rerank=False, json=False, full=False))
    out = _strip_ansi(capsys.readouterr().out)
    assert "Exact matches" in out
    assert "doc.pdf" in out
    assert "morgue/doc.pdf#page=4" in out


def test_search_exact_note_hit_shows_kind_label_not_path_for_corpus(configured, monkeypatch, capsys):
    _register_search_project(configured)
    monkeypatch.setattr("watchdog.pipeline.embed.index_stats", lambda vault: {"total": 1})
    monkeypatch.setattr("watchdog.pipeline.embed.search", _stub_search())
    monkeypatch.setattr("watchdog.pipeline.fulltext.search", _stub_fts([
        {"kind": "entity", "key": "entities/alice", "title": "Alice Smith",
         "path": "entities/alice", "page": None, "text": "Alice Smith is a director"},
    ]))

    cli.cmd_search(args(project="test-proj", query="director", top_n=5,
                         threshold=None, no_rerank=False, json=False, full=False))
    out = _strip_ansi(capsys.readouterr().out)
    assert "entities/alice" in out
    assert "Entity note" in out


def test_search_json_includes_exact_lane(configured, monkeypatch, capsys):
    _register_search_project(configured)
    monkeypatch.setattr("watchdog.pipeline.embed.index_stats", lambda vault: {"total": 1})
    monkeypatch.setattr("watchdog.pipeline.embed.search", _stub_search())
    monkeypatch.setattr("watchdog.pipeline.fulltext.search", _stub_fts([
        {"kind": "entity", "key": "entities/alice", "title": "Alice Smith",
         "path": "entities/alice", "page": None, "text": "Alice Smith is a director"},
    ]))

    cli.cmd_search(args(project="test-proj", query="director", top_n=5,
                         threshold=None, no_rerank=False, json=True, full=False))
    payload = json.loads(capsys.readouterr().out)
    assert payload["exact"][0]["title"] == "Alice Smith"
    assert payload["exact"][0]["kind"] == "entity"


def test_search_no_results_across_all_three_lanes_says_no_results(configured, monkeypatch, capsys):
    _register_search_project(configured)
    monkeypatch.setattr("watchdog.pipeline.embed.index_stats", lambda vault: {"total": 1})
    monkeypatch.setattr("watchdog.pipeline.embed.search", _stub_search())
    monkeypatch.setattr("watchdog.pipeline.fulltext.search", _stub_fts([]))

    cli.cmd_search(args(project="test-proj", query="nothing", top_n=5,
                         threshold=None, no_rerank=False, json=False, full=False))
    out = _strip_ansi(capsys.readouterr().out)
    assert "No results" in out


def test_search_exact_lane_failure_does_not_crash_search(configured, monkeypatch, capsys):
    _register_search_project(configured)
    monkeypatch.setattr("watchdog.pipeline.embed.index_stats", lambda vault: {"total": 1})
    monkeypatch.setattr("watchdog.pipeline.embed.search", _stub_search(
        passage={"filename": "doc.pdf", "page": 1, "text": "shell company filed", "score": 0.5}))

    def _boom(vault, query, **kw):
        raise RuntimeError("no FTS5 support")
    monkeypatch.setattr("watchdog.pipeline.fulltext.search", _boom)

    cli.cmd_search(args(project="test-proj", query="shell", top_n=5,
                         threshold=None, no_rerank=False, json=False, full=False))
    out = _strip_ansi(capsys.readouterr().out)
    assert "Source passages" in out


# ── cmd_search --batch (#110) ───────────────────────────────────────────────────

def _write_manifest(vault, manifest):
    reg_dir = vault / ".watchdog" / "registry"
    reg_dir.mkdir(parents=True, exist_ok=True)
    (reg_dir / "manifest.json").write_text(json.dumps(manifest))


def test_manifest_matches_by_name_substring():
    manifest = {"alice-smith": {"name": "Alice Smith", "type": "Person",
                                "aliases": [], "note_path": "entities/person/alice-smith"}}
    hits = _vault._manifest_matches(manifest, "alice")
    assert len(hits) == 1
    assert hits[0]["note_path"] == "entities/person/alice-smith"


def test_manifest_matches_by_alias():
    manifest = {"alice-smith": {"name": "Alice Smith", "type": "Person",
                                "aliases": ["A. Smith"], "note_path": "entities/person/alice-smith"}}
    assert len(_vault._manifest_matches(manifest, "A. Smith")) == 1


def test_manifest_matches_case_insensitive_no_match_returns_empty():
    manifest = {"alice-smith": {"name": "Alice Smith", "type": "Person",
                                "aliases": [], "note_path": "entities/person/alice-smith"}}
    assert _vault._manifest_matches(manifest, "Bob Jones") == []


def test_read_batch_terms_skips_blank_lines_and_comments(tmp_path):
    f = tmp_path / "names.txt"
    f.write_text("Alice Smith\n\n# a comment\nBob Jones\n  \n")
    assert _vault._read_batch_terms(f) == ["Alice Smith", "Bob Jones"]


def test_read_batch_terms_missing_file_exits(tmp_path):
    with pytest.raises(SystemExit):
        _vault._read_batch_terms(tmp_path / "missing.txt")


# ── _poll_stable_files (watchdog watch mid-copy guard, #261) ───────────────────

def test_poll_stable_files_holds_growing_file(tmp_path):
    f = tmp_path / "big.pdf"
    f.write_bytes(b"a" * 10)
    ready, pending = _vault._poll_stable_files({f}, {})
    assert ready == []
    assert pending == {f: 10}


def test_poll_stable_files_releases_file_once_size_holds(tmp_path):
    f = tmp_path / "big.pdf"
    f.write_bytes(b"a" * 10)
    # First poll: no prior size recorded — held.
    ready, pending = _vault._poll_stable_files({f}, {})
    assert ready == []
    # Second poll: same size as last time — copy finished.
    ready, pending = _vault._poll_stable_files({f}, pending)
    assert ready == [f]
    assert pending == {}


def test_poll_stable_files_keeps_holding_a_still_growing_file(tmp_path):
    f = tmp_path / "big.pdf"
    f.write_bytes(b"a" * 10)
    ready, pending = _vault._poll_stable_files({f}, {})
    f.write_bytes(b"a" * 20)   # more bytes copied in between polls
    ready, pending = _vault._poll_stable_files({f}, pending)
    assert ready == []
    assert pending == {f: 20}


def test_poll_stable_files_skips_file_removed_before_stat(tmp_path):
    f = tmp_path / "gone.pdf"   # never created — simulates a race with deletion
    ready, pending = _vault._poll_stable_files({f}, {})
    assert ready == []
    assert pending == {}


def test_search_batch_reports_hits_and_no_hits(configured, monkeypatch, tmp_path, capsys):
    vault = _register_search_project(configured)
    _write_manifest(vault, {"alice-smith": {"name": "Alice Smith", "type": "Person",
                                            "aliases": [], "note_path": "entities/person/alice-smith"}})
    monkeypatch.setattr("watchdog.pipeline.fulltext.search", lambda vault, query, **kw: (
        [{"kind": "corpus", "key": "sha1", "title": "doc.pdf", "path": "morgue/doc.pdf",
          "page": 2, "text": "..."}] if query == "Bob Jones" else []
    ))
    batch_file = tmp_path / "names.txt"
    batch_file.write_text("Alice Smith\nBob Jones\nNo One\n")

    cli.cmd_search(args(project="test-proj", query=None, batch=str(batch_file), top_n=5, json=False))
    out = _strip_ansi(capsys.readouterr().out)
    assert "Alice Smith" in out
    assert "entities/person/alice-smith" in out
    assert "Bob Jones" in out
    assert "doc.pdf" in out
    assert "No One" in out
    assert "no hits" in out


def test_search_batch_json_output(configured, monkeypatch, tmp_path, capsys):
    vault = _register_search_project(configured)
    _write_manifest(vault, {})
    monkeypatch.setattr("watchdog.pipeline.fulltext.search", lambda vault, query, **kw: [])
    batch_file = tmp_path / "names.txt"
    batch_file.write_text("Ghost\n")

    cli.cmd_search(args(project="test-proj", query=None, batch=str(batch_file), top_n=5, json=True))
    payload = json.loads(capsys.readouterr().out)
    assert payload["terms"][0]["term"] == "Ghost"
    assert payload["terms"][0]["entities"] == []
    assert payload["terms"][0]["hits"] == []


def test_search_batch_rejects_query_argument(configured, tmp_path):
    _register_search_project(configured)
    batch_file = tmp_path / "names.txt"
    batch_file.write_text("Alice\n")
    with pytest.raises(SystemExit):
        cli.cmd_search(args(project="test-proj", query="alice", batch=str(batch_file), top_n=5, json=False))


def test_search_batch_empty_file_exits(configured, tmp_path):
    _register_search_project(configured)
    batch_file = tmp_path / "names.txt"
    batch_file.write_text("\n\n")
    with pytest.raises(SystemExit):
        cli.cmd_search(args(project="test-proj", query=None, batch=str(batch_file), top_n=5, json=False))


# ── cmd_search --everywhere (#272) ──────────────────────────────────────────────

def _register_projects(configured, entries):
    """entries: list of (slug, name, extra) dicts. extra may set archived=True or
    missing_path=True (vault dir is never created). Registers all at once and
    returns {slug: vault_path}."""
    projects = {}
    vaults = {}
    for slug, name, extra in entries:
        extra = extra or {}
        vault = configured / slug
        if not extra.get("missing_path"):
            vault.mkdir(parents=True, exist_ok=True)
        info = {"name": name, "path": str(vault), "created": "2026-01-01"}
        if extra.get("archived"):
            info["archived"] = True
        projects[slug] = info
        vaults[slug] = vault
    cli.save_projects(projects)
    return vaults


def test_search_everywhere_groups_by_investigation(configured, monkeypatch, capsys):
    vaults = _register_projects(configured, [
        ("shell-co", "Shell Co", None),
        ("muni-contracts", "Muni Contracts", None),
    ])
    _write_manifest(vaults["shell-co"], {
        f"e{i}": {"name": f"Acme Holding {i}", "type": "Company", "aliases": [], "note_path": f"entities/e{i}"}
        for i in range(3)
    })
    _write_manifest(vaults["muni-contracts"], {})

    def fake_fts(vault, query, **kw):
        if vault == vaults["shell-co"]:
            return [{"kind": "corpus", "key": f"sha{i}", "title": "doc.pdf", "path": "morgue/doc.pdf",
                     "page": 1, "text": "acme"} for i in range(14)]
        return [{"kind": "corpus", "key": "sha1", "title": "contract-award-2023.pdf",
                 "path": "morgue/contract-award-2023.pdf", "page": 12, "text": "acme"}]
    monkeypatch.setattr("watchdog.pipeline.fulltext.search", fake_fts)

    cli.cmd_search(args(project=None, query="acme", everywhere=True, top_n=5, json=False))
    out = _strip_ansi(capsys.readouterr().out)
    assert "Shell Co" in out and "shell-co" in out
    assert "3 entities · 14 exact matches" in out
    assert "Muni Contracts" in out and "muni-contracts" in out
    assert "1 exact match (p. 12, contract-award-2023.pdf)" in out


def test_search_everywhere_skips_archived_projects(configured, monkeypatch, capsys):
    vaults = _register_projects(configured, [
        ("active-proj", "Active Proj", None),
        ("old-proj", "Old Proj", {"archived": True}),
    ])
    for v in vaults.values():
        _write_manifest(v, {})
    monkeypatch.setattr("watchdog.pipeline.fulltext.search", lambda vault, query, **kw: (
        [{"kind": "corpus", "key": "sha1", "title": "doc.pdf", "path": "morgue/doc.pdf",
          "page": 1, "text": "x"}] if vault == vaults["old-proj"] else []
    ))

    cli.cmd_search(args(project=None, query="x", everywhere=True, top_n=5, json=False))
    out = _strip_ansi(capsys.readouterr().out)
    assert "Old Proj" not in out
    assert "No matches across 1 investigation." in out


def test_search_everywhere_skips_missing_vault_path(configured, monkeypatch, capsys):
    vaults = _register_projects(configured, [
        ("healthy-proj", "Healthy Proj", None),
        ("gone-proj", "Gone Proj", {"missing_path": True}),
    ])
    _write_manifest(vaults["healthy-proj"], {})
    monkeypatch.setattr("watchdog.pipeline.fulltext.search", lambda vault, query, **kw: [])

    cli.cmd_search(args(project=None, query="x", everywhere=True, top_n=5, json=False))
    out = _strip_ansi(capsys.readouterr().out)
    assert "Skipped 1 investigation with a broken vault path." in out


def test_search_everywhere_no_registered_investigations(configured, capsys):
    cli.cmd_search(args(project=None, query="x", everywhere=True, top_n=5, json=False))
    out = capsys.readouterr().out
    assert "No registered investigations." in out


def test_search_everywhere_batch_aggregates_terms_and_dedupes_entities(configured, monkeypatch, tmp_path, capsys):
    vault = _register_projects(configured, [("shell-co", "Shell Co", None)])["shell-co"]
    _write_manifest(vault, {"alice-smith": {"name": "Alice Smith", "type": "Person",
                                            "aliases": ["Smith"], "note_path": "entities/alice-smith"}})
    monkeypatch.setattr("watchdog.pipeline.fulltext.search", lambda vault, query, **kw: (
        [{"kind": "corpus", "key": f"sha-{query}", "title": "doc.pdf", "path": "morgue/doc.pdf",
          "page": 1, "text": query}]
    ))
    batch_file = tmp_path / "names.txt"
    batch_file.write_text("Alice\nSmith\n")

    cli.cmd_search(args(project=None, query=None, everywhere=True, batch=str(batch_file), top_n=5, json=False))
    out = _strip_ansi(capsys.readouterr().out)
    assert "1 entity · 2 exact matches" in out


def test_search_everywhere_json_output(configured, monkeypatch, capsys):
    vault = _register_projects(configured, [("shell-co", "Shell Co", None)])["shell-co"]
    _write_manifest(vault, {})
    monkeypatch.setattr("watchdog.pipeline.fulltext.search", lambda vault, query, **kw: [
        {"kind": "corpus", "key": "sha1", "title": "doc.pdf", "path": "morgue/doc.pdf", "page": 1, "text": "x"},
    ])

    cli.cmd_search(args(project=None, query="x", everywhere=True, top_n=5, json=True))
    payload = json.loads(capsys.readouterr().out)
    assert payload["terms"] == ["x"]
    assert payload["investigations"][0]["slug"] == "shell-co"
    assert payload["investigations"][0]["hits"][0]["title"] == "doc.pdf"


def test_search_everywhere_rejects_project_and_query_together(configured):
    _register_projects(configured, [("shell-co", "Shell Co", None)])
    with pytest.raises(SystemExit):
        cli.cmd_search(args(project="shell-co", query="x", everywhere=True, top_n=5, json=False))


def test_search_everywhere_batch_rejects_project_argument(configured, tmp_path):
    _register_projects(configured, [("shell-co", "Shell Co", None)])
    batch_file = tmp_path / "names.txt"
    batch_file.write_text("Alice\n")
    with pytest.raises(SystemExit):
        cli.cmd_search(args(project="shell-co", query=None, everywhere=True, batch=str(batch_file), top_n=5, json=False))


def test_search_everywhere_requires_query(configured):
    _register_projects(configured, [("shell-co", "Shell Co", None)])
    with pytest.raises(SystemExit):
        cli.cmd_search(args(project=None, query=None, everywhere=True, top_n=5, json=False))
