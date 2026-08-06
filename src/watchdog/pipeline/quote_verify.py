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

import html
import re
import unicodedata

_WS_RE = re.compile(r"\s+")
_HYPHEN_BREAK_RE = re.compile(r"-\s*\n\s*")
_NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WORD_CHAR_RE = re.compile(r"\w", re.UNICODE)
_SPACE_CHAR_RE = re.compile(r"\s", re.UNICODE)
_SENTENCE_END_CHARS = ".!?"

# A next line opening with one of these is a new markdown block (table row, heading, blockquote,
# list bullet), never the continuation of a wrapped sentence.
_BLOCK_MARKER_RE = re.compile(r"[|#>*+\-]|\d+[.)]")
_WRAP_COLUMN = 60               # a line at least this long, ending mid-sentence, looks column-wrapped

_SENTENCE_BACK_WINDOW = 300     # max chars scanned backward for a sentence start
_SENTENCE_FORWARD_WINDOW = 400  # max chars scanned forward (past the match) for a sentence end
_QUOTE_CAP = 500                # max rendered quote length, in characters


def _normalize(text: str) -> str:
    """Case/whitespace/punctuation/hyphenation/accent-insensitive form for fuzzy matching.

    Deliberately aggressive: a false "unverified" flag on a real quote erodes trust in the
    flag itself more than an occasional false-positive match would.
    """
    text = text.replace("­", "")          # soft hyphen
    text = _HYPHEN_BREAK_RE.sub("", text)       # word broken across a line by hyphenation
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _NON_WORD_RE.sub("", text.lower())
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

    return "".join(out_chars), out_idx


def verify_quote(page_texts: dict[int, str], page: int | None, quote: str) -> dict:
    """Check ``quote`` against its cited page, then the page ±1 (OCR noise, page-marker
    drift), and report the outcome.

    Legacy path only (#529): a `quote_locator`-carrying fact is resolved by `resolve_quote`
    instead. This is kept for a pre-#529 extraction staged before the change and re-run through
    post-flight by `watchdog bark`.

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

    for candidate in (page, page - 1, page + 1):
        text = page_texts.get(candidate)
        # Docling's markdown export HTML-escapes "&" to "&amp;" in some cells/paragraphs even
        # though markdown doesn't require it; a model transcribing the page naturally reads
        # that as a plain "&" (#560), so decode before comparing or every quote spanning an
        # "X & Y" would falsely fail to verify.
        if text and norm_quote in _normalize(html.unescape(text)):
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
    winning candidate isn't the cited page. When no candidate matches at all, returns
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
        # Decode Docling's HTML-escaped "&amp;" (etc.) before matching or expanding — a model
        # transcribing the page naturally writes the decoded "&", so the raw markdown's escaped
        # form would otherwise never match (#560). Unescaping `text` itself, not just the
        # comparison, keeps it consistent with `idx_map`'s origin indices and means the rendered
        # quote below is clean too, rather than showing "&amp;" to a reader.
        text = html.unescape(text)
        norm_text, idx_map = _normalize_with_map(text)
        match = _find_match(norm_text, idx_map, norm_locator)
        if match is None:
            continue
        a, b, count = match

        quote = _expand_sentence(text, a, b)
        norm_quote = _normalize(quote)
        # Sanity guard: the expansion logic produced something unrelated to the match. Checked
        # space-insensitively too — a whitespace-blind match (above) can legitimately expand to
        # a quote that still carries the source's own missing/fused word boundary, which would
        # otherwise trip this guard on the very case it exists to recover (#560).
        if norm_locator not in norm_quote and norm_locator.replace(" ", "") not in norm_quote.replace(" ", ""):
            quote = locator

        result = {"quote": quote}
        if candidate != page:
            result["found_page"] = candidate
        if count > 1:
            result["ambiguous"] = True
            result["occurrences"] = count
        return result

    return {"quote": locator, "verified": False}


def resolve_quotes(extraction: dict, page_texts: dict[int, str]) -> list[str]:
    """Resolve every ``document.key_facts[].quote_locator`` into a full ``quote`` (#529), and
    verify any legacy ``quote`` (a pre-#529 extraction re-run through post-flight with no
    locator). Mutates ``extraction`` in place; returns a WARN line per locator that couldn't be
    resolved and per ambiguous (page-collided) match.

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
            if result.get("verified") is False:
                fact["quote_verified"] = False
                warnings.append(
                    f"document.key_facts[{i}].quote_locator not found on page {fact.get('page')} "
                    f"(or adjacent pages) — flagged as unverified: {locator!r}"
                )
            if result.get("ambiguous"):
                warnings.append(
                    f"document.key_facts[{i}].quote_locator matched {result['occurrences']} times "
                    f"on page {result.get('found_page', fact.get('page'))} — used the first match: "
                    f"{locator!r}"
                )
            elif result.get("found_page") is not None:
                # A single, unambiguous match — but not on the page the model cited (#560). This
                # used to be silent unless also ambiguous: `fact["page"]` (what every downstream
                # reader — the document note, entity evidence, timeline — actually displays)
                # still carries the model's original, wrong citation even though the quote text
                # itself was quietly resolved from the real page. Warn rather than silently
                # correct: the citation may be genuinely off by one (a real, if minor, model
                # error worth a reporter's attention), not a matching artifact to paper over.
                warnings.append(
                    f"document.key_facts[{i}].quote_locator was cited to page {fact.get('page')} "
                    f"but only found on page {result['found_page']} — the fact's page citation "
                    f"may be off by one: {locator!r}"
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
