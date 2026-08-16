"""
Watchdog section planner — split a large queued document into sections for
sectioned (sequential) extraction.

Large documents don't fit comfortably in one extraction call's context, and
the cost of a single huge context grows with its size. This splits a document
into overlapping sections of roughly `section_token_budget` estimated tokens
each. The orchestrator extracts the sections in reading order, carrying a
running scratchpad forward, then merges the per-section results with
`watchdog merge-sections`.

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
"""

import json
import sys
from pathlib import Path

# Overlap between consecutive sections, as a fraction of the section budget (#490's overlap
# finding) — this used to be a fixed 4,000-token absolute value, calibrated against Claude's
# historical 60,000-token default budget (6.7%). On a backend with a small output-derived budget
# (openai/gemini, ~7,000 tokens — see model_defaults below), that same fixed value ate 57% of
# every section: a 70-page document that needs 6 sections at Claude's overlap ratio needed 22 at
# the fixed one, each one mostly re-reading pages the previous section already covered. Scaling
# it keeps the overlap's actual purpose (make sure a table/paragraph straddling a page boundary
# is wholly visible in at least one section) proportional to the section size instead of
# swallowing it. (Those small output-derived budgets are themselves gone as of #555 — see the
# comment above `_config_get` — but scaling the overlap remains the right shape regardless.)
# `_OVERLAP_NUMERATOR/_OVERLAP_DENOMINATOR` are chosen so Claude's default budget
# (60,000) reproduces the historical fixed value exactly — this fix targets other backends'
# runaway scaling, not Claude's already-correct default behaviour.
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

# Sectioning is sized from the input window alone (#555, D199). It used to be additionally capped
# by an output-derived input ceiling (#343, #542): predict a call's *total* (reasoning + visible)
# output from its input size with an affine fit, then invert that fit against the model's wire
# `max_output_tokens` envelope to find the largest input whose answer would still fit. That whole
# apparatus — `_OUTPUT_DENSITY_BY_EFFORT`, `_invert_output_ceiling`, and the
# `_MIN`/`_MAX_OUTPUT_CAPPED_BUDGET` clamps around it — is gone, for three measured reasons.
#
# It never bound. Across 673 archived extraction calls, peak output as a share of the model's own
# wire envelope was 14% (gemini-3.5-flash medium), 27% (gpt-5.4-nano medium), 16% (gpt-5.6-luna
# high). The only calls that ever reached a cap were gpt-5.4-mini at high effort, and they reached
# the *old* 96,000 wire cap that #598 has since raised to 115,200. Nine of the fifteen catalogued
# models share one `max_output_tokens` (128,000) and six paginate past it, so the ceiling could
# not have distinguished between models even where it did apply.
#
# The fit pooled models that behave nothing alike. gpt-5.4-mini at high effort emits ~2.4 tokens
# of chain-of-thought per input token; gemini-3.5-flash at the same effort emits essentially none
# (per-model slope -0.002). Pooling let gpt-5.4-mini's appetite set Gemini's budget — which is how
# one model came to get 50,000 at low effort and 5,660 at high, a 9x swing driven by a different
# vendor's reasoning volume. That, not the ceilings, produced the spread #555 was filed about.
#
# And the inversion is ill-conditioned wherever the slope is near zero: dividing an envelope by a
# marginal rate of -0.002 yields an unbounded budget, so the formula degenerates on exactly the
# models it was meant to protect. `_MAX_OUTPUT_CAPPED_BUDGET` (50,000) was the constant holding
# that degeneracy back — and it, rather than any property of the model, was what actually set the
# budget on every ceiling-governed backend.
#
# Truncation is now handled where it is observable instead of predicted: `orchestrate`'s bounded
# re-split (#540) halves a section that actually did truncate, and the starvation retry (#558)
# drops one effort level. Both act on what happened; the fit acted on a guess with R² 0.05-0.37.
#
# Consequence worth stating plainly, because it looks like an omission: `effort` no longer affects
# sectioning at all, and is no longer a parameter here. Effort changes how much a model *thinks*,
# not how much room a call needs — and with the envelope no longer in the arithmetic there is
# nothing left for it to scale. `output_ceiling_for_sectioning` still exists and still governs the
# wire `max_tokens` sent on every call; it simply no longer feeds back into input sizing.


def _config_get(key: str, default):
    try:
        cfg = json.loads((Path.home() / ".watchdog" / "config.json").read_text())
    except Exception:
        cfg = {}
    return cfg.get(key, default)


def _resolve_override(key: str, model_default: int) -> int:
    """Config value for `key`, falling back to `model_default` when it is unset or the literal
    `"auto"` sentinel (#321). Only a positive int pins a fixed value; "auto", None, or a missing
    key all mean "use the model-aware default". An absolute override does NOT rescale when the
    extraction model changes — that is the tradeoff a user accepts by pinning a number."""
    val = _config_get(key, None)
    return val if isinstance(val, int) and not isinstance(val, bool) else model_default


def model_defaults(model: str | None, backend: str | None = None,
                   vault: Path | None = None) -> tuple[int, int]:
    """(threshold, budget) est-token sectioning defaults for the extraction stage (#321, #574,
    #555).

    Catalogued numbers only: `model`'s context window sets the size, its `long_context_threshold`
    caps it where exceeding one would double the bill, and its tokenizer ratio converts the result
    into the est-token units the planner packs against. `model`/`backend` are the extraction
    stage's tier/id and backend (None ⇒ default tier / auth-routed Claude backend). There is no
    output-ceiling term — see the comment above `_config_get` for the measurements that removed
    it, and note that `effort` is deliberately no longer a parameter.

    The pricing clamp is applied in REAL tokens, before the ratio division, because that is the
    unit a provider bills and meters in. Clamping the est-token value afterwards would let a
    sub-1.0 ratio divide it back up past the boundary — the same ordering bug D198 found in the
    old output clamp, avoided here by construction rather than by a second clamp.

    Divided by `model_client.tokenizer_ratio` (#574, remeasured #617): `est_tokens`'s chars/4
    heuristic is calibrated against Claude's *old* tokenizer, so on a model whose tokenizer
    produces a different number of real tokens per character the est-token count mis-states what a
    call will actually spend. Dividing the est-token threshold/budget by that ratio keeps the real
    tokens sectioning sends inside the model's real context window. Above 1.0 — only Claude 4.7+,
    at 1.28 — it shrinks the budget. Below 1.0 it widens it, and that is the common case: Claude
    through Sonnet 4.6 at 0.93, Gemini at 0.91, GPT-5.x at 0.80, DeepSeek V4 at 0.81, i.e. chars/4
    over-estimates most real tokenizers on this corpus. Widening is safe because
    `_THRESHOLD_FRACTION` leaves 40% of the window unused regardless. Only an uncatalogued id
    resolves to 1.0 and leaves this a no-op.

    `vault` (#606 Part B), when given, is passed straight through to `tokenizer_ratio` so it can
    prefer this vault's own empirically-measured ratio over the static catalog constant once
    enough matching history has accumulated — see `tokenizer_ratio`'s own docstring. `section.run`
    passes its own `vault` argument here automatically, so the real ingest path benefits without
    any caller change; a caller with no vault context (e.g. `watchdog configure`'s preview) leaves
    this `None` and gets the catalog ratio, exactly as before.

    `_BUDGET_FRACTION` is deliberately half `_THRESHOLD_FRACTION`, so a document just over the
    threshold splits into two sections rather than one section plus a sliver."""
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
        model: str | None = None, backend: str | None = None) -> dict:
    """Plan sections for a document.

    Normally threshold-gated: documents at/under `section_token_threshold` are not
    sectioned. Pass `force_budget` to always section (used as a fallback when
    whole-document extraction overruns the model's output ceiling) — the per-section
    budget is capped at half the document so a splittable document yields ≥2 sections.

    `model`/`backend` are the extraction stage's model (tier name or raw id) and backend, used to
    derive the context-window-aware threshold and budget (#321, #555); config values override the
    derived defaults. Both are irrelevant on the `force_budget` path, which sets its own small
    budget. `vault` (already this function's own first argument) is additionally passed to
    `model_defaults` so the tokenizer-ratio correction can prefer this vault's own calibrated ratio
    over the static catalog constant when enough history is available (#606 Part B).
    """
    queue_file = vault / ".watchdog" / "queue" / f"{sha256}.json"
    if not queue_file.exists():
        return {"error": f"queue file not found for sha256 {sha256}"}

    queue = json.loads(queue_file.read_text(encoding="utf-8"))
    pages = queue.get("pages", [])
    page_count = queue.get("page_count") or len(pages)
    total_tokens = est_tokens_from_pages(pages)

    default_threshold, default_budget = model_defaults(model, backend, vault)
    threshold = _resolve_override("section_token_threshold", default_threshold)
    budget = _resolve_override("section_token_budget", default_budget)
    default_overlap = max(1, budget * _OVERLAP_NUMERATOR // _OVERLAP_DENOMINATOR)
    overlap_tokens = _config_get("section_overlap_tokens", default_overlap)

    if force_budget is not None:
        budget = min(force_budget, max(1, total_tokens // 2))   # guarantee ≥2 sections
        overlap_tokens = min(overlap_tokens, max(0, budget // 4))
    elif total_tokens <= threshold:
        return {"sectioned": False, "page_count": page_count, "est_tokens": total_tokens}

    tmp = vault / ".watchdog" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    sections = []

    if page_count > 1 and len(pages) > 1:
        # Paginated: pack pages into sections greedily against their own token counts (#596), so a
        # dense stretch gets fewer pages and a sparse one more, instead of every section getting
        # the same page count cut from the document-wide average.
        by_num = {p.get("page"): p.get("markdown", "") for p in pages}
        page_tokens = [est_tokens(by_num.get(n, "")) for n in range(1, page_count + 1)]
        for idx, (start, end) in enumerate(plan_ranges(page_tokens, budget, overlap_tokens), start=1):
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
        windows = char_windows(len(text), budget * _CHARS_PER_TOKEN, overlap_tokens * _CHARS_PER_TOKEN)
        for idx, (cstart, cend) in enumerate(windows, start=1):
            path = tmp / f"section_{sha256}_{idx:02d}.md"
            path.write_text(text[cstart:cend], encoding="utf-8")
            sections.append({
                "index": idx,
                "label": f"part {idx} of {len(windows)}",
                "paginated": False,
                "pages_path": str(path.relative_to(vault)),
            })

    return {"sectioned": True, "page_count": page_count,
            "est_tokens": total_tokens, "sections": sections}


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("Usage: watchdog section-plan <sha256>")
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")
    result = run(vault, sys.argv[1])
    if "error" in result:
        sys.exit(f"Error: {result['error']}")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
