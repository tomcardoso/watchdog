"""Tier 0 candidate harvest (#361/D123).

Benchmark hand-scoring found extraction misses "buried" facts — a single sentence after a
table, a table row, a one-line disclosure — even on strong models, and cheap models miss most
of them. Nearly every miss is anchored by a money figure, date, percentage, proper noun, or
court file number, all of which a deterministic pass or a small local NER model can find
without an LLM. This module harvests those candidate spans from the chewed text; the
orchestrator renders them into a per-page checklist injected into the extraction prompt
(`prompts.build_extract_prompt`/`build_section_prompt`), converting recall ("notice it") into
verification ("here it is — is it material?").

Deterministic harvesting (`harvest`) is pure regex, no I/O. `harvest_entities` additionally
loads the optional local GLiNER NER model — import-guarded and wrapped so a missing package or
any model failure degrades to an empty list rather than breaking ingest.
"""

import contextlib
import io
import os
import re
import threading
import warnings

# ── deterministic patterns ────────────────────────────────────────────────────────────────

_PAGE_MARKER_RE = re.compile(r"<!-- PAGE (\d+) -->")

# Either a comma-grouped integer part with an optional decimal, or a plain (ungrouped) number
# with an optional decimal — covers both "66,671" and "590"/"66.7" without matching a bare
# short run of digits mid-word.
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"

# Word scale ("million") or a single-letter scale ("M") with no digit/letter run-on, e.g.
# "$85.9M" but not the "M" inside "$590Mile".
_SCALE = r"(?:\s*(?:million|billion|thousand)|\s*[MBK](?![A-Za-z]))?"
_MONEY_RE = re.compile(
    rf"\$\s*\((?:{_NUM})\){_SCALE}"    # $(66,671) — parens are paired, not independently optional
    rf"|\$\s*(?:{_NUM}){_SCALE}",      # $66.7 million, $590, $1,412, $85.9M, $ 4,903
    re.IGNORECASE,
)

# Bare (no $) comma-grouped figures of 4+ digits, with an optional decimal tail — table numbers
# in financial statements. The comma-group requirement (at least one ",\d{3}" group) is what
# guarantees 4+ digits, so a bare 4-digit year like "2020" never matches here.
_FIGURE_RE = re.compile(r"\(?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?")

_PERCENT_RE = re.compile(r"\d+(?:\.\d+)?%")

_MONTHS = [
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December",
    # Bilingual Canadian documents also carry French month names.
    "janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
    "octobre", "novembre", "décembre",
]
_MONTH_ALT = "|".join(_MONTHS)
# English abbreviated forms, e.g. "Sep. 26, 2021" / "Sept 26, 2021" — "Sept" ordered before "Sep"
# so the four-letter form matches whole rather than leaving a stray "t".
_MONTHS_ABBR = ["Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sept", "Sep", "Oct", "Nov", "Dec"]
_MONTH_ABBR_ALT = "|".join(_MONTHS_ABBR)
_MONTH_TOKEN = rf"(?:{_MONTH_ALT}|(?:{_MONTH_ABBR_ALT})\.?)"
_DATE_RE = re.compile(
    rf"\b{_MONTH_TOKEN}\s+\d{{1,2}},?\s+\d{{4}}\b"         # January 30, 2021 / Sep. 26, 2021
    rf"|\b\d{{1,2}}\s+{_MONTH_TOKEN}\s+\d{{4}}\b"          # 30 January 2021 / 17 mars 2021
    rf"|\b\d{{4}}-\d{{2}}-\d{{2}}\b",                      # 2021-01-30
    re.IGNORECASE,
)

# Court file number shapes, e.g. CV-21-00656040-00CL.
_DOCKET_RE = re.compile(r"\b[A-Z]{1,4}-\d{2}-\d{4,}(?:-[A-Z0-9]+)*\b")

# Bare small numbers inside a markdown table row (Docling's rendering of table cells) — the
# comma-grouped _FIGURE_RE above only catches 4+ digit figures, so a bare "223" or "590" in a
# financial-note table row is otherwise never harvested. Table-context only: the same digits in
# prose are noise (see the `|`-count gate in `_harvest_table_figures`).
_TABLE_NUM_RE = re.compile(r"\b\d{1,6}(?:\.\d+)?\b")
# A standalone 4-digit year is a table column header ("2020"), not a figure worth flagging.
_TABLE_YEAR_RE = re.compile(r"\A(?:18|19|20)\d\d\Z")

_KIND_PRIORITY = ["money", "docket", "date", "percent", "figure",
                  "person", "organization", "location"]
_PAGE_CAP = 80
_DOC_REPEAT_CAP = 3   # a (kind, value) recurring on more than this many pages is page furniture


def _split_marked(text: str) -> list[tuple[int | None, str]]:
    """Split extraction-ready text on `<!-- PAGE N -->` markers into (page, body) chunks, in
    document order. Text before the first marker has no page number."""
    parts = _PAGE_MARKER_RE.split(text)
    pages: list[tuple[int | None, str]] = []
    if parts[0]:
        pages.append((None, parts[0]))
    for i in range(1, len(parts), 2):
        pages.append((int(parts[i]), parts[i + 1] if i + 1 < len(parts) else ""))
    return pages


def split_pages(text: str) -> dict[int, str]:
    """Page-number -> text map for callers that need per-page text rather than a flat
    candidate list (`harvest_entities`). Text before the first marker has no page to attribute
    a candidate to, so it's dropped here."""
    return {page: body for page, body in _split_marked(text) if page is not None}


def _normalize(kind: str, value: str) -> str:
    """Dedupe key for a candidate value: names are case/whitespace-folded (GLiNER can return
    the same name with different casing or spacing across mentions); figures/dates/percentages/
    dockets keep their digits and punctuation exactly, since a comma or a parenthesis there is
    part of the value, not noise."""
    if kind in ("person", "organization", "location"):
        return " ".join(value.split()).casefold()
    return value.strip()


def _harvest_table_figures(text: str, page: int | None, other_spans: list[tuple[int, int]]) -> list[dict]:
    """Bare small numbers from markdown table rows (lines with 2+ `|`), excluding spans already
    covered by another kind's match on that line and standalone 4-digit years (column headers).
    Prose lines (no `|`) are never touched — this is table-context only."""
    cands = []
    offset = 0
    for line in text.splitlines(keepends=True):
        if line.count("|") >= 2:
            for m in _TABLE_NUM_RE.finditer(line):
                if _TABLE_YEAR_RE.match(m.group()):
                    continue
                start, end = offset + m.start(), offset + m.end()
                if any(start < e and s < end for s, e in other_spans):
                    continue
                cands.append({"page": page, "kind": "figure", "value": m.group()})
        offset += len(line)
    return cands


def _harvest_page(text: str, page: int | None) -> list[dict]:
    """Raw (undeduped) deterministic candidates for one page's text."""
    cands = []
    money_spans = []
    other_spans = []   # money/figure/percent/date/docket spans — for table-figure exclusion
    for m in _MONEY_RE.finditer(text):
        money_spans.append(m.span())
        other_spans.append(m.span())
        cands.append({"page": page, "kind": "money", "value": m.group()})
    for m in _FIGURE_RE.finditer(text):
        # A bare figure that's really the digits inside a money match (e.g. the "66,671"
        # inside "$(66,671)") is not a separate candidate.
        if any(m.start() < e and s < m.end() for s, e in money_spans):
            continue
        other_spans.append(m.span())
        cands.append({"page": page, "kind": "figure", "value": m.group()})
    for m in _PERCENT_RE.finditer(text):
        other_spans.append(m.span())
        cands.append({"page": page, "kind": "percent", "value": m.group()})
    for m in _DATE_RE.finditer(text):
        other_spans.append(m.span())
        cands.append({"page": page, "kind": "date", "value": m.group()})
    for m in _DOCKET_RE.finditer(text):
        other_spans.append(m.span())
        cands.append({"page": page, "kind": "docket", "value": m.group()})
    cands.extend(_harvest_table_figures(text, page, other_spans))
    return cands


def _dedupe_and_cap(candidates: list[dict]) -> list[dict]:
    """Within a page, dedupe by (kind, normalized value). Across the document, a (kind, value)
    recurring on more than `_DOC_REPEAT_CAP` pages (a running header/footer) keeps only its
    first occurrences and drops the rest. Finally each page is capped at `_PAGE_CAP`,
    truncating lowest-priority kinds first — no cap across the whole document."""
    pages_order: list[int | None] = []
    by_page: dict[int | None, list[dict]] = {}
    for c in candidates:
        p = c["page"]
        if p not in by_page:
            by_page[p] = []
            pages_order.append(p)
        by_page[p].append(c)

    for p in pages_order:
        seen: set[tuple[str, str]] = set()
        deduped = []
        for c in by_page[p]:
            key = (c["kind"], _normalize(c["kind"], c["value"]))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(c)
        by_page[p] = deduped

    occurrences: dict[tuple[str, str], int] = {}
    for p in pages_order:
        kept = []
        for c in by_page[p]:
            key = (c["kind"], _normalize(c["kind"], c["value"]))
            n = occurrences.get(key, 0)
            if n >= _DOC_REPEAT_CAP:
                continue
            occurrences[key] = n + 1
            kept.append(c)
        by_page[p] = kept

    result = []
    for p in pages_order:
        ranked = sorted(by_page[p], key=lambda c: _KIND_PRIORITY.index(c["kind"]))
        result.extend(ranked[:_PAGE_CAP])
    return result


def harvest(text: str) -> list[dict]:
    """Deterministic candidates (money, figure, percent, date, docket) from extraction-ready
    text carrying `<!-- PAGE N -->` markers. Each candidate is `{"page", "kind", "value"}`."""
    candidates = []
    for page, body in _split_marked(text):
        candidates.extend(_harvest_page(body, page))
    return _dedupe_and_cap(candidates)


# ── GLiNER (optional local NER) ───────────────────────────────────────────────────────────

_GLINER_LABELS = ["person", "organization", "location"]
_GLINER_THRESHOLD = 0.4
# Approximate word-count window standing in for GLiNER's ~384-token input limit, with overlap
# so a name straddling a window boundary is still caught whole by the neighbouring window.
# 200 words, not 384: subword tokenization inflates words to tokens by ~1.5–2x on dense text,
# and GLiNER silently truncates over-long inputs — verified on the benchmark corpus, where
# 300-word windows still drew "truncated to 384" warnings on table-heavy pages.
_GLINER_WINDOW_WORDS = 200
_GLINER_WINDOW_OVERLAP = 50

_gliner_model = None   # module-level singleton — loaded once per process
_gliner_lock = threading.Lock()  # serializes the one-time load; see _load_gliner


@contextlib.contextmanager
def _quiet_stderr():
    """Suppress stderr at the OS file-descriptor level for the duration of the block, on top of
    whatever Python-level suppression (`redirect_stderr`, `warnings` filters) the caller also has
    active. `contextlib.redirect_stderr` only swaps Python's `sys.stderr` object; huggingface_hub's
    own logging handler doesn't consult it — it holds a direct reference to the real stream,
    captured at import time — so its "unauthenticated requests" notice writes straight past
    redirect_stderr regardless of the warnings/logging filters in effect (#456). An fd-level
    dup2 catches that write no matter which mechanism produced it."""
    saved_fd = os.dup(2)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull_fd, 2)
        yield
    finally:
        os.dup2(saved_fd, 2)
        os.close(devnull_fd)
        os.close(saved_fd)


def _load_gliner():
    global _gliner_model
    if _gliner_model is not None:
        return _gliner_model
    # harvest_entities runs concurrently across documents via asyncio.to_thread, so without this
    # lock two threads can both see `_gliner_model is None` and call `from_pretrained()` at once —
    # racing on the global `ssl` module patch below (one thread's `finally` can unpatch it while
    # the other's download is still in flight) and on the stderr suppression a few lines down,
    # which is exactly how the HF Hub "unauthenticated requests" notice and torch's jit.script
    # FutureWarning leaked past it once (#456 was about the separate predict()-time warning, not
    # this load race).
    with _gliner_lock:
        if _gliner_model is not None:
            return _gliner_model
        # Verify the model download via the OS trust store rather than certifi's bundled list —
        # same corporate-proxy fix as D122. `inject_into_ssl` patches the global `ssl` module,
        # so it's scoped to just this one-time load via `finally` (harmless no-op when cached).
        import truststore
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        # Ignored permanently, not just for this load call: harvest_entities runs concurrently
        # across documents via asyncio.to_thread, and a scoped `warnings.catch_warnings()` context
        # per predict() call (the previous approach) is documented as not thread-safe — concurrent
        # enter/exit pairs race on the shared global filter list, letting a truncation warning from
        # one thread leak out when another thread's context has already restored it (#456). A
        # permanent filter, set once here before any concurrent predict() call can start, needs no
        # restore and so can't race.
        warnings.filterwarnings("ignore", category=UserWarning, module=r"gliner(\..*)?")
        truststore.inject_into_ssl()
        try:
            from gliner import GLiNER
            with warnings.catch_warnings(), contextlib.redirect_stderr(io.StringIO()), _quiet_stderr():
                warnings.simplefilter("ignore", UserWarning)
                _gliner_model = GLiNER.from_pretrained("urchade/gliner_multi-v2.1")
        finally:
            truststore.extract_from_ssl()
    return _gliner_model


def _chunk_words(text: str) -> list[str]:
    words = text.split()
    if not words:
        return []
    step = max(1, _GLINER_WINDOW_WORDS - _GLINER_WINDOW_OVERLAP)
    return [" ".join(words[i:i + _GLINER_WINDOW_WORDS]) for i in range(0, len(words), step)]


def harvest_entities(text_by_page: dict[int, str]) -> list[dict]:
    """Person/organization/location candidates via the local GLiNER model (mandatory dependency
    since D223). Returns `[]` silently if the model fails to load or run for any reason — an
    uncatalogued install (an editable checkout missing this dependency, a corrupted model cache)
    degrades to the deterministic-only harvest rather than failing ingest."""
    try:
        model = _load_gliner()
    except Exception:
        return []

    try:
        candidates = []
        # gliner warns on stderr when a window still exceeds its token limit — truncation is
        # already mitigated by the conservative window size above; the permanent filter set in
        # _load_gliner keeps it out of ingest's terminal output without a per-call context
        # manager (concurrent documents call this via asyncio.to_thread, see _load_gliner).
        for page, text in text_by_page.items():
            for chunk in _chunk_words(text):
                for ent in model.predict_entities(chunk, _GLINER_LABELS,
                                                   threshold=_GLINER_THRESHOLD):
                    candidates.append({"page": page, "kind": ent["label"], "value": ent["text"]})
        return _dedupe_and_cap(candidates)
    except Exception:
        return []


def format_checklist(candidates: list[dict]) -> str:
    """Compact, token-lean checklist grouped by page, one line per page:
    `p.52: [money] $590 · [percent] 2.0% · [person] Robert Haché`. Page `None` renders as
    `p.?:`. Empty input renders as an empty string."""
    if not candidates:
        return ""
    pages_order: list[int | None] = []
    by_page: dict[int | None, list[dict]] = {}
    for c in candidates:
        p = c["page"]
        if p not in by_page:
            by_page[p] = []
            pages_order.append(p)
        by_page[p].append(c)

    lines = []
    for p in pages_order:
        label = f"p.{p}" if p is not None else "p.?"
        items = " · ".join(f"[{c['kind']}] {c['value']}" for c in by_page[p])
        lines.append(f"{label}: {items}")
    return "\n".join(lines)
