"""Undo an entity merge (D280): split the merged record back out of the one that absorbed it.

A merge (D279) folds a record into a survivor. Since D280 every merge keeps, on its log entry's
`undo`, exactly what in the stored extractions carried the merged record's id just before the
fold (`merge_log.carried_items`): its entity records, the facts tagged to it with their tags at
the time, and the roles that pointed at it. A merge of two committed records also keeps a
snapshot of the merged record's registry entry; its older extractions were never rewritten and
still name it, reaching the survivor only through the merge log (`entity_facts.resolve`).

Undoing gives those items back to a record of their own:

1. the recorded items in the extractions are re-tagged with the split record's id (the merged id
   when it is free, else a fresh one), and the merge is marked undone in the log, so the log no
   longer leads that id to the survivor;
2. both records' registry entries are rebuilt from the extractions (documents, aliases, roles,
   timeline events), and any third record's role that came from the split record's documents is
   re-pointed; timeline events from those documents follow their facts;
3. both notes, and the notes of re-pointed records, are rendered from data; the pair is marked
   "Not the same" so it is never merged automatically again.

Stays with the survivor, and the result says so: contradictions recorded on the joined record,
the reporter's Notes (written on the survivor, including notes carried over at the merge), and
its AI-written summary (marked as written before the split). Documents added after the merge
that Watchdog matched to the survivor by a later decision stay with it; that decision has its
own log entry.

An undo is refused, with the reason, when it cannot be done correctly: a merge logged before
D280 (no record of what moved), a fold inside one document read in sections, a merge already
undone, a survivor that was itself merged away later, a merged id now used by another record,
or extractions changed since the merge so that the recorded items no longer match.
"""

from __future__ import annotations

import json
from pathlib import Path

from watchdog.pipeline import entity_facts, merge_log
from watchdog.pipeline.json_io import _read_json_or


class UndoRefused(ValueError):
    """The merge cannot be split correctly; the message says why, in plain words."""


def _reg(vault: Path) -> Path:
    return Path(vault) / ".watchdog" / "registry"


def _find(data: dict, merge_id: str) -> dict:
    for m in data.get("merges") or []:
        if isinstance(m, dict) and m.get("id") == merge_id:
            return m
    raise UndoRefused("That merge is not in this investigation's merge log.")


def _artifact_path(vault: Path, sha: str) -> Path:
    return Path(vault) / ".watchdog" / "extracted" / f"{sha}.json"


def _load_artifact(vault: Path, sha: str) -> dict | None:
    try:
        data = json.loads(_artifact_path(vault, sha).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def check(vault: Path, entry: dict, entities: dict | None = None) -> str | None:
    """None when `entry` can be undone now, else the reason it cannot, for the reporter."""
    vault = Path(vault)
    entities = entities if entities is not None else _read_json_or(_reg(vault) / "entities.json", {})
    undo = entry.get("undo") or {}
    if entry.get("undone"):
        return "This merge has already been undone."
    if undo.get("available") is False:
        return undo.get("reason") or "This merge cannot be undone."
    if (undo.get("version") or 1) < merge_log.UNDO_VERSION:
        return ("This merge was made by an earlier version of Watchdog, which did not record which "
                "facts moved, so it cannot be split exactly.")
    keep = (entry.get("keep") or {}).get("id")
    merged = (entry.get("merged") or {}).get("id")
    if keep not in entities:
        return ("The surviving record was itself merged into another one later. Undo that later "
                "merge first.")
    if undo.get("entry") and merged in entities:
        return f"The id “{merged}” is now used by another record, so this merge cannot be split back."
    for change in undo.get("changes") or []:
        art = _load_artifact(vault, change.get("sha", ""))
        if art is None:
            return "A document this merge touched no longer has its stored extraction."
        if _mismatch(art, change, keep):
            return ("A document this merge touched has been processed again since, so the facts "
                    "that moved can no longer be identified.")
    if not undo.get("changes") and not undo.get("entry"):
        return "This merge recorded nothing to split back."
    return None


def _mismatch(art: dict, change: dict, keep: str) -> bool:
    ents = art.get("entities") or []
    facts = (art.get("document") or {}).get("key_facts") or []
    for i in change.get("entities") or []:
        if i >= len(ents) or ents[i].get("id") != keep:
            return True
    for i, _before in change.get("facts") or []:
        if i >= len(facts) or keep not in (facts[i].get("entities") or []):
            return True
    for i, j in change.get("roles") or []:
        if i >= len(ents) or j >= len(ents[i].get("roles") or []) or ents[i]["roles"][j].get("target_id") != keep:
            return True
    return False


def _split_id(entry: dict, entities: dict) -> str:
    merged = (entry.get("merged") or {}).get("id") or "entity"
    keep = (entry.get("keep") or {}).get("id")
    if merged not in entities and merged != keep:
        return merged
    from watchdog.pipeline.write_vault import _unique_entity_id
    return _unique_entity_id(merged, set(entities))


def _retag(vault: Path, change: dict, keep: str, merged: str, new: str) -> dict:
    """Give one document's recorded items back to `new`. Returns the patched artifact."""
    art = _load_artifact(vault, change["sha"])
    ents = art.get("entities") or []
    facts = (art.get("document") or {}).get("key_facts") or []
    for i in change.get("entities") or []:
        ents[i]["id"] = new
    # A fact that was about the survivor too before the merge stays about it as well; one whose
    # tag was only the merged record's (the merged id, or the survivor's id reused for it in this
    # document) goes to the split record alone.
    merged_ids = set(change.get("ids") or [merged])
    for i, before in change.get("facts") or []:
        stays = keep in before and keep not in merged_ids
        out = []
        for t in facts[i].get("entities") or []:
            if t == keep:
                out += [keep, new] if stays else [new]
            else:
                out.append(t)
        facts[i]["entities"] = list(dict.fromkeys(out))
    for i, j in change.get("roles") or []:
        ents[i]["roles"][j]["target_id"] = new
    _artifact_path(vault, change["sha"]).write_text(json.dumps(art, ensure_ascii=False, indent=2),
                                                    encoding="utf-8")
    return art


def _rebuild_entry(vault: Path, eid: str, base: dict, index: entity_facts.FactIndex,
                   shas: list[str]) -> dict:
    """`base` with its documents, aliases, forward roles and timeline events re-derived from the
    extractions in `shas`, through `index` (whose registry already names `eid`)."""
    from watchdog.pipeline.write_vault import _merge_timeline_events
    entry = dict(base)
    appears, aliases, roles, events = [], [], [], []
    seen_roles = set()
    for sha in shas:
        art = index.overrides.get(sha) or _load_artifact(vault, sha)
        if not art:
            continue
        mine = [e for e in art.get("entities") or [] if index.resolve(sha, e.get("id", "")) == eid]
        if not mine:
            continue
        appears.append(sha)
        for e in mine:
            for name in [e.get("name", ""), *(e.get("aliases") or [])]:
                if name and name.lower() != entry["name"].lower() and name.lower() not in {a.lower() for a in aliases}:
                    aliases.append(name)
            for r in e.get("roles") or []:
                target = index.resolve(sha, r.get("target_id", "")) or r.get("target_id")
                key = ((r.get("relationship") or "").lower(), target)
                if key in seen_roles:
                    continue
                seen_roles.add(key)
                t = index.entities.get(target) or {}
                roles.append({**{k: v for k, v in r.items() if k != "extracted_target_id"},
                              "target_id": target, "target_name": t.get("name") or r.get("target_name") or target,
                              "target_type": t.get("type") or r.get("target_type") or "Unknown",
                              "source_sha256": sha, "is_reverse": False})
        dated = [{"date": f["date"], "event": f["fact"], "page": f.get("page"),
                  "basis": f.get("basis") or "stated"}
                 for f in index.facts_for(eid, [sha]) if f.get("date")]
        events = _merge_timeline_events(events, dated, sha)
    known = [a for a in base.get("aliases") or [] if a.lower() in {x.lower() for x in aliases}]
    entry["aliases"] = known + [a for a in aliases if a.lower() not in {k.lower() for k in known}]
    entry["appears_in"] = appears
    reverse = [r for r in base.get("roles") or [] if r.get("is_reverse")]
    entry["roles"] = roles + reverse
    entry["timeline_events"] = events
    return entry


def _repoint_roles(entities: dict, keep: str, new: str, index: entity_facts.FactIndex,
                   vault: Path, shas: set[str]) -> set[str]:
    """Every role naming `keep` that came from one of `shas` and whose source now names `new` is
    re-pointed (forward roles of third records, and reverse roles). Returns the records changed."""
    changed = set()
    for eid, ent in entities.items():
        for r in ent.get("roles") or []:
            if r.get("target_id") != keep or r.get("source_sha256") not in shas:
                continue
            art = index.overrides.get(r["source_sha256"]) or _load_artifact(vault, r["source_sha256"])
            if not art:
                continue
            sha = r["source_sha256"]
            for e in art.get("entities") or []:
                me = index.resolve(sha, e.get("id", ""))
                for rr in e.get("roles") or []:
                    if (rr.get("relationship") or "").lower() != (r.get("relationship") or "").lower():
                        continue
                    other = index.resolve(sha, rr.get("target_id", ""))
                    hit = (not r.get("is_reverse") and me == eid and other == new) or \
                          (r.get("is_reverse") and me == new and other == eid)
                    if hit:
                        r["target_id"] = new
                        r["target_name"] = entities[new]["name"]
                        r["target_type"] = entities[new]["type"]
                        changed.add(eid)
                        break
                else:
                    continue
                break
    return changed


def _restore_third_party_roles(entities: dict, ids: set[str], index: entity_facts.FactIndex,
                               vault: Path) -> set[str]:
    """A third record's forward roles to either side, re-derived from its own documents. A merge
    deduplicates them (two "lobbied" roles to the two records become one), so re-pointing the
    survivor alone cannot bring the second back. Returns the records changed."""
    changed = set()
    for eid, ent in entities.items():
        if eid in ids or not any(r.get("target_id") in ids for r in ent.get("roles") or []):
            continue
        have = {((r.get("relationship") or "").lower(), r.get("target_id"))
                for r in ent.get("roles") or [] if not r.get("is_reverse")}
        for sha in ent.get("appears_in") or []:
            art = index.overrides.get(sha) or _load_artifact(vault, sha)
            for e in (art or {}).get("entities") or []:
                if index.resolve(sha, e.get("id", "")) != eid:
                    continue
                for r in e.get("roles") or []:
                    target = index.resolve(sha, r.get("target_id", ""))
                    key = ((r.get("relationship") or "").lower(), target)
                    if target not in ids or key in have:
                        continue
                    have.add(key)
                    t = entities[target]
                    ent.setdefault("roles", []).append(
                        {**{k: v for k, v in r.items() if k != "extracted_target_id"},
                         "target_id": target, "target_name": t["name"], "target_type": t["type"],
                         "source_sha256": sha, "is_reverse": False})
                    changed.add(eid)
    return changed


def _reverse_roles(entities: dict, eid: str) -> list[dict]:
    """Reverse roles for `eid` from every other record's forward roles that name it."""
    out, seen = [], set()
    for other_id, other in entities.items():
        if other_id == eid:
            continue
        for r in other.get("roles") or []:
            if r.get("is_reverse") or r.get("target_id") != eid:
                continue
            key = ((r.get("relationship") or "").lower(), other_id)
            if key in seen:
                continue
            seen.add(key)
            out.append({"relationship": r.get("relationship"), "target_id": other_id,
                        "target_type": other.get("type"), "target_name": other.get("name"),
                        "page": r.get("page"), "basis": r.get("basis", "stated"),
                        "date_range": r.get("date_range"), "source_sha256": r.get("source_sha256"),
                        "is_reverse": True})
    return out


def _retag_timeline(vault: Path, arts: dict[str, dict], keep: str, new: str) -> None:
    """Timeline events from the patched documents follow their facts' new tags."""
    from watchdog.pipeline.timeline import _read_ndjson_lines
    td = Path(vault) / ".watchdog" / "timeline"
    if not td.exists():
        return
    tags: dict[tuple, set] = {}
    for sha, art in arts.items():
        for f in (art.get("document") or {}).get("key_facts") or []:
            if f.get("date") and f.get("fact"):
                key = (sha, f["date"].strip(), " ".join(f["fact"].split()).lower())
                tags.setdefault(key, set()).update(f.get("entities") or [])
    for path in sorted(td.rglob("*.ndjson")):
        lines, touched = [], False
        for line in _read_ndjson_lines(path):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                lines.append(line)
                continue
            key = (rec.get("source_sha256"), (rec.get("date") or "").strip(),
                   " ".join((rec.get("event") or "").split()).lower())
            now = tags.get(key)
            ids = rec.get("entity_ids") if isinstance(rec.get("entity_ids"), list) else None
            if now is not None and ids is not None and (keep in ids or new in now):
                out = [e for e in ids if e != keep or keep in now]
                if new in now and new not in out:
                    out.append(new)
                if out != ids:
                    rec["entity_ids"] = out
                    touched = True
            lines.append(json.dumps(rec, ensure_ascii=False))
        if touched:
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _undo_unlocked(vault: Path, merge_id: str, by: str | None) -> dict:
    from watchdog.pipeline import entity_notes, resolutions
    from watchdog.pipeline.backup import snapshot
    from watchdog.pipeline.identity import pair_id
    from watchdog.pipeline.timeline import cmd_rebuild_timeline
    from watchdog.pipeline.verification import reporter_name
    from watchdog.pipeline.write_vault import _now_iso, _today, _type_dir, _update_manifest, _write_json_atomic

    reg = _reg(vault)
    data = merge_log.load(vault)
    if merge_log.too_new(data):
        raise UndoRefused("This investigation's merge log was written by a newer version of Watchdog.")
    entry = _find(data, merge_id)
    entities = _read_json_or(reg / "entities.json", {})
    documents = _read_json_or(reg / "documents.json", {})
    reason = check(vault, entry, entities)
    if reason:
        raise UndoRefused(reason)

    undo = entry["undo"]
    keep = entry["keep"]["id"]
    merged = entry["merged"]["id"]
    new = _split_id(entry, entities)
    snap = undo.get("entry") or {}
    changes = undo.get("changes") or []

    paths = [reg / "entities.json", merge_log.path(vault), reg / "manifest.json"]
    paths += [_artifact_path(vault, c["sha"]) for c in changes]
    paths += [vault / f"{entities[keep]['note_path']}.md"]
    snapshot(vault, "undo-merge", [p for p in paths if p.exists()])

    # 1. The recorded items go back to their own record, and the log stops leading the merged id
    #    to the survivor.
    arts = {c["sha"]: _retag(vault, c, keep, merged, new) for c in changes}
    entry["undone"] = {"at": _now_iso(), "by": (by or "").strip() or reporter_name(), "split_id": new}

    # 2. Both registry entries, re-derived from the extractions.
    record = next((arts[c["sha"]]["entities"][i] for c in changes for i in c.get("entities") or []), None)
    etype = snap.get("type") or (record or {}).get("type") or entry["merged"].get("type") or "other"
    from watchdog.pipeline.entity_type import canonical_type
    etype = canonical_type(etype)
    entities[new] = {
        "id": new, "name": snap.get("name") or (record or {}).get("name") or entry["merged"].get("name") or new,
        "type": etype, "aliases": list(snap.get("aliases") or []),
        "appears_in": [], "note_path": snap.get("note_path") if new == merged and snap.get("note_path")
        else f"entities/{_type_dir(etype)}/{new}",
        "roles": [], "timeline_events": [], "contradictions": list(snap.get("contradictions") or []),
        "date_first_seen": snap.get("date_first_seen") or _today(), "date_last_updated": _today(),
    }
    if snap.get("synthesis"):
        entities[new]["synthesis"] = {**snap["synthesis"], "stale": "undo"}
    if snap.get("legacy_claims"):
        entities[new]["legacy_claims"] = snap["legacy_claims"]
    candidates = list(dict.fromkeys([*(entities[keep].get("appears_in") or []),
                                     *(snap.get("appears_in") or []), *arts]))
    entities[new]["appears_in"] = [s for s in candidates if s in set(snap.get("appears_in") or []) | set(arts)]
    index = entity_facts.FactIndex(vault, entities, documents, overrides=arts, merges=data)
    entities[new] = _rebuild_entry(vault, new, entities[new], index, candidates)
    entities[keep] = _rebuild_entry(vault, keep, entities[keep], index, candidates)
    index = entity_facts.FactIndex(vault, entities, documents, overrides=arts, merges=data)
    touched = _repoint_roles(entities, keep, new, index, vault, set(candidates))
    touched |= _restore_third_party_roles(entities, {keep, new}, index, vault)
    for eid in (keep, new):
        entities[eid]["roles"] = [r for r in entities[eid]["roles"] if not r.get("is_reverse")] \
            + _reverse_roles(entities, eid)
    if entities[keep].get("synthesis"):
        entities[keep]["synthesis"] = {**entities[keep]["synthesis"], "stale": "undo"}
    entities[keep]["date_last_updated"] = _today()

    # Timeline events follow their facts: this batch's patched documents, and the older documents a
    # merge of two committed records re-pointed wholesale (`merge_entities` remaps every event).
    older = {s: a for s in snap.get("appears_in") or [] if s not in arts
             for a in [_load_artifact(vault, s)] if a}
    _retag_timeline(vault, {**older, **arts}, keep, new)

    # 3. The pair is "Not the same", the log and registry are written, and the notes rendered.
    resolutions.resolve(vault, [pair_id(keep, new)], label="undo merge")
    merge_log.save(vault, data, render_note=False)
    _write_json_atomic(reg / "entities.json", entities)
    _update_manifest(vault, entities)
    stub = vault / f"{entities[new]['note_path']}.md"
    if stub.exists() and "merged_into:" in stub.read_text(encoding="utf-8"):
        stub.unlink()
    entity_facts.clear_cache()
    written = entity_notes.write_entities(vault, {keep, new} | touched, entities, documents,
                                          index=entity_facts.FactIndex(vault, entities, documents))
    _write_json_atomic(reg / "entities.json", entities)
    merge_log.render(vault)
    cmd_rebuild_timeline(vault, quiet=True)
    entity_notes.index_notes(vault, written)
    return {"keep_id": keep, "keep_name": entities[keep]["name"], "split_id": new,
            "split_name": entities[new]["name"],
            "documents": len(entities[new]["appears_in"]),
            "facts": len(entity_facts.FactIndex(vault, entities, documents).facts_for(new)),
            "kept_on_survivor": ["contradictions", "notes", "summary"]}


def undo(vault: Path, merge_id: str, by: str | None = None) -> dict:
    """Undo one merge under the registry lock. Raises `UndoRefused` with the reason when it
    cannot be done correctly."""
    from watchdog.pipeline import history
    from watchdog.pipeline.write_vault import _registry_lock
    vault = Path(vault)
    with _registry_lock(_reg(vault)):
        entry = _find(merge_log.load(vault), merge_id) or {}
        cause = {"kind": "undo_merge", "merge_id": merge_id,
                 "keep": (entry.get("keep") or {}).get("name"),
                 "split": (entry.get("merged") or {}).get("name")}
        with history.recording(vault, cause):     # a version of the vault's history (D286)
            return _undo_unlocked(vault, merge_id, by)
