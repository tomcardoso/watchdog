#!/usr/bin/env python3
"""
Watchdog near-duplicate detector.

Compares a candidate document's text against all previously ingested documents
using Jaccard similarity on word 3-grams (shingles). No external dependencies.

Usage:
    python3 near_dup.py --text <extracted_text> --registry <path/to/documents.json>
                        [--threshold 0.85]

Or pipe text via stdin:
    cat text.txt | python3 near_dup.py --stdin --registry <path/to/documents.json>

Outputs JSON:
{
  "near_duplicates": [
    {
      "sha256": str,
      "filename": str,
      "similarity": float,
      "document_note": str  # path to the document note if known
    }
  ],
  "candidate_shingles_count": int
}

The caller stores the candidate's shingles in documents.json after a successful
ingest so future documents can be compared against it.
"""

import hashlib
import re
from watchdog import config as user_config



DEFAULT_THRESHOLD = 0.85
SHINGLE_SIZE = 3  # word 3-grams
NUM_HASHES = 128
_MOD = (1 << 31) - 1  # Mersenne prime keeps values to 10 digits in JSON


def _make_coeffs(n: int) -> tuple[list[int], list[int]]:
    import random
    r = random.Random(0)
    a = [r.randint(1, _MOD - 1) for _ in range(n)]
    b = [r.randint(0, _MOD - 1) for _ in range(n)]
    return a, b


_MINHASH_A, _MINHASH_B = _make_coeffs(NUM_HASHES)


_config_cache: dict | None = None


def _reset_config_cache() -> None:
    global _config_cache
    _config_cache = None


def _config_get(key: str, default):
    """`config.json`, read once per process and then served from cache."""
    global _config_cache
    if _config_cache is None:
        _config_cache = user_config.read()
    return _config_cache.get(key, default)


def tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, split into words."""
    return re.findall(r"\b[a-z0-9]+\b", text.lower())


def shingles(tokens: list[str], k: int = SHINGLE_SIZE) -> set[str]:
    """Return the set of k-gram shingles from a token list."""
    if len(tokens) < k:
        return {" ".join(tokens)}
    return {" ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def shingles_from_text(text: str, k: int | None = None) -> set[str]:
    if k is None:
        k = _config_get("shingle_size", SHINGLE_SIZE)
    return shingles(tokenize(text), k=k)


def _shingle_hash(s: str) -> int:
    return int.from_bytes(hashlib.md5(s.encode()).digest()[:8], "little")


def minhash(sh: set[str], num_hashes: int = NUM_HASHES) -> list[int]:
    if not sh:
        return [0] * num_hashes
    hashed = [_shingle_hash(s) for s in sh]
    return [
        min((_MINHASH_A[i] * h + _MINHASH_B[i]) % _MOD for h in hashed)
        for i in range(num_hashes)
    ]


def minhash_similarity(sig_a: list[int], sig_b: list[int]) -> float:
    if not sig_a or not sig_b or len(sig_a) != len(sig_b):
        return 0.0
    return sum(a == b for a, b in zip(sig_a, sig_b)) / len(sig_a)
