import argparse
from pathlib import Path

import pytest

from watchdog.cmd.resolve import cmd_resolve, cmd_unresolve
from watchdog.pipeline import resolutions


def _vault(tmp_path: Path) -> Path:
    (tmp_path / ".watchdog" / "Registry").mkdir(parents=True)
    return tmp_path


def _args(**kw):
    return argparse.Namespace(**{"ids": [], "sync": False, "list": False, **kw})


def test_requires_running_inside_a_vault(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no .watchdog/
    with pytest.raises(SystemExit):
        cmd_resolve(_args(ids=["lead:isolated:x"]))


def test_resolve_ids_marks_them(tmp_path, monkeypatch, capsys):
    v = _vault(tmp_path)
    monkeypatch.chdir(v)
    cmd_resolve(_args(ids=["lead:isolated:x", "lead:inferred:y"]))
    assert resolutions.resolved_ids(v) == {"lead:isolated:x", "lead:inferred:y"}
    assert "Resolved" in capsys.readouterr().out


def test_resolve_with_no_ids_and_no_flags_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(_vault(tmp_path))
    with pytest.raises(SystemExit):
        cmd_resolve(_args())


def test_resolve_sync_imports_checkboxes(tmp_path, monkeypatch, capsys):
    v = _vault(tmp_path)
    (v / "briefings").mkdir()
    (v / "briefings" / "leads-2025-01-01.md").write_text(
        "- [x] **Acme** <!--wid:lead:isolated:acme-->\n", encoding="utf-8")
    monkeypatch.chdir(v)
    cmd_resolve(_args(sync=True))
    assert resolutions.resolved_ids(v) == {"lead:isolated:acme"}
    assert "Resolved" in capsys.readouterr().out


def test_resolve_list(tmp_path, monkeypatch, capsys):
    v = _vault(tmp_path)
    resolutions.resolve(v, ["lead:isolated:acme"])
    monkeypatch.chdir(v)
    cmd_resolve(_args(list=True))
    assert "lead:isolated:acme" in capsys.readouterr().out


def test_unresolve_removes_ids(tmp_path, monkeypatch, capsys):
    v = _vault(tmp_path)
    resolutions.resolve(v, ["lead:isolated:acme"])
    monkeypatch.chdir(v)
    cmd_unresolve(argparse.Namespace(ids=["lead:isolated:acme"]))
    assert resolutions.resolved_ids(v) == frozenset()
    assert "Reopened" in capsys.readouterr().out
