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
    (["projects", "--all"], ["list", "--all"]),
    (["projects", "new", "x"], ["projects", "new", "x"]),     # `new` is top-level, not a verb
    (["settings", "setup"], ["configure", "setup"]),           # likewise `setup`
    (["projects", "--help"], ["projects", "--help"]),
    (["projects", "bogus"], ["projects", "bogus"]),
    (["settings"], ["configure"]),
    (["settings", "extractor_model", "sonnet"], ["configure", "extractor_model", "sonnet"]),
    (["settings", "auth"], ["auth"]),
    (["settings", "skills"], ["show-skills"]),
    (["settings", "configure", "telemetry"], ["configure", "telemetry"]),
    (["review", "resolve", "lead:isolated:acme"], ["resolve", "lead:isolated:acme"]),
    (["review", "leads"], ["review", "leads"]),            # a kind, not a verb
    (["review"], ["review"]),
    (["research", "fetch", "links.txt"], ["fetch", "links.txt"]),
    (["research", "-q", "who?"], ["research", "-q", "who?"]),
    (["add", "--watch"], ["watch"]),
    (["add", "my-story", "--watch"], ["watch", "my-story"]),
    (["ask", "--context", "--project", "city", "--model", "opus"], ["context", "city", "--model", "opus"]),
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
    assert groups.pointer("leads") is None        # `leads` prints the sweep; it did not move
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
    for cmd in ("add", "ask", "review", "open", "research", "projects", "settings", "new", "setup",
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


@pytest.mark.parametrize("argv", [
    ["add", "file.pdf", "other", "--watch"],      # two names
    ["add", "--watch", "--retry"],               # a flag `watch` does not take
    ["ask", "who", "is", "--context", "she"],   # a question is not a project name
])
def test_flag_routing_refuses_arguments_the_target_cannot_take(argv):
    with pytest.raises(SystemExit, match="takes only an investigation name"):
        groups.rewrite(argv)


@pytest.mark.parametrize("argv, expected", [
    (["help", "skills"], "Usage:  watchdog show-skills"),
    (["help", "projects", "rename"], "Usage:  watchdog rename"),
    (["help", "review"], "Usage:  watchdog review"),
])
def test_help_topics(monkeypatch, capsys, argv, expected):
    _run(monkeypatch, *argv)
    assert expected in capsys.readouterr().out


def test_review_help_lists_its_verbs(monkeypatch, capsys):
    _run(monkeypatch, "review", "--help")
    out = capsys.readouterr().out
    for verb in groups.VERBS["review"]:
        assert f"watchdog review {verb}" in out


def test_pointer_prints_through_main(monkeypatch, capsys):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(cli, "CONFIG_FILE", type("P", (), {"exists": lambda self: True})())
    seen = []
    parser = cli.build_parser
    monkeypatch.setattr(cli, "build_parser", lambda: _patched(parser(), "list", seen))
    _run(monkeypatch, "list")
    assert "`watchdog list` is now" in capsys.readouterr().out and seen


@pytest.mark.parametrize("argv", [["add", "--watch", "--help"], ["ask", "--context", "-h"]])
def test_help_reaches_the_flag_routed_command(argv):
    assert groups.rewrite(argv)[1:] == ["--help"]


def test_context_model_equals_form_and_one_name_only():
    assert groups.rewrite(["ask", "--context", "--model=opus"]) == ["context", "--model=opus"]
    with pytest.raises(SystemExit, match="takes only an investigation name"):
        groups.rewrite(["ask", "--context", "city", "--project", "other"])


def test_abbreviated_flags_still_run_the_right_command(monkeypatch):
    import argparse as ap
    import watchdog.cmd.ask as ask_mod
    import watchdog.cmd.ingest as ing
    seen = []
    monkeypatch.setattr("watchdog.cmd.vault.cmd_watch", lambda a: seen.append(("watch", a.name)))
    monkeypatch.setattr("watchdog.cmd.ingest.cmd_context", lambda a: seen.append(("context", a.name, a.model)))
    a = cli.build_parser().parse_args(["add", "--watc", "city"])
    ing.cmd_add(a)
    a = cli.build_parser().parse_args(["ask", "--cont", "-p", "city"])
    ask_mod.cmd_ask(a)
    assert seen == [("watch", "city"), ("context", "city", "sonnet")]
    with pytest.raises(SystemExit, match="takes no question"):
        ask_mod.cmd_ask(ap.Namespace(context=True, question=["who"], project=None, model=None))
