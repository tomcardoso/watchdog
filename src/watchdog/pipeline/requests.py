"""Document-request ledger (D111, D159).

A document request is a concrete document to go and get: its type, what it is, why it matters and
where it is likely held. The model writes those fields; Python stamps the id and provenance (I1).

Ledger at ``.watchdog/registry/requests.json``::

    {"schema_version": 2, "requests": {"<rid>": {
        "type": str, "what": str, "why_it_matters": str, "likely_source": str|None,
        "sources": [{"sha256": str, "filename": str, "document_note": str}, ...],
        "added": "<iso>"
    }}}

Entries are keyed on the normalized ``what`` text, so identical wording converges; paraphrases
are folded by the finalize-time dedup call. Resolution is manual (in the app's Review, or
`marking an item handled in Review`). ``write_requests`` renders open entries to ``requests.md``."""

import datetime
import json
import os
import re
from pathlib import Path

from watchdog.pipeline import resolutions
from watchdog.pipeline.json_io import _read_json

_SCHEMA_VERSION = 2


def _path(vault: Path) -> Path:
    return vault / ".watchdog" / "registry" / "requests.json"


def load(vault: Path) -> dict:
    """Load the ledger, returning a fresh empty structure on missing/corrupt file."""
    try:
        data = _read_json(_path(vault))
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
    os.replace(tmp, path)   # rename() fails on Windows when the target exists


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


# ── Working the list in Review (D328) ────────────────────────────────────────────

# Where the model's free-text `likely_source` stops naming the holder and starts saying how to ask:
# "City Clerk, by access request", "the corporation, or a court order", "Land Registry (Ontario)".
_SOURCE_CUT = re.compile(r"\s*(?:[,;(]|\s[-–—]\s|\bor\b|\bvia\b|\bby\b|\bthrough\b|\bunder\b)", re.I)
_SOURCE_LEAD = re.compile(r"^(?:the|a|an)\s+", re.I)
NO_SOURCE = ""   # the group key of requests whose source was not given


def source_group(likely_source: str | None) -> tuple[str, str]:
    """Pure: `(key, label)` of the holder a request's `likely_source` names, so requests to the
    same office group together whatever the wording around it.

    The holder is the text before the first comma, semicolon, parenthesis, spaced dash, or "or",
    "via", "by", "through", "under" (which start an alternative or the way to ask); a leading
    article, punctuation, case and spacing are folded for the key. The label is that text as
    written, less the article. No source, or nothing left, gives `("", "")`."""
    text = re.sub(r"\s+", " ", likely_source or "").strip()
    head = _SOURCE_CUT.split(text, maxsplit=1)[0].strip() if text else ""
    head = _SOURCE_LEAD.sub("", head).strip()
    key = re.sub(r"[^\w&]+", " ", head.casefold()).strip()
    if not key:
        return NO_SOURCE, ""
    # A closing full stop stays on an abbreviation ("Ltd.", "Inc.") and goes elsewhere.
    if head.endswith(".") and not re.search(r"\b[A-Za-z]{1,4}\.$", head):
        head = head.rstrip(" .")
    return key, head[0].upper() + head[1:]


def cite_count(request: dict) -> int:
    """How many distinct documents cite a request."""
    return len({s.get("sha256") or s.get("filename") for s in request.get("sources") or []
                if isinstance(s, dict) and (s.get("sha256") or s.get("filename"))})


def worklist(open_: list[dict], mapped: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Pure: the open requests as Review works them, and their source groups.

    Requests are sorted by how many documents cite them, most first, then by `what`; each gains
    `cite_count` and `source_key`. Groups are `{key, label, count, documents, grouped_by,
    wordings}`, ordered by the most-cited request in each, then by size and label; requests with no
    source come last. A group's label is its most frequent wording (ties: alphabetical).

    `mapped` is `request_sources.mapping` (D334): a source key in one of its groups goes under that
    group's id and label instead, with `grouped_by: "model"` and the wordings it joined; nothing
    in the requests themselves changes."""
    mapped = mapped or {}
    rows = []
    for r in open_:
        key, label = source_group(r.get("likely_source"))
        g = mapped.get(key) if key else None
        rows.append({**r, "cite_count": cite_count(r), "source_key": g["id"] if g else key,
                     "_label": label, "_group": g})
    rows.sort(key=lambda r: ((r.get("what") or "").casefold(), r.get("rid") or ""))
    rows.sort(key=lambda r: -r["cite_count"])
    by_key: dict[str, list[dict]] = {}
    for r in rows:
        by_key.setdefault(r["source_key"], []).append(r)
    groups = []
    for key, members in by_key.items():
        words: dict[str, int] = {}
        for r in members:
            words[r["_label"]] = words.get(r["_label"], 0) + 1
        label = sorted(words.items(), key=lambda t: (-t[1], t[0].casefold(), t[0]))[0][0] if key else ""
        shas = {s.get("sha256") or s.get("filename") for r in members for s in r.get("sources") or []
                if isinstance(s, dict)}
        g = members[0]["_group"]
        groups.append({"key": key, "label": (g.get("label") or label) if g else label,
                       "count": len(members), "documents": len(shas - {None}),
                       "grouped_by": "model" if g else None,
                       "wordings": sorted(words, key=lambda w: (-words[w], w.casefold())) if g else [],
                       "_top": members[0]["cite_count"]})
    groups.sort(key=lambda g: (g["key"] == NO_SOURCE, -g["_top"], -g["count"], g["label"].casefold()))
    for g in groups:
        del g["_top"]
    for r in rows:
        del r["_label"], r["_group"]
    return rows, groups


def _format(open_: list[dict]) -> str:
    by_type: dict[str, list[dict]] = {}
    for r in open_:
        by_type.setdefault(r.get("type") or "Other", []).append(r)

    lines = [
        "# Documents to request\n",
        "*Regenerated on each ingest — lists only what is still outstanding.*\n",
        "*Handled in Watchdog: Review → Requests. This file is a read-only summary.*\n",
    ]
    for dtype in sorted(by_type):
        lines.append(f"\n## {dtype}\n")
        for r in by_type[dtype]:
            lines.append(f"- **{r['what']}** <!--wid:{r['rid']}-->")
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
