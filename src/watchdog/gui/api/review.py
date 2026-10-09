"""`review.*` — what is waiting on the journalist: contradictions, leads, watch-list hits and
possible duplicates, plus marking them handled.

The items come from `cmd/review.open_items` and the lead sweep from `pipeline/leads.scan`, so the
app and `watchdog review` always list the same things. Resolving writes the same
`resolutions.json` store the terminal walk writes and ticks the matching checkboxes in the
briefings (`resolutions.tick_in_briefings`), so the files and the store agree.
"""

from __future__ import annotations

from watchdog.gui import vaultio
from watchdog.gui.rpc import RpcError, method
from watchdog.gui.vaultio import require_vault

_RID_KINDS = {"lead": "leads", "contradiction": "contradictions", "alert": "alerts",
              "duplicate": "duplicates", "request": "requests", "same": "merges"}


def _rids(rids) -> list[str]:
    if isinstance(rids, str):
        rids = [rids]
    if not isinstance(rids, list) or not all(isinstance(r, str) for r in rids):
        raise RpcError("rids must be a list of resolution ids", code="bad_params")
    clean = [r.strip() for r in rids if r.strip()]
    if not clean:
        raise RpcError("No items were given.", code="bad_params")
    return clean


@method("review.items")
def items(vault: str, kinds: list[str] | None = None) -> dict:
    from watchdog.cmd.review import KINDS, open_items

    v = require_vault(vault)
    wanted = tuple(KINDS) if not kinds else tuple(kinds)
    bad = [k for k in wanted if k not in KINDS]
    if bad:
        raise RpcError(f"Unknown kind: {bad[0]}. Choose from {', '.join(KINDS)}.", code="bad_params")
    found = open_items(v, wanted)
    return {"items": found,
            "counts": {k: sum(1 for i in found if i["kind"] == k) for k in KINDS if k in wanted}}


@method("review.resolve")
def resolve(vault: str, rids: list[str]) -> dict:
    from watchdog.pipeline import resolutions

    v = require_vault(vault)
    ids = _rids(rids)
    added = resolutions.resolve(v, ids, label="review")
    resolutions.tick_in_briefings(v, ids)
    return {"resolved": added}


@method("review.unresolve")
def unresolve(vault: str, rids: list[str]) -> dict:
    from watchdog.pipeline import resolutions

    v = require_vault(vault)
    ids = _rids(rids)
    removed = resolutions.unresolve(v, ids)
    resolutions.tick_in_briefings(v, ids, ticked=False)
    return {"unresolved": removed}


@method("review.resolved")
def resolved(vault: str) -> dict:
    """What `watchdog review resolve --list` shows, newest first."""
    from watchdog.pipeline import resolutions

    v = require_vault(vault)
    out = []
    for rid, meta in resolutions.load(v).get("resolved", {}).items():
        meta = meta if isinstance(meta, dict) else {}
        out.append({"rid": rid, "label": meta.get("label") or "", "resolved_at": meta.get("at") or None,
                    "kind": _RID_KINDS.get(rid.split(":", 1)[0], "other")})
    out.sort(key=lambda r: r["rid"])
    out.sort(key=lambda r: r["resolved_at"] or "", reverse=True)
    return {"items": out}


@method("review.sync")
def sync(vault: str) -> dict:
    """Import ticked and cleared checkboxes from the briefings (`watchdog review resolve --sync`)."""
    from watchdog.pipeline import resolutions

    added, removed = resolutions.sync_from_briefings(require_vault(vault))
    return {"resolved": added, "unresolved": removed}


@method("review.leads")
def leads(vault: str) -> dict:
    from watchdog.pipeline import leads as _leads

    found = _leads.scan(require_vault(vault))
    return {**found, "total": _leads.total(found)}


@method("review.watchlist")
def watchlist(vault: str) -> dict:
    from watchdog.pipeline import watchlist as _watchlist

    v = require_vault(vault)
    return {"terms": [t["term"] for t in _watchlist.load_terms(v)],
            "text": vaultio.read_text(v / "watchlist.md")}


@method("review.mergeLog")
def merge_log(vault: str, limit: int = 200) -> dict:
    """Recent entries of the merge log (D279), newest first, each with its documents and facts
    resolved for display. `undo_available` says whether `merge_undo` can split the entry back
    exactly now, and `undo_reason` why not when it cannot (D280); `undone` is the undo record."""
    from watchdog.pipeline import merge_log as _log
    from watchdog.pipeline import merge_undo, verification
    from watchdog.pipeline.verification import all_facts

    v = require_vault(vault)
    data = _log.load(v)
    marks = verification.marks(v)
    ents = vaultio.load_entities(v)
    docs = vaultio.load_documents(v)
    facts = None
    out = []
    merges = sorted(data.get("merges", []), key=lambda m: m.get("last_at") or "", reverse=True)
    for m in merges[:max(1, min(int(limit or 200), 1000))]:
        shas: list[str] = []
        fact_ids: list[str] = []
        identifier, shared = None, []
        for occ in m.get("occurrences") or []:
            for sha in occ.get("documents") or ([occ["sha"]] if occ.get("sha") else []):
                if sha not in shas:
                    shas.append(sha)
            fact_ids += [f for f in occ.get("facts") or [] if f not in fact_ids]
            identifier = identifier or occ.get("identifier")
            shared = shared or occ.get("shared") or []
        if fact_ids and facts is None:
            facts = all_facts(v)
        keep = m.get("keep") or {}
        reason = ("This merge log was written by a newer version of Watchdog." if _log.too_new(data)
                  else merge_undo.check(v, m, ents))
        out.append({
            "id": m.get("id"), "keep": {**keep, "exists": keep.get("id") in ents,
                                        "note": (ents.get(keep.get("id")) or {}).get("note_path")},
            "merged": m.get("merged") or {}, "tier": m.get("tier"), "decided_by": m.get("decided_by"),
            "rule": m.get("rule"), "reason": m.get("reason") or "", "model": m.get("model"),
            "reporter": m.get("reporter"), "same_name": bool(m.get("same_name")),
            "at": m.get("last_at"), "first_at": m.get("first_at"),
            "identifier": identifier, "shared": shared,
            "documents": [{"sha": s, "title": (docs.get(s) or {}).get("title")
                           or (docs.get(s) or {}).get("filename") or s[:12]} for s in shas[:50]],
            "document_count": len(shas),
            "facts": [{"id": f, "fact": facts[f]["fact"], "page": facts[f]["page"],
                       "sha": facts[f]["sha256"], "title": facts[f]["title"],
                       "disputed": (verification.attach(marks.get(f), facts[f]) or {}).get("status") == "disputed"}
                      for f in fact_ids[:8] if facts and f in facts],
            "undo_available": reason is None,
            "undo_reason": reason,
            "undone": m.get("undone") if isinstance(m.get("undone"), dict) else None,
        })
    return {"merges": out, "total": len(merges), "too_new": _log.too_new(data),
            "undo_available": not _log.too_new(data)}


@method("review.mergePreview")
def merge_preview(vault: str, keep: str, merge: str) -> dict:
    """Both entities side by side before `merge-entities`, and whether both carry a Summary
    (the merge keeps one and suggests `/watchdog-entity` to reconcile them)."""
    from watchdog.pipeline import resolutions

    v = require_vault(vault)
    if keep == merge:
        raise RpcError("Choose two different entities to merge.", code="bad_params")
    ents = vaultio.load_entities(v)
    for eid in (keep, merge):
        if not isinstance(ents.get(eid), dict):
            raise RpcError(f"Entity “{eid}” isn't in this investigation.", code="not_found")
    resolved_ids = resolutions.resolved_ids(v)
    rows = {eid: vaultio.entity_row(v, eid, ents[eid], resolved_ids) for eid in (keep, merge)}
    return {"keep": rows[keep], "merge": rows[merge],
            "both_have_summary": rows[keep]["has_summary"] and rows[merge]["has_summary"],
            "type_mismatch": rows[keep]["type"] != rows[merge]["type"]}
