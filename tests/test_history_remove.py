"""Removing versions from the history (D288) and the history's cached size."""

import json
import threading
import zlib
from pathlib import Path

import pytest

from watchdog.pipeline import history

from tests.gui_support import call, call_error, register
from tests.test_history import _log, _vault

pytest_plugins = ["tests.gui_support"]   # the wdg_home fixture


def _all_blob_bytes(v: Path) -> list[bytes]:
    return [zlib.decompress(p.read_bytes())
            for p in (history.history_dir(v) / "objects").rglob("*") if p.is_file()]


def _edits(v: Path, *texts: str) -> list[int]:
    out = []
    for t in texts:
        (v / "context.md").write_text(t)
        out.append(history.snapshot(v, {"kind": "edit", "file": "context.md"}))
    return out


def _consistent(v: Path) -> None:
    """The saved index equals one rebuilt from the log, and every blob a version names exists."""
    hdir = history.history_dir(v)
    saved = json.loads((hdir / "index.json").read_text())
    rebuilt = history._rebuild_index(hdir)
    for idx in (saved, rebuilt):
        for e in idx["files"].values():
            e["st"] = None
    assert saved == rebuilt
    for rec in _log(v):
        for _p, b in rec["changes"]:
            assert b is None or history._blob_path(hdir, b).is_file()


def test_removing_a_version_deletes_its_text_from_disk(tmp_path):
    v = _vault(tmp_path)
    history.snapshot(v, {"kind": "found"})
    secret, _after = _edits(v, "Source: Marguerite Olsen\n", "Source removed\n")
    assert any(b"Marguerite Olsen" in b for b in _all_blob_bytes(v))
    out = history.remove(v, secret, "context.md")
    assert out["removed"] == [{"path": "context.md", "version": secret}] and out["purged"] == 1
    assert not any(b"Marguerite Olsen" in b for b in _all_blob_bytes(v))
    assert not any(b"Marguerite Olsen" in p.read_bytes()
                   for p in history.history_dir(v).rglob("*") if p.is_file())
    # A version that changed only this file is gone from the log altogether.
    assert secret not in [r["version"] for r in _log(v)]
    rows = history.file_history(v, "context.md")["versions"]
    assert [r["version"] for r in rows] == [secret + 1, 1]
    assert rows[0]["removed_before"] == 1
    _consistent(v)


def test_a_blob_shared_with_another_version_is_kept(tmp_path):
    v = _vault(tmp_path)
    history.snapshot(v, {"kind": "found"})
    a1, _b, a2, _c = _edits(v, "same words\n", "other\n", "same words\n", "now\n")
    out = history.remove(v, a1, "context.md")
    assert out["purged"] == 0 and out["shared"] == [{"path": "context.md", "version": a1}]
    assert history.read_version(v, "context.md", a2)["text"] == "same words\n"
    # Removing the other copy as well removes the text.
    out = history.remove(v, a2, "context.md")
    assert out["purged"] == 1
    assert not any(b == b"same words\n" for b in _all_blob_bytes(v))
    _consistent(v)


def test_a_blob_shared_with_another_file_is_kept(tmp_path):
    v = _vault(tmp_path)
    (v / "wiki").mkdir()
    (v / "wiki" / "a.md").write_text("twin\n")
    (v / "wiki" / "b.md").write_text("twin\n")
    first = history.snapshot(v, {"kind": "found"})
    (v / "wiki" / "a.md").write_text("changed\n")
    history.snapshot(v, {"kind": "edit"})
    out = history.remove(v, first, "wiki/a.md")
    assert out["purged"] == 0 and out["shared"]
    assert history.read_version(v, "wiki/b.md", first)["text"] == "twin\n"


def test_the_current_version_cannot_be_removed(tmp_path):
    v = _vault(tmp_path)
    history.snapshot(v, {"kind": "found"})
    (last,) = _edits(v, "now\n")
    with pytest.raises(history.CannotRemove):
        history.remove(v, last, "context.md")
    # A change made outside Watchdog is recorded first, so what was latest becomes removable
    # and what is on disk now is not.
    (v / "context.md").write_text("typed in Obsidian\n")
    history.remove(v, last, "context.md")
    rows = history.file_history(v, "context.md")["versions"]
    assert rows[0]["current"] and rows[0]["label"] == "Changed since the last recorded version"
    with pytest.raises(history.CannotRemove):
        history.remove(v, rows[0]["version"], "context.md")
    with pytest.raises(LookupError):
        history.remove(v, 999, "context.md")


def test_this_and_older_versions(tmp_path):
    v = _vault(tmp_path)
    history.snapshot(v, {"kind": "found"})
    vs = _edits(v, "a secret\n", "a secret, more\n", "clean\n", "clean, more\n")
    out = history.remove(v, vs[2], "context.md", older=True)
    assert {r["version"] for r in out["removed"]} == {1, vs[0], vs[1], vs[2]}
    assert not any(b"secret" in b for b in _all_blob_bytes(v))
    rows = history.file_history(v, "context.md")["versions"]
    assert [r["version"] for r in rows] == [vs[3]] and rows[0]["removed_before"] == 4
    # Other files' entries in version 1 stay; version 1 says one change was removed from it.
    one = next(r for r in _log(v) if r["version"] == 1)
    assert {p for p, _ in one["changes"]} == {"entities/person/jane.md", ".watchdog/registry/entities.json"}
    assert one["removed"] == 1
    _consistent(v)


def test_diffs_compare_across_the_gap_and_say_so(tmp_path):
    v = _vault(tmp_path)
    history.snapshot(v, {"kind": "found"})
    a, b, c = _edits(v, "one\n", "one two\n", "one two three\n")
    history.remove(v, b, "context.md")
    d = history.diff(v, "context.md", c)
    assert d["before"]["version"] == a and d["removed_between"] == 1
    assert d["added"] == 1 and d["removed"] == 1                 # "one" -> "one two three"
    # Removing the version before the gap carries the count to the same next version.
    history.remove(v, a, "context.md")
    d = history.diff(v, "context.md", c)
    assert d["before"]["version"] == 1 and d["removed_between"] == 2
    assert history.diff(v, "context.md", 1)["removed_between"] == 0
    assert history.diff(v, "context.md", c, "current")["removed_between"] == 0


def test_removing_a_whole_version_keeps_files_it_left_as_they_are(tmp_path):
    v = _vault(tmp_path)
    history.snapshot(v, {"kind": "found"})
    (v / "context.md").write_text("Source: Marguerite Olsen\n")
    (v / "entities" / "person" / "jane.md").write_text("# Jane\n\n## Notes\n\nMet M. Olsen.\n")
    both = history.snapshot(v, {"kind": "edit"})
    (v / "context.md").write_text("clean\n")
    history.snapshot(v, {"kind": "edit"})
    plan = history.remove(v, both, dry_run=True)
    assert plan["dry_run"] and plan["kept_current"] == ["entities/person/jane.md"]
    assert plan["removed"] == [{"path": "context.md", "version": both}] and plan["purged"] == 1
    assert any(b"Marguerite Olsen" in b for b in _all_blob_bytes(v))         # dry run: nothing changed
    out = history.remove(v, both)
    assert out["purged"] == 1 and not any(b"Marguerite Olsen" in b for b in _all_blob_bytes(v))
    rec = next(r for r in _log(v) if r["version"] == both)
    assert rec["changes"] == [["entities/person/jane.md", rec["changes"][0][1]]] and rec["removed"] == 1
    assert history.versions(v)["versions"][1]["removed"] == 1
    _consistent(v)


def test_removal_is_refused_while_a_run_holds_the_vault(tmp_path):
    from watchdog.vault_paths import processing_lock
    v = _vault(tmp_path)
    history.snapshot(v, {"kind": "found"})
    _edits(v, "x\n")
    processing_lock(v).write_text("pid: 1\n")
    with pytest.raises(history.HistoryBusy):
        history.remove(v, 1, "context.md")


def test_removal_and_snapshots_interleave_safely(tmp_path):
    v = _vault(tmp_path)
    history.snapshot(v, {"kind": "found"})
    (v / "wiki").mkdir()
    errors = []

    def writer():
        try:
            for i in range(40):
                (v / "wiki" / "page.md").write_text(f"page {i}\n")
                history.snapshot(v, {"kind": "edit"})
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def remover():
        try:
            for _ in range(40):
                rows = history.file_history(v, "context.md")["versions"]
                (v / "context.md").write_text(f"ctx {len(rows)}\n")
                history.snapshot(v, {"kind": "edit"})
                rows = history.file_history(v, "context.md")["versions"]
                if len(rows) > 2:
                    history.remove(v, rows[1]["version"], "context.md")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=writer), threading.Thread(target=remover)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    _consistent(v)
    versions = [r["version"] for r in _log(v)]
    assert versions == sorted(set(versions))
    page = history.file_history(v, "wiki/page.md")["versions"]
    assert history.read_version(v, "wiki/page.md", page[0]["version"])["text"] == "page 39\n"


def test_history_size_is_cached_and_matches_a_walk(tmp_path):
    v = _vault(tmp_path)
    assert history.history_bytes(v) is None
    history.snapshot(v, {"kind": "found"})
    _edits(v, "a\n", "b\n")
    hdir = history.history_dir(v)
    assert history.history_bytes(v) == history._walk_bytes(hdir)
    history.remove(v, 2, "context.md")
    assert history.history_bytes(v) == history._walk_bytes(hdir)
    (hdir / "size.json").unlink()                       # a store from before the cache
    assert history.history_bytes(v) == history._walk_bytes(hdir)
    _edits(v, "c\n")
    assert history.history_bytes(v) == history._walk_bytes(hdir)
    history.clear(v)
    assert history.history_bytes(v) == history._walk_bytes(hdir)


def test_remove_api_and_project_size(tmp_path, wdg_home):
    v = _vault(tmp_path)
    history.snapshot(v, {"kind": "found"})
    _edits(v, "secret\n", "clean\n")
    register(wdg_home, v)
    rows = call("projects.list")
    assert rows[0]["stats"]["history_bytes"] == history._walk_bytes(history.history_dir(v))
    plan = call("history.remove", vault=str(v), version=2, path="context.md", dry_run=True)
    assert plan["purged"] == 1 and plan["dry_run"]
    out = call("history.remove", vault=str(v), version=2, path="context.md")
    assert out["removed"] == [{"path": "context.md", "version": 2}]
    assert call_error("history.remove", vault=str(v), version=3, path="context.md")["code"] == "cannot_remove"
    assert call_error("history.remove", vault=str(v), version=2, path="context.md")["code"] == "not_found"
    assert call_error("history.remove", vault=str(v), version=1, path="morgue/x.pdf")["code"] == "not_tracked"
