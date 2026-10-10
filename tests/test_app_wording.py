"""Output shown in the app (Activity's job logs, the Add documents failure view) must not tell the
reader to type a command, press a key or pass a flag (D267, issue #729).

The app starts every subprocess with `WATCHDOG_APP=1`; `watchdog.appmode.hint(terminal, app)` is
where a message says both. These tests run representative messages with and without the flag, and
scan the source so a new hint written for the terminal alone is caught.
"""

import ast
import json
import re
import sys
from pathlib import Path

import pytest

from watchdog import appmode
from watchdog.gui import jobs

SRC = Path(__file__).resolve().parent.parent / "src" / "watchdog"
# What a terminal user would be told to type or press.
_COMMANDS = ("add|chew|dig|bark|ingest|unlock|requeue|reindex|setup|settings|research-fetch|research|"
             "review|projects|search|timeline|leads|usage|status|doctor|merge-entities|new|open|ask|"
             "resolve|unresolve|configure|auth|gui")
_FLAGS = ("force|verify|no-verify|wait|skip-briefing|estimate|estimate-all|limit|retry|chew-workers|"
          "chunk-workers|skill|watch")
APP_BAD = re.compile(rf"\bwatchdog ({_COMMANDS})\b|Ctrl[-+]|(?<![\w-])--({_FLAGS})\b")
TERMINAL = re.compile(APP_BAD.pattern + r"|\bPress ")


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("WATCHDOG_APP", "1")


@pytest.fixture
def terminal(monkeypatch):
    monkeypatch.delenv("WATCHDOG_APP", raising=False)


# ── the flag ───────────────────────────────────────────────────────────────────────────

def test_hint_picks_by_environment(monkeypatch):
    monkeypatch.delenv("WATCHDOG_APP", raising=False)
    assert appmode.hint("run watchdog dig", "add documents again") == "run watchdog dig"
    monkeypatch.setenv("WATCHDOG_APP", "1")
    assert appmode.hint("run watchdog dig", "add documents again") == "add documents again"
    assert appmode.hint("run watchdog dig", "") == ""
    assert appmode.hint("run watchdog dig", None) == ""


def test_jobs_mark_their_subprocesses_as_the_app(monkeypatch):
    seen = {}

    class _Proc:
        stdout = stderr = None
        pid = 1

    def fake_popen(argv, **kw):
        seen.update(kw["env"])
        raise OSError("stop here")

    monkeypatch.setattr(jobs.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(jobs, "require_engine", lambda args: None)
    monkeypatch.setattr(jobs, "_require_granted_cwd", lambda vault: None)
    try:
        jobs.MANAGER.start(None, ["reindex"], "x", "reindex")
    except Exception:
        pass
    assert seen.get("WATCHDOG_APP") == "1"

    seen.clear()

    def fake_run(argv, **kw):
        seen.update(kw["env"])
        raise OSError("stop here")

    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    try:
        jobs.run_action(None, ["unlock"], 5)
    except Exception:
        pass
    assert seen.get("WATCHDOG_APP") == "1"
    assert "WATCHDOG_PROGRESS" not in seen


# ── representative messages, both ways ─────────────────────────────────────────────────

def _quarantine(n=2):
    from watchdog.cmd.ingest import _quarantine_notice
    return _quarantine_notice(n)


def test_quarantine_notice(terminal, monkeypatch):
    assert "watchdog requeue" in _quarantine()
    monkeypatch.setenv("WATCHDOG_APP", "1")
    text = _quarantine()
    assert not APP_BAD.search(text)
    assert "Requeue failed documents" in text


def _vault_with_lock(tmp_path, monkeypatch):
    from watchdog.vault_paths import preprocessing_lock
    vault = tmp_path / "v"
    (vault / ".watchdog").mkdir(parents=True)
    lock = preprocessing_lock(vault)
    lock.parent.mkdir(parents=True, exist_ok=True)
    import os
    import time
    lock.write_text(f"started_at: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\npid: {os.getpid()}\n")
    monkeypatch.chdir(vault)
    return vault


def test_unlock_recent_lock_wording(tmp_path, monkeypatch, capsys):
    from argparse import Namespace
    from watchdog.cmd.setup import cmd_unlock
    _vault_with_lock(tmp_path, monkeypatch)
    monkeypatch.delenv("WATCHDOG_APP", raising=False)
    cmd_unlock(Namespace(project=None, force=False))
    assert "watchdog unlock --force" in capsys.readouterr().out

    _vault_with_lock(tmp_path / "again", monkeypatch)
    monkeypatch.setenv("WATCHDOG_APP", "1")
    cmd_unlock(Namespace(project=None, force=False))
    out = capsys.readouterr().out
    assert "Lock is recent" in out
    assert not APP_BAD.search(out)
    assert "Force" in out


def test_second_run_refusals_name_the_app_remedy(tmp_path, monkeypatch):
    from watchdog.cmd.base import _check_vault_locks
    vault = _vault_with_lock(tmp_path, monkeypatch)
    monkeypatch.delenv("WATCHDOG_APP", raising=False)
    with pytest.raises(SystemExit) as e:
        _check_vault_locks(vault, "v")
    assert "watchdog unlock" in str(e.value)
    monkeypatch.setenv("WATCHDOG_APP", "1")
    with pytest.raises(SystemExit) as e:
        _check_vault_locks(vault, "v")
    assert not APP_BAD.search(str(e.value))
    assert "Release a stuck lock" in str(e.value)


def test_pending_research_warning(tmp_path, monkeypatch, capsys):
    from watchdog.cmd import base
    from watchdog.pipeline import research
    monkeypatch.setattr(research, "pending_count", lambda vault: 3)
    monkeypatch.delenv("WATCHDOG_APP", raising=False)
    base._warn_pending_research(tmp_path)
    assert "watchdog research-fetch" in capsys.readouterr().out
    monkeypatch.setenv("WATCHDOG_APP", "1")
    base._warn_pending_research(tmp_path)
    out = capsys.readouterr().out
    assert "3 research URLs" in out
    assert not APP_BAD.search(out)
    assert "Web research" in out


def test_model_errors_do_not_name_commands_under_the_app(app):
    from watchdog import model_client
    from watchdog.pipeline import ingest_setup
    assert not APP_BAD.search(ingest_setup._hint("if stale, run: watchdog unlock", "if it is stale, use Activity"))
    assert model_client._hint("run `watchdog settings auth`", "add one in Settings") == "add one in Settings"


# ── expected failures: one sentence, not a traceback ───────────────────────────────────

def _registry(vault: Path):
    reg = vault / ".watchdog" / "registry"
    reg.mkdir(parents=True)
    (reg / "documents.json").write_text(json.dumps({"abc": {"filename": "a.pdf"}}))
    (vault / ".embeddings").mkdir()
    (vault / ".embeddings" / "keep").write_text("x")


def test_reindex_without_search_tools_is_one_sentence_and_changes_nothing(tmp_path, monkeypatch, app):
    from argparse import Namespace
    from watchdog.cmd import reindex
    from watchdog.pipeline import embed
    vault = tmp_path / "v"
    _registry(vault)
    monkeypatch.setattr(reindex, "_resolve_vault", lambda p: ("v", {"name": "V"}, vault))
    monkeypatch.setattr(embed, "embedder_available", lambda: False)
    with pytest.raises(SystemExit) as e:
        reindex.cmd_reindex(Namespace(project=None))
    msg = str(e.value)
    assert "\n" not in msg.strip() and "Traceback" not in msg
    assert not APP_BAD.search(msg)
    assert (vault / ".embeddings" / "keep").exists()


def test_a_missing_package_under_the_app_prints_a_sentence(monkeypatch, capsys, app):
    from watchdog import cli

    def boom():
        raise ModuleNotFoundError("No module named 'fastembed'", name="fastembed")

    monkeypatch.setattr(cli, "_main", boom)
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "fastembed" in err and "Traceback" not in err and len(err.strip().splitlines()) == 1


def test_a_missing_package_at_a_terminal_still_raises(monkeypatch, terminal):
    from watchdog import cli

    def boom():
        raise ModuleNotFoundError("No module named 'fastembed'", name="fastembed")

    monkeypatch.setattr(cli, "_main", boom)
    with pytest.raises(ModuleNotFoundError):
        cli.main()


def test_the_command_line_runs_under_the_app_without_terminal_wording(tmp_path):
    """A real subprocess, as the app starts it: the not-set-up message names no command."""
    import os
    import subprocess
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "WATCHDOG_APP": "1", "WATCHDOG_HOME": str(home), "HOME": str(home),
           "PYTHONPATH": str(SRC.parent), "NO_COLOR": "1"}
    done = subprocess.run([sys.executable, "-m", "watchdog", "reindex"], env=env, cwd=tmp_path,
                          capture_output=True, text=True, timeout=60)
    text = done.stdout + done.stderr
    assert done.returncode != 0
    assert "Traceback" not in text
    assert not APP_BAD.search(text)


# ── the source scan ────────────────────────────────────────────────────────────────────

# Files whose every string is app-visible, and (file, function) pairs for the shared modules where
# only a few functions run under the app. Anything outside these lists is CLI-only or writes a
# file the reader opens (see the report in D-less issue #729) and is not scanned.
WHOLE_FILES = [
    "cmd/ingest.py", "cmd/reindex.py", "cmd/merge_entities.py", "cmd/review.py",
    "pipeline/orchestrate.py", "pipeline/preprocess_batch.py", "pipeline/ingest_setup.py",
    "pipeline/fulltext.py", "model_client.py",
]
FUNCTIONS = {
    "cmd/base.py": {"_warn_pending_research", "_registered_project", "_find_project",
                    "_check_vault_locks", "load_projects", "load_config"},
    "cmd/setup.py": {"cmd_unlock"},
    "cmd/research.py": {"_report_deposits"},
    "cmd/vault.py": {"cmd_register", "cmd_archive", "cmd_new", "cmd_move", "cmd_watch"},
}
# Strings that legitimately name the terminal even in a scanned file: argparse help, the
# deprecated-command banners, and wording only a terminal run can reach.
ALLOWED = [
    "watchdog add --watch [name]",              # argument error from the retired CLI form
    "Run:  {next_cmd}",                         # guarded by `not under_app()`
    "watchdog open",                            # Obsidian helper, not run by the app
    "watchdog review unresolve",                # the interactive walk, only reached at a terminal
    "add --watch",                              # a command name used only in a terminal-only error
]


def _is_hint_call(node):
    f = getattr(node, "func", None)
    return isinstance(f, ast.Name) and f.id in ("_hint", "hint")


def _scan(path: Path, funcs: set | None):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parents = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            parents[c] = n
    docstrings = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body:
            first = n.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(first.value)
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if node in docstrings or not TERMINAL.search(node.value):
            continue
        chain, p = [], node
        while p in parents:
            p = parents[p]
            chain.append(p)
        if funcs is not None:
            names = {a.name for a in chain if isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef))}
            if not names & funcs:
                continue
        # Terminal side of a hint(), a guard on under_app(), argparse text, or a comment-like use.
        exempt = False
        for child, anc in zip([node] + chain, chain):
            if isinstance(anc, ast.Call) and _is_hint_call(anc):
                if anc.args and child is anc.args[0] or any(
                        isinstance(k, ast.keyword) and k.arg == "terminal" and k.value is child
                        for k in anc.keywords):
                    exempt = True
                elif child in anc.args[1:]:
                    exempt = False
                    break
            if isinstance(anc, ast.Call) and getattr(anc.func, "attr", "") in (
                    "add_argument", "add_parser", "add_subparsers", "ArgumentParser"):
                exempt = True
            if isinstance(anc, ast.If) and "under_app" in ast.dump(anc.test):
                exempt = True
            if isinstance(anc, ast.keyword) and anc.arg in ("help", "description", "epilog"):
                exempt = True
            # Argument lists handed to a subprocess are not shown to the reader.
            if isinstance(anc, ast.List):
                exempt = True
            if isinstance(anc, ast.arguments):
                exempt = True
            # A constant naming the command a "run it again" message offers a terminal reader.
            if isinstance(anc, (ast.Assign, ast.AnnAssign)):
                tgt = anc.targets[0] if isinstance(anc, ast.Assign) else anc.target
                if getattr(tgt, "id", getattr(tgt, "attr", "")) in ("pipeline_hint", "resume_hint", "next_cmd", "hint", "force_cmd"):
                    exempt = True
        # A JoinedStr piece lives inside the f-string; the f-string's parent chain is the same.
        if exempt or any(a in node.value for a in ALLOWED):
            continue
        # Application side of a hint(): flag if it names a command.
        bad.append(f"{path.relative_to(SRC)}:{node.lineno}: {node.value.strip()[:80]!r}")
    return bad


def _app_side_violations(path: Path):
    """The wording a hint() gives the app must itself be free of commands, keys and flags."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_hint_call(node):
            app_args = list(node.args[1:2]) + [k.value for k in node.keywords if k.arg == "app"]
            for a in app_args:
                for c in ast.walk(a):
                    if isinstance(c, ast.Constant) and isinstance(c.value, str) and APP_BAD.search(c.value):
                        bad.append(f"{path.relative_to(SRC)}:{c.lineno}: {c.value.strip()[:80]!r}")
    return bad


def test_no_terminal_wording_outside_a_hint_in_app_visible_code():
    bad = []
    for rel in WHOLE_FILES:
        bad += _scan(SRC / rel, None)
    for rel, funcs in FUNCTIONS.items():
        bad += _scan(SRC / rel, funcs)
    assert not bad, "terminal wording outside hint(terminal=…, app=…):\n" + "\n".join(bad)


def test_the_app_side_of_every_hint_names_no_command_key_or_flag():
    bad = []
    for path in SRC.rglob("*.py"):
        if "appmode" in path.name:
            continue
        bad += _app_side_violations(path)
    assert not bad, "app wording that tells the reader to type or press:\n" + "\n".join(bad)
