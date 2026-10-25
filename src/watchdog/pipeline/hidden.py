"""Entities a reporter removed from an investigation (#753, D326): a view overlay, never an edit.

Processing sometimes profiles something that is not part of the story: an office named on a cover
page, the reporter's own organization. Deleting its record would not stick, since the next document
that names it brings it back, and would edit the record of what the sources say. Instead the
entity's id goes on a lasting list, and the app's views leave it out while it is listed.

Stored at ``.watchdog/registry/hidden.json``::

    {"schema_version": 1,
     "hidden": {"<entity id>": {"at": "<iso>", "by": "<reporter>", "name": "...", "type": "..."}}}

``name`` and ``type`` are the entity's as it was removed, so the list can say what it was even
after the record itself has gone from the registry.

What the list changes (I17): the Network, the Entities list, entity notes in Search, leads,
"possible same" pairs and the Overview and Review counts leave a listed entity out. What it never
changes: the entity's registry record and note, the facts in the stored extractions and document
notes, other entities' notes (their relationships to it stay), and every model input (synthesis,
the briefing, reconciliation). Keyed on the id, so a later extraction that produces the same id,
or a record the identity code folds into a listed one, is hidden too.

A merge settles it by the survivor (`on_merge`): the survivor is hidden only if it was hidden
itself. A merged-away record's listing goes with the merge's undo record, and undoing the merge
lists the split record again (`restore`).
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

from watchdog.pipeline.json_io import _read_json

SCHEMA_VERSION = 1
FILE = "hidden.json"


def path(vault: Path) -> Path:
    return Path(vault) / ".watchdog" / "registry" / FILE


def load(vault: Path) -> dict:
    """The store, or a fresh empty one when it is missing or unreadable."""
    try:
        data = _read_json(path(vault))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": SCHEMA_VERSION, "hidden": {}}
    if not isinstance(data, dict) or not isinstance(data.get("hidden"), dict):
        return {"schema_version": SCHEMA_VERSION, "hidden": {}}
    return data


def records(vault: Path) -> dict[str, dict]:
    """Every listed id with its record (`at`, `by`, `name`, `type`)."""
    return {k: (v if isinstance(v, dict) else {}) for k, v in load(vault)["hidden"].items()}


def hidden_ids(vault: Path) -> frozenset[str]:
    """The ids the views leave out."""
    return frozenset(load(vault)["hidden"])


def save(vault: Path, data: dict) -> None:
    p = path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def note_stem(note_path) -> str | None:
    """A note path as the search indexes and the registry write it, without `.md`."""
    if not isinstance(note_path, str) or not note_path:
        return None
    p = note_path.replace("\\", "/")
    return p[:-3] if p.endswith(".md") else p


def hidden_notes(vault: Path, entities: dict) -> set[str]:
    """The note stems of the listed entities that still have a record, for filtering search hits."""
    out = set()
    for eid in hidden_ids(vault):
        stem = note_stem((entities.get(eid) or {}).get("note_path"))
        if stem:
            out.add(stem)
    return out


# ── changes (callers hold the registry lock) ──────────────────────────────────────────────


def hide(vault: Path, ids, entities: dict, by: str) -> list[str]:
    """List `ids` (each must be in `entities`). Returns the ids newly listed."""
    data = load(vault)
    listed = data.setdefault("hidden", {})
    added = []
    for eid in ids:
        if eid in listed:
            continue
        ent = entities[eid]
        listed[eid] = {"at": _now(), "by": by, "name": ent.get("name") or eid,
                       "type": ent.get("type") or ""}
        added.append(eid)
    if added:
        save(vault, data)
    return added


def bring_back(vault: Path, ids) -> list[str]:
    """Take `ids` off the list. Returns the ids actually removed."""
    data = load(vault)
    listed = data.setdefault("hidden", {})
    removed = [eid for eid in dict.fromkeys(ids) if listed.pop(eid, None) is not None]
    if removed:
        save(vault, data)
    return removed


def on_merge(vault: Path, keep_id: str, merge_id: str) -> dict | None:
    """Follow a merge of `merge_id` into `keep_id`: the survivor keeps its own state, and the
    merged-away id leaves the list. Returns the merged-away id's record, for the merge's undo
    record, or None when it was not listed."""
    data = load(vault)
    rec = data.get("hidden", {}).pop(merge_id, None)
    if rec is not None:
        save(vault, data)
    return rec


def restore(vault: Path, eid: str, record: dict) -> None:
    """List `eid` again with `record` (an undone merge gives a split record back its listing)."""
    data = load(vault)
    data.setdefault("hidden", {}).setdefault(eid, dict(record))
    save(vault, data)


# ── the operations' library side ──────────────────────────────────────────────────────────


def _locked(vault: Path, cause: dict, fn):
    """`fn()` under the registry lock every registry writer takes (D258), recorded as one version
    of the vault's history (D286)."""
    from watchdog.pipeline import history
    from watchdog.pipeline.write_vault import _registry_lock
    reg = Path(vault) / ".watchdog" / "registry"
    with _registry_lock(reg):
        with history.recording(vault, cause, [f".watchdog/registry/{FILE}"]):
            return fn()


def run_hide(vault: Path, ids: list[str], by: str | None = None) -> dict:
    """Remove `ids` from the investigation's views. Raises `ValueError` naming an id that is not
    an entity in this investigation."""
    from watchdog.pipeline.json_io import _read_json_or
    from watchdog.pipeline.verification import reporter_name
    ids = [i.strip() for i in dict.fromkeys(ids) if isinstance(i, str) and i.strip()]
    if not ids:
        raise ValueError("Choose at least one entity to remove.")
    reg = Path(vault) / ".watchdog" / "registry"

    def go():
        entities = _read_json_or(reg / "entities.json", {})
        missing = [i for i in ids if not isinstance(entities.get(i), dict)]
        if missing:
            raise ValueError(f"There is no entity “{missing[0]}” in this investigation.")
        added = hide(vault, ids, entities, by or reporter_name())
        return {"hidden": added, "already": [i for i in ids if i not in added],
                "names": [entities[i].get("name") or i for i in ids]}

    names = []
    try:
        ents = _read_json_or(reg / "entities.json", {})
        names = [(ents.get(i) or {}).get("name") or i for i in ids[:3]]
    except Exception:  # noqa: BLE001 — the label only
        pass
    return _locked(vault, {"kind": "hide", "names": names, "count": len(ids)}, go)


def run_bring_back(vault: Path, ids: list[str]) -> dict:
    """Show `ids` again. An id that is not listed is reported, not refused."""
    ids = [i.strip() for i in dict.fromkeys(ids) if isinstance(i, str) and i.strip()]
    if not ids:
        raise ValueError("Choose at least one entity to bring back.")
    listed = records(vault)
    names = [(listed.get(i) or {}).get("name") or i for i in ids[:3]]

    def go():
        recs = records(vault)
        removed = bring_back(vault, ids)
        return {"shown": removed, "not_listed": [i for i in ids if i not in removed],
                "names": [(recs.get(i) or {}).get("name") or i for i in removed]}

    return _locked(vault, {"kind": "unhide", "names": names, "count": len(ids)}, go)
