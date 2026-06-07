"""watchdog — investigative journalism document intelligence CLI"""

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

WATCHDOG_HOME = Path.home() / ".watchdog"
PROJECTS_FILE = WATCHDOG_HOME / "projects.json"
CONFIG_FILE   = WATCHDOG_HOME / "config.json"

_ALIASES = {
    "init":    "new",
    "create":  "new",
    "ls":      "list",
    "info":    "status",
    "inspect": "status",
    "cd":      "open",
    "version": "about",
}

_PIPELINE_COMMANDS = {
    "preprocess":       ("watchdog.pipeline.preprocess",       "watchdog-preprocess"),
    "preprocess-batch": ("watchdog.pipeline.preprocess_batch", "watchdog-preprocess-batch"),
    "near-dup":         ("watchdog.pipeline.near_dup",         "watchdog-near-dup"),
    "arrows":           ("watchdog.pipeline.arrows_parser",    "watchdog-arrows"),
    "batch-get":        ("watchdog.pipeline.batch_get",        "watchdog-batch-get"),
    "write-vault":      ("watchdog.pipeline.write_vault",      "watchdog-write-vault"),
    "write-entity":     ("watchdog.pipeline.write_entity",     "watchdog-write-entity"),
}

_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_CYAN   = "\033[0;36m"
_YELLOW = "\033[0;33m"
_GREEN  = "\033[0;32m"
_RESET  = "\033[0m"


def _projects_dir() -> Path:
    if CONFIG_FILE.exists():
        config = json.loads(CONFIG_FILE.read_text())
        return Path(config["projects_dir"]).expanduser()
    return Path.home() / "Investigations"


def slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


def load_projects() -> dict:
    if not PROJECTS_FILE.exists():
        return {}
    with open(PROJECTS_FILE) as f:
        return json.load(f)


def save_projects(projects: dict) -> None:
    WATCHDOG_HOME.mkdir(parents=True, exist_ok=True)
    with open(PROJECTS_FILE, "w") as f:
        json.dump(projects, f, indent=2)
        f.write("\n")


def _fmt_date(iso: str) -> str:
    try:
        return iso[:10]
    except Exception:
        return "—"


def _load_registry(vault: Path) -> dict | None:
    reg = vault / ".watchdog" / "Registry" / "registry.json"
    if not reg.exists():
        return None
    try:
        return json.loads(reg.read_text())
    except Exception:
        return None


def _count_incoming(vault: Path) -> int:
    incoming = vault / "_INCOMING"
    if not incoming.exists():
        return 0
    count = 0
    for root, dirs, files in os.walk(incoming):
        if "_FAILED" in root:
            dirs.clear()
            continue
        count += sum(1 for f in files if not f.startswith(".") and not f.endswith(".yml"))
    return count


def _find_project(name: str) -> tuple[str, dict]:
    projects = load_projects()
    slug = slugify(name)
    if slug not in projects:
        matches = [k for k in projects if k.startswith(slug)]
        if len(matches) == 1:
            slug = matches[0]
        elif len(matches) > 1:
            sys.exit(f"Ambiguous name — matches: {', '.join(sorted(matches))}")
        else:
            sys.exit(f"Project not found: {name}\nRun 'watchdog list' to see all projects.")
    return slug, projects[slug]


def cmd_new(args) -> None:
    name = args.name
    slug = slugify(name)

    if not slug:
        sys.exit("Error: project name is invalid.")

    parent = Path(args.dir).expanduser().resolve() if args.dir else _projects_dir()
    vault = parent / slug

    if vault.exists():
        sys.exit(f"Error: {vault} already exists.")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today = datetime.now().strftime("%Y-%m-%d")

    for d in [
        "_INCOMING",
        "_CONTEXT",
        "morgue",
        ".watchdog/Registry",
        "entities/person",
        "entities/company",
        "entities/address",
        "documents",
        "briefings",
        "wiki",
        "queries",
        ".obsidian/plugins",
        ".obsidian/snippets",
        ".claude",
    ]:
        (vault / d).mkdir(parents=True)

    (vault / ".watchdog" / "Registry" / "documents.json").write_text("{}\n")
    (vault / ".watchdog" / "Registry" / "entities.json").write_text("{}\n")
    (vault / ".watchdog" / "Registry" / "registry.json").write_text(
        json.dumps(
            {"schema_version": "1", "created_at": now, "last_updated": now,
             "document_count": 0, "entity_count": 0},
            indent=2,
        ) + "\n"
    )
    (vault / ".watchdog" / "Registry" / "ingest.log").write_text("")

    (vault / ".obsidian" / "app.json").write_text(
        json.dumps(
            {"userIgnoreFilters": [".watchdog"], "showInlineTitle": False},
            indent=2,
        ) + "\n"
    )

    # rgb values are 24-bit packed integers: (R << 16) | (G << 8) | B
    (vault / ".obsidian" / "graph.json").write_text(
        json.dumps(
            {
                "colorGroups": [
                    {"query": "path:entities/person",  "color": {"a": 1, "rgb": 4886745}},   # #4A90D9 blue
                    {"query": "path:entities/company", "color": {"a": 1, "rgb": 5999451}},   # #5BA95B green
                    {"query": "path:entities/address", "color": {"a": 1, "rgb": 15238714}},  # #E8863A orange
                    {"query": "path:documents",        "color": {"a": 1, "rgb": 9145227}},   # #8B8B8B grey
                ]
            },
            indent=2,
        ) + "\n"
    )

    (vault / "hot.md").write_text(
        "# Hot cache\n\n"
        "*No sessions yet. This file is updated after every ingest.*\n"
    )

    (vault / "log.md").write_text(
        "# Ingest log\n\n"
        "*Append-only. Updated automatically after every ingest.*\n\n"
        "---\n"
    )

    (vault / "context.md").write_text(
        f"# {name} — context\n\n"
        "> Fill this in before your first ingest. The more specific you are, the more "
        "targeted Watchdog's briefings, leads, and analysis will be. Update it any time "
        "your understanding of the story evolves.\n\n"
        "## What I'm investigating\n\n"
        "<!-- One paragraph. What is the story? What pattern, question, or wrongdoing are you pursuing? -->\n\n"
        "## Key questions I'm trying to answer\n\n"
        "- \n\n"
        "## Entities I already know are relevant\n\n"
        "<!-- People, companies, addresses you know matter to this story. -->\n\n"
        "- \n\n"
        "## Documents I'm expecting or looking for\n\n"
        "<!-- Types of records that would be useful but you don't have yet. -->\n\n"
        "- \n\n"
        "## What I don't yet understand\n\n"
        "<!-- Gaps in your current knowledge of the subject. -->\n\n"
        "- \n"
    )

    (vault / "index.md").write_text(
        f"# {name}\n\n"
        f"*Watchdog investigation vault — created {today}.*\n\n"
        "## Recent documents\n\n"
        "```dataview\n"
        'TABLE document_type, date_of_document\n'
        'FROM "documents"\n'
        "SORT date_ingested DESC\n"
        "LIMIT 10\n"
        "```\n\n"
        "## Entities\n\n"
        "```dataview\n"
        'TABLE type, aliases\n'
        'FROM "entities"\n'
        "SORT date_last_updated DESC\n"
        "```\n"
    )

    (vault / "CLAUDE.md").write_text(
        f"# {name} — Watchdog\n\n"
        "At the start of every session: (1) read `hot.md` for a summary of recent activity and open questions; "
        "(2) read `context.md` to understand what this investigation is about; "
        "(3) check `_INCOMING/` for unprocessed files — if any are present, run `/watchdog-ingest` before doing anything else.\n\n"
        "## Vault layout\n\n"
        "| Path | Purpose |\n"
        "|------|---------|\n"
        "| `_INCOMING/` | Drop zone — drag files here to ingest |\n"
        "| `_INCOMING/_FAILED/` | Created on failure — files that could not be processed |\n"
        "| `_CONTEXT/` | Background material (prior stories, notes) — run `/watchdog-context` to seed context.md |\n"
        "| `morgue/` | Original files after successful ingest |\n"
        "| `.watchdog/Registry/` | Internal state — do not edit manually |\n"
        "| `entities/` | One note per real-world entity |\n"
        "| `documents/` | One note per ingested document |\n"
        "| `briefings/` | Post-ingest briefing notes |\n"
        "| `wiki/` | Investigation thread pages |\n"
        "| `hot.md` | Session-to-session context cache — updated after every ingest |\n"
        "| `log.md` | Append-only ingest history — human-readable in Obsidian |\n"
        "| `context.md` | Your investigation intent and key questions — read this before every skill |\n\n"
        "## Hard rules\n\n"
        "1. Public records only — never process confidential source material, private correspondence, or leaked documents. If a document cannot be identified as a public record, stop and ask before proceeding.\n"
        "2. Registry updates are atomic with note creation — never one without the other.\n"
        "3. No duplicate entities — check `.watchdog/Registry/entities.json` before creating.\n"
        "4. Entity IDs are kebab-case: `john-doe`, `shell-co-ltd`, `123-main-st`.\n"
        "5. Every extracted fact must carry a confidence level: `high`, `medium`, `low`, or `disputed`. A `low`-confidence fact is a lead, not a finding.\n"
        "6. The `## Notes` section in any note is reserved for journalist annotations — never overwrite it.\n"
        "7. Acquire `.watchdog/Registry/.ingest-lock` before any vault writes; release it on completion or failure.\n\n"
        "## Commands\n\n"
        "| Command | Action |\n"
        "|---------|--------|\n"
        "| `/watchdog-context` | Seed context.md from background files in `_CONTEXT/` |\n"
        "| `/watchdog-ingest` | Process all files in `_INCOMING/` |\n"
        "| `/watchdog-ingest [file]` | Process a specific file |\n"
        "| `/watchdog-query [question]` | Answer a question from the vault |\n"
        "| `/watchdog-surface` | Find connections and anomalies across the vault |\n"
        "| `/watchdog-wiki` | Create or update investigation thread pages |\n"
        "| `/watchdog-health` | Check vault integrity |\n\n"
        "## Confidence levels\n\n"
        "| Level | When to use |\n"
        "|-------|-------------|\n"
        "| `high` | Fact directly stated in the source document |\n"
        "| `medium` | Fact stated but requires one short inference |\n"
        "| `low` | Fact inferred across multiple sources |\n"
        "| `disputed` | Fact contradicted by another source in the vault |\n"
    )

    (vault / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "permissions": {
                    "allow": [
                        "Bash(watchdog preprocess-batch *)",
                        "Bash(watchdog preprocess *)",
                        "Bash(watchdog batch-get *)",
                        "Bash(watchdog near-dup *)",
                        "Bash(watchdog arrows *)",
                        "Bash(find _INCOMING/ *)",
                        "Bash(find _CONTEXT/ *)",
                        "Bash(mv _INCOMING*)",
                        "Bash(mkdir -p *)",
                        "Bash(watchdog write-vault *)",
                        "Bash(watchdog write-entity *)",
                        "Bash(rm /tmp/watchdog-extraction-*)",
                        "Bash(rm /tmp/entity-refresh-*)",
                        "Bash(rm .watchdog/Registry/.ingest-lock)",
                        "Bash(rm .watchdog/ingest.json)",
                    ]
                },
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "matcher": "",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": (
                                        "python3 -c \""
                                        "import os; "
                                        "files = []; "
                                        "[files.extend([f for f in fnames if not f.startswith('.') and not f.endswith('.yml')]) "
                                        "for root, dirs, fnames in os.walk('_INCOMING/') "
                                        "if '_FAILED' not in root]; "
                                        "n = len(files); "
                                        "print('WATCHDOG: ' + str(n) + ' file(s) pending in _INCOMING/') if n else None"
                                        "\""
                                    ),
                                }
                            ],
                        }
                    ]
                },
            },
            indent=2,
        ) + "\n"
    )

    projects = load_projects()
    projects[slug] = {"name": name, "path": str(vault), "created_at": now}
    save_projects(projects)

    print(f"{_GREEN}Created:{_RESET} {_BOLD}{vault}{_RESET}")
    print()
    print(f"{_BOLD}Next steps:{_RESET}")
    print(f"  1. Open {_CYAN}{vault}{_RESET} as a new vault in Obsidian")
    print(f"  2. Open {_CYAN}{vault}{_RESET} in Claude Code")
    print(f"  3. Drop documents into {_CYAN}_INCOMING/{_RESET} to begin ingesting")
    print()
    print(f"  {_DIM}To reopen: {_RESET}{_CYAN}watchdog open {slug}{_RESET}")


def cmd_about(_args) -> None:
    from watchdog import __version__
    print()
    print(f"  {_BOLD}Watchdog{_RESET}  {_DIM}v{__version__}{_RESET}")
    print(f"  {_DIM}Investigative journalism document intelligence{_RESET}")
    print()
    print(f"  {_DIM}GitHub   {_RESET}{_CYAN}https://github.com/tomcardoso/watchdog{_RESET}")
    print(f"  {_DIM}Issues   {_RESET}{_CYAN}https://github.com/tomcardoso/watchdog/issues{_RESET}")
    print(f"  {_DIM}Install  {_RESET}{_CYAN}https://github.com/tomcardoso/watchdog/blob/main/INSTALL.md{_RESET}")
    print()


def cmd_open(args) -> None:
    _, info = _find_project(args.name)
    path = info["path"]
    if not Path(path).exists():
        sys.exit(f"Error: project directory not found: {path}")
    print(f"  {_BOLD}{info['name']}{_RESET}  {_CYAN}{path}{_RESET}")
    os.chdir(path)
    os.execvp("claude", ["claude", "."])


def cmd_setup(args) -> None:
    from watchdog.setup_cmd import run as run_setup
    run_setup(force=getattr(args, "force", False))


def cmd_list(_args) -> None:
    projects = load_projects()
    if not projects:
        print(f"No projects. Create one with: {_CYAN}watchdog new <name>{_RESET}")
        return

    rows = []
    for slug, info in sorted(projects.items(), key=lambda x: x[1]["name"]):
        reg = _load_registry(Path(info["path"]))
        docs     = str(reg["document_count"]) if reg else "—"
        entities = str(reg["entity_count"])   if reg else "—"
        updated  = _fmt_date(reg["last_updated"]) if reg else "—"
        rows.append((info["name"], slug, docs, entities, updated))

    name_w = max(len(r[0]) for r in rows) + 2
    slug_w = max(len(r[1]) for r in rows) + 2
    header = f"  {_BOLD}{'Project':<{name_w}}{_RESET}  {_DIM}{'Slug':<{slug_w}}{'Docs':>6}  {'Entities':>8}  Updated{_RESET}"
    print(f"\n{header}")
    print(f"  {_DIM}{'─' * (name_w + slug_w + 30)}{_RESET}")
    for name, slug, docs, entities, updated in rows:
        print(f"  {_BOLD}{name:<{name_w}}{_RESET}  {_DIM}{slug:<{slug_w}}{docs:>6}  {entities:>8}  {updated}{_RESET}")
    print()


def cmd_status(args) -> None:
    _, info = _find_project(args.name)
    vault = Path(info["path"])

    if not vault.exists():
        sys.exit(f"Error: project directory not found: {vault}")

    reg = _load_registry(vault)
    if not reg:
        print(f"\n  {_BOLD}{info['name']}{_RESET}")
        print(f"  {_CYAN}{info['path']}{_RESET}")
        print(f"  {_DIM}Created {_fmt_date(info['created_at'])}{_RESET}")
        print(f"\n  {_DIM}No registry found — open this vault in Claude Code to begin ingesting.{_RESET}\n")
        return

    docs_file = vault / ".watchdog" / "Registry" / "documents.json"
    ents_file = vault / ".watchdog" / "Registry" / "entities.json"
    docs_data = json.loads(docs_file.read_text()) if docs_file.exists() else {}
    ents_data = json.loads(ents_file.read_text()) if ents_file.exists() else {}

    total_pages = sum(d.get("page_count", 0) for d in docs_data.values())
    doc_types   = Counter(d["document_type"] for d in docs_data.values() if d.get("document_type"))
    ent_types   = Counter(e["type"]          for e in ents_data.values() if e.get("type"))
    pending     = _count_incoming(vault)

    print(f"\n  {_BOLD}{info['name']}{_RESET}  {_DIM}{slugify(info['name'])}{_RESET}")
    print(f"  {_CYAN}{info['path']}{_RESET}")
    print(f"  {_DIM}Created {_fmt_date(info['created_at'])}{_RESET}")
    print()

    pages_note = f" {_DIM}({total_pages} pages){_RESET}" if total_pages else ""
    print(f"  {_BOLD}{reg['document_count']}{_RESET} documents{pages_note} · {_BOLD}{reg['entity_count']}{_RESET} entities · {_DIM}last updated {_fmt_date(reg['last_updated'])}{_RESET}")

    if doc_types:
        print()
        print(f"  {_BOLD}Documents by type{_RESET}")
        for dtype, count in sorted(doc_types.items(), key=lambda x: -x[1]):
            print(f"  {_DIM}  {dtype:<40}{_RESET} {count:>4}")

    if ent_types:
        print()
        print(f"  {_BOLD}Entities by type{_RESET}")
        for etype, count in sorted(ent_types.items(), key=lambda x: -x[1]):
            print(f"  {_DIM}  {etype:<40}{_RESET} {count:>4}")

    print()
    if pending:
        pending_label = f"{_YELLOW}{pending} file{'s' if pending != 1 else ''} pending{_RESET}"
    else:
        pending_label = f"{_DIM}none{_RESET}"
    print(f"  {_DIM}Pending in{_RESET} {_CYAN}_INCOMING/{_RESET}  {pending_label}")
    print()


def _print_banner() -> None:
    print(f"🔍🐕  {_BOLD}Watchdog{_RESET} — investigative document intelligence")
    print()
    print(f"{_DIM}Usage:  watchdog <command> [options]{_RESET}")
    print()
    print("Commands:")
    cmds = [
        ("new",    "Create a new investigation vault"),
        ("open",   "Open an investigation in Claude Code"),
        ("list",   "List all registered investigations"),
        ("status", "Show detailed status for an investigation"),
        ("setup",  "Set up Watchdog after installation"),
    ]
    for cmd, desc in cmds:
        print(f"  {_CYAN}{cmd:<8}{_RESET} {desc}")
    print()


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] in _ALIASES:
        sys.argv[1] = _ALIASES[sys.argv[1]]

    if len(sys.argv) >= 2 and sys.argv[1] in _PIPELINE_COMMANDS:
        import importlib
        module_path, prog_name = _PIPELINE_COMMANDS[sys.argv[1]]
        sys.argv = [prog_name] + sys.argv[2:]
        importlib.import_module(module_path).main()
        return

    parser = argparse.ArgumentParser(
        prog="watchdog",
        description="Investigative journalism document intelligence tool",
    )
    sub = parser.add_subparsers(dest="command", required=False)

    p_new = sub.add_parser("new", help="Create a new investigation vault")
    p_new.add_argument("name", help="Investigation name (e.g. 'Shell Company Investigation')")
    p_new.add_argument("--dir", help=f"Parent directory (default: projects_dir from config)")
    p_new.set_defaults(func=cmd_new)

    p_open = sub.add_parser("open", help="Open an investigation in Claude Code")
    p_open.add_argument("name", help="Investigation name or slug")
    p_open.set_defaults(func=cmd_open)

    p_list = sub.add_parser("list", help="List all registered investigations")
    p_list.set_defaults(func=cmd_list)

    p_status = sub.add_parser("status", help="Show detailed status for an investigation")
    p_status.add_argument("name", help="Investigation name or slug")
    p_status.set_defaults(func=cmd_status)

    p_setup = sub.add_parser("setup", help="Set up Watchdog after installation")
    p_setup.add_argument("--force", action="store_true", help="Re-run setup even if already complete")
    p_setup.set_defaults(func=cmd_setup)

    p_about = sub.add_parser("about", help="Show version and project links")
    p_about.set_defaults(func=cmd_about)

    args = parser.parse_args()

    if args.command is None:
        _print_banner()
        return

    if args.command not in ("setup", "about") and not CONFIG_FILE.exists():
        sys.exit("Watchdog isn't set up yet. Run:\n  watchdog setup")

    args.func(args)


if __name__ == "__main__":
    main()
