"""Prompt builders for the orchestrator's model tasks (#118).

The task-specific instruction prose lives in editable markdown templates under
``watchdog/prompts/*.md`` (loaded via :func:`_text` / :func:`_render`); these builders
load that prose and assemble it with the per-call data (document text, entity JSON,
conditional sections). model_client prepends a generic JSON-only system prompt and
appends the schema; these builders supply the task-specific instructions + data as the
`prompt`.

The prompt templates are deliberately **separate** from the record skills in
``watchdog/skills/records/`` — ``skills_catalog`` never scans this directory, so editing a
prompt here does not touch the classifier index or ``watchdog show-skills``.
"""

import importlib.resources
import json
from functools import lru_cache


@lru_cache(maxsize=None)
def _text(name: str) -> str:
    """Load a prompt template (``watchdog/prompts/<name>.md``), stripped of edge whitespace."""
    return (importlib.resources.files("watchdog") / "prompts" / f"{name}.md").read_text(
        encoding="utf-8").strip()


def _render(name: str, **values: object) -> str:
    """Load a template and substitute ``{{key}}`` tokens. Single braces are left untouched,
    so a template may contain literal ``{`` / ``}`` (e.g. JSON examples) freely."""
    text = _text(name)
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text


def build_classify_prompt(doc_excerpt: str, index_text: str) -> str:
    return (
        f"{_text('classify')}\n\n"
        f"Available skills (one line each):\n{index_text}\n\n"
        f"Document excerpt:\n{doc_excerpt}"
    )


def _known_types_block(known_document_types: list) -> str:
    """The `document_type` vocabulary the model should reuse from — see extract_instructions.md."""
    if not known_document_types:
        return "\nKNOWN_DOCUMENT_TYPES: (none yet — coin a concise descriptive type)"
    listed = "\n".join(f"- {t}" for t in known_document_types)
    return ("\nKNOWN_DOCUMENT_TYPES (reuse one verbatim if it fits; only coin a new type "
            f"if none match):\n{listed}")


def _cache_block(text: str, *, ttl: str = "5m") -> dict:
    """A content block marking the end of the cacheable prefix (A1) — Anthropic's Messages
    API caches everything up to and including the block carrying `cache_control`. `ttl`
    defaults to the standard 5-minute window (the bare `{"type": "ephemeral"}` form, unchanged
    from #213); batch submissions (#214) pass `"1h"` explicitly since a batch routinely outlives
    5 minutes before its requests are even picked up."""
    cache_control = {"type": "ephemeral"}
    if ttl != "5m":
        cache_control["ttl"] = ttl
    return {"type": "text", "text": text, "cache_control": cache_control}


def build_extract_prompt(*, pages_text: str, existing_entities: list, skill_text: str,
                         sidecar: str | None, brief: str | None,
                         known_document_types: list, cache_ttl: str = "5m") -> list[dict]:
    # Document identity (sha256/filename/original_path/page_count) and provenance
    # (source/obtained) are stamped onto the result by Python — see
    # orchestrate._stamp_document — so they are deliberately not asked of the model here.
    #
    # Returned as content blocks, not one string (A1): block 1 (instructions + brief) is
    # constant for the whole run; block 2 (the domain skill) is constant per document type and
    # carries the cache breakpoint, so blocks 1+2 together are the cacheable prefix — every
    # extraction call sharing a skill within a run re-pays only the 0.1x cache-read rate for it.
    # Block 3 is per-document volatile data and is never cached. `cache_ttl` is "1h" for batch
    # submissions (#214) — see _cache_block.
    stable = [_text("extract_instructions")]
    if brief:
        stable.append(f"\nINVESTIGATION BRIEF (orient extraction toward this):\n{brief}")

    volatile = [f"\nEXISTING_ENTITIES (for dedup + contradiction check):\n"
               f"{json.dumps(existing_entities, ensure_ascii=False)}",
               _known_types_block(known_document_types)]
    if sidecar:
        volatile.append(f"\nSIDECAR (provenance + notes — context for your extraction):\n{sidecar}")
    volatile.append(f"\nDOCUMENT TEXT:\n{pages_text}")

    return [
        {"type": "text", "text": "\n".join(stable)},
        _cache_block(f"\nDOMAIN SKILL ({'matched' if skill_text else 'none'}):\n{skill_text or '(none)'}",
                    ttl=cache_ttl),
        {"type": "text", "text": "\n".join(volatile)},
    ]


def build_section_prompt(*, pages_text: str, existing_entities: list, skill_text: str,
                         carry_forward: str, section_label: str, is_first: bool,
                         known_document_types: list, brief: str | None = None) -> list[dict]:
    # Same cache-block split as build_extract_prompt (A1): instructions + brief + skill lead,
    # since those are the only parts stable across every section of one run — the section
    # label/metadata-mode note, carry-forward, and section text change on every call, so they
    # move after the skill block instead of leading (as in the old single-string layout) to
    # keep the cacheable prefix at the front of the content array.
    stable = [_text("extract_instructions")]
    if brief:
        stable.append(f"\nINVESTIGATION BRIEF (orient extraction toward this):\n{brief}")

    volatile = [_render("section_intro", section_label=section_label)]
    if is_first:
        volatile.append("This is SECTION 1: fill document metadata (title, document_type, "
                        "date_of_document) and the morgue_entity_id field.")
        volatile.append(_known_types_block(known_document_types))
    else:
        volatile.append("This is a LATER section: omit document metadata and morgue fields; supply "
                        "entities + document.key_facts + document.summary for this section only.")
    volatile.append("Put only forward-looking reporting notes for the briefing in `observations` — "
                    "leads to chase, open questions, missing documents, threads to other sections or "
                    "documents. Do NOT restate figures, dates, chronology, or contradictions (those "
                    "are captured in key_facts); leave it empty if there is nothing forward-looking.")
    if carry_forward:
        volatile.append(f"\nCARRY-FORWARD (entities/observations from earlier sections — reuse these "
                        f"ids):\n{carry_forward}")
    volatile.append(f"\nEXISTING_ENTITIES:\n{json.dumps(existing_entities, ensure_ascii=False)}")
    volatile.append(f"\nSECTION TEXT:\n{pages_text}")

    return [
        {"type": "text", "text": "\n".join(stable)},
        _cache_block(f"\nDOMAIN SKILL:\n{skill_text or '(none)'}"),
        {"type": "text", "text": "\n".join(volatile)},
    ]


def build_synthesis_prompt(bundle: dict) -> str:
    return (
        f"{_text('synthesis')}\n\n"
        f"Entities:\n{json.dumps(bundle.get('entities', []), ensure_ascii=False)}"
    )


def build_briefing_prompt(*, brief: str | None, results: list, scratchpads: list,
                          neardup_alerts: list, contradiction_flags: list) -> str:
    return (
        f"{_text('briefing')}\n\n"
        f"INVESTIGATION BRIEF:\n{brief or '(none)'}\n\n"
        f"RESULTS:\n{json.dumps(results, ensure_ascii=False)}\n\n"
        f"NEAR-DUP ALERTS:\n{json.dumps(neardup_alerts, ensure_ascii=False)}\n\n"
        f"CONTRADICTION FLAGS:\n{json.dumps(contradiction_flags, ensure_ascii=False)}\n\n"
        f"SCRATCHPADS:\n" + "\n\n---\n\n".join(scratchpads)
    )


def build_timeline_dedup_prompt(date: str, events: list[dict]) -> str:
    # Present each event by index with just the text it needs to judge duplication (the date
    # is constant). page/basis/source_sha256 stay in Python — the model returns indices.
    listed = "\n".join(
        f"[{i}] {e.get('event', '')}" + (f"  (p.{e['page']})" if e.get("page") else "")
        for i, e in enumerate(events))
    return f"{_render('timeline_dedup', date=date)}\n\nEvents:\n{listed}"
