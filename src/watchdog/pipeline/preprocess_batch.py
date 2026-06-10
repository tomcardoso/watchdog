"""
Watchdog batch preprocessor — run from the CLI, not from Claude Code.

Preprocesses all files in _INCOMING/, writes per-file results to
.watchdog/preprocessed/<sha256>.json, and prints a Claude Code handoff
message when done.
"""

import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

DEFAULT_WORKERS = 4
DEFAULT_FILE_TIMEOUT = 600

SKIP_NAMES    = {".ds_store", ".ingest-lock"}
SKIP_SUFFIXES = {".yml"}
SKIP_DIRS     = {"_failed", "_FAILED"}

_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_CYAN   = "\033[0;36m"
_GREEN  = "\033[0;32m"
_YELLOW = "\033[0;33m"
_RESET  = "\033[0m"

_BAR_WIDTH = 28


def _config_workers() -> int:
    """Read preprocess_workers from ~/.watchdog/config.json, fall back to DEFAULT_WORKERS."""
    try:
        cfg = json.loads((Path.home() / ".watchdog" / "config.json").read_text())
        return int(cfg.get("preprocess_workers", DEFAULT_WORKERS))
    except Exception:
        return DEFAULT_WORKERS


def _bar(done: int, total: int) -> str:
    filled = round(_BAR_WIDTH * done / total) if total else 0
    return f"[{'█' * filled}{'░' * (_BAR_WIDTH - filled)}]"


def find_files(paths: list[Path]) -> list[Path]:
    files = []
    for p in paths:
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


def preprocess_one(path: Path, vault_path: str | None = None, timeout: int = DEFAULT_FILE_TIMEOUT) -> dict:
    t0 = time.time()
    cmd = [sys.executable, "-m", "watchdog.pipeline.preprocess", str(path)]
    if vault_path:
        cmd += ["--vault-path", vault_path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        elapsed = round(time.time() - t0, 1)
        if not r.stdout.strip():
            result = {"error": r.stderr.strip()[:300] or "Empty output from preprocessor"}
        else:
            result = json.loads(r.stdout)
    except subprocess.TimeoutExpired:
        elapsed = round(time.time() - t0, 1)
        result = {"error": f"Timed out after {timeout}s"}
    except Exception as e:
        elapsed = round(time.time() - t0, 1)
        result = {"error": str(e)}

    result["source_path"] = str(path)
    result["elapsed_s"]   = elapsed
    result["char_count"]  = sum(len(p.get("markdown", "")) for p in result.get("pages", []))
    return result


def run_ingest(vault: Path, workers: int | None = None) -> None:
    if workers is None:
        workers = _config_workers()

    incoming     = vault / "_INCOMING"
    preprocessed = vault / ".watchdog" / "preprocessed"
    preprocessed.mkdir(parents=True, exist_ok=True)

    files = find_files([incoming])
    if not files:
        print(f"\n  {_DIM}_INCOMING/ is empty — nothing to preprocess.{_RESET}\n")
        return

    total       = len(files)
    batch_start = time.time()

    print(f"\n  {_BOLD}Preprocessing {total} file{'s' if total != 1 else ''}{_RESET}  {_DIM}({workers} workers){_RESET}\n")

    results: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(preprocess_one, f, str(vault)): f for f in files}
        done = 0
        for future in as_completed(futures):
            path   = futures[future]
            result = future.result()
            results[str(path)] = result
            done += 1

            elapsed_wall = time.time() - batch_start
            is_err = "error" in result
            status = f"{_YELLOW}ERR{_RESET}" if is_err else f"{_GREEN}OK {_RESET}"
            pages  = result.get("page_count", "?")

            # Progress bar + ETA on a single updating line
            bar = _bar(done, total)
            if done < total and elapsed_wall > 0:
                eta     = round((elapsed_wall / done) * (total - done))
                eta_str = f"{eta // 60}m {eta % 60}s" if eta >= 60 else f"{eta}s"
                time_part = f"  {_DIM}ETA ~{eta_str}{_RESET}"
            elif done == total:
                time_part = f"  {_DIM}{round(elapsed_wall)}s total{_RESET}"
            else:
                time_part = ""

            # Overwrite the progress line, then print the file log on a new line
            print(f"\r  {bar} {done}/{total}{time_part}          ", flush=True)
            print(f"  {status}  {_BOLD}{path.name}{_RESET}  {_DIM}{pages}p{_RESET}")

            if is_err:
                failed_dir = incoming / "_FAILED"
                failed_dir.mkdir(exist_ok=True)
                try:
                    path.rename(failed_dir / path.name)
                except OSError:
                    pass
                print(f"       {_YELLOW}→ _INCOMING/_FAILED/{_RESET}  {_DIM}{result['error'][:80]}{_RESET}")
            else:
                sha256 = result.get("sha256", "")
                if sha256:
                    (preprocessed / f"{sha256}.json").write_text(
                        json.dumps(result, ensure_ascii=False)
                    )

    ok   = sum(1 for r in results.values() if "error" not in r)
    errs = total - ok
    elapsed_total = round(time.time() - batch_start, 1)

    print()
    if errs:
        print(f"  {ok} file{'s' if ok != 1 else ''} ready  ·  {_YELLOW}{errs} failed{_RESET}  ·  {_DIM}{elapsed_total}s{_RESET}")
    else:
        print(f"  {_GREEN}{ok} file{'s' if ok != 1 else ''} ready{_RESET}  {_DIM}({elapsed_total}s){_RESET}")

    if ok:
        print()
        print(f"  Open Claude Code and run:  {_CYAN}/watchdog-ingest{_RESET}")

    print()
