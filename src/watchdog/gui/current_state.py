"""Briefings → Current state: the session primer's picture of the investigation, written for the
reporter rather than for Claude.

The primer (`cmd/primer.py`, D285) is the text every Ask Claude session starts with. It carries
instructions meant for the model (how to cite a fact, a `watchdog search … --json` command line)
and points to files and commands, under a budget that shrinks its lists. The app showed it
verbatim, so a reporter read model instructions and terminal commands. This renders the same
records (`primer.gather`, so the two never disagree on a count) without the instructions, with
the app's own names for where things live, and without the budget. The primer itself stays
available in the app as "What Claude is given".
"""

from __future__ import annotations

from pathlib import Path

from watchdog.cmd import primer

_LIST_MAX = 6
_QUESTIONS_MAX = 12


def _list(title: str, rows: list[str], where: str) -> list[str]:
    if not rows:
        return []
    out = [f"**{title} ({len(rows):,})**"]
    out += [f"- {r}" for r in rows[:_LIST_MAX]]
    if len(rows) > _LIST_MAX:
        out.append(f"- …and {len(rows) - _LIST_MAX:,} more in {where}")
    return out + [""]


def render(data: dict) -> str:
    """Reader-facing Markdown from `primer.gather`'s data. No model instructions, no command
    lines, no file names the reporter would have to open by hand."""
    lines = ["Built just now from the investigation's records, with no AI model involved. It covers "
             "the whole investigation, not only the latest run. Ask Claude starts every conversation "
             "with the same information.", ""]

    lines += ["## Your questions", ""]
    if data["aim"]:
        lines += [f"Investigating: {primer._clip(data['aim'], 600)}", ""]
    if data["questions"]:
        lines += [f"- {primer._clip(q, 300)}" for q in data["questions"][:_QUESTIONS_MAX]]
        if len(data["questions"]) > _QUESTIONS_MAX:
            lines.append(f"- …and {len(data['questions']) - _QUESTIONS_MAX} more in Investigation context")
        lines.append("")
    elif not data["aim"]:
        lines += ["No questions yet. Add them in Investigation context; every run and every "
                  "conversation with Claude reads them.", ""]

    lines += ["## The record", ""]
    if not data["documents"]:
        lines += ["No documents have been added yet.", ""]
    else:
        v = data["verification"] or {}
        checked = sum(v.get(k, 0) for k in ("verified", "disputed", "unverifiable"))
        lines.append(f"{primer._plural(data['documents'], 'document')} "
                     f"({primer._plural(data['pages'], 'page')}), "
                     f"{primer._plural(data['entities'], 'entity', 'entities')}, "
                     f"{primer._plural(data['facts'], 'fact')}.")
        if checked:
            lines.append(f"You have checked {checked:,} of the facts against their source: "
                         f"{v.get('verified', 0):,} verified, {v.get('disputed', 0):,} disputed, "
                         f"{v.get('unverifiable', 0):,} can't verify (Review → Verification).")
        else:
            lines.append("You have not marked any fact as checked yet (Review → Verification).")
        lines.append("")

    if data["top_entities"]:
        lines += ["## Most-mentioned entities", ""]
        for e in data["top_entities"]:
            name = primer._safe(e["name"], 80)
            shown = f"[[{e['note']}|{name}]]" if e.get("note") else name
            kind = f", {e['type'].replace('-', ' ')}" if e.get("type") else ""
            lines.append(f"- {shown}{kind}: {primer._plural(e['docs'], 'document')}")
        lines.append("")

    waiting: list[str] = []
    waiting += _list("Open contradictions", [primer._safe(i["title"]) for i in data["contradictions"]],
                     "Review → Contradictions")
    waiting += _list("Open leads", [primer._safe(i["title"]) for i in data["leads"]], "Review → Leads")
    waiting += _list("Documents to request",
                     [primer._safe(r.get("what") or "") for r in data["requests"]], "Review → Requests")
    waiting += _list("Possible same entities, not merged",
                     [primer._safe(i["title"]) for i in data["merges"]], "Review → Merges")
    waiting += _list("Facts you dispute",
                     [f"{primer._safe(d['fact'], 160)} {d['cite']}" for d in data["disputed"]],
                     "Review → Verification")
    if waiting:
        lines += ["## Waiting on you", ""] + waiting

    if data["briefings"]:
        lines += ["## Recent briefings", ""]
        for stem, status in data["briefings"]:
            tail = f": {primer._clip(status, 300)}" if status else ""
            lines.append(f"- [[briefings/{stem}|{primer._briefing_label(stem)}]]{tail}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build(vault: Path) -> str:
    return render(primer.gather(Path(vault)))
