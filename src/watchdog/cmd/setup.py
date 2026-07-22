"""Setup and configuration commands: about, setup, refresh-skills, configure, unlock."""

import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from watchdog import interactive
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
    "extract_concurrency": {
        "short": "Documents extracted in parallel during `watchdog dig` (default: 5)",
        "help": (
            "How many documents `watchdog dig` extracts simultaneously. Each runs a model\n"
            "  call, so this is bounded by your model rate limits — lower it if you hit throttling,\n"
            "  raise it for throughput. Override for one run with `watchdog dig --concurrency N`.\n"
            "  Default: 5. Minimum: 1 (sequential)."
        ),
        "type": "int",
        "default": 5,
        "min": 1,
    },
    "classify_pages": {
        "short": "Pages shown to the document classifier during `watchdog dig` (default: 5)",
        "help": (
            "How many leading pages of each document the classifier reads to pick a record skill.\n"
            "  Uses min(page_count, this). More pages classify ambiguous documents better (e.g. a\n"
            "  cover letter before the real filing) at a small extra cost on the cheap classifier model.\n"
            "  Override for one run with `watchdog dig --classify-pages N`. Default: 5. Minimum: 1."
        ),
        "type": "int",
        "default": 5,
        "min": 1,
    },
    "default_skill": {
        "short": "Pin a record skill for every ingested document, skipping classification (default: unset)",
        "help": (
            "When every document in a vault is the same type, set this to a record-skill name\n"
            "  (a file in the vault's records dir, minus .md) to skip per-document classification\n"
            "  and use that one skill for all of them. Leave unset to classify each document.\n"
            "  Override for one run with: watchdog dig --skill NAME (or --skill to pick one)."
        ),
        "type": "string",
        "default": None,
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
    "section_token_threshold": {
        "short": "Estimated tokens above which a document is split for sectioned extraction ('auto' or a fixed number)",
        "help": (
            "Documents whose estimated token count is at or under this value are extracted\n"
            "  whole; larger ones are split into overlapping sections and extracted sequentially\n"
            "  with a carried-forward entity list. Token count is estimated as chars/4.\n"
            "  'auto' (default): derived from the extraction model's context window (about 60% of\n"
            "  it) — so a 1M-window model like DeepSeek V4 reads far more of a document in one call\n"
            "  than a 200K Claude window (which resolves to 120000). Set a fixed number to pin it as\n"
            "  an advanced override — e.g. lower it if whole-document extraction is overrunning the\n"
            "  model's output ceiling on dense documents.\n"
            "  Note: a fixed number does NOT rescale when you change extractor_model — pin it back to\n"
            "  'auto' (or re-check the value) if you switch to a model with a different context window."
        ),
        "type": "int_or_auto",
        "default": "auto",
        "min": 1,
    },
    "section_token_budget": {
        "short": "Target estimated tokens per section when sectioning a large document ('auto' or a fixed number)",
        "help": (
            "When a document is sectioned (see section_token_threshold), pages are grouped into\n"
            "  sections targeting roughly this many estimated tokens each.\n"
            "  'auto' (default): half the threshold (model-aware), so a document just over the\n"
            "  threshold splits into two sections. Set a fixed number to pin it as an advanced\n"
            "  override.\n"
            "  Note: a fixed number does NOT rescale when you change extractor_model — pin it back to\n"
            "  'auto' (or re-check the value) if you switch to a model with a different context window."
        ),
        "type": "int_or_auto",
        "default": "auto",
        "min": 1,
    },
    "section_overlap_tokens": {
        "short": "Estimated-token overlap between consecutive sections (default: 4000)",
        "help": (
            "Consecutive sections of a large document share this many estimated tokens of overlap,\n"
            "  so entities and events spanning a section boundary aren't lost.\n"
            "  Default: 4000."
        ),
        "type": "int",
        "default": 4_000,
        "min": 0,
    },
    # ── Models ───────────────────────────────────────────────────────────────
    "classifier_model": {
        "short": "Model that picks each document's record skill (default: haiku)",
        "help": (
            "Model used for the cheap classification step that reads a document's first pages\n"
            "  and picks the matching record skill. Haiku is plenty for this; raise it only if\n"
            "  classification is going wrong on ambiguous documents.\n"
            "  Value: a Claude tier (haiku, sonnet, opus), or a backend:model form to route to\n"
            "  another provider (openai:gpt-5-mini, deepseek:deepseek-v4-flash, gemini:gemini-2.5-flash).\n"
            "  Default: haiku.\n"
            "  Override for a single run with: watchdog dig --classifier-model M"
        ),
        "type": "string",
        "default": "haiku",
    },
    "extractor_model": {
        "short": "Model for document extraction (default: sonnet)",
        "help": (
            "Model used to extract each document during `watchdog dig`.\n"
            "  Haiku is cheaper and faster for large batches of straightforward documents;\n"
            "  Sonnet handles complex or ambiguous documents better.\n"
            "  Value: a Claude tier (haiku, sonnet, opus), or a backend:model form to route to\n"
            "  another provider (openai:gpt-5-mini, deepseek:deepseek-v4-flash, gemini:gemini-2.5-flash).\n"
            "  Default: sonnet.\n"
            "  DeepSeek thinking mode is off by default; append -thinking (deepseek:deepseek-v4-flash-thinking)\n"
            "  to enable it. Override for a single run with: watchdog dig --extractor-model M"
        ),
        "type": "string",
        "default": "sonnet",
    },
    "finalizer_model": {
        "short": "Model for the post-ingest step — reconciliation + synthesis + briefing (default: haiku)",
        "help": (
            "Model used for the post-ingest step: merging duplicate entities, flagging\n"
            "  contradictions between documents, synthesizing prose for multi-mention entities,\n"
            "  reconciling timeline collisions, and writing the briefing.\n"
            "  This step works from compact digests rather than reading raw documents, so the\n"
            "  cheaper Haiku tier is the default; raise it if synthesized prose feels thin, if\n"
            "  duplicate entities slip through, or if contradictions are being missed.\n"
            "  Value: a Claude tier (haiku, sonnet, opus), or a backend:model form to route to\n"
            "  another provider (openai:gpt-5-mini, deepseek:deepseek-v4-flash, gemini:gemini-2.5-flash).\n"
            "  Default: haiku.\n"
            "  Override for a single run with: watchdog bark --finalizer-model M"
        ),
        "type": "string",
        "default": "haiku",
    },
    "extractor_effort": {
        "short": "Reasoning effort for document extraction (default: high)",
        "help": (
            "How hard the extractor model thinks. Thinking tokens bill as output, so a lower\n"
            "  effort spends fewer tokens per document — the main cost lever for an extraction run.\n"
            "  'high' is the model default (unchanged behaviour); try 'medium' or 'low' to cut cost\n"
            "  and verify extraction quality holds. Ignored when the extractor is Haiku (which has\n"
            "  no effort control).\n"
            "  Valid values: low, medium, high. Default: high.\n"
            "  Override for a single run with: watchdog dig --extractor-effort E"
        ),
        "type": "enum",
        "default": "high",
        "choices": ["low", "medium", "high"],
    },
    "finalizer_effort": {
        "short": "Reasoning effort for the post-ingest step (default: high)",
        "help": (
            "How hard the finalizer model thinks during post-ingest. Reasoning helps the prose\n"
            "  steps, so keep this higher than the extractor unless cost-trimming; lower it to spend\n"
            "  fewer tokens. Ignored when the finalizer is Haiku (which has no effort control).\n"
            "  Valid values: low, medium, high. Default: high.\n"
            "  Override for a single run with: watchdog bark --finalizer-effort E"
        ),
        "type": "enum",
        "default": "high",
        "choices": ["low", "medium", "high"],
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
    # ── Search ────────────────────────────────────────────────────────────────
    "embed_model": {
        "short": "Local embedding model for semantic search (default: BAAI/bge-small-en-v1.5)",
        "help": (
            "The fastembed model used to index source passages and notes for `watchdog search`.\n"
            "  Runs entirely on your machine — no API, no cost, nothing leaves the computer.\n"
            "  bge-small-en-v1.5 is small (67 MB), fast, and a strong retriever; raise it only if\n"
            "  you want more recall on a large vault. Stronger fastembed options, biggest gain first:\n"
            "    mxbai-embed-large-v1   (1024-dim, 640 MB — best quality-per-MB)\n"
            "    BAAI/bge-base-en-v1.5  (768-dim, 210 MB — same family, easy step up)\n"
            "    snowflake/snowflake-arctic-embed-s  (384-dim, 130 MB — same size class)\n"
            "  Must be a model fastembed can load (see `TextEmbedding.list_supported_models()`).\n"
            "  After changing this, run `watchdog reindex` — vectors from two models aren't\n"
            "  comparable, so the index is rebuilt from disk (no re-ingest needed).\n"
            "  Default: BAAI/bge-small-en-v1.5."
        ),
        "type": "str",
        "default": "BAAI/bge-small-en-v1.5",
    },
    "rerank_model": {
        "short": "Local cross-encoder that reranks corpus search results (default: BAAI/bge-reranker-base)",
        "help": (
            "After `watchdog search` fuses the dense (embedding) and sparse (BM25) candidate\n"
            "  lists, a cross-encoder reranks the top of that pool for precision — the biggest\n"
            "  single retrieval-quality lever (Anthropic contextual-retrieval). Runs entirely on\n"
            "  your machine via fastembed — no API, no cost. Pre-downloaded by `watchdog setup`,\n"
            "  otherwise on first search (~300 MB).\n"
            "  Set to `none` (or empty) to turn reranking off and rank by fusion alone.\n"
            "  Other fastembed cross-encoders: Xenova/ms-marco-MiniLM-L-6-v2 (English, ~90 MB),\n"
            "    jinaai/jina-reranker-v2-base-multilingual.\n"
            "  Default: BAAI/bge-reranker-base."
        ),
        "type": "str",
        "default": "BAAI/bge-reranker-base",
    },
    # ── Research ──────────────────────────────────────────────────────────────
    "research_max_rounds": {
        "short": "Default search rounds for `watchdog research` standard effort (default: 3)",
        "help": (
            "How many search rounds the /watchdog-research skill runs by default before it must\n"
            "  check in and stop. An advisory budget the interactive skill self-limits to — a wider\n"
            "  net (the 'deep' effort tier) overrides it per run. Higher = more thorough, more cost.\n"
            "  Default: 3. Minimum: 1."
        ),
        "type": "int",
        "default": 3,
        "min": 1,
    },
    "research_max_fetches": {
        "short": "Default sources captured per `watchdog research` standard run (default: 25)",
        "help": (
            "About how many web sources the /watchdog-research skill captures into _INCOMING/ in a\n"
            "  default (standard-effort) run. An advisory budget the interactive skill self-limits to;\n"
            "  the 'quick' and 'deep' effort tiers scale it down or up per run. Each captured source\n"
            "  is later read by the local pipeline, not in the research session, so this bounds scope\n"
            "  and ingest cost rather than session tokens. Default: 25. Minimum: 1."
        ),
        "type": "int",
        "default": 25,
        "min": 1,
    },
    # ── Web archiving ─────────────────────────────────────────────────────────
    "wayback_save": {
        "short": "Also save each research source to the Wayback Machine (default: false)",
        "help": (
            "When enabled, `watchdog research` submits every source it downloads to the Internet\n"
            "  Archive's Wayback Machine (Save Page Now), and records the resulting snapshot URL in\n"
            "  each source's provenance sidecar — a permanent, citable copy that survives if the\n"
            "  original page later changes or is taken down. Off by default, and a no-op until both\n"
            "  wayback_access_key and wayback_secret_key are set. The local download is unaffected —\n"
            "  archiving is a best-effort bonus that never blocks or fails a download.\n"
            "  Default: false."
        ),
        "type": "bool",
        "default": False,
    },
    "wayback_access_key": {
        "short": "archive.org S3 access key (for wayback_save)",
        "help": (
            "The 'S3-like' access key for your archive.org account, used to authenticate Save Page\n"
            "  Now requests when wayback_save is on. Create a free account and generate keys at\n"
            "  https://archive.org/account/s3.php. Stored in this config file; leave unset to disable.\n"
            "  Paired with wayback_secret_key."
        ),
        "type": "string",
        "secret": True,
    },
    "wayback_secret_key": {
        "short": "archive.org S3 secret key (for wayback_save)",
        "help": (
            "The 'S3-like' secret key that pairs with wayback_access_key. Generate both together at\n"
            "  https://archive.org/account/s3.php. Stored in this config file; leave unset to disable."
        ),
        "type": "string",
        "secret": True,
    },
}

# Display grouping for the `watchdog configure` listing (presentation only — set/get is
# unaffected). Each section owns an ordered list of keys; any key missing from every
# section is still shown, under "Other", so a newly added key never silently disappears.
_CONFIGURE_SECTIONS = [
    ("Vaults", "Where investigations are created.",
     ["projects_dir"]),
    ("OCR", "Text recognition for scanned documents.",
     ["ocr_engine", "ocr_languages", "garbled_threshold"]),
    ("Chew", "Local preprocessing — parallelism and large-PDF handling.",
     ["chew_workers", "chunk_size", "chunk_workers", "chunk_timeout", "table_structure"]),
    ("Ingest", "Extraction run — parallelism, classification, skill pinning, sectioning.",
     ["extract_concurrency", "classify_pages", "default_skill",
      "section_token_threshold", "section_token_budget", "section_overlap_tokens"]),
    ("Models", "Which Claude model runs each step, and how hard it thinks.",
     ["classifier_model", "extractor_model", "finalizer_model",
      "extractor_effort", "finalizer_effort"]),
    ("Deduplication", "Near-duplicate detection.",
     ["dup_threshold", "shingle_size"]),
    ("Search", "Local semantic search over the source corpus.",
     ["embed_model", "rerank_model"]),
    ("Research", "Web research mode — default effort budget for `watchdog research`.",
     ["research_max_rounds", "research_max_fetches"]),
    ("Web archiving", "Optionally save research sources to the Wayback Machine.",
     ["wayback_save", "wayback_access_key", "wayback_secret_key"]),
]

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
    print(r'      ___                    ')
    print(r'   __/_  `.  .-"""-.         ')
    print(r"   \_,` | \-'  /   )`-')    ")
    print(r'    "") `"`    \  ((`"`      ')
    print(r"   ___Y  ,    .'7 /|         ")
    print(r"  (_,___/...-` (_/_/         ")
    print()
    print(f"  {_BOLD}Watchdog{_RESET}  {_DIM}v{__version__}{_RESET}")
    print(f"  {_DIM}Investigative journalism document intelligence{_RESET}")
    print()
    print(f"  {_DIM}GitHub   {_RESET}{_CYAN}https://github.com/tomcardoso/watchdog{_RESET}")
    print(f"  {_DIM}Issues   {_RESET}{_CYAN}https://github.com/tomcardoso/watchdog/issues{_RESET}")
    print(f"  {_DIM}Install  {_RESET}{_CYAN}https://github.com/tomcardoso/watchdog/blob/main/docs/install.md{_RESET}")
    print()
    print(f"  {_DIM}ASCII art: dog in 'watchdog new' by Felix Lee; dog in 'watchdog about' by Sarah Kearsley{_RESET}")
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


def cmd_show_skills(args) -> None:
    """List the global record skills, or print one. With no name, also opens the skills
    folder on GitHub so the full text is easy to read."""
    import webbrowser
    from watchdog import skills_catalog

    catalog = skills_catalog.catalog()
    name = getattr(args, "name", None)

    if name:
        canon = name.removesuffix(".md")
        if canon not in catalog:
            sys.exit(f"\n  {_YELLOW}Error:{_RESET} no record skill {_BOLD}{canon}{_RESET}.\n"
                     f"  Run {_CYAN}watchdog show-skills{_RESET}{_DIM} to list them.{_RESET}\n")
        print()
        print(Path(catalog[canon]).read_text(encoding="utf-8"))
        return

    print()
    print(f"  {_BOLD}Record skills{_RESET}  {_DIM}{len(catalog)} available{_RESET}")
    print()
    for n, path in catalog.items():
        desc = skills_catalog._skill_descriptor(Path(path).read_text(encoding="utf-8"))
        if len(desc) > 96:
            desc = desc[:95].rstrip() + "…"
        print(f"  {_CYAN}{n}{_RESET}")
        print(f"  {_DIM}{desc}{_RESET}\n")

    try:
        ver = _pkg_version()
    except Exception:
        ver = None
    url = skills_catalog.github_skills_url("main")
    print(f"  {_DIM}Read the full text:{_RESET} {_CYAN}{url}{_RESET}")
    print(f"  {_DIM}Print one:{_RESET} {_CYAN}watchdog show-skills <name>{_RESET}")
    print(f"  {_DIM}Add your own:{_RESET} {_CYAN}{skills_catalog.USER_SKILLS_DIR}{_RESET}")
    if ver:
        print(f"  {_DIM}(installed watchdog-intel {ver}){_RESET}")
    print()
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _pkg_version() -> str:
    from importlib.metadata import version
    return version("watchdog-intel")


def _pick_skill_arrow(catalog: dict, current) -> tuple[str, str | None]:
    """Arrow-key picker for a record skill. Returns ``(action, value)`` where action is
    ``"set"`` (value = chosen skill name or a typed name/path), ``"unset"``, or ``"cancel"``.

    Built on the shared `interactive.pick()` (raw-mode with a numbered fallback)."""
    names = list(catalog)
    items = names + ["(unset — classify each document)", "Type my own…"]
    idx   = names.index(current) if current in names else 0

    def _ask_custom() -> tuple[str, str | None]:
        try:
            ans = input("\n  Skill name or file path (Enter to cancel): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return ("cancel", None)
        return ("set", ans) if ans else ("cancel", None)

    result = interactive.pick(items, idx, title="Pin a record skill")
    if result is interactive.CANCELLED:
        return ("cancel", None)
    if result == len(names):        # "(unset — classify each document)" row
        return ("unset", None)
    if result == len(names) + 1:    # "Type my own…" row
        return _ask_custom()
    return ("set", names[result])


def _configure_default_skill_interactive(key: str, config: dict) -> str | None:
    """Interactive ``configure default_skill``: show the skills catalog's GitHub link, run the
    arrow-key picker, and handle unset/cancel directly. Returns the chosen skill name or path
    for the caller to persist (the ``"set"`` case), or ``None`` when there's nothing left to do
    (unset, cancel, or no skills available)."""
    from watchdog import skills_catalog
    catalog = skills_catalog.catalog()
    url = skills_catalog.github_skills_url("main")
    print(f"  {_DIM}Read the full text:{_RESET} {_CYAN}{url}{_RESET}")
    if not catalog:
        print(f"\n  {_DIM}No record skills available — classification stays on.{_RESET}\n")
        return None

    action, picked = _pick_skill_arrow(catalog, config.get(key))
    if action == "cancel":
        print(f"  {_DIM}No change.{_RESET}\n")
        return None
    if action == "unset":
        config.pop(key, None)
        _persist(config)
        print(f"  {_GREEN}Set:{_RESET} {_BOLD}{key}{_RESET} = "
              f"{_DIM}(not set — classify each document){_RESET}\n")
        return None
    if picked not in catalog and not Path(picked).expanduser().is_file():
        print(f"  {_YELLOW}Note:{_RESET} {_BOLD}{picked}{_RESET} isn't a known skill or an "
              f"existing file; {_DIM}ingest will re-check it.{_RESET}")
    return picked


class _ConfigError(Exception):
    """Raised by `_coerce_value` when a value fails validation. Callers decide whether to
    exit (command-line set) or print and continue (interactive edit / wizard)."""


def _persist(config: dict) -> None:
    WATCHDOG_HOME.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")
    os.chmod(CONFIG_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0600 — mirrors auth._save_state (#304);
    # config.json holds the archive.org S3 wayback_secret_key in plaintext, so this is
    # unconditional on every persist to correct an existing loose-permission file too.


def _auto_resolved_hint(key: str, config: dict) -> str:
    """Render 'auto' for a model-aware section budget along with the concrete value it currently
    resolves to for the configured extractor model, e.g. 'auto (120000 — sonnet)'."""
    from watchdog.pipeline import section
    model = config.get("extractor_model")   # None ⇒ default tier
    threshold, budget = section.model_defaults(model)
    resolved = threshold if key == "section_token_threshold" else budget
    tier = model or "sonnet"
    return f"{_CYAN}auto{_RESET} {_DIM}({resolved} — {tier}){_RESET}"


def _display_value(k, v, config=None):
    meta = _CONFIGURE_KEYS.get(k, {})
    # Model-aware section budgets: 'auto' (or an unset key) resolves to a value derived from the
    # extraction model's context window — show that resolved value when we have the config.
    if k in ("section_token_threshold", "section_token_budget") and (v is None or v == "auto"):
        return _auto_resolved_hint(k, config) if config is not None else f"{_CYAN}auto{_RESET}"
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
    if meta.get("secret") and v:
        return f"{_CYAN}••••••••{_RESET} {_DIM}(set){_RESET}"  # never print a stored secret back
    return f"{_CYAN}{v}{_RESET}"


def _coerce_value(config: dict, key: str, value: str) -> str:
    """Validate and coerce raw string `value` for `key`, mutating `config` in place and
    returning the display string. Raises `_ConfigError` on invalid input. Side effects:
    `projects_dir` creates the directory; `ocr_engine` ensures the engine package."""
    meta = _CONFIGURE_KEYS[key]
    if key == "ocr_languages":
        langs = [lang.strip() for lang in value.split(",") if lang.strip()]
        config[key] = langs
        return ", ".join(langs) if langs else "auto-detect"
    if key == "projects_dir":
        path = Path(value).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        config[key] = str(path)
        return str(path)
    if meta["type"] == "float":
        try:
            v = float(value)
        except ValueError:
            raise _ConfigError(f"'{key}' must be a number (e.g. 0.85)")
        lo, hi = meta.get("min"), meta.get("max")
        if lo is not None and v < lo:
            raise _ConfigError(f"'{key}' must be >= {lo}")
        if hi is not None and v > hi:
            raise _ConfigError(f"'{key}' must be <= {hi}")
        config[key] = v
        return str(v)
    if meta["type"] == "int_or_auto":
        if value.lower() == "auto":
            config[key] = "auto"
            return "auto"
        try:
            v = int(value)
        except ValueError:
            raise _ConfigError(f"'{key}' must be 'auto' or a whole number")
        lo = meta.get("min")
        if lo is not None and v < lo:
            raise _ConfigError(f"'{key}' must be >= {lo}")
        config[key] = v
        return str(v)
    if meta["type"] == "int":
        try:
            v = int(value)
        except ValueError:
            raise _ConfigError(f"'{key}' must be a whole number")
        lo = meta.get("min")
        if lo is not None and v < lo:
            raise _ConfigError(f"'{key}' must be >= {lo}")
        config[key] = v
        return str(v)
    if meta["type"] == "bool":
        if value.lower() in ("true", "yes", "1", "on"):
            v = True
        elif value.lower() in ("false", "no", "0", "off"):
            v = False
        else:
            raise _ConfigError(f"'{key}' must be true or false")
        config[key] = v
        return "true" if v else "false"
    if meta["type"] == "enum":
        choices = meta.get("choices", [])
        if value not in choices:
            raise _ConfigError(f"'{key}' must be one of: {', '.join(choices)}")
        config[key] = value
        if key == "ocr_engine":
            _ensure_ocr_engine(value)
        return value
    config[key] = value
    return value


# Provider name -> the `_OPENAI_PRICING` model-id prefix used to group its models in the picker.
_PROVIDER_MODEL_PREFIXES = {"openai": "gpt-", "deepseek": "deepseek-", "gemini": "gemini-"}


def _pick_model_interactive(current: str | None = None, *, only_provider: str | None = None) -> str | None:
    """Arrow-key picker for a `[backend:]model` config value (classifier_model/extractor_model/
    finalizer_model). Offers Claude tiers plus every OpenAI/DeepSeek/Gemini model
    `model_client.py` has pricing for, grouped under section headers, with a free-text escape
    hatch. Pass `only_provider` to show just that provider's models (e.g. from the metered-
    ingestion setup wizard, which already knows the provider). Returns the chosen value, or
    ``None`` if cancelled or left blank."""
    from watchdog.model_client import _MODEL_IDS, _OPENAI_PRICING

    if only_provider:
        prefix = _PROVIDER_MODEL_PREFIXES[only_provider]
        groups = [(None, only_provider, [m for m in _OPENAI_PRICING if m.startswith(prefix)])]
    else:
        groups = [
            ("Claude", None, list(_MODEL_IDS)),
            ("OpenAI", "openai", [m for m in _OPENAI_PRICING if m.startswith("gpt-")]),
            ("DeepSeek", "deepseek", [m for m in _OPENAI_PRICING if m.startswith("deepseek-")]),
            ("Gemini", "gemini", [m for m in _OPENAI_PRICING if m.startswith("gemini-")]),
        ]

    items: list = []
    item_values: list[str | None] = []   # parallel to items; None for Header rows
    for title, backend, models in groups:
        if not models:
            continue
        if title:
            items.append(interactive.Header(title))
            item_values.append(None)
        for m in models:
            items.append(f"  {m}")
            item_values.append(m if backend is None else f"{backend}:{m}")
    items.append("  Type my own…")
    item_values.append("__custom__")

    initial = item_values.index(current) if current in item_values else 0
    result = interactive.pick(items, initial, title="Choose a model")
    if result is interactive.CANCELLED:
        return None
    chosen = item_values[result]
    if chosen == "__custom__":
        value = input("  Model (e.g. openai:gpt-5-mini): ").strip()
        return value or None
    return chosen


def _edit_key_interactive(config: dict, key: str) -> None:
    """Show a key's help and current value, prompt for a new value, then coerce, persist, and
    report. Used by `watchdog configure <key>` and the wizard. Prints an error and returns
    (without exiting) on invalid input so the wizard can carry on."""
    meta = _CONFIGURE_KEYS[key]
    print(f"\n  {_BOLD}{key}{_RESET}\n")
    for line in meta["help"].split("\n"):
        print(f"  {_DIM}{line.strip()}{_RESET}")
    print()
    print(f"  Current value:  {_display_value(key, config.get(key), config)}")
    if key in ("chunk_workers", "chew_workers"):
        print(f"  Machine cores:  {os.cpu_count() or 1}")

    if key == "default_skill":
        value = _configure_default_skill_interactive(key, config)
        if value is None:   # unset / cancel handled inside, or no skills
            return
    elif meta["type"] == "bool":
        # A two-state value doesn't need a free-text box — toggle it. The capitalised letter in the
        # bracket is what Enter does, so Enter always keeps the current value.
        current = config.get(key)
        current = bool(meta.get("default", False) if current is None else current)
        print()
        answer = interactive.read_answer(
            f"  Enable? [{'Y/n' if current else 'y/N'}]  (Enter to keep current) ")
        if answer == "":
            print(f"\n  {_DIM}No change.{_RESET}\n")
            return
        if answer in ("y", "yes", "true", "on", "1"):
            value = "true"
        elif answer in ("n", "no", "false", "off", "0"):
            value = "false"
        else:
            print(f"\n  {_YELLOW}Error:{_RESET} enter y or n\n")
            return
    elif key in ("classifier_model", "extractor_model", "finalizer_model"):
        current = config.get(key, meta.get("default"))
        print()
        value = _pick_model_interactive(current)
        if value is None:
            print(f"\n  {_DIM}No change.{_RESET}\n")
            return
        # Routing a stage to a provider we have no key for would leave it broken until the next
        # ingest failed — ask now, while the user is thinking about that provider.
        from watchdog.cmd.auth import ensure_provider_key
        ensure_provider_key(value)
    elif meta["type"] == "enum":
        choices = meta.get("choices", [])
        current = config.get(key, meta.get("default"))
        idx = choices.index(current) if current in choices else 0
        print()
        result = interactive.pick(choices, idx, title="Choose a value")
        if result is interactive.CANCELLED:
            print(f"\n  {_DIM}No change.{_RESET}\n")
            return
        value = choices[result]
    else:
        print()
        if not interactive.confirm("  Change this value?", default=False):
            print()
            return
        print()
        value = input("  New value: ").strip()
        if not value:
            print(f"\n  {_DIM}No change.{_RESET}\n")
            return

    try:
        display = _coerce_value(config, key, value)
    except _ConfigError as e:
        print(f"\n  {_YELLOW}Error:{_RESET} {e}\n")
        return
    _persist(config)
    print(f"\n  {_GREEN}Set:{_RESET} {_BOLD}{key}{_RESET} = {_CYAN}{display}{_RESET}\n")


def _wizard_menu(config: dict, initial_sel: int = 0):
    """Flat arrow-key menu over every configure key, grouped under dim section headers and
    showing each key's current value. Returns ``(key, sel)`` — the chosen key and the menu
    position so the caller can restore it on the next pass — or ``(None, sel)`` to quit.

    Built on the shared `interactive.pick()` (raw-mode with a numbered fallback)."""
    def _label(k):
        return f"{k:<22} {_display_value(k, config.get(k), config)}"

    items: list = []   # str (selectable key label) or interactive.Header
    keys: list = []    # keys, parallel to the selectable items in `items`
    shown = set()

    for title, _blurb, section_keys in _CONFIGURE_SECTIONS:
        present = [k for k in section_keys if k in _CONFIGURE_KEYS]
        if not present:
            continue
        items.append(interactive.Header(title))
        for k in present:
            items.append(_label(k))
            keys.append(k)
            shown.add(k)
    leftovers = [k for k in _CONFIGURE_KEYS if k not in shown]
    if leftovers:
        items.append(interactive.Header("Other"))
        for k in leftovers:
            items.append(_label(k))
            keys.append(k)

    if not keys:
        return (None, 0)
    sel = max(0, min(initial_sel, len(keys) - 1))

    result = interactive.pick(items, sel, title="Configure",
                               hint="↑/↓ move · Enter edit · q quit")
    if result is interactive.CANCELLED:
        return (None, sel)

    selectable_positions = [i for i, it in enumerate(items) if not isinstance(it, interactive.Header)]
    new_sel = selectable_positions.index(result)
    return (keys[new_sel], new_sel)


def _run_configure_wizard(config: dict) -> None:
    """Interactive loop: pick a setting from the flat arrow menu, edit it, repeat until quit."""
    sel = 0
    while True:
        key, sel = _wizard_menu(config, sel)
        if key is None:
            print()
            return
        _edit_key_interactive(config, key)


def cmd_configure(args) -> None:
    config = {}
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text())
        except json.JSONDecodeError:
            sys.exit("Error: config file is corrupt. Try running 'watchdog setup --force'.")

    key   = getattr(args, "key",   None)
    value = getattr(args, "value", None)

    if key is None:
        print()
        print(f"  {_BOLD}Configuration{_RESET}  {_DIM}{CONFIG_FILE}{_RESET}")
        print(f"  {_DIM}Model authentication (Claude subscription/API key, other provider keys) is"
              f" managed separately — see{_RESET} {_CYAN}watchdog auth{_RESET}{_DIM}.{_RESET}")

        def _print_key(k):
            meta = _CONFIGURE_KEYS[k]
            print(f"  {_DIM}{k:<26}{_RESET} {_display_value(k, config.get(k), config)}")
            print(f"  {' ' * 26} {_DIM}{meta['short']}{_RESET}")
            print()

        shown = set()
        for title, blurb, keys in _CONFIGURE_SECTIONS:
            print()
            print(f"  {_BOLD}{title}{_RESET}  {_DIM}{blurb}{_RESET}")
            print()
            for k in keys:
                if k in _CONFIGURE_KEYS:
                    _print_key(k)
                    shown.add(k)
        leftovers = [k for k in _CONFIGURE_KEYS if k not in shown]
        if leftovers:
            print()
            print(f"  {_BOLD}Other{_RESET}")
            print()
            for k in leftovers:
                _print_key(k)

        if sys.stdin.isatty():
            print()
            try:
                answer = input("  Configure a setting? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if answer in ("y", "yes"):
                _run_configure_wizard(config)
        return

    if key not in _CONFIGURE_KEYS:
        sys.exit(f"Error: unknown key '{key}'. Known keys: {', '.join(_CONFIGURE_KEYS)}")

    if value is None:
        if sys.stdin.isatty():
            _edit_key_interactive(config, key)
        else:
            print(f"\n  {_BOLD}{key}{_RESET} = {_display_value(key, config.get(key), config)}\n")
        return

    try:
        display = _coerce_value(config, key, value)
    except _ConfigError as e:
        sys.exit(f"Error: {e}")
    _persist(config)
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
        (vault / ".watchdog" / "registry" / ".ingest-lock",  ".ingest-lock", "ingest"),
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
