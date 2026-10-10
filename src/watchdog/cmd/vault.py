"""Vault lifecycle commands: create, list, status, obsidian, archive, etc."""

import json
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from watchdog.vault_paths import is_vault
from watchdog.appmode import hint as _hint
from watchdog.cmd.base import (
    VAULT_SCHEMA_VERSION,
    _BOLD, _CYAN, _DIM, _GREEN, _RESET, _YELLOW,
    _check_project_health,
    _count_awaiting_bark,
    _count_awaiting_dig,
    _count_incoming,
    _warn_pending_research,
    _find_project,
    _registered_project,
    _fmt_date,
    _fmt_size,
    _load_registry,
    _vault_size,
    load_projects,
    save_projects,
    slugify,
)
from watchdog.pipeline.json_io import _read_json, _read_json_or


_WATCHLIST_TEMPLATE = """\
# Watch list
#
# One term per line. When a newly-ingested document mentions a term listed here,
# Watchdog writes an alert to briefings/alerts-<date>.md and flags it in the terminal.
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
_DASHBOARD_BASE = """\
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


def _obsidian_config_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "obsidian" / "obsidian.json"
    elif sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "obsidian" / "obsidian.json"
    else:
        # An unset or empty XDG_CONFIG_HOME means ~/.config. (`Path("")` is ".", which is truthy,
        # so `Path(env or "") or default` would write obsidian.json under the working directory.)
        xdg = os.environ.get("XDG_CONFIG_HOME")
        cfg_home = Path(xdg) if xdg else Path.home() / ".config"
        return cfg_home / "obsidian" / "obsidian.json"


def _obsidian_registered(vault: Path) -> bool:
    cfg = _obsidian_config_path()
    if not cfg.exists():
        return False
    try:
        data = json.loads(cfg.read_text())
        return any(v.get("path") == str(vault) for v in data.get("vaults", {}).values())
    except Exception:
        return False


def _register_obsidian_vault(vault: Path) -> None:
    cfg = _obsidian_config_path()
    try:
        data = json.loads(cfg.read_text()) if cfg.exists() else {"vaults": {}}
        data.setdefault("vaults", {})[secrets.token_hex(8)] = {
            "path": str(vault),
            "ts": int(datetime.now(timezone.utc).timestamp() * 1000),
        }
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps(data))
    except Exception:
        pass  # non-fatal — user can register manually via watchdog open


def _obsidian_vault_ts(vault: Path):
    """The registration timestamp (ms since epoch) for `vault` in obsidian.json, or None."""
    cfg = _obsidian_config_path()
    if not cfg.exists():
        return None
    try:
        data = json.loads(cfg.read_text())
        for v in data.get("vaults", {}).values():
            if v.get("path") == str(vault):
                return v.get("ts")
    except Exception:
        return None
    return None


def _obsidian_launch_epoch():
    """Epoch seconds when the running Obsidian was launched, or None if it is not
    running or the start time can't be determined.

    Obsidian reads obsidian.json only at startup, so a vault registered after
    launch is invisible to the running instance until it is restarted.
    """
    if sys.platform == "win32":
        return None
    try:
        out = subprocess.run(["pgrep", "-x", "Obsidian"], capture_output=True, text=True)
    except Exception:
        return None
    pids = [p for p in out.stdout.split() if p.strip()]
    if not pids:
        return None
    starts = []
    for pid in pids:
        try:
            r = subprocess.run(["ps", "-o", "lstart=", "-p", pid], capture_output=True, text=True)
            s = r.stdout.strip()
            if s:
                starts.append(datetime.strptime(s, "%a %b %d %H:%M:%S %Y").timestamp())
        except Exception:
            continue
    return min(starts) if starts else None


def cmd_register(args):
    from watchdog.ops.projects import _register
    return _register(args)


def _pkg_version() -> str:
    """Installed watchdog-intel version, for stamping into a new vault's README."""
    try:
        from importlib.metadata import version, PackageNotFoundError
        try:
            return version("watchdog-intel")
        except PackageNotFoundError:
            return "dev"
    except Exception:
        return "dev"


def cmd_new(args):
    from watchdog.ops.projects import _new
    return _new(args)


def _print_new_vault_steps(vault, slug) -> None:
    print(f"  {_DIM}To navigate into your new vault, copy and paste this command:{_RESET}")
    print(f"  {_CYAN}cd {vault}{_RESET}")
    print()
    print(f"  {_BOLD}Next steps{_RESET}")
    print(f"    1. {_DIM}(optional){_RESET} Drop background material into {_CYAN}{vault}/context/{_RESET} and run {_CYAN}watchdog ask --context{_RESET}")
    print(f"    2. Run {_CYAN}watchdog add <files or folders>{_RESET} to add documents "
          f"{_DIM}(or drop them into incoming/ and run {_RESET}{_CYAN}watchdog add{_RESET}{_DIM}){_RESET}")
    print(f"    3. Run {_CYAN}watchdog open {slug}{_RESET} to open the vault in Obsidian")
    print()


def cmd_obsidian(args) -> None:
    info = _registered_project(args.name, "open")
    vault = Path(info["path"])
    if not vault.exists():
        sys.exit(f"Error: project directory not found: {vault}")
    if not _obsidian_registered(vault):
        print(f"\n  {_YELLOW}Vault not registered in Obsidian yet.{_RESET}")
        print()
        print(f"  Open Obsidian → {_BOLD}Open folder as vault{_RESET} → navigate to:")
        print(f"  {_CYAN}{vault}{_RESET}")
        print()
        print(f"  After opening it once, {_CYAN}watchdog open {info['name']}{_RESET} will work automatically.\n")
        return
    launch = _obsidian_launch_epoch()
    ts = _obsidian_vault_ts(vault)
    if launch is not None and ts is not None and launch < ts / 1000:
        # Obsidian is running but was launched before this vault was registered,
        # so its in-memory vault list doesn't include it — the URI would fail
        # with "Vault not found". Ask the user to restart Obsidian.
        print(f"\n  {_YELLOW}Obsidian is already running but hasn't loaded this vault yet.{_RESET}")
        print()
        print(f"  Obsidian only reads its vault list when it starts, and {_BOLD}{info['name']}{_RESET}")
        print("  was registered afterwards.")
        print()
        print(f"  Quit Obsidian completely, then run {_CYAN}watchdog open {info['name']}{_RESET} again.\n")
        return
    from urllib.parse import quote
    url = f"obsidian://open?path={quote(str(vault))}"
    if sys.platform == "darwin":
        opener = ["open", url]
    elif sys.platform.startswith("linux"):
        opener = ["xdg-open", url]
    elif sys.platform == "win32":
        import os as _os
        try:
            _os.startfile(url)
        except Exception:
            sys.exit("Error: could not open Obsidian — is it installed?")
        print(f"\n  {_GREEN}Opened:{_RESET} {_BOLD}{info['name']}{_RESET} in Obsidian\n")
        return
    else:
        sys.exit("Error: watchdog open is not supported on this platform")
    result = subprocess.run(opener, capture_output=True)
    if result.returncode != 0:
        sys.exit("Error: could not open Obsidian — is it installed?")
    print(f"\n  {_GREEN}Opened:{_RESET} {_BOLD}{info['name']}{_RESET} in Obsidian\n")


def cmd_open(args) -> None:
    if not getattr(args, "folder", False):
        return cmd_obsidian(args)
    info = _registered_project(args.name, "open")
    vault = Path(info["path"])
    if not vault.exists():
        sys.exit(f"Error: project directory not found: {vault}")
    if sys.platform == "darwin":
        opener = ["open", str(vault)]
    elif sys.platform.startswith("linux"):
        opener = ["xdg-open", str(vault)]
    elif sys.platform == "win32":
        import os as _os
        try:
            _os.startfile(str(vault))
        except Exception:
            sys.exit("Error: could not open file explorer")
        print(f"\n  {_GREEN}Opened:{_RESET} {_CYAN}{vault}{_RESET}\n")
        return
    else:
        sys.exit("Error: watchdog open is not supported on this platform")
    result = subprocess.run(opener, capture_output=True)
    if result.returncode != 0:
        sys.exit("Error: could not open file explorer")
    print(f"\n  {_GREEN}Opened:{_RESET} {_CYAN}{vault}{_RESET}\n")


def _relocate_telemetry(old_resolved: Path, new: Path) -> None:
    try:
        from watchdog import telemetry_db
        telemetry_db.relocate_vault(old_resolved, new)
    except Exception as e:
        print(f"  {_YELLOW}Warning:{_RESET} could not update this vault's rows in the telemetry "
              f"store: {e}")


def cmd_rename(args):
    from watchdog.ops.projects import _rename
    return _rename(args)


def cmd_describe(args):
    from watchdog.ops.projects import _describe
    return _describe(args)


def cmd_delete(args):
    from watchdog.ops.projects import _delete
    return _delete(args)


def cmd_move(args):
    from watchdog.ops.projects import _move
    return _move(args)


def cmd_archive(args) -> None:
    slug, info = _find_project(args.name)
    projects = load_projects()
    projects[slug]["archived"] = True
    save_projects(projects)
    print(f"\n  {_GREEN}Archived:{_RESET} {_BOLD}{info['name']}{_RESET}  {_DIM}hidden from the {_hint('watchdog projects list', 'project list')}{_RESET}\n")


def cmd_unarchive(args) -> None:
    slug, info = _find_project(args.name)
    projects = load_projects()
    projects[slug].pop("archived", None)
    save_projects(projects)
    print(f"\n  {_GREEN}Unarchived:{_RESET} {_BOLD}{info['name']}{_RESET}\n")


def cmd_log(args) -> None:
    info = _registered_project(args.name, "projects log")
    vault = Path(info["path"])
    log_path = vault / "log.md"

    if not log_path.exists():
        print(f"\n  {_DIM}No ingest log found — nothing has been ingested yet.{_RESET}\n")
        return

    content = log_path.read_text().strip()
    if not content:
        print(f"\n  {_DIM}Ingest log is empty.{_RESET}\n")
        return

    lines = content.splitlines()
    n = getattr(args, "lines", None)
    if n:
        lines = lines[-n:]

    print(f"\n  {_BOLD}{info['name']}{_RESET}  {_DIM}ingest log{_RESET}\n")
    for line in lines:
        stripped = line.rstrip()
        if not stripped:
            print()
        elif stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            text = stripped.lstrip("#").strip()
            indent = "  " + "  " * (level - 1)
            print(f"{indent}{_BOLD}{text}{_RESET}")
        else:
            print(f"  {_DIM}{stripped}{_RESET}")
    print()




def cmd_watch(args):
    from watchdog.ops.projects import _watch
    return _watch(args)


def cmd_list(args) -> None:
    all_projects = load_projects()
    show_all = getattr(args, "all", False)
    active   = {k: v for k, v in all_projects.items() if not v.get("archived")}
    archived = {k: v for k, v in all_projects.items() if v.get("archived")}

    visible = dict(active)
    if show_all:
        visible.update(archived)

    if not visible:
        if archived and not show_all:
            print(f"\n  No active investigations. {len(archived)} archived — run {_CYAN}watchdog projects list --all{_RESET} to show.\n")
        else:
            print(f"\n  No projects. Create one with: {_CYAN}watchdog new <name>{_RESET}\n")
        return

    rows = []
    for slug, info in sorted(visible.items(), key=lambda x: x[1]["name"]):
        vault = Path(info["path"])
        health   = _check_project_health(info)
        registry_corrupt = False
        try:
            reg = _load_registry(vault)
        except json.JSONDecodeError:
            reg = None
            registry_corrupt = True
        docs     = str(reg["document_count"]) if reg else "—"
        entities = str(reg["entity_count"])   if reg else "—"
        updated  = _fmt_date(reg["last_updated"]) if reg else "—"
        incoming = str(_count_incoming(vault))    if vault.exists() else "—"
        # Split rather than a single "queued" count (#461): a queue file persists until `bark`
        # commits it, so post-dig/bark split, "queued" alone conflates "not yet dug" with "dug,
        # awaiting bark" — two very different states for a vault that may stay dig-only forever
        # (e.g. an extractor-only benchmark arm).
        awaiting_dig  = str(_count_awaiting_dig(vault))  if vault.exists() else "—"
        awaiting_bark = str(_count_awaiting_bark(vault)) if vault.exists() else "—"
        created  = _fmt_date(info.get("created_at", ""))
        size     = _fmt_size(_vault_size(vault)) if vault.exists() else "—"
        is_arch  = bool(info.get("archived"))
        description = info.get("description", "")
        rows.append((info["name"], slug, docs, entities, updated, incoming, awaiting_dig,
                    awaiting_bark, is_arch, description, health, created, size, registry_corrupt))

    name_w = max(len(r[0]) for r in rows) + 2
    slug_w = max(len(r[1]) for r in rows) + 2
    header = (
        f"  {_BOLD}{'Project':<{name_w}}{_RESET}"
        f"  {_DIM}{'Slug':<{slug_w}}"
        f"  {'Docs':>6}"
        f"  {'Entities':>8}"
        f"  {'Awaiting chew':>13}"
        f"  {'Awaiting dig':>12}"
        f"  {'Awaiting bark':>13}"
        f"  {'Created':>10}"
        f"  {'Size':>8}"
        f"  Updated{_RESET}"
    )
    sep_w = len(re.sub(r"\033\[[0-9;]*m", "", header)) - 2
    print(f"\n{header}")
    print(f"  {_DIM}{'─' * sep_w}{_RESET}")
    for (name, slug, docs, entities, updated, incoming, awaiting_dig, awaiting_bark,
         is_arch, description, health, created, size, registry_corrupt) in rows:
        inc = "—" if incoming == "0" else incoming
        dig = "—" if awaiting_dig  == "0" else awaiting_dig
        bark = "—" if awaiting_bark == "0" else awaiting_bark
        if is_arch:
            inc_str = f"{_DIM}{inc:>13}{_RESET}"
            dig_str = f"{_DIM}{dig:>12}{_RESET}"
            bark_str = f"{_DIM}{bark:>13}{_RESET}"
            name_str = f"  {_DIM}{name:<{name_w}}{_RESET}"
        else:
            inc_str = f"{_YELLOW}{inc:>13}{_RESET}" if inc != "—" else f"{_DIM}{inc:>13}{_RESET}"
            dig_str = f"{_YELLOW}{dig:>12}{_RESET}" if dig != "—" else f"{_DIM}{dig:>12}{_RESET}"
            bark_str = f"{_YELLOW}{bark:>13}{_RESET}" if bark != "—" else f"{_DIM}{bark:>13}{_RESET}"
            name_str = f"  {_BOLD}{name:<{name_w}}{_RESET}"
        print(
            f"{name_str}"
            f"  {_DIM}{slug:<{slug_w}}"
            f"  {docs:>6}"
            f"  {entities:>8}{_RESET}"
            f"  {inc_str}"
            f"  {dig_str}"
            f"  {bark_str}"
            f"  {_DIM}{created:>10}"
            f"  {size:>8}"
            f"  {updated}{_RESET}"
        )
        if description:
            print(f"    {_DIM}{description}{_RESET}")
        if health:
            print(f"    {_YELLOW}⚠ {health}{_RESET}  {_DIM}run {_RESET}{_CYAN}watchdog projects move {slug} <path>{_RESET}{_DIM} to relink or {_RESET}{_CYAN}watchdog projects delete {slug}{_RESET}{_DIM} to remove{_RESET}")
        elif registry_corrupt:
            print(f"    {_YELLOW}⚠ registry file is corrupt{_RESET}  {_DIM}run {_RESET}{_CYAN}watchdog settings doctor{_RESET}{_DIM} to diagnose{_RESET}")
    if archived and not show_all:
        n = len(archived)
        print(f"  {_DIM}+ {n} archived — run {_RESET}{_CYAN}watchdog projects list --all{_RESET}{_DIM} to show{_RESET}")
    print()


def cmd_status(args) -> None:
    from collections import Counter
    if not args.name:
        cwd = Path(".").resolve()
        if is_vault(cwd):
            projects = load_projects()
            info = next((v for v in projects.values() if Path(v["path"]).resolve() == cwd), None)
            if info is None:
                cmd_list(args)
                return
        else:
            cmd_list(args)
            return
    else:
        _, info = _find_project(args.name)
    vault = Path(info["path"])

    if not vault.exists():
        sys.exit(f"Error: project directory not found: {vault}")

    try:
        reg = _load_registry(vault)
    except json.JSONDecodeError as e:
        sys.exit(f"Error: registry file is corrupt — {e}\nRun 'watchdog settings doctor' to diagnose.")
    if not reg:
        print(f"\n  {_BOLD}{info['name']}{_RESET}")
        print(f"  {_CYAN}{info['path']}{_RESET}")
        if info.get("description"):
            print(f"  {_DIM}{info['description']}{_RESET}")
        print(f"  {_DIM}Created {_fmt_date(info.get('created_at', ''))}{_RESET}")
        print(f"\n  {_DIM}No registry found — run {_RESET}{_CYAN}watchdog chew{_RESET}{_DIM} then {_RESET}{_CYAN}watchdog dig{_RESET}{_DIM} to begin.{_RESET}\n")
        return

    docs_file = vault / ".watchdog" / "registry" / "documents.json"
    ents_file = vault / ".watchdog" / "registry" / "entities.json"
    try:
        docs_data = _read_json(docs_file) if docs_file.exists() else {}
        ents_data = _read_json(ents_file) if ents_file.exists() else {}
    except json.JSONDecodeError as e:
        sys.exit(f"Error: registry file is corrupt — {e}\nRun 'watchdog settings doctor' to diagnose.")

    total_pages = sum(d.get("page_count", 0) for d in docs_data.values())
    doc_types   = Counter(d["document_type"] for d in docs_data.values() if d.get("document_type"))
    ent_types   = Counter(e["type"]          for e in ents_data.values() if e.get("type"))
    incoming_n = _count_incoming(vault)
    awaiting_dig_n  = _count_awaiting_dig(vault)
    awaiting_bark_n = _count_awaiting_bark(vault)

    print(f"\n  {_BOLD}{info['name']}{_RESET}  {_DIM}{slugify(info['name'])}{_RESET}")
    print(f"  {_CYAN}{info['path']}{_RESET}")
    if info.get("description"):
        print(f"  {_DIM}{info['description']}{_RESET}")

    size_str    = _fmt_size(_vault_size(vault))
    schema_ver  = reg.get("schema_version", "unversioned")
    schema_note = "" if schema_ver == VAULT_SCHEMA_VERSION else f"  {_YELLOW}schema v{schema_ver} (current: v{VAULT_SCHEMA_VERSION}){_RESET}"
    print(f"  {_DIM}Created {_fmt_date(info.get('created_at', ''))}  ·  {size_str}  ·  schema v{schema_ver}{_RESET}{schema_note}")
    print()

    pages_note = f" {_DIM}({total_pages} pages){_RESET}" if total_pages else ""
    print(f"  {_BOLD}{reg['document_count']}{_RESET} documents{pages_note} · {_BOLD}{reg['entity_count']}{_RESET} entities · {_DIM}last updated {_fmt_date(reg['last_updated'])}{_RESET}")

    from watchdog.pipeline import orchestrate
    last_usage = orchestrate.latest_usage(vault)
    if last_usage:
        cost = f" · ~${last_usage['cost_usd']:.4f}" if last_usage.get("cost_usd") else ""
        print(f"  {_DIM}Last ingest: {last_usage['input_tokens']:,} in / "
              f"{last_usage['output_tokens']:,} out tokens{cost}{_RESET}")

    if incoming_n:
        print(f"  {_YELLOW}{incoming_n} file{'s' if incoming_n != 1 else ''}{_RESET} in {_CYAN}incoming/{_RESET} {_DIM}— run{_RESET} {_CYAN}watchdog chew{_RESET}")
    if awaiting_dig_n:
        print(f"  {_YELLOW}{awaiting_dig_n} file{'s' if awaiting_dig_n != 1 else ''}{_RESET} chewed and awaiting {_CYAN}watchdog dig{_RESET}")
    if awaiting_bark_n:
        print(f"  {_YELLOW}{awaiting_bark_n} file{'s' if awaiting_bark_n != 1 else ''}{_RESET} dug and awaiting {_CYAN}watchdog bark{_RESET}")
    _warn_pending_research(vault)

    if orchestrate.has_pending_finalization(vault):
        p = orchestrate.pending_finalization(vault)
        bits = []
        if p["docs"]:
            bits.append(f"{p['docs']} document{'s' if p['docs'] != 1 else ''}")
        if p["entities"]:
            bits.append(f"{p['entities']} entit{'ies' if p['entities'] != 1 else 'y'} to synthesize")
        detail = f" {_DIM}({', '.join(bits)}){_RESET}" if bits else ""
        print(f"  {_YELLOW}Batch waiting to be finished{_RESET}{detail} {_DIM}— run{_RESET} {_CYAN}watchdog bark{_RESET}")

    from watchdog.pipeline import batch_extract
    pending_batch = batch_extract.read_state(vault)
    if pending_batch:
        n = len(pending_batch.get("shas", []))
        print(f"  {_YELLOW}Batch extraction pending{_RESET} {_DIM}({n} document{'s' if n != 1 else ''}, "
              f"{pending_batch.get('batch_id', '?')}) — run{_RESET} {_CYAN}watchdog dig{_RESET}"
              f"{_DIM} to check on it{_RESET}")

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


def doctor_findings(all_projects: dict) -> tuple[list, list, list]:
    """The three kinds of problem `watchdog doctor` reports, as `(struct_issues, schema_issues,
    corrupt_registries)` lists of `(slug, info[, detail])` tuples, in project-name order. Shared
    with the desktop app, which presents the same findings without the terminal layout."""
    struct_issues  = []
    schema_issues  = []
    corrupt_registries = []
    for slug, info in sorted(all_projects.items(), key=lambda x: x[1]["name"]):
        problem = _check_project_health(info)
        if problem:
            struct_issues.append((slug, info, problem))
        else:
            try:
                reg = _load_registry(Path(info["path"]))
            except json.JSONDecodeError:
                corrupt_registries.append((slug, info))
                continue
            if reg and reg.get("schema_version") != VAULT_SCHEMA_VERSION:
                schema_issues.append((slug, info, reg.get("schema_version", "unversioned")))
    return struct_issues, schema_issues, corrupt_registries


def cmd_doctor(args) -> None:
    all_projects = load_projects()
    if not all_projects:
        print("\n  No registered investigations.\n")
        return

    struct_issues, schema_issues, corrupt_registries = doctor_findings(all_projects)

    total   = len(all_projects)
    n_issues = len(struct_issues) + len(schema_issues) + len(corrupt_registries)
    healthy  = total - n_issues
    noun     = "investigation" if total == 1 else "investigations"
    print(f"\n  Checking {total} registered {noun}...")
    print()

    if not n_issues:
        print(f"  {_GREEN}✓ All {total} {noun} healthy{_RESET}")
        print()
        return

    h_noun = "investigation" if healthy == 1 else "investigations"
    print(f"  {_GREEN}✓ {healthy} {h_noun} healthy{_RESET}")
    print()

    for slug, info, problem in struct_issues:
        arch_note = f"  {_DIM}(archived){_RESET}" if info.get("archived") else ""
        print(f"  {_YELLOW}⚠  {_BOLD}{info['name']}{_RESET}  {_DIM}{slug}{_RESET}{arch_note}")
        print(f"     {_DIM}{problem.capitalize()}: {info['path']}{_RESET}")
        print(f"     {_DIM}→ {_RESET}{_CYAN}watchdog projects move {slug} <new-path>{_RESET}{_DIM} to relink{_RESET}")
        print(f"     {_DIM}→ {_RESET}{_CYAN}watchdog projects delete {slug}{_RESET}{_DIM} to remove from registry{_RESET}")
        print()

    for slug, info, found_ver in schema_issues:
        arch_note = f"  {_DIM}(archived){_RESET}" if info.get("archived") else ""
        print(f"  {_YELLOW}⚠  {_BOLD}{info['name']}{_RESET}  {_DIM}{slug}{_RESET}{arch_note}")
        print(f"     {_DIM}Schema v{found_ver} — current is v{VAULT_SCHEMA_VERSION}{_RESET}")
        print(f"     {_DIM}This vault may need migration before it is fully compatible.{_RESET}")
        print()

    for slug, info in corrupt_registries:
        arch_note = f"  {_DIM}(archived){_RESET}" if info.get("archived") else ""
        reg_path = Path(info["path"]) / ".watchdog" / "registry" / "registry.json"
        print(f"  {_YELLOW}⚠  {_BOLD}{info['name']}{_RESET}  {_DIM}{slug}{_RESET}{arch_note}")
        print(f"     {_DIM}Registry file is corrupt: {reg_path}{_RESET}")
        print(f"     {_DIM}It's a regenerated summary cache — delete it and it will rebuild on the next {_RESET}{_CYAN}watchdog dig{_RESET}{_DIM} or {_RESET}{_CYAN}watchdog bark{_RESET}{_DIM}.{_RESET}")
        print()


# Facts listed beside one search hit (`--json`), so a session can cite what it found (D283).
_SEARCH_FACTS_PER_HIT = 12


def _page_facts(vault: Path):
    """A function (sha, page) -> the facts of that page of that document, each with its citation
    link, for search hits. Facts whose matched passage is on the page count too."""
    from watchdog.pipeline import citations, entity_facts
    index = entity_facts.FactIndex(vault)

    def facts(sha: str | None, page) -> list[dict]:
        if not sha or not isinstance(page, int):
            return []
        out = []
        for f in index.document_facts(sha):
            if page in (f.get("page"), f.get("passage_page")) and f.get("note"):
                out.append({"id": f["id"], "cite": citations.link(f), "fact": f.get("fact"),
                            "page": f.get("page"), "mark": (f.get("mark") or {}).get("status")})
        return out[:_SEARCH_FACTS_PER_HIT]
    return facts, index


def _build_search_json(query: str, passages: list[dict], notes: list[dict],
                       exact: list[dict] | None = None, vault: Path | None = None) -> dict:
    """Shape search results as JSON for the session's `search` tool (D299) and the app's search
    screen. Each passage carries the citable span (`text`) + its page; `score` is the
    cosine similarity (ordering already reflects fusion + rerank). ``exact`` is the full-text
    (FTS5) lane (#109) — every hit for the exact term/phrase, no relevance score.

    This JSON shape is a contract with the `watchdog-query` skill
    (src/watchdog/skills/watchdog-query.md), which tells Claude what each field means: a field
    rename or removal here must change the skill with it (#499).

    With `vault`, each passage and each exact match in a document's text also carries `facts`:
    the facts recorded on that page, each with its D271 `id`, the `cite` link to paste into a
    page (`[[documents/<slug>#^f-<hash>|p. 4]]`), its text, page and the reporter's mark (D283)."""
    facts_on, index = _page_facts(vault) if vault is not None else (None, None)
    sha_by_name: dict = {}
    if index is not None:
        for sha, d in index.documents.items():
            if isinstance(d, dict):
                sha_by_name.setdefault(d.get("filename"), []).append(sha)

    def with_facts(item: dict, sha: str | None) -> dict:
        if facts_on is not None:
            item["facts"] = facts_on(sha, item.get("page"))
        return item

    def by_name(name) -> str | None:
        shas = sha_by_name.get(name) or []
        return shas[0] if len(shas) == 1 else None

    return {
        "query": query,
        "passages": [
            with_facts({"filename": r.get("filename"), "page": r.get("page"),
                        "text": r.get("text"), "score": round(float(r["score"]), 4)},
                       by_name(r.get("filename")))
            for r in passages
        ],
        "notes": [
            {"note_path": r.get("note_path"), "preview": r.get("preview"),
             "score": round(float(r["score"]), 4)}
            for r in notes
        ],
        "exact": [
            with_facts({"kind": r.get("kind"), "title": r.get("title"), "path": r.get("path"),
                        "page": r.get("page"), "text": r.get("text")},
                       r.get("key") if r.get("kind") == "corpus" else None)
            if r.get("kind") == "corpus" else
            {"kind": r.get("kind"), "title": r.get("title"), "path": r.get("path"),
             "page": r.get("page"), "text": r.get("text")}
            for r in (exact or [])
        ],
    }


def _note_format() -> int:
    from watchdog.pipeline.entity_notes import NOTE_FORMAT
    return NOTE_FORMAT


def _manifest_matches(manifest: dict, term: str) -> list[dict]:
    """Entities whose name or any alias contains `term` (case-insensitive substring)."""
    t = term.lower()
    hits = []
    for eid, entry in manifest.items():
        names = [entry.get("name", ""), *entry.get("aliases", [])]
        if any(t in n.lower() for n in names if n):
            hits.append({"id": eid, "name": entry.get("name"), "type": entry.get("type"),
                        "note_path": entry.get("note_path")})
    return hits


def batch_report(vault: Path, terms: list[str], limit: int) -> tuple[list[dict], int]:
    """One entry per term — `{term, entities, hits, error}` — from the vault's manifest (name and
    alias matches) and its full-text index, plus how many exact-match lookups failed. A failed
    lookup carries its `error` and must never be read as "no hits". Shared with the desktop app."""
    from watchdog.pipeline import fulltext
    manifest_path = vault / ".watchdog" / "registry" / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        manifest = _read_json_or(manifest_path, {}, catch=(json.JSONDecodeError,))

    report = []
    failures = 0
    for term in terms:
        entities = _manifest_matches(manifest, term)
        try:
            hits, error = fulltext.search(vault, term, limit=limit), None
        except Exception as e:
            hits, error = [], str(e)
            failures += 1
        report.append({"term": term, "entities": entities, "hits": hits, "error": error})
    return report, failures


def everywhere_report(all_projects: dict, terms: list[str], limit: int) -> tuple[list[dict], list[tuple[str, str]]]:
    """Every active (non-archived) investigation's manifest and full-text matches for `terms`:
    `(results, skipped)`, where each result is `{slug, name, path, entities, hits, error}` in
    project-name order and `skipped` lists `(slug, problem)` for investigations whose folder is
    missing or isn't a vault. Shared with the desktop app."""
    from watchdog.pipeline import fulltext

    active = {k: v for k, v in all_projects.items() if not v.get("archived")}
    results = []
    skipped = []
    for slug, info in sorted(active.items(), key=lambda x: x[1]["name"]):
        problem = _check_project_health(info)
        if problem:
            skipped.append((slug, problem))
            continue
        vault = Path(info["path"])

        manifest_path = vault / ".watchdog" / "registry" / "manifest.json"
        manifest = {}
        if manifest_path.exists():
            manifest = _read_json_or(manifest_path, {}, catch=(json.JSONDecodeError,))

        entities_by_id = {}
        hits = []
        error = None
        for term in terms:
            for e in _manifest_matches(manifest, term):
                entities_by_id[e["id"]] = e
            try:
                hits.extend(fulltext.search(vault, term, limit=limit))
            except Exception as e:
                error = str(e)

        results.append({"slug": slug, "name": info["name"], "path": info["path"],
                        "entities": list(entities_by_id.values()), "hits": hits, "error": error})
    return results, skipped
