"""`verify.*` — the verification ledger (D271): marking facts Verified, Disputed or Can't verify,
and the vault-wide view of what has been checked.

Marks are written in-process through `pipeline/verification.mark`, the library function `watchdog
verify-fact` calls (I10), which takes the ledger's lock and regenerates `verification.md`.
"""

from __future__ import annotations

from watchdog.gui import vaultio
from watchdog.gui.rpc import RpcError, method
from watchdog.gui.vaultio import require_vault


@method("verify.mark")
def mark(vault: str, id: str, status: str | None, note: str | None = None) -> dict:
    """Set or clear (`status` null or "") one fact's mark. Returns `Mark & {id}`."""
    from watchdog.pipeline import verification

    v = require_vault(vault)
    if not isinstance(id, str) or not id.startswith("fact:"):
        raise RpcError("That is not a fact id.", code="bad_params")
    try:
        parsed = verification.parse_status(status if isinstance(status, str) else None)
        entry = verification.mark(v, id, parsed, note=note if isinstance(note, str) else None)
    except LookupError:
        raise RpcError("That fact is no longer in this investigation. It may have changed when "
                       "its document was processed again.", code="not_found")
    except ValueError as e:
        raise RpcError(str(e), code="bad_value")
    return {"id": id, "status": entry.get("status"), "note": entry.get("note"),
            "by": entry.get("by"), "at": entry.get("at")}


@method("verify.facts")
def facts(vault: str) -> dict:
    """Every current fact with its mark and passage state, the progress summary, and marks whose
    fact no longer exists (orphans), for the Verification tab in Review."""
    from watchdog.pipeline import verification
    from watchdog.pipeline.passages import METHODS

    v = require_vault(vault)
    docs = vaultio.load_documents(v)
    data = verification.load(v)
    marks = {fid: m for fid, m in data["marks"].items()
             if isinstance(m, dict) and m.get("status") in verification.STATUSES}
    rows = []
    for sha, rec in docs.items():
        if not isinstance(rec, dict):
            continue
        facts_ = verification.document_facts(v, sha, rec)
        for fid, f in zip(verification.fact_ids(sha, facts_), facts_):
            m = marks.get(fid)
            page = f.get("page") if isinstance(f.get("page"), int) else None
            rows.append({
                "id": fid, "fact": f["fact"], "page": page,
                "basis": "inferred" if f.get("basis") == "inferred" else "stated",
                "sha": sha, "filename": rec.get("filename") or sha[:12],
                "title": rec.get("title") or rec.get("filename") or sha[:12],
                "passage_method": f.get("passage_method") if f.get("passage_method") in METHODS else None,
                "passage_page": f.get("passage_page") if isinstance(f.get("passage_page"), int) else None,
                "mark": {k: m.get(k) for k in ("status", "note", "by", "at")} if m else None,
            })
    rows.sort(key=lambda r: ((r["title"] or "").lower(), r["sha"]))   # stable: reading order within each
    all_facts = {r["id"]: {"passage_method": r["passage_method"]} for r in rows}
    orphans = [e for e in verification.entries(v, all_facts, data) if e["orphaned"]]
    return {"summary": verification.summary(v, all_facts, data), "facts": rows,
            "orphaned": [{k: e[k] for k in ("id", "status", "note", "by", "at", "fact", "page",
                                            "sha256", "filename")} for e in orphans],
            "reporter": verification.reporter_name()}

