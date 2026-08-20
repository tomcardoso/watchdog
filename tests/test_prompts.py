"""Prompt templates load from watchdog/prompts/*.md and stay out of the skills catalog."""

import pytest

from watchdog import model_client, skills_catalog
from watchdog.pipeline import prompts

_flat = model_client._flatten_prompt   # extract/section prompts are content-block lists (A1)

_TEMPLATES = ["classify", "extract_instructions", "extract_scaffold", "section_intro",
              "synthesis", "briefing", "timeline_dedup", "timeline_precision", "digest",
              "candidates_intro"]


@pytest.mark.parametrize("name", _TEMPLATES)
def test_template_loads_nonempty(name):
    assert prompts._text(name).strip()


def test_render_substitutes_tokens():
    out = prompts._render("timeline_dedup", date="2021-02-01")
    assert "2021-02-01" in out
    assert "{{date}}" not in out


def test_timeline_dedup_prompt_enumerates_events_not_full_objects():
    events = [
        {"event": "Court declared insolvency", "page": 2, "source_sha256": "sha-xyz"},
        {"event": "Insolvency declared under CCAA", "page": 9, "source_sha256": "sha-abc"},
    ]
    p = prompts.build_timeline_dedup_prompt("2021-02-01", events)
    assert "[0] Court declared insolvency  (p.2)" in p
    assert "[1] Insolvency declared under CCAA  (p.9)" in p
    assert "keep" in p
    # the 64-char hashes are not echoed into the prompt — that's the whole point
    assert "sha-xyz" not in p and "sha-abc" not in p


def test_timeline_precision_prompt_separates_and_labels_precisions():
    coarse = [{"event": "Acme filed in March", "page": 3}]
    precise = [{"date": "2020-03-15", "event": "Acme filed on the 15th", "page": 9}]
    p = prompts.build_timeline_precision_prompt("2020-03", coarse, precise)
    assert "2020-03" in p
    assert "[0] Acme filed in March  (p.3)" in p                     # coarse: no date shown
    assert "[0] (2020-03-15) Acme filed on the 15th  (p.9)" in p     # precise: day shown
    assert "coarse" in p and "precise" in p                          # instructs the pairing shape


def test_render_leaves_single_braces_untouched():
    # A template may contain literal { } (e.g. JSON examples); only {{key}} is special.
    prompts._text.cache_clear()
    assert prompts._render("classify") == prompts._text("classify")


def test_section_prompt_renders_label():
    p = prompts.build_section_prompt(
        pages_text="x", skill_text="", carry_forward="",
        section_label="pp.1-10", is_first=True, known_document_types=[])
    assert "pp.1-10" in _flat(p) and "{{" not in _flat(p)


def test_classify_prompt_includes_sidecar_only_when_present():
    without = prompts.build_classify_prompt("doc excerpt", "index line")
    assert "excerpt" in without and "Provenance sidecar" not in without

    with_sc = prompts.build_classify_prompt("doc excerpt", "index line",
                                            "source: https://example.gov\nnotes: hint")
    assert "Provenance sidecar" in with_sc
    assert "hint" in with_sc and "example.gov" in with_sc


def test_later_section_prompt_does_not_ask_for_summary():
    # No section ever emits document.summary any more (#279): the whole-document digest is
    # composed after merge — inline for a non-sectioned doc, via one post-merge model call for
    # a sectioned one. The shared instructions block still documents the field generically
    # (also used by the whole-doc path); what must be gone from the later-section note is any
    # reference to "only section 1's summary is kept" (the pre-#279 contract).
    p = prompts.build_section_prompt(
        pages_text="x", skill_text="", carry_forward="",
        section_label="pp.11-20", is_first=False, known_document_types=[])
    text = _flat(p)
    assert "only section 1's summary is kept" not in text
    assert "composed after the merge" in text
    assert "LATER section" in text


def test_first_section_prompt_still_fills_metadata():
    p = prompts.build_section_prompt(
        pages_text="x", skill_text="", carry_forward="",
        section_label="pp.1-10", is_first=True, known_document_types=[])
    text = _flat(p)
    assert "morgue_entity_id" in text
    # Section 1 no longer emits document.summary either — that's now composed post-merge (#279).
    assert "Omit document.summary" in text
    assert "composed after all sections are merged" in text


def test_build_digest_prompt_renders_title_type_pages_and_facts():
    facts = [{"fact": "Filed in 2024", "date": "2024-01-15"}, {"fact": "Revenue grew"}]
    p = prompts.build_digest_prompt(filename="acme-ar.pdf", title="Acme AR",
                                    document_type="Annual Report", page_count=42,
                                    skill_text="THE DOMAIN SKILL", brief="CHASE THE FRAUD",
                                    sidecar="SIDECAR NOTES", key_facts=facts)
    text = _flat(p)
    assert "acme-ar.pdf" in text
    assert "Acme AR" in text
    assert "Annual Report" in text
    assert "42" in text
    assert "THE DOMAIN SKILL" in text     # extractor-tier context parity (#279)
    assert "CHASE THE FRAUD" in text
    assert "SIDECAR NOTES" in text
    assert "Filed in 2024" in text and "Revenue grew" in text


def test_build_digest_prompt_falls_back_when_fields_missing():
    p = prompts.build_digest_prompt(filename="", title="", document_type="", page_count=None,
                                    skill_text=None, brief=None, sidecar=None, key_facts=[])
    text = _flat(p)
    assert "(untitled)" in text
    assert "(unknown)" in text
    assert "(none)" in text


def test_build_digest_prompt_caches_skill_ahead_of_volatile_data():
    # #393: DOMAIN_SKILL and the brief must lead (and carry the cache breakpoint), with
    # per-document identity/sidecar/facts strictly after — mirrors build_extract_prompt/
    # build_section_prompt's block order so prefix caching (explicit or automatic) can hit.
    p = prompts.build_digest_prompt(filename="acme-ar.pdf", title="Acme AR",
                                    document_type="Annual Report", page_count=42,
                                    skill_text="THE DOMAIN SKILL", brief="CHASE THE FRAUD",
                                    sidecar="SIDECAR NOTES", key_facts=[])
    assert len(p) == 3
    assert "cache_control" in p[1]
    assert "THE DOMAIN SKILL" in p[1]["text"]
    text = _flat(p)
    assert text.index("CHASE THE FRAUD") < text.index("THE DOMAIN SKILL")
    assert text.index("THE DOMAIN SKILL") < text.index("acme-ar.pdf")


def test_build_digest_prompt_cache_prefix_is_stable_across_documents():
    """Mirrors test_extract_prompt_cache_prefix_is_stable_across_volatile_data and
    test_section_prompt_cache_prefix_is_stable_across_sections (issue #586): the cacheable
    prefix (instructions+brief, then skill) must be byte-identical for two different documents
    that share a skill, with everything document-specific (filename/title/type/page_count/
    sidecar/key_facts) confined to the volatile block after the breakpoint."""
    kwargs = dict(skill_text="SKILL TEXT", brief="Investigate the fraud")
    p1 = prompts.build_digest_prompt(filename="doc-a.pdf", title="Doc A",
                                     document_type="Annual Report", page_count=12,
                                     sidecar="sidecar A", key_facts=[{"fact": "Fact about A"}],
                                     **kwargs)
    p2 = prompts.build_digest_prompt(filename="doc-b.pdf", title="Doc B — a different filing",
                                     document_type="Affidavit", page_count=99,
                                     sidecar="a longer, unrelated sidecar for B",
                                     key_facts=[{"fact": "A completely different fact about B"}],
                                     **kwargs)
    assert [b["text"] for b in p1[:2]] == [b["text"] for b in p2[:2]]
    assert p1[2]["text"] != p2[2]["text"]         # volatile block does differ
    assert model_client._prompt_cache_key(p1) == model_client._prompt_cache_key(p2)


def test_extract_prompt_includes_instructions_and_data():
    p = prompts.build_extract_prompt(
        pages_text="DOCBODY", skill_text="SKILL", sidecar=None, brief=None, known_document_types=[])
    text = _flat(p)
    assert "key_facts" in text              # instruction prose loaded
    assert "DOCBODY" in text and "SKILL" in text   # data assembled in
    # identity fields are no longer asked of the model (stamped in Python)
    assert "Set document.sha256" not in text


def test_extract_prompt_lists_known_document_types():
    p = prompts.build_extract_prompt(
        pages_text="x", skill_text="", sidecar=None,
        brief=None, known_document_types=["Annual Report", "Affidavit"])
    text = _flat(p)
    assert "KNOWN_DOCUMENT_TYPES" in text
    assert "- Annual Report" in text and "- Affidavit" in text
    assert "reuse one verbatim" in text


def test_extract_prompt_handles_no_known_types():
    p = prompts.build_extract_prompt(
        pages_text="x", skill_text="", sidecar=None,
        brief=None, known_document_types=[])
    assert "none yet" in _flat(p)


def test_extract_prompt_forbids_silent_date_correction():
    """#534: the extractor was silently "correcting" an implausible printed date (an affidavit
    dated a year before the report citing it) to the year it judged intended, destroying the
    evidence that the source itself carried the error. The prompt must instruct transcription
    as printed, plus flagging the inconsistency, rather than resolving it."""
    p = prompts.build_extract_prompt(
        pages_text="x", skill_text="", sidecar=None, brief=None, known_document_types=[])
    text = _flat(p)
    assert "TRANSCRIBE, DON'T CORRECT" in text
    assert "never quietly swap in the date you infer was intended" in text


def test_extract_prompt_warns_about_conversion_artifacts():
    """#631: Docling can merge table rows during conversion (e.g. two payroll line items
    fused into one, scattering their figures), so the extractor needs to know its input is
    an automated conversion that can lose table structure — and to decline a label/figure
    pairing it can't plausibly stand behind, rather than the general TRANSCRIBE, DON'T
    CORRECT rule alone, which governs source *content*, not conversion-introduced structure."""
    p = prompts.build_extract_prompt(
        pages_text="x", skill_text="", sidecar=None, brief=None, known_document_types=[])
    text = _flat(p)
    assert "CONVERSION ARTIFACTS" in text
    assert "rows can merge" in text


def test_extract_prompt_carries_no_vault_state():
    """Extraction is stateless (#381/D118): no EXISTING_ENTITIES / EXISTING_TIMELINE block in
    any content block — entity resolution and the contradiction check moved to the finalizer."""
    p = prompts.build_extract_prompt(
        pages_text="x", skill_text="", sidecar=None,
        brief=None, known_document_types=[])
    text = _flat(p)
    assert "EXISTING_ENTITIES" not in text
    assert "EXISTING_TIMELINE" not in text


# ── explicit scaffold for non-reasoning models (#570 Phase 1) ─────────────────────────────

@pytest.mark.parametrize("model", [
    "haiku",                # Claude, no thinking control at all
    "deepseek-v4-flash",    # DeepSeek's plain (non "-thinking") id
    "gemini-3.7-flash",     # Gemini — no reasoning field in the catalog
    "not-a-real-model",     # uncatalogued — conservative default is "no channel"
])
def test_extract_prompt_adds_scaffold_for_a_non_reasoning_model(model):
    """The branch is resolved from the catalog (`catalog_has_reasoning`), not hardcoded to
    Haiku — every model family the catalog doesn't confirm a reasoning channel for gets the
    scaffold, not just the one this was built and benchmarked against (#570 Phase 1). `model`
    here is always the bare catalog id, never a `provider:model` CLI form — `cmd/ingest.py`'s
    `_resolve_stage` strips that prefix into a separate `backend` before `model` ever reaches
    this layer, so a bare id is what `orchestrate.py` actually threads through in production."""
    p = prompts.build_extract_prompt(
        pages_text="x", skill_text="", sidecar=None, brief=None, known_document_types=[],
        model=model)
    text = _flat(p)
    assert "no private reasoning channel" in text
    assert "document.plan" in text


@pytest.mark.parametrize("model", ["deepseek-v4-flash-thinking", "deepseek-v4-pro-thinking"])
def test_extract_prompt_omits_scaffold_for_deepseek_in_thinking_mode(model):
    """DeepSeek in thinking mode has a private channel — it returns `reasoning_content` beside
    `content` — so it gets the compact nudge, not the explicit `document.plan` form written for
    models without one (D217). The `-thinking` marker isn't a catalog id, so before the catalog
    learned to strip it the lookup missed and every thinking call was handed the scaffold: a
    visible plan paid for on top of the private thinking it was meant to substitute for."""
    p = prompts.build_extract_prompt(
        pages_text="x", skill_text="", sidecar=None, brief=None, known_document_types=[],
        model=model)
    text = _flat(p)
    assert "no private reasoning channel" not in text
    assert "document.plan" not in text


def test_a_non_deepseek_id_ending_in_thinking_is_not_granted_a_channel():
    """The marker is DeepSeek's grammar alone (D88). Stripping it off any id that happens to end
    the same way would hand an unrelated model a reasoning channel on the strength of its name."""
    from watchdog.model_catalog import catalog_has_reasoning
    assert catalog_has_reasoning("deepseek-v4-flash-thinking") is True
    assert catalog_has_reasoning("claude-haiku-4-5-thinking") is False
    assert catalog_has_reasoning("gemini-3.7-flash-thinking") is False


def test_extract_prompt_omits_scaffold_for_a_reasoning_model():
    p = prompts.build_extract_prompt(
        pages_text="x", skill_text="", sidecar=None, brief=None, known_document_types=[],
        model="sonnet-4.6")
    assert "no private reasoning channel" not in _flat(p)


def test_extract_prompt_omits_scaffold_when_no_model_given():
    """`model` is optional (callers threading it through incrementally, or a caller that just
    wants the base instructions) — no model means no scaffold, not a crash."""
    p = prompts.build_extract_prompt(
        pages_text="x", skill_text="", sidecar=None, brief=None, known_document_types=[])
    assert "no private reasoning channel" not in _flat(p)


def test_extract_prompt_scaffold_survives_tier_alias_resolution():
    """`model` may be a tier alias ('haiku') or a raw catalog id ('claude-haiku-4-5') — both
    must resolve to the same branch, since callers pass whichever they were given."""
    by_alias = prompts.build_extract_prompt(
        pages_text="x", skill_text="", sidecar=None, brief=None, known_document_types=[],
        model="haiku")
    by_id = prompts.build_extract_prompt(
        pages_text="x", skill_text="", sidecar=None, brief=None, known_document_types=[],
        model="claude-haiku-4-5")
    assert "no private reasoning channel" in _flat(by_alias)
    assert "no private reasoning channel" in _flat(by_id)


def test_section_prompt_adds_scaffold_for_a_non_reasoning_model():
    p = prompts.build_section_prompt(
        pages_text="x", skill_text="", carry_forward="", section_label="pages 1-5",
        is_first=True, known_document_types=[], model="haiku")
    assert "no private reasoning channel" in _flat(p)


def test_section_prompt_omits_scaffold_for_a_reasoning_model():
    p = prompts.build_section_prompt(
        pages_text="x", skill_text="", carry_forward="", section_label="pages 1-5",
        is_first=True, known_document_types=[], model="sonnet-5")
    assert "no private reasoning channel" not in _flat(p)


# ── prompt caching (A1): extract/section prompts are content-block lists ──────────────────

def test_extract_prompt_cache_prefix_is_stable_across_volatile_data():
    """The cacheable prefix (instructions+brief, then skill) must be byte-identical regardless
    of per-document volatile data — that's the property Anthropic's prompt cache depends on."""
    kwargs = dict(skill_text="SKILL", brief="Investigate the fraud")
    p1 = prompts.build_extract_prompt(pages_text="doc one", known_document_types=[],
                                      sidecar=None, **kwargs)
    p2 = prompts.build_extract_prompt(pages_text="doc two, much longer text entirely",
                                      known_document_types=["Annual Report", "Affidavit"],
                                      sidecar="unrelated sidecar notes", **kwargs)
    assert [b["text"] for b in p1[:2]] == [b["text"] for b in p2[:2]]
    assert p1[2]["text"] != p2[2]["text"]         # volatile block does differ


def test_extract_prompt_cache_control_marks_the_skill_block():
    p = prompts.build_extract_prompt(pages_text="x", skill_text="SKILL", sidecar=None, brief=None,
                                     known_document_types=[])
    assert len(p) == 3
    assert "cache_control" not in p[0]
    assert p[1]["cache_control"] == {"type": "ephemeral"}
    assert "SKILL" in p[1]["text"]
    assert "cache_control" not in p[2]


def test_extract_prompt_cache_ttl_overridable_for_batch():
    """Batch submissions (#214) use the 1-hour cache TTL — a batch routinely outlives the
    default 5-minute window before its requests are even picked up."""
    p = prompts.build_extract_prompt(pages_text="x", skill_text="SKILL", sidecar=None, brief=None,
                                     known_document_types=[], cache_ttl="1h")
    assert p[1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_section_prompt_cache_prefix_is_stable_across_sections():
    """The stable block (instructions+brief) and the skill block must be identical whether this
    is section 1 or a later section, with different carry-forward/section text — sequential
    sections of one document need a stable prefix to actually hit the cache."""
    kwargs = dict(skill_text="SKILL",
                 known_document_types=[], brief="Investigate the fraud")
    p1 = prompts.build_section_prompt(pages_text="section one text", carry_forward="",
                                      section_label="pages 1", is_first=True, **kwargs)
    p2 = prompts.build_section_prompt(pages_text="section two, different text", carry_forward="carried",
                                      section_label="pages 2", is_first=False, **kwargs)
    assert [b["text"] for b in p1[:2]] == [b["text"] for b in p2[:2]]
    assert p1[2]["text"] != p2[2]["text"]
    # "1h" by default (#498) — see test_section_prompt_cache_ttl_defaults_to_1h_and_is_overridable
    assert p1[1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"} == p2[1]["cache_control"]


def test_section_prompt_cache_ttl_defaults_to_1h_and_is_overridable():
    """#498: a checkpointed retry can land on this document's next section well past the 5m
    default window (a rate-limit backoff, a Ctrl-C resumed later) — same reasoning as the batch
    path's "1h" override, but the default here rather than opt-in, since every section call
    within one document now has to tolerate an arbitrary gap before the next one."""
    p = prompts.build_section_prompt(pages_text="x", skill_text="SKILL", carry_forward="",
                                     section_label="pages 1", is_first=True,
                                     known_document_types=[])
    assert p[1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}

    p = prompts.build_section_prompt(pages_text="x", skill_text="SKILL", carry_forward="",
                                     section_label="pages 1", is_first=True,
                                     known_document_types=[], cache_ttl="5m")
    assert p[1]["cache_control"] == {"type": "ephemeral"}


# ── FILE_METADATA block (#369) ────────────────────────────────────────────────

def test_extract_prompt_includes_file_metadata_block_when_present():
    p = prompts.build_extract_prompt(
        pages_text="x", skill_text="", sidecar=None,
        brief=None, known_document_types=[],
        file_metadata={"author": "Jane Doe", "producer": "Acrobat"},
        processing={"ocr_used": False, "source_type": "direct_text"})
    volatile = p[-1]["text"]
    assert "FILE_METADATA" in volatile
    assert "Jane Doe" in volatile and "Acrobat" in volatile
    assert "ocr_used=False" in volatile
    assert "source_type='direct_text'" in volatile
    # trust caveat: forgeable/weigh-don't-trust, OCR/scanner caveat, template-inheritance caveat
    assert "forgeable" in volatile
    assert "scanner" in volatile or "scan" in volatile
    assert "template" in volatile


def test_extract_prompt_omits_file_metadata_block_when_empty():
    p = prompts.build_extract_prompt(
        pages_text="x", skill_text="", sidecar=None,
        brief=None, known_document_types=[], file_metadata={}, processing={})
    assert "FILE_METADATA" not in _flat(p)


def test_extract_prompt_omits_file_metadata_block_when_not_supplied():
    """The params are optional — an omitted file_metadata must not error and must not render
    the block (existing call sites that don't pass it keep working)."""
    p = prompts.build_extract_prompt(
        pages_text="x", skill_text="", sidecar=None,
        brief=None, known_document_types=[])
    assert "FILE_METADATA" not in _flat(p)


def test_section_prompt_includes_file_metadata_block_when_present():
    p = prompts.build_section_prompt(
        pages_text="x", skill_text="", carry_forward="",
        section_label="pp.1-10", is_first=True, known_document_types=[],
        file_metadata={"author": "Jane Doe"}, processing={"ocr_used": True, "source_type": "docling"})
    volatile = p[-1]["text"]
    assert "FILE_METADATA" in volatile
    assert "Jane Doe" in volatile
    assert "ocr_used=True" in volatile


def test_section_prompt_omits_file_metadata_block_when_empty():
    p = prompts.build_section_prompt(
        pages_text="x", skill_text="", carry_forward="",
        section_label="pp.1-10", is_first=True, known_document_types=[])
    assert "FILE_METADATA" not in _flat(p)


# ── candidate checklist (#361/D123) ─────────────────────────────────────────────────────────

def test_extract_prompt_candidates_land_in_volatile_block_only():
    p = prompts.build_extract_prompt(
        pages_text="x", skill_text="SKILL", sidecar=None, brief=None,
        known_document_types=[], candidates="p.1: [money] $5")
    assert "p.1: [money] $5" in p[-1]["text"]
    assert "p.1: [money] $5" not in p[0]["text"]     # not in the stable prefix
    assert "p.1: [money] $5" not in p[1]["text"]     # not in the cached skill block


def test_extract_prompt_omits_candidates_block_when_none():
    without = prompts.build_extract_prompt(
        pages_text="x", skill_text="SKILL", sidecar=None, brief=None, known_document_types=[])
    assert "CANDIDATE CHECKLIST" not in _flat(without)


def test_section_prompt_candidates_land_in_volatile_block_only():
    p = prompts.build_section_prompt(
        pages_text="x", skill_text="SKILL", carry_forward="", section_label="pp.1-10",
        is_first=True, known_document_types=[], candidates="p.1: [money] $5")
    assert "p.1: [money] $5" in p[-1]["text"]
    assert "p.1: [money] $5" not in p[0]["text"]
    assert "p.1: [money] $5" not in p[1]["text"]


def test_section_prompt_omits_candidates_block_when_none():
    p = prompts.build_section_prompt(
        pages_text="x", skill_text="SKILL", carry_forward="", section_label="pp.1-10",
        is_first=True, known_document_types=[])
    assert "CANDIDATE CHECKLIST" not in _flat(p)


# ── the verification pass's shared prefix (#535) ──────────────────────────────

def test_verify_prompt_prefix_is_byte_identical_to_the_extraction_call():
    """The whole cost case for the pass: the verifier re-reads the document at the cached-input
    rate rather than paying for it a second time. That only happens if every block the extraction
    call sent is sent again unchanged, so the verify prompt is the extract prompt plus a tail."""
    base = prompts.build_extract_prompt(
        pages_text="DOCUMENT TEXT HERE", skill_text="SKILL", sidecar=None,
        brief="Investigate the fraud", known_document_types=[], cache_document=True)
    p = prompts.build_verify_prompt(base, key_facts=[{"fact": "Filed in 2024"}],
                                    entities=[{"id": "acme-corp", "name": "Acme Corp"}])

    assert p[:len(base)] == base
    assert len(p) == len(base) + 1


def test_verify_prompt_carries_the_facts_and_bounds_the_entity_ids():
    p = prompts.build_verify_prompt(
        [{"type": "text", "text": "BASE"}],
        key_facts=[{"fact": "Filed in 2024", "page": 3}],
        entities=[{"id": "acme-corp", "name": "Acme Corp"}, {"name": "no id here"}])
    tail = p[-1]["text"]

    assert "Filed in 2024" in tail
    assert "- acme-corp | Acme Corp" in tail
    assert "no id here" not in tail          # an entity with no id can't be tagged, so it's not offered


def test_verify_prompt_handles_an_extraction_that_named_no_entities():
    p = prompts.build_verify_prompt([{"type": "text", "text": "BASE"}], key_facts=[], entities=[])
    assert "(none)" in p[-1]["text"]


def test_document_block_is_cached_only_when_the_verifier_will_reread_it():
    """Off by default: with no verification pass to read the cache back, marking a document's own
    text — unique to that one call — would pay the cache-write premium for nothing."""
    plain = prompts.build_extract_prompt(pages_text="x", skill_text="SKILL", sidecar=None,
                                         brief=None, known_document_types=[])
    cached = prompts.build_extract_prompt(pages_text="x", skill_text="SKILL", sidecar=None,
                                          brief=None, known_document_types=[], cache_document=True)

    assert "cache_control" not in plain[2]
    assert cached[2]["cache_control"] == {"type": "ephemeral"}
    assert plain[2]["text"] == cached[2]["text"]


def test_section_document_block_is_cached_only_when_the_verifier_will_reread_it():
    kwargs = dict(skill_text="SKILL", carry_forward="", section_label="pp.1-10",
                  is_first=True, known_document_types=[])
    plain = prompts.build_section_prompt(pages_text="x", **kwargs)
    cached = prompts.build_section_prompt(pages_text="x", cache_document=True, **kwargs)

    assert "cache_control" not in plain[2]
    # "1h" here, matching the skill block's section default (#498)
    assert cached[2]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_extract_prompt_tags_a_computed_figure_as_inferred():
    """#622: the `basis` paragraph closed by telling the model how to *format* a computed figure
    ("name its components in the fact text") without ever saying to tag one — and 69 `stated`
    facts on disk carry a dollar figure that appears nowhere in their source document. The
    formatting rule must read as a worked example of the inferred rule, not an alternative to it."""
    p = prompts.build_extract_prompt(
        pages_text="x", skill_text="", sidecar=None, brief=None, known_document_types=[])
    text = _flat(p)
    assert "the fact carrying it is `inferred`, so tag it AND name the figure's components" in text
    assert "Naming the components is not a substitute for the tag" in text


# ── contradictions are surfaced regardless of basis (#622/D214) ──────────────

def test_reconcile_prompt_does_not_gate_contradictions_on_basis():
    """D214 reversed D203/D34: a contradiction is among the most valuable signals here, and the
    old gate suppressed only the *candid* derivations — `basis: inferred` fires on 0.16% of
    facts, so the derivations the model never declared were being compared all along."""
    p = prompts.build_reconcile_prompt({"pairs": [], "entities": []})
    assert "A claim's basis is **not** a reason to withhold" in p
    # the three suppression clauses are gone, not merely softened
    assert "both** sides are directly stated" not in p
    assert "is a reasoning error, not a finding" not in p
    assert "so the conflict is between a value the extractor computed" not in p


def test_synthesis_prompt_weighs_a_derived_figure_like_an_inferred_claim():
    """D215: synthesis prefers a stated value over an inferred one where sources conflict, but
    read `basis` alone and a computed figure — which almost never carries the label — could
    outrank a printed one. It now names `figure_verify`'s annotation alongside *(inferred)*."""
    text = prompts._text("synthesis")
    assert "not found in the document — may be derived" in text
    assert "Treat the two markings alike" in text


def test_reconcile_prompt_names_the_annotations_the_renderer_actually_emits():
    """The prompt lists the notes by their exact rendered wording so the model recognizes them
    in the claims bundle (vault and bundle both render through `_render_evidence_fragments`).
    Drift here is no longer silent suppression — nothing is gated on these any more — but a
    phrase the model is told about and never sees is still dead text worth catching."""
    from watchdog.pipeline.write_vault import _figure_verification_note

    p = prompts.build_reconcile_prompt({"pairs": [], "entities": []})

    derived = _figure_verification_note({"figures_unverified": ["360300000"]})
    assert "not found in the document — may be derived" in derived

    off_page = _figure_verification_note({"figures_off_page": {"21406000": [7]}})
    assert "found on another page" in off_page

    for phrase in ("not found in the document — may be derived", "found on another page"):
        assert phrase in p


def test_prompt_templates_not_in_skills_catalog():
    """The user-facing guarantee: prompt templates are invisible to the classifier."""
    catalog = set(skills_catalog.catalog())
    assert not (catalog & set(_TEMPLATES))
    index = skills_catalog.build_index()
    # Index entries are "- `name.md` — desc"; match the entry form, not the bare
    # word, which can legitimately appear inside a skill description.
    assert "`extract_instructions.md`" not in index
    assert "`briefing.md`" not in index
