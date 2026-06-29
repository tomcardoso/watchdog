"""`watchdog leads` — print the deterministic whole-vault lead sweep (#155).

Read-only, no model, no lock: runs `pipeline.leads.scan` over the vault's entity registry and
prints the named-but-unprofiled / isolated / unresolved-contradiction digest. The same sweep
runs automatically at the end of `watchdog ingest` (writing `briefings/leads-<date>.md`); this
command is for re-running it on demand between ingests."""

from watchdog.cmd.base import _BOLD, _CYAN, _DIM, _RESET, _YELLOW, _resolve_vault
from watchdog.pipeline import leads as _leads


def _section(title: str, count: int) -> None:
    print(f"  {_BOLD}{title}{_RESET}  {_DIM}({count}){_RESET}\n")


def cmd_leads(args) -> None:
    _, info, vault = _resolve_vault(args.project)
    data = _leads.scan(vault)

    print()
    print(f"  {_BOLD}Leads{_RESET} {_DIM}— {info['name']}{_RESET}")
    print()

    if not _leads.total(data):
        print(f"  {_DIM}No leads — every named entity is profiled, nothing isolated, "
              f"no open contradictions.{_RESET}\n")
        return

    if data["unprofiled"]:
        _section("Named but never profiled", len(data["unprofiled"]))
        for u in data["unprofiled"]:
            docs = f"{u['doc_count']} document{'s' if u['doc_count'] != 1 else ''}"
            print(f"    {_BOLD}{u['name']}{_RESET}  {_DIM}named by {', '.join(u['mentioned_by'])} "
                  f"· {docs}{_RESET}")
        print()

    if data["isolated"]:
        _section("Mentioned often but unconnected", len(data["isolated"]))
        for i in data["isolated"]:
            print(f"    {_BOLD}{i['name']}{_RESET}  {_DIM}appears in {i['doc_count']} documents "
                  f"· no relationships{_RESET}")
        print()

    if data["contradictions"]:
        _section("Unresolved contradictions", len(data["contradictions"]))
        for c in data["contradictions"]:
            n = c["count"]
            note = f"  {_CYAN}{c['note_path']}.md{_RESET}" if c["note_path"] else ""
            print(f"    {_BOLD}{c['name']}{_RESET}  {_DIM}{n} flagged "
                  f"conflict{'s' if n != 1 else ''}{_RESET}{note}")
        print()
