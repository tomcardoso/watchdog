#!/usr/bin/env python3
"""
Read a single entry from a watchdog batch results file (ingest.json).

Usage:
    watchdog-batch-get .watchdog/ingest.json --index N --meta
    watchdog-batch-get .watchdog/ingest.json --index N --text
    watchdog-batch-get .watchdog/ingest.json --index N --field sha256
    watchdog-batch-get .watchdog/ingest.json --count
"""

import argparse
import json
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Read a single entry from a batch results file")
    parser.add_argument("batch_file", help="Path to ingest.json")
    parser.add_argument("--index", type=int, help="0-based index of the entry to read")
    parser.add_argument("--meta",  action="store_true", help="Print all fields except text as JSON")
    parser.add_argument("--text",  action="store_true", help="Print the text field only")
    parser.add_argument("--field", metavar="NAME",       help="Print a single named field")
    parser.add_argument("--count", action="store_true", help="Print the number of entries")
    args = parser.parse_args()

    path = Path(args.batch_file)
    if not path.exists():
        sys.exit(f"Error: {path} not found")

    try:
        batch = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"Error: could not parse {path}: {e}")

    if not isinstance(batch, list):
        sys.exit(f"Error: expected a JSON array in {path}")

    if args.count:
        print(len(batch))
        return

    if args.index is None:
        sys.exit("Error: --index is required unless --count is used")

    if args.index < 0 or args.index >= len(batch):
        sys.exit(f"Error: index {args.index} out of range (batch has {len(batch)} entries)")

    entry = batch[args.index]

    if args.text:
        pages = entry.get("pages", [])
        print("\n\n".join(p.get("markdown", "") for p in pages))
    elif args.field:
        if args.field not in entry:
            sys.exit(f"Error: field '{args.field}' not found in entry {args.index}")
        value = entry[args.field]
        print(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
    elif args.meta:
        meta = {k: v for k, v in entry.items() if k not in {"pages"}}
        print(json.dumps(meta, ensure_ascii=False))
    else:
        sys.exit("Error: specify one of --meta, --text, --field NAME, or --count")


if __name__ == "__main__":
    main()
