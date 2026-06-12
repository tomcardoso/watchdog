"""Registry query commands: entity-index, validate-extraction, is-duplicate."""

import json
import sys
from pathlib import Path

from watchdog.cmd.base import _find_project

_VALID_CONFIDENCE = {"high", "medium", "low", "disputed"}


def cmd_entity_index(args) -> None:
    cwd = Path(".").resolve()
    if (cwd / ".watchdog").is_dir():
        vault = cwd
    else:
        _, info = _find_project(args.project)
        vault = Path(info["path"])

    manifest_file = vault / ".watchdog" / "Registry" / "manifest.json"
    if not manifest_file.exists():
        print("[]")
        return

    try:
        manifest = json.loads(manifest_file.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"Error: manifest.json is corrupt — {e}")

    compact = [
        {"id": eid, "name": e["name"], "type": e["type"], "aliases": e.get("aliases", [])}
        for eid, e in manifest.items()
        if e.get("name")
    ]
    print(json.dumps(compact, ensure_ascii=False))


def cmd_validate_extraction(args) -> None:
    path = Path(args.file).resolve()
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")
    if not str(path).startswith(str(vault) + "/"):
        sys.exit(f"Error: file must be inside the vault directory ({vault})")
    if not path.exists():
        sys.exit(f"Error: file not found: {path}")

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"Error: invalid JSON — {e}")

    errors = []

    doc = data.get("document")
    if not isinstance(doc, dict):
        errors.append("missing or invalid 'document' field")
    else:
        for field in ("sha256", "filename"):
            if not doc.get(field):
                errors.append(f"document.{field} is missing or empty")
        for fact in doc.get("key_facts", []):
            if not isinstance(fact, dict):
                errors.append("key_facts contains a non-object entry")
            elif fact.get("confidence") and fact["confidence"] not in _VALID_CONFIDENCE:
                errors.append(f"key_facts confidence '{fact['confidence']}' is not valid")

    entities = data.get("entities")
    if not isinstance(entities, list):
        errors.append("missing or invalid 'entities' field")
    else:
        for i, ent in enumerate(entities):
            if not isinstance(ent, dict):
                errors.append(f"entities[{i}] is not an object")
                continue
            for field in ("id", "name", "type"):
                if not ent.get(field):
                    errors.append(f"entities[{i}].{field} is missing or empty")
            for j, ev in enumerate(ent.get("timeline_events", [])):
                if not isinstance(ev, dict):
                    errors.append(f"entities[{i}].timeline_events[{j}] is not an object")
                elif ev.get("confidence") and ev["confidence"] not in _VALID_CONFIDENCE:
                    errors.append(f"entities[{i}].timeline_events[{j}] confidence '{ev['confidence']}' is not valid")
            for j, role in enumerate(ent.get("roles", [])):
                if not isinstance(role, dict):
                    errors.append(f"entities[{i}].roles[{j}] is not an object")

    if not data.get("morgue_entity_id"):
        errors.append("morgue_entity_id is missing or empty")
    if not data.get("morgue_document_type"):
        errors.append("morgue_document_type is missing or empty")

    if errors:
        for e in errors:
            print(f"error: {e}")
        sys.exit(1)

    print("ok")


def cmd_is_duplicate(args) -> None:
    cwd = Path(".").resolve()
    if (cwd / ".watchdog").is_dir():
        vault = cwd
    else:
        _, info = _find_project(args.project)
        vault = Path(info["path"])

    docs_file = vault / ".watchdog" / "Registry" / "documents.json"
    try:
        docs = json.loads(docs_file.read_text()) if docs_file.exists() else {}
    except json.JSONDecodeError:
        docs = {}

    if args.sha256 in docs:
        print("dup")
        sys.exit(1)
    print("ok")
