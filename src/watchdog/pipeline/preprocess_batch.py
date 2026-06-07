#!/usr/bin/env python3
"""
Watchdog batch parallel preprocessor.

Runs watchdog-preprocess on multiple files simultaneously, then outputs
a single JSON array to stdout. Progress is written to stderr so the
ingest skill can display it while capturing the JSON result.

Usage:
    watchdog-preprocess-batch _INCOMING/ [--workers 4]
    watchdog-preprocess-batch file1.pdf file2.pdf [--workers 4]

Output: JSON array, one object per file, in input order.
Each object is either a normal preprocess result or {"error": ..., "source_path": ...}.
"""

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

DEFAULT_WORKERS = 4

SKIP_NAMES   = {".ds_store", ".ingest-lock"}
SKIP_SUFFIXES = {".yml"}
SKIP_DIRS    = {"_failed", "_FAILED"}


def find_files(paths: list[str]) -> list[Path]:
    files = []
    for p in paths:
        path = Path(p).resolve()
        if path.is_file():
            if path.name.lower() not in SKIP_NAMES and path.suffix.lower() not in SKIP_SUFFIXES:
                files.append(path)
        elif path.is_dir():
            for f in sorted(path.rglob("*")):
                if not f.is_file():
                    continue
                if f.name.lower() in SKIP_NAMES:
                    continue
                if f.suffix.lower() in SKIP_SUFFIXES:
                    continue
                if any(part.lower() in SKIP_DIRS for part in f.relative_to(path).parts):
                    continue
                files.append(f)
    return files


def preprocess_one(path: Path) -> dict:
    t0 = time.time()
    try:
        r = subprocess.run(
            ["watchdog-preprocess", str(path)],
            capture_output=True, text=True, timeout=600,
        )
        elapsed = round(time.time() - t0, 1)
        if not r.stdout.strip():
            result = {"error": r.stderr.strip()[:300] or "Empty output from preprocessor"}
        else:
            result = json.loads(r.stdout)
    except subprocess.TimeoutExpired:
        elapsed = round(time.time() - t0, 1)
        result = {"error": f"Preprocessing timed out after {600}s"}
    except Exception as e:
        elapsed = round(time.time() - t0, 1)
        result = {"error": str(e)}

    result["source_path"] = str(path)
    result["elapsed_s"] = elapsed
    result["char_count"] = len(result.get("text", ""))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Watchdog batch parallel preprocessor")
    parser.add_argument("paths", nargs="+", help="Files or directories to preprocess")
    parser.add_argument(
        "--workers", type=int, default=DEFAULT_WORKERS,
        help=f"Parallel workers (default: {DEFAULT_WORKERS})",
    )
    args = parser.parse_args()

    files = find_files(args.paths)
    total = len(files)

    if not files:
        print("[]")
        return

    batch_start = time.time()
    print(
        f"Batch preprocessing {total} file(s) with {args.workers} workers...",
        file=sys.stderr, flush=True,
    )

    results_map: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(preprocess_one, f): f for f in files}
        completed = 0
        for future in as_completed(futures):
            path = futures[future]
            result = future.result()
            results_map[str(path)] = result
            completed += 1
            elapsed_wall = time.time() - batch_start
            status = "ERR" if "error" in result else "OK "
            chars = len(result.get("text", ""))
            pages = result.get("page_count", "?")
            elapsed_file = result.get("elapsed_s", "?")
            remaining = total - completed
            if remaining > 0:
                eta = round((elapsed_wall / completed) * remaining)
                eta_str = f"{eta // 60}m {eta % 60}s" if eta >= 60 else f"{eta}s"
                eta_part = f"  ETA ~{eta_str}"
            else:
                eta_part = ""
            print(
                f"[{completed}/{total}] {status} {path.name}  {pages}p  {chars}c  {elapsed_file}s{eta_part}",
                file=sys.stderr, flush=True,
            )

    elapsed_total = round(time.time() - batch_start, 1)
    ok    = sum(1 for r in results_map.values() if "error" not in r)
    errs  = total - ok
    print(
        f"Preprocessing done: {ok} OK, {errs} errors, {elapsed_total}s total",
        file=sys.stderr, flush=True,
    )

    # Output in original file order
    ordered = [results_map[str(f)] for f in files]
    print(json.dumps(ordered, ensure_ascii=False))


if __name__ == "__main__":
    main()
