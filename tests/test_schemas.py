"""Structural checks on the model-reasoning schemas (`pipeline/schemas.py`).

Claude's strict structured-output mode (`output_config.format.schema`) rejects any object-type
schema node that doesn't explicitly declare `additionalProperties` — every `{"type": "object"}`
in this module is meant to go through the `_obj()` helper, which always sets it, but a bare
literal dict (like the `file_metadata` bug this guards against) bypasses that and slips through
silently until a live call 400s.
"""
from watchdog.pipeline import schemas


def _object_nodes_missing_additional_properties(node, path="$") -> list[str]:
    """Recursively walk a schema, returning the path of every object-type node that omits
    `additionalProperties` — regardless of how deeply it's nested in `properties`/`items`."""
    missing = []
    if not isinstance(node, dict):
        return missing
    if node.get("type") == "object" and "additionalProperties" not in node:
        missing.append(path)
    for key, sub in (node.get("properties") or {}).items():
        missing += _object_nodes_missing_additional_properties(sub, f"{path}.{key}")
    items = node.get("items")
    if items:
        missing += _object_nodes_missing_additional_properties(items, f"{path}[]")
    return missing


_ALL_SCHEMAS = {
    name: getattr(schemas, name)
    for name in ("EXTRACTION", "SECTION", "CLASSIFY", "DIGEST", "SYNTHESIS", "RECONCILE",
                 "BRIEFING", "TIMELINE_DEDUP", "TIMELINE_PRECISION_MATCH")
}


def test_every_object_node_declares_additional_properties():
    for name, schema in _ALL_SCHEMAS.items():
        missing = _object_nodes_missing_additional_properties(schema)
        assert not missing, f"{name} has object node(s) missing 'additionalProperties': {missing}"


def test_file_metadata_is_closed_and_empty():
    """The model never fills this field (Python stamps real values in after the call, D-none —
    see schemas.py's comment on _DOCUMENT) — closed-and-empty is the correct schema for it, not
    just the one that satisfies Claude's strict mode."""
    file_metadata = schemas.EXTRACTION["properties"]["document"]["properties"]["file_metadata"]
    assert file_metadata["additionalProperties"] is False
    assert file_metadata["properties"] == {}
