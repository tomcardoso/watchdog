"""One-line values written into Markdown (D304).

A label, a value or a term that a session or a form supplies ends up inside a line of a note: a
contradiction callout's line, a watchlist line, a timeline bullet. A line break there would let it
write lines of its own (a second, forged callout; a heading; extra watchlist terms), so these
values are refused, not cleaned, when they carry any line break or other control character.
"""

from __future__ import annotations

import unicodedata

# Line and paragraph separators that Markdown renderers or editors may also break on.
_BREAKS = {"\u0085", " ", " "}


def has_control(text: str) -> bool:
    """Whether `text` holds a line break or any other control character (tab included)."""
    return any(ch in _BREAKS or unicodedata.category(ch) == "Cc" for ch in text)


def require_single_line(text, what: str) -> str:
    """`text` unchanged, or `ValueError` naming `what` when it isn't one line of plain text."""
    if text is None:
        return text
    if not isinstance(text, str):
        raise ValueError(f"{what} must be text.")
    if has_control(text):
        raise ValueError(f"{what} must be a single line, with no line breaks or control characters.")
    return text
