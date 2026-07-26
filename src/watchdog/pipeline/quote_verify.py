"""Deterministic quote verification against the morgue text (#267).

D22 deferred checking that a captured ``key_facts[].quote`` actually appears on its cited
page to the model-graded #106 evaluator, since the Docling text was discarded after
extraction. Post-D26 the page text is already in hand at post-flight time (read here from
the chew-time queue descriptor, the same source `write_vault` uses to write the morgue
markdown) — so this is now a deterministic substring/fuzzy match, not a model call.

Annotation only, never a gate (same posture as the D32 coverage warning): an unmatched
quote is flagged in the rendered note and logged as a WARN, but never blocks the document.
"""

import re
import unicodedata

_WS_RE = re.compile(r"\s+")
_HYPHEN_BREAK_RE = re.compile(r"-\s*\n\s*")
_NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)


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


def verify_quote(page_texts: dict[int, str], page: int | None, quote: str) -> dict:
    """Check ``quote`` against its cited page, then the page ±1 (OCR noise, page-marker
    drift), and report the outcome.

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
        if text and norm_quote in _normalize(text):
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


def verify_quotes(extraction: dict, page_texts: dict[int, str]) -> list[str]:
    """Annotate every ``key_facts[].quote`` and its fanned-out ``evidence_fragments`` copies
    (post `explode_key_facts`, #140) with a verification result. Mutates in place; returns a
    WARN line per quote that couldn't be matched anywhere.
    """
    warnings: list[str] = []
    for i, fact in enumerate(extraction.get("document", {}).get("key_facts", [])):
        _annotate(fact, page_texts)
        if fact.get("quote_verified") is False:
            quote = (fact.get("quote") or "").strip()
            warnings.append(
                f"document.key_facts[{i}].quote not found on page {fact.get('page')} "
                f"(or adjacent pages) — flagged as unverified: {quote!r}"
            )

    for entity in extraction.get("entities", []):
        for frag in entity.get("evidence_fragments", []):
            _annotate(frag, page_texts)

    return warnings
