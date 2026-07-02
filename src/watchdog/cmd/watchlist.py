"""`watchdog watchlist` — sweep the whole vault against `watchlist.md` (#220).

`pipeline.watchlist.scan` normally only sees the current ingest run's documents (D35), so
adding a term never re-checks documents ingested before it was added. This command builds a
synthetic "every document in the vault" results list straight from `documents.json` — every
key is a `sha256` already known to have a `morgue_path`, so each is treated as an `ok` result
— and hands it to the unchanged `scan`/`write_alerts` pair the per-run path already uses. Read-
only except for the alerts file it writes; no lock, no model call."""

import json

from watchdog.cmd.base import _BOLD, _CYAN, _DIM, _GREEN, _RESET, _YELLOW, _resolve_vault
from watchdog.pipeline import watchlist as _watchlist


def cmd_watchlist(args) -> None:
    _, info, vault = _resolve_vault(getattr(args, "project", None))

    documents_path = vault / ".watchdog" / "Registry" / "documents.json"
    try:
        documents_reg = json.loads(documents_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        documents_reg = {}

    print()
    print(f"  {_BOLD}Watchlist scan{_RESET} {_DIM}— {info['name']}{_RESET}")
    print()

    if not documents_reg:
        print(f"  {_DIM}No documents ingested yet — nothing to scan.{_RESET}\n")
        return

    if not _watchlist.load_terms(vault):
        print(f"  {_DIM}watchlist.md is empty or missing — nothing to scan for.{_RESET}\n")
        return

    results = [{"sha256": sha, "status": "ok"} for sha in documents_reg]
    hits = _watchlist.scan(vault, results)
    alert = _watchlist.write_alerts(vault, hits)

    if not alert:
        print(f"  {_DIM}Scanned {len(documents_reg)} document"
              f"{'s' if len(documents_reg) != 1 else ''} — no matches.{_RESET}\n")
        return

    relpath, n_terms, n_docs = alert
    print(f"  {_YELLOW}{len(hits)} match{'es' if len(hits) != 1 else ''}{_RESET} "
          f"{_DIM}({n_terms} term{'s' if n_terms != 1 else ''}, "
          f"{n_docs} document{'s' if n_docs != 1 else ''}){_RESET}")
    print(f"  {_GREEN}Written to{_RESET} {_CYAN}{relpath}{_RESET}")
    print()
