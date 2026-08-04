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
# reconstructs from these tags. `quote_locator` is the first several words of the source sentence,
# which Python resolves against the page text into a full `quote` at post-flight (#529,
# `quote_verify.resolve_quotes`) — the model is never asked to retype the sentence itself.
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

# The graph layer (#140): entity identity + relationships. What a document *says* about an
# entity (claims, dated events) is not carried here — it lives in the document's `key_facts`,
# tagged by entity id, and postflight reconstructs the per-entity views.
#
# Extraction is a pure function of the document (#381/D118): it names the entities *this*
# document mentions and nothing else. It carries no `match_id` (entity resolution against the
# vault is the finalizer's job — see RECONCILE) and no `contradictions` (a conflict needs two
# claims side by side, which no single extraction call can see).
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

# sha256/filename/original_path/page_count, source/obtained, file_metadata, and (on EXTRACTION,
# below) morgue_document_type used to sit in these schemas too, as optional properties — the
# model never fills any of them (orchestrate._stamp_document sets every one unconditionally,
# after the model call returns, straight from pf/sha/document_type — never reading a pre-existing
# value first), so they were pure dead weight from the model's perspective. They were kept
# "so the stamped dict validates" against this same schema, but nothing actually re-validates
# the stamped dict afterward — jsonschema validation only ever runs on the model's raw response,
# before stamping (model_client.acomplete_json / batch_extract.collect). Claude's strict
# structured-output mode counts every optional property toward a hard complexity limit
# ("Schemas contains too many optional parameters (28)... limit: 24") and nullable-typed ones
# disproportionately so — corpus-v1's dense 70-page document hit exactly this, 400ing every
# claude-api extraction outright. Dropping these seven dead properties is a genuine schema
# correction, not just a workaround: they were never part of the model's actual contract.
_DOCUMENT = _obj(
    {
        "title": {"type": "string"},
        "document_type": {"type": "string"},
        "date_of_document": _NULLABLE_STR,
        "summary": {"type": "string"},
        "key_facts": {"type": "array", "items": _KEY_FACT},
    },
    ["title", "document_type", "summary", "key_facts"],
)

# Full single-document extraction (simple path, and the merged result of a sectioned doc).
EXTRACTION = _obj(
    {
        "document": _DOCUMENT,
        "entities": {"type": "array", "items": _ENTITY},
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
# that motivated widening morgue_entity_id/morgue_document_type/observations below. Reproduced
# directly: a SECTION document with these three set to None hard-failed with the exact three
# "None is not of type 'string'" errors seen on a live gpt-nano ingest.
_SECTION_DOCUMENT_PROPS = {
    **_DOCUMENT["properties"],
    "title": _NULLABLE_STR,
    "document_type": _NULLABLE_STR,
    "summary": _NULLABLE_STR,
}

# One page-range section's partial contribution. Looser than EXTRACTION: only section 1
# fills document metadata + morgue fields; merge.run assembles the whole. `document.key_facts`
# stays required on every section regardless (#496) — title/document_type/summary are the only
# fields genuinely section-1-only. Before this, `document` carried no `required` at all, so a
# model under low reasoning effort could (and, on gemini-flash, reliably did) omit key_facts
# entirely — schema-valid, silent, and invisible to postflight since every section still
# reported entities normally. `document` itself is now required at the top level too, so a
# later section can't skip the object altogether to dodge the inner requirement.
#
# `entities` is deliberately NOT required, unlike `document`/`key_facts` above (#490 follow-up):
# it has been required since the very first Python-orchestrator commit, not from a documented
# silent-omission incident the way key_facts was (#496) — and on gpt-nano it hard-failed a whole
# document with `"'entities' is a required property"` when a section genuinely named no new
# entities. merge.merge_extractions already reads it as `sec.get("entities", [])`, so an omitted
# key was always handled safely downstream; only the schema was stricter than the code needed.
SECTION = _obj(
    {
        "document": _obj(_SECTION_DOCUMENT_PROPS, ["key_facts"]),
        "entities": {"type": "array", "items": _ENTITY},
        # Nullable (not bare "string"): OpenAI's json_object mode gives no wire-level shape
        # enforcement, so a model that means "nothing for this section" sometimes emits an
        # explicit `null` here instead of omitting the key — bare "string" made that a hard
        # schema-validation failure for the whole section. Every downstream reader already
        # treats null and absent identically (merge.py's `sec.get(...) or ...` folding across
        # sections, postflight's emptiness checks on the merged document, orchestrate.py's
        # `.get(...) or ""` reads) — so widening costs nothing and stops a validator rejection
        # that was never actually protecting a real invariant.
        "morgue_entity_id": _NULLABLE_STR,
        "morgue_document_type": _NULLABLE_STR,
        "observations": _NULLABLE_STR,   # appended to the carry-forward scratchpad
        # Same field as EXTRACTION's, moved out of `observations` (#365) — optional, omit when
        # this section names nothing obtainable. merge.merge_extractions unions across sections.
        "document_requests": {"type": "array", "items": _DOCUMENT_REQUEST},
    },
    ["document"],
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

# Post-ingest reconciliation (#381/D118) — the two jobs extraction is structurally unable to do,
# because both need a view extraction never has: the whole entity set, after every document has
# landed.
#
#   `merges`         — entity resolution. Python has already blocked the field down to plausible
#                      candidate PAIRS (reconcile.candidate_pairs — same canonical type, token
#                      subset or Jaccard overlap); the model only confirms or rejects each. It
#                      answers by pair `index`, so it never re-types an id and cannot invent one.
#   `contradictions` — two conflicting claims about one entity, each grounded in a value, a
#                      source document, and a page. Deliberately structured, not model-authored
#                      markdown: these fields are exactly `contradiction.run`'s arguments, so the
#                      callout is rendered and filed by the same deterministic writer the manual
#                      `watchdog contradiction` command uses (D81's escape hatch), and a bad
#                      document reference fails validation instead of landing in a note.
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
                 "b_page": {"type": ["integer", "null"]}},
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
# performs the merge.
REQUEST_DEDUP = _obj(
    {"groups": {"type": "array", "items": _obj(
        {"keep": {"type": "integer"},
         "duplicates": {"type": "array", "items": {"type": "integer"}}},
        ["keep", "duplicates"],
    )}},
    ["groups"],
)
