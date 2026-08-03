"""Document-request ledger (#365).

Ingest already told the journalist "you should get the hearing transcript this order cites" —
but only as prose buried in the briefing's leads, indistinguishable from an open-ended
investigative thread. A **document request** is a different kind of object: a concrete,
known-to-exist artifact to go and acquire (a document type, the specific thing, why it matters,
and often where to get it), not a thread to investigate. Splitting it out follows §I1: the model
authors the content (``type``/``what``/``why_it_matters``/``likely_source``); Python stamps the
id and provenance.

Ledger at ``.watchdog/registry/requests.json``::

    {"schema_version": 2, "requests": {"<rid>": {
        "type": str, "what": str, "why_it_matters": str, "likely_source": str|None,
        "sources": [{"sha256": str, "filename": str, "document_note": str}, ...],
        "added": "<iso>"
    }}}

Request ids (``rid``, built by ``resolutions.request_id``) are content-keyed on the normalized
``what`` text alone — vault-wide, not per source document (#416) — so re-recording the same
request, whether a repair retry of the same document or a second document citing the same
artifact under the same wording, converges onto one entry instead of duplicating; each citing
document is appended to that entry's ``sources`` rather than spawning a new one. That only
catches identical wording, though — a paraphrase of the same real document still lands as a
separate entry, which ``orchestrate._post_ingest``'s document-request dedup pass (#416, D159)
catches with a model call over the current open set, judging which entries name the same
real-world document; ``merge_duplicates`` performs the fold. Resolution is otherwise manual —
a ticked checkbox or ``watchdog resolve``, never auto-closed by matching a newly-ingested
document — and a resolved or rendered request is never re-fed into any *other* model prompt
(briefing bundle, pre-flight digests, extraction): the dedup pass is a narrow, bounded exception
to that D111 rule, scoped to judging sameness among requests themselves, the same code/model
split (§I1) entity reconciliation and timeline dedup already use. ``write_requests`` renders the
still-open entries to the vault-root ``requests.md`` (a current-state view, overwritten each
ingest, not an event log).
"""

import datetime
import json
from pathlib import Path

from watchdog.pipeline import resolutions

_SCHEMA_VERSION = 2


def _path(vault: Path) -> Path:
    return vault / ".watchdog" / "registry" / "requests.json"


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

    Rids are content-keyed on the normalized ``what`` text alone (#416), so a second document
    citing the same artifact under the same wording — or a repair retry of the same document —
    converges onto the existing entry instead of duplicating; the citing document is appended to
    that entry's ``sources`` rather than dropped, so each stays traceable. Skips malformed
    entries defensively — a bad request must never fail an extraction. Returns the rids of
    entries newly created (not those that only gained an additional source).
    """
    data = load(vault)
    stored = data.setdefault("requests", {})
    now = datetime.datetime.now().isoformat(timespec="seconds")
    added: list[str] = []
    changed = False
    for item in items or []:
        if not isinstance(item, dict):
            continue
        what = (item.get("what") or "").strip()
        if not what:
            continue
        rid = resolutions.request_id(what)
        source = {"sha256": sha256, "filename": filename, "document_note": document_note}
        if rid in stored:
            sources = stored[rid].setdefault("sources", [])
            if not any(s.get("sha256") == sha256 for s in sources):
                sources.append(source)
                changed = True
            continue
        stored[rid] = {
            "type": (item.get("type") or "").strip(),
            "what": what,
            "why_it_matters": (item.get("why_it_matters") or "").strip(),
            "likely_source": (item.get("likely_source") or "").strip() or None,
            "sources": [source],
            "added": now,
        }
        added.append(rid)
        changed = True
    if changed:
        _save(vault, data)
    return added


def merge_duplicates(vault: Path, keep_rid: str, dup_rids: list[str]) -> int:
    """Fold ``dup_rids`` into ``keep_rid`` (#416) — for near-duplicate requests the exact-string
    match in ``record`` can't catch (paraphrased wording), identified by the model-judged dedup
    pass in ``orchestrate._post_ingest``. Each dup's ``sources`` are appended onto ``keep_rid``'s
    (deduped by sha256, same rule as ``record``) and the dup entry is dropped from the ledger;
    ``resolutions.remap_rid`` carries forward the dup's resolved state if it had any, so a
    request the journalist already resolved doesn't reappear as newly open under the survivor's
    id. Silently skips any rid no longer present (defensive — the model's judgement is applied
    a moment after it was formed, but never trust it blindly). Returns the number folded."""
    data = load(vault)
    stored = data.get("requests", {})
    if keep_rid not in stored:
        return 0
    folded = 0
    for dup_rid in dup_rids:
        if dup_rid == keep_rid or dup_rid not in stored:
            continue
        keep_sources = stored[keep_rid].setdefault("sources", [])
        keep_shas = {s.get("sha256") for s in keep_sources}
        for source in stored[dup_rid].get("sources") or []:
            if source.get("sha256") not in keep_shas:
                keep_sources.append(source)
                keep_shas.add(source.get("sha256"))
        del stored[dup_rid]
        folded += 1
    if folded:
        _save(vault, data)
        for dup_rid in dup_rids:
            resolutions.remap_rid(vault, dup_rid, keep_rid)
    return folded


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
            refs = ", ".join(
                f"[[{s['document_note']}|{s['filename']}]]"
                for s in r.get("sources") or []
                if s.get("document_note") and s.get("filename")
            )
            if refs:
                lines.append(f"  - Referenced in {refs}")
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
