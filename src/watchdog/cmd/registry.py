"""Registry query commands: entity-index, is-duplicate."""

import json
import sys
from pathlib import Path

from watchdog.cmd.base import _find_project


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
        manifest = json.loads(manifest_file.read_text())
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
    try:
        docs = json.loads(docs_file.read_text()) if docs_file.exists() else {}
    except json.JSONDecodeError:
        docs = {}

    if args.sha256 in docs:
        print("dup")
        sys.exit(1)
    print("ok")
