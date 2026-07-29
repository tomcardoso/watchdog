"""Structural checks on the model-reasoning schemas (`pipeline/schemas.py`).

Two real constraints Claude's strict structured-output mode (`output_config.format.schema`)
enforces, both of which fail a live call outright (400) rather than degrading gracefully:

1. Every object-type schema node must explicitly declare `additionalProperties` — every
   `{"type": "object"}` in this module is meant to go through the `_obj()` helper, which always
   sets it, but a bare literal dict bypasses that and slips through silently until a live call
   hits it.
2. A hard cap on total optional (non-required) properties across the whole schema tree
   ("Schemas contains too many optional parameters... limit: 24").
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


def _count_optional(node, total=0) -> int:
    """Total optional (non-required) properties across every nested object/array in a schema
    tree — the same shape of count Claude's strict structured-output mode enforces a hard limit
    against ("Schemas contains too many optional parameters... limit: 24")."""
    if not isinstance(node, dict):
        return total
    if node.get("type") == "object":
        props = node.get("properties") or {}
        required = set(node.get("required") or [])
        total += sum(1 for k in props if k not in required)
        for sub in props.values():
            total = _count_optional(sub, total)
    items = node.get("items")
    if items:
        total = _count_optional(items, total)
    return total


# Anthropic doesn't document the exact counting rule (confirmed unclear even in their own repo's
# issue tracker as of this writing) — nullable-typed optional fields appear to cost more than
# plain ones. SECTION now has four nullable optional fields (document.date_of_document, plus
# morgue_entity_id/morgue_document_type/observations widened to nullable by #490, to stop OpenAI's
# weak json_object mode from hard-failing schema validation when it nulls an optional field
# instead of omitting it) — up from one before that fix. Even a full 2x worst-case weighting on
# all four only adds ~4 to SECTION's raw count of 21, landing at 25 — over the raw limit of 24,
# not just inside the margin. This hasn't been verified against a real claude-api call since the
# nullable-cost theory itself was never confirmed, only inferred defensively; treat this as a real
# open risk, not a settled one, until it's checked live.
_CLAUDE_OPTIONAL_PARAM_LIMIT = 24
_SAFETY_MARGIN = 2


def test_extraction_and_section_stay_under_claudes_optional_parameter_limit():
    for name in ("EXTRACTION", "SECTION"):
        n = _count_optional(getattr(schemas, name))
        assert n <= _CLAUDE_OPTIONAL_PARAM_LIMIT - _SAFETY_MARGIN, (
            f"{name} has {n} optional properties, too close to Claude's strict-mode limit of "
            f"{_CLAUDE_OPTIONAL_PARAM_LIMIT} (every claude-api extraction 400s outright when "
            f"this is exceeded) — this is a real API constraint, not a style preference")


def test_document_has_no_python_stamped_fields():
    """sha256/filename/original_path/page_count/source/obtained/file_metadata are set
    unconditionally by orchestrate._stamp_document after the model call returns — the model
    never fills any of them (confirmed: _stamp_document never reads a pre-existing value first).
    They don't belong in the model-facing schema; keeping them was the direct cause of hitting
    Claude's optional-parameter limit on corpus-v1's dense document."""
    doc_props = set(schemas.EXTRACTION["properties"]["document"]["properties"])
    stamped_only = {"sha256", "filename", "original_path", "page_count", "source",
                    "obtained", "file_metadata"}
    assert doc_props.isdisjoint(stamped_only)


def test_extraction_has_no_morgue_document_type():
    """Same dead-weight pattern as document's stamped fields — orchestrate._stamp_document
    unconditionally derives this as slugify(document_type), never reading the model's value."""
    assert "morgue_document_type" not in schemas.EXTRACTION["properties"]


def test_section_tolerates_explicit_null_on_its_optional_string_fields():
    """#490: gpt-nano's sectioned extraction hard-failed with 'None is not of type string' on
    exactly these three fields — OpenAI's json_object mode gives no wire-level shape enforcement,
    so a model that means 'nothing for this section' sometimes emits an explicit null instead of
    omitting the key. Every downstream reader already treats null and absent the same way, so
    these being nullable rather than bare 'string' should never fail validation."""
    import jsonschema
    section = {"document": {"key_facts": []}, "entities": [], "morgue_entity_id": None,
              "morgue_document_type": None, "observations": None}
    errors = list(jsonschema.Draft202012Validator(schemas.SECTION).iter_errors(section))
    assert not errors, [e.message for e in errors]


def test_section_requires_document_key_facts():
    """#496: gemini-flash under extractor_effort=low reliably omitted document.key_facts from
    every section of every sectioned document — silently, with no schema-validation error, no
    postflight warning, and a clean OK in ingest.log, because SECTION's inline `document`
    sub-schema had no `required` list at all. `document` itself was also optional at the top
    level, so a section could dodge the inner requirement by skipping the whole object. Both a
    missing `document` key and a `document` present but missing `key_facts` must now fail
    validation — an empty array is fine (a section can genuinely have no material facts), but
    omitting the field silently must not be."""
    import jsonschema
    validator = jsonschema.Draft202012Validator(schemas.SECTION)

    no_document = {"entities": []}
    assert list(validator.iter_errors(no_document))

    document_missing_key_facts = {"document": {"title": "x"}, "entities": []}
    assert list(validator.iter_errors(document_missing_key_facts))

    document_with_empty_key_facts = {"document": {"key_facts": []}, "entities": []}
    assert not list(validator.iter_errors(document_with_empty_key_facts))
