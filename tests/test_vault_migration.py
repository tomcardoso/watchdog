"""The D266 folder rename (`_INCOMING` -> `incoming`, `_CONTEXT` -> `context`) and the automatic
migration of vaults created before it."""

import json
from pathlib import Path

import pytest

from watchdog import vault_paths
from watchdog.vault_paths import (
    context_dir, ensure_current_layout, incoming_dir, incoming_failed_dir,
    incoming_skipped_dir, is_vault, migrate_folder_names, modernize_path,
)


def _old_vault(tmp_path) -> Path:
    v = tmp_path / "old"
    (v / ".watchdog" / "registry").mkdir(parents=True)
    (v / ".watchdog" / "queue").mkdir()
    for sub in ("_INCOMING/_FAILED", "_INCOMING/_SKIPPED", "_CONTEXT"):
        (v / sub).mkdir(parents=True)
    (v / "_INCOMING" / "a.pdf").write_text("a")
    (v / "_INCOMING" / "_FAILED" / "bad.pdf").write_text("bad")
    (v / "_INCOMING" / "_SKIPPED" / "dup.pdf").write_text("dup")
    (v / "_CONTEXT" / "brief.txt").write_text("brief")
    (v / "context.md").write_text("# ctx")
    return v


def test_helpers(tmp_path):
    assert incoming_dir(tmp_path) == tmp_path / "incoming"
    assert incoming_failed_dir(tmp_path) == tmp_path / "incoming" / "failed"
    assert incoming_skipped_dir(tmp_path) == tmp_path / "incoming" / "skipped"
    assert context_dir(tmp_path) == tmp_path / "context"


def test_unmigrated_vault_is_still_a_vault(tmp_path):
    assert is_vault(_old_vault(tmp_path))


def test_migrates_old_only(tmp_path):
    v = _old_vault(tmp_path)
    changes = migrate_folder_names(v)
    assert changes
    assert not (v / "_INCOMING").exists() and not (v / "_CONTEXT").exists()
    assert (v / "incoming" / "a.pdf").read_text() == "a"
    assert (v / "incoming" / "failed" / "bad.pdf").read_text() == "bad"
    assert (v / "incoming" / "skipped" / "dup.pdf").read_text() == "dup"
    assert (v / "context" / "brief.txt").read_text() == "brief"
    assert (v / "context.md").read_text() == "# ctx"          # the file beside it is untouched


def test_idempotent(tmp_path):
    v = _old_vault(tmp_path)
    migrate_folder_names(v)
    assert migrate_folder_names(v) == []
    assert migrate_folder_names(tmp_path / "does-not-exist") == []


def test_both_exist_merges_without_overwriting(tmp_path):
    v = _old_vault(tmp_path)
    (v / "incoming").mkdir()
    (v / "incoming" / "a.pdf").write_text("NEW a")             # clashes with _INCOMING/a.pdf
    (v / "incoming" / "failed").mkdir()
    (v / "incoming" / "failed" / "bad.pdf").write_text("NEW bad")
    (v / "context").mkdir()
    (v / "context" / "brief.txt").write_text("NEW brief")
    before = sum(1 for p in v.rglob("*") if p.is_file())

    migrate_folder_names(v)

    assert not (v / "_INCOMING").exists() and not (v / "_CONTEXT").exists()
    assert (v / "incoming" / "a.pdf").read_text() == "NEW a"
    assert (v / "incoming" / "a-migrated.pdf").read_text() == "a"
    assert (v / "incoming" / "failed" / "bad.pdf").read_text() == "NEW bad"
    assert (v / "incoming" / "failed" / "bad-migrated.pdf").read_text() == "bad"
    assert (v / "incoming" / "skipped" / "dup.pdf").read_text() == "dup"
    assert (v / "context" / "brief.txt").read_text() == "NEW brief"
    assert (v / "context" / "brief-migrated.txt").read_text() == "brief"
    assert sum(1 for p in v.rglob("*") if p.is_file()) == before + 3    # three new files, none lost
    assert migrate_folder_names(v) == []


def test_settings_permissions_rewritten(tmp_path):
    v = _old_vault(tmp_path)
    (v / ".claude").mkdir()
    (v / ".claude" / "settings.json").write_text(json.dumps({
        "permissions": {"allow": ["Edit(_CONTEXT/**)", "Edit(_INCOMING/_FAILED/**)", "Edit(queries/**)"],
                        "deny": ["Read(_INCOMING/_SKIPPED/x)"]}}))
    changes = migrate_folder_names(v)
    perms = json.loads((v / ".claude" / "settings.json").read_text())["permissions"]
    assert perms["allow"] == ["Edit(context/**)", "Edit(incoming/failed/**)", "Edit(queries/**)"]
    assert perms["deny"] == ["Read(incoming/skipped/x)"]
    assert any("settings.json" in c for c in changes)
    assert migrate_folder_names(v) == []


def test_ensure_current_layout_refreshes_commands_and_logs(tmp_path):
    v = _old_vault(tmp_path)
    notes = []
    changes = ensure_current_layout(v, say=notes.append)
    assert changes and len(notes) == 1
    assert (v / ".claude" / "commands" / "watchdog-context.md").exists()
    assert "context/" in (v / ".claude" / "commands" / "watchdog-context.md").read_text()
    assert "MIGRATED" in (v / ".watchdog" / "registry" / "ingest.log").read_text()
    assert ensure_current_layout(v, say=notes.append) == [] and len(notes) == 1


def test_require_vault_migrates_once_per_process(tmp_path, monkeypatch):
    from watchdog.gui import vaultio
    v = _old_vault(tmp_path)
    monkeypatch.setattr(vault_paths, "_ensured", set())
    vaultio.require_vault(str(v))
    assert (v / "incoming" / "a.pdf").exists() and not (v / "_INCOMING").exists()
    (v / "_INCOMING").mkdir()                         # re-created behind our back: cached, left alone
    vaultio.require_vault(str(v))
    assert (v / "_INCOMING").exists()


def test_vault_migrate_rpc(tmp_path):
    from watchdog.gui.api import vault as api
    v = _old_vault(tmp_path)
    assert api.migrate(str(v))["changes"] or (v / "incoming").exists()
    assert api.migrate(str(v)) == {"changes": []}


def test_modernize_path():
    assert modernize_path("_INCOMING/x.pdf") == "incoming/x.pdf"
    assert modernize_path("_INCOMING/_FAILED/x.pdf") == "incoming/failed/x.pdf"
    assert modernize_path("incoming/x.pdf") == "incoming/x.pdf"
    assert modernize_path("morgue/x.pdf") == "morgue/x.pdf"


@pytest.mark.parametrize("sub", ["failed", "skipped"])
def test_count_incoming_excludes_new_names(tmp_path, sub):
    from watchdog import cli
    (tmp_path / "incoming" / sub).mkdir(parents=True)
    (tmp_path / "incoming" / sub / "x.pdf").write_text("")
    (tmp_path / "incoming" / "y.pdf").write_text("")
    assert cli._count_incoming(tmp_path) == 1
