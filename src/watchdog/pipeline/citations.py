"""Fact citations in AI-written and session-written text, checked by code (D283).

Every fact has a D271 id and, in its document's note, a line ending in an Obsidian block id
(`^f-<hash>`). A citation is a wikilink to that line:

    [[documents/<slug>#^f-<hash>|p. 4]]

It works in Obsidian (the link opens the document note at the fact) and in the app (which opens
the document at the fact and shows it on hover). This module makes and checks those links:

* `render_short` turns a model's short citations (`[f:3a9c]`, given in the synthesis and briefing
  prompts and mapped back to D271 ids by a stored `fact_refs` map) into links. A citation whose
  short ref is not in the map, or whose fact no longer exists, is dropped; the sentence stays,
  and the drop is counted. A link is only ever made to a fact that exists.
* `annotate_prose` checks links already written as wikilinks (a Claude session's summary):
  a dangling one is dropped and counted, a disputed one gets the label "disputed".
* `annotate_callouts` does the same inside contradiction callouts, where a dangling fact link
  falls back to a link to the document itself.
* `check_text` / `check_vault` report every fact citation in a page — found, not found, disputed —
  without changing the page. The app runs it when it shows a page; `watchdog check-citations`
  runs it as a maintenance check.

Uncited sentences are allowed and never flagged: AI-written text is labelled as such, and may
connect or frame what the cited facts say. Disputed facts are never hidden, only labelled.
Nothing here calls a model or writes a file.
"""

from __future__ import annotations

import re
from pathlib import Path

from watchdog.pipeline import entity_facts

# A model's short citation: `f:` plus at least four hex characters of a D271 hash, and the
# duplicate suffix when it has one. One bracket may hold several (`[f:3a9c, f:77b1]`), and a run of
# brackets (`[f:3a9c][f:77b1]`) is rendered as one group.
_ONE = r"f:[0-9a-f]{4,12}(?::\d+)?"
_GROUP = rf"\[\s*{_ONE}(?:\s*[,;]\s*{_ONE})*\s*\]"
SHORT_RUN_RE = re.compile(rf"[ \t]*{_GROUP}(?:[ \t]*{_GROUP})*", re.IGNORECASE)
_ONE_RE = re.compile(_ONE, re.IGNORECASE)

# A wikilink to a fact's line: `[[<note>#^f-<hash>(-<n>)|label]]`.
FACT_LINK_RE = re.compile(r"\[\[([^\]|#\n]+)#\^(f-[0-9a-f]{4,}(?:-\d+)?)(?:\|([^\]\n]*))?\]\]")

DISPUTED = "disputed"
# Appended after a contradiction side whose fact the reporter disputes. `resolutions._callout_text`
# strips it (and fact fragments), so a callout's id never depends on a mark or a fact link.
CALLOUT_DISPUTED = " · *disputed*"
NOT_FOUND = "source not found"


def block_id(fid: str) -> str:
    """The Obsidian block id for a fact's line (`f-<hash>`, plus `-<n>` for an exact duplicate)."""
    bits = fid.split(":")
    h = bits[3] if len(bits) > 3 else re.sub(r"[^a-z0-9]", "", fid.lower())[-10:]
    return f"f-{h}" + (f"-{bits[4]}" if len(bits) > 4 else "")


def is_disputed(fact: dict | None) -> bool:
    return ((fact or {}).get("mark") or {}).get("status") == "disputed"


def label(fact: dict) -> str:
    """The compact marker a citation shows: the page, and "disputed" when the reporter disputes
    the fact."""
    text = f"p. {fact['page']}" if fact.get("page") else "source"
    return f"{text}, {DISPUTED}" if is_disputed(fact) else text


def link(fact: dict) -> str:
    """`[[documents/<slug>#^f-<hash>|p. 4]]` for a fact with a document note."""
    return f"[[{fact['note']}#^{block_id(fact['id'])}|{label(fact)}]]"


def new_stats() -> dict:
    return {"cited": 0, "linked": 0, "unknown": 0, "missing": 0, "disputed": 0}


def _merge(into: dict, more: dict) -> dict:
    for k, v in more.items():
        into[k] = into.get(k, 0) + v
    return into


def render_short(text: str, refs: dict, lookup, stats: dict | None = None) -> tuple[str, dict]:
    """Replace each run of short citations in `text` with links to the facts. `refs` maps a short
    ref (`f:3a9c`) to a D271 id; `lookup(fid)` returns the current fact or None. A run renders as
    ` (p. 2; p. 4)`, each marker a link; a ref not in `refs`, or whose fact is gone, is left out,
    and a run with nothing left disappears. Returns the text and counts: `cited` (refs seen),
    `linked`, `unknown` (not in the map), `missing` (fact gone) and `disputed` (linked to a fact
    the reporter disputes)."""
    stats = stats if stats is not None else new_stats()
    refs = {str(k).lower(): v for k, v in (refs or {}).items()}

    def sub(m: re.Match) -> str:
        links: list[str] = []
        for ref in _ONE_RE.findall(m.group(0)):
            stats["cited"] += 1
            fid = refs.get(ref.lower())
            if not fid:
                stats["unknown"] += 1
                continue
            fact = lookup(fid)
            if not fact or not fact.get("note"):
                stats["missing"] += 1
                continue
            stats["linked"] += 1
            stats["disputed"] += is_disputed(fact)
            made = link(fact)
            if made not in links:
                links.append(made)
        return f" ({'; '.join(links)})" if links else ""

    return SHORT_RUN_RE.sub(sub, text or ""), stats


def match_short(ref: str, facts: list[dict]) -> dict | None:
    """The one fact in `facts` whose D271 id the short ref (`f:3a9c`, `[f:3a9c:2]`) is a window
    onto, or None when none or several match. Used where the map from short ref to id was not
    kept (a contradiction names its document, so matching within it is unambiguous)."""
    m = re.fullmatch(r"\[?\s*f:([0-9a-f]{4,12})(?::(\d+))?\s*\]?", (ref or "").strip(), re.IGNORECASE)
    if not m:
        return None
    hexpart, dup = m.group(1).lower(), m.group(2)
    hits = []
    for f in facts:
        bits = f["id"].split(":")
        if len(bits) > 3 and bits[3].startswith(hexpart) and (bits[4] if len(bits) > 4 else None) == dup:
            hits.append(f)
    return hits[0] if len(hits) == 1 else None


class Resolver:
    """Finds the fact a citation link names: `documents/<slug>` plus a block id (or an entity note
    plus a block id, for a link to an entity note's Facts line). Read-only and cached."""

    def __init__(self, vault: Path, index: entity_facts.FactIndex | None = None):
        self.index = index or entity_facts.FactIndex(vault)
        self._facts: dict[str, dict[str, dict]] = {}
        self._entity_by_note: dict[str, str] | None = None

    def _note_facts(self, target: str) -> dict[str, dict]:
        t = target.strip().replace("\\", "/").removesuffix(".md")
        if t not in self._facts:
            facts: list[dict] = []
            if t.startswith("documents/"):
                sha = self.index.sha_for_note(t)
                facts = self.index.document_facts(sha) if sha else []
            elif t.startswith("entities/"):
                if self._entity_by_note is None:
                    self._entity_by_note = {e.get("note_path"): eid for eid, e in self.index.entities.items()
                                            if isinstance(e, dict) and e.get("note_path")}
                eid = self._entity_by_note.get(t)
                facts = self.index.facts_for(eid) if eid else []
            self._facts[t] = {block_id(f["id"]): f for f in facts}
        return self._facts[t]

    def resolve(self, target: str, block: str) -> dict | None:
        return self._note_facts(target).get(block)


def annotate_prose(text: str, resolver: Resolver, stats: dict | None = None) -> tuple[str, dict]:
    """Check fact links already written into prose: a dangling one is removed (with a space before
    it, and an empty pair of brackets left around it), a disputed one gets "disputed" in its
    label. Counts as `render_short` does (`unknown` is not used)."""
    stats = stats if stats is not None else new_stats()

    def sub(m: re.Match) -> str:
        stats["cited"] += 1
        fact = resolver.resolve(m.group(1), m.group(2))
        if fact is None:
            stats["missing"] += 1
            return "\x00"
        stats["linked"] += 1
        if not is_disputed(fact):
            return m.group(0)
        stats["disputed"] += 1
        text = (m.group(3) or "").strip() or label(fact).removesuffix(f", {DISPUTED}")
        if DISPUTED not in text.lower():
            text += f", {DISPUTED}"
        return f"[[{m.group(1)}#^{m.group(2)}|{text}]]"

    out = FACT_LINK_RE.sub(sub, text or "")
    if "\x00" in out:
        out = re.sub(r"[ \t]*\(\s*(?:\x00\s*[;,]?\s*)+\)", "", out)     # "(\x00; \x00)" → ""
        out = re.sub(r"\s*[;,]\s*\x00|\x00\s*[;,]\s*", "", out)          # one of several in a run
        out = re.sub(r"[ \t]*\x00", "", out)
    return out, stats


def annotate_callouts(body: str, resolver: Resolver) -> str:
    """A contradiction body with each side's fact link checked: a disputed fact's side ends in
    " · *disputed*"; a fact that no longer exists leaves a plain link to its document."""
    out = []
    for line in (body or "").split("\n"):
        disputed = False
        line = line.removesuffix(CALLOUT_DISPUTED)     # a label a hand-copied callout carried

        def sub(m: re.Match) -> str:
            nonlocal disputed
            fact = resolver.resolve(m.group(1), m.group(2))
            if fact is None:
                return f"[[{m.group(1)}|{m.group(3)}]]" if m.group(3) else f"[[{m.group(1)}]]"
            disputed = disputed or is_disputed(fact)
            return m.group(0)
        line = FACT_LINK_RE.sub(sub, line)
        if disputed:
            line += CALLOUT_DISPUTED
        out.append(line)
    return "\n".join(out)


# ── checking a page ──────────────────────────────────────────────────────────────────────────

def _summary(fact: dict) -> dict:
    mark = fact.get("mark") or {}
    return {"id": fact["id"], "sha": fact["sha"], "fact": fact.get("fact") or "",
            "page": fact.get("page"), "title": fact.get("title"), "note": fact.get("note"),
            "date": fact.get("date"), "basis": fact.get("basis"),
            "mark": mark.get("status"), "passage": fact.get("quote") or (
                fact.get("passage") if fact.get("passage_method") == "matched" else None),
            "passage_page": fact.get("passage_page")}


def check_link(resolver: Resolver, target: str, block: str) -> dict:
    """One citation's status: `found` (with the fact), or `not_found`."""
    fact = resolver.resolve(target, block)
    if fact is None:
        return {"target": target, "block": block, "status": "not_found", "fact": None}
    return {"target": target, "block": block, "status": "found", "fact": _summary(fact),
            "disputed": is_disputed(fact)}


def check_text(text: str, resolver: Resolver) -> dict:
    """Every fact citation in `text`, in order, with its status, plus counts: `citations`,
    `found`, `not_found`, `disputed`. Short refs (`[f:3a9c]`) left in a page have no map to
    resolve them, so they are counted as `unresolved_short`. Uncited text is not reported."""
    items = []
    for m in FACT_LINK_RE.finditer(text or ""):
        item = check_link(resolver, m.group(1).strip(), m.group(2))
        item["label"] = m.group(3)
        items.append(item)
    short = sum(len(_ONE_RE.findall(g.group(0))) for g in SHORT_RUN_RE.finditer(text or ""))
    return {"items": items, "citations": len(items),
            "found": sum(1 for i in items if i["status"] == "found"),
            "not_found": sum(1 for i in items if i["status"] == "not_found"),
            "disputed": sum(1 for i in items if i.get("disputed")),
            "unresolved_short": short}


# Pages a Claude session or Watchdog writes as prose that may cite facts.
CHECKED_DIRS = ("queries", "wiki", "briefings")
CHECKED_FILES = ("hot.md",)


def pages(vault: Path) -> list[Path]:
    vault = Path(vault)
    out = [vault / f for f in CHECKED_FILES if (vault / f).is_file()]
    for d in CHECKED_DIRS:
        if (vault / d).is_dir():
            out += sorted(p for p in (vault / d).rglob("*.md") if p.is_file())
    return out


def check_vault(vault: Path, paths=None) -> dict:
    """Check every page that may cite facts (or `paths`): {"pages": [{path, …check_text}], totals}.
    Pages without a fact citation are left out of `pages` but counted in `checked`."""
    vault = Path(vault)
    resolver = Resolver(vault)
    files = [Path(p) if Path(p).is_absolute() else vault / p for p in paths] if paths else pages(vault)
    reports, totals = [], {"checked": 0, "citations": 0, "found": 0, "not_found": 0, "disputed": 0,
                           "unresolved_short": 0}
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        totals["checked"] += 1
        r = check_text(text, resolver)
        for k in ("citations", "found", "not_found", "disputed", "unresolved_short"):
            totals[k] += r[k]
        if r["citations"] or r["unresolved_short"]:
            try:
                rel = f.resolve().relative_to(vault.resolve()).as_posix()
            except ValueError:
                rel = str(f)
            reports.append({"path": rel, **r})
    return {"pages": reports, **totals}
