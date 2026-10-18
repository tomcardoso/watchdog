"""Knowledge-graph export: emit the entity/relationship graph as Neo4j-import CSV
(or a single Cypher file). Fully deterministic — reads `.watchdog/registry/entities.json`
and writes; no model calls, no markdown parsing. See DECISIONS D39."""

import csv
import json
import re
import sys
from pathlib import Path

from watchdog.ops import op, require_vault
from watchdog.pipeline.json_io import _read_json


def _forward_edges(entities: dict, canonical=None) -> tuple[list[dict], int]:
    """Stated-direction roles only, with both endpoints present in the node set. `type` is the
    canonical label (`canonical(start, end, wording)`, from the relationship view, D291) and
    `wording` the document's own words; without `canonical` the two are the same.

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
            wording = role.get("relationship", "")
            edges.append({
                "start": eid,
                "end": target,
                "type": canonical(eid, target, wording) if canonical else wording,
                "wording": wording,
                "page": role.get("page"),
                "basis": role.get("basis", "stated"),
                "date_range": role.get("date_range") or "",
                "source_sha256": role.get("source_sha256"),   # unused by the exporters; the GUI graph reads it
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
        w.writerow([":START_ID", ":END_ID", ":TYPE", "wording", "source_page:int", "basis",
                    "date_range"])
        for e in edges:
            w.writerow([
                e["start"],
                e["end"],
                e["type"],
                e.get("wording", e["type"]),
                "" if e["page"] is None else e["page"],
                e["basis"],
                e["date_range"],
            ])

    return nodes_csv, rels_csv


_LABEL_RE = re.compile(r"[^A-Za-z0-9_]")

# The label every exported node carries alongside its type label.
_NODE_LABEL = "WatchdogEntity"


def _write_facts_csv(vault: Path, out: Path) -> tuple[Path, int, int]:
    """Every fact with its source passage and the reporter's verification mark (D270, D271), one
    row each. Returns (path, facts, marked)."""
    from watchdog.pipeline import verification

    docs = _read_json(vault / ".watchdog" / "registry" / "documents.json") \
        if (vault / ".watchdog" / "registry" / "documents.json").exists() else {}
    marks = verification.marks(vault)
    path = out / "facts.csv"
    n = marked = 0
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["fact_id", "document", "sha256", "page", "fact", "basis", "passage",
                    "passage_page", "passage_method", "status", "checked_by", "checked_at", "note"])
        for sha, rec in sorted(docs.items(), key=lambda kv: (kv[1].get("filename") or "", kv[0])):
            facts = verification.document_facts(vault, sha, rec)
            for fid, fact in zip(verification.fact_ids(sha, facts), facts):
                # Only a mark made on these words counts (D271); a disputed fact is exported with
                # its status like every other, never left out (D285).
                m = verification.attach(marks.get(fid), fact) or {}
                status = m.get("status")
                n += 1
                marked += bool(status)
                w.writerow([fid, rec.get("filename") or "", sha, fact.get("page") or "",
                            fact.get("fact") or "", fact.get("basis") or "stated",
                            fact.get("passage") or "", fact.get("passage_page") or "",
                            fact.get("passage_method") or "",
                            verification.LABELS.get(status, "") if status else "",
                            m.get("by") or "" if status else "", m.get("at") or "" if status else "",
                            m.get("note") or "" if status else ""])
    return path, n, marked


def _cypher_label(type_: str) -> str:
    """Map an entity type to a Cypher label token (backtick-quoted at the call site)."""
    return _LABEL_RE.sub("_", type_).strip("_") or "Entity"


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _write_cypher(entities: dict, edges: list[dict], out: Path) -> Path:
    path = out / "graph.cypher"
    # Every node also carries one shared label with a uniqueness constraint on `id`, so each
    # relationship's MATCH is an index lookup rather than a scan of every node (Neo4j 4.4+).
    lines = [f"CREATE CONSTRAINT watchdog_entity_id IF NOT EXISTS "
             f"FOR (n:`{_NODE_LABEL}`) REQUIRE n.id IS UNIQUE;"]
    for eid, ent in entities.items():
        label = _cypher_label(ent.get("type", ""))
        lines.append(
            f"MERGE (n:`{_NODE_LABEL}` {{id: '{_esc(eid)}'}}) "
            f"SET n:`{label}`, n.name = '{_esc(ent.get('name', ''))}', "
            f"n.type = '{_esc(ent.get('type', ''))}', "
            f"n.doc_count = {len(ent.get('appears_in', []))};"
        )
    for e in edges:
        rel = _LABEL_RE.sub("_", e["type"]).strip("_").upper() or "RELATED_TO"
        # The document's wording is part of the MERGE key, so two wordings grouped under one
        # canonical type stay two relationships, each with its own page.
        wording = _esc(e.get("wording", e["type"]))
        props = [f"page: {e['page']}"] if e["page"] is not None else []
        props.append(f"basis: '{_esc(e['basis'])}'")
        if e["date_range"]:
            props.append(f"date_range: '{_esc(e['date_range'])}'")
        lines.append(
            f"MATCH (a:`{_NODE_LABEL}` {{id: '{_esc(e['start'])}'}}), "
            f"(b:`{_NODE_LABEL}` {{id: '{_esc(e['end'])}'}}) "
            f"MERGE (a)-[r:`{rel}` {{wording: '{wording}'}}]->(b) SET r += {{{', '.join(props)}}};"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@op("export")
def export(rep, vault: Path, *, format: str = "csv", output: str | None = None) -> dict:
    """Export the entity graph as CSV (with the facts) or Cypher, into `output` (default: an
    `<investigation>-export` folder inside the investigation)."""
    if format not in ("csv", "cypher"):
        sys.exit("Error: the format is csv or cypher.")
    require_vault(vault)
    entities_path = vault / ".watchdog" / "registry" / "entities.json"
    if not entities_path.exists():
        sys.exit("Error: nothing has been added to this investigation yet, so there is nothing to export.")
    try:
        entities = _read_json(entities_path)
    except json.JSONDecodeError as e:
        sys.exit(f"Error: the entity registry is damaged ({e}).")
    if not entities:
        sys.exit("Error: this investigation has no entities to export yet.")

    from watchdog.pipeline import relationships
    from watchdog.pipeline.write_vault import slugify
    view = relationships.View(vault)
    edges, dangling = _forward_edges(entities, view.canonical)
    out = Path(output) if output else vault / f"{slugify(vault.name)}-export"
    out.mkdir(parents=True, exist_ok=True)

    rep.log(f"Exported {len(entities)} entities and {len(edges)} relationships to {out}.")
    if format == "cypher":
        path = _write_cypher(entities, edges, out)
        rep.log(f"  {path.name}: load it with Neo4j's cypher-shell.")
    else:
        nodes_csv, rels_csv = _write_csv(entities, edges, out)
        rep.log(f"  {nodes_csv.name} and {rels_csv.name}: import them into Neo4j, or open them in Gephi.")
        facts_csv, n_facts, n_marked = _write_facts_csv(vault, out)
        rep.log(f"  {facts_csv.name}: {n_facts} facts with their source passages; {n_marked} "
                "marked in the verification ledger.")
    if dangling:
        rep.log(f"Skipped {dangling} relationship(s) pointing at entities without a note of their own.")
    return {"format": format, "output": str(out), "entities": len(entities), "relationships": len(edges)}
