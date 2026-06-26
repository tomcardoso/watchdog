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


def build_extract_prompt(*, pages_text: str, existing_entities: list, skill_text: str,
                         sidecar: str | None, sha256: str, filename: str,
                         original_path: str | None, page_count: int,
                         brief: str | None) -> str:
    parts = [_text("extract_instructions"), ""]
    parts.append(f"Set document.sha256 = {sha256!r}, document.filename = {filename!r}, "
                 f"document.original_path = {original_path!r}, document.page_count = {page_count}.")
    if brief:
        parts.append(f"\nINVESTIGATION BRIEF (orient extraction toward this):\n{brief}")
    parts.append(f"\nDOMAIN SKILL ({'matched' if skill_text else 'none'}):\n{skill_text or '(none)'}")
    parts.append(f"\nEXISTING_ENTITIES (for dedup + contradiction check):\n"
                 f"{json.dumps(existing_entities, ensure_ascii=False)}")
    if sidecar:
        parts.append(f"\nSIDECAR (source/obtained metadata):\n{sidecar}")
    parts.append(f"\nDOCUMENT TEXT:\n{pages_text}")
    return "\n".join(parts)


def build_section_prompt(*, pages_text: str, existing_entities: list, skill_text: str,
                         carry_forward: str, section_label: str, is_first: bool,
                         sha256: str, filename: str, original_path: str | None,
                         page_count: int) -> str:
    parts = [
        _render("section_intro", section_label=section_label),
        "",
        _text("extract_instructions"),
        "",
    ]
    if is_first:
        parts.append("This is SECTION 1: fill document metadata (title, document_type, "
                     "date_of_document, page_count, sha256, filename, original_path) and the "
                     "morgue_entity_id / morgue_document_type fields.")
        parts.append(f"Set document.sha256 = {sha256!r}, document.filename = {filename!r}, "
                     f"document.original_path = {original_path!r}, document.page_count = {page_count}.")
    else:
        parts.append("This is a LATER section: omit document metadata and morgue fields; supply "
                     "entities + document.key_facts + document.summary for this section only.")
    parts.append("Put salient, high-signal notes for the briefing in `observations`.")
    if carry_forward:
        parts.append(f"\nCARRY-FORWARD (entities/observations from earlier sections — reuse these "
                     f"ids):\n{carry_forward}")
    parts.append(f"\nDOMAIN SKILL:\n{skill_text or '(none)'}")
    parts.append(f"\nEXISTING_ENTITIES:\n{json.dumps(existing_entities, ensure_ascii=False)}")
    parts.append(f"\nSECTION TEXT:\n{pages_text}")
    return "\n".join(parts)


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
    return (
        f"{_render('timeline_dedup', date=date)}\n\n"
        f"Events:\n{json.dumps(events, ensure_ascii=False)}"
    )
