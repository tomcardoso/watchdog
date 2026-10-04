"""`watchdog ask` opens an interactive Claude Code session on the vault (D253)."""

import argparse

import pytest

import watchdog.cmd.ask as ask


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "probe"
    (v / ".watchdog").mkdir(parents=True)
    (v / ".claude" / "commands").mkdir(parents=True)
    (v / ".claude" / "commands" / "watchdog-query.md").write_text("x")
    monkeypatch.chdir(v)
    monkeypatch.setattr(ask, "load_projects", lambda: {})
    return v


@pytest.fixture
def launched(monkeypatch):
    calls = []
    monkeypatch.setattr(ask, "_launch_claude",
                        lambda vault, prompt=None, model=None: calls.append((vault, prompt, model)))
    return calls


def _args(question=(), project=None, model=None):
    return argparse.Namespace(question=list(question), project=project, model=model)


@pytest.mark.parametrize("question, expected", [
    (None, None), ("", None), ("   ", None),
    ("who signed it?", "/watchdog-query who signed it?"),
    ("who\nsigned  it?", "/watchdog-query who signed it?"),
])
def test_prompt_for(question, expected):
    assert ask.prompt_for(question) == expected


def test_ask_with_a_question_starts_with_the_query_skill(vault, launched, capsys):
    ask.cmd_ask(_args(["who", "signed", "the", "loan?"], model="opus"))
    assert launched == [(vault, "/watchdog-query who signed the loan?", "opus")]
    out = capsys.readouterr().out
    assert "Opening Claude Code in probe" in out and "refresh-skills" not in out


def test_ask_without_a_question_opens_an_empty_session(vault, launched):
    ask.cmd_ask(_args())
    assert launched == [(vault, None, None)]


def test_ask_warns_when_the_query_skill_is_missing(vault, launched, capsys):
    (vault / ".claude" / "commands" / "watchdog-query.md").unlink()
    ask.cmd_ask(_args())
    assert "watchdog settings refresh-skills" in capsys.readouterr().out
    assert launched


def test_ask_by_project_name(tmp_path, monkeypatch, launched):
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ask, "_find_project", lambda name: ("city", {"path": str(other), "name": "City"}))
    ask.cmd_ask(_args(["q"], project="city"))
    assert launched[0][:2] == (other, "/watchdog-query q")


def test_ask_outside_a_vault_exits(tmp_path, monkeypatch, launched):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="not inside a Watchdog project"):
        ask.cmd_ask(_args())
    assert not launched


def test_ask_parses_from_the_cli(monkeypatch):
    from watchdog.cli import build_parser
    a = build_parser().parse_args(["ask", "who", "is", "Jane?", "--model", "haiku"])
    assert (a.question, a.model, a.project, a.func) == (["who", "is", "Jane?"], "haiku", None, ask.cmd_ask)
