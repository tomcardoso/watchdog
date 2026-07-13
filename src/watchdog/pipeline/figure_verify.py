"""Deterministic figure verification against the morgue text (#363).

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

Normalization is deliberately conservative: no unit conversion and no arithmetic. A
paraphrase like "about $1.2-million" for a page that says "$1,200,000" will legitimately
miss and get flagged — acceptable, because this is advisory, not a gate. The alternative —
expanding "1.2" to "1200000" — risks false confidence from a coincidental small-number
match, which is worse than an occasional over-flagged paraphrase.
"""

import re

_GROUPED_NUM_RE = re.compile(r"\d{1,3}(?:[,  ]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_PLAIN_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_GROUP_CHARS_RE = re.compile(r"[,  ]")


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


def verify_figures(extraction: dict, page_texts: dict[int, str]) -> list[str]:
    """Check every stated `key_facts[].fact`'s numeric figures against its cited page (and
    the page ±1). Pure and advisory: mutates nothing, returns a warning per fact with at
    least one figure that couldn't be found anywhere searched.
    """
    warnings: list[str] = []
    for i, fact in enumerate(extraction.get("document", {}).get("key_facts", [])):
        if fact.get("basis") == "inferred":
            continue

        page = fact.get("page")
        if not _is_real_page(page) or not page_texts.get(page):
            continue

        text = (fact.get("fact") or "").strip()
        if not text:
            continue

        fact_tokens = _tokens(text, _GROUPED_NUM_RE)
        if not fact_tokens:
            continue

        page_token_set = _page_tokens(page_texts, page)
        missing = sorted(t for t in fact_tokens if t not in page_token_set)
        if missing:
            warnings.append(
                f"document.key_facts[{i}] figure(s) {', '.join(missing)} not found on page "
                f"{page} (or adjacent pages) — may be derived or garbled; check the source: "
                f"{text[:80]!r}"
            )

    return warnings
