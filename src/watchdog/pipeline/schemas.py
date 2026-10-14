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


# The fact primitive (D26). Each material fact is emitted once and rendered deterministically into
# the document note, the entity notes (`entities`) and the timeline (`date`, set only when the fact
# is a datable occurrence). `quote_locator` is the opening words of a source sentence, which
# post-flight expands into the full `quote` from the page text (D170).
_KEY_FACT = _obj(
    {
        "fact": {"type": "string"},
        "page": {"type": ["integer", "null"]},
        "basis": _BASIS,
        # Nullable, not bare "string": a gpt-nano sectioned extraction nulled `date` on 21 of 26
        # key_facts in one live document rather than omitting it — the same weak-json_object-mode
        # null-vs-omit gap fixed elsewhere in this schema (#490). Every reader already treats null
        # and absent the same (postflight._sanitize_dates's `if date and ...`, explode_key_facts's
        # `(fact.get("date") or "").strip()`, merge.py's `f.get("date")` truthiness check).
        "date": _NULLABLE_STR,                                        # set ⇒ also a timeline event
        "entities": {"type": "array", "items": {"type": "string"}},   # entity ids the fact is about
        "quote_locator": {"type": "string"},   # first ~6-12 words of the source sentence (only when
                                                # wording matters); resolved to a full quote in Python
    },
    ["fact"],   # basis omitted ⇒ stated (the overwhelming default)
)

# A concrete document a reporter could go and get — distinct from a "lead" (an open-ended
# thread to investigate): a known-to-exist artifact with a type, a reason, and often a venue.
# This content is *moved* out of `scratchpad` (#365), not duplicated — extract_instructions.md
# tells the model not to also describe documents-to-request there.
_DOCUMENT_REQUEST = _obj(
    {
        "type": {"type": "string",
                  "description": "the kind of document, e.g. hearing transcript, enabling "
                                  "regulation, criminal complaint"},
        "what": {"type": "string",
                 "description": "the specific artifact, identified precisely enough to ask for it"},
        "why_it_matters": {"type": "string",
                            "description": "what obtaining it would establish"},
        "likely_source": {"type": "string",
                           "description": "where it can plausibly be obtained — registry, court, "
                                           "regulator, FOI office, published source"},
    },
    ["type", "what", "why_it_matters"],
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

# The graph layer (D26): entity identity and relationships. What a document says about an entity
# lives in `key_facts`, tagged by entity id. Extraction names only the entities this document
# mentions (D118): no vault matching and no contradictions, which need the whole batch.
_ENTITY = _obj(
    {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "type": {"type": "string",
                 "description": ("exactly one of the fixed entity classes: person, "
                                 "organization, public-body, place, asset, proceeding")},
        "aliases": {"type": "array", "items": {"type": "string"}},
        "roles": {"type": "array", "items": _ROLE},
    },
    ["id", "name", "type"],
)

# Fields `orchestrate._stamp_document` sets after the call (sha256, filename, page count,
# provenance, file metadata, morgue document type) are not in the schemas: the model never fills
# them, validation runs only on the raw response, and Claude's strict structured-output mode caps
# the number of optional properties — a dense document once exceeded it.
_DOCUMENT = _obj(
    {
        "title": {"type": "string"},
        "document_type": {"type": "string"},
        "date_of_document": _NULLABLE_STR,
        "summary": {"type": "string"},
        # Visible scratch space, read by nothing downstream (#570). Ordered ahead of key_facts
        # because structured-output decoding fills an object's properties in schema-declaration
        # order — a model with no private reasoning channel (extract_instructions.md's scaffold
        # section only addresses this field to that model) gets to work through plan, evidence
        # triage, and a consistency pass here BEFORE it has to commit to key_facts. A model that
        # already has a private channel (thinking) has no reason to use it and normally won't;
        # optional, so leaving it empty costs nothing.
        "plan": {"type": "string"},
        "key_facts": {"type": "array", "items": _KEY_FACT},
    },
    ["title", "document_type", "summary", "key_facts"],
)

# Full single-document extraction (simple path, and the merged result of a sectioned doc).
# `entities` is declared BEFORE `document` (#651, following D208's own assumption that
# structured-output decoding fills an object's properties in schema-declaration order): the
# roster of entity ids `document.key_facts[].entities` tags against should exist before the
# model has to write those tags, not after (D211 found the opposite order was a real source of
# dangling tags — this addresses the root cause that fix only made visible).
EXTRACTION = _obj(
    {
        "entities": {"type": "array", "items": _ENTITY},
        "document": _DOCUMENT,
        "morgue_entity_id": {"type": "string"},
        "scratchpad": {"type": "string"},   # curated briefing notes (Step 9 of the old skill)
        # Concrete documents this document refers to that a reporter could go and get (#365) —
        # moved out of scratchpad, not duplicated. Optional: omit entirely when none apply.
        "document_requests": {"type": "array", "items": _DOCUMENT_REQUEST},
    },
    ["document", "entities", "morgue_entity_id", "scratchpad"],
)

# title/document_type/summary widened to nullable for sections only (not EXTRACTION, where
# they're required and always meant to carry real content): once #496 made document.key_facts
# required on every section, gpt-nano started explicitly nulling these three — genuinely
# section-1-only fields it has nothing to say on past section 1 — on later sections instead of
# omitting them, the same "OpenAI's json_object mode gives no wire-level shape enforcement" gap
# that motivated widening morgue_entity_id/observations below. Reproduced
# directly: a SECTION document with these three set to None hard-failed with the exact three
# "None is not of type 'string'" errors seen on a live gpt-nano ingest.
_SECTION_DOCUMENT_PROPS = {
    **_DOCUMENT["properties"],
    "title": _NULLABLE_STR,
    "document_type": _NULLABLE_STR,
    "summary": _NULLABLE_STR,
}

# One page-range section's partial contribution; merge.merge_extractions assembles the whole. Only
# section 1 supplies document metadata and the morgue field. `document` and `document.key_facts`
# are required on every section, so a model can't silently omit a section's facts. `entities` is
# not required: a section may genuinely name no new entities, and the merge reads it with a
# default.
SECTION = _obj(
    {
        # `entities` before `document`, mirroring EXTRACTION (#651) — same decode-order
        # reasoning, so a section's key_facts tag ids the section has already named.
        "entities": {"type": "array", "items": _ENTITY},
        "document": _obj(_SECTION_DOCUMENT_PROPS, ["key_facts"]),
        # Nullable (not bare "string"): OpenAI's json_object mode gives no wire-level shape
        # enforcement, so a model that means "nothing for this section" sometimes emits an
        # explicit `null` here instead of omitting the key — bare "string" made that a hard
        # schema-validation failure for the whole section. Every downstream reader already
        # treats null and absent identically (merge.py's `sec.get(...) or ...` folding across
        # sections, postflight's emptiness checks on the merged document, orchestrate.py's
        # `.get(...) or ""` reads) — so widening costs nothing and stops a validator rejection
        # that was never actually protecting a real invariant.
        "morgue_entity_id": _NULLABLE_STR,
        "observations": _NULLABLE_STR,   # appended to the carry-forward scratchpad
        # Same field as EXTRACTION's, moved out of `observations` (#365) — optional, omit when
        # this section names nothing obtainable. merge.merge_extractions unions across sections.
        "document_requests": {"type": "array", "items": _DOCUMENT_REQUEST},
    },
    ["document"],
)

# The verification pass (#535): a second, cheap read of the *same* document text asking only
# "what material fact is on the page and absent from this fact list". `missing_facts` reuses
# `_KEY_FACT` unchanged, so a candidate that survives `verify.merge_candidates` is shape-identical
# to a fact the extractor emitted and every downstream reader sees no new shape. Nothing else is
# asked for — no entities, no document metadata, no summary: the extractor already produced those,
# and the whole point of running this on a cheap model at low effort is that its output stays
# short (D172).
VERIFY = _obj({"missing_facts": {"type": "array", "items": _KEY_FACT}}, ["missing_facts"])


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
                {"entity_id": {"type": "string",
                               "description": "the internal id of the entity being synthesized, "
                                               "copied verbatim from the bundle"},
                 "summary": {"type": "string",
                             "description": "a rewritten, up-to-date summary of the entity across "
                                             "all its mentions"},
                 "analysis": {"type": "string",
                              "description": "optional analytical notes on patterns across the "
                                              "entity's mentions (contradictions, escalating roles, "
                                              "recurring counterparties)"}},
                ["entity_id", "summary"],
            ),
        }
    },
    ["entity_syntheses"],
)

# Reconciliation (D118): the two jobs that need the whole entity set.
#
#   `merges`         — entity resolution over Python-blocked candidate pairs; the model confirms or
#                      rejects each by pair `index`, so it can't invent an id.
#   `contradictions` — structured fields that are exactly `contradiction.run`'s arguments, so the
#                      callout is written by the deterministic writer (D81) and a bad document
#                      reference fails validation instead of landing in a note.
RECONCILE = _obj(
    {
        "merges": {
            "type": "array",
            "items": _obj(
                {"pair": {"type": "integer",
                          "description": "index into the CANDIDATE PAIRS list"},
                 "keep_id": {"type": "string",
                             "description": "which of the pair's two ids survives — copied "
                                            "verbatim; the other is folded into it"},
                 "reason": {"type": "string",
                            "description": "one clause on why these are the same real-world thing"}},
                ["pair", "keep_id", "reason"],
            ),
        },
        "contradictions": {
            "type": "array",
            "items": _obj(
                {"entity_id": {"type": "string",
                               "description": "the entity the conflict is about, copied verbatim "
                                              "from the bundle"},
                 "label": {"type": "string",
                           "description": "a short label for the conflict, e.g. 'Insolvency date'"},
                 "a_value": {"type": "string",
                             "description": "the first claim's conflicting value, stated briefly"},
                 "a_doc": {"type": "string",
                           "description": "the slug of the document the first claim comes from — "
                                          "the `<slug>` in the [[documents/<slug>|…]] link the "
                                          "claim is filed under in the bundle"},
                 "a_page": {"type": ["integer", "null"]},
                 "b_value": {"type": "string",
                             "description": "the second claim's conflicting value"},
                 "b_doc": {"type": "string",
                           "description": "the slug of the document the second claim comes from"},
                 "b_page": {"type": ["integer", "null"]},
                 "a_fact": {"type": ["string", "null"],
                            "description": "the short id of the first claim's fact, e.g. "
                                           "'f:3a9c', copied from the bundle; null if none"},
                 "b_fact": {"type": ["string", "null"],
                            "description": "the short id of the second claim's fact"}},
                ["entity_id", "label", "a_value", "a_doc", "b_value", "b_doc"],
            ),
        },
    },
    ["merges", "contradictions"],
)

# Post-ingest briefing prose (Python writes the files from this).
BRIEFING = _obj(
    {
        "investigation_status": {"type": "string",
                                  "description": "one sentence summarizing where the investigation "
                                                  "stands after this batch"},
        "what_was_ingested": {"type": "array", "items": {"type": "string"},
                               "description": "one line per file describing what it is and its "
                                               "document type"},
        "new_entities": {"type": "array", "items": {"type": "string"},
                          "description": "human-readable display names of entities first seen in "
                                          "this batch — never internal ids/slugs"},
        "connections": {"type": "array", "items": {"type": "string"},
                         "description": "connections this batch draws to existing vault entities, "
                                         "by display name, with what the connection is and why it "
                                         "matters"},
        "leads": {"type": "array", "items": {"type": "string"},
                  "description": "actionable follow-up ideas: open questions, contacts, missing "
                                  "documents, FOI ideas"},
        "anomalies": {"type": "array", "items": {"type": "string"},
                      "description": "things worth a closer look: shared addresses, unexpected "
                                      "roles, disproportionate transactions, highly-connected "
                                      "entities with no documented relationships"},
        "emerging_patterns": {"type": "array", "items": {"type": "string"},
                               "description": "patterns emerging across documents in this batch or "
                                               "against the existing vault"},
        "open_questions": {"type": "array", "items": {"type": "string"},
                            "description": "unresolved questions the investigation should pursue "
                                            "next"},
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

# Document-request dedup (#416): exact-string matching at record time only converges identical
# wording, so paraphrased citations of the same real document stay as separate open requests.
# Same shape and code/model split as TIMELINE_DEDUP — the model groups by sameness, Python
# performs the merge — so it is the same schema.
REQUEST_DEDUP = TIMELINE_DEDUP

# Relationship labels (D291): for each numbered pair of entities, the groups of its numbered
# wordings that name the same relationship, and which member wording is the canonical label.
# Pairs and wordings are referred to by number only; code checks every group and records it
# (`relationships.apply`). A pair with nothing to group is left out.
RELATIONSHIP_LABELS = _obj(
    {"pairs": {"type": "array", "items": _obj(
        {"pair": {"type": "integer"},
         "groups": {"type": "array", "items": _obj(
             {"labels": {"type": "array", "items": {"type": "integer"}},
              "canonical": {"type": "integer"},
              "reason": {"type": "string"}},
             ["labels", "canonical", "reason"],
         )}},
        ["pair", "groups"],
    )}},
    ["pairs"],
)
