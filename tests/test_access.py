"""Folder-access enforcement (watchdog/access.py). Audit hooks can't be removed once added, so every
enforcement check runs in a fresh Python process with HOME pointed at a temporary folder."""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import watchdog

SRC = str(Path(watchdog.__file__).resolve().parent.parent)


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".watchdog").mkdir(parents=True)
    (h / "granted").mkdir()
    (h / "elsewhere").mkdir()
    (h / ".watchdog" / "access.json").write_text(json.dumps(
        {"version": 1, "folders": [{"path": str(h / "granted"), "label": "test"}]}))
    return h


def run(home: Path, code: str, enforce: bool = True, extra_env: dict | None = None):
    env = {**os.environ, "HOME": str(home), "PYTHONPATH": SRC,
           "TMPDIR": str(home.parent / "tmp")}
    (home.parent / "tmp").mkdir(exist_ok=True)
    env.pop("WATCHDOG_ACCESS_FILE", None)
    if enforce:
        env["WATCHDOG_ENFORCE_ACCESS"] = "1"
    else:
        env.pop("WATCHDOG_ENFORCE_ACCESS", None)
    env.update(extra_env or {})
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)], env=env,
                          capture_output=True, text=True, timeout=60)


PROBE = """
    import os, shutil, sqlite3, tempfile
    import watchdog  # installs the hook when enforcement is on
    from pathlib import Path
    home = Path.home()
    def attempt(label, fn):
        try:
            fn()
            print(label, "ok")
        except PermissionError:
            print(label, "denied")
    attempt("write-granted", lambda: (home / "granted" / "a.md").write_text("x"))
    (home / "granted" / "r.md").write_text("to move")
    attempt("write-elsewhere", lambda: (home / "elsewhere" / "a.md").write_text("x"))
    attempt("read-elsewhere", lambda: (home / "elsewhere").joinpath("r.txt").exists())
    attempt("mkdir-elsewhere", lambda: (home / "elsewhere" / "d").mkdir())
    attempt("rename-out", lambda: os.replace(home / "granted" / "r.md", home / "elsewhere" / "b.md"))
    attempt("sqlite-elsewhere", lambda: sqlite3.connect(str(home / "elsewhere" / "x.db")))
    attempt("sqlite-granted", lambda: sqlite3.connect(str(home / "granted" / "x.db")).close())
    attempt("temp", lambda: tempfile.NamedTemporaryFile(delete=True).close())
    attempt("settings", lambda: (home / ".watchdog" / "config.json").write_text("{}"))
    attempt("access-list", lambda: (home / ".watchdog" / "access.json").write_text("{}"))
    attempt("copy-in", lambda: shutil.copyfile(home / "granted" / "a.md", home / "granted" / "c.md"))
    attempt("copy-out", lambda: shutil.copyfile(home / "granted" / "a.md", home / "elsewhere" / "c.md"))
    attempt("remove-elsewhere", lambda: os.remove(home / "elsewhere" / "keep.txt"))
"""


def _results(out: str) -> dict:
    return dict(line.split() for line in out.strip().splitlines())


def test_enforcement_allows_only_granted_and_exempt_folders(home):
    (home / "elsewhere" / "keep.txt").write_text("mine")
    r = run(home, PROBE)
    assert r.returncode == 0, r.stderr
    res = _results(r.stdout)
    assert res == {
        "write-granted": "ok", "write-elsewhere": "denied", "read-elsewhere": "ok",
        "mkdir-elsewhere": "denied", "rename-out": "denied", "sqlite-elsewhere": "denied",
        "sqlite-granted": "ok", "temp": "ok", "settings": "ok", "access-list": "denied",
        "copy-in": "ok", "copy-out": "denied", "remove-elsewhere": "denied"}
    assert (home / "elsewhere" / "keep.txt").read_text() == "mine"
    assert not (home / "elsewhere" / "a.md").exists()


def test_without_the_variable_nothing_is_enforced(home):
    (home / "elsewhere" / "keep.txt").write_text("mine")
    r = run(home, PROBE, enforce=False)
    assert r.returncode == 0, r.stderr
    assert set(_results(r.stdout).values()) == {"ok"}


def test_a_missing_access_list_grants_nothing(home):
    (home / ".watchdog" / "access.json").unlink()
    r = run(home, """
        import watchdog
        from pathlib import Path
        try:
            (Path.home() / "granted" / "a.md").write_text("x")
            print("ok")
        except PermissionError as e:
            print("denied", "Folder access" in str(e))
    """)
    assert r.stdout.split() == ["denied", "True"], r.stderr


def test_extra_roots_and_symlinks(home):
    (home / "engine").mkdir()
    (home / "granted" / "link").symlink_to(home / "elsewhere")
    r = run(home, """
        import watchdog
        from pathlib import Path
        h = Path.home()
        for label, p in [("engine", h / "engine" / "x"), ("via-link", h / "granted" / "link" / "y")]:
            try:
                p.write_text("x"); print(label, "ok")
            except PermissionError:
                print(label, "denied")
    """, extra_env={"WATCHDOG_EXTRA_WRITE_ROOTS": str(home / "engine")})
    # A symlink inside a granted folder doesn't extend the grant to where it points.
    assert _results(r.stdout) == {"engine": "ok", "via-link": "denied"}, r.stderr


def test_a_real_command_runs_in_a_granted_vault_and_is_refused_in_another(home, tmp_path):
    from tests.test_write_vault import make_vault
    granted = make_vault(home / "granted")
    other = make_vault(home / "elsewhere")
    (home / ".watchdog" / "config.json").write_text("{}")     # "set up", as far as the CLI cares
    (tmp_path / "tmp").mkdir(exist_ok=True)
    for vault, expect_ok in ((granted, True), (other, False)):
        env = {**os.environ, "HOME": str(home), "PYTHONPATH": SRC, "WATCHDOG_ENFORCE_ACCESS": "1",
               "NO_COLOR": "1", "TMPDIR": str(tmp_path / "tmp")}
        r = subprocess.run([sys.executable, "-m", "watchdog", "timeline"], cwd=vault, env=env,
                           capture_output=True, text=True, timeout=60)
        assert (r.returncode == 0) is expect_ok, (vault, r.stdout, r.stderr)
        assert (vault / "timeline.md").exists() is expect_ok
        if not expect_ok:
            assert "does not have permission" in r.stderr


def test_is_granted_and_write_allowed_in_process(home, monkeypatch, tmp_path):
    import tempfile
    from watchdog import access
    # The test's home sits inside the system temp folder, which is always writable; point temp
    # elsewhere so the home folder is judged on its own.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    monkeypatch.delenv("TMPDIR", raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("WATCHDOG_ACCESS_FILE", raising=False)
    assert access.is_granted(home / "granted" / "sub" / "x.md")
    assert not access.is_granted(home / "elsewhere")
    assert access.write_allowed(home / ".watchdog" / "chats" / "c.json")
    assert not access.write_allowed(home / ".watchdog" / "access.json")
    monkeypatch.delenv("WATCHDOG_ENFORCE_ACCESS", raising=False)
    access.check(home / "elsewhere" / "x")          # not enforced: no error
    monkeypatch.setenv("WATCHDOG_ENFORCE_ACCESS", "1")
    with pytest.raises(access.AccessDenied):
        access.check(home / "elsewhere" / "x")
