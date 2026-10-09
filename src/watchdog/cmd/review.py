"""`watchdog review [kind]` — step through what is waiting on the user, one item at a time (D252).

Five kinds, each read from local files only:

  * contradictions — conflicting claims recorded on entity notes
  * leads          — entities named but never profiled, isolated, or carrying inferred facts
  * alerts         — unresolved watch-list hits in `briefings/alerts-*.md`
  * duplicates     — documents chew's MinHash matched to an earlier one
  * merges         — two entity records that may be the same, left unmerged (D279)

Marking a `merges` item handled means "not the same": the pair is never merged automatically
afterwards. Merging one runs `watchdog merge-entities`.

Each item can be marked handled (the same `resolutions.json` store `watchdog resolve` writes, so
it stops re-surfacing in every report), kept open, or opened in Obsidian. Off a terminal the
command prints the list with each item's resolution id instead."""

import re
import sys
from pathlib import Path

from watchdog import interactive
from watchdog.cmd.base import _BOLD, _CYAN, _DIM, _GREEN, _RESET, _YELLOW
from watchdog.links import note_link, obsidian_url, open_url
from watchdog.pipeline import leads as _leads
from watchdog.pipeline import resolutions
from watchdog.pipeline.json_io import _read_json_or
from watchdog.vault_paths import is_vault

KINDS = ("contradictions", "leads", "alerts", "duplicates", "merges")
_LABELS = {"contradictions": "Contradictions", "leads": "Leads",
           "alerts": "Watch-list hits", "duplicates": "Possible duplicate documents",
           "merges": "Possible same entities"}
_SAME_WORD = {"person": "person", "organization": "organization", "public-body": "public body",
              "place": "place", "asset": "asset", "proceeding": "proceeding"}

# The link is greedy up to the last `**` before the optional entity/count suffix, so a filename
# containing `**` stays whole.
_ALERT_LINE = re.compile(r"^- \[[ xX]\] \*\*(?P<link>.+)\*\*(?P<rest>(?: · known entity .*?)?"
                         r"(?: \(\d+ matches\))?) ?<!--wid:(?P<rid>alert:[^>]+?)-->")
_ALERT_TERM = re.compile(r"^### `(?P<term>.+)`")
_WIKILINK = re.compile(r"^\[\[(?P<target>[^|\]]+)(?:\|(?P<text>.+))?\]\]$")   # text may hold `]`


def _item(kind: str, rid: str, title: str, detail: list[str], note: str | None) -> dict:
    return {"kind": kind, "rid": rid, "title": title, "detail": detail, "note": note or None}


def _callout_lines(callout: str) -> list[str]:
    lines = [re.sub(r"^\s*>\s?", "", ln).rstrip() for ln in callout.splitlines()]
    lines = [re.sub(r"^\[!contradiction\]\s*", "", ln) for ln in lines]
    return [ln for ln in lines if ln.strip()]


def _contradictions(found: dict) -> list[dict]:
    out = []
    for c in found["contradictions"]:
        for callout in c["callouts"]:
            lines = _callout_lines(callout.get("text", "")) or [callout["summary"]]
            out.append(_item("contradictions", callout["rid"], f"{c['name']} — {lines[0]}",
                             lines[1:], c["note_path"]))
    return out


def _lead_items(found: dict) -> list[dict]:
    out = []
    for u in found["unprofiled"]:
        docs = f"{u['doc_count']} document{'s' if u['doc_count'] != 1 else ''}"
        out.append(_item("leads", u["rid"], f"{u['name']} — named but never profiled",
                         [f"Named by {', '.join(u['mentioned_by'])} · {docs}"], None))
    for i in found["isolated"]:
        out.append(_item("leads", i["rid"], f"{i['name']} — mentioned often but unconnected",
                         [f"Appears in {i['doc_count']} documents with no relationships"],
                         i.get("note_path")))
    for i in found["inferred"]:
        out.append(_item("leads", i["rid"], f"{i['name']} — inferred facts to verify",
                         list(i["claims"]), i["note_path"]))
    return out


def _alerts(vault: Path, resolved: frozenset[str]) -> list[dict]:
    """Watch-list hits from every `alerts-*.md`, newest file first, one item per id."""
    d = vault / "briefings"
    files = sorted(d.glob("alerts-*.md"), key=lambda p: p.name, reverse=True) if d.is_dir() else []
    seen: dict[str, dict] = {}
    for path in files:
        term, current = None, None
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if m := _ALERT_TERM.match(line):
                term, current = m.group("term"), None
            elif m := _ALERT_LINE.match(line):
                rid = m.group("rid").strip()
                current = None
                if rid in resolved or rid in seen:
                    continue
                link = _WIKILINK.match(m.group("link"))
                note = link.group("target") if link else None
                name = (link.group("text") or link.group("target")) if link else m.group("link")
                title = f"`{term}` in {name}" if term else name
                current = seen[rid] = _item("alerts", rid, title, [], note)
            elif current is not None and line.startswith("  - "):
                current["detail"].append(line[4:])
            elif not line.strip():
                current = None
    return list(seen.values())


def _duplicates(vault: Path, resolved: frozenset[str]) -> list[dict]:
    docs = _read_json_or(vault / ".watchdog" / "registry" / "documents.json", {})
    out = []
    for sha, d in sorted(docs.items(), key=lambda kv: kv[1].get("filename") or ""):
        match = d.get("near_duplicate_of")
        rid = resolutions.duplicate_id(sha)
        if not match or rid in resolved:
            continue
        link = _WIKILINK.match(match)
        other = (link.group("text") or link.group("target")) if link else match
        out.append(_item("duplicates", rid, d.get("filename") or sha[:12],
                         [f"Closely matches {other} — confirm whether they are the same document"],
                         d.get("document_note")))
    return out


def _same_entities(vault: Path, resolved: frozenset[str]) -> list[dict]:
    """"Possible same" pairs the merge log holds open (D279), each with both sides' evidence."""
    from watchdog.pipeline import merge_log, verification
    entities = _read_json_or(vault / ".watchdog" / "registry" / "entities.json", {})
    marks = None
    out = []
    for c in merge_log.open_candidates(vault, entities=entities, resolved=resolved):
        if marks is None:
            marks = verification.marks(vault)
        # Each side's facts were recorded with the pair; a fact the reporter disputes is labelled.
        c = {**c, **{k: {**c[k], "facts": [
            {**f, "disputed": (verification.attach(marks.get(f.get("id")), f) or {}).get("status") == "disputed"}
            for f in c[k].get("facts") or [] if isinstance(f, dict)]} for k in ("a", "b")}}
        a, b = c["a"], c["b"]
        word = _SAME_WORD.get(a.get("type"), "entity")
        detail = [c.get("reason") or ""]
        if c.get("model_declined"):
            detail.append("The AI model compared their facts and was not confident they are the same.")
        item = _item("merges", c["id"], f"{a['name']} and {b['name']} — possible same {word}",
                     [d for d in detail if d], (entities.get(a["id"]) or {}).get("note_path"))
        item["pair"] = {
            "tier": c.get("tier"), "rule": c.get("rule"), "model_declined": bool(c.get("model_declined")),
            "evidence": c.get("evidence") or {},
            "a": {**a, "note": (entities.get(a["id"]) or {}).get("note_path"),
                  "doc_count": len((entities.get(a["id"]) or {}).get("appears_in") or [])},
            "b": {**b, "note": (entities.get(b["id"]) or {}).get("note_path"),
                  "doc_count": len((entities.get(b["id"]) or {}).get("appears_in") or [])},
        }
        out.append(item)
    return out


def open_items(vault: Path, kinds=KINDS) -> list[dict]:
    """Every unresolved item of the given kinds, in `KINDS` order."""
    resolved = resolutions.resolved_ids(vault)
    found = _leads.scan(vault) if {"contradictions", "leads"} & set(kinds) else None
    out: list[dict] = []
    for kind in KINDS:
        if kind not in kinds:
            continue
        if kind == "contradictions":
            out += _contradictions(found)
        elif kind == "leads":
            out += _lead_items(found)
        elif kind == "alerts":
            out += _alerts(vault, resolved)
        elif kind == "merges":
            out += _same_entities(vault, resolved)
        else:
            out += _duplicates(vault, resolved)
    return out


def count_open_duplicates(vault: Path) -> int:
    return len(_duplicates(vault, resolutions.resolved_ids(vault)))


def count_open_same_entities(vault: Path, resolved: frozenset[str] | None = None) -> int:
    from watchdog.pipeline import merge_log
    return len(merge_log.open_candidates(vault, resolved=resolved))


def _show(vault: Path, item: dict, n: int, total: int) -> None:
    print(f"\n  {_DIM}{n} of {total} · {_LABELS[item['kind']]}{_RESET}")
    print(f"  {_BOLD}{item['title']}{_RESET}")
    for line in item["detail"]:
        print(f"    {line}")
    if item["note"]:
        print(f"    {_CYAN}{note_link(vault, item['note'], item['note'])}{_RESET}")
    if item.get("pair"):
        a, b = item["pair"]["a"], item["pair"]["b"]
        print(f"    {_DIM}To merge them:{_RESET} {_CYAN}watchdog review merge-entities "
              f"{a['id']} {b['id']}{_RESET}")


def _print_list(vault: Path, items: list[dict]) -> None:
    for kind in KINDS:
        group = [i for i in items if i["kind"] == kind]
        if not group:
            continue
        print(f"\n  {_BOLD}{_LABELS[kind]}{_RESET}  {_DIM}({len(group)}){_RESET}")
        for i in group:
            print(f"    {i['title']}")
            for line in i["detail"]:
                print(f"      {_DIM}{line}{_RESET}")
            print(f"      {_DIM}resolve: {i['rid']}{_RESET}")
    print(f"\n  {_DIM}Mark one handled with{_RESET} {_CYAN}watchdog review resolve <id>{_RESET}\n")


def _walk(vault: Path, items: list[dict]) -> int:
    """Step through `items`. Returns how many were marked handled."""
    handled = 0
    for n, item in enumerate(items, 1):
        _show(vault, item, n, len(items))
        while True:
            handle = "Not the same" if item["kind"] == "merges" else "Mark as handled"
            choices = [handle, "Keep open"]
            if item["note"]:
                choices.append("Open in Obsidian")
            choices.append("Stop reviewing")
            pick = interactive.pick(choices, 0, hint="↑/↓ move · Enter select · q stop")
            if pick is interactive.CANCELLED or choices[pick] == "Stop reviewing":
                return handled
            choice = choices[pick]
            if choice == "Open in Obsidian":
                if not open_url(obsidian_url(vault, item["note"])):
                    print(f"  {_YELLOW}Could not open Obsidian — is it installed?{_RESET}")
                continue
            if choice == handle:
                resolutions.resolve(vault, [item["rid"]], label="review")
                resolutions.tick_in_briefings(vault, [item["rid"]])
                handled += 1
                print(f"  {_GREEN}Marked handled.{_RESET}")
            break
    return handled


def cmd_review(args) -> dict | None:
    vault = Path(".").resolve()
    if not is_vault(vault):
        sys.exit("Error: not inside a Watchdog project — run this from the project folder")
    kinds = (args.kind,) if getattr(args, "kind", None) else KINDS
    items = open_items(vault, kinds)
    what = _LABELS[args.kind].lower() if getattr(args, "kind", None) else "items"
    if not items:
        print(f"\n  {_DIM}Nothing to review — no open {what}.{_RESET}\n")
        return None
    if not sys.stdin.isatty():
        _print_list(vault, items)
        return None
    print(f"\n  {_BOLD}{len(items)}{_RESET} {what} to review. "
          f"{_DIM}Handled items stop appearing in briefings and on the home screen;"
          f" `watchdog review unresolve <id>` brings one back.{_RESET}")
    handled = _walk(vault, items)
    left = len(items) - handled
    print(f"\n  {_GREEN}{handled} handled{_RESET}{_DIM} · {left} still open{_RESET}\n")
    return None
