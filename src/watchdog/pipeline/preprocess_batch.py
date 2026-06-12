"""
Watchdog batch preprocessor — run from the CLI, not from Claude Code.

Chews all files in _INCOMING/, writes per-file results to
.watchdog/queue/<sha256>.json, moves originals to .watchdog/staging/<sha256>/,
and prints a Claude Code handoff message when done.
"""

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from watchdog.pipeline.preprocess import _perf_cpu_count, sha256_file

DEFAULT_FILE_TIMEOUT = 600


def _compute_near_dup(result: dict, vault: Path) -> dict:
    """Compute near-duplicate check for a freshly chewed document. Never raises."""
    try:
        from watchdog.pipeline.near_dup import shingles_from_text, minhash, minhash_similarity
        text = " ".join(p.get("markdown", "") for p in result.get("pages", []))
        if not text.strip():
            return {"near_duplicates": [], "top_similarity": 0.0, "candidate_minhash": []}
        documents_path = vault / ".watchdog" / "Registry" / "documents.json"
        documents = json.loads(documents_path.read_text()) if documents_path.exists() else {}
        candidate_mh = minhash(shingles_from_text(text))
        threshold = 0.85
        try:
            cfg_path = Path.home() / ".watchdog" / "config.json"
            threshold = json.loads(cfg_path.read_text()).get("dup_threshold", threshold)
        except Exception:
            pass
        matches = []
        for sha, doc in documents.items():
            stored_mh = doc.get("minhash")
            if stored_mh:
                sim = minhash_similarity(candidate_mh, stored_mh)
                if sim >= threshold:
                    matches.append({
                        "sha256": sha,
                        "filename": doc.get("filename", ""),
                        "similarity": round(sim, 4),
                        "document_note": doc.get("document_note", ""),
                    })
        matches.sort(key=lambda x: x["similarity"], reverse=True)
        top = matches[0]["similarity"] if matches else 0.0
        return {"near_duplicates": matches, "top_similarity": top, "candidate_minhash": candidate_mh}
    except Exception:
        return {"near_duplicates": [], "top_similarity": 0.0, "candidate_minhash": []}

_cancel_event = threading.Event()

SKIP_NAMES    = {".ds_store", ".ingest-lock", "thumbs.db", "desktop.ini"}
SKIP_SUFFIXES = {".yml"}
SKIP_DIRS     = {"_failed", "_FAILED", "_skipped", "_SKIPPED"}
_OS_JUNK      = {".ds_store", "thumbs.db", "desktop.ini"}

_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_CYAN   = "\033[0;36m"
_GREEN  = "\033[0;32m"
_YELLOW = "\033[0;33m"
_RESET  = "\033[0m"

_BAR_WIDTH = 28

_PAGE_UNIT = {
    ".pdf": "page", ".docx": "page", ".doc": "page", ".pptx": "page", ".ppt": "page",
    ".xlsx": "sheet", ".xls": "sheet", ".ods": "sheet", ".numbers": "sheet",
}


def _page_label(path: Path, count: int) -> str:
    unit = _PAGE_UNIT.get(path.suffix.lower())
    if unit is None or count == 0:
        return ""
    return f"{count} {unit}{'s' if count != 1 else ''}"


def _count_pdf_pages(path: Path) -> int:
    if path.suffix.lower() != ".pdf":
        return 1
    try:
        r = subprocess.run(
            ["qpdf", "--show-npages", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return int(r.stdout.strip())
    except Exception:
        pass
    return 1


def _adaptive_workers(files: list[Path]) -> tuple[int, int, dict]:
    perf = _perf_cpu_count()
    with ThreadPoolExecutor(max_workers=min(8, len(files))) as pool:
        counts = list(pool.map(_count_pdf_pages, files))
    page_counts = dict(zip(files, counts))
    median = sorted(counts)[len(counts) // 2]
    if median <= 10:
        return max(2, perf // 2), max(2, perf // 5), page_counts
    elif median <= 50:
        return max(2, perf // 3), max(2, perf // 3), page_counts
    else:
        return max(2, perf // 5), max(2, perf // 2), page_counts


def _resolve_workers(
    files: list[Path],
    explicit_pre: int | None,
    explicit_chunk: int | None = None,
) -> tuple[int, int, bool, dict | None]:
    cfg: dict = {}
    try:
        cfg = json.loads((Path.home() / ".watchdog" / "config.json").read_text())
    except Exception:
        pass

    pre_cfg   = cfg.get("chew_workers", "auto")
    chunk_cfg = cfg.get("chunk_workers", "auto")

    needs_adaptive = (
        (explicit_pre is None and pre_cfg == "auto") or
        (explicit_chunk is None and chunk_cfg == "auto")
    )

    if needs_adaptive:
        adaptive_pre, adaptive_chunk, page_counts = _adaptive_workers(files)
    else:
        adaptive_pre = adaptive_chunk = 0
        page_counts = None

    pre   = explicit_pre if explicit_pre is not None else (
        adaptive_pre if pre_cfg == "auto" else int(pre_cfg)
    )
    chunk = explicit_chunk if explicit_chunk is not None else (
        adaptive_chunk if chunk_cfg == "auto" else int(chunk_cfg)
    )
    pre   = min(pre, max(1, len(files)))

    return pre, chunk, needs_adaptive, page_counts


def _bar(done: int, total: int) -> str:
    filled = round(_BAR_WIDTH * done / total) if total else 0
    return f"[{'█' * filled}{'░' * (_BAR_WIDTH - filled)}]"


def _fmt_eta(seconds: int) -> str:
    if seconds < 60:
        return f"~{seconds}s"
    elif seconds < 300:
        return f"~{seconds // 60}m {seconds % 60}s"
    else:
        return f"~{seconds // 60}m"


def _prune_empty_dirs(root: Path) -> None:
    for d in sorted(root.rglob("*"), reverse=True):
        if d.is_dir() and d != root:
            for f in d.iterdir():
                if f.is_file() and f.name.lower() in _OS_JUNK:
                    try:
                        f.unlink()
                    except OSError:
                        pass
            try:
                d.rmdir()
            except OSError:
                pass


def find_files(paths: list[Path]) -> list[Path]:
    files = []
    for p in paths:
        p = Path(p)
        if p.is_file():
            if p.name.lower() not in SKIP_NAMES and p.suffix.lower() not in SKIP_SUFFIXES:
                files.append(p)
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if not f.is_file():
                    continue
                if f.name.lower() in SKIP_NAMES:
                    continue
                if f.suffix.lower() in SKIP_SUFFIXES:
                    continue
                if any(part.lower() in SKIP_DIRS for part in f.relative_to(p).parts):
                    continue
                files.append(f)
    return files


def preprocess_one(
    path: Path,
    vault_path: str | None = None,
    timeout: int = DEFAULT_FILE_TIMEOUT,
    chunk_workers: int | None = None,
) -> dict:
    t0 = time.time()
    cmd = [sys.executable, "-m", "watchdog.pipeline.preprocess", str(path)]
    if vault_path:
        cmd += ["--vault-path", vault_path]
    if chunk_workers is not None:
        cmd += ["--chunk-workers", str(chunk_workers)]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = t0 + timeout
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if _cancel_event.is_set():
                    proc.kill()
                    proc.wait()
                    return {"error": "cancelled", "source_path": str(path),
                            "elapsed_s": round(time.time() - t0, 1), "char_count": 0}
                if time.time() >= deadline:
                    proc.kill()
                    proc.wait()
                    result = {"error": f"Timed out after {timeout}s"}
                    result["source_path"] = str(path)
                    result["elapsed_s"]   = round(time.time() - t0, 1)
                    result["char_count"]  = 0
                    return result
        elapsed = round(time.time() - t0, 1)
        if not stdout.strip():
            result = {"error": stderr.strip()[:300] or "Empty output from preprocessor"}
        else:
            result = json.loads(stdout)
    except Exception as e:
        elapsed = round(time.time() - t0, 1)
        result = {"error": str(e)}

    result["source_path"] = str(path)
    result["elapsed_s"]   = elapsed
    result["char_count"]  = sum(len(p.get("markdown", "")) for p in result.get("pages", []))
    return result


def run_ingest(
    vault: Path,
    workers: int | None = None,
    chunk_workers: int | None = None,
    files: list | None = None,
) -> None:
    incoming = vault / "_INCOMING"
    queue    = vault / ".watchdog" / "queue"
    staging  = vault / ".watchdog" / "staging"
    queue.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=True, exist_ok=True)

    # Write chew lock file
    lock_dir = vault / ".watchdog"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / ".chew-lock"
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    lock_file.write_text(f"started_at: {started_at}\npid: {os.getpid()}\n")

    try:
        _run_ingest_inner(vault, incoming, queue, staging, workers, chunk_workers, files)
    finally:
        try:
            lock_file.unlink()
        except OSError:
            pass


def _run_ingest_inner(
    vault: Path,
    incoming: Path,
    queue: Path,
    staging: Path,
    workers: int | None,
    chunk_workers: int | None,
    files: list | None,
) -> None:
    if files is None:
        files = find_files([incoming])
    if not files:
        queued = len(list(queue.glob("*.json")))
        if queued:
            print(f"\n  {_DIM}_INCOMING/ is empty — {queued} file{'s' if queued != 1 else ''} ready for {_RESET}{_CYAN}/watchdog-ingest{_RESET}{_DIM}.{_RESET}\n")
        else:
            print(f"\n  {_DIM}_INCOMING/ is empty — nothing to chew.{_RESET}\n")
        return

    total = len(files)
    pre_workers, chunk_workers, adaptive, page_counts = _resolve_workers(files, workers, chunk_workers)
    if page_counts:
        files = sorted(files, key=lambda f: page_counts.get(f, 1))
    batch_start = time.time()

    adaptive_tag = ", adaptive" if adaptive else ""
    print(
        f"\n  {_BOLD}Chewing {total} file{'s' if total != 1 else ''}{_RESET}"
        f"  {_DIM}({pre_workers} file · {chunk_workers} chunk workers{adaptive_tag}){_RESET}\n"
    )

    sys.stdout.write(f"  {_DIM}Starting workers — first output may take a few seconds…{_RESET}")
    sys.stdout.flush()

    results: dict[str, dict] = {}
    _cancel_event.clear()

    pool = ThreadPoolExecutor(max_workers=pre_workers)
    futures = {
        pool.submit(preprocess_one, f, str(vault), DEFAULT_FILE_TIMEOUT, chunk_workers): f
        for f in files
    }
    done = 0
    skipped = 0
    cancelled = False
    try:
        for future in as_completed(futures):
            path   = futures[future]
            result = future.result()
            results[str(path)] = result
            done += 1
            if done % 50 == 0:
                _prune_empty_dirs(incoming)

            elapsed_wall = time.time() - batch_start
            is_err     = "error" in result
            is_empty   = not is_err and result.get("char_count", 0) == 0
            is_garbled = not is_err and not is_empty and result.get("metadata", {}).get("garbled_detected", False)
            if is_err:
                status = f"{_YELLOW}ERR{_RESET}"
            elif is_empty:
                status = f"{_DIM}SKP{_RESET}"
            else:
                status = f"{_GREEN}OK {_RESET}"

            try:
                rel = str(path.relative_to(incoming))
            except ValueError:
                rel = path.name

            label     = _page_label(path, result.get("page_count", 0))
            label_str = f"  {_DIM}{label}{_RESET}" if label else ""
            garb_str  = f"  {_DIM}·  garbled OCR{_RESET}" if is_garbled else ""

            # Progress bar + ETA
            bar = _bar(done, total)
            if done < total and elapsed_wall > 0:
                eta       = round((elapsed_wall / done) * (total - done))
                time_part = f"  {_DIM}{_fmt_eta(eta)}{_RESET}"
            elif done == total:
                time_part = f"  {_DIM}{round(elapsed_wall)}s total{_RESET}"
            else:
                time_part = ""

            bar_display = f"{bar} {done}/{total}{time_part}          "

            # Clear the bar line, print file result (scrolls), then re-print bar without newline
            sys.stdout.write("\r\033[K")
            print(f"  {status}  {_BOLD}{rel}{_RESET}{label_str}{garb_str}")
            sys.stdout.write(f"  {bar_display}")
            sys.stdout.flush()

            if is_err:
                failed_dir = incoming / "_FAILED"
                failed_dir.mkdir(exist_ok=True)
                try:
                    path.rename(failed_dir / path.name)
                except OSError:
                    pass
                sys.stdout.write("\r\033[K")
                print(f"       {_YELLOW}→ _INCOMING/_FAILED/{_RESET}  {_DIM}{result['error'][:80]}{_RESET}")
                sys.stdout.write(f"  {bar_display}")
                sys.stdout.flush()
            elif is_empty:
                skipped += 1
                skipped_dir = incoming / "_SKIPPED"
                skipped_dir.mkdir(exist_ok=True)
                try:
                    path.rename(skipped_dir / path.name)
                except OSError:
                    pass
                sys.stdout.write("\r\033[K")
                print(f"       {_DIM}→ _INCOMING/_SKIPPED/  no text content extracted{_RESET}")
                sys.stdout.write(f"  {bar_display}")
                sys.stdout.flush()
            else:
                sha256 = result.get("sha256", "")
                if sha256:
                    dest_dir = staging / sha256
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    dest = dest_dir / path.name
                    try:
                        path.rename(dest)
                        result["source_path"] = str(dest)
                    except OSError:
                        pass
                    result["near_dup"] = _compute_near_dup(result, vault)
                    (queue / f"{sha256}.json").write_text(
                        json.dumps(result, ensure_ascii=False)
                    )

    except KeyboardInterrupt:
        cancelled = True
        _cancel_event.set()
        for fut in futures:
            fut.cancel()
        pool.shutdown(wait=True, cancel_futures=True)
        _prune_empty_dirs(incoming)
        sys.stdout.write("\r\033[K")
        print(f"\n  {_DIM}Cancelled — {done} of {total} files processed. Unfinished files remain in _INCOMING/.{_RESET}\n")
        return
    else:
        pool.shutdown(wait=False)

    # Clear the progress bar before printing summary
    sys.stdout.write("\r\033[K")

    _prune_empty_dirs(incoming)

    errs = sum(1 for r in results.values() if "error" in r)
    ok   = total - errs - skipped
    elapsed_total = round(time.time() - batch_start, 1)

    parts = [f"{_GREEN}{ok} file{'s' if ok != 1 else ''} ready{_RESET}"]
    if skipped:
        parts.append(f"{_DIM}{skipped} skipped{_RESET}")
    if errs:
        parts.append(f"{_YELLOW}{errs} failed{_RESET}")
    parts.append(f"{_DIM}{elapsed_total}s{_RESET}")

    print()
    print(f"  {'  ·  '.join(parts)}")

    if ok:
        print()
        print(f"  Open Claude Code and run:  {_CYAN}/watchdog-ingest{_RESET}")

    print()
