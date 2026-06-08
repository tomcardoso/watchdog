#!/usr/bin/env python3
"""
Watchdog arrows.app JSON parser.

Converts an arrows.app export into a structured format the ingest skill
can use to create entity notes and relationships directly — bypassing the
Docling pipeline entirely, since the structure is already explicit.

Usage:
    python3 arrows_parser.py <arrows_file.json>

Output JSON:
{
  "entities": [
    {
      "id": str,                  # kebab-case, derived from caption
      "name": str,
      "type": str,                # from labels[0], or "Entity" if none
      "properties": dict,
      "arrows_id": str            # original node id from arrows.app
    }
  ],
  "relationships": [
    {
      "from_id": str,             # kebab-case entity id
      "to_id": str,               # kebab-case entity id
      "type": str,                # relationship type (e.g. DIRECTOR_OF)
      "properties": dict
    }
  ],
  "source_file": str
}
"""

import json
import sys
from pathlib import Path

from watchdog.pipeline.write_vault import slugify


def parse_arrows(path: Path) -> dict:
    with open(path) as f:
        data = json.load(f)

    nodes = data.get("nodes", [])
    relationships = data.get("relationships", [])

    # Map arrows internal IDs to kebab-case entity IDs
    id_map: dict[str, str] = {}
    seen_ids: set[str] = set()
    entities = []

    for node in nodes:
        caption = node.get("caption") or node.get("id", "unknown")
        labels = node.get("labels", [])
        entity_type = labels[0].capitalize() if labels else "Entity"
        entity_id = slugify(caption)

        # Deduplicate: suffix with a counter until the slug is unique
        if entity_id in seen_ids:
            counter = 1
            while f"{entity_id}-{counter}" in seen_ids:
                counter += 1
            entity_id = f"{entity_id}-{counter}"

        seen_ids.add(entity_id)
        id_map[node["id"]] = entity_id
        entities.append(
            {
                "id": entity_id,
                "name": caption,
                "type": entity_type,
                "properties": node.get("properties", {}),
                "arrows_id": node["id"],
            }
        )

    parsed_relationships = []
    for rel in relationships:
        from_arrows = rel.get("fromId") or rel.get("from")
        to_arrows = rel.get("toId") or rel.get("to")
        if from_arrows not in id_map or to_arrows not in id_map:
            continue
        parsed_relationships.append(
            {
                "from_id": id_map[from_arrows],
                "to_id": id_map[to_arrows],
                "type": rel.get("type", "RELATED_TO"),
                "properties": rel.get("properties", {}),
            }
        )

    return {
        "entities": entities,
        "relationships": parsed_relationships,
        "source_file": path.name,
    }


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: arrows_parser.py <file.json>"}))
        sys.exit(1)

    path = Path(sys.argv[1]).resolve()
    if not path.exists():
        print(json.dumps({"error": f"File not found: {path}"}))
        sys.exit(1)

    try:
        result = parse_arrows(path)
    except (json.JSONDecodeError, KeyError) as e:
        print(json.dumps({"error": f"Failed to parse arrows.app JSON: {e}"}))
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
