"""Deterministic quote resolution against the morgue text (#267, #529).

D22 deferred checking that a captured ``key_facts[].quote`` actually appears on its cited
page to the model-graded #106 evaluator, since the Docling text was discarded after
extraction. Post-D26 the page text is already in hand at post-flight time (read here from
the chew-time queue descriptor, the same source `write_vault` uses to write the morgue
markdown) — so this became a deterministic substring/fuzzy match, not a model call.

#529/D170 went a step further: the model no longer emits the quote at all, only a short
``quote_locator`` (the first several words of the source sentence). ``resolve_quote`` finds
that locator in the real page text and expands it into the full sentence in Python, so the
rendered quote is source text by construction rather than a model retyping checked after the
fact. ``verify_quote`` (substring/fuzzy match against a model-supplied ``quote``) is kept only
for the legacy path: an extraction staged before this change, re-run through post-flight by
`watchdog bark` with no ``quote_locator`` on its key_facts.

Annotation only, never a gate (same posture as the D32 coverage warning): a locator that can't
be resolved is flagged in the rendered note and logged as a WARN, but never blocks the document.
"""

import html.entities as html_entities
import re
import unicodedata

_WS_RE = re.compile(r"\s+")
_HYPHEN_BREAK_RE = re.compile(r"-\s*\n\s*")
_NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WORD_CHAR_RE = re.compile(r"\w", re.UNICODE)
_SPACE_CHAR_RE = re.compile(r"\s", re.UNICODE)
_SENTENCE_END_CHARS = ".!?"

# A lone page-number stamp ("- 3 -", "9") on its own line at the very start or end of a page's
# text — chew furniture, not sentence content. Left alone it sits literally between two halves
# of a page-spanning sentence when they're joined for matching (#560: "...application for" +
# "3" + "a transfer..."), breaking the very match the join exists to recover. Requires nothing
# but whitespace/dashes around the digits on that line, so a genuine numbered clause ("1. Some
# clause...") is never touched — real content always has more than a number on the line.
_PAGE_FURNITURE_LEAD_RE = re.compile(r"^[\s\-–—]*\d{1,4}[\s\-–—]*\n+")
_PAGE_FURNITURE_TAIL_RE = re.compile(r"\n+[\s\-–—]*\d{1,4}[\s\-–—]*$")

# A next line opening with one of these is a new markdown block (table row, heading, blockquote,
# list bullet), never the continuation of a wrapped sentence.
_BLOCK_MARKER_RE = re.compile(r"[|#>*+\-]|\d+[.)]")
_WRAP_COLUMN = 60               # a line at least this long, ending mid-sentence, looks column-wrapped

_SENTENCE_BACK_WINDOW = 300     # max chars scanned backward for a sentence start
_SENTENCE_FORWARD_WINDOW = 400  # max chars scanned forward (past the match) for a sentence end
_QUOTE_CAP = 500                # max rendered quote length, in characters

# An ELIDED quote — one where the model cut the middle and kept both ends:
#   "the payment due on March 30, 2021 … is stayed and suspended"
# Something must follow the ellipsis, so a merely truncated quote ("the payment
# due…") is not an elision and keeps its pre-#630 behaviour. The trailing side
# tests for any non-space rather than a word character: a cut very often resumes
# on a figure, and "the total was … $842,018.34" resumes on "$", which is not a
# word character. Leading punctuation is consumed by the character classes, and
# a part that turns out to be punctuation-only is rejected by the length floor.
_ELISION_RE = re.compile(r"(?<=\w)[\s\"'’”)\]]*(?:…|\.\.\.)[\s\"'‘“(\[]*(?=\S)")
_MIN_ELISION_PART = 12   # normalized chars; a shorter fragment matches by luck, not by content
_MAX_ELISION_GAP = 600   # normalized chars the model may cut out between two kept parts


_ENTITY_RE = re.compile(r"&(?:#\d+|#[xX][0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});")


def _entity_char(token: str) -> str | None:
    """The character `token` denotes, or None if it is not actually an entity.

    Deliberately stricter than `html.unescape`, which resolves the longest
    KNOWN prefix and leaves the rest: it turns `&notanentity;` into `¬anentity;`
    because `&not` is real. Silently mangling text that merely looks like an
    entity would lose real document content, so only a token that resolves
    whole is treated as one.
    """
    body = token[1:-1]
    if body.startswith("#"):
        try:
            code = int(body[2:], 16) if body[1:2] in "xX" else int(body[1:])
        except ValueError:
            return None
        return chr(code) if 0 < code < 0x110000 else None
    resolved = html_entities.html5.get(body + ";")
    return resolved if resolved else None


def _unescape_with_map(text: str) -> tuple[str, list[int]]:
    """HTML-unescape `text`, mapping each output character back to its input index.

    The chew emits `&amp;` where the page prints `&`, so without this a quote
    reading "Payroll & Benefits" cannot match its own page. Entities change
    length, which is why this returns a map rather than just a string — the
    callers that resolve a match back into real page text need it.
    """
    out: list[str] = []
    origin: list[int] = []
    at = 0
    for m in _ENTITY_RE.finditer(text):
        if m.start() < at:
            continue
        ch = _entity_char(m.group(0))
        if ch is None:
            continue
        for i in range(at, m.start()):
            out.append(text[i])
            origin.append(i)
        for c in ch:
            out.append(c)
            origin.append(m.start())
        at = m.end()
    for i in range(at, len(text)):
        out.append(text[i])
        origin.append(i)
    return "".join(out), origin


def _normalize(text: str, *, collapse_spaces: bool = True) -> str:
    """Case/whitespace/punctuation/hyphenation/accent-insensitive form for fuzzy matching.

    Deliberately aggressive: a false "unverified" flag on a real quote erodes trust in the
    flag itself more than an occasional false-positive match would.

    `collapse_spaces=False` discards whitespace outright instead of collapsing runs to a
    single space. That is what a checker comparing text against CHEWED MARKDOWN needs, because
    the conversion splits words with spaces: the pension order's header converts to
    "WEDNESDAY, THE 17 th", which no amount of collapsing will reconcile with the page's
    printed "17th". Discarding whitespace is safe applied to both sides — a despaced span of a
    hundred characters does not match by accident — but it is looser, so the pipeline's own
    quote checking keeps the default.
    """
    text, _ = _unescape_with_map(text)
    text = text.replace("­", "")          # soft hyphen
    text = _HYPHEN_BREAK_RE.sub("", text)       # word broken across a line by hyphenation
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _NON_WORD_RE.sub("", text.lower())
    if not collapse_spaces:
        return _WS_RE.sub("", text)
    return _WS_RE.sub(" ", text).strip()


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Same output as `_normalize`, plus a list mapping each output character's index back to
    its index in the original `text` — what `resolve_quote` needs to translate a match found in
    normalized space back into a span of the real, un-normalized page text.

    Implemented char-by-char (rather than `_normalize`'s whole-string regex passes) so each
    surviving character can carry its origin index along with it. The two functions must never
    drift apart — `test_quote_verify.py` asserts byte-for-byte equivalence with `_normalize`
    across a battery of gnarly inputs (accents, hyphen line breaks, soft hyphens, punctuation
    and whitespace runs, empty string).
    """
    # Entities are expanded first and their map composed with this function's,
    # so `origin` still refers to indices in the caller's original string.
    text, entity_origin = _unescape_with_map(text)

    skip = [False] * len(text)
    for m in _HYPHEN_BREAK_RE.finditer(text):
        for i in range(m.start(), m.end()):
            skip[i] = True

    chars: list[str] = []
    origin: list[int] = []
    for i, ch in enumerate(text):
        if skip[i] or ch == "­":   # hyphen-linebreak span, or a soft hyphen
            continue
        for dch in unicodedata.normalize("NFKD", ch):
            if unicodedata.combining(dch):
                continue
            dch = dch.lower()
            if _WORD_CHAR_RE.match(dch) or _SPACE_CHAR_RE.match(dch):
                chars.append(dch)
                origin.append(i)

    out_chars: list[str] = []
    out_idx: list[int] = []
    prev_ws = False
    for c, i in zip(chars, origin):
        if _SPACE_CHAR_RE.match(c):
            if not prev_ws and out_chars:
                out_chars.append(" ")
                out_idx.append(i)
            prev_ws = True
        else:
            out_chars.append(c)
            out_idx.append(i)
            prev_ws = False

    while out_chars and out_chars[-1] == " ":
        out_chars.pop()
        out_idx.pop()

    return "".join(out_chars), [entity_origin[i] for i in out_idx]


def _elision_parts(quote: str) -> list[str] | None:
    """Split an elided quote into its kept parts, normalized.

    Returns ``None`` when the quote isn't elided, or when any part is too short
    to carry a match on its own — a two-word fragment would verify against
    almost any page, which is worse than the false negative it fixes.
    """
    if not _ELISION_RE.search(quote):
        return None
    parts = [_normalize(p) for p in _ELISION_RE.split(quote)]
    if len(parts) < 2 or any(len(p) < _MIN_ELISION_PART for p in parts):
        return None
    return parts


def _elided_match(norm_text: str, parts: list[str]) -> bool:
    """True when every part appears in `norm_text`, in order, without the model
    having stitched together fragments from opposite ends of the page.

    Order and gap are both load-bearing. Without them this trades a
    false-negative problem for a false-positive one: any two phrases that happen
    to share a page would "verify" as a quote that was never written.
    """
    idx = 0
    for i, part in enumerate(parts):
        pos = norm_text.find(part, idx)
        if pos < 0:
            return False
        if i and pos - idx > _MAX_ELISION_GAP:
            return False
        idx = pos + len(part)
    return True


def verify_quote(page_texts: dict[int, str], page: int | None, quote: str) -> dict:
    """Check ``quote`` against its cited page, then the page ±1 (OCR noise, page-marker
    drift), and report the outcome.

    Legacy path only (#529): a `quote_locator`-carrying fact is resolved by `resolve_quote`
    instead. This is kept for a pre-#529 extraction staged before the change and re-run through
    post-flight by `watchdog bark`.

    Handles an **elided** quote — one where the model kept both ends and cut the middle with
    an ellipsis (#630). Those used to fail categorically: measured over the archived benchmark
    extractions, elided quotes verified at 0.9% against 92.7% for the rest, and the rejected
    ones were accurate quotations of real provisions.

    Returns ``{"verified": None}`` when there's nothing to check (no page cited, or the
    page text isn't available at all — e.g. a `watchdog bark` re-run with no chew-time
    queue descriptor on disk); ``{"verified": True}`` on an exact substring match;
    ``{"verified": True, "found_page": N}`` when only a normalized match was found, and on
    a different page than cited; ``{"verified": False}`` when page text *is* available but
    no match was found anywhere searched.
    """
    quote = quote.strip()
    if not quote or page is None or not page_texts:
        return {"verified": None}

    exact_text = page_texts.get(page)
    if exact_text and quote in exact_text:
        return {"verified": True}

    norm_quote = _normalize(quote)
    if not norm_quote:
        return {"verified": None}

    parts = _elision_parts(quote)
    for candidate in (page, page - 1, page + 1):
        text = page_texts.get(candidate)
        if not text:
            continue
        norm_text = _normalize(text)
        # On each page the whole quote is tried first, so the elided form can
        # recover an otherwise-lost match but never override a real one. Pages
        # are still searched cited-first, so an elided match on the cited page
        # is preferred over a whole-quote match on a neighbour — which is the
        # right way round: the model told us which page it read.
        if norm_quote in norm_text or (parts and _elided_match(norm_text, parts)):
            result = {"verified": True}
            if candidate != page:
                result["found_page"] = candidate
            return result

    return {"verified": False}


def _annotate(obj: dict, page_texts: dict[int, str]) -> None:
    quote = (obj.get("quote") or "").strip()
    if not quote:
        return
    result = verify_quote(page_texts, obj.get("page"), quote)
    if result.get("verified") is False:
        obj["quote_verified"] = False
    elif result.get("found_page") is not None:
        obj["quote_verified"] = True
        obj["quote_found_page"] = result["found_page"]


def _sentence_boundary_end(text: str, i: int) -> bool:
    """True if `text[i]` (one of `.!?`) ends a sentence: followed by whitespace or
    end-of-text, and the next non-whitespace character is an uppercase letter or end-of-text.
    Deliberately does not break on "Inc. as monitor" or "Feb. 1, 2021"."""
    n = len(text)
    if i + 1 < n and not text[i + 1].isspace():
        return False
    j = i + 1
    while j < n and text[j].isspace():
        j += 1
    return j >= n or text[j].isupper()


def _is_soft_wrap(text: str, k: int) -> bool:
    """True if the newline at ``text[k]`` is a hard-wrapped break *inside* one sentence rather
    than a real block break — the sentence continues on the next line and expansion should
    scan straight through it.

    Docling's markdown export puts a whole text block on one line, so there a newline is always
    a real boundary. But `preprocess.process_direct_text` passes a `.txt`/`.md` file's raw text
    through as page markdown untouched, and those are routinely hard-wrapped mid-sentence —
    stopping at every newline would truncate most quotes from that path.

    A blank line is always a block break, and so is a next line opening with a markdown block
    marker (`| …` table row, `# …` heading, list bullet) — that is what keeps expansion from
    running out of a sentence and into a table. Past those, the next line continues the sentence
    when it resumes in lowercase, or — for the capitalized continuations that fill legal prose
    (`April 30, 2021,` / `Ernst & Young Inc.`) — when the line it continues was long enough to
    have been wrapped at a column and did not end on sentence-terminating punctuation. A short
    line resuming in caps still stops: that is the shape of an address or signature block, where
    consecutive lines really are separate. Direction-independent, so the backward scan can use
    the same test.
    """
    n = len(text)
    start = k
    while start > 0 and text[start - 1].isspace():
        start -= 1
    end = k + 1
    while end < n and text[end].isspace():
        end += 1
    if text.count("\n", start, end) > 1 or end >= n:
        return False
    if _BLOCK_MARKER_RE.match(text, end):
        return False
    if text[end].islower():
        return True
    line = text[text.rfind("\n", 0, start) + 1:start]
    return len(line) >= _WRAP_COLUMN and not line.rstrip().endswith((".", "!", "?"))


def _snap_forward_to_word(text: str, limit: int, a: int) -> int:
    """The backward scan ran out of window mid-word: advance to the start of the next whole word
    so the quote doesn't open on a fragment (`"rlie delta echo …"`). Only ever called on the
    fall-through path — a real sentence boundary is honoured exactly."""
    if limit <= 0 or text[limit - 1].isspace():
        return limit
    k = limit
    while k < a and not text[k].isspace():
        k += 1
    while k < a and text[k].isspace():
        k += 1
    return k if k < a else limit


def _snap_back_to_word(text: str, limit: int, b: int) -> int:
    """Mirror of `_snap_forward_to_word` for the forward scan's window limit: retreat to the end
    of the previous whole word."""
    if limit >= len(text) or text[limit].isspace():
        return limit
    k = limit
    while k > b and not text[k - 1].isspace():
        k -= 1
    while k > b and text[k - 1].isspace():
        k -= 1
    return k if k > b else limit


def _sentence_start(text: str, a: int) -> int:
    """Scan backward from `a`, at most `_SENTENCE_BACK_WINDOW` chars, for the start of the
    enclosing sentence: just after a sentence boundary, or at a block-breaking `\\n`, or the
    scan limit (snapped to a word start)."""
    limit = max(0, a - _SENTENCE_BACK_WINDOW)
    for k in range(a - 1, limit - 1, -1):
        if text[k] == "\n":
            if _is_soft_wrap(text, k):
                continue
            return k + 1
        if text[k] in _SENTENCE_END_CHARS and _sentence_boundary_end(text, k):
            return k + 1
    return _snap_forward_to_word(text, limit, a)


def _sentence_end(text: str, b: int) -> int:
    """Scan forward from `b`, at most `_SENTENCE_FORWARD_WINDOW` chars past `b`, for the end of
    the enclosing sentence: just after a sentence boundary (terminator included), at a
    block-breaking `\\n` (excluded), or end of text (or the scan limit, snapped to a word end)."""
    n = len(text)
    limit = min(n, b + _SENTENCE_FORWARD_WINDOW)
    for k in range(b, limit):
        if text[k] == "\n":
            if _is_soft_wrap(text, k):
                continue
            return k
        if text[k] in _SENTENCE_END_CHARS and _sentence_boundary_end(text, k):
            return k + 1
    return _snap_back_to_word(text, limit, b)


def _cap(quote: str) -> str:
    """Cap a rendered quote at `_QUOTE_CAP` chars, cutting at the last word boundary at or
    before the limit and appending " …" — write_vault renders the quote as a one-line
    blockquote, and an untruncated quote could otherwise run to a whole page."""
    if len(quote) <= _QUOTE_CAP:
        return quote
    truncated = quote[:_QUOTE_CAP]
    cut = truncated.rfind(" ")
    if cut <= 0:
        cut = _QUOTE_CAP
    return truncated[:cut].rstrip() + " …"


def _expand_sentence(text: str, a: int, b: int) -> str:
    """Expand the original-text span `[a, b)` (a locator match) to its enclosing sentence,
    de-hyphenated and collapsed to a single line — mandatory, not cosmetic: write_vault renders
    the quote as a one-line `  > …` blockquote, so an embedded newline would break the note's
    markdown."""
    start = _sentence_start(text, a)
    end = _sentence_end(text, b)
    quote = _HYPHEN_BREAK_RE.sub("", text[start:end])
    quote = _WS_RE.sub(" ", quote).strip()
    return _cap(quote)


_MIN_SPACELESS_LOCATOR = 8   # normalized chars, spaces removed — below this a whitespace-blind
                             # match risks colliding on an unrelated span (#560)


def _find_match(norm_text: str, idx_map: list[int], norm_locator: str) -> tuple[int, int, int] | None:
    """Find `norm_locator` in `norm_text`, first with whitespace intact, then — only if that
    fails — with ALL whitespace stripped from both sides, to recover a match that OCR broke by
    fusing or dropping a word boundary (#560: e.g. "DEFERREDCONTRIBUTIONS ANDNET ASSETS" in the
    source vs. the model's correctly-spaced "DEFERRED CONTRIBUTIONS AND NET ASSETS"). The
    whitespace-blind fallback only ever runs after the normal match comes up empty, so it can
    only recover an otherwise-lost match, never override a real one; `_MIN_SPACELESS_LOCATOR`
    keeps a short locator from being trusted to match without any word-boundary signal at all.

    Returns ``(origin_start, origin_end, occurrence_count)`` in the original text's index space
    (via `idx_map`), or ``None`` if neither variant matches.
    """
    count = norm_text.count(norm_locator)
    if count > 0:
        start = norm_text.index(norm_locator)
        end = start + len(norm_locator)
        return idx_map[start], idx_map[end - 1] + 1, count

    sp_locator = norm_locator.replace(" ", "")
    if len(sp_locator) < _MIN_SPACELESS_LOCATOR:
        return None
    sp_chars: list[str] = []
    sp_idx: list[int] = []
    for ch, oi in zip(norm_text, idx_map):
        if ch != " ":
            sp_chars.append(ch)
            sp_idx.append(oi)
    sp_text = "".join(sp_chars)
    sp_count = sp_text.count(sp_locator)
    if sp_count == 0:
        return None
    start = sp_text.index(sp_locator)
    end = start + len(sp_locator)
    return sp_idx[start], sp_idx[end - 1] + 1, sp_count


def _sanity_checked_quote(norm_locator: str, quote: str, locator: str) -> str:
    """`_expand_sentence`'s output should still contain the matched locator — checked
    space-insensitively too, since a whitespace-blind match (`_find_match`) can legitimately
    expand to a quote that still carries the source's own missing/fused word boundary (#560).
    Falls back to the raw locator if expansion produced something unrelated to the match."""
    norm_quote = _normalize(quote)
    if norm_locator in norm_quote or norm_locator.replace(" ", "") in norm_quote.replace(" ", ""):
        return quote
    return locator


def resolve_quote(page_texts: dict[int, str], page: int | None, locator: str) -> dict:
    """Resolve a model-emitted ``quote_locator`` — the first several words of a source
    sentence — against the real page text, and expand it to the full sentence.

    Returns ``{}`` for an empty/blank locator. Returns ``{"quote": locator}`` unresolved when
    there's nothing to resolve against (no page cited, or no page text available at all — same
    "nothing to check" posture `verify_quote` already takes; searching document-wide would
    invite collisions). Otherwise tries the cited page, then page ±1 (OCR noise, page-marker
    drift). The first candidate page whose normalized text contains the normalized locator wins;
    if the locator occurs more than once on that page, the first occurrence is used and
    ``"ambiguous": True`` (plus ``"occurrences"``) is set. ``"found_page"`` is set when the
    winning candidate isn't the cited page.

    If none of the three single-page candidates match at all, falls back to joining two
    adjacent pages' text into one and searching that (#560) — a hard-wrapped sentence split
    across a page break can never be found on either page alone, since each page's text is
    searched independently. Tried in natural reading order (the cited page's tail continuing
    into the next page), then the mirror direction, each only when both halves are on hand. A
    match found this way sets ``"spans_pages": (a, b)`` instead of ``"found_page"`` — the quote
    genuinely isn't contained by any single page, so nothing here claims one.

    When no candidate matches at all — single-page or joined — returns
    ``{"quote": locator, "verified": False}``.
    """
    locator = (locator or "").strip()
    if not locator:
        return {}
    if not page_texts or page is None:
        return {"quote": locator}

    norm_locator = _normalize(locator)
    if not norm_locator:
        return {"quote": locator}

    for candidate in (page, page - 1, page + 1):
        text = page_texts.get(candidate)
        if not text:
            continue
        norm_text, idx_map = _normalize_with_map(text)
        match = _find_match(norm_text, idx_map, norm_locator)
        if match is None:
            continue
        a, b, count = match

        quote = _sanity_checked_quote(norm_locator, _expand_sentence(text, a, b), locator)

        result = {"quote": quote}
        if candidate != page:
            result["found_page"] = candidate
        if count > 1:
            result["ambiguous"] = True
            result["occurrences"] = count
        return result

    for a_page, b_page in ((page, page + 1), (page - 1, page)):
        text_a, text_b = page_texts.get(a_page), page_texts.get(b_page)
        if not text_a or not text_b:
            continue
        # Strip a lone page-number stamp right at the join point — left in, it would sit
        # between the two true halves of the sentence. A single "\n" for the join itself, not a
        # blank line: `_is_soft_wrap` treats more than one embedded newline as a hard block
        # break, so sentence expansion scans straight through this exactly as it already does
        # for a same-page hard-wrapped line.
        text_a_clean = _PAGE_FURNITURE_TAIL_RE.sub("", text_a.rstrip())
        text_b_clean = _PAGE_FURNITURE_LEAD_RE.sub("", text_b.lstrip())
        joined = text_a_clean + "\n" + text_b_clean
        norm_joined, idx_map_j = _normalize_with_map(joined)
        match = _find_match(norm_joined, idx_map_j, norm_locator)
        if match is None:
            continue
        a, b, count = match

        quote = _sanity_checked_quote(norm_locator, _expand_sentence(joined, a, b), locator)

        result = {"quote": quote, "spans_pages": (a_page, b_page)}
        if count > 1:
            result["ambiguous"] = True
            result["occurrences"] = count
        return result

    return {"quote": locator, "verified": False}


def _document_occurrence_count(page_texts: dict[int, str], locator: str) -> int:
    """How many times `locator` occurs across the WHOLE document, not just the ±1 window
    `resolve_quote` checks (#560). `resolve_quote` itself deliberately stays scoped to that
    narrow window for ordinary quote resolution — a document-wide search there would flag too
    many harmless recurring labels as unsafe to even display. But correcting `fact["page"]`
    itself, not merely resolving a display quote, is a stronger action and needs a stronger
    guarantee than "unique among the three pages we happened to check": a locator unique within
    that window could still collide with an unrelated occurrence on page 40 of a 70-page
    document that resolve_quote never looks at.
    """
    norm_locator = _normalize(locator)
    if not norm_locator:
        return 0
    return sum(_normalize(text).count(norm_locator) for text in page_texts.values() if text)


def resolve_quotes(extraction: dict, page_texts: dict[int, str]) -> list[str]:
    """Resolve every ``document.key_facts[].quote_locator`` into a full ``quote`` (#529), and
    verify any legacy ``quote`` (a pre-#529 extraction re-run through post-flight with no
    locator). Mutates ``extraction`` in place; returns a WARN line only for an outcome that
    actually needs a reader's attention — a locator that couldn't be resolved at all, or a page
    citation left uncorrected because it's ambiguous. A page-spanning match, an ambiguous-but-
    resolved match (used the first occurrence), and a confidently auto-corrected page citation
    are routine self-resolution, not warnings — live runs on live documents showed these three
    accounting for most of the warning volume with nothing for a reader to act on.

    Runs BEFORE `explode_key_facts` — the fan-out copies an already-resolved ``quote`` onto
    each entity's fanned-out evidence fragment, so it no longer walks
    ``entities[].evidence_fragments`` itself.
    """
    warnings: list[str] = []
    for i, fact in enumerate(extraction.get("document", {}).get("key_facts", [])):
        locator = (fact.get("quote_locator") or "").strip()
        if locator:
            result = resolve_quote(page_texts, fact.get("page"), locator)
            if "quote" in result:
                fact["quote"] = result["quote"]
            if result.get("found_page") is not None:
                fact["quote_found_page"] = result["found_page"]
            if result.get("spans_pages"):
                fact["quote_spans_pages"] = list(result["spans_pages"])

            if result.get("verified") is False:
                fact["quote_verified"] = False
                warnings.append(
                    f"document.key_facts[{i}].quote_locator not found on page {fact.get('page')} "
                    f"(or adjacent pages) — flagged as unverified: {locator!r}"
                )
            elif result.get("spans_pages"):
                pass  # resolved fine (joined two pages) — nothing for a reader to act on
            elif result.get("ambiguous"):
                pass  # resolved fine (used the first of several matches) — no reader action
            elif result.get("found_page") is not None:
                # A single match within the ±1 window — but not on the page the model cited
                # (#560). Trusting `fact["page"]` to it needs more than "unique among the three
                # pages resolve_quote checked": confirm it's unique across the WHOLE document
                # before correcting the citation a reporter will actually see, rather than just
                # the quote text resolve_quote already resolves regardless.
                if _document_occurrence_count(page_texts, locator) == 1:
                    fact["page"] = result["found_page"]  # confidently auto-corrected, no warning
                else:
                    warnings.append(
                        f"document.key_facts[{i}].quote_locator was cited to page "
                        f"{fact.get('page')} but only found on page {result['found_page']} — "
                        f"the fact's page citation may be off by one, but the phrase also "
                        f"recurs elsewhere in the document, so it was not auto-corrected: "
                        f"{locator!r}"
                    )
            continue

        quote = (fact.get("quote") or "").strip()
        if quote:
            _annotate(fact, page_texts)
            if fact.get("quote_verified") is False:
                warnings.append(
                    f"document.key_facts[{i}].quote not found on page {fact.get('page')} "
                    f"(or adjacent pages) — flagged as unverified: {quote!r}"
                )

    return warnings
