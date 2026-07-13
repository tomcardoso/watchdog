"""Document-request ledger (#365).

Ingest already told the journalist "you should get the hearing transcript this order cites" —
but only as prose buried in the briefing's leads, indistinguishable from an open-ended
investigative thread. A **document request** is a different kind of object: a concrete,
known-to-exist artifact to go and acquire (a document type, the specific thing, why it matters,
and often where to get it), not a thread to investigate. Splitting it out follows §I1: the model
authors the content (``type``/``what``/``why_it_matters``/``likely_source``); Python stamps the
id and provenance.

Ledger at ``.watchdog/Registry/requests.json``::

    {"schema_version": 1, "requests": {"<rid>": {
        "type": str, "what": str, "why_it_matters": str, "likely_source": str|None,
        "source_sha256": str, "source_filename": str, "document_note": str, "added": "<iso>"
    }}}

Request ids (``rid``, built by ``resolutions.request_id``) are content-keyed on the source
document plus the normalized ``what`` text, so re-recording the same request on a repair retry
converges instead of duplicating. Requests are resolved manually — a ticked checkbox or
``watchdog resolve`` — never auto-closed by a fuzzy match, and once recorded they are never
re-fed into a model prompt (D111): ``write_requests`` renders only the still-open ones to the
vault-root ``requests.md`` (a current-state view, overwritten each ingest, not an event log).
"""

import datetime
import json
from pathlib import Path

from watchdog.pipeline import resolutions

_SCHEMA_VERSION = 1


def _path(vault: Path) -> Path:
    return vault / ".watchdog" / "Registry" / "requests.json"


def load(vault: Path) -> dict:
    """Load the ledger, returning a fresh empty structure on missing/corrupt file."""
    try:
        data = json.loads(_path(vault).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": _SCHEMA_VERSION, "requests": {}}
    if not isinstance(data, dict) or not isinstance(data.get("requests"), dict):
        return {"schema_version": _SCHEMA_VERSION, "requests": {}}
    return data


def _save(vault: Path, data: dict) -> None:
    path = _path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.rename(path)


def record(vault: Path, items: list, *, sha256: str, filename: str, document_note: str) -> list[str]:
    """Stamp and persist one document's ``document_requests`` into the ledger.

    Skips anything already in the ledger (rid-keyed, so a repair retry / re-write of the same
    document converges instead of duplicating) and skips malformed entries defensively — a bad
    request must never fail an extraction. Returns the newly-added rids.
    """
    data = load(vault)
    stored = data.setdefault("requests", {})
    now = datetime.datetime.now().isoformat(timespec="seconds")
    added: list[str] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        what = (item.get("what") or "").strip()
        if not what:
            continue
        rid = resolutions.request_id(sha256, what)
        if rid in stored:
            continue
        stored[rid] = {
            "type": (item.get("type") or "").strip(),
            "what": what,
            "why_it_matters": (item.get("why_it_matters") or "").strip(),
            "likely_source": (item.get("likely_source") or "").strip() or None,
            "source_sha256": sha256,
            "source_filename": filename,
            "document_note": document_note,
            "added": now,
        }
        added.append(rid)
    if added:
        _save(vault, data)
    return added


def open_requests(vault: Path) -> list[dict]:
    """Ledger entries not yet resolved (``resolutions.resolved_ids``), each carrying its
    ``rid``, sorted newest-added first, then by ``what``."""
    resolved = resolutions.resolved_ids(vault)
    items = [
        {**entry, "rid": rid}
        for rid, entry in load(vault).get("requests", {}).items()
        if rid not in resolved
    ]
    items.sort(key=lambda r: (r.get("what") or "").lower())
    items.sort(key=lambda r: r.get("added") or "", reverse=True)
    return items


def _format(open_: list[dict]) -> str:
    by_type: dict[str, list[dict]] = {}
    for r in open_:
        by_type.setdefault(r.get("type") or "Other", []).append(r)

    lines = [
        "# Documents to request\n",
        "*Regenerated on each ingest — lists only what is still outstanding.*\n",
        "*Tick a box and run `watchdog resolve --sync` (or `watchdog resolve <id>`) once you "
        "have the document.*\n",
    ]
    for dtype in sorted(by_type):
        lines.append(f"\n## {dtype}\n")
        for r in by_type[dtype]:
            lines.append(f"- [ ] **{r['what']}** <!--wid:{r['rid']}-->")
            if r.get("why_it_matters"):
                lines.append(f"  - Why: {r['why_it_matters']}")
            if r.get("likely_source"):
                lines.append(f"  - Likely source: {r['likely_source']}")
            if r.get("document_note") and r.get("source_filename"):
                lines.append(f"  - Referenced in [[{r['document_note']}|{r['source_filename']}]]")
    return "\n".join(lines) + "\n"


def write_requests(vault: Path) -> str | None:
    """Render the vault's still-open document requests into ``requests.md`` (overwrite — a
    current-state view, not an event log). Removes any existing file and returns ``None`` when
    there is nothing open."""
    open_ = open_requests(vault)
    path = vault / "requests.md"
    if not open_:
        path.unlink(missing_ok=True)
        return None
    path.write_text(_format(open_), encoding="utf-8")
    return "requests.md"
