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

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


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
    """Read ~/.watchdog/config.json once per process, then serve from cache."""
    global _config_cache
    if _config_cache is None:
        try:
            _config_cache = json.loads((Path.home() / ".watchdog" / "config.json").read_text())
        except Exception:
            _config_cache = {}
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Watchdog near-duplicate detector")
    parser.add_argument("--text", help="Candidate document text (or use --stdin/--text-file)")
    parser.add_argument("--stdin", action="store_true", help="Read candidate text from stdin")
    parser.add_argument("--text-file", help="Path to a file containing the candidate text")
    parser.add_argument("--registry", help="Path to Registry/documents.json")
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help=f"Similarity threshold (default: from config or {DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--summary",
        metavar="FILE",
        help="Read an existing near-dup JSON output file and print only {near_duplicates, top_similarity}",
    )
    args = parser.parse_args()

    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        print(json.dumps({"error": "must be run from inside a Watchdog vault directory"}))
        sys.exit(1)

    def _check_vault(label: str, p: Path) -> None:
        if not str(p.resolve()).startswith(str(vault) + "/"):
            print(json.dumps({"error": f"{label} must be inside the vault directory ({vault})"}))
            sys.exit(1)

    if args.summary:
        summary_path = Path(args.summary)
        _check_vault("--summary", summary_path)
        data = json.loads(summary_path.read_text())
        matches = data.get("near_duplicates", [])
        top = matches[0]["similarity"] if matches else 0.0
        print(json.dumps({"near_duplicates": matches, "top_similarity": round(top, 4)}))
        return

    if not args.registry:
        print(json.dumps({"error": "--registry is required"}))
        sys.exit(1)

    threshold = (
        args.threshold
        if args.threshold is not None
        else _config_get("dup_threshold", DEFAULT_THRESHOLD)
    )

    if args.stdin:
        text = sys.stdin.read()
    elif args.text_file:
        _check_vault("--text-file", Path(args.text_file))
        text = Path(args.text_file).read_text(encoding="utf-8", errors="replace")
    elif args.text:
        text = args.text
    else:
        print(json.dumps({"error": "Provide --text, --text-file, or --stdin"}))
        sys.exit(1)

    registry_path = Path(args.registry)
    _check_vault("--registry", registry_path)
    documents = json.loads(registry_path.read_text()) if registry_path.exists() else {}

    candidate_sh = shingles_from_text(text)
    candidate_mh = minhash(candidate_sh)
    matches = []

    for sha, doc in documents.items():
        stored_mh = doc.get("minhash")
        if stored_mh:
            sim = minhash_similarity(candidate_mh, stored_mh)
        else:
            stored_sh = doc.get("shingles")
            if not stored_sh:
                continue
            sim = jaccard(candidate_sh, set(stored_sh))
        if sim >= threshold:
            matches.append(
                {
                    "sha256": sha,
                    "filename": doc.get("filename", ""),
                    "similarity": round(sim, 4),
                    "document_note": doc.get("document_note", ""),
                }
            )

    matches.sort(key=lambda x: x["similarity"], reverse=True)

    print(
        json.dumps(
            {
                "near_duplicates": matches,
                "candidate_minhash": candidate_mh,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
