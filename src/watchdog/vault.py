"""An investigation folder's scaffolding and what is waiting in it.

The files a new investigation starts with (rendered from `templates/vault/`), the Claude Code
setup an Ask Claude session reads (`.claude/settings.json`, `.claude/CLAUDE.md` and the shortcut
commands in `.claude/commands/`), and the counts of documents at each stage that the app shows.
"""

from __future__ import annotations

import importlib.resources
import json
import os
from pathlib import Path

from watchdog.vault_paths import incoming_dir, is_set_aside

TEMPLATES_DIR = Path(__file__).parent / "templates" / "vault"

# What a session may write with Claude Code's file tools: its own pages and the web-research
# worklist (#196), and nothing else in the vault. Everything else there belongs to the pipeline or to
# Claude Code's own setup (`.claude/`, `.mcp.json`), which a session that could write it could use to
# run commands at the next session's start (D304). The app's edit hook (`gui/chat.py`) enforces this
# list in code; the `Edit(...)` rules below only spare the reporter a prompt for these paths.
# A folder ends in "/"; anything else is one file.
SESSION_WRITABLE = ("queries/", "wiki/", "briefings/", "context.md", ".watchdog/research/")


def session_may_write(rel: str) -> bool:
    """Whether a session may change the vault-relative path `rel` (forward slashes)."""
    rel = rel.strip("/")
    return any(rel.startswith(w) if w.endswith("/") else rel == w for w in SESSION_WRITABLE)


def _edit_rule(entry: str) -> str:
    return f"Edit({entry}**)" if entry.endswith("/") else f"Edit({entry})"


# What a session runs without a prompt: Watchdog's read-only tools and edits to the paths above.
# The tools that write (timeline, write_entity, watchlist_add, contradiction_add) ask the reporter
# every time (D304). WebSearch / WebFetch are deliberately NOT here: they make outbound requests, so
# the watchdog-research skill's own `allowed-tools` pre-approves them only while it is active
# (#186, D45). File-permission checks only match Edit(path) rules, which cover every file-editing
# tool, Write included.
SESSION_PERMISSIONS = [
    *(f"mcp__watchdog__{name}" for name in ("search", "leads", "check_citations", "research_seen",
                                                 "read_background")),
    *(_edit_rule(w) for w in SESSION_WRITABLE),
]

# Watchdog's own settings and keys, denied to a vault's sessions outright (D257). These rules
# govern Claude Code's file tools; Watchdog's tools confine themselves in code.
SESSION_DENY = [
    "Read(~/.watchdog/**)",
    "Edit(~/.watchdog/**)",
]

# The markers around Watchdog's part of `.claude/CLAUDE.md`; the reporter's notes below the end
# marker are kept when the part is rewritten.
CLAUDE_MD_BEGIN = "<!-- watchdog:begin"
CLAUDE_MD_END = "<!-- watchdog:end -->"


def claude_settings() -> dict:
    """The investigation's `.claude/settings.json`, for Claude Code opened on the folder outside
    the app. No hooks. An app session doesn't depend on this file: it is handed the same rules,
    with hooks switched off, by `session_settings` (D304)."""
    return {
        "permissions": {
            "allow": list(SESSION_PERMISSIONS),
            # Read/Glob/Grep are unrestricted by default in Claude Code, so without this a session
            # in one vault could silently read another vault, or anything else on the account.
            # Source documents are adversarial by assumption (I6), so a prompt-injected one could
            # otherwise direct an exfiltrating read with no permission prompt to catch it.
            "blockReadsOutsideWorkingDirectories": True,
            "deny": list(SESSION_DENY),
        },
    }


def session_settings() -> dict:
    """The settings the app passes to every Ask Claude session on the command line (D304).

    Claude Code ranks settings passed this way above the vault's own `.claude/settings.json`, so a
    vault's file (which a shared folder, a document-steered session or the reporter may have
    changed) can neither add hooks nor loosen these rules. `disableAllHooks` switches off every
    command hook a settings file declares; the app's own hooks are SDK callbacks and still run.
    Claude Code also ignores a vault's `permissions.allow` entries outright in an untrusted
    workspace, which an app session always is, so the allow rules have to come from here."""
    settings = claude_settings()
    settings["disableAllHooks"] = True
    settings["enableAllProjectMcpServers"] = False
    return settings


def render_template(filename: str, **values: str) -> str:
    text = (TEMPLATES_DIR / filename).read_text()
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


def _write_if_changed(path: Path, data: bytes) -> bool:
    """Write `data` unless the file already holds it, so an unchanged file keeps its date."""
    try:
        if path.read_bytes() == data:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def install_commands(commands_dir: Path) -> list[str]:
    """Copy the session's shortcut commands (`/watchdog-query` and the rest) into the vault, and
    remove a `watchdog-*` command this version no longer ships; a command of the reporter's own,
    under another name, is left alone (D354). Returns the names changed. Record skills are not
    copied: the pipeline reads them from `watchdog.skills_catalog` (D21)."""
    skills = importlib.resources.files("watchdog") / "skills"
    shipped = {item.name: item.read_bytes() for item in skills.iterdir()
               if item.name.endswith(".md") and not item.name.startswith("_")}
    changed = [name for name, data in sorted(shipped.items())
               if _write_if_changed(commands_dir / name, data)]
    for old in sorted(commands_dir.glob("watchdog-*.md")):
        if old.name not in shipped:
            old.unlink()
            changed.append(old.name)
    return changed


def write_claude_md(vault: Path, name: str) -> bool:
    """Write Watchdog's part of `.claude/CLAUDE.md` from the current template, keeping anything
    the reporter wrote outside the markers. Returns whether the file changed."""
    path = vault / ".claude" / "CLAUDE.md"
    block = render_template("CLAUDE.md", name=name).rstrip("\n")
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    begin, end = current.find(CLAUDE_MD_BEGIN), current.find(CLAUDE_MD_END)
    if 0 <= begin < end:
        current = current[:begin] + block + current[end + len(CLAUDE_MD_END):]
    else:
        current = block + "\n"
    return _write_if_changed(path, current.encode("utf-8"))


def refresh_claude_setup(vault: Path, name: str) -> list[str]:
    """Bring the investigation's `.claude/` up to this version of Watchdog: the shortcut commands,
    Watchdog's section of `CLAUDE.md` and `settings.json` (written whole from the current rules).
    Only files that differ are written. Returns the paths changed, relative to `.claude/`. The app
    runs this before every conversation's first message, so a release's new instructions reach
    existing investigations without the reporter doing anything (D354)."""
    claude = vault / ".claude"
    changed = [f"commands/{n}" for n in install_commands(claude / "commands")]
    if write_claude_md(vault, name):
        changed.append("CLAUDE.md")
    settings = (json.dumps(claude_settings(), indent=2) + "\n").encode("utf-8")
    if _write_if_changed(claude / "settings.json", settings):
        changed.append("settings.json")
    return changed


def pkg_version() -> str:
    """The installed Watchdog version, for stamping into a new vault's README."""
    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version("watchdog-intel")
        except PackageNotFoundError:
            return "dev"
    except Exception:
        return "dev"


# ── what is waiting ──────────────────────────────────────────────────────────────────────

def count_incoming(vault: Path) -> int:
    """Files in `incoming/` waiting to be pre-processed (the failed and skipped folders and
    sidecars aside)."""
    incoming = incoming_dir(vault)
    if not incoming.exists():
        return 0
    count = 0
    for root, dirs, files in os.walk(incoming):
        if is_set_aside(Path(root).relative_to(incoming).parts):
            dirs.clear()
            continue
        count += sum(1 for f in files if not f.startswith(".") and not f.endswith(".yml"))
    return count


def count_queued(vault: Path) -> int:
    queue = vault / ".watchdog" / "queue"
    if not queue.exists():
        return 0
    return sum(1 for f in queue.iterdir() if f.suffix == ".json")


def count_awaiting_processing(vault: Path) -> int:
    """Queued documents with no staged extraction yet. A queue file stays until post-processing
    commits its document, so the queue alone can't tell these from the ones below."""
    queue = vault / ".watchdog" / "queue"
    if not queue.exists():
        return 0
    extracted = vault / ".watchdog" / "extracted"
    return sum(1 for f in queue.iterdir()
               if f.suffix == ".json" and not (extracted / f.name).exists())


def count_awaiting_post(vault: Path) -> int:
    """Queued documents already extracted but not yet committed by post-processing."""
    queue = vault / ".watchdog" / "queue"
    if not queue.exists():
        return 0
    extracted = vault / ".watchdog" / "extracted"
    return sum(1 for f in queue.iterdir()
               if f.suffix == ".json" and (extracted / f.name).exists())


def count_failed(vault: Path) -> int:
    """Documents processing set aside in `.watchdog/queue/_failed/`."""
    d = vault / ".watchdog" / "queue" / "_failed"
    return sum(1 for f in d.glob("*.json")) if d.is_dir() else 0


WATCHLIST_TEMPLATE = """\
# Watch list
#
# One term per line. When a newly-ingested document mentions a term listed here,
# Watchdog writes an alert to briefings/alerts-<date>.md and lists it in Review.
#
#   - Lines starting with # are ignored (like this one).
#   - Matching is case-insensitive and matches whole words ("Ana" won't match "banana").
#   - Blank lines are ignored.
#   - Advanced: wrap a line in slashes for a regular expression, e.g. /Smith,?\\s+John/
#
# Add your terms below:

"""


# Native Obsidian Bases dashboard (core feature, Obsidian 1.9+ — no plugin needed).
# Written verbatim at scaffold time; queries the entity/document note frontmatter the
# pipeline already writes. Bases has no documented `sort:` key, so recurrence (an
# entity's appears_in count) is surfaced as a column the user sorts on by clicking the
# header. See DECISIONS D42.
DASHBOARD_BASE = """\
filters:
  and:
    - file.ext == "md"
formulas:
  documents: appears_in.length
properties:
  name:
    displayName: Name
  type:
    displayName: Type
  aliases:
    displayName: Also known as
  document_type:
    displayName: Document type
  date_of_document:
    displayName: Dated
  date_ingested:
    displayName: Ingested
  date_last_updated:
    displayName: Updated
  page_count:
    displayName: Pages
  near_duplicate_of:
    displayName: Near-duplicate of
  formula.documents:
    displayName: Documents
views:
  - type: table
    name: Most-mentioned entities
    filters:
      and:
        - file.inFolder("entities")
    order:
      - file.name
      - name
      - type
      - formula.documents
      - date_last_updated
    limit: 20
  - type: table
    name: Recent documents
    filters:
      and:
        - file.inFolder("documents")
    order:
      - file.name
      - document_type
      - date_of_document
      - date_ingested
      - page_count
    limit: 15
  - type: table
    name: People
    filters:
      and:
        - file.inFolder("entities/person")
    order:
      - file.name
      - name
      - aliases
      - formula.documents
  - type: table
    name: Organizations
    filters:
      and:
        - file.inFolder("entities/organization")
    order:
      - file.name
      - name
      - aliases
      - formula.documents
  - type: table
    name: Single-source entities (review)
    filters:
      and:
        - file.inFolder("entities")
        - appears_in.length == 1
    order:
      - file.name
      - name
      - type
  - type: table
    name: Possible duplicate documents
    filters:
      and:
        - file.inFolder("documents")
        - near_duplicate_of
    order:
      - file.name
      - near_duplicate_of
"""

