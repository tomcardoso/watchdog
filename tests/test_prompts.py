"""Prompt templates load from watchdog/prompts/*.md and stay out of the skills catalog."""

import pytest

from watchdog import model_client, skills_catalog
from watchdog.pipeline import prompts

_flat = model_client._flatten_prompt   # extract/section prompts are content-block lists (A1)

_TEMPLATES = ["classify", "extract_instructions", "section_intro",
              "synthesis", "briefing", "timeline_dedup", "timeline_precision", "digest"]


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
        pages_text="x", existing_entities=[], skill_text="", carry_forward="",
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
        pages_text="x", existing_entities=[], skill_text="", carry_forward="",
        section_label="pp.11-20", is_first=False, known_document_types=[])
    text = _flat(p)
    assert "only section 1's summary is kept" not in text
    assert "composed after the merge" in text
    assert "LATER section" in text


def test_first_section_prompt_still_fills_metadata():
    p = prompts.build_section_prompt(
        pages_text="x", existing_entities=[], skill_text="", carry_forward="",
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
    assert "acme-ar.pdf" in p
    assert "Acme AR" in p
    assert "Annual Report" in p
    assert "42" in p
    assert "THE DOMAIN SKILL" in p        # extractor-tier context parity (#279)
    assert "CHASE THE FRAUD" in p
    assert "SIDECAR NOTES" in p
    assert "Filed in 2024" in p and "Revenue grew" in p


def test_build_digest_prompt_falls_back_when_fields_missing():
    p = prompts.build_digest_prompt(filename="", title="", document_type="", page_count=None,
                                    skill_text=None, brief=None, sidecar=None, key_facts=[])
    assert "(untitled)" in p
    assert "(unknown)" in p
    assert "(none)" in p


def test_extract_prompt_includes_instructions_and_data():
    p = prompts.build_extract_prompt(
        pages_text="DOCBODY", existing_entities=[{"id": "x"}], skill_text="SKILL",
        sidecar=None, brief=None, known_document_types=[])
    text = _flat(p)
    assert "key_facts" in text              # instruction prose loaded
    assert "DOCBODY" in text and "SKILL" in text   # data assembled in
    # identity fields are no longer asked of the model (stamped in Python)
    assert "Set document.sha256" not in text


def test_extract_prompt_lists_known_document_types():
    p = prompts.build_extract_prompt(
        pages_text="x", existing_entities=[], skill_text="", sidecar=None, brief=None,
        known_document_types=["Annual Report", "Affidavit"])
    text = _flat(p)
    assert "KNOWN_DOCUMENT_TYPES" in text
    assert "- Annual Report" in text and "- Affidavit" in text
    assert "reuse one verbatim" in text


def test_extract_prompt_handles_no_known_types():
    p = prompts.build_extract_prompt(
        pages_text="x", existing_entities=[], skill_text="", sidecar=None, brief=None,
        known_document_types=[])
    assert "none yet" in _flat(p)


# ── prompt caching (A1): extract/section prompts are content-block lists ──────────────────

def test_extract_prompt_cache_prefix_is_stable_across_volatile_data():
    """The cacheable prefix (instructions+brief, then skill) must be byte-identical regardless
    of per-document volatile data — that's the property Anthropic's prompt cache depends on."""
    kwargs = dict(skill_text="SKILL", brief="Investigate the fraud", known_document_types=[])
    p1 = prompts.build_extract_prompt(pages_text="doc one", existing_entities=[{"id": "a"}],
                                      sidecar=None, **kwargs)
    p2 = prompts.build_extract_prompt(pages_text="doc two, much longer text entirely",
                                      existing_entities=[{"id": "b"}, {"id": "c"}],
                                      sidecar="unrelated sidecar notes", **kwargs)
    assert [b["text"] for b in p1[:2]] == [b["text"] for b in p2[:2]]
    assert p1[2]["text"] != p2[2]["text"]         # volatile block does differ


def test_extract_prompt_cache_control_marks_the_skill_block():
    p = prompts.build_extract_prompt(pages_text="x", existing_entities=[], skill_text="SKILL",
                                     sidecar=None, brief=None, known_document_types=[])
    assert len(p) == 3
    assert "cache_control" not in p[0]
    assert p[1]["cache_control"] == {"type": "ephemeral"}
    assert "SKILL" in p[1]["text"]
    assert "cache_control" not in p[2]


def test_extract_prompt_cache_ttl_overridable_for_batch():
    """Batch submissions (#214) use the 1-hour cache TTL — a batch routinely outlives the
    default 5-minute window before its requests are even picked up."""
    p = prompts.build_extract_prompt(pages_text="x", existing_entities=[], skill_text="SKILL",
                                     sidecar=None, brief=None, known_document_types=[],
                                     cache_ttl="1h")
    assert p[1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_section_prompt_cache_prefix_is_stable_across_sections():
    """The stable block (instructions+brief) and the skill block must be identical whether this
    is section 1 or a later section, with different carry-forward/section text — sequential
    sections of one document need a stable prefix to actually hit the cache."""
    kwargs = dict(existing_entities=[], skill_text="SKILL", known_document_types=[],
                 brief="Investigate the fraud")
    p1 = prompts.build_section_prompt(pages_text="section one text", carry_forward="",
                                      section_label="pages 1", is_first=True, **kwargs)
    p2 = prompts.build_section_prompt(pages_text="section two, different text", carry_forward="carried",
                                      section_label="pages 2", is_first=False, **kwargs)
    assert [b["text"] for b in p1[:2]] == [b["text"] for b in p2[:2]]
    assert p1[2]["text"] != p2[2]["text"]
    assert p1[1]["cache_control"] == {"type": "ephemeral"} == p2[1]["cache_control"]


def test_prompt_templates_not_in_skills_catalog():
    """The user-facing guarantee: prompt templates are invisible to the classifier."""
    catalog = set(skills_catalog.catalog())
    assert not (catalog & set(_TEMPLATES))
    index = skills_catalog.build_index()
    # Index entries are "- `name.md` — desc"; match the entry form, not the bare
    # word, which can legitimately appear inside a skill description.
    assert "`extract_instructions.md`" not in index
    assert "`briefing.md`" not in index
