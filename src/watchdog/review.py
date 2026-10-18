"""What Review lists: everything waiting on the reporter, read from local files only (D252).

Five kinds:

  * contradictions — conflicting claims recorded on entity notes
  * leads          — entities named but never profiled, isolated, or carrying inferred facts
  * alerts         — unresolved watch-list hits in `briefings/alerts-*.md`
  * duplicates     — documents pre-processing's MinHash matched to an earlier one
  * merges         — two entity records that may be the same, left unmerged (D279)

Marking an item handled records its id in `resolutions.json`, so it stops re-surfacing in every
report. Marking a `merges` item handled means "not the same": the pair is never merged
automatically afterwards.
"""

import re
from pathlib import Path

from watchdog.pipeline import leads as _leads
from watchdog.pipeline import resolutions
from watchdog.pipeline.json_io import _read_json_or

KINDS = ("contradictions", "leads", "alerts", "duplicates", "merges")


_SAME_WORD = {"person": "person", "organization": "organization", "public-body": "public body",
              "place": "place", "asset": "asset", "proceeding": "proceeding"}


# The link is greedy up to the last `**` before the optional entity/count suffix, so a filename
# containing `**` stays whole.
_ALERT_LINE = re.compile(r"^- (?:\[[ xX]\] )?\*\*(?P<link>.+)\*\*(?P<rest>(?: · known entity .*?)?"
                         r"(?: \(\d+ matches\))?) ?<!--wid:(?P<rid>alert:[^>]+?)-->")


_ALERT_TERM = re.compile(r"^### `(?P<term>.+)`")


WIKILINK = re.compile(r"^\[\[(?P<target>[^|\]]+)(?:\|(?P<text>.+))?\]\]$")   # text may hold `]`


def _item(kind: str, rid: str, title: str, detail: list[str], note: str | None) -> dict:
    return {"kind": kind, "rid": rid, "title": title, "detail": detail, "note": note or None}


def _callout_lines(callout: str) -> list[str]:
    lines = [re.sub(r"^\s*>\s?", "", ln).rstrip() for ln in callout.splitlines()]
    lines = [re.sub(r"^\[!contradiction\]\s*", "", ln) for ln in lines]
    return [ln for ln in lines if ln.strip()]


def contradictions(found: dict) -> list[dict]:
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
                link = WIKILINK.match(m.group("link"))
                note = link.group("target") if link else None
                name = (link.group("text") or link.group("target")) if link else m.group("link")
                title = f"`{term}` in {name}" if term else name
                current = seen[rid] = _item("alerts", rid, title, [], note)
                current["term"] = term
            elif current is not None and line.startswith("  - "):
                current["detail"].append(line[4:])
            elif not line.strip():
                current = None
    return list(seen.values())


def duplicates(vault: Path, resolved: frozenset[str]) -> list[dict]:
    docs = _read_json_or(vault / ".watchdog" / "registry" / "documents.json", {})
    out = []
    for sha, d in sorted(docs.items(), key=lambda kv: kv[1].get("filename") or ""):
        match = d.get("near_duplicate_of")
        rid = resolutions.duplicate_id(sha)
        if not match or rid in resolved:
            continue
        link = WIKILINK.match(match)
        other = (link.group("text") or link.group("target")) if link else match
        out.append(_item("duplicates", rid, d.get("filename") or sha[:12],
                         [f"Closely matches {other} — confirm whether they are the same document"],
                         d.get("document_note")))
    return out


def same_entities(vault: Path, resolved: frozenset[str]) -> list[dict]:
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
            out += contradictions(found)
        elif kind == "leads":
            out += _lead_items(found)
        elif kind == "alerts":
            out += _alerts(vault, resolved)
        elif kind == "merges":
            out += same_entities(vault, resolved)
        else:
            out += duplicates(vault, resolved)
    return out


def count_open_duplicates(vault: Path) -> int:
    return len(duplicates(vault, resolutions.resolved_ids(vault)))


def count_open_same_entities(vault: Path, resolved: frozenset[str] | None = None) -> int:
    from watchdog.pipeline import merge_log
    return len(merge_log.open_candidates(vault, resolved=resolved))

