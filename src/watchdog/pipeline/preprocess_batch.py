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

from watchdog.cmd.live import LiveRegion
from watchdog.pipeline.preprocess import _perf_cpu_count, sha256_file

DEFAULT_FILE_TIMEOUT = 600

# Key for the persistent progress/ETA row, pinned (LiveRegion `pin=True`) so it always
# renders last, below the in-flight file rows (#158, #333 follow-up — previously just the
# first key inserted, so it visually jumped between finished/in-flight rows as files completed).
_PROGRESS_KEY = "__progress__"

# A blank pinned row rendered just above the progress bar so the bar always keeps one line of
# clearance from the finished/in-flight rows above it instead of butting directly against them.
_SPACER_KEY = "__progress_spacer__"


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
    timeout: int = DEFAULT_FILE_TIMEOUT,
    chunk_workers: int | None = None,
) -> dict:
    t0 = time.time()
    cmd = [sys.executable, "-m", "watchdog.pipeline.preprocess", str(path)]
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


def _filter_already_seen(files: list, vault: Path, incoming: Path, queue: Path) -> list:
    """Drop files whose exact content is already known, before paying for OCR (#146).

    Each file's sha256 is checked against the document registry (already ingested) and the pending
    queue (already chewed this round, awaiting ingest), plus the shas seen earlier in this same
    batch (intra-batch duplicates). A match is moved to ``_INCOMING/_SKIPPED/`` with a warning
    rather than re-OCR'd and re-queued — the journalist keeps the file, it just isn't processed
    again. (Exact bytes only; a near-duplicate has a different sha and is handled by the MinHash
    check at ingest.)
    """
    docs_path = vault / ".watchdog" / "Registry" / "documents.json"
    try:
        ingested = set(json.loads(docs_path.read_text(encoding="utf-8"))) if docs_path.exists() else set()
    except (OSError, json.JSONDecodeError):
        ingested = set()
    queued = {p.stem for p in queue.glob("*.json")}

    keep, seen = [], set()
    for f in files:
        try:
            sha = sha256_file(f)
        except OSError:
            keep.append(f)
            continue
        reason = ("already ingested" if sha in ingested
                  else "already queued" if sha in queued
                  else "duplicate in this batch" if sha in seen
                  else None)
        if reason is None:
            seen.add(sha)
            keep.append(f)
            continue
        skipped_dir = incoming / "_SKIPPED"
        skipped_dir.mkdir(exist_ok=True)
        try:
            f.rename(skipped_dir / f.name)
        except OSError:
            pass
        print(f"  {_YELLOW}⚠ duplicate{_RESET}  {f.name}  "
              f"{_DIM}{reason} → _INCOMING/_SKIPPED/{_RESET}")
    return keep


def run_ingest(
    vault: Path,
    workers: int | None = None,
    chunk_workers: int | None = None,
    files: list | None = None,
    show_ingest_hint: bool = True,
) -> None:
    incoming = vault / "_INCOMING"
    queue    = vault / ".watchdog" / "queue"
    staging  = vault / ".watchdog" / "staging"
    queue.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=True, exist_ok=True)

    # Acquire the chew lock atomically (#257). Previously the lock was written unconditionally,
    # so two chews (e.g. `watchdog watch` plus a manual `watchdog chew`) could run concurrently
    # on one vault and race the staging renames/near-dup computes. O_CREAT|O_EXCL admits exactly
    # one; a >30-min stale lock (from a crashed chew) is taken over, recoverable via `unlock`.
    from watchdog.pipeline.locks import acquire_or_take_stale
    from watchdog.pipeline.ingest_setup import STALE_SECONDS
    lock_dir = vault / ".watchdog"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / ".chew-lock"
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if not acquire_or_take_stale(lock_file, f"started_at: {started_at}\npid: {os.getpid()}\n",
                                 STALE_SECONDS):
        sys.exit("\n  Error: a chew is already in progress on this vault. "
                 "Wait for it to finish, or run: watchdog unlock\n")

    try:
        _run_ingest_inner(vault, incoming, queue, staging, workers, chunk_workers, files,
                          show_ingest_hint)
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
    show_ingest_hint: bool = True,
) -> None:
    if files is None:
        files = find_files([incoming])
    files = _filter_already_seen(files, vault, incoming, queue)
    if not files:
        queued = len(list(queue.glob("*.json")))
        if queued:
            print(f"\n  {_DIM}_INCOMING/ is empty — {queued} file{'s' if queued != 1 else ''} ready. Run {_RESET}{_CYAN}watchdog ingest{_RESET}{_DIM}.{_RESET}\n")
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

    # Live status region (#158): one in-place row per in-flight file, finished/failed lines and
    # notes scrolling above, plus a persistent progress/ETA row on top. Off a TTY it disables and
    # only the finished lines + summary print (append-only), matching the previous logged output.
    live = LiveRegion()

    def _rel(path: Path) -> str:
        try:
            return str(path.relative_to(incoming))
        except ValueError:
            return path.name

    def _refresh_progress(done: int) -> None:
        if not live.enabled:
            return
        elapsed_wall = time.time() - batch_start
        bar = _bar(done, total)
        if done < total and done and elapsed_wall > 0:
            tail = f"  {_DIM}{_fmt_eta(round((elapsed_wall / done) * (total - done)))}{_RESET}"
        elif done == total:
            tail = f"  {_DIM}{round(elapsed_wall)}s total{_RESET}"
        else:
            tail = ""
        live.update(_PROGRESS_KEY, f"  {bar} {_BOLD}{done}/{total}{_RESET}{tail}", pin=True)

    def _chew(path: Path) -> dict:
        # Runs in a worker thread: mark the file in-flight the moment a worker picks it up, so the
        # row appears while OCR spins up. Gated to TTYs to keep non-TTY output to finished lines only.
        if live.enabled:
            # Pad the arrow marker to the same width as the settled status codes ("OK "/"ERR"/
            # "SKP") so filenames start at the same column whether a row is in-flight or done.
            live.update(str(path), f"  {_DIM}→  {_RESET}  {_DIM}{_rel(path)}  chewing…{_RESET}")
        return preprocess_one(path, timeout=DEFAULT_FILE_TIMEOUT, chunk_workers=chunk_workers)

    results: dict[str, dict] = {}
    _cancel_event.clear()

    if live.enabled:
        live.update(_SPACER_KEY, "", pin=True)   # blank clearance line above the progress bar
    _refresh_progress(0)            # seed the pinned progress row; it renders last regardless
    pool = ThreadPoolExecutor(max_workers=pre_workers)
    futures = {pool.submit(_chew, f): f for f in files}
    done = 0
    skipped = 0
    try:
        for future in as_completed(futures):
            path   = futures[future]
            result = future.result()
            results[str(path)] = result
            done += 1
            if done % 50 == 0:
                _prune_empty_dirs(incoming)

            is_err     = "error" in result
            is_empty   = not is_err and result.get("char_count", 0) == 0
            is_garbled = not is_err and not is_empty and result.get("metadata", {}).get("garbled_detected", False)
            if is_err:
                status = f"{_YELLOW}ERR{_RESET}"
            elif is_empty:
                status = f"{_DIM}SKP{_RESET}"
            else:
                status = f"{_GREEN}OK {_RESET}"

            rel       = _rel(path)
            label     = _page_label(path, result.get("page_count", 0))
            label_str = f"  {_DIM}{label}{_RESET}" if label else ""
            garb_str  = f"  {_DIM}·  garbled OCR{_RESET}" if is_garbled else ""

            # Settle this file's row: print its result above the live region, clearing the in-flight row.
            live.finish(str(path), f"  {status}  {_BOLD}{rel}{_RESET}{label_str}{garb_str}")

            if is_err:
                failed_dir = incoming / "_FAILED"
                failed_dir.mkdir(exist_ok=True)
                try:
                    path.rename(failed_dir / path.name)
                except OSError:
                    pass
                live.note(f"       {_YELLOW}→ _INCOMING/_FAILED/{_RESET}  {_DIM}{result['error'][:80]}{_RESET}")
            elif is_empty:
                skipped += 1
                skipped_dir = incoming / "_SKIPPED"
                skipped_dir.mkdir(exist_ok=True)
                try:
                    path.rename(skipped_dir / path.name)
                except OSError:
                    pass
                live.note(f"       {_DIM}→ _INCOMING/_SKIPPED/  no text content extracted{_RESET}")
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
                    result["document_type"] = None
                    (queue / f"{sha256}.json").write_text(
                        json.dumps(result, ensure_ascii=False)
                    )

            _refresh_progress(done)

    except KeyboardInterrupt:
        _cancel_event.set()
        for fut in futures:
            fut.cancel()
        pool.shutdown(wait=True, cancel_futures=True)
        _prune_empty_dirs(incoming)
        live.stop()
        print(f"\n  {_DIM}Cancelled — {done} of {total} files processed. Unfinished files remain in _INCOMING/.{_RESET}\n")
        return
    else:
        pool.shutdown(wait=False)

    # Extraction phase done — leave the final region on screen and print the summary plainly.
    live.stop()

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

    if ok and show_ingest_hint:
        print()
        print(f"  Run:  {_CYAN}watchdog ingest{_RESET}")

    print()
