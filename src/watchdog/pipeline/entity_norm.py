"""
Canonical entity-name normalization.

Shared by both duplicate-reconciliation paths so they collapse the same spelling
variants to the same comparison key:
  * write_vault — reconciling near-duplicate slugs coined by parallel subagents
  * merge      — folding id drift across sections of one large document
"""

import re


def normalize_entity_name(name: str) -> str:
    """Collapse a name to a comparison key for duplicate-entity detection.

    Deterministic and conservative: lowercase, ``&`` → ``and``, strip
    punctuation, collapse whitespace. It catches spelling/punctuation variants
    (``Ernst & Young Inc.`` == ``Ernst and Young Inc``) but deliberately does
    **not** catch abbreviation or missing-token differences
    (``chief-justice-gb-morawetz`` ≠ ``chief-justice-morawetz``) — auto-merging
    those carries real false-positive risk.
    """
    s = (name or "").lower().replace("&", "and")
    s = re.sub(r"[^\w\s]", "", s)
    return re.sub(r"\s+", " ", s).strip()
