"""Deterministic lead sweep (#155, slice 1).

A whole-vault, registry-only pass that surfaces investigative leads without a model call —
the cheap, on-brand complement to the model-driven `/watchdog-surface`. Three signals, all
read straight from `.watchdog/Registry/entities.json`:

  * **Named but never profiled** — a relationship `target_id` that no entity record exists
    for. The extractor named (say) a company as the target of a "Director" role but never
    profiled it, so it has page-cited mentions yet no note of its own. A lead: go find records.
  * **Mentioned often but unconnected** — an entity that appears in several documents
    (`appears_in` ≥ `_ISOLATED_MIN_DOCS`) yet carries no relationships at all. Why does this
    name keep recurring in isolation?
  * **Unresolved contradictions** — an entity carrying contradiction flags recorded at ingest,
    listed so they don't sit unreviewed.

`scan` loads the registry and returns the three lists; `write_leads` snapshots them to
`briefings/leads-<date>.md` (overwrite, not append — it is current-state, not an event log).
The model-driven whole-vault pieces (cross-document contradiction re-check, stale/superseded
claims) are deliberately out of scope here — see DECISIONS D40 and #155.
"""

import datetime
import json
from pathlib import Path

_ISOLATED_MIN_DOCS = 3   # appears-in count below which an unconnected entity is just long-tail noise


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def find_leads(entities_reg: dict) -> dict:
    """Pure: derive the three lead lists from an entity-registry dict."""
    unprofiled: dict[str, dict] = {}
    isolated: list[dict] = []
    contradictions: list[dict] = []

    for eid, ent in entities_reg.items():
        roles = ent.get("roles", [])
        for role in roles:
            tid = role.get("target_id")
            if not tid or tid in entities_reg:
                continue   # a profiled target (incl. every reverse role) is not a lead
            rec = unprofiled.setdefault(tid, {
                "id": tid, "name": role.get("target_name") or tid,
                "mentioned_by": set(), "docs": set(),
            })
            rec["mentioned_by"].add(ent.get("name") or eid)
            if role.get("source_sha256"):
                rec["docs"].add(role["source_sha256"])

        if not roles and len(ent.get("appears_in", [])) >= _ISOLATED_MIN_DOCS:
            isolated.append({"id": eid, "name": ent.get("name") or eid,
                             "doc_count": len(ent["appears_in"])})

        if ent.get("contradictions"):
            contradictions.append({"id": eid, "name": ent.get("name") or eid,
                                   "note_path": ent.get("note_path", ""),
                                   "count": len(ent["contradictions"])})

    unprofiled_list = [
        {"id": v["id"], "name": v["name"],
         "mentioned_by": sorted(v["mentioned_by"]), "doc_count": len(v["docs"])}
        for v in unprofiled.values()
    ]
    unprofiled_list.sort(key=lambda x: (-x["doc_count"], x["name"].lower()))
    isolated.sort(key=lambda x: (-x["doc_count"], x["name"].lower()))
    contradictions.sort(key=lambda x: (-x["count"], x["name"].lower()))

    return {"unprofiled": unprofiled_list, "isolated": isolated, "contradictions": contradictions}


def total(leads: dict) -> int:
    return sum(len(v) for v in leads.values())


def scan(vault: Path) -> dict:
    """Run the sweep over a vault's entity registry."""
    return find_leads(_load_json(vault / ".watchdog" / "Registry" / "entities.json"))


def _format(leads: dict, now: datetime.datetime) -> str:
    lines = [f"# Investigative leads — {now:%Y-%m-%d}\n",
             "*Deterministic whole-vault sweep of the entity registry — no model, "
             "regenerated on each ingest.*\n"]

    if leads["unprofiled"]:
        lines.append("\n## Named but never profiled\n")
        lines.append("Relationship targets with page-cited mentions but no entity note of their own.\n")
        for u in leads["unprofiled"]:
            by = ", ".join(u["mentioned_by"])
            docs = f"{u['doc_count']} document{'s' if u['doc_count'] != 1 else ''}"
            lines.append(f"- **{u['name']}** — named by {by} · {docs}")

    if leads["isolated"]:
        lines.append("\n## Mentioned often but unconnected\n")
        lines.append("Entities recurring across documents with no extracted relationships.\n")
        for i in leads["isolated"]:
            lines.append(f"- **{i['name']}** — appears in {i['doc_count']} documents · no relationships")

    if leads["contradictions"]:
        lines.append("\n## Unresolved contradictions\n")
        lines.append("Entities carrying contradiction flags recorded at ingest.\n")
        for c in leads["contradictions"]:
            link = f"[[{c['note_path']}|{c['name']}]]" if c["note_path"] else f"**{c['name']}**"
            n = c["count"]
            lines.append(f"- {link} — {n} flagged conflict{'s' if n != 1 else ''}")

    return "\n".join(lines) + "\n"


def write_leads(vault: Path, leads: dict) -> str | None:
    """Snapshot the leads to ``briefings/leads-<date>.md`` (overwrite). Returns the relpath,
    or ``None`` when there are no leads (no file written)."""
    if not total(leads):
        return None
    now = datetime.datetime.now()
    relpath = f"briefings/leads-{now:%Y-%m-%d}.md"
    path = vault / relpath
    path.parent.mkdir(exist_ok=True)
    path.write_text(_format(leads, now), encoding="utf-8")
    return relpath
