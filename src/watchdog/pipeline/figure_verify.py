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
A fact whose figure can't be tied to any single printed number — a sum, a difference, a
count — will legitimately miss and get flagged, which is the whole point of the check.

D141 (#397) narrowed that gap for the one case real-world benchmarking showed to dominate
the warning volume: financial statements reported in thousands, where the page shows
"21,406" under a "(in $000s)" header and the model correctly writes the fact as
"$21,406,000". That's not a paraphrase, it's an exact x1,000/x1,000,000 scale of the same
digits — `_scale_variants` checks both directions before giving up on a token. A fact citing
the wrong page for a number that's verbatim elsewhere in the *same* document (a comparative
figure pulled from the MD&A highlights page while citing the statement page) gets a
separate, softer warning — the number is real, the citation just points at the wrong spot —
rather than being lumped in with "may be derived or garbled".

D213 closed the successor gap, which turned out to dominate the remaining flag volume: a
figure reported to *fewer digits* than the page prints it at. A $000s statement showing
"360,291" backs a fact that correctly says "$360.3 million", but that is not an exact scale
of the same digits, so `_scale_variants` never matched it and the fact was told the number
appears nowhere in its own source. Measured over the extraction artifacts on disk, 15 of 27
flagged figures were restatements of this kind. `_rounding_interval` accepts them, taking
its precision from how the fact writes the figure — see that function for the window and
what it deliberately gives up.
"""

import bisect
import re
from decimal import Decimal, InvalidOperation

_GROUPED_NUM_RE = re.compile(r"\d{1,3}(?:[,  ]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
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


def _rounding_interval(token: str) -> tuple[Decimal, Decimal] | None:
    """Half-open ``[lo, hi)`` of document values a reporter could write as `token`, given the
    precision `token` is written at — one unit of its last digit.

    Two ways to shorten a number, and we can't know which a model used, so both are allowed:
    **rounding** (7.4 -> "7") admits ``[t - u/2, t + u/2)``, **truncating** (7.9 -> "7") admits
    ``[t, t + u)``. Their union is ``[t - u/2, t + u)``. Deliberately NOT symmetric: the low
    side stays half a unit because nothing shortens 6.0 to "7", while the high side needs a
    full unit to cover truncation.

    The window is as wide as the figure is vague, which is the point — "$360.3 million" (4
    significant digits) admits +/-0.014%, while "$7 million" (1 digit) admits 6.5-8.0. A fact
    that reports a figure to one digit genuinely does not assert more than that, so a tight
    window there would flag correct readings, which is the failure this function exists to
    stop causing. The cost is the mirror: a *derived* one-digit figure that happens to land
    near a printed one goes unflagged. Advisory check, so a miss is cheaper than a false alarm.
    """
    try:
        value = Decimal(token)
    except InvalidOperation:
        return None
    decimals = len(token.split(".")[1]) if "." in token else 0
    unit = Decimal(1).scaleb(-decimals)
    return value - unit / 2, value + unit


def _numeric_values(text: str) -> set[Decimal]:
    values: set[Decimal] = set()
    for token in _tokens(text, _GROUPED_NUM_RE) | _tokens(text, _PLAIN_NUM_RE):
        try:
            values.add(Decimal(token))
        except InvalidOperation:
            continue
    return values


def _page_values(page_texts: dict[int, str], page: int) -> list[Decimal]:
    """Sorted numeric values on the cited page and its immediate neighbors — the `_page_tokens`
    counterpart for the rounding pass, which compares magnitudes rather than digit strings."""
    values: set[Decimal] = set()
    for candidate in (page - 1, page, page + 1):
        text = page_texts.get(candidate)
        if text:
            values |= _numeric_values(text)
    return sorted(values)


def _rounding_scales(token: str) -> list[tuple[Decimal, Decimal]]:
    """`token`'s rounding interval expressed in each scale a page might print it at — the same
    x1,000/x1,000,000 both-directions set `_scale_variants` covers, plus the unscaled case."""
    interval = _rounding_interval(token)
    if interval is None:
        return []
    lo, hi = interval
    windows = [(lo, hi)]
    for exponent in _SCALE_EXPONENTS:
        windows.append((lo.scaleb(-exponent), hi.scaleb(-exponent)))
        windows.append((lo.scaleb(exponent), hi.scaleb(exponent)))
    return windows


def _rounds_to_page_value(token: str, page_values: list[Decimal]) -> bool:
    """Whether any value on the cited page rounds or truncates to `token` at some scale."""
    for lo, hi in _rounding_scales(token):
        index = bisect.bisect_left(page_values, lo)
        if index < len(page_values) and page_values[index] < hi:
            return True
    return False


def _rounding_pages(token: str, indexed: list[tuple[Decimal, int]]) -> list[int]:
    """Pages holding a value that rounds or truncates to `token`, for the document-wide pass."""
    values = [value for value, _ in indexed]
    pages: set[int] = set()
    for lo, hi in _rounding_scales(token):
        index = bisect.bisect_left(values, lo)
        while index < len(indexed) and indexed[index][0] < hi:
            pages.add(indexed[index][1])
            index += 1
    return sorted(pages)


def _index_values(page_texts: dict[int, str]) -> list[tuple[Decimal, int]]:
    """Every numeric value in the document paired with its page, sorted by value so the
    rounding pass can bisect it. Built lazily beside `_index_pages`, for the same reason."""
    indexed: list[tuple[Decimal, int]] = []
    for page_num, text in page_texts.items():
        if not text:
            continue
        for value in _numeric_values(text):
            indexed.append((value, page_num))
    indexed.sort(key=lambda pair: pair[0])
    return indexed


def _index_pages(page_texts: dict[int, str]) -> dict[str, list[int]]:
    """Every numeric token in the document, mapped to the sorted pages it appears on —
    built lazily (only once a fact has a token that failed the near-page check) since most
    documents never need it."""
    index: dict[str, list[int]] = {}
    for page_num, text in page_texts.items():
        if not text:
            continue
        for tok in _tokens(text, _GROUPED_NUM_RE):
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
    doc_values: list[tuple[Decimal, int]] = []
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
        page_values: list[Decimal] | None = None
        near_missing = []
        for t in sorted(fact_tokens):
            if t in page_token_set or (_scale_variants(t) & page_token_set):
                continue
            # Exact and exact-scale matching missed, so the fact may still be reporting a
            # printed figure to fewer digits than the page prints it at (D213). Only reached
            # on a token that already failed the cheap string paths.
            if page_values is None:
                page_values = _page_values(page_texts, page)
            if _rounds_to_page_value(t, page_values):
                continue
            near_missing.append(t)
        if not near_missing:
            continue

        if doc_index is None:
            doc_index = _index_pages(page_texts)
            doc_values = _index_values(page_texts)

        still_missing = []
        elsewhere: list[tuple[str, list[int]]] = []
        for t in near_missing:
            found_on = doc_index.get(t) or []
            if not found_on:
                for variant in _scale_variants(t):
                    found_on = doc_index.get(variant) or []
                    if found_on:
                        break
            if not found_on:
                found_on = _rounding_pages(t, doc_values)
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
