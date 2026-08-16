"""Deterministic figure verification against the morgue text (#363, widened by D141/#397).

Tributary's validation review surfaced a failure class quote-checking doesn't catch: a
model reports "$430,000 across two transfers" when the source pages state $250,000 and
$180,000 separately — a computed sum presented as if it were extracted verbatim. Nothing
checked that the numbers inside a `key_facts[].fact` sentence actually appear on the cited
page.

Same posture as the D32 coverage warning and #267 quote verification (`quote_verify.py`):
annotation/advisory only, never a gate. A fact with `basis: "inferred"` is a declared
derivation and is exempt outright; a fact with `basis` absent or `"stated"` claims to be
read off the page, so its numeric tokens are checked against the cited page and its
immediate neighbors.

Normalization is deliberately conservative: no free-form unit conversion and no arithmetic.
A paraphrase like "about $1.2-million" for a page that says "$1,234,567" will legitimately
miss and get flagged — accepted, because this is advisory, not a gate.

D141 (#397) narrowed that gap for the one case real-world benchmarking showed to dominate
the warning volume: financial statements reported in thousands, where the page shows
"21,406" under a "(in $000s)" header and the model correctly writes the fact as
"$21,406,000". That's not a paraphrase, it's an exact x1,000/x1,000,000 scale of the same
digits — `_scale_variants` checks both directions before giving up on a token. A fact citing
the wrong page for a number that's verbatim elsewhere in the *same* document (a comparative
figure pulled from the MD&A highlights page while citing the statement page) gets a
separate, softer warning — the number is real, the citation just points at the wrong spot —
rather than being lumped in with "may be derived or garbled".
"""

import re
from decimal import Decimal, InvalidOperation

_GROUPED_NUM_RE = re.compile(r"\d{1,3}(?:[,  ]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_PLAIN_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_GROUP_CHARS_RE = re.compile(r"[,  ]")
_SCALE_EXPONENTS = (3, 6)  # thousands, millions

# A bare four-digit year is a date, not a figure (#623). It was always the noisiest token class
# here — 9.6% of every flag over the extraction corpus was a fact flagged for a year alone, e.g.
# "…for the fiscal year ended April 30, 2021" where the page writes that period some other way —
# and that was tolerable while the finding only reached ingest.log. Now that a flag renders in the
# vault, a false "figure 2,021 not found in the document" costs the reader's trust in every other
# flag. What skipping them costs: a genuine dollar amount that happens to be exactly $2,021 goes
# unchecked. Applied to the fact's own tokens only — page text is still indexed in full.
_YEAR_RE = re.compile(r"^(?:1[5-9]\d\d|20\d\d|21\d\d)$")


def _normalize_token(token: str) -> str:
    """Strip grouping separators, leading zeros (keep a lone "0"), and trailing decimal
    zeros/dot — no unit conversion, no arithmetic."""
    token = _GROUP_CHARS_RE.sub("", token)
    if "." in token:
        int_part, dec_part = token.split(".", 1)
        dec_part = dec_part.rstrip("0")
        int_part = int_part.lstrip("0") or "0"
        token = f"{int_part}.{dec_part}" if dec_part else int_part
    else:
        token = token.lstrip("0") or "0"
    return token


def _tokens(text: str, regex: "re.Pattern[str]") -> set[str]:
    return {_normalize_token(m) for m in regex.findall(text)}


def _is_real_page(page) -> bool:
    return isinstance(page, int) and not isinstance(page, bool)


def _page_tokens(page_texts: dict[int, str], page: int) -> set[str]:
    tokens: set[str] = set()
    for candidate in (page - 1, page, page + 1):
        text = page_texts.get(candidate)
        if not text:
            continue
        tokens |= _tokens(text, _GROUPED_NUM_RE)
        tokens |= _tokens(text, _PLAIN_NUM_RE)
    return tokens


def _scale_variants(token: str) -> set[str]:
    """A token's exact x1,000 and x1,000,000 scalings, both directions — moving the decimal
    point is lossless for a base-10 value, unlike the free-form unit conversion this module
    otherwise declines to do. Catches "21,406" (a $000s table) grounding a fact that correctly
    writes "$21,406,000", and the symmetric case of a fact quoting the table's raw thousands."""
    try:
        value = Decimal(token)
    except InvalidOperation:
        return set()
    variants: set[str] = set()
    for exponent in _SCALE_EXPONENTS:
        for scaled in (value.scaleb(exponent), value.scaleb(-exponent)):
            variants.add(_normalize_token(format(scaled, "f")))
    variants.discard(token)
    return variants


def _index_pages(page_texts: dict[int, str]) -> dict[str, list[int]]:
    """Every numeric token in the document, mapped to the sorted pages it appears on —
    built lazily (only once a fact has a token that failed the near-page check) since most
    documents never need it."""
    index: dict[str, list[int]] = {}
    for page_num, text in page_texts.items():
        if not text:
            continue
        for tok in _tokens(text, _GROUPED_NUM_RE) | _tokens(text, _PLAIN_NUM_RE):
            index.setdefault(tok, []).append(page_num)
    for pages in index.values():
        pages.sort()
    return index


def verify_figures(extraction: dict, page_texts: dict[int, str]) -> list[str]:
    """Check every stated `key_facts[].fact`'s numeric figures against its cited page (and
    the page ±1, with exact x1,000/x1,000,000 scale variants allowed). A figure absent nearby
    but verbatim (or scale-equivalent) elsewhere in the document gets a softer "check the
    citation" warning; a figure absent from the whole document keeps the original "may be
    derived or garbled" warning.

    Advisory, never a gate — but the finding has to reach the reporter, not just the ingest
    log (#623), so a flagged fact is annotated in place the way `quote_verify` annotates an
    unresolved quote: ``figures_unverified`` lists the tokens found nowhere in the document,
    ``figures_off_page`` maps each token found elsewhere to the pages holding it. `write_vault`
    renders both on the fact's own line. Facts that pass are left untouched — an absent key
    means "checked and clean", or not checked at all (no page text, no figures, `inferred`).
    """
    warnings: list[str] = []
    doc_index: dict[str, list[int]] | None = None
    for i, fact in enumerate(extraction.get("document", {}).get("key_facts", [])):
        # Clear any annotation from an earlier post-flight pass over the same staged extraction
        # (`watchdog bark`) before re-deciding: a figure that resolves this time — better OCR, a
        # page text that wasn't on disk before — must not keep yesterday's flag.
        fact.pop("figures_unverified", None)
        fact.pop("figures_off_page", None)

        if fact.get("basis") == "inferred":
            continue

        page = fact.get("page")
        if not _is_real_page(page) or not page_texts.get(page):
            continue

        text = (fact.get("fact") or "").strip()
        if not text:
            continue

        fact_tokens = {t for t in _tokens(text, _GROUPED_NUM_RE) if not _YEAR_RE.match(t)}
        if not fact_tokens:
            continue

        page_token_set = _page_tokens(page_texts, page)
        near_missing = sorted(
            t for t in fact_tokens
            if t not in page_token_set and not (_scale_variants(t) & page_token_set)
        )
        if not near_missing:
            continue

        if doc_index is None:
            doc_index = _index_pages(page_texts)

        still_missing = []
        elsewhere: list[tuple[str, list[int]]] = []
        for t in near_missing:
            found_on = doc_index.get(t) or []
            if not found_on:
                for variant in _scale_variants(t):
                    found_on = doc_index.get(variant) or []
                    if found_on:
                        break
            if found_on:
                elsewhere.append((t, found_on))
            else:
                still_missing.append(t)

        if still_missing:
            fact["figures_unverified"] = still_missing
            warnings.append(
                f"document.key_facts[{i}] figure(s) {', '.join(still_missing)} not found on "
                f"page {page} (or adjacent pages) — may be derived or garbled; check the "
                f"source: {text!r}"
            )
        if elsewhere:
            fact["figures_off_page"] = {t: pages for t, pages in elsewhere}
            detail = ", ".join(f"{t} (page {'/'.join(str(p) for p in pages)})" for t, pages in elsewhere)
            warnings.append(
                f"document.key_facts[{i}] figure(s) {detail} not found on page {page} (or "
                f"adjacent pages) but appear(s) elsewhere in the document — the figure is "
                f"real, the page citation may be wrong; check the source: {text!r}"
            )

    return warnings
