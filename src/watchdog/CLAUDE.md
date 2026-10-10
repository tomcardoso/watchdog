# CLI style guide

All terminal output in `cli.py` follows a consistent visual language. The colour constants are defined at the top of the file — use them, never raw ANSI codes.

The constants are gated on terminal detection (`cmd.base._color_enabled`, #499): each one is a real escape code when stdout is a terminal, and `""` otherwise (`NO_COLOR` forces them off; `FORCE_COLOR` is deliberately ignored — see D174). Code must never assume a constant carries escape bytes, and must never measure display width with `len()` of a string built from these constants — `len(f"{_BOLD}{name}{_RESET}")` is wrong whether or not colour is active in the current run, since it counts the escape bytes as visible columns when they are present. Compute width from the plain text instead.

## Colour semantics

| Constant | Use for |
|----------|---------|
| `_BOLD` | Project names, important counts, section headers |
| `_DIM` | Secondary metadata: dates, slugs, path labels, quiet prompts |
| `_CYAN` | Actionable items: file paths, commands the user should type, directory names like `incoming/` |
| `_GREEN` | Success states (`Created:`) |
| `_YELLOW` | Warnings (pending files, things that need attention) |
| `_RESET` | Always close every coloured span |

## Layout conventions

- **Indent everything 2 spaces** — all output lines start with `"  "`. The banner and list headers set this pattern; every command should match it.
- **Bold name, dim slug** — when showing a project, display its human name in bold and its slug in dim on the same line: `  **My Project**  [dim]my-project[/dim]`.
- **Cyan for paths, never dim** — file system paths and `watchdog …` commands the user should run are always `_CYAN`, not `_DIM`. Dim is for decorative/secondary text only.
- **Section headers: bold, no trailing colon** — e.g. `  **Documents by type**` not `Documents by type:`. The colon was dropped in the consistency pass.
- **Dim labels, normal counts** — in type-breakdown tables, the label is `_DIM`, the count is unstyled (so it reads at normal brightness).
- **No trailing colons on "Pending in" lines** — format is `Pending in {_CYAN}incoming/{_RESET}  <label>`.

## Adding a new command

1. Print a blank line before the first content line and after the last, matching the spacing in `cmd_status`.
2. Use `_find_project` for any command that takes a project name — it handles prefix matching and exits cleanly.
3. Never call `print(f"Error: …")` and continue — use `sys.exit(f"Error: …")`.
4. If the command produces a success confirmation, use `_GREEN` for the label and `_BOLD` for the key value.
5. Register the command's arguments only on its argparse subparser in `build_parser()`. `watchdog <cmd> --help` renders them from the parser; `_CMD_HELP` in `cmd/base.py` holds only an optional description override and notes, never flags.
6. Inside a vault, a lone positional argument is the command's own value (a query, a new name), not a project prefix — only an exact slug names another project.
7. Decide where it appears (D254). `watchdog --help` shows only the main commands. A new command either becomes a verb under one of them — add it to `GROUPS`, `VERBS` or `FLAGS` in `cmd/groups.py`, plus `_GROUP_HELP` for a group — or goes in `MAINTENANCE` there. It keeps its own subparser either way: the grouped form is rewritten to it before argparse runs. Printed hints name the grouped form (`watchdog projects rename`), while the app's jobs keep the original name, which is the stable interface for scripts. Ask Claude sessions never run a command; give them a tool in `watchdog/session_tools.py` instead (D299).

## Adding a new CLI alias

Add the alias → canonical mapping to `_ALIASES` in `cmd/base.py`. Aliases are resolved before argparse sees `sys.argv`, so they are invisible to `--help`. Add a parametrized test case to the `test_aliases_remap_argv` test in `tests/test_cli.py`.
