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
              "duplicate": "duplicates", "request": "requests"}


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
