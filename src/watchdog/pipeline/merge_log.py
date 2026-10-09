"""The merge log: every entity merge, and every "possible same" pair left for the reporter (D279).

Stored at ``.watchdog/registry/merges.json``::

    {"schema_version": 1,
     "merges": [{"id", "keep", "merged", "tier", "decided_by", "rule", "reason", "model",
                 "reporter", "same_name", "occurrences": [...], "undo": {...},
                 "first_at", "last_at"}],
     "candidates": {"same:<hash>": {"id", "a", "b", "tier", "rule", "reason", "model_declined",
                                    "evidence", "status", "first_seen", "last_seen", "run"}}}

A merge is one decision to treat two records as one entity: `keep` survives, `merged` is folded
into it. `decided_by` is ``rule`` (code, under `identity.classify`), ``model`` (the reconcile
model, judging a medium-tier pair) or ``reporter`` (`watchdog merge-entities`, the app's Merge).
The same decision recurring — one more document naming "Northgate Civil Works Ltd." — adds an
occurrence to the existing entry instead of a new entry, so the log grows with the number of
entities and name variants, not with documents × entities. `undo` keeps what a later split needs:
the merged record's id as extracted, its registry entry when it had one, and the pre-merge
snapshot.

Writers: the finalize commit (`write_vault.run`, via `RegistryBatch`) for merges and candidates
found while adding documents, and `merge_entities.run` for a merge it performs — both under the
registry lock (I7). Every write regenerates ``merges.md`` at the vault root, so the record stays
readable as Markdown. A newer ``schema_version`` is read but never rewritten.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
NOTE_FORMAT = 1
NOTE_FILE = "merges.md"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def path(vault: Path) -> Path:
    return Path(vault) / ".watchdog" / "registry" / "merges.json"


def _empty() -> dict:
    return {"schema_version": SCHEMA_VERSION, "merges": [], "candidates": {}}


def load(vault: Path | None) -> dict:
    """The log, or an empty one when the file is missing or unreadable."""
    if vault is None:
        return _empty()
    try:
        data = json.loads(path(vault).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    data.setdefault("schema_version", SCHEMA_VERSION)
    if not isinstance(data.get("merges"), list):
        data["merges"] = []
    if not isinstance(data.get("candidates"), dict):
        data["candidates"] = {}
    return data


def too_new(data: dict) -> bool:
    v = data.get("schema_version")
    return isinstance(v, int) and v > SCHEMA_VERSION


def merge_id(keep_id: str, merged_id: str, merged_name: str, decided_by: str, rule: str | None) -> str:
    key = f"{keep_id}|{merged_id}|{(merged_name or '').casefold()}|{decided_by}|{rule or ''}"
    return "merge:" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def merge_entry(*, keep: dict, merged: dict, tier: str, decided_by: str, rule: str | None,
                reason: str, occurrence: dict, model: str | None = None,
                reporter: str | None = None, undo: dict | None = None, run: str | None = None) -> dict:
    """One merge decision, ready for `record`. `keep`/`merged` are ``{"id", "name", "type"}``;
    `merged["id"]` is the id the record had before the merge (as extracted, for a fold)."""
    from watchdog.pipeline.identity import name_key
    at = _now()
    occ = {**occurrence, "at": at, "run": run}
    return {
        "kind": "merge",
        "id": merge_id(keep["id"], merged["id"], merged.get("name", ""), decided_by, rule),
        "keep": {k: keep.get(k) for k in ("id", "name", "type")},
        "merged": {k: merged.get(k) for k in ("id", "name", "type")},
        "tier": tier, "decided_by": decided_by, "rule": rule, "reason": reason,
        "model": model, "reporter": reporter,
        "same_name": name_key(keep.get("name", "")) == name_key(merged.get("name", "")),
        "occurrences": [occ], "undo": undo or {}, "first_at": at, "last_at": at,
    }


def candidate_entry(*, a, b, verdict: dict, model_declined: bool = False,
                    run: str | None = None) -> dict:
    """One "possible same" pair, ready for `record`. `a`/`b` are `identity.Profile`s."""
    from watchdog.pipeline.identity import pair_id

    def side(p) -> dict:
        return {"id": p.id, "name": p.name, "type": p.type, "aliases": p.surfaces[1:8],
                "documents": p.documents[:20],
                "facts": [{**d, "id": f["id"], "sha": f["sha"]}
                          for f, d in zip(p.facts, p.facts_digest())],
                "roles": p.roles_digest()}

    at = _now()
    return {"kind": "candidate", "id": pair_id(a.id, b.id), "a": side(a), "b": side(b),
            "tier": verdict["tier"], "rule": verdict["rule"], "reason": verdict["reason"],
            "model_declined": model_declined, "evidence": verdict.get("evidence") or {},
            "status": "open", "first_seen": at, "last_seen": at, "run": run}


UNDO_VERSION = 2


def carried_items(artifact: dict, sha: str, ids: set[str]) -> dict | None:
    """What in one extraction carries one of `ids` right before a merge folds it away (D280): the
    entity records with that id, each fact tagged with it (and its tags then), and each role that
    targets it. Kept on the merge's `undo.changes`, so an undo can give exactly those back."""
    ents = artifact.get("entities") or []
    out = {"sha": sha, "ids": sorted(ids),
           "entities": [i for i, e in enumerate(ents) if isinstance(e, dict) and e.get("id") in ids],
           "facts": [[i, list(f.get("entities") or [])]
                     for i, f in enumerate((artifact.get("document") or {}).get("key_facts") or [])
                     if isinstance(f, dict) and set(f.get("entities") or []) & ids],
           "roles": [[i, j] for i, e in enumerate(ents) if isinstance(e, dict)
                     for j, r in enumerate(e.get("roles") or []) if r.get("target_id") in ids]}
    return out if out["entities"] or out["facts"] or out["roles"] else None


def _upsert_merge(merges: list[dict], entry: dict) -> None:
    for existing in merges:
        if existing.get("id") == entry["id"]:
            undo = existing.setdefault("undo", {})
            have = {c.get("sha") for c in undo.get("changes") or []}
            for change in (entry.get("undo") or {}).get("changes") or []:
                if change.get("sha") not in have:
                    undo.setdefault("changes", []).append(change)
            seen = {(o.get("sha"), tuple(o.get("documents") or [])) for o in existing.get("occurrences", [])}
            for occ in entry["occurrences"]:
                if (occ.get("sha"), tuple(occ.get("documents") or [])) not in seen:
                    existing.setdefault("occurrences", []).append(occ)
                    existing["last_at"] = occ.get("at") or existing.get("last_at")
            return
    merges.append({k: v for k, v in entry.items() if k != "kind"})


def _upsert_candidate(cands: dict, entry: dict) -> None:
    entry = {k: v for k, v in entry.items() if k != "kind"}
    existing = cands.get(entry["id"])
    if existing:
        entry["first_seen"] = existing.get("first_seen") or entry["first_seen"]
        if existing.get("status") == "merged":
            entry["status"] = "merged"
        entry["model_declined"] = entry["model_declined"] or existing.get("model_declined", False)
    cands[entry["id"]] = entry


def record(vault: Path, entries: list[dict], data: dict | None = None, render_note: bool = True) -> bool:
    """Add `entries` (from `merge_entry`/`candidate_entry`) to the log and regenerate `merges.md`.
    Idempotent: a re-run of the same commit adds nothing new. Returns False, writing nothing, when
    the log was written by a newer Watchdog. Callers hold the registry lock."""
    if not entries:
        return True
    data = data if data is not None else load(vault)
    if too_new(data):
        return False
    for entry in entries:
        if entry.get("kind") == "candidate":
            _upsert_candidate(data["candidates"], entry)
        else:
            _upsert_merge(data["merges"], entry)
    save(vault, data, render_note=render_note)
    return True


def save(vault: Path, data: dict, render_note: bool = True) -> None:
    from watchdog.pipeline.write_vault import _write_json_atomic
    p = path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(p, data)
    if render_note:
        render(vault, data)


def mark_candidate_merged(data: dict, a: str, b: str) -> dict | None:
    """Close the open candidate for the pair (a, b), if any, and return it."""
    from watchdog.pipeline.identity import pair_id
    cand = data["candidates"].get(pair_id(a, b))
    if cand:
        cand["status"] = "merged"
        cand["last_seen"] = _now()
    return cand


def former_ids(vault: Path | None) -> dict[str, set[str]]:
    """Survivor id -> every id merged into it, following chains (a → b → c gives c both)."""
    direct: dict[str, set[str]] = {}
    for m in load(vault).get("merges", []):
        keep, merged = (m.get("keep") or {}).get("id"), (m.get("merged") or {}).get("id")
        if keep and merged and keep != merged:
            direct.setdefault(keep, set()).add(merged)
    out: dict[str, set[str]] = {}
    for keep in direct:
        seen, stack = set(), list(direct[keep])
        while stack:
            eid = stack.pop()
            if eid in seen or eid == keep:
                continue
            seen.add(eid)
            stack.extend(direct.get(eid, ()))
        out[keep] = seen
    return out


def open_candidates(vault: Path, entities: dict | None = None,
                    resolved: frozenset[str] | None = None, data: dict | None = None) -> list[dict]:
    """Candidates still waiting on the reporter: open, not dismissed, and both records still exist
    as separate entities (a merge by any route closes them)."""
    from watchdog.pipeline import resolutions
    from watchdog.pipeline.json_io import _read_json_or
    data = data if data is not None else load(vault)
    if entities is None:
        entities = _read_json_or(Path(vault) / ".watchdog" / "registry" / "entities.json", {})
    resolved = resolved if resolved is not None else resolutions.resolved_ids(vault)
    out = []
    for cid, c in data.get("candidates", {}).items():
        a, b = (c.get("a") or {}).get("id"), (c.get("b") or {}).get("id")
        if c.get("status") != "open" or cid in resolved:
            continue
        if a not in entities or b not in entities or a == b:
            continue
        out.append(c)
    order = {"low": 0, "medium": 1, "high": 2}
    out.sort(key=lambda c: (c.get("last_seen") or ""), reverse=True)
    out.sort(key=lambda c: order.get(c.get("tier"), 3))
    return out


# ── merges.md ─────────────────────────────────────────────────────────────────────────────────

_DECIDED = {"rule": "Watchdog's rules", "model": "the AI model", "reporter": "a reporter"}
_TIER = {"high": "high confidence", "medium": "medium confidence", "low": "low confidence",
         "manual": "chosen by hand"}


def _md(text) -> str:
    from watchdog.pipeline.write_vault import _defang
    return _defang(str(text or "")).replace("|", "\\|")


def _doc_titles(vault: Path) -> dict[str, tuple[str, str]]:
    from watchdog.pipeline.json_io import _read_json_or
    docs = _read_json_or(Path(vault) / ".watchdog" / "registry" / "documents.json", {})
    return {sha: (d.get("title") or d.get("filename") or sha[:12], d.get("document_note") or "")
            for sha, d in docs.items() if isinstance(d, dict)}


def _doc_link(titles: dict, sha: str) -> str:
    title, note = titles.get(sha, (sha[:12], ""))
    return f"[[{note}|{_md(title)}]]" if note else _md(title)


def render(vault: Path, data: dict | None = None) -> Path:
    """Regenerate ``merges.md``: open "possible same" pairs, then every merge, newest first."""
    data = data if data is not None else load(vault)
    titles = _doc_titles(vault)
    lines = [
        "---", f"format_version: {NOTE_FORMAT}", "generated: true", "---", "",
        "# Entity merges", "",
        "Every time Watchdog treated two records as the same person, organization, place or thing,"
        " and every pair it was not sure enough to merge. Generated from"
        " `.watchdog/registry/merges.json`; edits here are overwritten.", "",
    ]
    from watchdog.pipeline.json_io import _read_json_or
    from watchdog.pipeline import resolutions
    entities = _read_json_or(Path(vault) / ".watchdog" / "registry" / "entities.json", {})
    cands = open_candidates(vault, entities=entities, resolved=resolutions.resolved_ids(vault), data=data)
    lines += ["## Possible same entities", ""]
    if not cands:
        lines += ["None waiting.", ""]
    marks = None
    for c in cands:
        a, b = c["a"], c["b"]
        if marks is None:
            from watchdog.pipeline import verification
            marks = verification.marks(vault)
        lines.append(f"- **{_md(a['name'])}** and **{_md(b['name'])}** ({_TIER.get(c['tier'], c['tier'])})"
                     f" — {_md(c['reason'])}"
                     + (" The AI model was not confident they are the same." if c.get("model_declined") else ""))
        for side in (a, b):
            note = (entities.get(side["id"]) or {}).get("note_path")
            label = f"[[{note}|{_md(side['name'])}]]" if note else _md(side["name"])
            for f in side.get("facts", [])[:3]:
                from watchdog.pipeline.verification import attach
                disputed = (attach(marks.get(f.get("id")), f) or {}).get("status") == "disputed"
                lines.append(f"  - {label}: {_md(f.get('fact'))} ({_md(f.get('document'))}"
                             + (f", p. {f['page']}" if f.get("page") else "") + ")"
                             + (" · ✗ disputed" if disputed else ""))
    lines.append("")
    lines += ["## Merges", ""]
    merges = sorted(data.get("merges", []), key=lambda m: m.get("last_at") or "", reverse=True)
    if not merges:
        lines += ["No merges yet.", ""]
    for m in merges:
        keep, merged = m.get("keep") or {}, m.get("merged") or {}
        note = (entities.get(keep.get("id")) or {}).get("note_path")
        keep_label = f"[[{note}|{_md(keep.get('name'))}]]" if note else _md(keep.get("name"))
        who = _DECIDED.get(m.get("decided_by"), m.get("decided_by"))
        if m.get("decided_by") == "model" and m.get("model"):
            who += f" ({_md(m['model'])})"
        if m.get("decided_by") == "reporter" and m.get("reporter"):
            who = _md(m["reporter"])
        head = (f"- {keep_label} recognised again under the same name" if m.get("same_name")
                else f"- **{_md(merged.get('name'))}** merged into {keep_label}")
        docs = []
        for occ in m.get("occurrences", []):
            for sha in occ.get("documents") or ([occ["sha"]] if occ.get("sha") else []):
                if sha not in docs:
                    docs.append(sha)
        undone = ""
        if isinstance(m.get("undone"), dict):
            u = m["undone"]
            undone = (f" **Undone** on {(u.get('at') or '')[:10]} by {_md(u.get('by'))}; the split "
                      f"record is `{_md(u.get('split_id'))}`.")
        lines.append(f"{head} — {_TIER.get(m.get('tier'), m.get('tier'))}, decided by {who}, "
                     f"{(m.get('last_at') or '')[:10]}. {_md(m.get('reason'))}{undone}")
        if docs:
            shown = ", ".join(_doc_link(titles, s) for s in docs[:8])
            more = f" and {len(docs) - 8} more" if len(docs) > 8 else ""
            lines.append(f"  - Documents: {shown}{more}")
    lines.append("")
    out = Path(vault) / NOTE_FILE
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
