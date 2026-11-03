"""A document's details set from the app (D381): where it came from, when it was obtained, the
reporter's notes and the record skill it is pinned to, the fields of its `.yml` sidecar.

The sidecar stays the one store. A file waiting to be read keeps its `.yml` beside it in
`incoming/`, so editing it there is editing that file. Once pre-processing has read a document the
filtered sidecar text travels inside Watchdog's own records (D121): the queue descriptor, the copy
kept for a retry, the stored extraction (which also carries `source` and `obtained`) and, after
commit, the `.yml` beside the original in `morgue/`. A change is written to every one of those
that exists, and the document's note is written again from the stored extraction, so what the
reporter sees and what a later extraction reads agree.

A changed skill pin on a document already extracted only takes effect when it is extracted again,
which is billed; the app asks first and starts that run itself (Retry extraction, D367). This
module refuses a new pin for a document whose text is no longer kept, since nothing could act on
it."""

from __future__ import annotations

import json
import re
from pathlib import Path

from watchdog.pipeline import sidecar
from watchdog.pipeline.json_io import _read_json_or

_SHA = re.compile(r"^[0-9a-f]{64}$")


class DetailsError(ValueError):
    """A change refused, with the reason in words the app shows."""


# ── values ────────────────────────────────────────────────────────────────────────────────

def _skill(value: str) -> str:
    from watchdog import skills_catalog
    if not value:
        return ""
    name = value.removesuffix(".md")
    if not skills_catalog.resolve_name(name):
        raise DetailsError(f"There is no record skill called “{value}”.")
    return name


def _language(value: str) -> str:
    """A language Watchdog can read a scan in (D383), written as its name."""
    from watchdog.pipeline import language
    if not value:
        return ""
    code = language.normalize(value)
    if code is None:
        raise DetailsError(f"Watchdog doesn't know the language “{value}”. Use a name such as "
                           f"French, or a code such as fr.")
    return language.name(code)


# A field whose value needs more than tidying names its check here; the rest are plain text.
_CHECKS = {"skill": _skill, "language": _language}


def clean(values: dict) -> dict[str, str]:
    """`values` as they will be written: each a string, stripped, '' to remove the field. Only
    the editable fields (`sidecar.EDITABLE_KEYS`) are accepted."""
    if not isinstance(values, dict):
        raise DetailsError("The details must be a set of named fields.")
    unknown = sorted(k for k in values if k not in sidecar.EDITABLE_KEYS)
    if unknown:
        raise DetailsError(f"“{unknown[0]}” is not a detail that can be set here.")
    out = {}
    for key, value in values.items():
        if value is not None and not isinstance(value, str):
            raise DetailsError(f"The {key} must be text.")
        text = (value or "").strip()
        if len(text) > sidecar.MAX_VALUE_LEN:
            raise DetailsError(f"The {key} is limited to {sidecar.MAX_VALUE_LEN:,} characters.")
        out[key] = _CHECKS.get(key, lambda v: v)(text)
    return out


def editable(sidecar_text: str | None) -> dict[str, str]:
    """The editable fields of a sidecar, as the form shows them ('' for a field not set)."""
    data = sidecar.parse(sidecar_text)
    return {k: "" if data.get(k) is None else str(data[k]).strip() for k in sidecar.EDITABLE_KEYS}


def _changed(before: dict, values: dict) -> list[str]:
    return [k for k in sidecar.EDITABLE_KEYS if k in values and values[k] != before.get(k, "")]


def _same_skill(a: str, b: str) -> bool:
    return a.removesuffix(".md") == b.removesuffix(".md")


# ── a file waiting to be read ─────────────────────────────────────────────────────────────

def _waiting_file(vault: Path, path: str) -> Path:
    from watchdog.vault_paths import SET_ASIDE_NAMES, incoming_dir
    incoming = incoming_dir(vault).resolve()
    f = (Path(vault) / path).resolve()
    try:
        parts = f.relative_to(incoming).parts
    except ValueError:
        raise DetailsError("Only a file waiting to be added can be changed this way.") from None
    if parts[0] in SET_ASIDE_NAMES or f.name.endswith(".yml") or not f.is_file():
        raise DetailsError("That file is no longer waiting to be added.")
    return f


def _sidecar_file(f: Path) -> Path:
    return f.with_name(f.name + ".yml")


def read_waiting(vault: Path, path: str) -> dict[str, str]:
    side = _sidecar_file(_waiting_file(vault, path))
    try:
        raw = side.read_text(encoding="utf-8") if side.exists() else None
    except OSError:
        raw = None
    return editable(raw)


def set_waiting(vault: Path, path: str, values: dict) -> dict:
    """Write `values` into the sidecar beside a file waiting in `incoming/`, keeping any other
    field it holds; the sidecar is removed when nothing is left in it."""
    from watchdog.pipeline.locks import held
    from watchdog.vault_paths import preprocessing_lock
    values = clean(values)
    f = _waiting_file(vault, path)
    if held(preprocessing_lock(Path(vault))) is not None:
        raise DetailsError("Files are being read in this investigation. Wait for that to finish, "
                           "then change the details.")
    side = _sidecar_file(f)
    raw = side.read_text(encoding="utf-8") if side.exists() else None
    before = editable(raw)
    try:
        text = sidecar.edit(raw, values)
    except ValueError:
        raise DetailsError(f"The details file beside {f.name} could not be read, so it was left "
                           f"as it is.") from None
    if text is None:
        side.unlink(missing_ok=True)
    else:
        tmp = side.with_name(f".{side.name}.part")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(side)
    return {"filename": f.name, "stage": "waiting", "changed": _changed(before, values)}


# ── a document pre-processing has read ────────────────────────────────────────────────────

def _records(vault: Path, sha: str) -> dict:
    """Where `sha`'s sidecar is kept, and how far the document has gone."""
    from watchdog.pipeline.ingest_setup import kept_dir
    if not isinstance(sha, str) or not _SHA.match(sha):
        raise DetailsError("That document could not be found.")
    w = Path(vault) / ".watchdog"
    queue = w / "queue" / f"{sha}.json"
    kept = kept_dir(vault) / f"{sha}.json"
    failed = w / "queue" / "_failed" / f"{sha}.json"
    extracted = w / "extracted" / f"{sha}.json"
    rec = _read_json_or(w / "registry" / "documents.json", {}).get(sha)
    rec = rec if isinstance(rec, dict) else None
    descriptors = [p for p in (queue, kept, failed) if p.exists()]
    if rec:
        stage = "added"
    elif extracted.exists() and queue.exists():
        stage = "staged"
    elif descriptors:
        stage = "queued"
    else:
        raise DetailsError("That document could not be found.")
    return {"rec": rec, "stage": stage, "descriptors": descriptors,
            "extracted": extracted if extracted.exists() else None,
            # Retry extraction can send the text again only from a queue descriptor (D367).
            "reextract": queue.exists() or kept.exists()}


def _current_text(vault: Path, r: dict) -> str | None:
    if r["extracted"] is not None:
        doc = (_read_json_or(r["extracted"], {}) or {}).get("document") or {}
        return doc.get("sidecar")
    for p in r["descriptors"]:
        return (_read_json_or(p, {}) or {}).get("sidecar")
    morgue = (r["rec"] or {}).get("morgue_path")
    if morgue:
        side = Path(vault) / f"{morgue}.yml"
        if side.is_file():
            return sidecar.filter_and_render(side.read_text(encoding="utf-8"))[0]
    return None


def read_document(vault: Path, sha: str) -> dict:
    """The form's starting values for a document, its stage, and whether a new skill could be
    acted on (`skill_costs`: it means extracting again; `reextract`: its text is kept for that)."""
    r = _records(Path(vault), sha)
    return {"values": editable(_current_text(Path(vault), r)), "stage": r["stage"],
            "skill_costs": r["stage"] in ("staged", "added"), "reextract": r["reextract"]}


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(f".{path.name}.part")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def set_document(vault: Path, sha: str, values: dict) -> dict:
    """Write `values` into every copy of a read document's sidecar, update the stored
    extraction's `source`/`obtained`, and write its note again when it has been added."""
    from watchdog.pipeline import entity_notes, history
    from watchdog.pipeline.write_vault import _assert_in_vault, _registry_lock
    vault = Path(vault)
    values = clean(values)
    if history.run_in_progress(vault):
        raise DetailsError("Documents are being processed in this investigation. Wait for that to "
                           "finish, then change the details.")
    reg = vault / ".watchdog" / "registry"
    reg.mkdir(parents=True, exist_ok=True)
    with _registry_lock(reg):
        r = _records(vault, sha)
        before = editable(_current_text(vault, r))
        changed = _changed(before, values)
        if "language" in changed:
            # The language decides how a document is read (D383), and this one has been read.
            raise DetailsError("A document’s language is used when it is read, and this one has "
                               "been read. Its Details tab says the language it was read in.")
        skill_changed = "skill" in changed and not _same_skill(before["skill"], values["skill"])
        if skill_changed and r["stage"] in ("staged", "added") and not r["reextract"]:
            raise DetailsError("This document’s text is no longer kept, so it cannot be extracted "
                               "again with another record skill.")
        rec = r["rec"] or {}
        filename = rec.get("filename") or sha[:12]
        note = rec.get("document_note")
        scope = [f"{note}.md"] if note else []
        if not changed:
            return {"filename": filename, "stage": r["stage"], "changed": [],
                    "skill_changed": False, "reextract": r["reextract"]}

        def rewrite(text: str | None) -> str | None:
            return sidecar.filter_and_render(sidecar.edit(text, values))[0]

        with history.recording(vault, {"kind": "details", "name": filename}, scope):
            for p in r["descriptors"]:
                data = _read_json_or(p, None)
                if isinstance(data, dict):
                    filename = rec.get("filename") or data.get("filename") or filename
                    data["sidecar"] = rewrite(data.get("sidecar"))
                    _write_json(p, data)
            if r["extracted"] is not None:
                art = _read_json_or(r["extracted"], None)
                if isinstance(art, dict) and isinstance(art.get("document"), dict):
                    doc = art["document"]
                    doc["sidecar"] = rewrite(doc.get("sidecar"))
                    for k in ("source", "obtained"):
                        doc.pop(k, None)
                    doc.update(sidecar.provenance(doc["sidecar"]))
                    _write_json(r["extracted"], art)
            if rec.get("morgue_path"):
                side = _assert_in_vault(vault / f"{rec['morgue_path']}.yml", vault, "morgue sidecar")
                text = rewrite(side.read_text(encoding="utf-8") if side.exists() else None)
                if text is None:
                    side.unlink(missing_ok=True)
                elif side.parent.is_dir():
                    side.write_text(text, encoding="utf-8")
            if note and {"source", "obtained"} & set(changed):
                # Full-text index only, as for a mark (D271): a source changes a line, not what
                # the note is about, and loading the embedding model would stall the app.
                documents = _read_json_or(reg / "documents.json", {})
                entities = _read_json_or(reg / "entities.json", {})
                entity_notes.write_queued_documents(vault, [sha], entities, documents, embed=False)
    return {"filename": filename, "stage": r["stage"], "changed": changed,
            "skill_changed": skill_changed, "reextract": r["reextract"]}
