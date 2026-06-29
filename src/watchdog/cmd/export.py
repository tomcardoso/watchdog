"""Knowledge-graph export: emit the entity/relationship graph as Neo4j-import CSV
(or a single Cypher file). Fully deterministic — reads `.watchdog/Registry/entities.json`
and writes; no model calls, no markdown parsing. See ARCHITECTURE D39."""

import csv
import json
import re
import sys
from pathlib import Path

from watchdog.cmd.base import (
    _BOLD,
    _CYAN,
    _DIM,
    _GREEN,
    _RESET,
    _resolve_vault,
)


def _forward_edges(entities: dict) -> tuple[list[dict], int]:
    """Stated-direction roles only, with both endpoints present in the node set.

    The registry stores a reverse copy (`is_reverse: true`) of every relationship and may
    reference targets that were never profiled as their own entity. Emitting reverse roles
    would double every edge; emitting an edge to a missing node breaks `neo4j-admin import`.
    Both are dropped here; returns (edges, dangling_count) for the run summary."""
    edges, dangling = [], 0
    for eid, ent in entities.items():
        for role in ent.get("roles", []):
            if role.get("is_reverse"):
                continue
            target = role.get("target_id")
            if not target or target not in entities:
                dangling += 1
                continue
            edges.append({
                "start": eid,
                "end": target,
                "type": role.get("relationship", ""),
                "page": role.get("page"),
                "basis": role.get("basis", "stated"),
                "date_range": role.get("date_range") or "",
            })
    return edges, dangling


def _write_csv(entities: dict, edges: list[dict], out: Path) -> tuple[Path, Path]:
    nodes_csv = out / "nodes.csv"
    rels_csv = out / "relationships.csv"

    with nodes_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([":ID", "name", ":LABEL", "type", "doc_count:int"])
        for eid, ent in entities.items():
            w.writerow([
                eid,
                ent.get("name", ""),
                ent.get("type", ""),
                ent.get("type", ""),
                len(ent.get("appears_in", [])),
            ])

    with rels_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([":START_ID", ":END_ID", ":TYPE", "source_page:int", "basis", "date_range"])
        for e in edges:
            w.writerow([
                e["start"],
                e["end"],
                e["type"],
                "" if e["page"] is None else e["page"],
                e["basis"],
                e["date_range"],
            ])

    return nodes_csv, rels_csv


_LABEL_RE = re.compile(r"[^A-Za-z0-9_]")


def _cypher_label(type_: str) -> str:
    """Map an entity type to a Cypher label token (backtick-quoted at the call site)."""
    return _LABEL_RE.sub("_", type_).strip("_") or "Entity"


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _write_cypher(entities: dict, edges: list[dict], out: Path) -> Path:
    path = out / "graph.cypher"
    lines = []
    for eid, ent in entities.items():
        label = _cypher_label(ent.get("type", ""))
        lines.append(
            f"MERGE (n:`{label}` {{id: '{_esc(eid)}'}}) "
            f"SET n.name = '{_esc(ent.get('name', ''))}', "
            f"n.type = '{_esc(ent.get('type', ''))}', "
            f"n.doc_count = {len(ent.get('appears_in', []))};"
        )
    for e in edges:
        rel = _LABEL_RE.sub("_", e["type"]).strip("_").upper() or "RELATED_TO"
        props = [f"page: {e['page']}"] if e["page"] is not None else []
        props.append(f"basis: '{_esc(e['basis'])}'")
        if e["date_range"]:
            props.append(f"date_range: '{_esc(e['date_range'])}'")
        lines.append(
            f"MATCH (a {{id: '{_esc(e['start'])}'}}), (b {{id: '{_esc(e['end'])}'}}) "
            f"MERGE (a)-[r:`{rel}`]->(b) SET r += {{{', '.join(props)}}};"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def cmd_export(args) -> None:
    slug, info, vault = _resolve_vault(args.project)

    entities_path = vault / ".watchdog" / "Registry" / "entities.json"
    if not entities_path.exists():
        sys.exit(f"Error: no entity registry found for {info['name']} — has anything been ingested?")
    try:
        entities = json.loads(entities_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"Error: entities.json is corrupt — {e}")

    if not entities:
        sys.exit(f"Error: {info['name']} has no entities to export yet.")

    edges, dangling = _forward_edges(entities)

    out = Path(args.output) if args.output else Path(f"{slug}-export")
    out.mkdir(parents=True, exist_ok=True)

    print()
    if args.format == "cypher":
        path = _write_cypher(entities, edges, out)
        print(f"  {_GREEN}Exported:{_RESET} {_BOLD}{len(entities)}{_RESET} nodes, "
              f"{_BOLD}{len(edges)}{_RESET} relationships")
        print(f"  {_CYAN}{path}{_RESET}")
        print(f"  {_DIM}Load with:  cat {path} | cypher-shell{_RESET}")
    else:
        nodes_csv, rels_csv = _write_csv(entities, edges, out)
        print(f"  {_GREEN}Exported:{_RESET} {_BOLD}{len(entities)}{_RESET} nodes, "
              f"{_BOLD}{len(edges)}{_RESET} relationships")
        print(f"  {_CYAN}{nodes_csv}{_RESET}")
        print(f"  {_CYAN}{rels_csv}{_RESET}")
        print(f"  {_DIM}Import with:  neo4j-admin database import full "
              f"--nodes={nodes_csv.name} --relationships={rels_csv.name} <db>{_RESET}")
        print(f"  {_DIM}Or open nodes.csv / relationships.csv directly in Gephi.{_RESET}")

    if dangling:
        print(f"  {_DIM}Skipped {dangling} relationship(s) pointing at unprofiled entities.{_RESET}")
    print()
