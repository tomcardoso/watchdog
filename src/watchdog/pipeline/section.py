"""
Watchdog section planner — split a large queued document into sections for
sectioned (sequential) extraction.

Large documents don't fit comfortably in one extraction subagent's context, and
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

DEFAULT_TOKEN_THRESHOLD = 120_000   # est tokens; at/under this, no sectioning
DEFAULT_TOKEN_BUDGET = 60_000       # target est tokens per section
DEFAULT_OVERLAP_TOKENS = 4_000      # est-token overlap between consecutive sections
_CHARS_PER_TOKEN = 4                # cheap heuristic


def _config_get(key: str, default):
    try:
        cfg = json.loads((Path.home() / ".watchdog" / "config.json").read_text())
    except Exception:
        cfg = {}
    return cfg.get(key, default)


def section_token_threshold() -> int:
    """Estimated-token count at/under which a document is not sectioned."""
    return _config_get("section_token_threshold", DEFAULT_TOKEN_THRESHOLD)


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


def run(vault: Path, sha256: str) -> dict:
    queue_file = vault / ".watchdog" / "queue" / f"{sha256}.json"
    if not queue_file.exists():
        return {"error": f"queue file not found for sha256 {sha256}"}

    queue = json.loads(queue_file.read_text(encoding="utf-8"))
    pages = queue.get("pages", [])
    page_count = queue.get("page_count") or len(pages)
    total_tokens = est_tokens_from_pages(pages)

    threshold = section_token_threshold()
    budget = _config_get("section_token_budget", DEFAULT_TOKEN_BUDGET)
    overlap_tokens = _config_get("section_overlap_tokens", DEFAULT_OVERLAP_TOKENS)

    if total_tokens <= threshold:
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
