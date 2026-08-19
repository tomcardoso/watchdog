"""Registry query commands: entity-index, is-duplicate."""

import json
import sys
from pathlib import Path

from watchdog.cmd.base import _find_project
from watchdog.pipeline.json_io import _read_json, _read_json_or


def cmd_entity_index(args) -> None:
    cwd = Path(".").resolve()
    if (cwd / ".watchdog").is_dir():
        vault = cwd
    else:
        _, info = _find_project(args.project)
        vault = Path(info["path"])

    manifest_file = vault / ".watchdog" / "registry" / "manifest.json"
    if not manifest_file.exists():
        print("[]")
        return

    try:
        manifest = _read_json(manifest_file)
    except json.JSONDecodeError as e:
        sys.exit(f"Error: manifest.json is corrupt — {e}")

    compact = [
        {"id": eid, "name": e["name"], "type": e["type"], "aliases": e.get("aliases", [])}
        for eid, e in manifest.items()
        if e.get("name")
    ]
    print(json.dumps(compact, ensure_ascii=False))


def cmd_is_duplicate(args) -> None:
    cwd = Path(".").resolve()
    if (cwd / ".watchdog").is_dir():
        vault = cwd
    else:
        _, info = _find_project(args.project)
        vault = Path(info["path"])

    docs_file = vault / ".watchdog" / "registry" / "documents.json"
    docs = _read_json_or(docs_file, {}, catch=(json.JSONDecodeError,)) if docs_file.exists() else {}

    if args.sha256 in docs:
        print("dup")
        sys.exit(1)
    print("ok")
