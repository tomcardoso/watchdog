"""The grouped command surface (D254): routing, pointers, help, and `open`."""

import sys

import pytest

import watchdog.cli as cli
import watchdog.cmd.groups as groups


@pytest.mark.parametrize("argv, expected", [
    ([], []),
    (["projects"], ["list"]),
    (["projects", "rename", "old", "new"], ["rename", "old", "new"]),
    (["projects", "list", "--all"], ["list", "--all"]),
    (["projects", "--help"], ["projects", "--help"]),
    (["projects", "bogus"], ["projects", "bogus"]),
    (["settings"], ["configure"]),
    (["settings", "extractor_model", "sonnet"], ["configure", "extractor_model", "sonnet"]),
    (["settings", "auth"], ["auth"]),
    (["settings", "skills"], ["show-skills"]),
    (["settings", "configure", "telemetry"], ["configure", "telemetry"]),
    (["review", "resolve", "lead:isolated:acme"], ["resolve", "lead:isolated:acme"]),
    (["review", "add-contradiction", "--entity", "x"], ["contradiction-add", "--entity", "x"]),
    (["review", "leads"], ["review", "leads"]),            # a kind, not a verb
    (["review"], ["review"]),
    (["research", "fetch", "links.txt"], ["fetch", "links.txt"]),
    (["research", "-q", "who?"], ["research", "-q", "who?"]),
    (["add", "--watch"], ["watch"]),
    (["add", "my-story", "--watch"], ["watch", "my-story"]),
    (["add", "a.pdf"], ["add", "a.pdf"]),
    (["ask", "--context"], ["context"]),
    (["ask", "who", "is", "she?"], ["ask", "who", "is", "she?"]),
    (["rename", "x"], ["rename", "x"]),                    # original names pass through
    (["chew"], ["chew"]),
])
def test_rewrite(argv, expected):
    assert groups.rewrite(argv) == expected


def test_every_routed_command_exists():
    parser_cmds = set(cli._subparsers(cli.build_parser()))
    routed = ({c for g in groups.GROUPS.values() for c in g.values()}
              | {c for v in groups.VERBS.values() for c in v.values()}
              | set(groups.FLAGS.values()) | set(groups.MOVED) | {c for c, _ in groups.MAINTENANCE})
    assert routed <= parser_cmds, routed - parser_cmds


def test_pointer_only_at_a_terminal(monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert "watchdog projects rename" in groups.pointer("rename")
    assert "watchdog settings" in groups.pointer("configure")
    assert "watchdog review leads" in groups.pointer("leads")
    assert "watchdog open" in groups.pointer("obsidian")
    assert groups.pointer("chew") is None and groups.pointer("search") is None
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert groups.pointer("rename") is None          # skills and scripts see clean output


def _run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["watchdog", *argv])
    cli.main()


def test_banner_shows_the_grouped_surface(monkeypatch, capsys):
    _run(monkeypatch, "--help")
    out = capsys.readouterr().out
    for cmd in ("add", "ask", "search", "review", "open", "research", "projects", "settings", "new", "setup",
                "watchdog help maintenance"):
        assert cmd in out
    for old in ("merge-entities", "refresh-skills", "unarchive", "contradiction-add"):
        assert old not in out


def test_help_maintenance_and_group_help(monkeypatch, capsys):
    _run(monkeypatch, "help", "maintenance")
    assert "reindex" in capsys.readouterr().out
    _run(monkeypatch, "projects", "--help")
    out = capsys.readouterr().out
    assert "unarchive" in out and "watchdog projects" in out
    _run(monkeypatch, "help", "settings")
    assert "refresh-skills" in capsys.readouterr().out


def test_grouped_command_help_is_the_original_commands(monkeypatch, capsys):
    _run(monkeypatch, "projects", "rename", "--help")
    assert "Usage:  watchdog rename" in capsys.readouterr().out


def test_grouped_form_runs_the_original_command(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "CONFIG_FILE", type("P", (), {"exists": lambda self: True})())
    monkeypatch.setattr(cli, "cmd_list", lambda a: seen.append(("list", a.all)))
    monkeypatch.setattr(sys, "argv", ["watchdog", "projects", "list", "--all"])
    parser = cli.build_parser
    monkeypatch.setattr(cli, "build_parser", lambda: _patched(parser(), "list", seen))
    cli.main()
    assert seen == [("list", True)]


def _patched(parser, cmd, seen):
    cli._subparsers(parser)[cmd].set_defaults(func=lambda a: seen.append((cmd, a.all)))
    return parser


def test_unknown_verb_explains_the_group(monkeypatch, capsys):
    monkeypatch.setattr(cli, "CONFIG_FILE", type("P", (), {"exists": lambda self: True})())
    with pytest.raises(SystemExit, match="unknown command 'bogus'"):
        _run(monkeypatch, "projects", "bogus")
    assert "watchdog projects" in capsys.readouterr().out
