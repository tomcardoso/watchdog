"""Deterministic lead sweep (#155, slice 1; #221 added the fourth signal).

A whole-vault, registry-only pass that surfaces investigative leads without a model call —
the cheap, on-brand complement to the model-driven `/watchdog-surface`. Four signals, all
read straight from `.watchdog/registry/entities.json`:

  * **Named but never profiled** — a relationship `target_id` that no entity record exists
    for. The extractor named (say) a company as the target of a "Director" role but never
    profiled it, so it has page-cited mentions yet no note of its own. A lead: go find records.
  * **Mentioned often but unconnected** — an entity that appears in several documents
    (`appears_in` ≥ `_ISOLATED_MIN_DOCS`) yet carries no relationships at all. Why does this
    name keep recurring in isolation?
  * **Unresolved contradictions** — an entity carrying contradiction flags recorded at ingest,
    listed so they don't sit unreviewed.
  * **Inferred facts to verify** — an entity carrying a `roles`/`timeline_events` entry with
    `basis: inferred` (D34: "a lead to verify, not a finding"). Surfaces the pipeline's own
    verify-me markers so they reach the report a journalist actually reads instead of sitting
    unread in a note body.

`scan` loads the registry and returns the four lists; `write_leads` snapshots them to
`briefings/leads-<date>.md` (overwrite, not append — it is current-state, not an event log).
The model-driven whole-vault pieces (cross-document contradiction re-check, stale/superseded
claims) are deliberately out of scope here — see DECISIONS D40 and #155.
"""

import datetime
import re
from pathlib import Path

from watchdog.pipeline import resolutions
from watchdog.pipeline.watchlist import _load_json

_ISOLATED_MIN_DOCS = 3   # appears-in count below which an unconnected entity is just long-tail noise


def _callout_summary(callout: str) -> str:
    """A one-line human summary of a `> [!contradiction]` callout for the report."""
    lines = [re.sub(r"^\s*>\s?", "", ln).strip() for ln in callout.splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        return "contradiction"
    first = re.sub(r"^\[!contradiction\]\s*", "", lines[0]).strip()
    return first or (lines[1] if len(lines) > 1 else "contradiction")


def _inferred_claims(ent: dict) -> list[str]:
    """Text for every basis:inferred role/timeline_event on an entity, in registry order."""
    claims = []
    for role in ent.get("roles", []):
        if role.get("basis") == "inferred":
            target = role.get("target_name") or role.get("target_id") or "?"
            claims.append(f"{role.get('relationship', '?')} → {target}")
    for ev in ent.get("timeline_events", []):
        if ev.get("basis") == "inferred":
            claims.append(f"{ev.get('date', '?')}: {ev.get('event', '?')}")
    return claims


def find_leads(entities_reg: dict, resolved: frozenset[str] = frozenset()) -> dict:
    """Pure: derive the four lead lists from an entity-registry dict.

    ``resolved`` is the set of acknowledged resolution ids (#266); any lead — or individual
    contradiction callout — whose id is in it drops out of the active list. Every returned item
    carries its own ``rid`` so the report can print a copyable/checkbox-able marker."""
    unprofiled: dict[str, dict] = {}
    isolated: list[dict] = []
    contradictions: list[dict] = []
    inferred: list[dict] = []

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
            rid = resolutions.lead_id("isolated", eid)
            if rid not in resolved:
                isolated.append({"id": eid, "name": ent.get("name") or eid,
                                 "doc_count": len(ent["appears_in"]), "rid": rid})

        if ent.get("contradictions"):
            callouts = [
                {"summary": _callout_summary(c), "rid": resolutions.contradiction_id(c)}
                for c in ent["contradictions"]
            ]
            callouts = [c for c in callouts if c["rid"] not in resolved]
            if callouts:
                contradictions.append({"id": eid, "name": ent.get("name") or eid,
                                       "note_path": ent.get("note_path", ""),
                                       "callouts": callouts, "count": len(callouts)})

        claims = _inferred_claims(ent)
        if claims:
            rid = resolutions.lead_id("inferred", eid)
            if rid not in resolved:
                inferred.append({"id": eid, "name": ent.get("name") or eid,
                                 "note_path": ent.get("note_path", ""), "claims": claims,
                                 "rid": rid})

    unprofiled_list = [
        {"id": v["id"], "name": v["name"],
         "mentioned_by": sorted(v["mentioned_by"]), "doc_count": len(v["docs"]),
         "rid": resolutions.lead_id("unprofiled", v["id"])}
        for v in unprofiled.values()
    ]
    unprofiled_list = [u for u in unprofiled_list if u["rid"] not in resolved]
    unprofiled_list.sort(key=lambda x: (-x["doc_count"], x["name"].lower()))
    isolated.sort(key=lambda x: (-x["doc_count"], x["name"].lower()))
    contradictions.sort(key=lambda x: (-x["count"], x["name"].lower()))
    inferred.sort(key=lambda x: (-len(x["claims"]), x["name"].lower()))

    return {"unprofiled": unprofiled_list, "isolated": isolated,
            "contradictions": contradictions, "inferred": inferred}


def total(leads: dict) -> int:
    return sum(len(v) for v in leads.values())


def scan(vault: Path) -> dict:
    """Run the sweep over a vault's entity registry, minus anything already resolved (#266)."""
    return find_leads(_load_json(vault / ".watchdog" / "registry" / "entities.json"),
                      resolutions.resolved_ids(vault))


def _format(leads: dict, now: datetime.datetime) -> str:
    lines = [f"# Investigative leads — {now:%Y-%m-%d}\n",
             "*Deterministic whole-vault sweep of the entity registry — no model, "
             "regenerated on each ingest.*\n",
             "*Tick a box and run `watchdog resolve --sync` (or `watchdog resolve <id>`) to "
             "drop an item from future sweeps.*\n"]

    if leads["unprofiled"]:
        lines.append("\n## Named but never profiled\n")
        lines.append("Relationship targets with page-cited mentions but no entity note of their own.\n")
        for u in leads["unprofiled"]:
            by = ", ".join(u["mentioned_by"])
            docs = f"{u['doc_count']} document{'s' if u['doc_count'] != 1 else ''}"
            lines.append(f"- [ ] **{u['name']}** — named by {by} · {docs} <!--wid:{u['rid']}-->")

    if leads["isolated"]:
        lines.append("\n## Mentioned often but unconnected\n")
        lines.append("Entities recurring across documents with no extracted relationships.\n")
        for i in leads["isolated"]:
            lines.append(f"- [ ] **{i['name']}** — appears in {i['doc_count']} documents · "
                         f"no relationships <!--wid:{i['rid']}-->")

    if leads["contradictions"]:
        lines.append("\n## Unresolved contradictions\n")
        lines.append("Entities carrying contradiction flags recorded at ingest.\n")
        for c in leads["contradictions"]:
            link = f"[[{c['note_path']}|{c['name']}]]" if c["note_path"] else f"**{c['name']}**"
            n = c["count"]
            lines.append(f"- {link} — {n} flagged conflict{'s' if n != 1 else ''}")
            for callout in c["callouts"]:
                lines.append(f"  - [ ] {callout['summary']} <!--wid:{callout['rid']}-->")

    if leads["inferred"]:
        lines.append("\n## Inferred facts to verify\n")
        lines.append("Roles and timeline events the extractor flagged `basis: inferred` — "
                     "a lead to verify, not a finding.\n")
        for i in leads["inferred"]:
            link = f"[[{i['note_path']}|{i['name']}]]" if i["note_path"] else f"**{i['name']}**"
            lines.append(f"- [ ] {link} <!--wid:{i['rid']}-->")
            for claim in i["claims"]:
                lines.append(f"  - {claim}")

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
