"""Shared constants, path globals, and utility helpers used across cmd modules."""

import json
import os
import subprocess
import sys
from collections import Counter  # noqa: F401 — re-exported for cmd modules
from pathlib import Path

from watchdog.appmode import hint as _hint
from watchdog.vault_paths import PAGE_NOTES_HOOKS, PAGE_NOTES_MATCHER, SESSION_HOOK_COMMAND, incoming_dir, is_set_aside, is_vault
from watchdog.model_catalog import _MODEL_IDS, resolve_model_id  # noqa: F401 — re-exported
from watchdog.pipeline.json_io import _read_json
from watchdog.pipeline.write_vault import slugify  # noqa: F401 — re-exported
# Colour constants and their gating logic live in watchdog.terminal (#636) — a neutral module
# with no dependency on this package — re-exported here so the ~40 existing call sites across
# cmd/*.py that do `from watchdog.cmd.base import _BOLD, ...` keep working unchanged.
from watchdog.terminal import (  # noqa: F401 — re-exported
    _COLOR, _color_enabled, _BOLD, _DIM, _CYAN, _YELLOW, _GREEN, _RESET,
)
from watchdog.vault_paths import preprocessing_lock, processing_lock

WATCHDOG_HOME = Path.home() / ".watchdog"
PROJECTS_FILE = WATCHDOG_HOME / "projects.json"
CONFIG_FILE   = WATCHDOG_HOME / "config.json"

VAULT_SCHEMA_VERSION = "1"

_ALIASES = {
    "init":       "new",
    "create":     "new",
    "ls":         "list",
    "info":       "status",
    "inspect":    "status",
    "version":    "about",
    "config":     "configure",
    "setting":    "configure",
    "find":       "search",
    "health":     "doctor",
    "check":      "doctor",
    "telemetry":  "usage",
    "process":    "chew",
    "preprocess": "chew",
    "prep":       "chew",
    "remove":     "delete",
    "rm":         "delete",
    "mv":         "move",
    "rn":         "rename",
}

# Old command names kept working during a deprecation window rather than removed outright
# (#441, D138) — unlike `_ALIASES` (silent, permanent shortcuts), these print a warning
# before remapping, since the goal is to move people onto the new name, not hide it forever.
# `extract`/`finalize` renamed straight across to `dig`/`bark` (same flags, same function);
# `ingest` has no such 1:1 successor — it combined extract+finalize into one non-interactive
# shot, a role now taken by `watchdog add` and manual `dig`+`bark` — so it
# keeps its own subparser and is flagged separately in `main()`, not remapped here.
_DEPRECATED_ALIASES = {
    "extract":  "dig",
    "finalize": "bark",
}

# Pipeline modules that keep a command-line entry point because a vault's Claude Code session
# runs them (/watchdog-entity). Everything else in pipeline/ is called as functions.
_PIPELINE_COMMANDS = {
    "write-entity":  ("watchdog.pipeline.write_entity",   "watchdog-write-entity"),
}

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "vault"

_VAULT_PERMISSIONS = [
    # watchdog commands the in-Claude-Code skills run (extraction is a terminal command —
    # `watchdog dig` / `watchdog bark` — and needs no in-vault Bash permissions).
    "Bash(watchdog write-entity --entity-id *)",
    "Bash(watchdog timeline)",
    # /watchdog-query's semantic lane and /watchdog-surface's deterministic lead sweep — both
    # read-only, both run every session, so a prompt on each call was pure friction.
    "Bash(watchdog search *)",
    "Bash(watchdog leads)",
    # The query/wiki/surface/research skills check a page's fact citations before filing it (D283);
    # read-only, and confined to the investigation it runs in.
    "Bash(watchdog check-citations*)",
    # /watchdog-context proposes watchlist seed terms (#229); the deterministic append+dedup
    # lives in this command, not the skill hand-editing watchlist.md.
    "Bash(watchdog watchlist-add *)",
    # /watchdog-surface promotes a journalist-confirmed contradiction candidate into the note
    # via this internal command (#312, D82/D83) — pre-approved so the confirmed promotion runs
    # without a second permission prompt; the journalist's explicit confirmation is the gate.
    "Bash(watchdog contradiction-add *)",
    # WebSearch / WebFetch are deliberately NOT here — they make outbound requests, so they are
    # pre-approved only by the watchdog-research skill's own `allowed-tools` frontmatter, scoped to
    # when /watchdog-research is active. Archival downloads run as a deterministic post-flight of
    # `watchdog research` (in the terminal, ungated), never from the skill (#186, D45).
    # File-permission checks only match Edit(path) rules — Edit(path) covers every file-editing
    # tool, Write included.
    # Scratch space: the /watchdog-entity refresh JSON.
    "Edit(.watchdog/tmp/**)",
    # durable web-research worklist (#196) — the skill writes queued URLs here (not tmp/, which
    # setup sweeps), so a crashed session's queue survives.
    "Edit(.watchdog/research/**)",
    # session-authored pages (compounding queries → wiki threads, surface/research reports)
    "Edit(queries/**)",
    "Edit(wiki/**)",
    "Edit(briefings/**)",
    "Edit(context.md)",
]

# Watchdog's own config and keys, denied to a vault's sessions outright (D257). These rules govern
# Claude Code's file tools; commands the vault pre-approves confine themselves in code.
_VAULT_DENY = [
    "Read(~/.watchdog/**)",
    "Edit(~/.watchdog/**)",
]

# Rules older vaults were created with that `refresh-skills` now removes. Entity and document
# notes, the morgue, the registry and the timeline are pipeline-owned (D81): no skill writes them
# directly, and pre-approving edits there let a prompt-injected session rewrite them without a
# prompt (I6). The ingest-era commands are no longer run from a session at all.
_RETIRED_VAULT_PERMISSIONS = {
    "Bash(watchdog entity-index)",
    "Bash(watchdog queue-status)",
    "Bash(watchdog is-duplicate *)",
    "Bash(watchdog unlock*)",
    "Edit(.watchdog/registry/**)",
    "Edit(.watchdog/timeline/**)",
    "Edit(entities/**)",
    "Edit(documents/**)",
    "Edit(morgue/**)",
    "Edit(hot.md)",
    "Edit(log.md)",
    "Edit(.obsidian/graph.json)",
}

# The UserPromptSubmit hook each vault runs before every prompt: a one-line nudge when documents
# are waiting for `dig` or `bark`. It calls an internal watchdog command rather than an inline
# `python3 -c` one-liner, which assumed `python3` was on PATH (often not on Windows) and counted
# every queue file — including ones already dug — as "ready for extraction".
_PROMPT_HOOK_COMMAND = "watchdog prompt-status"
_LEGACY_PROMPT_HOOK_MARKER = "ready for extraction — run watchdog dig in your terminal"


def _vault_settings() -> dict:
    """The `.claude/settings.json` a new vault starts with (and that `refresh-skills` brings an
    existing vault's settings up to)."""
    return {
        "permissions": {
            "allow": list(_VAULT_PERMISSIONS),
            # Read/Glob/Grep are unrestricted by default in Claude Code — no permission
            # prompt, any path the OS user can read — so without this, a session inside
            # one vault could silently read another vault, ~/.watchdog/credentials.json
            # (API keys), or anything else on the account. This confines them to the
            # vault the session was launched in (I6: source documents are adversarial by
            # assumption, so a prompt-injected one could otherwise direct an exfiltrating
            # read with no permission prompt to catch it).
            "blockReadsOutsideWorkingDirectories": True,
            # An explicit deny on Watchdog's own config and keys, which holds whatever the
            # read-scope setting above does or doesn't cover.
            "deny": list(_VAULT_DENY),
        },
        "hooks": {
            # A primer of where the investigation stands, built by code from the vault's records
            # (D285), loaded at the start of a session and again after compaction, which drops
            # hook-injected context. SessionStart stdout is added to Claude's context.
            "SessionStart": [
                {"matcher": "startup|resume|compact",
                 "hooks": [{"type": "command", "command": SESSION_HOOK_COMMAND}]},
            ],
            "UserPromptSubmit": [
                {"matcher": "", "hooks": [{"type": "command", "command": _PROMPT_HOOK_COMMAND}]},
            ],
            # The reporter's Notes on a saved page survive a session's rewrite (D296).
            **{event: [{"matcher": PAGE_NOTES_MATCHER, "hooks": [{"type": "command", "command": command}]}]
               for event, command in PAGE_NOTES_HOOKS.items()},
        },
    }


def _prompt_status_line(vault: Path) -> str | None:
    """The nudge `watchdog prompt-status` prints, or None when nothing is waiting."""
    dig, bark = _count_awaiting_dig(vault), _count_awaiting_bark(vault)
    parts = []
    if dig:
        parts.append(f"{dig} file(s) ready for extraction — run watchdog dig")
    if bark:
        parts.append(f"{bark} file(s) extracted and awaiting watchdog bark")
    return "WATCHDOG: " + "; ".join(parts) + " (in your terminal)" if parts else None


_CMD_HELP: dict[str, dict] = {
    # Per-command prose for `watchdog <cmd> --help`: a one-line description and optional
    # notes. Arguments and options are read from the argparse parser itself
    # (`_print_cmd_help`), so the help can never drift from what the parser accepts.
    'register': {
        "desc": 'Register an existing vault folder with watchdog',
    },
    'new': {
        "desc": 'Create a new investigation vault',
    },
    'ingest': {
        "desc": '[deprecated] Extract queued documents (runs the Python pipeline) — use `watchdog add`, or `watchdog dig` then `watchdog bark` instead',
    },
    'add': {
        "desc": 'Add documents to the vault: copy them into incoming/, chew, extract and finish the batch in one step',
        "notes": [
            'With no files, add whatever is waiting: files in incoming/, chewed documents, or a',
            'batch that was interrupted. Originals passed by path stay where they are.',
            '',
            'Stops only for the public-records acknowledgement and for a provider refusing your',
            'key or account. `watchdog settings auto_approve true` skips the acknowledgement',
            'when every step runs on your Claude subscription. A rate limit pauses the run until',
            'it resets.',
        ],
    },
    'context': {
        "desc": 'Open Claude Code to seed investigation context from context/',
    },
    'obsidian': {
        "desc": 'Open an investigation vault in Obsidian',
    },
    'open': {
        "desc": 'Open an investigation in Obsidian',
        "notes": [
            'Omit the name when you are inside the investigation. With --folder, opens the',
            'folder in Finder or your file explorer instead, as `watchdog open` did before.',
        ],
    },
    'archive': {
        "desc": 'Archive a completed investigation (hidden from watchdog projects list)',
    },
    'unarchive': {
        "desc": 'Restore an archived investigation',
    },
    'rename': {
        "desc": 'Rename an investigation (folder and registry)',
    },
    'describe': {
        "desc": 'Set or update an investigation description',
    },
    'move': {
        "desc": 'Update vault path in registry',
    },
    'delete': {
        "desc": 'Remove an investigation from registry',
    },
    'chew': {
        "desc": 'Process documents in incoming/ and prepare them for ingestion',
    },
    'watch': {
        "desc": 'Watch incoming/ and chew files automatically as they arrive',
    },
    'log': {
        "desc": 'Show ingest history for an investigation',
    },
    'list': {
        "desc": 'List all registered investigations',
    },
    'status': {
        "desc": 'Show detailed status for an investigation',
    },
    'search': {
        "desc": 'Semantic search across ingested documents',
        "notes": [
            'Searches by meaning, not keywords: "conflict of interest" surfaces passages about',
            'recusals or related-party dealings even when that phrase never appears. Returns the',
            'matching source passage with its page — not a generated answer.',
            '',
            'Steer with +/-: lead a phrase with - to push away from it, + to pull toward another',
            'idea. The whole phrase up to the next +/- is one term (no quotes needed); a hyphenated',
            'word like no-bid stays intact.',
            '    watchdog search "shell company -real estate"',
            '    watchdog search "consulting fee +offshore -salary"',
            '',
            'Scores run 0–1 and are relative: a strong conceptual match sits around 0.5–0.65,',
            "below ~0.4 is usually noise. There's no universal cutoff — tune --threshold to your",
            'corpus. (A +/- query shifts the scale lower, so judge those by ranking, not score.)',
        ],
    },
    'leads': {
        "desc": 'Surface investigative leads from the entity graph (deterministic, no model)',
        "notes": [
            'Reads the entity registry and reports, with no model call: entities named as a',
            'relationship target but never profiled, entities recurring across documents with no',
            'relationships, and entities carrying unresolved contradiction flags.',
            '',
            'The same sweep runs at the end of every ingest run, writing the full report',
            'to briefings/leads-<date>.md; this command re-runs it on demand between ingests.',
        ],
    },
    'ask': {
        "desc": 'Open a Claude Code session to ask questions about the vault',
        "notes": [
            'Opens Claude Code inside the investigation, so you can go back and forth with',
            'Claude about the documents. With a question, the session starts by answering it',
            'through /watchdog-query: a cited answer from the vault, saved to queries/ when it is',
            'substantive. The session then stays open for follow-ups. Without a question, it',
            'opens ready for one. Exit with Ctrl-D.',
            '',
            'The session uses whatever Claude Code is signed in with (your subscription or key),',
            'not the model provider set for adding documents.',
            '',
            'Examples:',
            '    watchdog ask',
            '    watchdog ask "who signed the 2021 loan agreement?"',
            '    watchdog ask --project city-hall "what changed in the latest filings?"',
        ],
    },
    'review': {
        "desc": 'Step through what is waiting on you, one item at a time',
        "notes": [
            'Shows each open contradiction, lead, watch-list hit and possible duplicate document',
            'in turn. For each one, mark it handled, keep it open, or open its note in Obsidian.',
            'Handled items stop appearing in briefings, reports and the home screen;',
            '`watchdog review unresolve <id>` brings one back.',
            '',
            'Name a kind to review only that: contradictions, leads, alerts or duplicates.',
            'Piped or run without a terminal, it prints the list with each resolution id instead.',
            '',
            'Related commands:',
            '    watchdog review resolve <id…>       mark items handled by their ids (--sync, --list)',
            '    watchdog review unresolve <id…>     bring handled items back',
            '    watchdog review watchlist           sweep every document against watchlist.md',
            '    watchdog review merge-entities <keep-id> <merge-id>',
            '    watchdog review add-contradiction <entity-id>',
            '',
            'Examples:',
            '    watchdog review',
            '    watchdog review contradictions',
        ],
    },
    'merge-entities': {
        "desc": 'Merge a duplicate entity into another, deterministically',
        "notes": [
            'Must be run from inside the vault. Unions aliases, appears_in, roles, and timeline',
            'events onto keep-id; remaps every role.target_id across the whole registry that',
            'pointed at merge-id (not just the two entities involved); concatenates the losing',
            "note's Analysis into the survivor's with provenance intact; and redirects the losing",
            'note to a stub linking to the survivor. No model calls.',
            '',
            'Prints both entities (name, type, document/relationship counts) and asks for',
            'confirmation before doing anything — this is irreversible. Answering anything other',
            'than y/yes cancels with no changes made; pass --force to skip the prompt.',
            '',
            "This is the fix for duplicate entities that `/watchdog-health` or a review of the",
            "dashboard's single-source entities turns up. Run `watchdog reindex` afterward to drop",
            "the merged entity's stale search-index entries.",
        ],
    },
    'contradiction-add': {
        "desc": 'Promote a verified surface-found contradiction into an entity note',
        "notes": [
            'Must be run from inside the vault. `/watchdog-surface` reports cross-document',
            'contradictions as labelled candidates rather than writing callouts into entity',
            'notes, which are pipeline-owned (D81). Once you have verified a candidate against',
            "the sources, this writes it into the entity's ## Contradictions section through the",
            "pipeline's own note builder, in the exact format extraction emits — so the callout",
            'is tracked by the resolutions layer and `watchdog review resolve` / `unresolve` work on it',
            'like any pipeline-emitted one. No model calls.',
            '',
            'Validates that the entity id and both document slugs exist before writing; a callout',
            'already present is a no-op. `/watchdog-surface` can run this after explicit',
            'journalist confirmation when promoting a candidate.',
        ],
    },
    'research': {
        "desc": "Open Claude Code to research the vault's open questions on the web",
        "notes": [
            "Seeded by the vault's entities, leads, and gaps, Claude conducts bounded web research",
            'and queues the sources it finds; when the session ends, watchdog downloads them into',
            'incoming/ — so findings flow through the normal chew → ingest pipeline. Claude never',
            'writes vault notes directly. After the download, run `watchdog add` to fold the',
            'sources into the vault.',
            '',
            'Already have the links? `watchdog research fetch <urls or file>` downloads them',
            'into incoming/ without a research session.',
        ],
    },
    'watchlist': {
        "desc": 'Sweep the whole vault against watchlist.md (deterministic, no model)',
        "notes": [
            "Reads every document already in documents.json — not just the current run's — and",
            "scans each one's morgue text against watchlist.md, exactly like the per-ingest scan",
            '(D35). For when a term is added to the watchlist after documents were already',
            "ingested and you want the whole vault swept, not just what's ingested from now on.",
            '',
            'Writes to the same briefings/alerts-<date>.md as the per-run scan (appending if the',
            'file already exists). Since it has no memory of prior scans, a full sweep re-reports',
            'every past hit each time it runs — expected, not a bug.',
        ],
    },
    'fetch': {
        "desc": 'Download a batch of URLs (or a links file) into incoming/',
        "notes": [
            'For when you already have a list of links — from a spreadsheet, a colleague, your own',
            'browsing — and just want them pulled into the pipeline, no research session needed. Each',
            'URL runs through the same egress hygiene as research sources (public host only, size cap,',
            'scripts stripped) and lands as a document + provenance sidecar. Then run `watchdog chew`',
            'and `watchdog dig`. Archives to the Wayback Machine too when wayback_save is on.',
        ],
    },
    'usage': {
        "desc": 'Per-call token/cost/latency breakdown for ingest runs (deterministic, no model)',
        "notes": [
            'Reads `.watchdog/registry/usage/usage-<ts>.json`, written after every ingest run',
            '(`watchdog dig`/`watchdog bark`), and groups calls by stage (classifier/extractor/',
            "finalizer, matching the CLI's own --classifier-model/--extractor-model/",
            '--finalizer-model flags). Extractor rows show the filename and page range (or',
            'section) each call covered.',
            '',
            'Cost is read directly from each record — model_client computes cost_usd',
            'authoritatively at call time, so there is no local pricing table to keep in sync.',
            "Also reports each call's wall-clock latency, and cost per page across the vault's",
            'whole document registry (not just the run being analyzed).',
        ],
    },
    'export': {
        "desc": 'Export the entity/relationship graph for Neo4j, Gephi, or NetworkX',
        "notes": [
            'Reads the entity registry and emits a graph — no model calls, fully deterministic.',
            'csv writes nodes.csv + relationships.csv for `neo4j-admin database import` (also',
            'loadable in Gephi); cypher writes a single graph.cypher of MERGE statements.',
            '',
            'Only stated-direction relationships are emitted (the auto-generated reverse edges are',
            'skipped), and edges to entities that were never profiled are dropped so the import',
            'stays valid. Graph quality is bounded by ingest-time entity deduplication.',
        ],
    },
    'timeline': {
        "desc": 'Rebuild timeline.md from canonical .watchdog/timeline/ files',
    },
    'unlock': {
        "desc": 'Release a stale chew or ingest lock',
    },
    'gui': {
        "desc": 'Open the Watchdog desktop app',
        "notes": [
            'Uses WATCHDOG_APP (a path to the app) if set, then the installed app on macOS, then',
            "the app's dev server when run from a repository checkout.",
        ],
    },
    'setup': {
        "desc": 'Set up Watchdog after installation',
    },
    'configure': {
        "desc": 'View or change configuration',
    },
    'doctor': {
        "desc": 'Check all registered investigations for missing or broken vaults',
    },
    'about': {
        "desc": 'Show version and project links',
    },
}


def _detected_install_manager() -> str:
    """Best-effort detection of which packaging tool manages this watchdog install, so a
    printed follow-up command for adding an optional extra afterwards (`pipx inject` vs
    `uv tool install --with`) matches how it was actually installed (#610). Inspects
    sys.executable's private-venv path — pipx: `.../pipx/venvs/<pkg>/bin/python`, uv tool:
    `.../uv/tools/<pkg>/bin/python` — and defaults to pipx, the long-documented default,
    when neither layout is recognized."""
    parts = Path(sys.executable).parts
    if "uv" in parts and "tools" in parts:
        return "uv"
    return "pipx"


def _extra_install_cmd(extra: str) -> str:
    """Command that adds an optional extra (e.g. "playwright", "gliner") to an
    already-installed watchdog, matching whichever tool installed it. Re-running `uv tool
    install` with a new `--with` picks it up on its own — confirmed against uv 0.12 — so no
    `--reinstall`/`--force` flag is needed, mirroring how plain `pipx inject` needs none either."""
    if _detected_install_manager() == "uv":
        return f"uv tool install watchdog-intel --with {extra}"
    return f"pipx inject watchdog-intel {extra}"


def _venv_bin(name: str) -> str:
    """Path to `name` inside watchdog's own install venv if it's there — pipx and uv tool both
    put an injected extra's console scripts alongside watchdog's own interpreter, off PATH —
    else the bare name, so a plain PATH lookup still has a chance."""
    candidate = Path(sys.executable).parent / name
    return str(candidate) if candidate.exists() else name


def _render_template(filename: str, **vars: str) -> str:
    text = (_TEMPLATES_DIR / filename).read_text()
    for key, value in vars.items():
        text = text.replace("{" + key + "}", value)
    return text


def load_projects() -> dict:
    if not PROJECTS_FILE.exists():
        return {}
    with open(PROJECTS_FILE) as f:
        try:
            return json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            sys.exit(f"Error: projects file is corrupt — {e}"
                     + _hint("\nRun 'watchdog setup --force'.", f"\nFix or remove {PROJECTS_FILE}."))


def _project_completer(prefix, parsed_args, **kwargs):
    return {slug: info["name"] for slug, info in load_projects().items()}


def save_projects(projects: dict) -> None:
    WATCHDOG_HOME.mkdir(parents=True, exist_ok=True)
    with open(PROJECTS_FILE, "w") as f:
        json.dump(projects, f, indent=2)
        f.write("\n")


def load_config() -> dict:
    """`config.json` as a dict — `{}` when it doesn't exist yet. A corrupt file exits with a clear
    error instead of being silently treated as empty, which ran `dig`/`bark` on the default models
    and provider with no hint that the user's settings were being ignored."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        data = _read_json(CONFIG_FILE)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        sys.exit(f"Error: config file is corrupt — {e}\nFix or remove {CONFIG_FILE}"
                 + _hint(", or run 'watchdog setup --force'.", "."))
    return data if isinstance(data, dict) else {}


def _projects_dir() -> Path:
    default = Path.home() / "Investigations"
    if CONFIG_FILE.exists():
        try:
            config = _read_json(CONFIG_FILE)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            sys.exit(f"Error: config file is corrupt — {e}"
                     + _hint("\nRun 'watchdog setup --force'.", f"\nFix or remove {CONFIG_FILE}."))
        # config.json can legitimately omit "projects_dir", or carry it as "" / null (e.g. a
        # config written before that key existed, or hand-edited to set only one other knob) —
        # `or default` treats any falsy value the same as a missing one, since `config.get(...,
        # default)` alone only covers the missing-key case: an explicit "" would otherwise pass
        # through to Path("").expanduser(), which is the cwd ('.'), silently creating vaults
        # wherever the command happened to be run from.
        return Path(config.get("projects_dir") or default).expanduser()
    return default


def _fmt_date(iso: str) -> str:
    try:
        return iso[:10]
    except Exception:
        return "—"


def _vault_size(vault: Path) -> int:
    """Total bytes under `vault`. `os.scandir` reuses the directory listing's own metadata where the
    platform provides it, instead of a separate stat per file — `watchdog list` sizes every vault."""
    total = 0
    stack = [str(vault)]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def _fmt_size(n: int) -> str:
    for unit, threshold in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= threshold:
            return f"{n / threshold:.1f}{unit}"
    return f"{n}B"


def _check_project_health(info: dict) -> str | None:
    vault = Path(info["path"])
    if not vault.exists():
        return "folder not found"
    if not is_vault(vault):
        return "not a watchdog vault"
    return None


def _load_registry(vault: Path) -> dict | None:
    """Returns None only when registry.json doesn't exist yet (nothing ingested). A corrupt
    file raises json.JSONDecodeError rather than being swallowed as "no registry" (#636) —
    callers that check one project (cmd_register, cmd_status) should let that propagate as a
    clean sys.exit; callers that loop over every registered project (cmd_list, cmd_doctor)
    should catch it per-project so one corrupt vault doesn't abort the whole command."""
    reg = vault / ".watchdog" / "registry" / "registry.json"
    if not reg.exists():
        return None
    return json.loads(reg.read_text())


def _ensure_layout(vault: Path) -> None:
    """Rename an older vault's `_INCOMING/` and `_CONTEXT/` folders (D266) the first time a
    command touches it, with a one-line note. Quiet and cheap once the vault is current."""
    from watchdog.vault_paths import ensure_current_layout_once
    ensure_current_layout_once(vault, say=lambda msg: print(f"\n  {_DIM}{msg}{_RESET}"))


def _count_incoming(vault: Path) -> int:
    incoming = incoming_dir(vault)
    if not incoming.exists():
        return 0
    count = 0
    for root, dirs, files in os.walk(incoming):
        rel_parts = Path(root).relative_to(incoming).parts
        if is_set_aside(rel_parts):
            dirs.clear()
            continue
        count += sum(1 for f in files if not f.startswith(".") and not f.endswith(".yml"))
    return count


def _count_queued(vault: Path) -> int:
    queue = vault / ".watchdog" / "queue"
    if not queue.exists():
        return 0
    return sum(1 for f in queue.iterdir() if f.suffix == ".json")


def _count_awaiting_dig(vault: Path) -> int:
    """Queued documents with no staged extraction yet — chewed but not yet dug (#461). A queue
    file persists until `bark` commits it (`orchestrate._commit_extracted`), so post-dig/bark
    split `_count_queued` alone conflates "not yet dug" with "dug, awaiting bark" — this and
    `_count_awaiting_bark` split it by whether a matching `.watchdog/extracted/<sha>.json`
    (staged by `dig`) exists for that queue entry."""
    queue = vault / ".watchdog" / "queue"
    if not queue.exists():
        return 0
    extracted = vault / ".watchdog" / "extracted"
    return sum(1 for f in queue.iterdir()
              if f.suffix == ".json" and not (extracted / f.name).exists())


def _count_awaiting_bark(vault: Path) -> int:
    """Queued documents already dug (staged extraction present) but not yet committed via
    `bark` — see `_count_awaiting_dig`."""
    queue = vault / ".watchdog" / "queue"
    if not queue.exists():
        return 0
    extracted = vault / ".watchdog" / "extracted"
    return sum(1 for f in queue.iterdir()
              if f.suffix == ".json" and (extracted / f.name).exists())


def _warn_pending_research(vault: Path) -> None:
    """Warn when web-research URLs are queued but not downloaded (#196). A crashed research session
    leaves them in the durable worklist; bare `watchdog`, `chew`, and `status` surface them so they
    aren't silently lost. No-op when none are pending."""
    from watchdog.pipeline import research
    n = research.pending_count(vault)
    if n:
        print(f"  {_YELLOW}{n} research URL{'s' if n != 1 else ''}{_RESET} queued but not downloaded "
              + _hint(f"{_DIM}— run{_RESET} {_CYAN}watchdog research-fetch{_RESET}",
                      f"{_DIM}— they stay queued; download them from Web research.{_RESET}"))


def _resolve_vault(project: str | None) -> tuple[str, dict, Path]:
    """(slug, info, vault_path) from a name arg, or the current directory.

    Used by read-only, whole-vault commands (export, leads): a name resolves through the
    registry; with no name the cwd must itself be a vault. A vault opened by path that isn't
    registered still works — it falls back to a synthetic info dict keyed on the folder name."""
    if project:
        slug, info = _find_project(project)
        return slug, info, Path(info["path"])

    cwd = Path(".").resolve()
    if not is_vault(cwd):
        sys.exit("Error: not inside a Watchdog vault — provide an investigation name.")
    for slug, info in load_projects().items():
        if Path(info["path"]).resolve() == cwd:
            return slug, info, cwd
    return slugify(cwd.name), {"name": cwd.name, "path": str(cwd)}, cwd


def _registered_project(name: str | None, command: str) -> dict:
    """The registry entry for `name`, or for the current directory when no name is given — which
    must then be a registered vault. `command` names the command in the error message."""
    if name:
        return _find_project(name)[1]
    cwd = Path(".").resolve()
    if not is_vault(cwd):
        sys.exit(_hint(f"Error: not inside a watchdog project. Run `watchdog {command} <name>` or cd "
                       f"into a project first.", "Error: this folder is not an investigation."))
    info = next((v for v in load_projects().values() if Path(v["path"]).resolve() == cwd), None)
    if info is None:
        sys.exit("Error: current directory is a vault but not registered."
                 + _hint(" Run `watchdog projects register` first.", " Add it as an investigation first."))
    return info


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
            sys.exit(f"Project not found: {name}"
                     + _hint("\nRun 'watchdog projects list' to see all projects.", ""))
    return slug, projects[slug]


def _notify(title: str, body: str) -> None:
    if sys.platform != "darwin":
        return
    try:
        # Title and body go in as script arguments, never spliced into the AppleScript source,
        # so a quote in a project name can't break (or rewrite) the script.
        subprocess.run(
            ["osascript", "-e", "on run argv", "-e",
             "display notification (item 2 of argv) with title (item 1 of argv)",
             "-e", "end run", title, body],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


def _launch_claude(vault: Path, prompt: str | None = None, model: str | None = None) -> None:
    try:
        os.chdir(vault)
        cmd = ["claude"]
        if model:
            cmd += ["--model", resolve_model_id(model)]
        if prompt:
            cmd.append(prompt)
        os.execvp("claude", cmd)
    except FileNotFoundError:
        sys.exit("Error: Claude Code not found — install from https://claude.ai/download")


def _check_vault_locks(vault: Path, slug: str) -> None:
    """Refuse to rename, move or delete an investigation while a run holds it. A lock whose run
    died, or one past the age window when its run can't be checked, holds nothing (D293)."""
    from watchdog.pipeline import locks
    for lock, stage, app_stage in ((preprocessing_lock(vault), "chew", "pre-processing"),
                                   (processing_lock(vault), "ingest", "adding documents")):
        holder = locks.held(lock)
        if holder is None:
            continue
        elsewhere = holder["where"] == "elsewhere"
        where = f" on {holder.get('host') or 'another computer'}" if elsewhere else ""
        sys.exit(_hint(f"Error: {stage} is in progress{where}. Wait for it to finish, or if it is "
                       f"stuck run: watchdog unlock {slug} --force",
                       f"Error: {app_stage} is in progress"
                       f"{' on another computer' if elsewhere else ''}. "
                       "Try again when it has finished."))


def _flag_label(action) -> str:
    """`--name METAVAR` (or `-q Q, --question Q`) for one argparse optional action."""
    if action.nargs == 0:
        return ", ".join(action.option_strings)
    metavar = action.metavar or (action.dest.upper() if not action.choices else
                                 "|".join(str(c) for c in action.choices))
    if action.nargs in ("?", "*"):
        metavar = f"[{metavar}]"
    return ", ".join(f"{o} {metavar}" for o in action.option_strings)


def _print_cmd_help(cmd: str, subparser, desc: str | None = None) -> None:
    """Render `watchdog <cmd> --help` in the CLI's own style. Arguments and options come from
    `subparser` (the real argparse parser for `cmd`); only the description and notes come from
    `_CMD_HELP`, so a flag can't be accepted but missing from the help, or vice versa."""
    import argparse
    info = _CMD_HELP.get(cmd, {})
    arg_defs, opts = [], []
    for action in subparser._actions:
        if isinstance(action, argparse._HelpAction) or action.help == argparse.SUPPRESS:
            continue
        text = (action.help or "").replace("%%", "%")
        if action.option_strings:
            opts.append((_flag_label(action), text))
        else:
            arg_defs.append((action.metavar or action.dest, text, action.nargs in ("?", "*")))
    usage_parts = ["watchdog", cmd]
    for name, _, optional in arg_defs:
        usage_parts.append(f"[{name}]" if optional else f"<{name}>")
    if opts:
        usage_parts.append("[options]")
    import shutil
    import textwrap
    # Labels longer than the cap print their description on the next line instead of pushing
    # every row's description to the right.
    width = min(26, max([len(a[0]) for a in arg_defs] + [len(flag) for flag, _ in opts] + [len("--help")]))
    cols = max(60, min(shutil.get_terminal_size((100, 24)).columns, 110))

    def _row(label: str, text: str) -> None:
        indent = " " * (4 + width + 1)
        body = textwrap.wrap(text, cols - len(indent)) or [""]
        if len(label) > width:
            print(f"    {_CYAN}{label}{_RESET}")
            lines = body
        else:
            print(f"    {_CYAN}{label:<{width}}{_RESET} {body[0]}")
            lines = body[1:]
        for line in lines:
            print(f"{indent}{line}")

    print(f"\n  {info.get('desc') or desc or subparser.description or ''}")
    print()
    print(f"  {_DIM}Usage:  {' '.join(usage_parts)}{_RESET}")
    if arg_defs:
        print()
        print(f"  {_BOLD}Arguments{_RESET}")
        for name, text, optional in arg_defs:
            _row(name, text + ("  (optional)" if optional else ""))
    print()
    print(f"  {_BOLD}Options{_RESET}")
    for flag, text in opts:
        _row(flag, text)
    _row("--help", "Show this message and exit")
    notes = info.get("notes", [])
    if notes:
        print()
        print(f"  {_BOLD}Notes{_RESET}")
        for line in notes:
            print(f"    {_DIM}{line}{_RESET}")
    print()


def _print_banner() -> None:
    print(f"\n  🔍🐕  {_BOLD}Watchdog{_RESET} — investigative document intelligence")
    print()
    print(f"  {_DIM}Usage:  watchdog <command> [options]{_RESET}")
    print()
    groups = [
        ("Get documents in", [
            ("add",        "Add files or folders: read, extract, and write the briefing"),
        ]),
        ("Work the investigation", [
            ("ask",        "Ask questions about the vault in a Claude Code session"),
            ("search",     "Search documents by meaning and exact wording"),
            ("review",     "Step through contradictions, leads, watch-list hits and duplicates"),
            ("open",       "Open the investigation in Obsidian"),
            ("research",   "Research open questions on the web"),
        ]),
        ("Manage", [
            ("new",        "Create a new investigation"),
            ("projects",   "List, rename, move, archive or delete investigations"),
            ("settings",   "Models, keys, health checks and skills"),
            ("setup",      "Set up Watchdog after installation"),
        ]),
    ]
    for group_name, cmds in groups:
        print(f"  {_BOLD}{group_name}{_RESET}")
        for cmd, desc in cmds:
            print(f"    {_CYAN}{cmd:<15}{_RESET} {desc}")
        print()
    print(f"  {_DIM}Run{_RESET} {_CYAN}watchdog{_RESET} {_DIM}inside an investigation for what's waiting on you."
          f"{_RESET}")
    print(f"  {_DIM}Add --help after any command for its options;{_RESET} "
          f"{_CYAN}watchdog help maintenance{_RESET} {_DIM}lists the rest.{_RESET}")
    print()
