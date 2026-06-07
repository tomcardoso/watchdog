#!/usr/bin/env python3
"""
Watchdog near-duplicate detector.

Compares a candidate document's text against all previously ingested documents
using Jaccard similarity on word 3-grams (shingles). No external dependencies.

Usage:
    python3 near_dup.py --text <extracted_text> --registry <path/to/documents.json>
                        [--threshold 0.85] [--shingles-file <path>]

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
import json
import re
import sys
from pathlib import Path


DEFAULT_THRESHOLD = 0.85
SHINGLE_SIZE = 3  # word 3-grams


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


def shingles_from_text(text: str) -> set[str]:
    return shingles(tokenize(text))


def main() -> None:
    parser = argparse.ArgumentParser(description="Watchdog near-duplicate detector")
    parser.add_argument("--text", help="Candidate document text (or use --stdin/--text-file)")
    parser.add_argument("--stdin", action="store_true", help="Read candidate text from stdin")
    parser.add_argument("--text-file", help="Path to a file containing the candidate text")
    parser.add_argument("--registry", required=True, help="Path to Registry/documents.json")
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Similarity threshold (default: {DEFAULT_THRESHOLD})",
    )
    args = parser.parse_args()

    if args.stdin:
        text = sys.stdin.read()
    elif args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8", errors="replace")
    elif args.text:
        text = args.text
    else:
        print(json.dumps({"error": "Provide --text, --text-file, or --stdin"}))
        sys.exit(1)

    registry_path = Path(args.registry)
    documents = {}
    if registry_path.exists():
        with open(registry_path) as f:
            documents = json.load(f)

    candidate_sh = shingles_from_text(text)
    matches = []

    for sha, doc in documents.items():
        stored_sh = doc.get("shingles")
        if not stored_sh:
            continue
        stored_set = set(stored_sh)
        sim = jaccard(candidate_sh, stored_set)
        if sim >= args.threshold:
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
                "candidate_shingles_count": len(candidate_sh),
                "candidate_shingles_sample": list(candidate_sh)[:200],  # stored in registry
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
