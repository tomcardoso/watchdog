"""
Watchdog section planner — split a large queued document into sections for
sectioned (sequential) extraction.

Large documents don't fit comfortably in one extraction call's context, and
the cost of a single huge context grows with its size. This splits a document
into overlapping sections of roughly `section_token_budget` estimated tokens
each. The orchestrator extracts the sections in reading order, carrying a
running scratchpad forward, then merges the per-section results with
`merge.merge_extractions`.

The trigger is **estimated tokens**, not page count: page count is a poor proxy
because density varies several-fold (a table-heavy financial page is far denser
than prose) and non-paginated files (.txt/.csv/.md) are a single "page"
regardless of size. Token estimation is a cheap chars/4 heuristic.

Splitting:
  * paginated documents (page_count > 1) split on **page boundaries** — pages
    packed greedily against their own estimated token counts, not a uniform
    page count derived from the document's average density (#596) — so page
    citations are preserved and a section's size respects the budget wherever
    in the document it falls.
  * non-paginated documents split the single page's text into **character
    windows**; citations for that content carry no page number.

Threshold-gated: documents whose estimated tokens are at or below
`section_token_threshold` are not sectioned (the caller uses the whole-document
path).

On a model whose price steps up past a prompt length (`long_context_threshold`), the
**whole prompt** is planned under that line, not only the document text (D370): the
instructions, record skill, brief and file details are measured from the real prompt
builders (`fixed_prompt_tokens`), the candidate checklist is counted page by page from the
text each call covers (`page_checklist_tokens`), and later sections reserve room for the
carried-forward entities (`_CARRY_RESERVE_EST`). `plan_calls` is the one implementation of
"what calls will this document make, and how big is each prompt", shared by `run` and the
pre-run cost estimate (`ingest_setup.long_prompt_plan`), so the two cannot disagree.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from watchdog import config as user_config


# Overlap between consecutive sections, as a fraction of the section budget, so a table or
# paragraph straddling a page boundary is wholly visible in one section without the overlap
# swallowing small budgets. The fraction reproduces the historical 4,000 tokens at a
# 60,000-token budget.
_OVERLAP_NUMERATOR, _OVERLAP_DENOMINATOR = 4_000, 60_000
_CHARS_PER_TOKEN = 4                # cheap heuristic

# Provider-aware sectioning defaults (#321): the threshold and per-section budget are fractions
# of the extraction model's context window rather than fixed numbers, so a 1M-window model
# (DeepSeek V4) reads far more of a document in one call than a 200K Claude window would. The
# threshold reserves ~40% of the window for the schema, domain skill, carried-forward entities,
# scratchpad, and the extraction *output*; the budget is half the threshold so a document just
# over it splits into two sections. The fractions are chosen so a 200K Claude window reproduces
# the historical 120K/60K defaults exactly — no behaviour change on the default (Claude) path.
_THRESHOLD_FRACTION = 0.6
_BUDGET_FRACTION = 0.3

# Sectioning is sized from the input window alone (D202): no output-derived cap and no effort
# parameter. Truncation is handled where it is observed — a bounded re-split, or a retry one effort
# level lower when reasoning starves the answer.


def _config_get(key: str, default):
    return user_config.get(key, default)


def _resolve_override(key: str, model_default: int) -> int:
    """Config value for `key`, falling back to `model_default` when it is unset or the literal
    `"auto"` sentinel (#321). Only a positive int pins a fixed value; "auto", None, or a missing
    key all mean "use the model-aware default". An absolute override does NOT rescale when the
    extraction model changes — that is the tradeoff a user accepts by pinning a number."""
    val = _config_get(key, None)
    return val if isinstance(val, int) and not isinstance(val, bool) else model_default


def model_defaults(model: str | None, backend: str | None = None,
                   vault: Path | None = None) -> tuple[int, int]:
    """(threshold, budget) est-token sectioning defaults for the extraction model (D89, D180, D202).

    Fractions of the model's context window, clamped below its `long_context_threshold` (in real
    tokens, before the ratio division, so a sub-1.0 ratio can't push it back past the boundary), then
    divided by `tokenizer_ratio` to convert to the chars/4 est-token units the planner packs against.
    `vault`, when given, lets `tokenizer_ratio` use this vault's measured ratio. No output-ceiling term
    and no effort parameter (D202). The budget is half the threshold, so a document just over the
    threshold splits into two sections rather than one plus a sliver.

    These size the document TEXT only. On a model with a long-prompt line the clamp here is just an
    upper bound: what keeps each call's whole prompt under the line is `plan_calls` (D370)."""
    from watchdog import model_client
    window = model_client.context_window(model, backend)
    threshold = int(window * _THRESHOLD_FRACTION)
    budget = int(window * _BUDGET_FRACTION)
    # Both are clamped, not just the budget: `threshold` is what decides whether a document is
    # sectioned AT ALL, so leaving it unclamped would send a 400,000-token document past the
    # boundary in one whole-document call — the larger exposure of the two, since it is exactly
    # the big documents that reach a pricing tier.
    tier_cap = model_client.long_context_input_cap(model, backend)
    if tier_cap is not None:
        threshold = min(threshold, tier_cap)
        budget = min(budget, tier_cap)
    ratio = model_client.tokenizer_ratio(model, backend, vault)
    threshold = int(threshold / ratio)
    budget = max(1, int(budget / ratio))
    return threshold, budget


def section_token_threshold(model: str | None = None, backend: str | None = None,
                            vault: Path | None = None) -> int:
    """Estimated-token count at/under which a document is not sectioned.

    Model-aware by default: derived from the extraction model's context window and tokenizer ratio
    (#321, #555). An explicit integer `section_token_threshold` in config overrides it, as an
    advanced escape hatch; the `"auto"` sentinel (or an unset key) keeps the model-aware default.
    `vault` (#606 Part B) is passed straight through to `model_defaults`, so a caller with vault
    context benefits from this vault's own calibrated tokenizer ratio when one is available."""
    default_threshold, _ = model_defaults(model, backend, vault)
    return _resolve_override("section_token_threshold", default_threshold)


def est_tokens(text: str) -> int:
    """Cheap estimate of the token count of a string (~4 chars/token)."""
    return len(text or "") // _CHARS_PER_TOKEN


def est_tokens_from_pages(pages: list) -> int:
    return sum(est_tokens(p.get("markdown", "")) for p in pages)


# ── whole-prompt planning under a long-prompt line (D370) ─────────────────────────────────────
#
# A model with a `long_context_threshold` bills the whole request at a dearer card once its prompt
# is over that many real tokens. The prompt is the document text plus everything that rides on it,
# so every call is planned with all of it counted, in chars/4 est tokens converted with the model's
# tokenizer ratio, and kept under the line less `_WHOLE_PROMPT_HEADROOM`. The headroom covers only
# the estimate's own error now that nothing is left uncounted (D202's 10% also had to cover the
# then-unmeasured overhead, and was smaller than it).
_WHOLE_PROMPT_HEADROOM = 0.05
# Room kept on every section after the first for the carried-forward block (`orchestrate.
# _carry_text`): one short line per entity seen so far plus the last section's observations, capped
# at 2,000 characters. 4,000 est tokens is 16,000 characters: the observations and about 250
# entity lines. Not measured per document, since the entities are only known once the earlier
# sections have been extracted.
_CARRY_RESERVE_EST = 4_000
# The names half of a page's checklist line (GLiNER: person, organization, location) when the
# document carries no stored harvest to count it from (D365 stores one at pre-processing): est
# tokens per page, room for about ten names. A judgement, not a fit; see the CLAUDE.md Standing
# Follow-ups.
_NAMES_RESERVE_EST_PER_PAGE = 100
# A character-window section (a non-paginated document) carries at most `harvest._PAGE_CAP` regex
# candidates, all on one "p.?" line: 4,000 characters is room for 80 of them.
_CHAR_WINDOW_CHECKLIST_EST = 1_000
# The separator `run` and `orchestrate._pages_text` put between pages.
_PAGE_SEP = "\n\n---\n\n"


def _est_ceil(text: str | None) -> int:
    """`est_tokens`, rounded up: the planner sums many small pieces and must not lose a token on
    each."""
    return -(-len(text or "") // _CHARS_PER_TOKEN)


def _marked_page(page: int, markdown: str) -> str:
    return f"<!-- PAGE {page} -->\n\n{markdown}"


def long_prompt_line(model: str | None, backend: str | None = None,
                     vault: Path | None = None) -> int | None:
    """The most est tokens one extraction call's WHOLE prompt may be planned at on `model`, or None
    when the model prices flat at every length (D370): its `long_context_threshold`, less
    `_WHOLE_PROMPT_HEADROOM`, divided by the tokenizer ratio (this vault's measured one when
    `vault` is given). A local model never has one: a self-hosted id is not the vendor's."""
    from watchdog import model_catalog, model_client
    if backend == "local":
        return None
    line = model_catalog.catalog_long_context_threshold(
        model_client.resolve_model_id(model or model_client.DEFAULT_TIER))
    if not line:
        return None
    return int(line * (1 - _WHOLE_PROMPT_HEADROOM) / model_client.tokenizer_ratio(model, backend, vault))


_UNKNOWN = object()


def largest_skill_text() -> str:
    """The longest record skill in the catalogue: what the prompt is measured with when the skill
    is not known yet (the cost estimate, and batch submission, which plans before classifying), so
    a call can only turn out smaller than planned, never larger."""
    from watchdog import skills_catalog
    texts = [skills_catalog.read_skill(name) for name in skills_catalog.catalog()]
    return max(texts, key=len, default="")


def fixed_prompt_tokens(pf: dict, *, skill_text: str | None, brief: str | None,
                        model: str | None) -> int:
    """Est tokens of an extraction prompt for this document with no document text, no candidate
    checklist and no carry-forward: the instructions, the brief, the record skill, the document
    types, sidecar and file details, the candidate-list introduction, plus the system prompt and
    the output schema the API also counts as input. Rendered with the real prompt builders, as the
    largest of the whole-document, first-section and later-section variants, so one figure bounds
    every call the document makes."""
    from watchdog import model_client
    from watchdog.pipeline import prompts, schemas
    common = dict(skill_text=skill_text, brief=brief,
                  known_document_types=pf.get("known_document_types") or [],
                  file_metadata=pf.get("file_metadata") or {}, processing=pf.get("processing") or {},
                  candidates="-", model=model)
    label = "pages 9999–9999"
    variants = [
        prompts.build_extract_prompt(pages_text="", sidecar=pf.get("sidecar"), **common),
        prompts.build_section_prompt(pages_text="", carry_forward="", section_label=label,
                                     is_first=True, **common),
        prompts.build_section_prompt(pages_text="", carry_forward="", section_label=label,
                                     is_first=False, **common),
    ]
    body = max(sum(_est_ceil(b.get("text")) for b in blocks) for blocks in variants)
    schema = max(_est_ceil(json.dumps(schemas.EXTRACTION)), _est_ceil(json.dumps(schemas.SECTION)))
    return body + schema + _est_ceil(model_client._SYSTEM_PROMPT)


def _stored_names(stored, page: int) -> list[dict] | None:
    """The names pre-processing stored for `page` (D365's `harvest` key on the queue file,
    ``{"names": {"<page>": [[kind, value], …]}}``), or None when there is no stored harvest or its
    names half is missing (GLiNER failed there, or the document predates D365)."""
    names = stored.get("names") if isinstance(stored, dict) else None
    kept = names.get(str(page)) if isinstance(names, dict) else None
    if not isinstance(kept, list):
        return None
    return [{"page": page, "kind": k, "value": v} for k, v in kept]


def page_checklist_tokens(pf: dict) -> list[int]:
    """Est tokens of each page's line in the candidate checklist (index i is page i + 1), counted
    from that page's own text: the regex half as `harvest.harvest` finds it, the names half from
    the stored harvest when the document has one, else `_NAMES_RESERVE_EST_PER_PAGE`.

    Counted per page because the checklist is one line per page, so a call's checklist is the lines
    of the pages it covers. A page harvested alone yields as much as or more than the same page
    inside a longer text (the cross-page repeat cap only removes), so the sum over a call's pages
    bounds its checklist. GLiNER is never run here: names are found when a call is extracted."""
    from watchdog.pipeline import harvest
    pages = pf.get("pages") or []
    page_count = pf.get("page_count") or len(pages)
    by_num = {p.get("page"): p.get("markdown", "") for p in pages}
    stored = pf.get("harvest")
    out = []
    for n in range(1, page_count + 1):
        names = _stored_names(stored, n)
        line = harvest.format_checklist(harvest.harvest(_marked_page(n, by_num.get(n, "")))
                                        + (names or []))
        out.append((_est_ceil(line) + 1 if line else 0)
                   + (_NAMES_RESERVE_EST_PER_PAGE if names is None else 0))
    return out


@dataclass(frozen=True)
class PromptProfile:
    """What one document's extraction prompts are made of, in est tokens. `page_text[i]` is page
    i + 1's text as sectioning has always measured it (`est_tokens` of its markdown), so the
    window-sized rules decide exactly as before; `page_extra[i]` is what the page adds to a prompt
    on top of that: its marker and separator, a token of rounding, and its checklist line. `fixed`
    is the rest of the prompt (`fixed_prompt_tokens`), `carry` the room later sections keep for the
    carry-forward block. `char_text` is a non-paginated document's length in characters."""
    page_text: tuple[int, ...]
    page_extra: tuple[int, ...]
    fixed: int
    carry: int = _CARRY_RESERVE_EST
    paginated: bool = True
    char_text: int = 0


def prompt_profile(pf: dict, *, skill_text=_UNKNOWN, brief=_UNKNOWN, model: str | None = None,
                   vault: Path | None = None) -> PromptProfile:
    """The `PromptProfile` of the document `pf` (a `preflight.run` result) as `model` will be sent
    it. `skill_text` defaults to the largest record skill (`largest_skill_text`) and `brief` to the
    vault's `context.md`, for a caller that does not know them yet."""
    if skill_text is _UNKNOWN:
        skill_text = largest_skill_text()
    if brief is _UNKNOWN:
        path = (vault / "context.md") if vault is not None else None
        brief = path.read_text(encoding="utf-8") if path is not None and path.exists() else None
    pages = pf.get("pages") or []
    page_count = pf.get("page_count") or len(pages)
    by_num = {p.get("page"): p.get("markdown", "") for p in pages}
    paginated = page_count > 1 and len(pages) > 1
    nums = range(1, page_count + 1)
    frame = _est_ceil(_marked_page(9_999, "") + _PAGE_SEP) + 1
    return PromptProfile(
        page_text=tuple(est_tokens(by_num.get(n, "")) for n in nums),
        page_extra=tuple(frame + c for c in page_checklist_tokens(pf)),
        fixed=fixed_prompt_tokens(pf, skill_text=skill_text, brief=brief, model=model),
        paginated=paginated,
        char_text=0 if paginated or not pages else len(pages[0].get("markdown", "")))


def _smallest_budget(count, lo: int, hi: int, n: int) -> int:
    """The smallest budget in [lo, hi] for which `count(budget)` is at most `n` calls (binary
    search), or `hi`: evens out sections the line sized, so the last one is not a sliver."""
    best, lo = hi, max(1, lo)
    while lo <= hi:
        mid = (lo + hi) // 2
        if count(mid) <= n:
            best, hi = mid, mid - 1
        else:
            lo = mid + 1
    return best


def plan_calls(profile: PromptProfile, *, threshold: int, budget: int, overlap: int,
               line: int | None) -> dict:
    """The extraction calls one document will make, and each call's whole prompt in est tokens.

    `threshold`, `budget` and `overlap` size by document text, from the context window or the
    user's override, as before D370. `line` (`long_prompt_line`; None for a model that prices flat)
    adds the whole-prompt rule: the document goes as one call only if its whole prompt is at or
    under the line, and each section's text and checklist are held to the line less the fixed part
    and, after the first section, the carry-forward reserve. When the line is what sizes the
    sections, they are then evened out over the same number of calls.

    Returns ``{"sectioned", "ranges", "prompts"}``: `ranges` holds 1-based inclusive page ranges
    for a paginated document and character offsets (end exclusive) for one that is not, `prompts`
    each planned call's prompt in est tokens. A single page too large for the line on its own still
    gets its own call, planned over the line: pages are never cut."""
    text, extra = list(profile.page_text), list(profile.page_extra)
    text_total = sum(text) if profile.paginated else profile.char_text // _CHARS_PER_TOKEN
    whole = profile.fixed + sum(text) + sum(extra)
    if text_total <= threshold and (line is None or whole <= line):
        return {"sectioned": False, "ranges": [], "prompts": [whole]}

    capacity = budget
    if line is not None:
        room = line - profile.fixed - profile.carry
        if not profile.paginated:
            room -= _CHAR_WINDOW_CHECKLIST_EST
        if room < capacity:
            capacity = max(1, room)
            # The overlap is a share of the section, so it shrinks with it.
            overlap = min(overlap, max(1, capacity * _OVERLAP_NUMERATOR // _OVERLAP_DENOMINATOR))
    line_sized = capacity < budget

    if profile.paginated:
        cost = [t + x for t, x in zip(text, extra)] if line is not None else text
        ranges = plan_ranges(cost, capacity, overlap)
        if line_sized and len(ranges) > 1:
            n = len(ranges)
            fitted = _smallest_budget(lambda b: len(plan_ranges(cost, b, overlap)),
                                      -(-sum(cost) // n), capacity, n)
            ranges = plan_ranges(cost, fitted, overlap)
        prompts = [profile.fixed + (profile.carry if i else 0)
                   + sum(text[s - 1:e]) + sum(extra[s - 1:e])
                   for i, (s, e) in enumerate(ranges)]
    else:
        total, ov = profile.char_text, overlap * _CHARS_PER_TOKEN
        size = capacity * _CHARS_PER_TOKEN
        ranges = char_windows(total, size, ov)
        if line_sized and len(ranges) > 1:
            n = len(ranges)
            size = _smallest_budget(lambda b: len(char_windows(total, b, ov)), -(-total // n), size, n)
            ranges = char_windows(total, size, ov)
        prompts = [profile.fixed + (profile.carry if i else 0) + _CHAR_WINDOW_CHECKLIST_EST
                   + -(-(e - s) // _CHARS_PER_TOKEN)
                   for i, (s, e) in enumerate(ranges)]
    return {"sectioned": True, "ranges": ranges, "prompts": prompts}


def sizing(model: str | None, backend: str | None = None, vault: Path | None = None) -> dict:
    """The sizes `plan_calls` takes for `model`: the text threshold and budget (config overrides
    applied), the overlap, and the whole-prompt line."""
    default_threshold, default_budget = model_defaults(model, backend, vault)
    threshold = _resolve_override("section_token_threshold", default_threshold)
    budget = max(1, _resolve_override("section_token_budget", default_budget))
    default_overlap = max(1, budget * _OVERLAP_NUMERATOR // _OVERLAP_DENOMINATOR)
    return {"threshold": threshold, "budget": budget,
            "overlap": _config_get("section_overlap_tokens", default_overlap),
            "line": long_prompt_line(model, backend, vault)}


def plan_document(pf: dict, model: str | None, backend: str | None = None,
                  vault: Path | None = None, *, skill_text=_UNKNOWN, brief=_UNKNOWN) -> dict:
    """`plan_calls` for the document `pf` on `model`: the one place both `run` and the pre-run
    estimate (`ingest_setup.long_prompt_plan`) decide a document's calls. Adds `line` (est tokens,
    or None) and `ratio` (the tokenizer ratio the line was converted with) to the result."""
    from watchdog import model_client
    size = sizing(model, backend, vault)
    profile = prompt_profile(pf, skill_text=skill_text, brief=brief, model=model, vault=vault)
    plan = plan_calls(profile, threshold=size["threshold"], budget=size["budget"],
                      overlap=size["overlap"], line=size["line"])
    plan["line"] = size["line"]
    plan["ratio"] = model_client.tokenizer_ratio(model, backend, vault)
    return plan


def plan_ranges(page_tokens: list[int], budget: int, overlap: int) -> list[tuple[int, int]]:
    """Return 1-based inclusive (start, end) page ranges covering the document, packed greedily
    against each page's own estimated token count (`page_tokens[i - 1]` is page `i`'s).

    Walks pages in reading order and closes a section as soon as adding the next page would push
    it past `budget`, rather than cutting uniform ranges sized from the document's *average*
    density (#596). Density varies up to 4.6x inside a single document in the benchmark corpus, so
    one average-derived pages-per-section figure is wrong in both directions at once: dense
    stretches overshoot the budget (pushing output toward the ceiling, risking a truncation that
    costs an extra re-split pair of calls to recover from), sparse ones undershoot it (producing
    more sections than the budget requires, each re-paying the full prompt overhead — schema,
    record skill, harvested candidates, carry-forward). Greedy packing needs nothing the planner
    didn't already have: the per-page counts are right there in the queue file.

    A page whose own estimate exceeds `budget` stands alone as its own section. Sections split on
    page boundaries, so there is nothing smaller to cut, and a section must always advance.

    Consecutive ranges overlap by up to `overlap` estimated tokens — whole trailing pages of the
    section just closed, replayed at the head of the next — so a table straddling a boundary is
    wholly visible in at least one section. Overlap is accounted in tokens for the same reason the
    sections are: a page-count overlap derived from the average replays a wildly varying amount of
    text depending on where in the document it lands. It can never consume a whole section — the
    next section always starts at least one page past the previous section's start.
    """
    page_count = len(page_tokens)
    if page_count <= 0:
        return []
    budget = max(1, budget)
    overlap = max(0, overlap)
    ranges: list[tuple[int, int]] = []
    start = 1
    while start <= page_count:
        end = start                                  # first page always joins, however dense
        total = page_tokens[start - 1]
        while end < page_count and total + page_tokens[end] <= budget:
            total += page_tokens[end]
            end += 1
        ranges.append((start, end))
        if end >= page_count:
            break
        # Back the next section's start up over whole trailing pages while they fit the overlap
        # allowance, never past `start` — that guard is what makes the loop advance.
        next_start, carried = end + 1, 0
        while next_start - 1 > start and carried + page_tokens[next_start - 2] <= overlap:
            carried += page_tokens[next_start - 2]
            next_start -= 1
        start = next_start
    return ranges


def char_windows(total: int, size: int, overlap: int) -> list[tuple[int, int]]:
    """Return (start, end) character offsets (end exclusive) covering [0, total)."""
    if total <= 0:
        return []
    size = max(1, size)
    overlap = max(0, min(overlap, size - 1))
    windows: list[tuple[int, int]] = []
    start = 0
    while start < total:
        end = min(start + size, total)
        windows.append((start, end))
        if end >= total:
            break
        start = end - overlap
    return windows


def run(vault: Path, sha256: str, *, force_budget: int | None = None,
        model: str | None = None, backend: str | None = None, pf: dict | None = None,
        skill_text=_UNKNOWN, brief=_UNKNOWN) -> dict:
    """Plan sections for a document, and write each section's text to `.watchdog/tmp`.

    The calls are decided by `plan_document` (D370): one whole-document call when the text fits
    the window-sized threshold and, on a model with a long-prompt line, the whole prompt fits under
    the line; otherwise sections. `model`/`backend` are the extraction model; `pf` its pre-flight
    (read here when not given); `skill_text` and `brief` what the prompt will carry, when the
    caller knows them (otherwise the largest record skill and the vault's brief are measured).
    `force_budget` always sections by text alone (the fallback after a whole-document overrun),
    capping the budget at half the document so it yields at least two sections.

    The result carries `prompts` (each planned call's whole prompt in est tokens) and `line` (the
    est-token line they were planned under, or None) beside the sections."""
    queue_file = vault / ".watchdog" / "queue" / f"{sha256}.json"
    if not queue_file.exists():
        return {"error": f"queue file not found for sha256 {sha256}"}

    queue = json.loads(queue_file.read_text(encoding="utf-8"))
    pages = queue.get("pages", [])
    page_count = queue.get("page_count") or len(pages)
    total_tokens = est_tokens_from_pages(pages)
    paginated = page_count > 1 and len(pages) > 1

    if force_budget is not None:
        size = sizing(model, backend, vault)
        budget = min(force_budget, max(1, total_tokens // 2))   # guarantee ≥2 sections
        overlap_tokens = min(size["overlap"], max(0, budget // 4))
        if paginated:
            by_num = {p.get("page"): p.get("markdown", "") for p in pages}
            ranges = plan_ranges([est_tokens(by_num.get(n, "")) for n in range(1, page_count + 1)],
                                 budget, overlap_tokens)
        else:
            text = pages[0].get("markdown", "") if pages else ""
            ranges = char_windows(len(text), budget * _CHARS_PER_TOKEN,
                                  overlap_tokens * _CHARS_PER_TOKEN)
        plan = {"sectioned": True, "ranges": ranges, "prompts": [], "line": size["line"]}
    else:
        if pf is None:
            from watchdog.pipeline import preflight
            pf = preflight.run(vault, sha256)
        plan = plan_document(pf, model, backend, vault, skill_text=skill_text, brief=brief)
        if not plan["sectioned"]:
            return {"sectioned": False, "page_count": page_count, "est_tokens": total_tokens,
                    "prompts": plan["prompts"], "line": plan["line"]}

    tmp = vault / ".watchdog" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    sections = []

    if paginated:
        # Paginated: pages packed greedily against their own token counts (#596), so a dense
        # stretch gets fewer pages and a sparse one more (`plan_calls`).
        by_num = {p.get("page"): p.get("markdown", "") for p in pages}
        for idx, (start, end) in enumerate(plan["ranges"], start=1):
            parts = [f"<!-- PAGE {n} -->\n\n{by_num.get(n, '')}" for n in range(start, end + 1)]
            path = tmp / f"section_{sha256}_{idx:02d}.md"
            path.write_text("\n\n---\n\n".join(parts), encoding="utf-8")
            sections.append({
                "index": idx,
                "label": f"pages {start}–{end}",
                "paginated": True,
                "pages_path": str(path.relative_to(vault)),
            })
    else:
        # Non-paginated (text/csv/md, or a single huge page): split on characters.
        text = pages[0].get("markdown", "") if pages else ""
        windows = plan["ranges"]
        for idx, (cstart, cend) in enumerate(windows, start=1):
            path = tmp / f"section_{sha256}_{idx:02d}.md"
            path.write_text(text[cstart:cend], encoding="utf-8")
            sections.append({
                "index": idx,
                "label": f"part {idx} of {len(windows)}",
                "paginated": False,
                "pages_path": str(path.relative_to(vault)),
            })

    return {"sectioned": True, "page_count": page_count, "est_tokens": total_tokens,
            "sections": sections, "prompts": plan["prompts"], "line": plan["line"]}
