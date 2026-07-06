"""JSON schemas for the model reasoning tasks the Python orchestrator runs (#118).

The model is called only for reasoning; these schemas are the contract for what it
must return. EXTRACTION mirrors what ``postflight._validate`` requires (plus the richer
fields write_vault consumes) — keep the two in sync.
"""

# Provenance of a fact/role: `stated` = directly in the document, `inferred` = the model reasoned
# to it (a lead to verify, not a finding). Omit-default: absent ⇒ `stated`, so the model emits
# `basis` only for the rare `inferred` exception (#143; supersedes the old 4-level `confidence`).
# A fact that *conflicts* with the vault is not a basis level — it is captured by `[!contradiction]`.
_BASIS = {"type": "string", "enum": ["stated", "inferred"]}
_NULLABLE_STR = {"type": ["string", "null"]}


def _obj(properties: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


# The unified fact primitive (#140). Each material fact is emitted once and rendered into
# multiple views deterministically: the document note's key-facts list, plus — via its optional
# dimensions — the entity notes (`entities`: which entities the fact is about) and the timeline
# (`date`: the date of the occurrence, set only when the fact IS a datable event). This replaces
# the former separate per-entity `evidence_fragments` and `timeline_events`, which postflight now
# reconstructs from these tags. `quote` is an optional verbatim source sentence (only when wording
# matters).
_KEY_FACT = _obj(
    {
        "fact": {"type": "string"},
        "page": {"type": ["integer", "null"]},
        "basis": _BASIS,
        "date": {"type": "string"},                                   # set ⇒ also a timeline event
        "entities": {"type": "array", "items": {"type": "string"}},   # entity ids the fact is about
        "quote": {"type": "string"},   # optional verbatim source sentence (only when wording matters)
    },
    ["fact"],   # basis omitted ⇒ stated (the overwhelming default)
)

_ROLE = _obj(
    {
        "relationship": {"type": "string"},
        "target_id": {"type": "string"},   # target_name + target_type are derivable from this id
        "page": {"type": ["integer", "null"]},
        "basis": _BASIS,
        "date_range": {"type": ["string", "null"]},
    },
    ["relationship", "target_id"],
)

# The graph layer (#140): entity identity + relationships + contradictions. What a document
# *says* about an entity (claims, dated events) is no longer carried here — it lives in the
# document's `key_facts`, tagged by entity id, and postflight reconstructs the per-entity views.
_ENTITY = _obj(
    {
        "id": {"type": "string"},
        "match_id": {"type": "string"},          # omit entirely for new entities
        "name": {"type": "string"},
        "type": {"type": "string"},
        "aliases": {"type": "array", "items": {"type": "string"}},
        "contradictions": {"type": "array", "items": {"type": "string"}},
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
        "summary": {"type": "string"},
        "key_facts": {"type": "array", "items": _KEY_FACT},
    },
    # sha256/filename/original_path/page_count and source/obtained are stamped by Python
    # (orchestrate._stamp_document) — deterministic values the pipeline already holds, not
    # echoed by the model. They stay in `properties` (optional) so the stamped dict validates.
    ["title", "document_type", "summary", "key_facts"],
)

# Full single-document extraction (simple path, and the merged result of a sectioned doc).
EXTRACTION = _obj(
    {
        "document": _DOCUMENT,
        "entities": {"type": "array", "items": _ENTITY},
        "morgue_entity_id": {"type": "string"},
        # morgue_document_type is derived in Python as slugify(document.document_type)
        # (orchestrate._stamp_document) — kept optional here so the stamped dict validates.
        "morgue_document_type": {"type": "string"},
        "scratchpad": {"type": "string"},   # curated briefing notes (Step 9 of the old skill)
    },
    ["document", "entities", "morgue_entity_id", "scratchpad"],
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
    {"skill": {"type": "string"}},
    ["skill"],
)

# Whole-document digest for sectioned extraction (#279): no single section call ever sees the
# whole document, so the digest is composed once from the merged key_facts.
DIGEST = _obj({"summary": {"type": "string"}}, ["summary"])


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

# Semantic dedup of one date's colliding timeline events. The model returns `groups` — one
# cluster per surviving event, each `{keep, duplicates}` naming the index to keep and the
# indices of the pure restatements that fold into it. Python re-selects from the original
# objects (which already carry page/basis/source_sha256) and unions each group's entity tags
# onto the survivor (#237), rather than echoing full events back.
TIMELINE_DEDUP = _obj(
    {"groups": {"type": "array", "items": _obj(
        {"keep": {"type": "integer"},
         "duplicates": {"type": "array", "items": {"type": "integer"}}},
        ["keep", "duplicates"],
    )}},
    ["groups"],
)

# Cross-precision timeline reconciliation for one month (#239). The model matches each
# month-precision (YYYY-MM) event to the day-precision (YYYY-MM-DD) event it restates, if any:
# `matches` is `{coarse, precise}` index pairs. Python drops the matched coarse event and unions
# its entity tags onto the precise survivor; unmatched coarse events are left untouched. Only
# coarse→precise matches are expressible, so a precise event can never be dropped and two precise
# events can never collapse into each other.
TIMELINE_PRECISION_MATCH = _obj(
    {"matches": {"type": "array", "items": _obj(
        {"coarse": {"type": "integer"}, "precise": {"type": "integer"}},
        ["coarse", "precise"],
    )}},
    ["matches"],
)
