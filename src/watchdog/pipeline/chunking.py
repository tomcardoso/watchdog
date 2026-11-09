"""
Size-bounded packing for the finalizer's whole-batch model calls (#696).

Reconciliation and entity synthesis each used to send one prompt holding every entity a batch
touched. That is fine for a few dozen documents and fails outright for a few thousand: the prompt
outgrows the model's context window, and because the input is deterministic, every retry fails the
same way. Both calls judge their items independently — a candidate pair on its own, a
contradiction within one entity, a summary per entity — so splitting them at item boundaries into
several calls loses nothing (ARCHITECTURE.md §8.5).

`prompt_budget_chars` sizes a chunk from the stage's own model, the same way `section.py` sizes an
extraction section; `pack` fills chunks greedily in the order it is given, so a caller's ordering
(strongest pair first, entities by name) survives the split. Items are measured by `json_size`,
which counts what tokenizes badly in these payloads — ids, hashes, dates, figures, JSON punctuation
— rather than their plain length (D394).
"""

import json
import re
from pathlib import Path

from watchdog import model_client

# Share of the model's context window one chunk's *data* may take. The rest is the call's
# instructions plus room for the response — lower than sectioning's 0.3 per-section budget would
# suggest is needed, because a finalizer response grows with its input (one synthesized summary per
# entity) where an extraction's does not.
_DATA_FRACTION = 0.25

# chars/4 is the estimate every other token count in the pipeline uses (`section.est_tokens`).
_CHARS_PER_EST_TOKEN = 4


def prompt_budget_chars(model: str | None, backend: str | None = None,
                        vault: Path | None = None, fraction: float = _DATA_FRACTION) -> int:
    """Characters of item data one call to `model` may carry.

    A fraction of the context window, clamped to the long-context price boundary where the model
    has one (D202), then corrected for the model's tokenizer (D180) — the same three steps
    `section.model_defaults` applies, so a large-window model packs more per call."""
    real_tokens = model_client.context_window(model, backend) * fraction
    cap = model_client.long_context_input_cap(model, backend)
    if cap:
        real_tokens = min(real_tokens, cap)
    est_tokens = real_tokens / model_client.tokenizer_ratio(model, backend, vault)
    return int(est_tokens * _CHARS_PER_EST_TOKEN)


# What one est token covers in `content_est_tokens`: a run of letters counts one per this many
# letters (at least one), and every digit and every punctuation mark counts one on its own.
# Whitespace counts nothing. On ordinary prose this lands near chars/3.4, a little above chars/4
# (and it is never below it); on a post-processing bundle — JSON keys and quotes, fact ids such as
# `fact:1:<hex>:<hex>`, ISO dates, street numbers and postal codes — near chars/2.4, at or a little
# above what a live run's provider counts implied once the model's tokenizer ratio is taken out
# (chars/2.7 to 2.9, D394). Erring high costs a few more calls; erring low crosses the long-prompt
# line, which on Claude Haiku 5.5 bills the whole call at five times the rate. Fitted by judgement
# against those counts, not against a tokenizer; see the standing follow-up in CLAUDE.md.
_LETTERS_PER_EST_TOKEN = 4
_PIECES = re.compile(r"[^\W\d_]+|\S")


def content_est_tokens(text: str) -> float:
    """Est tokens (the chars/4 unit `prompt_budget_chars` divides by, before the tokenizer ratio)
    of `text`, counted by what it is made of rather than by its length (D394).

    chars/4 is close for prose and badly wrong for the ids, hashes, figures and punctuation a
    reconciliation or synthesis bundle is mostly made of: a live run's reconcile calls, planned at
    chars/4, came back from the provider 40–50% larger and crossed the long-prompt line. Never
    less than chars/4, so no text is planned below the old estimate."""
    n = 0.0
    for piece in _PIECES.findall(text or ""):
        n += max(1.0, len(piece) / _LETTERS_PER_EST_TOKEN) if piece[0].isalpha() else 1.0
    return max(n, len(text or "") / _CHARS_PER_EST_TOKEN)


def json_size(item) -> int:
    """An item's footprint in a prompt that renders it with `json.dumps(..., ensure_ascii=False)`,
    in the characters `prompt_budget_chars` counts: `content_est_tokens` of the rendering, times
    the four characters an est token stands for (D394). At least the plain length, and half again
    more or above for an item dense with ids and figures; every post-processing budget compared
    against it (reconcile, synthesis, briefing, re-check) is counted this way."""
    return int(content_est_tokens(json.dumps(item, ensure_ascii=False)) * _CHARS_PER_EST_TOKEN)


def pack(items: list, budget: int, size=json_size, max_items: int | None = None) -> list[list]:
    """Split `items` into consecutive chunks whose summed `size` stays within `budget`, and of at
    most `max_items` each when given. An item larger than the whole budget gets a chunk to itself
    rather than being dropped — the caller decides whether to trim it first. Empty in, empty out."""
    chunks: list[list] = []
    current: list = []
    used = 0
    for item in items:
        n = size(item)
        full = current and (used + n > budget or (max_items is not None and len(current) >= max_items))
        if full:
            chunks.append(current)
            current, used = [], 0
        current.append(item)
        used += n
    if current:
        chunks.append(current)
    return chunks
