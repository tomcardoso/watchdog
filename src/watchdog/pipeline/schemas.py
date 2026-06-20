"""JSON schemas for the model reasoning tasks the Python orchestrator runs (#118).

The model is called only for reasoning; these schemas are the contract for what it
must return. EXTRACTION mirrors what ``postflight._validate`` requires (plus the richer
fields write_vault consumes) — keep the two in sync.
"""

_CONFIDENCE = {"type": "string", "enum": ["high", "medium", "low", "disputed"]}
_NULLABLE_STR = {"type": ["string", "null"]}


def _obj(properties: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


_KEY_FACT = _obj(
    {"fact": {"type": "string"}, "page": {"type": ["integer", "null"]}, "confidence": _CONFIDENCE},
    ["fact", "confidence"],
)

_TIMELINE_EVENT = _obj(
    {
        "date": {"type": "string"},
        "event": {"type": "string"},
        "page": {"type": ["integer", "null"]},
        "confidence": _CONFIDENCE,
    },
    ["date", "event", "confidence"],
)

_ROLE = _obj(
    {
        "relationship": {"type": "string"},
        "target_id": {"type": "string"},
        "target_type": {"type": "string"},
        "target_name": {"type": "string"},
        "page": {"type": ["integer", "null"]},
        "confidence": _CONFIDENCE,
        "date_range": {"type": ["string", "null"]},
    },
    ["relationship", "target_id", "target_type", "target_name", "confidence"],
)

_ENTITY = _obj(
    {
        "id": {"type": "string"},
        "match_id": {"type": "string"},          # omit entirely for new entities
        "name": {"type": "string"},
        "type": {"type": "string"},
        "aliases": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "analysis": {"type": "string"},          # omit if nothing notable
        "contradictions": {"type": "array", "items": {"type": "string"}},
        "timeline_events": {"type": "array", "items": _TIMELINE_EVENT},
        "roles": {"type": "array", "items": _ROLE},
    },
    ["id", "name", "type"],
)

_DOCUMENT = _obj(
    {
        "sha256": {"type": "string"},
        "filename": {"type": "string"},
        "original_path": _NULLABLE_STR,
        "title": {"type": "string"},
        "document_type": {"type": "string"},
        "date_of_document": _NULLABLE_STR,
        "page_count": {"type": ["integer", "null"]},
        "source": _NULLABLE_STR,
        "obtained": _NULLABLE_STR,
        "near_duplicate_of": _NULLABLE_STR,
        "summary": {"type": "string"},
        "key_facts": {"type": "array", "items": _KEY_FACT},
    },
    ["sha256", "filename", "title", "document_type", "summary", "key_facts"],
)

# Full single-document extraction (simple path, and the merged result of a sectioned doc).
EXTRACTION = _obj(
    {
        "document": _DOCUMENT,
        "entities": {"type": "array", "items": _ENTITY},
        "morgue_entity_id": {"type": "string"},
        "morgue_document_type": {"type": "string"},
        "scratchpad": {"type": "string"},   # curated briefing notes (Step 9 of the old skill)
    },
    ["document", "entities", "morgue_entity_id", "morgue_document_type", "scratchpad"],
)

# One page-range section's partial contribution. Looser than EXTRACTION: only section 1
# fills document metadata + morgue fields; merge.run assembles the whole.
SECTION = _obj(
    {
        "document": {
            "type": "object",
            "properties": _DOCUMENT["properties"],
            "additionalProperties": False,
        },
        "entities": {"type": "array", "items": _ENTITY},
        "morgue_entity_id": {"type": "string"},
        "morgue_document_type": {"type": "string"},
        "observations": {"type": "string"},   # appended to the carry-forward scratchpad
    },
    ["entities"],
)

# Classify a document to a domain-skill filename (records/<name>.md).
CLASSIFY = _obj(
    {"skill": {"type": "string"}, "document_type": {"type": "string"}},
    ["skill"],
)

# Entity synthesis: prose for the multi-mention entities in the bundle.
SYNTHESIS = _obj(
    {
        "entity_syntheses": {
            "type": "array",
            "items": _obj(
                {"entity_id": {"type": "string"}, "summary": {"type": "string"},
                 "analysis": {"type": "string"}},
                ["entity_id", "summary"],
            ),
        }
    },
    ["entity_syntheses"],
)

# Post-ingest briefing prose (Python writes the files from this).
BRIEFING = _obj(
    {
        "investigation_status": {"type": "string"},
        "what_was_ingested": {"type": "array", "items": {"type": "string"}},
        "new_entities": {"type": "array", "items": {"type": "string"}},
        "connections": {"type": "array", "items": {"type": "string"}},
        "leads": {"type": "array", "items": {"type": "string"}},
        "anomalies": {"type": "array", "items": {"type": "string"}},
        "emerging_patterns": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    ["investigation_status", "what_was_ingested"],
)

# Semantic dedup of one date's colliding timeline events. The model returns the kept
# full event objects (preserving page/confidence/source_sha256), minus duplicates.
TIMELINE_DEDUP = _obj(
    {
        "events": {
            "type": "array",
            "items": _obj(
                {
                    "date": {"type": "string"},
                    "event": {"type": "string"},
                    "page": {"type": ["integer", "null"]},
                    "confidence": {"type": "string"},
                    "source_sha256": {"type": "string"},
                },
                ["date", "event"],
            ),
        }
    },
    ["events"],
)
