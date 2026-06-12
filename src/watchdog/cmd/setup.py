"""Setup and configuration commands: about, setup, refresh-skills, configure, unlock."""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from watchdog.cmd.base import (
    CONFIG_FILE,
    WATCHDOG_HOME,
    _BOLD, _CYAN, _DIM, _GREEN, _RESET, _YELLOW,
    _VAULT_PERMISSIONS,
    _find_project,
    load_projects,
)


_CONFIGURE_KEYS = {
    # ── Project ───────────────────────────────────────────────────────────────
    "projects_dir": {
        "short": "Path where new investigation vaults are created",
        "help": (
            "The directory where `watchdog new` creates investigation vaults.\n"
            "  Set during setup; change here to move future vaults to a different location.\n"
            "  Existing vaults are not moved."
        ),
        "type": "path",
    },
    # ── OCR ───────────────────────────────────────────────────────────────────
    "ocr_engine": {
        "short": "OCR engine for scanned documents (default: auto)",
        "help": (
            "OCR engine used when processing scanned documents.\n"
            "  auto:         Apple Vision on macOS (if ocrmac installed), Tesseract elsewhere.\n"
            "  apple_vision: Apple Vision only — macOS with ocrmac required.\n"
            "  tesseract:    Tesseract — requires system install (brew or apt install tesseract-ocr).\n"
            "  easyocr:      EasyOCR — pure pip install, no system deps, less accurate on forms.\n"
            "  rapidocr:     RapidOCR — lightweight, no C deps, fast.\n"
            "  Valid values: auto, apple_vision, tesseract, easyocr, rapidocr."
        ),
        "type": "enum",
        "default": "auto",
        "choices": ["auto", "apple_vision", "tesseract", "easyocr", "rapidocr"],
    },
    "ocr_languages": {
        "short": "Apple Vision OCR languages (comma-separated BCP 47 codes, e.g. en-US,fr-FR)",
        "help": (
            "Languages Apple Vision should try when reading scanned documents.\n"
            "  Leave unset to auto-detect from the image (macOS 13+).\n"
            "  Set explicitly if auto-detection produces poor results or you are on macOS 12.\n"
            "  Codes: https://developer.apple.com/documentation/vision/vnrecognizetextrequest"
        ),
        "type": "lang_list",
    },
    "garbled_threshold": {
        "short": "OCR trigger threshold — alphanumeric ratio below which a PDF text layer is garbled (default: 0.75)",
        "help": (
            "When reading a PDF, Watchdog samples the text layer and measures what fraction of\n"
            "  characters are alphanumeric or whitespace. If the ratio falls below this threshold,\n"
            "  the text layer is considered garbled and OCR is applied automatically.\n"
            "  Lower = more aggressive OCR. Higher may miss subtly garbled pages.\n"
            "  Valid range: 0.0–1.0. Default: 0.75."
        ),
        "type": "float",
        "default": 0.75,
        "min": 0.0,
        "max": 1.0,
    },
    # ── Processing ────────────────────────────────────────────────────────────
    "chew_workers": {
        "short": "Parallel files during chewing ('auto' for adaptive, or a fixed number)",
        "help": (
            "Number of files chewed simultaneously by `watchdog chew`.\n"
            "  'auto' (default): Watchdog scans the batch before starting and sets this based on\n"
            "  median document length — more workers for short-doc batches, fewer for large PDFs.\n"
            "  Set to a whole number to pin the value regardless of batch content.\n"
            "  Set to 1 to process files one at a time."
        ),
        "type": "int_or_auto",
        "default": "auto",
        "min": 1,
    },
    "chunk_size": {
        "short": "Pages per chunk when splitting large PDFs for parallel processing (default: 40)",
        "help": (
            "PDFs with more pages than this value are split into chunks and processed in parallel.\n"
            "  Smaller chunks reduce peak memory per worker but add per-chunk overhead.\n"
            "  Larger chunks are more efficient on fast machines with ample RAM.\n"
            "  Default: 40."
        ),
        "type": "int",
        "default": 40,
        "min": 1,
    },
    "chunk_workers": {
        "short": "Parallel subprocesses for large-PDF chunks ('auto' for adaptive, or a fixed number)",
        "help": (
            "Number of parallel subprocesses used when splitting large PDFs (>chunk_size pages).\n"
            "  'auto' (default): set adaptively based on median document length in the batch.\n"
            "  Works in tandem with chew_workers: total subprocess load for large-PDF batches\n"
            "  is approximately chew_workers × chunk_workers.\n"
            "  Set to 1 to disable within-file parallelism."
        ),
        "type": "int_or_auto",
        "default": "auto",
        "min": 1,
    },
    "chunk_timeout": {
        "short": "Seconds before a chunk subprocess is killed (default: 300)",
        "help": (
            "Each chunk subprocess is given this many seconds to complete before being killed.\n"
            "  Increase for very large or complex PDFs on slow machines.\n"
            "  Default: 300 (5 minutes)."
        ),
        "type": "int",
        "default": 300,
        "min": 1,
    },
    # ── Extraction ────────────────────────────────────────────────────────────
    "table_structure": {
        "short": "Run table detection model on PDFs (default: true)",
        "help": (
            "When enabled, Docling runs a dedicated ML model to detect and reconstruct tables.\n"
            "  Disable to speed up ingestion of text-only documents (court decisions, contracts).\n"
            "  Does not affect text extraction — only the table structure model.\n"
            "  Default: true."
        ),
        "type": "bool",
        "default": True,
    },
    "embed_images": {
        "short": "Embed images as base64 in markdown output so Claude can see figures (default: false)",
        "help": (
            "When enabled, images and figures in documents are embedded as base64 data\n"
            "  in the markdown output, allowing Claude to read charts, graphs, and other\n"
            "  visual content directly. Significantly increases token usage and processing\n"
            "  time per document. Only useful when documents contain charts, image-based\n"
            "  tables, or diagrams that carry investigative value.\n"
            "  Default: false."
        ),
        "type": "bool",
        "default": False,
    },
    # ── Classification ────────────────────────────────────────────────────────
    "classify_pages": {
        "short": "Number of pages used to classify document type at chew time (default: 10)",
        "help": (
            "Watchdog embeds the first N pages of each document and compares them against\n"
            "  skill embeddings to determine the document type before extraction.\n"
            "  More pages = more context but slower chewing.\n"
            "  Default: 10."
        ),
        "type": "int",
        "default": 10,
        "min": 1,
    },
    # ── Deduplication ─────────────────────────────────────────────────────────
    "dup_threshold": {
        "short": "Near-duplicate Jaccard similarity threshold — score at which documents are flagged (default: 0.85)",
        "help": (
            "Watchdog fingerprints each document and compares it to all previously ingested documents\n"
            "  using Jaccard similarity on word n-grams. If the score meets or exceeds this threshold,\n"
            "  the document is flagged as a near-duplicate.\n"
            "  Higher = stricter matching (fewer false positives, may miss near-duplicates).\n"
            "  Lower = looser matching (more matches, more false positives).\n"
            "  Valid range: 0.0–1.0. Default: 0.85."
        ),
        "type": "float",
        "default": 0.85,
        "min": 0.0,
        "max": 1.0,
    },
    "shingle_size": {
        "short": "Word n-gram size for near-duplicate fingerprinting (default: 3)",
        "help": (
            "Documents are fingerprinted using overlapping sequences of n consecutive words.\n"
            "  Larger n is more precise but slower and uses more registry storage per document.\n"
            "  Smaller n is faster but produces more false positives.\n"
            "  Changing this invalidates existing shingle data — re-ingest to rebuild fingerprints.\n"
            "  Default: 3 (word trigrams)."
        ),
        "type": "int",
        "default": 3,
        "min": 1,
    },
}

_OCR_ENGINE_PACKAGES = {
    # engine → (import_name, pip_package) or None if bundled with docling
    "apple_vision": ("ocrmac",               "ocrmac"),
    "tesseract":    ("tesserocr",            "tesserocr"),
    "rapidocr":     ("rapidocr_onnxruntime", "rapidocr-onnxruntime"),
    "easyocr":      None,
    "auto":         None,
}

_TESSERACT_HEADERS_HINT = (
    "Tesseract system headers are required to build tesserocr:\n"
    "  Ubuntu/Debian:  sudo apt install tesseract-ocr libtesseract-dev\n"
    "  Fedora:         sudo dnf install tesseract tesseract-devel\n"
    "  macOS:          brew install tesseract\n"
    "Then re-run: watchdog configure ocr_engine tesseract"
)


def _ensure_ocr_engine(engine: str) -> None:
    if engine == "apple_vision" and sys.platform != "darwin":
        sys.exit("Error: apple_vision OCR is only available on macOS.")

    spec = _OCR_ENGINE_PACKAGES.get(engine)
    if spec is None:
        return

    import_name, pip_name = spec
    try:
        __import__(import_name)
        return
    except ImportError:
        pass

    print(f"\n  {_DIM}Installing {pip_name}...{_RESET}")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", pip_name],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        msg = f"\n  {_YELLOW}Warning:{_RESET} could not install {pip_name}.\n"
        if "tesserocr" in pip_name and ("gcc" in stderr or "compile" in stderr.lower() or "build" in stderr.lower()):
            msg += f"\n  {_DIM}{_TESSERACT_HEADERS_HINT}{_RESET}\n"
        else:
            msg += f"\n  {_DIM}{stderr[:300]}{_RESET}\n"
        print(msg)
    else:
        print(f"  {_GREEN}Installed:{_RESET} {_BOLD}{pip_name}{_RESET}\n")


def cmd_about(_args) -> None:
    from watchdog import __version__
    print()
    print(f"  🔍🐕  {_BOLD}Watchdog{_RESET}  {_DIM}v{__version__}{_RESET}")
    print(f"  {_DIM}Investigative journalism document intelligence{_RESET}")
    print()
    print(f"  🐙  {_DIM}GitHub   {_RESET}{_CYAN}https://github.com/tomcardoso/watchdog{_RESET}")
    print(f"  🐛  {_DIM}Issues   {_RESET}{_CYAN}https://github.com/tomcardoso/watchdog/issues{_RESET}")
    print(f"  📖  {_DIM}Install  {_RESET}{_CYAN}https://github.com/tomcardoso/watchdog/blob/main/INSTALL.md{_RESET}")
    print()


def cmd_setup(args) -> None:
    from watchdog.setup_cmd import run as run_setup
    run_setup(force=getattr(args, "force", False))


def cmd_refresh_skills(args) -> None:
    if args.name:
        _, info = _find_project(args.name)
        vault = Path(info["path"])
    else:
        vault = Path(".").resolve()
        if not (vault / ".watchdog").is_dir():
            sys.exit("Error: not inside a watchdog project. cd into a vault or pass a project name.")
    from watchdog.setup_cmd import install_skills
    commands_dir = vault / ".claude" / "commands"
    install_skills(commands_dir)

    settings_path = vault / ".claude" / "settings.json"
    added = []
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
            existing = set(settings.get("permissions", {}).get("allow", []))
            missing  = [p for p in _VAULT_PERMISSIONS if p not in existing]
            if missing:
                settings.setdefault("permissions", {}).setdefault("allow", []).extend(missing)
                settings_path.write_text(json.dumps(settings, indent=2) + "\n")
                added = missing
        except (json.JSONDecodeError, KeyError):
            pass

    print(f"\n  {_GREEN}Skills refreshed{_RESET}  {_DIM}{commands_dir}{_RESET}")
    if added:
        print(f"  {_GREEN}Permissions updated{_RESET}  {_DIM}added {len(added)} missing rule{'s' if len(added) != 1 else ''}{_RESET}")
    print()


def cmd_configure(args) -> None:
    config = {}
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text())
        except json.JSONDecodeError:
            sys.exit("Error: config file is corrupt. Try running 'watchdog setup --force'.")

    key   = getattr(args, "key",   None)
    value = getattr(args, "value", None)

    def _display_value(k, v):
        meta = _CONFIGURE_KEYS.get(k, {})
        if v is None:
            if k == "ocr_languages":
                return f"{_DIM}auto-detect (default){_RESET}"
            d = meta.get("default")
            if d is not None:
                v = d
            else:
                return f"{_DIM}(not set){_RESET}"
        if k == "ocr_languages":
            return f"{_CYAN}{', '.join(v)}{_RESET}" if v else f"{_DIM}auto-detect (default){_RESET}"
        if isinstance(v, bool):
            return f"{_CYAN}{'true' if v else 'false'}{_RESET}"
        return f"{_CYAN}{v}{_RESET}"

    if key is None:
        print()
        print(f"  {_BOLD}Configuration{_RESET}  {_DIM}{CONFIG_FILE}{_RESET}")
        print()
        for k, meta in _CONFIGURE_KEYS.items():
            print(f"  {_DIM}{k:<20}{_RESET} {_display_value(k, config.get(k))}")
            print(f"  {' ' * 20} {_DIM}{meta['short']}{_RESET}")
            print()
        return

    if key not in _CONFIGURE_KEYS:
        sys.exit(f"Error: unknown key '{key}'. Known keys: {', '.join(_CONFIGURE_KEYS)}")

    meta = _CONFIGURE_KEYS[key]

    if value is None:
        if sys.stdin.isatty():
            print(f"\n  {_BOLD}{key}{_RESET}\n")
            for line in meta["help"].split("\n"):
                print(f"  {_DIM}{line.strip()}{_RESET}")
            print()
            print(f"  Current value:  {_display_value(key, config.get(key))}")
            if key in ("chunk_workers", "chew_workers"):
                print(f"  Machine cores:  {os.cpu_count() or 1}")
            print()
            answer = input("  Change this value? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print()
                return
            print()
            value = input("  New value: ").strip()
            if not value:
                print(f"\n  {_DIM}No change.{_RESET}\n")
                return
        else:
            print(f"\n  {_BOLD}{key}{_RESET} = {_display_value(key, config.get(key))}\n")
            return

    if key == "ocr_languages":
        langs = [lang.strip() for lang in value.split(",") if lang.strip()]
        config[key] = langs
        display = ", ".join(langs) if langs else "auto-detect"
    elif key == "projects_dir":
        path = Path(value).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        config[key] = str(path)
        display = str(path)
    elif meta["type"] == "float":
        try:
            v = float(value)
        except ValueError:
            sys.exit(f"Error: '{key}' must be a number (e.g. 0.85)")
        lo, hi = meta.get("min"), meta.get("max")
        if lo is not None and v < lo:
            sys.exit(f"Error: '{key}' must be >= {lo}")
        if hi is not None and v > hi:
            sys.exit(f"Error: '{key}' must be <= {hi}")
        config[key] = v
        display = str(v)
    elif meta["type"] == "int_or_auto":
        if value.lower() == "auto":
            config[key] = "auto"
            display = "auto"
        else:
            try:
                v = int(value)
            except ValueError:
                sys.exit(f"Error: '{key}' must be 'auto' or a whole number")
            lo = meta.get("min")
            if lo is not None and v < lo:
                sys.exit(f"Error: '{key}' must be >= {lo}")
            config[key] = v
            display = str(v)
    elif meta["type"] == "int":
        try:
            v = int(value)
        except ValueError:
            sys.exit(f"Error: '{key}' must be a whole number")
        lo = meta.get("min")
        if lo is not None and v < lo:
            sys.exit(f"Error: '{key}' must be >= {lo}")
        config[key] = v
        display = str(v)
    elif meta["type"] == "bool":
        if value.lower() in ("true", "yes", "1", "on"):
            v = True
        elif value.lower() in ("false", "no", "0", "off"):
            v = False
        else:
            sys.exit(f"Error: '{key}' must be true or false")
        config[key] = v
        display = "true" if v else "false"
    elif meta["type"] == "enum":
        choices = meta.get("choices", [])
        if value not in choices:
            sys.exit(f"Error: '{key}' must be one of: {', '.join(choices)}")
        config[key] = value
        display = value
        if key == "ocr_engine":
            _ensure_ocr_engine(value)
    else:
        config[key] = value
        display = value

    WATCHDOG_HOME.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")
    print(f"\n  {_GREEN}Set:{_RESET} {_BOLD}{key}{_RESET} = {_CYAN}{display}{_RESET}\n")


def cmd_unlock(args) -> None:
    inferred = not args.project
    if args.project:
        _, info = _find_project(args.project)
    else:
        cwd = Path(".").resolve()
        if not (cwd / ".watchdog").is_dir():
            sys.exit("Error: not inside a Watchdog vault. Run from a vault directory or pass a project name.")
        projects = load_projects()
        match = next(((s, v) for s, v in projects.items() if Path(v["path"]).resolve() == cwd), None)
        if match is None:
            sys.exit("Error: vault not found in registry. Pass the project name explicitly.")
        args.project, info = match
    vault = Path(info["path"])

    locks = [
        (vault / ".watchdog" / ".chew-lock",                 ".chew-lock",   "chew"),
        (vault / ".watchdog" / "Registry" / ".ingest-lock",  ".ingest-lock", "ingest"),
    ]

    print()
    found_any = False
    for lock_path, lock_name, op_name in locks:
        if not lock_path.exists():
            continue
        found_any = True

        started_at = None
        for line in lock_path.read_text().splitlines():
            if line.startswith("started_at:"):
                started_at = line.split(":", 1)[1].strip()
                break

        age_str = "unknown age"
        is_stale = True
        if started_at:
            try:
                t = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                age_secs = (datetime.now(timezone.utc) - t).total_seconds()
                age_str = f"{int(age_secs // 60)}m ago"
                is_stale = age_secs >= 1800
            except ValueError:
                pass

        if is_stale or args.force:
            lock_path.unlink()
            print(f"  {_GREEN}Removed:{_RESET} {_BOLD}{lock_name}{_RESET}  {_DIM}({age_str}){_RESET}")
        else:
            print(f"  {_YELLOW}Lock is recent{_RESET} ({age_str}) — {op_name} may still be running.")
            force_cmd = "watchdog unlock --force" if inferred else f"watchdog unlock {args.project} --force"
            print(f"  Use {_CYAN}{force_cmd}{_RESET} to remove it anyway.")

    if not found_any:
        print(f"  {_DIM}No locks found — nothing to do.{_RESET}")

    state_file = vault / ".watchdog" / "ingest-state.json"
    if state_file.exists():
        state_file.unlink(missing_ok=True)
        print(f"  {_GREEN}Cleaned:{_RESET}  {_DIM}ingest-state.json{_RESET}")

    tmp_dir = vault / ".watchdog" / "tmp"
    if tmp_dir.exists():
        leftover = list(tmp_dir.glob("wdg_*"))
        for f in leftover:
            f.unlink(missing_ok=True)
        if leftover:
            print(f"  {_GREEN}Cleaned:{_RESET}  {_DIM}{len(leftover)} leftover temp file{'s' if len(leftover) != 1 else ''} from .watchdog/tmp/{_RESET}")

    print()
