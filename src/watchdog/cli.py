"""watchdog — investigative journalism document intelligence CLI"""

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

WATCHDOG_HOME = Path.home() / ".watchdog"
PROJECTS_FILE = WATCHDOG_HOME / "projects.json"
CONFIG_FILE   = WATCHDOG_HOME / "config.json"


def _perf_cpu_count() -> int:
    """Performance core count on Apple Silicon; total core count everywhere else."""
    try:
        r = subprocess.run(
            ["sysctl", "-n", "hw.perflevel0.logicalcpu"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0:
            n = int(r.stdout.strip())
            if n > 0:
                return n
    except Exception:
        pass
    return os.cpu_count() or 4

_ALIASES = {
    "init":     "new",
    "create":   "new",
    "ls":       "list",
    "info":     "status",
    "inspect":  "status",
    "cd":       "open",
    "version":  "about",
    "config":   "configure",
    "setting":  "configure",
    "settings": "configure",
    "find":      "search",
    "process":   "chew",
    "preprocess": "chew",
    "prep":      "chew",
}

_PIPELINE_COMMANDS = {
    "near-dup":    ("watchdog.pipeline.near_dup",     "watchdog-near-dup"),
    "write-vault": ("watchdog.pipeline.write_vault",  "watchdog-write-vault"),
    "write-entity":("watchdog.pipeline.write_entity", "watchdog-write-entity"),
}

_TEMPLATES_DIR = Path(__file__).parent / "templates" / "vault"


def _render_template(filename: str, **vars: str) -> str:
    text = (_TEMPLATES_DIR / filename).read_text()
    for key, value in vars.items():
        text = text.replace("{" + key + "}", value)
    return text

_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_CYAN   = "\033[0;36m"
_YELLOW = "\033[0;33m"
_GREEN  = "\033[0;32m"
_RESET  = "\033[0m"


_CMD_HELP: dict[str, dict] = {
    "new": {
        "desc": "Create a new investigation vault",
        "args": [("name", "Investigation name (e.g. 'Shell Company Investigation')")],
        "opts": [("--dir DIR", "Parent directory (default: projects_dir from config)")],
    },
    "open": {
        "desc": "Chew pending documents and open in Claude Code",
        "args": [("name", "Investigation name or slug")],
    },
    "chew": {
        "desc": "Process documents in _INCOMING/ and prepare them for ingestion",
        "opts": [("--workers N", "Parallel file workers (default: auto)")],
    },
    "list": {
        "desc": "List all registered investigations",
    },
    "status": {
        "desc": "Show detailed status for an investigation",
        "args": [("name", "Investigation name or slug (omit to show all)")],
    },
    "search": {
        "desc": "Semantic search across ingested documents",
        "args": [("project", "Investigation name or slug"), ("query", "Search query")],
        "opts": [("--top N", "Number of results to return (default: 5)")],
    },
    "unlock": {
        "desc": "Release a stale ingest lock",
        "args": [("project", "Investigation name or slug")],
        "opts": [("--force", "Remove lock even if recent")],
    },
    "setup": {
        "desc": "Set up Watchdog after installation",
        "opts": [("--force", "Re-run setup even if already complete")],
    },
    "configure": {
        "desc": "View or change configuration",
        "args": [("key", "Configuration key (optional)"), ("value", "Value to set (optional)")],
    },
    "about": {
        "desc": "Show version and project links",
    },
}


def _print_cmd_help(cmd: str) -> None:
    info = _CMD_HELP.get(cmd, {})
    args = info.get("args", [])
    opts = info.get("opts", [])
    usage_parts = ["watchdog", cmd] + [f"<{n}>" for n, _ in args]
    if opts:
        usage_parts.append("[options]")
    print(f"\n  {info.get('desc', '')}")
    print()
    print(f"  {_DIM}Usage:  {' '.join(usage_parts)}{_RESET}")
    if args:
        print()
        print(f"  {_BOLD}Arguments{_RESET}")
        for name, desc in args:
            print(f"    {_CYAN}{name:<18}{_RESET} {desc}")
    print()
    print(f"  {_BOLD}Options{_RESET}")
    for flag, desc in opts:
        print(f"    {_CYAN}{flag:<18}{_RESET} {desc}")
    print(f"    {_CYAN}{'--help':<18}{_RESET} Show this message and exit")
    print()


def _projects_dir() -> Path:
    if CONFIG_FILE.exists():
        config = json.loads(CONFIG_FILE.read_text())
        return Path(config["projects_dir"]).expanduser()
    return Path.home() / "Investigations"


from watchdog.pipeline.write_vault import slugify  # noqa: E402 — after constants


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
        rel_parts = Path(root).relative_to(incoming).parts
        if any(p in ("_FAILED", "_failed") for p in rel_parts):
            dirs.clear()
            continue
        count += sum(1 for f in files if not f.startswith(".") and not f.endswith(".yml"))
    return count


def _count_queued(vault: Path) -> int:
    queue = vault / ".watchdog" / "queue"
    if not queue.exists():
        return 0
    return sum(1 for f in queue.iterdir() if f.suffix == ".json")


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
        ".watchdog/queue",
        ".watchdog/staging",
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
    (vault / ".watchdog" / "Registry" / "manifest.json").write_text("{}\n")
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

    (vault / "hot.md").write_text(_render_template("hot.md"))
    (vault / "log.md").write_text(_render_template("log.md"))
    (vault / "context.md").write_text(_render_template("context.md", name=name))
    (vault / "index.md").write_text(_render_template("index.md", name=name, today=today))
    (vault / "CLAUDE.md").write_text(_render_template("CLAUDE.md", name=name))

    from watchdog.setup_cmd import install_skills
    install_skills(vault / ".claude" / "commands")

    (vault / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "permissions": {
                    "allow": [
                        "Bash(watchdog near-dup *)",
                        "Bash(find .watchdog/queue/ *)",
                        "Bash(find _CONTEXT/ *)",
                        "Bash(mkdir -p *)",
                        "Bash(watchdog write-vault *)",
                        "Bash(watchdog write-entity *)",
                        "Bash(rm /tmp/watchdog-extraction-*)",
                        "Bash(rm /tmp/entity-refresh-*)",
                        "Bash(rm .watchdog/Registry/.ingest-lock)",
                        "Bash(rm .watchdog/queue/*)",
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
                                        "from pathlib import Path; "
                                        "p = list(Path('.watchdog/queue').glob('*.json')) "
                                        "if Path('.watchdog/queue').exists() else []; "
                                        "print('WATCHDOG: ' + str(len(p)) + ' file(s) ready for extraction — run /watchdog-ingest') if p else None"
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

    print(f"\n  {_GREEN}Created:{_RESET} {_BOLD}{vault}{_RESET}")
    print()
    print(f"  {_BOLD}Next steps{_RESET}")
    print(f"    1. Open {_CYAN}{vault}{_RESET} as a new vault in Obsidian")
    print(f"    2. Drop documents into {_CYAN}_INCOMING/{_RESET}")
    print(f"    3. Run {_CYAN}watchdog open {slug}{_RESET} to chew and open in Claude Code")
    print(f"    4. Run {_CYAN}/watchdog-ingest{_RESET} when prompted inside Claude Code")
    print()


def _run_preprocess(vault: Path, workers: int = 4, confirm: bool = False) -> None:
    from watchdog.pipeline.preprocess_batch import run_ingest, find_files
    incoming = vault / "_INCOMING"
    queue    = vault / ".watchdog" / "queue"
    if not incoming.is_dir():
        sys.exit(f"Error: _INCOMING/ not found in {vault}")
    if confirm:
        files = find_files([incoming])
        if not files:
            queued = len(list(queue.glob("*.json"))) if queue.exists() else 0
            if queued:
                print(f"\n  {_DIM}_INCOMING/ is empty — {queued} file{'s' if queued != 1 else ''} ready for {_RESET}{_CYAN}/watchdog-ingest{_RESET}{_DIM}.{_RESET}\n")
            else:
                print(f"\n  {_DIM}_INCOMING/ is empty — nothing to chew.{_RESET}\n")
            return
        n = len(files)
        label = f"{n} file{'s' if n != 1 else ''}"
        try:
            answer = input(f"\n  Found {_BOLD}{label}{_RESET} in _INCOMING/. Chew now? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if answer not in ("", "y", "yes"):
            return
    run_ingest(vault, workers=workers)


def cmd_chew(args) -> None:
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: not inside a Watchdog project folder. cd into your investigation first.")
    _run_preprocess(vault, workers=getattr(args, "workers", None))


def _launch_claude(vault: Path) -> None:
    try:
        os.execvp("claude", ["claude", str(vault)])
    except FileNotFoundError:
        sys.exit("Error: Claude Code not found — install from https://claude.ai/download")


def cmd_open(args) -> None:
    _, info = _find_project(args.name)
    vault = Path(info["path"])
    if not vault.exists():
        sys.exit(f"Error: project directory not found: {vault}")
    print(f"\n  {_BOLD}{info['name']}{_RESET}  {_CYAN}{vault}{_RESET}\n")
    os.chdir(vault)

    # Step 1: chew if there are files in _INCOMING/
    _run_preprocess(vault, confirm=True)

    # Step 2: offer to open Claude Code if there are files queued for ingest
    queue = vault / ".watchdog" / "queue"
    queued = len(list(queue.glob("*.json"))) if queue.exists() else 0
    if queued:
        try:
            answer = input(f"  {_BOLD}{queued}{_RESET} file{'s' if queued != 1 else ''} ready for {_CYAN}/watchdog-ingest{_RESET}. Open in Claude Code? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if answer in ("", "y", "yes"):
            _launch_claude(vault)
    else:
        _launch_claude(vault)



def cmd_about(_args) -> None:
    from watchdog import __version__
    print()
    print(f"  🔍🐕  {_BOLD}Watchdog{_RESET}  {_DIM}v{__version__}{_RESET}")
    print(f"  {_DIM}Investigative journalism document intelligence{_RESET}")
    print()
    print(f"  🐙  {_DIM}GitHub   {_RESET}{_CYAN}https://github.com/tomcardoso/watchdog{_RESET}")
    print(f"  🐛  {_DIM}Issues   {_RESET}{_CYAN}https://github.com/tomcardoso/watchdog/issues{_RESET}")
    print(f"  📖  {_DIM}Install  {_RESET}{_CYAN}https://github.com/tomcardoso/watchdog/blob/main/INSTALL.md{_RESET}")
    print()


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
        vault = Path(info["path"])
        reg      = _load_registry(vault)
        docs     = str(reg["document_count"]) if reg else "—"
        entities = str(reg["entity_count"])   if reg else "—"
        updated  = _fmt_date(reg["last_updated"]) if reg else "—"
        incoming = str(_count_incoming(vault)) if vault.exists() else "—"
        queued   = str(_count_queued(vault))   if vault.exists() else "—"
        rows.append((info["name"], slug, docs, entities, updated, incoming, queued))

    name_w = max(len(r[0]) for r in rows) + 2
    slug_w = max(len(r[1]) for r in rows) + 2
    # each column: 2-space prefix + content width
    # name(name_w) + slug(slug_w) + docs(6) + entities(8) + to_chew(7) + to_ingest(9) + updated(10) + 7×2 separators - 2(leading indent)
    sep_w = name_w + slug_w + 6 + 8 + 7 + 9 + 10 + 7 * 2 - 2
    header = (
        f"  {_BOLD}{'Project':<{name_w}}{_RESET}"
        f"  {_DIM}{'Slug':<{slug_w}}"
        f"  {'Docs':>6}"
        f"  {'Entities':>8}"
        f"  {'To chew':>7}"
        f"  {'To ingest':>9}"
        f"  Updated{_RESET}"
    )
    print(f"\n{header}")
    print(f"  {_DIM}{'─' * sep_w}{_RESET}")
    for name, slug, docs, entities, updated, incoming, queued in rows:
        inc = "—" if incoming == "0" else incoming
        que = "—" if queued   == "0" else queued
        inc_str = f"{_YELLOW}{inc:>7}{_RESET}" if inc != "—" else f"{_DIM}{inc:>7}{_RESET}"
        que_str = f"{_YELLOW}{que:>9}{_RESET}" if que != "—" else f"{_DIM}{que:>9}{_RESET}"
        print(
            f"  {_BOLD}{name:<{name_w}}{_RESET}"
            f"  {_DIM}{slug:<{slug_w}}"
            f"  {docs:>6}"
            f"  {entities:>8}{_RESET}"
            f"  {inc_str}"
            f"  {que_str}"
            f"  {_DIM}{updated}{_RESET}"
        )
    print()


def cmd_status(args) -> None:
    if not args.name:
        cmd_list(args)
        return
    _, info = _find_project(args.name)
    vault = Path(info["path"])

    if not vault.exists():
        sys.exit(f"Error: project directory not found: {vault}")

    reg = _load_registry(vault)
    if not reg:
        print(f"\n  {_BOLD}{info['name']}{_RESET}")
        print(f"  {_CYAN}{info['path']}{_RESET}")
        print(f"  {_DIM}Created {_fmt_date(info.get('created_at', ''))}{_RESET}")
        print(f"\n  {_DIM}No registry found — open this vault in Claude Code to begin ingesting.{_RESET}\n")
        return

    docs_file = vault / ".watchdog" / "Registry" / "documents.json"
    ents_file = vault / ".watchdog" / "Registry" / "entities.json"
    try:
        docs_data = json.loads(docs_file.read_text()) if docs_file.exists() else {}
        ents_data = json.loads(ents_file.read_text()) if ents_file.exists() else {}
    except json.JSONDecodeError as e:
        sys.exit(f"Error: registry file is corrupt — {e}\nRun '/watchdog-health' to diagnose.")

    total_pages = sum(d.get("page_count", 0) for d in docs_data.values())
    doc_types   = Counter(d["document_type"] for d in docs_data.values() if d.get("document_type"))
    ent_types   = Counter(e["type"]          for e in ents_data.values() if e.get("type"))
    incoming_n = _count_incoming(vault)
    queued_n   = _count_queued(vault)

    print(f"\n  {_BOLD}{info['name']}{_RESET}  {_DIM}{slugify(info['name'])}{_RESET}")
    print(f"  {_CYAN}{info['path']}{_RESET}")
    print(f"  {_DIM}Created {_fmt_date(info.get('created_at', ''))}{_RESET}")
    print()

    pages_note = f" {_DIM}({total_pages} pages){_RESET}" if total_pages else ""
    print(f"  {_BOLD}{reg['document_count']}{_RESET} documents{pages_note} · {_BOLD}{reg['entity_count']}{_RESET} entities · {_DIM}last updated {_fmt_date(reg['last_updated'])}{_RESET}")

    if incoming_n:
        print(f"  {_YELLOW}{incoming_n} file{'s' if incoming_n != 1 else ''}{_RESET} in {_CYAN}_INCOMING/{_RESET} {_DIM}— run{_RESET} {_CYAN}watchdog chew{_RESET}")
    if queued_n:
        print(f"  {_YELLOW}{queued_n} file{'s' if queued_n != 1 else ''}{_RESET} chewed and waiting for {_CYAN}/watchdog-ingest{_RESET}")

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


_CONFIGURE_KEYS = {
    # ── Project ───────────────────────────────────────────────────────────────
    "projects_dir": {
        "short": "Path where new investigation vaults are created",
        "help": (
            "The directory where `watchdog new` creates investigation vaults.\n"
            "  Set during setup; change here to move future vaults to a different location.\n"
            "  Existing vaults are not moved."
        ),
        "type": "path",
    },
    # ── OCR ───────────────────────────────────────────────────────────────────
    "ocr_engine": {
        "short": "OCR engine for scanned documents (default: auto)",
        "help": (
            "OCR engine used when processing scanned documents.\n"
            "  auto:         Apple Vision on macOS (if ocrmac installed), Tesseract elsewhere.\n"
            "  apple_vision: Apple Vision only — macOS with ocrmac required.\n"
            "  tesseract:    Tesseract — requires system install (brew or apt install tesseract-ocr).\n"
            "  easyocr:      EasyOCR — pure pip install, no system deps, less accurate on forms.\n"
            "  rapidocr:     RapidOCR — lightweight, no C deps, fast.\n"
            "  Valid values: auto, apple_vision, tesseract, easyocr, rapidocr."
        ),
        "type": "enum",
        "default": "auto",
        "choices": ["auto", "apple_vision", "tesseract", "easyocr", "rapidocr"],
    },
    "ocr_languages": {
        "short": "Apple Vision OCR languages (comma-separated BCP 47 codes, e.g. en-US,fr-FR)",
        "help": (
            "Languages Apple Vision should try when reading scanned documents.\n"
            "  Leave unset to auto-detect from the image (macOS 13+).\n"
            "  Set explicitly if auto-detection produces poor results or you are on macOS 12.\n"
            "  Codes: https://developer.apple.com/documentation/vision/vnrecognizetextrequest"
        ),
        "type": "lang_list",
    },
    "garbled_threshold": {
        "short": "OCR trigger threshold — alphanumeric ratio below which a PDF text layer is garbled (default: 0.75)",
        "help": (
            "When reading a PDF, Watchdog samples the text layer and measures what fraction of\n"
            "  characters are alphanumeric or whitespace. If the ratio falls below this threshold,\n"
            "  the text layer is considered garbled and OCR is applied automatically.\n"
            "  Lower = more aggressive OCR. Higher may miss subtly garbled pages.\n"
            "  Valid range: 0.0–1.0. Default: 0.75."
        ),
        "type": "float",
        "default": 0.75,
        "min": 0.0,
        "max": 1.0,
    },
    # ── Processing ────────────────────────────────────────────────────────────
    "chew_workers": {
        "short": "Parallel files during chewing ('auto' for adaptive, or a fixed number)",
        "help": (
            "Number of files chewed simultaneously by `watchdog chew`.\n"
            "  'auto' (default): Watchdog scans the batch before starting and sets this based on\n"
            "  median document length — more workers for short-doc batches, fewer for large PDFs.\n"
            "  Set to a whole number to pin the value regardless of batch content.\n"
            "  Set to 1 to process files one at a time."
        ),
        "type": "int_or_auto",
        "default": "auto",
        "min": 1,
    },
    "chunk_size": {
        "short": "Pages per chunk when splitting large PDFs for parallel processing (default: 40)",
        "help": (
            "PDFs with more pages than this value are split into chunks and processed in parallel.\n"
            "  Smaller chunks reduce peak memory per worker but add per-chunk overhead.\n"
            "  Larger chunks are more efficient on fast machines with ample RAM.\n"
            "  Default: 40."
        ),
        "type": "int",
        "default": 40,
        "min": 1,
    },
    "chunk_workers": {
        "short": "Parallel subprocesses for large-PDF chunks ('auto' for adaptive, or a fixed number)",
        "help": (
            "Number of parallel subprocesses used when splitting large PDFs (>chunk_size pages).\n"
            "  'auto' (default): set adaptively based on median document length in the batch.\n"
            "  Works in tandem with chew_workers: total subprocess load for large-PDF batches\n"
            "  is approximately chew_workers × chunk_workers.\n"
            "  Set to 1 to disable within-file parallelism."
        ),
        "type": "int_or_auto",
        "default": "auto",
        "min": 1,
    },
    "chunk_timeout": {
        "short": "Seconds before a chunk subprocess is killed (default: 300)",
        "help": (
            "Each chunk subprocess is given this many seconds to complete before being killed.\n"
            "  Increase for very large or complex PDFs on slow machines.\n"
            "  Default: 300 (5 minutes)."
        ),
        "type": "int",
        "default": 300,
        "min": 1,
    },
    # ── Extraction ────────────────────────────────────────────────────────────
    "table_structure": {
        "short": "Run table detection model on PDFs (default: true)",
        "help": (
            "When enabled, Docling runs a dedicated ML model to detect and reconstruct tables.\n"
            "  Disable to speed up ingestion of text-only documents (court decisions, contracts).\n"
            "  Does not affect text extraction — only the table structure model.\n"
            "  Default: true."
        ),
        "type": "bool",
        "default": True,
    },
    "embed_images": {
        "short": "Embed images as base64 in markdown output so Claude can see figures (default: false)",
        "help": (
            "When enabled, images and figures in documents are embedded as base64 data\n"
            "  in the markdown output, allowing Claude to read charts, graphs, and other\n"
            "  visual content directly. Significantly increases token usage and processing\n"
            "  time per document. Only useful when documents contain charts, image-based\n"
            "  tables, or diagrams that carry investigative value.\n"
            "  Default: false."
        ),
        "type": "bool",
        "default": False,
    },
    # ── Deduplication ─────────────────────────────────────────────────────────
    "dup_threshold": {
        "short": "Near-duplicate Jaccard similarity threshold — score at which documents are flagged (default: 0.85)",
        "help": (
            "Watchdog fingerprints each document and compares it to all previously ingested documents\n"
            "  using Jaccard similarity on word n-grams. If the score meets or exceeds this threshold,\n"
            "  the document is flagged as a near-duplicate.\n"
            "  Higher = stricter matching (fewer false positives, may miss near-duplicates).\n"
            "  Lower = looser matching (more matches, more false positives).\n"
            "  Valid range: 0.0–1.0. Default: 0.85."
        ),
        "type": "float",
        "default": 0.85,
        "min": 0.0,
        "max": 1.0,
    },
    "shingle_size": {
        "short": "Word n-gram size for near-duplicate fingerprinting (default: 3)",
        "help": (
            "Documents are fingerprinted using overlapping sequences of n consecutive words.\n"
            "  Larger n is more precise but slower and uses more registry storage per document.\n"
            "  Smaller n is faster but produces more false positives.\n"
            "  Changing this invalidates existing shingle data — re-ingest to rebuild fingerprints.\n"
            "  Default: 3 (word trigrams)."
        ),
        "type": "int",
        "default": 3,
        "min": 1,
    },
}


_OCR_ENGINE_PACKAGES = {
    # engine → (import_name, pip_package) or None if bundled with docling
    "apple_vision": ("ocrmac",               "ocrmac"),
    "tesseract":    ("tesserocr",            "tesserocr"),
    "rapidocr":     ("rapidocr_onnxruntime", "rapidocr-onnxruntime"),
    "easyocr":      None,
    "auto":         None,
}

_TESSERACT_HEADERS_HINT = (
    "Tesseract system headers are required to build tesserocr:\n"
    "  Ubuntu/Debian:  sudo apt install tesseract-ocr libtesseract-dev\n"
    "  Fedora:         sudo dnf install tesseract tesseract-devel\n"
    "  macOS:          brew install tesseract\n"
    "Then re-run: watchdog configure ocr_engine tesseract"
)


def _ensure_ocr_engine(engine: str) -> None:
    """Install the Python binding for the requested OCR engine if not already present."""
    if engine == "apple_vision" and sys.platform != "darwin":
        sys.exit("Error: apple_vision OCR is only available on macOS.")

    spec = _OCR_ENGINE_PACKAGES.get(engine)
    if spec is None:
        return

    import_name, pip_name = spec
    try:
        __import__(import_name)
        return  # already installed
    except ImportError:
        pass

    print(f"\n  {_DIM}Installing {pip_name}...{_RESET}")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", pip_name],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        msg = f"\n  {_YELLOW}Warning:{_RESET} could not install {pip_name}.\n"
        if "tesserocr" in pip_name and ("gcc" in stderr or "compile" in stderr.lower() or "build" in stderr.lower()):
            msg += f"\n  {_DIM}{_TESSERACT_HEADERS_HINT}{_RESET}\n"
        else:
            msg += f"\n  {_DIM}{stderr[:300]}{_RESET}\n"
        print(msg)
    else:
        print(f"  {_GREEN}Installed:{_RESET} {_BOLD}{pip_name}{_RESET}\n")


def cmd_search(args) -> None:
    _, info = _find_project(args.project)
    vault = Path(info["path"])

    from watchdog.pipeline.embed import search, index_stats
    stats = index_stats(vault)
    if stats["total"] == 0:
        print(f"\n  {_DIM}No embeddings found. Index is built automatically during ingest.{_RESET}\n")
        return

    results = search(vault, args.query, top_n=args.top_n)
    print()
    if not results:
        print(f"  {_DIM}No results.{_RESET}\n")
        return
    for r in results:
        score = f"{r['score']:.2f}"
        if r.get("type") == "note":
            print(f"  {_BOLD}{r['note_path']}{_RESET}  {_DIM}score {score}{_RESET}")
        else:
            print(f"  {_BOLD}{r.get('filename', '?')}{_RESET}  {_DIM}p.{r.get('page')}  score {score}{_RESET}")
        preview = r["preview"].replace("\n", " ").strip()
        print(f"  {_DIM}{preview[:200]}{_RESET}")
        print()


def cmd_unlock(args) -> None:
    _, info = _find_project(args.project)
    vault = Path(info["path"])
    lock_path = vault / ".watchdog" / "Registry" / ".ingest-lock"

    print()
    if not lock_path.exists():
        print(f"  {_DIM}No ingest lock found — nothing to do.{_RESET}\n")
        return

    content = lock_path.read_text()
    started_at = None
    for line in content.splitlines():
        if line.startswith("started_at:"):
            started_at = line.split(":", 1)[1].strip()
            break

    age_str = "unknown age"
    is_stale = True
    if started_at:
        try:
            t = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            age_secs = (datetime.now(timezone.utc) - t).total_seconds()
            mins = int(age_secs // 60)
            age_str = f"{mins}m ago"
            is_stale = age_secs >= 1800
        except ValueError:
            pass

    if is_stale or args.force:
        lock_path.unlink()
        print(f"  {_GREEN}Removed:{_RESET} {_BOLD}.ingest-lock{_RESET}  {_DIM}({age_str}){_RESET}\n")
    else:
        print(f"  {_YELLOW}Lock is recent{_RESET} ({age_str}) — an ingest may still be running.")
        print(f"  Use {_CYAN}watchdog unlock {args.project} --force{_RESET} to remove it anyway.\n")


def cmd_configure(args) -> None:
    config = {}
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text())
        except json.JSONDecodeError:
            sys.exit("Error: config file is corrupt. Try running 'watchdog setup --force'.")

    key   = getattr(args, "key",   None)
    value = getattr(args, "value", None)

    def _display_value(k, v):
        meta = _CONFIGURE_KEYS.get(k, {})
        if v is None:
            if k == "ocr_languages":
                return f"{_DIM}auto-detect (default){_RESET}"
            d = meta.get("default")
            if d is not None:
                v = d  # fall through to normal rendering with the default value
            else:
                return f"{_DIM}(not set){_RESET}"
        if k == "ocr_languages":
            return f"{_CYAN}{', '.join(v)}{_RESET}" if v else f"{_DIM}auto-detect (default){_RESET}"
        if isinstance(v, bool):
            return f"{_CYAN}{'true' if v else 'false'}{_RESET}"
        return f"{_CYAN}{v}{_RESET}"

    if key is None:
        print()
        print(f"  {_BOLD}Configuration{_RESET}  {_DIM}{CONFIG_FILE}{_RESET}")
        print()
        for k, meta in _CONFIGURE_KEYS.items():
            print(f"  {_DIM}{k:<20}{_RESET} {_display_value(k, config.get(k))}")
            print(f"  {' ' * 20} {_DIM}{meta['short']}{_RESET}")
            print()
        return

    if key not in _CONFIGURE_KEYS:
        sys.exit(f"Error: unknown key '{key}'. Known keys: {', '.join(_CONFIGURE_KEYS)}")

    meta = _CONFIGURE_KEYS[key]

    if value is None:
        if sys.stdin.isatty():
            print(f"\n  {_BOLD}{key}{_RESET}\n")
            for line in meta["help"].split("\n"):
                print(f"  {_DIM}{line.strip()}{_RESET}")
            print()
            print(f"  Current value:  {_display_value(key, config.get(key))}")
            if key in ("chunk_workers", "chew_workers"):
                print(f"  Machine cores:  {os.cpu_count() or 1}")
            print()
            answer = input("  Change this value? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print()
                return
            print()
            value = input("  New value: ").strip()
            if not value:
                print(f"\n  {_DIM}No change.{_RESET}\n")
                return
        else:
            print(f"\n  {_BOLD}{key}{_RESET} = {_display_value(key, config.get(key))}\n")
            return

    if key == "ocr_languages":
        langs = [lang.strip() for lang in value.split(",") if lang.strip()]
        config[key] = langs
        display = ", ".join(langs) if langs else "auto-detect"
    elif key == "projects_dir":
        path = Path(value).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        config[key] = str(path)
        display = str(path)
    elif meta["type"] == "float":
        try:
            v = float(value)
        except ValueError:
            sys.exit(f"Error: '{key}' must be a number (e.g. 0.85)")
        lo, hi = meta.get("min"), meta.get("max")
        if lo is not None and v < lo:
            sys.exit(f"Error: '{key}' must be >= {lo}")
        if hi is not None and v > hi:
            sys.exit(f"Error: '{key}' must be <= {hi}")
        config[key] = v
        display = str(v)
    elif meta["type"] == "int_or_auto":
        if value.lower() == "auto":
            config[key] = "auto"
            display = "auto"
        else:
            try:
                v = int(value)
            except ValueError:
                sys.exit(f"Error: '{key}' must be 'auto' or a whole number")
            lo = meta.get("min")
            if lo is not None and v < lo:
                sys.exit(f"Error: '{key}' must be >= {lo}")
            config[key] = v
            display = str(v)
    elif meta["type"] == "int":
        try:
            v = int(value)
        except ValueError:
            sys.exit(f"Error: '{key}' must be a whole number")
        lo = meta.get("min")
        if lo is not None and v < lo:
            sys.exit(f"Error: '{key}' must be >= {lo}")
        config[key] = v
        display = str(v)
    elif meta["type"] == "bool":
        if value.lower() in ("true", "yes", "1", "on"):
            v = True
        elif value.lower() in ("false", "no", "0", "off"):
            v = False
        else:
            sys.exit(f"Error: '{key}' must be true or false")
        config[key] = v
        display = "true" if v else "false"
    elif meta["type"] == "enum":
        choices = meta.get("choices", [])
        if value not in choices:
            sys.exit(f"Error: '{key}' must be one of: {', '.join(choices)}")
        config[key] = value
        display = value
        if key == "ocr_engine":
            _ensure_ocr_engine(value)
    else:
        config[key] = value
        display = value

    WATCHDOG_HOME.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")
    print(f"\n  {_GREEN}Set:{_RESET} {_BOLD}{key}{_RESET} = {_CYAN}{display}{_RESET}\n")


def _print_banner() -> None:
    print(f"\n  🔍🐕  {_BOLD}Watchdog{_RESET} — investigative document intelligence")
    print()
    print(f"  {_DIM}Usage:  watchdog <command> [options]{_RESET}")
    print()
    print(f"  {_BOLD}Commands{_RESET}")
    cmds = [
        ("new",       "Create a new investigation vault"),
        ("open",      "Chew pending documents and open in Claude Code"),
        ("chew",      "Process documents in _INCOMING/ and prepare them for ingestion"),
        ("list",      "List all registered investigations"),
        ("status",    "Show detailed status for an investigation"),
        ("search",    "Semantic search across ingested documents"),
        ("unlock",    "Release a stale ingest lock"),
        ("setup",     "Set up Watchdog after installation"),
        ("configure", "View or change configuration"),
        ("about",     "Show version and project links"),
    ]
    for cmd, desc in cmds:
        print(f"    {_CYAN}{cmd:<10}{_RESET} {desc}")
    print()


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] in ("-v", "--version"):
        cmd_about(None)
        return

    if len(sys.argv) >= 2 and sys.argv[1] in ("-h", "--help"):
        _print_banner()
        return

    if len(sys.argv) >= 3 and sys.argv[2] in ("-h", "--help"):
        cmd = _ALIASES.get(sys.argv[1], sys.argv[1])
        if cmd in _CMD_HELP:
            _print_cmd_help(cmd)
            return

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

    p_open = sub.add_parser("open", help="Chew pending documents and open in Claude Code")
    p_open.add_argument("name", help="Investigation name or slug")
    p_open.set_defaults(func=cmd_open)

    p_list = sub.add_parser("list", help="List all registered investigations")
    p_list.set_defaults(func=cmd_list)

    p_status = sub.add_parser("status", help="Show detailed status for an investigation")
    p_status.add_argument("name", nargs="?", help="Investigation name or slug (omit to list all)")
    p_status.set_defaults(func=cmd_status)

    p_setup = sub.add_parser("setup", help="Set up Watchdog after installation")
    p_setup.add_argument("--force", action="store_true", help="Re-run setup even if already complete")
    p_setup.set_defaults(func=cmd_setup)

    p_about = sub.add_parser("about", help="Show version and project links")
    p_about.set_defaults(func=cmd_about)

    p_search = sub.add_parser("search", help="Semantic search across ingested documents")
    p_search.add_argument("project", help="Investigation name or slug")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--top", dest="top_n", type=int, default=5, metavar="N",
                          help="Number of results to return (default: 5)")
    p_search.set_defaults(func=cmd_search)

    p_unlock = sub.add_parser("unlock", help="Release a stale ingest lock")
    p_unlock.add_argument("project", help="Investigation name or slug")
    p_unlock.add_argument("--force", action="store_true", help="Remove lock even if recent")
    p_unlock.set_defaults(func=cmd_unlock)

    p_configure = sub.add_parser("configure", help="View or change configuration")
    p_configure.add_argument("key",   nargs="?", help=f"Config key ({', '.join(_CONFIGURE_KEYS)})")
    p_configure.add_argument("value", nargs="?", help="Value to set")
    p_configure.set_defaults(func=cmd_configure)

    p_chew = sub.add_parser("chew", help="Process documents in _INCOMING/ and prepare them for ingestion")
    p_chew.add_argument("--workers", type=int, default=None, metavar="N",
                        help="Parallel file workers (default: auto)")
    p_chew.set_defaults(func=cmd_chew)

    args = parser.parse_args()

    if args.command is None:
        if Path(".watchdog").is_dir():
            _run_preprocess(Path(".").resolve(), confirm=True)
        else:
            _print_banner()
        return

    if args.command not in ("setup", "about", "configure", "search", "unlock") and not CONFIG_FILE.exists():
        sys.exit("Watchdog isn't set up yet. Run:\n  watchdog setup")

    args.func(args)


if __name__ == "__main__":
    main()
