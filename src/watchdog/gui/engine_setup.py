"""Non-interactive first-run pieces for the desktop app's managed engine.

`watchdog setup` asks questions at a terminal; the app installs everything itself. The app's main
process builds the Python environment with uv (see gui/src/main/engine.ts), then runs this module
inside it:

    python -m watchdog.gui.engine_setup models [--only docling,gliner,...]
    python -m watchdog.gui.engine_setup check
    python -m watchdog.gui.engine_setup claude-path

`models` downloads each local model and reports one structured line per step through
`watchdog.progress` (the app sets `WATCHDOG_PROGRESS=1`):

    {"kind": "engine", "step": "gliner", "label": "...", "state": "start|ok|warn", "detail": str|null}

A model that fails to download is a warning, not a failure: every one of them is fetched again on
first use, so the app stays usable and the person can retry from Settings. The process exits 0
unless the arguments are wrong.

`check` prints a JSON object saying which pieces are already on disk (no imports of the heavy
libraries, no network). `claude-path` prints the Claude Code binary bundled in claude-agent-sdk.

The engine installs in two phases (D272). Phase 1, which first-run setup waits for, is Python, the
`watchdog` package and only the libraries the app's backend needs to start, read investigations,
change settings and sign in: `core-requirements` prints that list, read from the installed
package's own metadata so pyproject.toml stays the single list of dependencies. Phase 2 runs in the
background: every remaining library, then `models`. The app refuses to add documents until phase 2
has finished (`engine_ready`, which the backend's `jobs.start` checks).

    python -m watchdog.gui.engine_setup core-requirements

Models a person may never need (audio transcription, for instance) are not part of phase 2: they
belong in ON_DEMAND and are downloaded the first time they are needed, with
`models --only <id>`.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable

from watchdog import progress

# (id, label shown in the app). The order is the download order.
STEPS: list[tuple[str, str]] = [
    ("docling", "Document conversion (Docling)"),
    ("gliner", "Name detection (GLiNER)"),
    ("embedding", "Search embedding"),
    ("reranker", "Search reranker"),
    ("ocr", "Text recognition for scans"),
]

# Models downloaded only when first needed, never during setup: (id, label). Each one also needs
# a runner in _RUNNERS; the feature that needs the model runs `models --only <id>` (or calls
# `download_models([id])`) before first use and reports the same progress lines. Empty for now.
ON_DEMAND: list[tuple[str, str]] = []


# ── the two install phases ──────────────────────────────────────────────────────────

# Distributions left out of phase 1: they pull in torch, transformers, onnxruntime and the rest
# of the multi-gigabyte stack, and only adding documents and semantic search use them. Every
# import of them in the package is inside the function that needs it.
PHASE2_ONLY = ("docling", "fastembed", "gliner", "ocrmac")
# Not declared by the package (it arrives through docling and fastembed) but imported at module
# level by watchdog.pipeline.embed, which the search screens load to read index statistics.
PHASE1_EXTRA = ("numpy",)

# Set by the app while phase 2 is incomplete, for the backend and everything it starts.
PENDING_ENV = "WATCHDOG_ENGINE_PENDING"
ENGINE_NOT_READY = ("Watchdog is still setting up. You can add documents when it finishes, "
                    "in a few minutes.")


def engine_ready() -> bool:
    """False while the app's engine is still installing its phase-2 libraries and models. A
    Python the app did not install (a development checkout, pipx) is always ready."""
    return os.environ.get(PENDING_ENV) != "1"


def _requirement_name(req: str) -> str:
    name = req.split(";", 1)[0].strip()
    for sep in "[<>=!~ @(":
        name = name.split(sep, 1)[0]
    return name.strip().lower().replace("_", "-")


def core_requirements(requires: list[str] | None = None) -> list[str]:
    """Phase 1's requirement strings: the package's own dependencies without PHASE2_ONLY and
    without optional extras, plus PHASE1_EXTRA. Markers are kept (the installer evaluates them)."""
    if requires is None:
        from importlib.metadata import requires as _requires
        requires = _requires("watchdog-intel") or []
    out = []
    for req in requires:
        if re.search(r"\bextra\s*==", req):
            continue
        if _requirement_name(req) in PHASE2_ONLY:
            continue
        out.append(req.strip())
    for extra in PHASE1_EXTRA:
        if all(_requirement_name(r) != extra for r in out):
            out.append(extra)
    return out


# ── where things live ───────────────────────────────────────────────────────────────

def _docling_cache() -> Path:
    return Path(os.environ.get("DOCLING_ARTIFACTS_PATH") or Path.home() / ".cache" / "docling" / "models")


def _fastembed_cache() -> Path:
    return Path(os.environ.get("FASTEMBED_CACHE_PATH") or Path.home() / ".cache" / "fastembed")


def _hf_cache() -> Path:
    try:
        from huggingface_hub import constants
        return Path(constants.HF_HUB_CACHE)
    except Exception:  # noqa: BLE001
        home = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
        return Path(home) / "hub"


def _dir_has(root: Path, needle: str) -> bool:
    try:
        return any(needle.lower() in p.name.lower() for p in root.iterdir() if p.is_dir())
    except OSError:
        return False


def bundled_claude_path() -> str | None:
    """The Claude Code binary shipped inside claude-agent-sdk, or None when it is absent."""
    try:
        import claude_agent_sdk
    except ImportError:
        return None
    name = "claude.exe" if sys.platform == "win32" else "claude"
    path = Path(claude_agent_sdk.__file__).parent / "_bundled" / name
    return str(path) if path.is_file() else None


def available_ocr_engine() -> str | None:
    """The pip-installable OCR engine Docling can use here, or None. Apple Vision on macOS and
    Tesseract (when its Python binding is importable) are left to Watchdog's own auto-selection,
    so they are not reported."""
    for module, engine in (("rapidocr", "rapidocr"), ("rapidocr_onnxruntime", "rapidocr"), ("easyocr", "easyocr")):
        try:
            __import__(module)
            return engine
        except ImportError:
            continue
    return None


def preferred_ocr_engine() -> str | None:
    """The `ocr_engine` setting this computer should have when the person has not chosen one: a
    pip-installable engine wherever Tesseract's Python binding is missing (every non-macOS
    computer without a system Tesseract). None means leave the setting alone."""
    if sys.platform == "darwin":
        return None
    try:
        import tesserocr  # noqa: F401
        return None
    except ImportError:
        return available_ocr_engine()


def check() -> dict:
    """Which pieces are already downloaded. Cheap: directory listings only."""
    from watchdog.pipeline import embed

    reranker = None
    if embed._rerank_enabled():
        reranker = _dir_has(_fastembed_cache(), embed._rerank_model_name().split("/")[-1])
    docling = _docling_cache()
    return {
        "docling": docling.is_dir() and any(docling.iterdir()),
        "gliner": _dir_has(_hf_cache(), "gliner_multi-v2.1"),
        "embedding": _dir_has(_fastembed_cache(), embed._MODEL_DEFAULT.split("/")[-1]),
        "reranker": reranker,
        "ocr": available_ocr_engine() if sys.platform != "darwin" else "apple_vision",
        "claude_cli": bundled_claude_path(),
    }


# ── downloads ───────────────────────────────────────────────────────────────────────

def _download_docling() -> str | None:
    from docling.utils.model_downloader import download_models
    download_models(progress=False, with_code_formula=False, with_picture_classifier=False)
    return None


def _download_gliner() -> str | None:
    from watchdog.setup_cmd import _download_gliner_model
    _download_gliner_model()
    return None


def _download_embedding() -> str | None:
    from fastembed import TextEmbedding
    from watchdog.pipeline import embed
    TextEmbedding(embed._MODEL_DEFAULT)
    return None


def _download_reranker() -> str | None:
    from watchdog.pipeline import embed
    if not embed._rerank_enabled():
        return "skipped: the reranker is switched off in settings"
    from fastembed.rerank.cross_encoder import TextCrossEncoder
    TextCrossEncoder(embed._rerank_model_name())
    return None


def _ensure_ocr() -> str | None:
    engine = available_ocr_engine()
    if sys.platform == "darwin":
        return "Apple Vision" if engine is None else f"Apple Vision, with {engine} as a fallback"
    if engine is None:
        raise RuntimeError("no OCR engine is installed; scanned pages cannot be read")
    apply_preferred_ocr()
    return engine


def apply_preferred_ocr() -> str | None:
    """Store `preferred_ocr_engine()` as `ocr_engine` when setup has written a settings file that
    does not name one, as `setup.complete` does. First-run setup usually finishes before the
    engine's background phase has installed the OCR engine, so this step repeats the choice once
    the engine is there. Returns the engine stored, or None."""
    from watchdog.cmd import setup as setup_cmd
    from watchdog.gui.api.settings import _read_config

    if not setup_cmd.CONFIG_FILE.exists():
        return None
    config = _read_config()
    if "ocr_engine" in config:
        return None
    engine = preferred_ocr_engine()
    if engine:
        config["ocr_engine"] = engine
        setup_cmd._persist(config)
    return engine


_RUNNERS: dict[str, Callable[[], str | None]] = {
    "docling": _download_docling,
    "gliner": _download_gliner,
    "embedding": _download_embedding,
    "reranker": _download_reranker,
    "ocr": _ensure_ocr,
}


def download_models(only: list[str] | None = None) -> dict[str, str]:
    """Run each step, emitting progress. Returns {step: "ok" | "warn"}."""
    os.environ.setdefault("FASTEMBED_CACHE_PATH", str(_fastembed_cache()))
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    results: dict[str, str] = {}
    # Without `only`, the setup steps; an ON_DEMAND model runs only when named.
    steps = [s for s in STEPS + ON_DEMAND if s[0] in only] if only else STEPS
    for step, label in steps:
        progress.emit("engine", step=step, label=label, state="start", detail=None)
        try:
            # The libraries print their own download chatter to stderr; the app keeps it in the
            # log disclosure, so it is not discarded here, only kept off the structured stream.
            detail = _RUNNERS[step]()
            results[step] = "ok"
            progress.emit("engine", step=step, label=label, state="ok", detail=detail)
        except Exception as e:  # noqa: BLE001 — a failed optional download must not stop the rest
            results[step] = "warn"
            progress.emit("engine", step=step, label=label, state="warn", detail=str(e)[:400] or type(e).__name__)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m watchdog.gui.engine_setup")
    sub = parser.add_subparsers(dest="command", required=True)
    models = sub.add_parser("models", help="download the local models")
    models.add_argument("--only", default="", help="comma-separated step ids")
    sub.add_parser("check", help="report which pieces are already downloaded")
    sub.add_parser("claude-path", help="print the bundled Claude Code binary")
    sub.add_parser("core-requirements", help="print phase 1's requirements, one per line")
    args = parser.parse_args(argv)

    if args.command == "core-requirements":
        print("\n".join(core_requirements()))
        return 0
    if args.command == "models":
        only = [s for s in args.only.split(",") if s]
        unknown = [s for s in only if s not in _RUNNERS]
        if unknown:
            parser.error(f"unknown step: {', '.join(unknown)}")
        download_models(only or None)
        return 0
    if args.command == "check":
        with contextlib.redirect_stderr(io.StringIO()):
            data = check()
        print(json.dumps(data))
        return 0
    path = bundled_claude_path()
    print(path or "")
    return 0 if path else 1


if __name__ == "__main__":
    sys.exit(main())
