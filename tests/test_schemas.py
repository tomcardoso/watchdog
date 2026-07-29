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
# plain ones. SECTION now has eight nullable optional fields (document.date_of_document; morgue_
# entity_id/morgue_document_type/observations widened by #490/D147; document.title/document_type/
# summary widened by #490/D149; key_facts[].date widened by #490/D150 — all to stop OpenAI's weak
# json_object mode from hard-failing schema validation when it nulls an optional field instead of
# omitting it) — up from one before the first of those fixes. SECTION's raw optional-property
# count is 20 (also up by one: `entities` itself was made optional by #490/D150); even a full 2x
# worst-case weighting on all eight nullable fields adds ~8, landing at 28 — over the raw limit of
# 24, not just inside the margin. This hasn't been verified against a real claude-api call since
# the nullable-cost theory itself was never confirmed, only inferred defensively; treat this as a
# real open risk, not a settled one, until it's checked live.
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


def test_section_document_tolerates_explicit_null_on_title_type_and_summary():
    """Live gpt-nano regression (post-#496): once document.key_facts became required on every
    section, a later section with nothing to add for title/document_type/summary — genuinely
    section-1-only fields — started emitting explicit null for them instead of omitting the keys,
    hard-failing with the exact same 'None is not of type string' shape #490 already fixed once
    for the morgue/observations fields. merge.merge_extractions only ever reads these three from
    the first section with a document dict, so a later section's null costs nothing downstream."""
    import jsonschema
    section = {"document": {"key_facts": [], "title": None, "document_type": None,
                             "summary": None}, "entities": []}
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


def test_section_does_not_require_entities():
    """Live gpt-nano regression (#490 follow-up): a section naming no new entities omitted the
    `entities` key entirely rather than returning an empty array, hard-failing with
    "'entities' is a required property". `entities` has been required since the original
    Python-orchestrator commit, not from a documented silent-omission incident the way
    document.key_facts was (#496) — and merge.merge_extractions already reads it defensively
    (`sec.get("entities", [])`), so an omitted key was always handled safely downstream."""
    import jsonschema
    section = {"document": {"key_facts": []}}
    errors = list(jsonschema.Draft202012Validator(schemas.SECTION).iter_errors(section))
    assert not errors, [e.message for e in errors]


def test_key_fact_tolerates_explicit_null_date():
    """Live gpt-nano regression (#490 follow-up): a sectioned extraction nulled `date` on 21 of
    26 key_facts in one document rather than omitting it — bare "string" made every one of those
    a hard schema-validation failure. Every reader already treats null and absent the same way
    (postflight._sanitize_dates's `if date and ...`, explode_key_facts's `(fact.get("date") or
    "").strip()`), so this being nullable costs nothing."""
    import jsonschema
    key_fact = {"fact": "something happened", "date": None}
    errors = list(jsonschema.Draft202012Validator(schemas._KEY_FACT).iter_errors(key_fact))
    assert not errors, [e.message for e in errors]
