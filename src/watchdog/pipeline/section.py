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
  * paginated documents (page_count > 1) split on **page boundaries** —
    pages-per-section derived from the document's average density — so page
    citations are preserved.
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
# swallowing it. `_OVERLAP_NUMERATOR/_OVERLAP_DENOMINATOR` are chosen so Claude's default budget
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

# Output-ceiling-aware sectioning (#343): the input-window fractions above bound how much a call
# *reads* but say nothing about how much it can *write*. A backend that enforces a fixed
# `max_tokens` and can't paginate its output (openai, gemini) will truncate a document that fits
# the input window comfortably but needs more output than the ceiling allows. So the threshold and
# budget are additionally capped by an output-derived input ceiling: the safe output budget
# converted back to an input-token cap by inverting the measured *visible*-output relationship
# (below). Backends that paginate their output (claude-api, deepseek) or have no ceiling
# (claude-agent-sdk) return None from `output_ceiling_for_sectioning` and keep the pure
# input-window defaults.
#
# Visible output (chain-of-thought reasoning excluded — #539's `reasoning_tokens` telemetry
# separates them) is **affine** in input size, not proportional to it: a fixed per-call cost plus a
# marginal rate, not a pure ratio. Fitted per reasoning effort on openai:gpt-5.4-mini (#542):
#
#   low:    1,112 + 0.199 × input tokens   (R²=0.23, reasoning 4% of total output)
#   medium:   429 + 0.213 × input tokens   (R²=0.39, reasoning 87% of total output)
#   high:   1,478 + 0.200 × input tokens   (R²=0.41, reasoning 94% of total output)
#
# The marginal rate is stable at ~0.20 across every effort level — that stability is the reliable
# part of the fit, so it's the constant to trust. The fixed cost varies 429–1,478 across effort
# levels; 1,000 is used here as a round, roughly-central estimate rather than the low end (which
# would undersize the budget for higher-effort calls) or the high end (which would oversize it for
# low-effort ones). A pure ratio (the pre-#542 model) divides by a shrinking effective density as
# sections get smaller, which makes measured density appear to *rise* as sections shrink — that
# artifact made over-sectioning self-justifying. Inverting the affine relation instead — solving
# `ceiling × safe_fraction = fixed_cost + marginal_rate × input` for `input` — removes it.
_OUTPUT_FIXED_COST = 1_000         # est. fixed visible-output tokens per call, independent of input size (#542)
_OUTPUT_MARGINAL_RATE = 0.20       # est. marginal visible-output tokens per input token — stable across effort (#542)
_MIN_OUTPUT_CAPPED_BUDGET = 1_000  # floor so a small/pathological ceiling can't invert to a zero or negative budget
_OUTPUT_SAFE_FRACTION = 0.7        # fraction of the output ceiling to target, leaving headroom for variance/CoT


def _invert_output_ceiling(ceiling: int) -> int:
    """Max input tokens a call can take and still keep its visible output under `ceiling *
    _OUTPUT_SAFE_FRACTION`, inverting the affine visible-output fit above. Floored at
    `_MIN_OUTPUT_CAPPED_BUDGET` so a ceiling too small to clear the fixed cost doesn't invert to
    <= 0."""
    return max(_MIN_OUTPUT_CAPPED_BUDGET,
               int((ceiling * _OUTPUT_SAFE_FRACTION - _OUTPUT_FIXED_COST) / _OUTPUT_MARGINAL_RATE))


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


def model_defaults(model: str | None, backend: str | None = None) -> tuple[int, int]:
    """(threshold, budget) est-token sectioning defaults for the extraction stage (#321, #343,
    #574).

    Derived from `model`'s context window (the input side); then, for a backend that enforces a
    fixed output ceiling it can't paginate past (openai/gemini — #343), additionally capped so a
    call's expected output stays under that ceiling. `model`/`backend` are the extraction stage's
    tier/id and backend (None ⇒ default tier / auth-routed Claude backend).

    Finally divided by `model_client.tokenizer_ratio` (#574): `est_tokens`'s chars/4 heuristic is
    calibrated against Claude's *old* tokenizer, so on a model whose tokenizer produces more real
    tokens per character (Claude 4.7+ — Opus 4.8, Sonnet 5) an est-token count undercounts the
    real tokens a call will spend. Shrinking the est-token threshold/budget by that same ratio
    keeps the real tokens sectioning actually sends under the model's real context window. A
    1.0 ratio (every model through Sonnet 4.6, and every non-Anthropic provider) leaves this a
    no-op — the historical 120K/60K Claude defaults are unchanged.

    `threshold` and `budget` are checked against *different* tasks' ceilings — a document at or
    under threshold runs whole-document (`extract`); once sectioned, each section runs as its own
    `extract-section` call — so each is capped against its own task's ceiling rather than both
    sharing one lookup. `_TASK_MAX_TOKENS` happens to give both the same value today, so this was
    previously harmless as a single shared lookup, but it was checking the wrong capability for
    `budget` specifically, not merely a redundant one.

    Unlike the input-window fractions above (`_BUDGET_FRACTION` is deliberately half
    `_THRESHOLD_FRACTION`, so a document just over threshold still splits into two sections),
    `budget` is *not* halved again when the output ceiling caps it (#490's over-sectioning
    finding): the ceiling-derived max-input already represents the full safe amount a single call
    can handle, for a whole document or for one section alike — halving it a second time was
    inherited from the input-window path's reasoning without that reasoning actually applying
    here, and cost a straight 2x in section count for no added safety."""
    from watchdog import model_client
    window = model_client.context_window(model, backend)
    threshold = int(window * _THRESHOLD_FRACTION)
    budget = int(window * _BUDGET_FRACTION)
    extract_ceiling = model_client.output_ceiling_for_sectioning("extract", backend, model)
    if extract_ceiling is not None:
        threshold = min(threshold, _invert_output_ceiling(extract_ceiling))
    section_ceiling = model_client.output_ceiling_for_sectioning("extract-section", backend, model)
    if section_ceiling is not None:
        budget = min(budget, _invert_output_ceiling(section_ceiling))
    ratio = model_client.tokenizer_ratio(model, backend)
    threshold = int(threshold / ratio)
    budget = max(1, int(budget / ratio))
    return threshold, budget


def section_token_threshold(model: str | None = None, backend: str | None = None) -> int:
    """Estimated-token count at/under which a document is not sectioned.

    Model-aware by default: derived from the extraction model's context window (#321) and, for a
    fixed-output-ceiling backend, its output cap (#343). An explicit integer
    `section_token_threshold` in config overrides it, as an advanced escape hatch; the `"auto"`
    sentinel (or an unset key) keeps the model-aware default."""
    default_threshold, _ = model_defaults(model, backend)
    return _resolve_override("section_token_threshold", default_threshold)


def est_tokens(text: str) -> int:
    """Cheap estimate of the token count of a string (~4 chars/token)."""
    return len(text or "") // _CHARS_PER_TOKEN


def est_tokens_from_pages(pages: list) -> int:
    return sum(est_tokens(p.get("markdown", "")) for p in pages)


def plan_ranges(page_count: int, size: int, overlap: int) -> list[tuple[int, int]]:
    """Return 1-based inclusive (start, end) page ranges covering the document.

    Consecutive ranges overlap by `overlap` pages so a table straddling a
    boundary is wholly visible in at least one section.
    """
    if page_count <= 0:
        return []
    size = max(1, size)
    overlap = max(0, min(overlap, size - 1))  # guard against non-advancing ranges
    ranges: list[tuple[int, int]] = []
    start = 1
    while start <= page_count:
        end = min(start + size - 1, page_count)
        ranges.append((start, end))
        if end >= page_count:
            break
        start = end - overlap + 1
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
    derive the context-window-aware threshold and budget (#321) and, for a fixed-output-ceiling
    backend, the output-aware cap (#343); config values override the derived defaults. Both are
    irrelevant on the `force_budget` path, which sets its own small budget.
    """
    queue_file = vault / ".watchdog" / "queue" / f"{sha256}.json"
    if not queue_file.exists():
        return {"error": f"queue file not found for sha256 {sha256}"}

    queue = json.loads(queue_file.read_text(encoding="utf-8"))
    pages = queue.get("pages", [])
    page_count = queue.get("page_count") or len(pages)
    total_tokens = est_tokens_from_pages(pages)

    default_threshold, default_budget = model_defaults(model, backend)
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
        # Paginated: derive pages-per-section from average density, split on pages.
        avg = max(1, total_tokens // page_count)
        pages_per = max(1, round(budget / avg))
        overlap_pages = min(pages_per - 1, max(0, round(overlap_tokens / avg)))
        by_num = {p.get("page"): p.get("markdown", "") for p in pages}
        for idx, (start, end) in enumerate(plan_ranges(page_count, pages_per, overlap_pages), start=1):
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
