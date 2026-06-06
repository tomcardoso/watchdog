#!/usr/bin/env python3
"""
Batch test runner for Watchdog preprocess.py.

Stages:
  1. Quick-classify all files using pypdf (no OCR, instant)
  2. Run full preprocess.py on every file in parallel, skipping MP4 (needs ASR)
  3. Write results to batch_results.json
  4. Print a summary

Usage:
    python3 batch_test.py <folder> [--workers N] [--sample N]

--workers N  Parallel workers (default: 4)
--sample N   Only process this many files (default: all)
"""

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

SKIP_EXTENSIONS = {".ds_store", ".mp4", ".avi", ".mov"}  # needs ASR
TIMEOUT_PER_FILE = 300  # seconds — generous for large scanned PDFs


def quick_classify(path: Path) -> str:
    """Fast text-layer probe using pypdf. Returns 'text', 'empty', 'garbled', or 'unknown'."""
    if path.suffix.lower() != ".pdf":
        return "unknown"
    try:
        import pypdf
        reader = pypdf.PdfReader(str(path))
        sample = " ".join(
            reader.pages[i].extract_text() or ""
            for i in range(min(3, len(reader.pages)))
        )
        if not sample.strip():
            return "empty"       # scanned — will need OCR
        alpha = sum(1 for c in sample if c.isalpha())
        ratio = alpha / len(sample) if sample else 0
        return "garbled" if ratio < 0.4 else "text"
    except Exception as e:
        return f"pypdf_error: {e}"


def run_preprocess(path: Path, preprocess_script: Path) -> dict:
    start = time.time()
    try:
        r = subprocess.run(
            [sys.executable, str(preprocess_script), str(path)],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_PER_FILE,
        )
        elapsed = round(time.time() - start, 1)
        if r.returncode != 0 or not r.stdout.strip():
            return {
                "status": "error",
                "error": r.stdout.strip() or r.stderr.strip()[:300],
                "elapsed_s": elapsed,
            }
        result = json.loads(r.stdout)
        if "error" in result:
            return {"status": "error", "error": result["error"], "elapsed_s": elapsed}
        text_len = len(result.get("text", ""))
        return {
            "status": "ok",
            "sha256": result["sha256"],
            "page_count": result["page_count"],
            "text_chars": text_len,
            "text_preview": result.get("text", "")[:200].replace("\n", " "),
            "ocr_used": result["metadata"]["ocr_used"],
            "garbled_detected": result["metadata"]["garbled_detected"],
            "source_type": result["metadata"]["source_type"],
            "elapsed_s": elapsed,
        }
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "elapsed_s": TIMEOUT_PER_FILE}
    except Exception as e:
        return {"status": "exception", "error": str(e), "elapsed_s": round(time.time() - start, 1)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("folder")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--sample", type=int, default=None)
    args = parser.parse_args()

    folder = Path(args.folder)
    script = Path(__file__).parent / "preprocess.py"
    results_file = Path(__file__).parent / "batch_results.json"

    all_files = sorted(
        f for f in folder.rglob("*")
        if f.is_file() and f.suffix.lower() not in {".ds_store"}
    )

    print(f"Found {len(all_files)} files in {folder.name}")

    # --- Stage 1: quick classify ---
    print("Stage 1: Classifying text layers...")
    classifications = {str(f): quick_classify(f) for f in all_files}
    counts = {}
    for v in classifications.values():
        counts[v] = counts.get(v, 0) + 1
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {k:20s} {v}")
    print()

    # --- Stage 2: parallel pipeline ---
    to_process = [f for f in all_files if f.suffix.lower() not in SKIP_EXTENSIONS]
    skipped = [f for f in all_files if f.suffix.lower() in SKIP_EXTENSIONS]
    if args.sample:
        to_process = to_process[:args.sample]

    print(f"Stage 2: Processing {len(to_process)} files "
          f"({args.workers} workers, {TIMEOUT_PER_FILE}s timeout each)...")
    print(f"  Skipping {len(skipped)}: {[f.name for f in skipped]}")
    print()

    results = {}
    completed = 0
    ok = error = timeout = 0
    batch_start = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_preprocess, f, script): f for f in to_process}
        for future in as_completed(futures):
            f = futures[future]
            rel = str(f.relative_to(folder))
            r = future.result()
            completed += 1
            results[rel] = {"classification": classifications.get(str(f), "n/a"), **r}

            elapsed_total = round(time.time() - batch_start)
            if r["status"] == "ok":
                ok += 1
                tag = f"OK   {r['page_count']}p  {r['text_chars']:>6}c  {r['elapsed_s']}s"
            elif r["status"] == "timeout":
                timeout += 1
                tag = f"TIMEOUT ({TIMEOUT_PER_FILE}s)"
            else:
                error += 1
                tag = f"ERR  {r.get('error', '')[:55]}"

            print(f"  [{completed:3d}/{len(to_process)}] {rel[:65]:<65} {tag}  "
                  f"(wall {elapsed_total}s)")

    # Save results
    output = {
        "folder": str(folder),
        "total_files": len(all_files),
        "processed": len(to_process),
        "skipped": [str(f.relative_to(folder)) for f in skipped],
        "classifications": {str(Path(k).relative_to(folder)): v for k, v in classifications.items()},
        "results": results,
        "summary": {"ok": ok, "error": error, "timeout": timeout,
                    "total_wall_s": round(time.time() - batch_start)},
    }
    results_file.write_text(json.dumps(output, indent=2))

    print()
    print("=" * 60)
    print(f"  OK:      {ok}")
    print(f"  Errors:  {error}")
    print(f"  Timeout: {timeout}")
    print(f"  Skipped: {len(skipped)}")
    print(f"  Wall time: {output['summary']['total_wall_s']}s")
    print(f"  Results: {results_file}")
    print("=" * 60)

    if error or timeout:
        print("\nFailed files:")
        for rel, r in results.items():
            if r["status"] != "ok":
                print(f"  [{r['status'].upper()}] {rel}")
                if "error" in r:
                    print(f"         {r['error'][:100]}")


if __name__ == "__main__":
    main()
