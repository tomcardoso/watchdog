"""Investigations: creating, renaming, describing, moving, archiving and deleting them
(`watchdog.ops.projects`), the list itself (`watchdog.projects`), Obsidian's vault list
(`watchdog.obsidian`) and the counts the app shows (`watchdog.vault`). Ported from the
command-line tests when the command line was removed (D300)."""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from watchdog import config, obsidian, projects
from watchdog import vault as vault_mod
from watchdog.ops import CollectingReporter
from watchdog.ops import projects as pops
from watchdog.pipeline.write_vault import slugify

pytest_plugins = ["tests.gui_support"]   # the wdg_home fixture

REP = CollectingReporter()


@pytest.fixture(autouse=True)
def _fresh_reporter():
    global REP
    REP = CollectingReporter()
    yield


@pytest.fixture
def configured(wdg_home, tmp_path):
    """A settings file naming the investigations folder."""
    investigations = tmp_path / "Investigations"
    investigations.mkdir()
    (wdg_home / "config.json").write_text(json.dumps({"projects_dir": str(investigations)}) + "\n")
    return investigations


def _new(name, dir=None, description=""):
    return pops.new(REP, name=name, dir=str(dir) if dir else None, description=description)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _telemetry_names():
    from watchdog import telemetry_db
    conn = sqlite3.connect(telemetry_db.DB_PATH)
    try:
        return sorted(r[0] for r in conn.execute("SELECT vault_name FROM calls"))
    finally:
        conn.close()


def _record_telemetry(vault):
    from watchdog import telemetry_db
    record = {"task": "extract", "model": "m", "backend": "claude-api", "input_tokens": 1,
              "output_tokens": 1, "cache_read_tokens": 0, "cache_write_tokens": 0}
    telemetry_db.record_call(record, vault=vault, run_id="r", benchmark_arm_id=None,
                             prompt_hash=None, config_snapshot=None)


@pytest.mark.parametrize("name, expected", [
    ("Shell Company Investigation", "shell-company-investigation"),
    ("  leading spaces  ",          "leading-spaces"),
    ("Weird & Chars!",              "weird-chars"),
    ("multiple---dashes",           "multiple-dashes"),
    ("ALLCAPS",                     "allcaps"),
])
def test_slugify(name, expected):
    assert slugify(name) == expected


def test_slugify_empty_after_strip():
    assert slugify("!!!") == ""


def test_count_incoming_empty_dir(tmp_path):
    (tmp_path / "incoming").mkdir()
    assert vault_mod.count_incoming(tmp_path) == 0


def test_count_incoming_no_dir(tmp_path):
    assert vault_mod.count_incoming(tmp_path) == 0


def test_count_incoming_counts_files(tmp_path):
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    (incoming / "doc.pdf").write_text("")
    (incoming / "report.docx").write_text("")
    assert vault_mod.count_incoming(tmp_path) == 2


def test_count_incoming_ignores_dotfiles(tmp_path):
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    (incoming / ".DS_Store").write_text("")
    (incoming / "real.pdf").write_text("")
    assert vault_mod.count_incoming(tmp_path) == 1


def test_count_incoming_ignores_sidecar_yml(tmp_path):
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    (incoming / "doc.pdf").write_text("")
    (incoming / "doc.yml").write_text("")
    assert vault_mod.count_incoming(tmp_path) == 1


def test_count_incoming_excludes_failed(tmp_path):
    incoming = tmp_path / "incoming"
    failed = incoming / "failed"
    failed.mkdir(parents=True)
    (incoming / "pending.pdf").write_text("")
    (failed / "broken.pdf").write_text("")
    assert vault_mod.count_incoming(tmp_path) == 1


def test_count_incoming_not_fooled_by_failed_in_vault_path(tmp_path):
    # The vault path itself contains "failed" — should still count files correctly.
    vault = tmp_path / "_FAILED_projects" / "investigation"
    incoming = vault / "incoming"
    incoming.mkdir(parents=True)
    (incoming / "real.pdf").write_text("")
    assert vault_mod.count_incoming(vault) == 1


def test_count_incoming_excludes_skipped(tmp_path):
    # A duplicate chew moves files to skipped/ — status must not count them as pending,
    # or the user is sent in a status -> chew -> status loop chasing files chew already
    # decided to skip (#255).
    incoming = tmp_path / "incoming"
    skipped = incoming / "skipped"
    skipped.mkdir(parents=True)
    (incoming / "pending.pdf").write_text("")
    (skipped / "duplicate.pdf").write_text("")
    assert vault_mod.count_incoming(tmp_path) == 1


def test_count_incoming_matches_find_files_exclusions(tmp_path):
    # _count_incoming (status) and find_files (chew) must exclude the same directories,
    # or a file can be simultaneously "pending" per status and invisible to chew (#255).
    from watchdog.pipeline.preprocess_batch import find_files
    from watchdog.vault_paths import SET_ASIDE_NAMES

    incoming = tmp_path / "incoming"
    for d in sorted(SET_ASIDE_NAMES):
        skip_dir = incoming / d
        skip_dir.mkdir(parents=True, exist_ok=True)  # case-insensitive filesystems collide _FAILED/_failed
        (skip_dir / "file.pdf").write_text("")
    (incoming / "pending.pdf").write_text("")

    assert vault_mod.count_incoming(tmp_path) == 1
    assert len(find_files([incoming])) == 1


def test_load_registry_missing(tmp_path):
    assert projects.load_registry(tmp_path) is None


def test_load_registry_returns_data(tmp_path):
    reg_dir = tmp_path / ".watchdog" / "registry"
    reg_dir.mkdir(parents=True)
    data = {"document_count": 5, "entity_count": 3, "last_updated": "2026-06-07T00:00:00Z"}
    (reg_dir / "registry.json").write_text(json.dumps(data))
    assert projects.load_registry(tmp_path) == data


def test_load_registry_corrupt_json_raises(tmp_path):
    """A corrupt registry.json must surface as a real exception — not be silently swallowed
    as "no registry" (#636), which callers like `cmd_status`/`cmd_list` would otherwise treat
    as "nothing ingested yet". `_load_registry` itself doesn't decide how to report this: a
    single-project caller (cmd_register, cmd_status) turns it into a clean sys.exit, while a
    caller that loops over every registered project (cmd_list, cmd_doctor) catches it
    per-project so one corrupt vault doesn't abort the whole command — see the tests below."""
    reg_dir = tmp_path / ".watchdog" / "registry"
    reg_dir.mkdir(parents=True)
    (reg_dir / "registry.json").write_text("not json {{{")
    with pytest.raises(json.JSONDecodeError):
        projects.load_registry(tmp_path)


def test_register_corrupt_registry_exits_cleanly(configured, wdg_home, tmp_path):
    vault = tmp_path / "external-vault"
    (vault / ".watchdog" / "registry").mkdir(parents=True)
    (vault / ".watchdog" / "registry" / "registry.json").write_text("not json {{{")
    with pytest.raises(SystemExit, match="damaged"):
        pops.register(REP, path=str(vault), name="External")


def test_doctor_one_corrupt_registry_reports_instead_of_crashing(configured, wdg_home, capsys):
    """The health check survives a damaged registry summary: it is one more reported problem,
    not an exception that stops the check."""
    _new("Healthy Co", configured)
    _new("Broken Co", configured)
    (configured / "broken-co" / ".watchdog" / "registry" / "registry.json").write_text("not json {{{")

    missing, corrupt = projects.doctor_findings(projects.load())
    assert missing == []
    assert [slug for slug, _info in corrupt] == ["broken-co"]


def test_new_creates_vault(configured, tmp_path):
    _new("Test Investigation", configured)
    vault = configured / "test-investigation"
    assert vault.is_dir()


def test_new_vault_structure(configured):
    _new("My Story", configured)
    vault = configured / "my-story"
    for d in ["incoming", "morgue", "entities", "documents", "briefings", "wiki", "queries",
              ".watchdog/queue",
              ".watchdog/staging"]:
        assert (vault / d).is_dir(), f"Missing: {d}"
    # Entity folders are created by ingest under the canonical types (D105) — never the
    # pre-D105 company/address folders that nothing writes to.
    assert not (vault / "entities" / "company").exists()
    assert not (vault / "entities" / "address").exists()
    dashboard = (vault / "dashboard.base").read_text()
    assert 'file.inFolder("entities/organization")' in dashboard
    assert "entities/company" not in dashboard
    graph = json.loads((vault / ".obsidian" / "graph.json").read_text())
    queries = {g["query"] for g in graph["colorGroups"]}
    assert "path:entities/organization" in queries and "path:entities/company" not in queries


def test_new_registry_initialized(configured):
    _new("My Story", configured)
    reg = json.loads((configured / "my-story" / ".watchdog" / "registry" / "registry.json").read_text())
    assert reg["document_count"] == 0
    assert reg["entity_count"] == 0
    assert reg["schema_version"] == "1"


def test_new_claude_md_in_dot_claude_not_root(configured):
    _new("City Hall Probe", configured)
    vault = configured / "city-hall-probe"
    # CLAUDE.md lives in .claude/ (Claude Code loads ./.claude/CLAUDE.md the same), keeping the root clean
    assert not (vault / "CLAUDE.md").exists()
    text = (vault / ".claude" / "CLAUDE.md").read_text()
    assert "City Hall Probe" in text


def test_new_creates_readme(configured):
    _new("City Hall Probe", configured)
    readme = (configured / "city-hall-probe" / "README.md").read_text()
    assert "City Hall Probe" in readme
    assert "github.com/tomcardoso/watchdog" in readme        # GitHub link
    assert "/issues" in readme                                # report-an-issue link
    assert "Public records only" in readme


def test_new_creates_watchlist(configured):
    _new("City Hall Probe", configured)
    watchlist = (configured / "city-hall-probe" / "watchlist.md").read_text()
    assert "Watch list" in watchlist
    assert "case-insensitive" in watchlist
    # comments/blank-only template parses to zero active terms
    from watchdog.pipeline import watchlist as wl
    assert wl.load_terms(configured / "city-hall-probe") == []


def test_new_creates_bases_dashboard(configured):
    _new("City Hall Probe", configured)
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


def test_new_vault_runs_no_commands_from_its_settings(configured):
    """The app registers a session's hooks itself (D299): a new vault's settings carry none, and
    there is no hot.md (D285)."""
    _new("City Hall Probe", configured)
    settings = json.loads(
        (configured / "city-hall-probe" / ".claude" / "settings.json").read_text()
    )
    assert "hooks" not in settings
    assert not any(r.startswith("Bash(") for r in settings["permissions"]["allow"])
    assert not (configured / "city-hall-probe" / "hot.md").exists()


def test_new_blocks_reads_outside_vault(configured):
    """Read/Glob/Grep are unrestricted by default in Claude Code — no confinement to the
    vault without this setting, so a session could otherwise silently read another vault or
    ~/.watchdog/credentials.json."""
    _new("City Hall Probe", configured)
    settings = json.loads(
        (configured / "city-hall-probe" / ".claude" / "settings.json").read_text()
    )
    assert settings["permissions"]["blockReadsOutsideWorkingDirectories"] is True


def test_new_never_writes_dead_write_permission_rules(configured):
    """Claude Code only matches Edit(path) rules for file-permission checks — Write(path)
    rules are never matched (and print a startup warning). New vaults must not carry any."""
    _new("City Hall Probe", configured)
    settings = json.loads(
        (configured / "city-hall-probe" / ".claude" / "settings.json").read_text()
    )
    allow = settings["permissions"]["allow"]
    assert not any(p.startswith("Write(") for p in allow)
    assert "Edit(briefings/**)" in allow
    # Pipeline-owned paths are not hand-editable from a session.
    assert "Edit(morgue/**)" not in allow
    assert "Edit(.watchdog/registry/**)" not in allow


def test_new_registers_project(configured, wdg_home):
    _new("My Story", configured)
    listed = json.loads((wdg_home / "projects.json").read_text())
    assert "my-story" in listed
    assert listed["my-story"]["name"] == "My Story"


def test_new_duplicate_exits(configured):
    _new("My Story", configured)
    with pytest.raises(SystemExit):
        _new("My Story", configured)


def test_new_invalid_name_exits(configured):
    with pytest.raises(SystemExit):
        _new("!!!", configured)


def test_new_slug_too_long_exits_cleanly(configured):
    # A slugified name past the filesystem's ~255-byte path-component limit must fail with a
    # clean CLI error (#636), not an uncaught OSError from mkdir().
    with pytest.raises(SystemExit, match="too long"):
        _new("x" * 300, configured)


def test_new_installs_skills_per_project(configured):
    _new("My Story", configured)
    commands_dir = configured / "my-story" / ".claude" / "commands"
    assert commands_dir.is_dir()
    skill_files = list(commands_dir.glob("*.md"))
    assert skill_files, "No skill files installed into vault .claude/commands/"


def test_new_uses_config_projects_dir(configured, wdg_home):
    # No --dir: should use the projects_dir from config.json
    _new("Auto Dir Test")
    listed = json.loads((wdg_home / "projects.json").read_text())
    assert "auto-dir-test" in listed
    assert str(configured) in listed["auto-dir-test"]["path"]


def test_projects_dir_falls_back_when_key_missing(wdg_home):
    # A config.json that exists but omits "projects_dir" (e.g. hand-edited to set only one
    # other knob) must fall back to the documented ~/Investigations default, not KeyError —
    # every command that resolves a vault path calls this, so a bare `config["projects_dir"]`
    # crashed the whole CLI on a config missing that one key.
    (wdg_home / "config.json").write_text(json.dumps({"extractor_model": "sonnet"}) + "\n")
    assert projects.projects_dir() == Path.home() / "Investigations"


@pytest.mark.parametrize("value", ["", None])
def test_projects_dir_falls_back_when_key_is_falsy(wdg_home, value):
    # An explicit "" or null is not "missing" as far as a bare .get(key, default) is concerned —
    # it's a genuine falsy value that must be treated the same as absent, or Path("").expanduser()
    # (the cwd) would become the silent vault-creation directory.
    (wdg_home / "config.json").write_text(json.dumps({"projects_dir": value}) + "\n")
    assert projects.projects_dir() == Path.home() / "Investigations"


def test_projects_dir_corrupt_config_exits_cleanly(wdg_home):
    # A truncated/corrupt config.json must not crash the CLI with a raw JSONDecodeError (#636) —
    # every command that resolves a vault path calls this.
    (wdg_home / "config.json").write_text("not json {{{")
    with pytest.raises(SystemExit, match="settings file is damaged"):
        projects.projects_dir()


def test_load_projects_corrupt_projects_file_exits_cleanly(wdg_home):
    # A truncated/corrupt projects.json must not crash the CLI with a raw JSONDecodeError
    # (#636) — nearly every command calls this to resolve a project by name.
    (wdg_home / "projects.json").write_text("not json {{{")
    with pytest.raises(SystemExit, match="list of investigations is damaged"):
        projects.load()


def test_new_description_stored_in_registry(configured, wdg_home):
    _new("My Story", configured, description="Tracking shell companies linked to city contracts")
    listed = json.loads((wdg_home / "projects.json").read_text())
    assert listed["my-story"]["description"] == "Tracking shell companies linked to city contracts"


def test_new_description_no_context_file_created(configured):
    # context.md is written at ingest/context time, not at vault creation
    _new("My Story", configured, description="Tracking shell companies linked to city contracts")
    assert not (configured / "my-story" / "context.md").exists()


def test_new_no_description_no_context_file(configured):
    _new("My Story", configured)
    assert not (configured / "my-story" / "context.md").exists()
    assert "description" not in json.loads(
        (configured / "my-story" / ".watchdog" / "registry" / "registry.json").read_text()
    )


def test_register_obsidian_vault_writes_entry(tmp_path, monkeypatch):
    monkeypatch.setattr("watchdog.obsidian.config_path", lambda: tmp_path / "obsidian.json")
    vault = tmp_path / "my-vault"
    obsidian.register(vault)
    data = json.loads((tmp_path / "obsidian.json").read_text())
    vaults = data["vaults"]
    assert len(vaults) == 1
    entry = next(iter(vaults.values()))
    assert entry["path"] == str(vault)
    assert isinstance(entry["ts"], int)


def test_register_obsidian_vault_appends_to_existing(tmp_path, monkeypatch):
    cfg = tmp_path / "obsidian.json"
    cfg.write_text(json.dumps({"vaults": {"aabbccdd11223344": {"path": "/other", "ts": 123}}}))
    monkeypatch.setattr("watchdog.obsidian.config_path", lambda: cfg)
    obsidian.register(tmp_path / "new-vault")
    data = json.loads(cfg.read_text())
    assert len(data["vaults"]) == 2


def test_obsidian_config_path_windows(monkeypatch):
    monkeypatch.setattr("watchdog.obsidian.sys.platform", "win32")
    monkeypatch.setenv("APPDATA", "/win/appdata")
    p = obsidian.config_path()
    assert "obsidian" in str(p)
    assert "appdata" in str(p).lower()


def test_obsidian_config_path_linux_without_xdg(monkeypatch, tmp_path):
    # Unset XDG_CONFIG_HOME means ~/.config, never a folder under the working directory.
    monkeypatch.setattr("watchdog.obsidian.sys.platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr("watchdog.obsidian.Path.home", lambda: tmp_path)
    assert obsidian.config_path() == tmp_path / ".config" / "obsidian" / "obsidian.json"
    monkeypatch.setenv("XDG_CONFIG_HOME", "/xdg")
    assert obsidian.config_path() == Path("/xdg") / "obsidian" / "obsidian.json"


def test_find_takes_the_exact_slug_only(configured, wdg_home):
    _new("Shell Co", configured)
    slug, info = projects.find("shell-co")
    assert slug == "shell-co" and info["name"] == "Shell Co"
    for near in ("Shell Co", "shell-c", "shell", "", None):
        with pytest.raises(SystemExit, match="no investigation is listed"):
            projects.find(near)


def test_a_stale_slug_never_reaches_another_investigation(configured, wdg_home):
    """Removing "port" and then a stale second "delete and purge port" used to purge Port Calder,
    the only remaining prefix match (C3)."""
    _new("Port", configured)
    _new("Port Calder", configured)
    pops.delete(REP, slug="port", purge=False)
    with pytest.raises(SystemExit, match="no investigation is listed"):
        pops.delete(REP, slug="port", purge=True)
    assert (configured / "port-calder").is_dir() and "port-calder" in projects.load()
    for op_args in ({"slug": "port", "name": "X"}, {"slug": "port", "path": str(configured / "x")}):
        fn = pops.rename if "name" in op_args else pops.move
        with pytest.raises(SystemExit, match="no investigation is listed"):
            fn(REP, **op_args)
    assert "port-calder" in projects.load()


def test_delete_removes_from_registry(configured, monkeypatch, capsys):
    _new("Shell Co", configured)
    pops.delete(REP, slug="shell-co", purge=False)
    assert "shell-co" not in projects.load()
    assert "Removed" in REP.text


def test_delete_purge_removes_files(configured, monkeypatch, capsys):
    _new("Shell Co", configured)
    vault = configured / "shell-co"
    assert vault.exists()
    pops.delete(REP, slug="shell-co", purge=True)
    assert not vault.exists()
    assert "Deleted" in REP.text


def test_delete_purge_removes_the_vaults_telemetry_rows(configured, monkeypatch):
    from watchdog import telemetry_db
    _new("Shell Co", configured)
    _new("Other Co", configured)
    record = {"task": "extract", "model": "m", "backend": "claude-api", "input_tokens": 1,
              "output_tokens": 1, "cache_read_tokens": 0, "cache_write_tokens": 0}
    for slug in ("shell-co", "other-co"):
        telemetry_db.record_call(record, vault=configured / slug, run_id="r",
                                 benchmark_arm_id=None, prompt_hash=None, config_snapshot=None)
    pops.delete(REP, slug="shell-co", purge=True)
    conn = sqlite3.connect(telemetry_db.DB_PATH)
    try:
        assert conn.execute("SELECT vault_name FROM calls").fetchall() == [("other-co",)]
    finally:
        conn.close()


def test_delete_purge_removes_telemetry_when_the_folder_is_already_gone(configured, monkeypatch):
    """A vault deleted by hand before `delete --purge` used to keep its telemetry rows."""
    import shutil
    _new("Shell Co", configured)
    _new("Other Co", configured)
    _record_telemetry(configured / "shell-co")
    _record_telemetry(configured / "other-co")
    shutil.rmtree(configured / "shell-co")
    pops.delete(REP, slug="shell-co", purge=True)
    assert _telemetry_names() == ["other-co"]


def test_delete_purge_keeps_rows_another_investigation_at_that_path_owns(configured, monkeypatch):
    _new("Shell Co", configured)
    _record_telemetry(configured / "shell-co")
    listed = projects.load()
    listed["other"] = {**listed["shell-co"], "name": "Other"}
    projects.save(listed)
    pops.delete(REP, slug="shell-co", purge=True)
    assert _telemetry_names() == ["shell-co"]
    assert (configured / "shell-co").is_dir()      # the other investigation's files stay too


def test_rename_then_purge_removes_the_renamed_vaults_telemetry(configured, monkeypatch):
    _new("Shell Co", configured)
    _record_telemetry(configured / "shell-co")
    pops.rename(REP, slug="shell-co", name="Oil Co")
    assert _telemetry_names() == ["oil-co"]
    pops.delete(REP, slug="oil-co", purge=True)
    assert _telemetry_names() == []


def test_move_relocates_the_vaults_telemetry(configured):
    _new("Shell Co", configured)
    _record_telemetry(configured / "shell-co")
    new_path = configured / "new-location"
    pops.move(REP, slug="shell-co", path=str(new_path))
    from watchdog import telemetry_db
    assert telemetry_db.purge_vault(new_path) == 1


def test_delete_purge_removes_the_vault_without_a_pointless_backup(configured, monkeypatch, capsys):
    """--purge used to snapshot the registry *inside* the folder it then deleted, and print a
    paragraph explaining the snapshot couldn't undo anything. It just deletes now."""
    _new("Shell Co", configured)
    vault = configured / "shell-co"
    pops.delete(REP, slug="shell-co", purge=True)
    out = (REP.text)
    assert "Deleted" in out
    assert "snapshot" not in out.lower()
    assert not vault.exists()


def test_delete_removes_from_obsidian_registry(configured, monkeypatch, capsys):
    _new("Shell Co", configured)
    vault = configured / "shell-co"
    cfg_path = obsidian.config_path()
    cfg_path.write_text(json.dumps({"vaults": {"abc123": {"path": str(vault), "ts": 0}}}))
    pops.delete(REP, slug="shell-co", purge=False)
    data = json.loads(cfg_path.read_text())
    assert not any(v.get("path") == str(vault) for v in data["vaults"].values())


def test_move_updates_registry_when_already_moved(configured, capsys):
    _new("Shell Co", configured)
    vault = configured / "shell-co"
    new_path = configured / "renamed"
    vault.rename(new_path)
    pops.move(REP, slug="shell-co", path=str(new_path))
    assert projects.load()["shell-co"]["path"] == str(new_path)
    assert "Updated" in REP.text


def test_move_moves_files_when_src_exists(configured, capsys):
    _new("Shell Co", configured)
    vault = configured / "shell-co"
    new_path = configured / "new-location"
    pops.move(REP, slug="shell-co", path=str(new_path))
    assert not vault.exists()
    assert new_path.exists()
    assert projects.load()["shell-co"]["path"] == str(new_path)
    assert "Moved" in REP.text


def test_move_reports_special_files_cleanly(configured, monkeypatch):
    """`shutil.move` falls back to `shutil.copytree` on a cross-filesystem move, which raises
    `shutil.Error` if the vault contains a non-regular file (FIFO, socket, device) it can't
    copy (#636). Force the fallback by making `os.rename` look like a cross-device move — same
    trigger a real move across filesystems would hit — and confirm a clean CLI error instead of
    a raw traceback."""
    import errno
    _new("Shell Co", configured)
    vault = configured / "shell-co"
    os.mkfifo(vault / "myfifo")
    new_path = configured / "new-location"

    real_rename = os.rename

    def fake_rename(a, b):
        raise OSError(errno.EXDEV, "cross-device")
    monkeypatch.setattr(os, "rename", fake_rename)

    with pytest.raises(SystemExit, match="myfifo"):
        pops.move(REP, slug="shell-co", path=str(new_path))
    monkeypatch.setattr(os, "rename", real_rename)


def test_move_updates_obsidian_registry(configured, capsys):
    _new("Shell Co", configured)
    vault = configured / "shell-co"
    new_path = configured / "new-location"
    cfg_path = obsidian.config_path()
    cfg_path.write_text(json.dumps({"vaults": {"abc123": {"path": str(vault), "ts": 0}}}))
    pops.move(REP, slug="shell-co", path=str(new_path))
    data = json.loads(cfg_path.read_text())
    paths = [v["path"] for v in data["vaults"].values()]
    assert str(new_path) in paths
    assert str(vault) not in paths


def test_archive_sets_flag(configured, capsys):
    _new("Shell Co", configured)
    pops.archive(REP, slug="shell-co")
    assert projects.load()["shell-co"].get("archived") is True
    assert "Archived" in REP.text


def test_unarchive_clears_flag(configured, capsys):
    _new("Shell Co", configured)
    pops.archive(REP, slug="shell-co")
    pops.archive(REP, slug="shell-co", archived=False)
    assert not projects.load()["shell-co"].get("archived")
    assert "Restored" in REP.text


def test_home_config_directory_is_not_a_vault(tmp_path):
    """The global ~/.watchdog config directory must not make the home folder look like a vault."""
    from watchdog.vault_paths import is_vault
    home = tmp_path / "home"
    (home / ".watchdog").mkdir(parents=True)
    (home / ".watchdog" / "config.json").write_text("{}")
    assert not is_vault(home)
    vault = tmp_path / "v"
    (vault / ".watchdog" / "registry").mkdir(parents=True)
    assert is_vault(vault)


def test_rename_updates_registry_and_folder(configured, capsys):
    _new("Shell Co", configured)
    vault = configured / "shell-co"
    pops.rename(REP, slug="shell-co", name="Oil Co")
    listed = projects.load()
    assert "oil-co" in listed
    assert "shell-co" not in listed
    assert listed["oil-co"]["name"] == "Oil Co"
    assert (configured / "oil-co").exists()
    assert not vault.exists()
    assert "Renamed" in REP.text


def test_rename_updates_obsidian_registry(configured, capsys):
    _new("Shell Co", configured)
    vault = configured / "shell-co"
    new_vault = configured / "oil-co"
    cfg_path = obsidian.config_path()
    cfg_path.write_text(json.dumps({"vaults": {"abc123": {"path": str(vault), "ts": 0}}}))
    pops.rename(REP, slug="shell-co", name="Oil Co")
    data = json.loads(cfg_path.read_text())
    paths = [v["path"] for v in data["vaults"].values()]
    assert str(new_vault) in paths
    assert str(vault) not in paths


def test_rename_not_blocked_by_an_abandoned_lock(configured, capsys):
    """D293: a lock past the age window (its run can't be checked) holds nothing."""
    _new("Shell Co", configured)
    vault = configured / "shell-co"
    (vault / ".watchdog" / ".preprocessing-lock").write_text("started_at: 2026-01-01T00:00:00Z\npid: 99\n")
    pops.rename(REP, slug="shell-co", name="Oil Co")
    assert "oil-co" in projects.load()


def test_rename_blocked_by_chew_lock(configured, capsys):
    _new("Shell Co", configured)
    vault = configured / "shell-co"
    (vault / ".watchdog" / ".preprocessing-lock").write_text(f"started_at: {_now_iso()}\npid: 99\n")
    with pytest.raises(SystemExit):
        pops.rename(REP, slug="shell-co", name="Oil Co")
    assert "shell-co" in projects.load()


def test_rename_blocked_by_ingest_lock(configured, capsys):
    _new("Shell Co", configured)
    vault = configured / "shell-co"
    (vault / ".watchdog" / "registry" / ".processing-lock").write_text(f"started_at: {_now_iso()}\n")
    with pytest.raises(SystemExit):
        pops.rename(REP, slug="shell-co", name="Oil Co")


def test_rename_same_slug_updates_name_only(configured, capsys):
    _new("Shell Co", configured)
    pops.rename(REP, slug="shell-co", name="Shell Co")
    listed = projects.load()
    assert "shell-co" in listed
    assert (configured / "shell-co").exists()


def test_describe_sets_description(configured, wdg_home, capsys):
    _new("My Story", configured)
    pops.describe(REP, slug="my-story", description="Offshore accounts linked to city contracts")
    listed = projects.load()
    assert listed["my-story"]["description"] == "Offshore accounts linked to city contracts"


def test_describe_updates_existing(configured, wdg_home, capsys):
    _new("My Story", configured, description="old desc")
    pops.describe(REP, slug="my-story", description="new desc")
    assert projects.load()["my-story"]["description"] == "new desc"


def test_load_config_with_invalid_utf8_exits_cleanly(wdg_home):
    (wdg_home / "config.json").write_bytes(b'{"x": "\xff\xfe"}')
    with pytest.raises(SystemExit, match="settings file is damaged"):
        config.load()
    with pytest.raises(SystemExit, match="settings file is damaged"):
        projects.projects_dir()


def test_load_projects_with_invalid_utf8_exits_cleanly(wdg_home):
    (wdg_home / "projects.json").write_bytes(b'{"x": "\xff\xfe"}')
    with pytest.raises(SystemExit, match="list of investigations is damaged"):
        projects.load()


def test_notify_no_op_on_non_darwin(monkeypatch):
    monkeypatch.setattr("watchdog.ops.projects.sys.platform", "linux")
    calls = []
    monkeypatch.setattr("watchdog.ops.projects.subprocess.run", lambda *a, **k: calls.append(a))
    pops._notify("title", "body")
    assert calls == []


def test_notify_calls_osascript_on_darwin(monkeypatch):
    monkeypatch.setattr("watchdog.ops.projects.sys.platform", "darwin")
    calls = []
    def fake_run(cmd, **kw):
        calls.append(cmd)
    monkeypatch.setattr("watchdog.ops.projects.subprocess.run", fake_run)
    pops._notify('Watchdog — "Quoted" project', "3 files chewed")
    assert len(calls) == 1
    assert calls[0][0] == "osascript"
    # Title and body are passed as arguments, never spliced into the script source.
    assert calls[0][-2:] == ['Watchdog — "Quoted" project', "3 files chewed"]
    assert not any("Quoted" in part for part in calls[0][:-2])


def test_poll_stable_files_holds_growing_file(tmp_path):
    f = tmp_path / "big.pdf"
    f.write_bytes(b"a" * 10)
    ready, pending = pops._poll_stable_files({f}, {})
    assert ready == []
    assert pending == {f: 10}


def test_poll_stable_files_releases_file_once_size_holds(tmp_path):
    f = tmp_path / "big.pdf"
    f.write_bytes(b"a" * 10)
    # First poll: no prior size recorded — held.
    ready, pending = pops._poll_stable_files({f}, {})
    assert ready == []
    # Second poll: same size as last time — copy finished.
    ready, pending = pops._poll_stable_files({f}, pending)
    assert ready == [f]
    assert pending == {}


def test_poll_stable_files_keeps_holding_a_still_growing_file(tmp_path):
    f = tmp_path / "big.pdf"
    f.write_bytes(b"a" * 10)
    ready, pending = pops._poll_stable_files({f}, {})
    f.write_bytes(b"a" * 20)   # more bytes copied in between polls
    ready, pending = pops._poll_stable_files({f}, pending)
    assert ready == []
    assert pending == {f: 20}


def test_poll_stable_files_skips_file_removed_before_stat(tmp_path):
    f = tmp_path / "gone.pdf"   # never created — simulates a race with deletion
    ready, pending = pops._poll_stable_files({f}, {})
    assert ready == []
    assert pending == {}




# ── what an investigation has waiting (the Overview and the investigation cards) ───────────────

def _queue_file(vault: Path, sha: str) -> None:
    q = vault / ".watchdog" / "queue"
    q.mkdir(parents=True, exist_ok=True)
    (q / f"{sha}.json").write_text(json.dumps({"sha256": sha, "filename": f"{sha}.pdf", "page_count": 1}))


def _extracted_file(vault: Path, sha: str) -> None:
    e = vault / ".watchdog" / "extracted"
    e.mkdir(parents=True, exist_ok=True)
    (e / f"{sha}.json").write_text(json.dumps({"document": {"filename": f"{sha}.pdf"}}))


def test_awaiting_processing_and_post_processing_are_counted_apart(configured):
    """A queue file stays until post-processing commits it (#461), so a raw queue count can't
    tell "pre-processed, not yet processed" from "processed, awaiting post-processing"."""
    vault = _new("Test Proj", configured)["path"]
    vault = Path(vault)
    _queue_file(vault, "sha_not_dug")
    _queue_file(vault, "sha_dug")
    _extracted_file(vault, "sha_dug")
    assert vault_mod.count_awaiting_processing(vault) == 1
    assert vault_mod.count_awaiting_post(vault) == 1
    from watchdog import overview
    s = overview.summary(vault)
    assert (s["awaiting_dig"], s["awaiting_bark"]) == (1, 1)


def test_overview_counts_incoming_files_and_queued_research(configured):
    vault = Path(_new("Test Proj", configured)["path"])
    (vault / "incoming" / "pending.pdf").write_text("")
    (vault / "incoming" / "also.pdf").write_text("")
    from watchdog import overview
    assert overview.summary(vault)["research_urls"] == 0
    q = vault / ".watchdog" / "research" / "queue.tsv"
    q.parent.mkdir(parents=True, exist_ok=True)
    q.write_text("https://a.com\tA\nhttps://b.com\tB\n", encoding="utf-8")
    s = overview.summary(vault)
    assert s["incoming"] == 2 and s["research_urls"] == 2
    assert overview.has_work(s)


# ── Refresh Claude setup (`refresh-claude-setup`) ─────────────────────────────────────────

def _vault_claude_md(configured):
    vault = Path(_new("City Hall Probe", configured)["path"])
    return vault, vault / ".claude" / "CLAUDE.md"


def test_refresh_restores_the_read_scope_setting(configured):
    from watchdog.ops.maintenance import refresh_claude_setup
    vault, _ = _vault_claude_md(configured)
    settings_path = vault / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text())
    del settings["permissions"]["blockReadsOutsideWorkingDirectories"]
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    refresh_claude_setup(REP, vault)
    assert json.loads(settings_path.read_text())["permissions"]["blockReadsOutsideWorkingDirectories"] is True


def test_refresh_replaces_watchdogs_section_and_keeps_the_reporters_notes(configured):
    """D264: stale session instructions that contradicted tightened permissions are replaced;
    whatever the reporter wrote after the end marker stays."""
    from watchdog.ops.maintenance import refresh_claude_setup
    vault, path = _vault_claude_md(configured)
    fresh = path.read_text()
    assert fresh.startswith("<!-- watchdog:begin") and fresh.rstrip().endswith("<!-- watchdog:end -->")
    assert "# City Hall Probe — Watchdog" in fresh
    stale = fresh.replace("Read `context.md`", "Read `stale.md`") + "\nMy own note: check the minutes.\n"
    path.write_text(stale)
    refresh_claude_setup(REP, vault)
    text = path.read_text()
    assert "Read `context.md`" in text and "stale.md" not in text
    assert text.endswith("\nMy own note: check the minutes.\n")


def test_refresh_leaves_an_up_to_date_claude_md_alone(configured):
    from watchdog.ops.maintenance import refresh_claude_setup
    vault, path = _vault_claude_md(configured)
    before = path.read_text()
    refresh_claude_setup(REP, vault)
    assert path.read_text() == before


def test_refresh_writes_only_what_changed(configured):
    from watchdog.vault import refresh_claude_setup
    vault, path = _vault_claude_md(configured)
    assert refresh_claude_setup(vault, "City Hall Probe") == []
    (vault / ".claude" / "commands" / "watchdog-query.md").write_text("edited")
    (vault / ".claude" / "commands" / "watchdog-gone.md").write_text("retired")
    (vault / ".claude" / "commands" / "my-checklist.md").write_text("mine")
    assert refresh_claude_setup(vault, "City Hall Probe") == ["commands/watchdog-query.md",
                                                              "commands/watchdog-gone.md"]
    assert (vault / ".claude" / "commands" / "my-checklist.md").read_text() == "mine"
    assert not (vault / ".claude" / "commands" / "watchdog-gone.md").exists()


def test_refresh_writes_a_missing_claude_md(configured):
    from watchdog.ops.maintenance import refresh_claude_setup
    vault, path = _vault_claude_md(configured)
    path.unlink()
    refresh_claude_setup(REP, vault)
    assert "# City Hall Probe — Watchdog" in path.read_text()
