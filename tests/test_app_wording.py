"""Watchdog is used only through the desktop app (#729, D300): nothing it shows may tell the reader
to type a command, press a key or pass a flag, and no module may depend on the command line that
was removed.

The source scan reads every string in the package (docstrings aside) and the text the package
ships into an investigation (its Claude commands, templates and prompts), so a message written for
a terminal is caught wherever it is added.
"""

import ast
import json
import re
from pathlib import Path

import pytest

from watchdog.ops import CollectingReporter, using

SRC = Path(__file__).resolve().parent.parent / "src" / "watchdog"
REPO = SRC.parent.parent
# What a terminal user would be told to type or press.
_COMMANDS = ("add|chew|dig|bark|ingest|unlock|requeue|reindex|setup|settings|research-fetch|research|"
             "review|projects|search|timeline|leads|usage|status|doctor|merge-entities|new|open|ask|"
             "resolve|unresolve|configure|auth|gui|watch|context|export|fetch|about|list")
_FLAGS = ("force|verify|no-verify|wait|skip-briefing|estimate|estimate-all|limit|retry|chew-workers|"
          "chunk-workers|skill|watch|project|sync|purge")
APP_BAD = re.compile(rf"\bwatchdog ({_COMMANDS})\b|Ctrl[-+]|(?<![\w-])--({_FLAGS})\b|\bPress (Enter|Ctrl|any key|q)\b")


# ── the command line is gone ────────────────────────────────────────────────────────────

def test_no_command_line_modules_remain():
    # Source files, not the folder: a stale __pycache__ can outlive a deleted package.
    assert not (SRC / "cli.py").exists() and not list((SRC / "cmd").glob("*.py"))
    assert not (SRC / "__main__.py").exists()
    for gone in ("terminal.py", "interactive.py", "appmode.py", "setup_cmd.py", "links.py"):
        assert not (SRC / gone).exists(), gone


def test_no_module_imports_the_command_line():
    stale = re.compile(r"\bwatchdog\.(cli|cmd|appmode|terminal|interactive|setup_cmd|links)\b"
                       r"|from watchdog import [^\n]*\b(cli|cmd|appmode|terminal|interactive|setup_cmd|links)\b")
    hits = []
    for root in (SRC, REPO / "benchmarks", REPO / "tests"):
        for path in root.rglob("*.py"):
            if path.name == Path(__file__).name:
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if stale.search(line):
                    hits.append(f"{path.relative_to(REPO)}:{n}: {line.strip()}")
    assert not hits, "\n".join(hits)


def test_the_package_declares_no_console_script():
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert "[project.scripts]" not in text and "argcomplete" not in text


# ── the source scan ─────────────────────────────────────────────────────────────────────

def _strings(path: Path):
    """Every string constant in a module except docstrings, lists (argument vectors) and the
    arguments of `add_argument` (a pre-processing subprocess's own internal options)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body:
            first = n.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                skip.add(first.value)
        if isinstance(n, ast.List) or (isinstance(n, ast.Call)
                                       and getattr(n.func, "attr", "") == "add_argument"):
            skip.update(c for c in ast.walk(n) if isinstance(c, ast.Constant))
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n not in skip:
            yield n.lineno, n.value


def test_no_string_in_the_package_tells_the_reader_to_type_a_command():
    bad = [f"{p.relative_to(SRC)}:{line}: {text.strip()[:80]!r}"
           for p in SRC.rglob("*.py") for line, text in _strings(p) if APP_BAD.search(text)]
    assert not bad, "\n".join(bad)


def test_no_shipped_text_tells_the_reader_to_type_a_command():
    bad = []
    for folder in ("skills", "templates", "prompts"):
        for p in (SRC / folder).rglob("*.md"):
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if APP_BAD.search(line):
                    bad.append(f"{p.relative_to(SRC)}:{n}: {line.strip()[:80]}")
    assert not bad, "\n".join(bad)


def test_no_app_screen_tells_the_reader_to_type_a_command():
    """The renderer's own strings (comments aside)."""
    bad = []
    for p in (REPO / "gui" / "src").rglob("*"):
        if p.suffix not in (".ts", ".tsx") or not p.is_file():
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("//")[0] if "://" not in line else line
            if code.strip().startswith(("*", "/*")):
                continue
            if re.search(rf"\bwatchdog ({_COMMANDS})\b", code):
                bad.append(f"{p.relative_to(REPO)}:{n}: {line.strip()[:80]}")
    assert not bad, "\n".join(bad)


# ── representative messages ─────────────────────────────────────────────────────────────

def test_quarantine_notice_names_the_app_control():
    from watchdog.ops.ingest import _quarantine_notice
    text = _quarantine_notice(2)
    assert not APP_BAD.search(text) and "Requeue failed documents" in text


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


def test_second_run_refusals_say_to_wait(tmp_path, monkeypatch):
    from watchdog.projects import check_locks
    vault = _vault_with_lock(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as e:
        check_locks(vault)
    assert not APP_BAD.search(str(e.value))
    assert "Try again when it has finished" in str(e.value)


def test_no_app_text_points_to_a_manual_unlock():
    """D293: locks clear themselves, and the app has no "Release a stuck lock" control any more.
    No Python message the app can show, and no renderer source, may send the reader to one."""
    gui = SRC.parent.parent / "gui" / "src"
    stale = re.compile(r"Release a stuck lock|Force unlock|Unlock…|\['unlock'")
    hits = [str(p) for root in (SRC, gui) for p in root.rglob("*")
            if p.suffix in (".py", ".ts", ".tsx", ".md") and p.is_file()
            and stale.search(p.read_text(encoding="utf-8", errors="replace"))]
    assert not hits, hits


def test_pending_research_warning(tmp_path, monkeypatch):
    from watchdog.ops import ingest
    from watchdog.pipeline import research
    monkeypatch.setattr(research, "pending_count", lambda vault: 3)
    rep = CollectingReporter()
    with using(rep):
        ingest._warn_pending_research(tmp_path)
    assert "3 research links" in rep.text and "Web research" in rep.text
    assert not APP_BAD.search(rep.text)


# ── expected failures: one sentence, not a traceback ───────────────────────────────────

def _registry(vault: Path):
    reg = vault / ".watchdog" / "registry"
    reg.mkdir(parents=True)
    (reg / "documents.json").write_text(json.dumps({"abc": {"filename": "a.pdf"}}))
    (vault / ".embeddings").mkdir()
    (vault / ".embeddings" / "keep").write_text("x")


def test_reindex_without_search_tools_is_one_sentence_and_changes_nothing(tmp_path, monkeypatch):
    from watchdog.ops import reindex
    from watchdog.pipeline import embed
    vault = tmp_path / "v"
    _registry(vault)
    monkeypatch.setattr(embed, "embedder_available", lambda: False)
    with pytest.raises(SystemExit) as e:
        reindex.reindex(CollectingReporter(), vault)
    msg = str(e.value)
    assert "\n" not in msg.strip() and "Traceback" not in msg
    assert not APP_BAD.search(msg)
    assert (vault / ".embeddings" / "keep").exists()
