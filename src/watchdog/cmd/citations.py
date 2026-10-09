"""`watchdog check-citations [FILE…] [--json]` — check the fact citations in a vault's pages (D283).

Read-only, no model, no lock. Every `[[documents/<slug>#^f-<hash>|…]]` link in `queries/`, `wiki/`,
`briefings/` and `hot.md` (or in the files named) is resolved against the stored facts; the report
lists citations whose fact no longer exists and those on facts the reporter disputes. Uncited
sentences are not reported. Pages are never changed. A Claude session runs it before filing a page;
it only ever reads the investigation it is run in."""

import json
import sys
from pathlib import Path

from watchdog.cmd.base import _BOLD, _DIM, _GREEN, _RESET, _YELLOW
from watchdog.vault_paths import is_vault


def cmd_check_citations(args) -> None:
    from watchdog.pipeline import citations
    vault = Path(".").resolve()
    if not is_vault(vault):
        sys.exit("Error: not inside a Watchdog investigation — run this from its folder.")
    paths = []
    for name in args.files or []:
        p = (vault / name).resolve()
        if vault not in p.parents or not p.is_file():
            sys.exit(f"Error: {name} is not a file in this investigation.")
        paths.append(p)
    report = citations.check_vault(vault, paths or None)

    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False))
        return

    print()
    n = report["citations"]
    print(f"  {_BOLD}Fact citations{_RESET}  {_DIM}{report['checked']} page"
          f"{'s' if report['checked'] != 1 else ''} checked · {n} citation{'s' if n != 1 else ''}{_RESET}")
    print()
    problems = False
    for page in report["pages"]:
        bad = [i for i in page["items"] if i["status"] == "not_found"]
        disputed = [i for i in page["items"] if i.get("disputed")]
        if not (bad or disputed or page["unresolved_short"]):
            continue
        problems = True
        print(f"  {_BOLD}{page['path']}{_RESET}")
        for i in bad:
            print(f"    {_YELLOW}source not found{_RESET}  {_DIM}{i['target']}#^{i['block']}{_RESET}")
        for i in disputed:
            print(f"    {_YELLOW}disputed{_RESET}  {_DIM}{(i['fact'] or {}).get('fact', '')[:100]}{_RESET}")
        if page["unresolved_short"]:
            print(f"    {_YELLOW}{page['unresolved_short']} short reference"
                  f"{'s' if page['unresolved_short'] != 1 else ''} ([f:…]) with nothing to resolve "
                  f"them{_RESET}")
        print()
    if not problems:
        print(f"  {_GREEN}Every citation resolves to a stored fact.{_RESET}\n")
