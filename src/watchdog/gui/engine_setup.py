"""Non-interactive first-run pieces for the desktop app's managed engine.

The app installs everything itself. The app's main
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
import importlib
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable

from watchdog import progress
from watchdog.tls import os_trust_store

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
# `download_models([id])`) before first use and reports the same progress lines. The
# speech-to-text model is 0.5 to 1.6 GB and only some investigations include recordings, so it is
# fetched the first time a recording is pre-processed or from Settings > Setup (D273). The capture
# browser (Playwright and Chromium, about 150 MB) is added from Settings > Setup only (D357).
ON_DEMAND: list[tuple[str, str]] = [("transcription", "Transcription model"), ("capture", "Capture browser")]


# ── the two install phases ──────────────────────────────────────────────────────────

# Distributions left out of phase 1: they pull in torch, transformers, onnxruntime and the rest
# of the multi-gigabyte stack, and only adding documents and semantic search use them. Every
# import of them in the package is inside the function that needs it.
PHASE2_ONLY = ("docling", "fastembed", "gliner", "ocrmac", "faster-whisper", "av")
# Not declared by the package (it arrives through docling and fastembed) but imported at module
# level by watchdog.pipeline.embed, which the search screens load to read index statistics.
PHASE1_EXTRA = ("numpy",)

# Set by the app while phase 2 is incomplete, for the backend and everything it starts.
PENDING_ENV = "WATCHDOG_ENGINE_PENDING"
ENGINE_NOT_READY = ("Watchdog is still setting up. You can add documents when it finishes, "
                    "in a few minutes.")


def engine_ready() -> bool:
    """False while the app's engine is still installing its phase-2 libraries and models. A
    Python the app did not install (a development checkout) is always ready."""
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
        if os.environ.get("HF_HUB_CACHE"):
            return Path(os.environ["HF_HUB_CACHE"])
        home = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
        return Path(home) / "hub"


# Written as a step's record before its download starts and replaced by the fingerprint when it
# finishes, so a download that stopped part-way (a certificate error on the second repository, the
# app quit, the connection lost) is never taken for a finished one.
INCOMPLETE = "incomplete"


def _docling_complete() -> bool:
    """Docling's models are all on disk. Its download fetches several repositories into one folder,
    so "the folder has something in it" is not enough (D357): a step that began and did not finish
    leaves the INCOMPLETE record, and Hugging Face's own `.incomplete` files mark a file cut off
    mid-transfer."""
    root = _docling_cache()
    try:
        if not root.is_dir() or not any(root.iterdir()):
            return False
        if any(root.rglob("*.incomplete")):
            return False
    except OSError:
        return False
    return _recorded().get("docling") != INCOMPLETE


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
    return {
        "docling": _docling_complete(),
        "gliner": _dir_has(_hf_cache(), "gliner_multi-v2.1"),
        "embedding": _dir_has(_fastembed_cache(), embed._MODEL_DEFAULT.split("/")[-1]),
        "reranker": reranker,
        "ocr": available_ocr_engine() if sys.platform != "darwin" else "apple_vision",
        "claude_cli": bundled_claude_path(),
        **_transcription_check(),
    }


def _transcription_check() -> dict:
    """Whether the configured transcription model's weights are in the hub cache, read off the
    snapshot folders (no import of the hub library, no network)."""
    from watchdog.pipeline import transcribe
    name = transcribe.configured_model()
    repo_dir = _hf_cache() / ("models--" + transcribe.model_repo(name).replace("/", "--"))
    try:
        cached = any((snap / "model.bin").exists() for snap in (repo_dir / "snapshots").iterdir())
    except OSError:
        cached = False
    return {"transcription": cached, "transcription_model": name,
            "transcription_size_mb": transcribe.MODELS[name]["size_mb"]}


# ── what is already downloaded, and for which version (D356) ───────────────────────

def _version(dist: str) -> str | None:
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version(dist)
    except PackageNotFoundError:
        return None


def fingerprints() -> dict[str, str | None]:
    """What each downloaded model is, by step: the library version or model name that decides which
    files the download fetches. A change to one means the model must be fetched again. A step
    missing here or None always runs (OCR is not a download; the transcription model is guarded by
    its own cache check)."""
    from watchdog.pipeline import embed
    docling = _version("docling")
    return {
        "docling": f"docling {docling} {sorted(_DOCLING_ARGS.items())}" if docling else None,
        "gliner": _GLINER_REPO,
        "embedding": embed._MODEL_DEFAULT,
        "reranker": embed._rerank_model_name() if embed._rerank_enabled() else "off",
    }


def _record_path() -> Path:
    from watchdog import config
    return config.home() / "models.json"


def _recorded() -> dict[str, str]:
    try:
        data = json.loads(_record_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _record(step: str, fingerprint: str) -> None:
    """Remember that `step` finished for this fingerprint. Written only after a download succeeds,
    so a failed one is attempted again at the next run."""
    path = _record_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({**_recorded(), step: fingerprint}, indent=2))
        tmp.replace(path)
    except OSError:
        pass  # the next run downloads again; nothing else depends on the record


def _up_to_date(step: str, current: dict[str, str | None], on_disk: dict) -> bool:
    """True when `step` was downloaded for the fingerprint it has now and its files are still on
    disk (a cleared cache is noticed, and fetched again)."""
    fp = current.get(step)
    return fp is not None and _recorded().get(step) == fp and bool(on_disk.get(step))


# ── downloads ───────────────────────────────────────────────────────────────────────

# What the Docling download fetches; part of its fingerprint, so changing it re-downloads.
_DOCLING_ARGS = {"progress": False, "with_code_formula": False, "with_picture_classifier": False}
_GLINER_REPO = "urchade/gliner_multi-v2.1"


def _download_docling() -> str | None:
    from docling.utils.model_downloader import download_models
    download_models(**_DOCLING_ARGS)
    return None


def _download_gliner() -> str | None:
    from gliner import GLiNER
    GLiNER.from_pretrained(_GLINER_REPO)
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


def _download_transcription() -> str | None:
    from watchdog.pipeline import transcribe
    name = transcribe.configured_model()
    if transcribe.model_cached(name):
        return f"{name}, already downloaded"
    # Byte progress as `model` events, the same ones a job shows when a recording triggers it.
    transcribe.ensure_model(name, progress.emit)
    return name


def _node_env() -> dict[str, str]:
    """The environment for Playwright's downloader, which runs on Node and so ignores the OS trust
    store that `os_trust_store` gives Python (D226): `NODE_USE_SYSTEM_CA` where Node supports it, and
    on macOS a bundle of the keychains' certificates in `NODE_EXTRA_CA_CERTS`. Anything the person
    set themselves is left alone."""
    env = dict(os.environ)
    env.setdefault("NODE_USE_SYSTEM_CA", "1")
    if sys.platform == "darwin" and "NODE_EXTRA_CA_CERTS" not in env:
        bundle = _macos_ca_bundle()
        if bundle:
            env["NODE_EXTRA_CA_CERTS"] = str(bundle)
    return env


def _macos_ca_bundle() -> Path | None:
    """The System and system-root keychains' certificates as one PEM file, rebuilt on every call so
    a rotated corporate certificate is picked up. None when `security` fails."""
    import subprocess
    from watchdog import config
    pems = []
    for keychain in ("/Library/Keychains/System.keychain", "/System/Library/Keychains/SystemRootCertificates.keychain"):
        try:
            r = subprocess.run(["security", "find-certificate", "-a", "-p", keychain],
                               capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0 and r.stdout.strip():
            pems.append(r.stdout)
    if not pems:
        return None
    path = config.home() / "node-ca.pem"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(pems))
    except OSError:
        return None
    return path


def _run_quietly(cmd: list[str], env: dict[str, str]) -> None:
    """Run one install command; on failure raise with the end of its output, which is what says why."""
    import subprocess
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-6:])
        raise RuntimeError(f"{' '.join(cmd[2:5])} failed (exit {r.returncode}).\n{tail}")


def _install_capture() -> str | None:
    """Playwright and the Chromium it drives, for full-page snapshots (D357). pip installs the package
    into this engine; `playwright install chromium` then fetches the browser (about 150 MB)."""
    from watchdog.pipeline import capture
    if capture.render_available():
        return "already installed"
    env = {**_node_env(), "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    _run_quietly([sys.executable, "-m", "pip", "install", "playwright>=1.49"], env)
    importlib.invalidate_caches()   # this process looks for the package it has just been given
    _run_quietly([sys.executable, "-m", "playwright", "install", "chromium"], env)
    if not capture.render_available():
        raise RuntimeError("the browser installed, but Watchdog could not start it")
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
    from watchdog import config
    from watchdog.gui.api.settings import _read_config

    if not config.path().exists():
        return None
    cfg = _read_config()
    if "ocr_engine" in cfg:
        return None
    engine = preferred_ocr_engine()
    if engine:
        cfg["ocr_engine"] = engine
        config.save(cfg)
    return engine


_RUNNERS: dict[str, Callable[[], str | None]] = {
    "docling": _download_docling,
    "gliner": _download_gliner,
    "embedding": _download_embedding,
    "reranker": _download_reranker,
    "ocr": _ensure_ocr,
    "transcription": _download_transcription,
    "capture": _install_capture,
}


# Why each step of the last `download_models` call failed, by step, for an operation to report.
ERRORS: dict[str, str] = {}


def download_models(only: list[str] | None = None) -> dict[str, str]:
    """Run each step, emitting progress. Returns {step: "ok" | "warn"}; ERRORS says why."""
    ERRORS.clear()
    os.environ.setdefault("FASTEMBED_CACHE_PATH", str(_fastembed_cache()))
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    results: dict[str, str] = {}
    # Without `only`, the setup steps; an ON_DEMAND model runs only when named.
    steps = [s for s in STEPS + ON_DEMAND if s[0] in only] if only else STEPS
    current = fingerprints()
    try:
        on_disk = check()
    except Exception:  # noqa: BLE001 — without it every step simply runs
        on_disk = {}
    for step, label in steps:
        progress.emit("engine", step=step, label=label, state="start", detail=None)
        try:
            # A model already downloaded for this version is left alone: no network, so an update
            # that changes no model finishes at once and a blocked connection costs nothing (D356).
            if _up_to_date(step, current, on_disk):
                results[step] = "ok"
                progress.emit("engine", step=step, label=label, state="ok", detail="Already downloaded")
                continue
            if current.get(step):
                _record(step, INCOMPLETE)
            # The libraries print their own download chatter to stderr; the app keeps it in the
            # log disclosure, so it is not discarded here, only kept off the structured stream.
            # Every download verifies TLS against the OS trust store (D122, D355), so a network
            # that re-signs traffic with its own root fails none of them.
            with os_trust_store():
                detail = _RUNNERS[step]()
            if current.get(step):
                _record(step, current[step])
            results[step] = "ok"
            progress.emit("engine", step=step, label=label, state="ok", detail=detail)
        except Exception as e:  # noqa: BLE001 — a failed optional download must not stop the rest
            results[step] = "warn"
            ERRORS[step] = str(e) or type(e).__name__
            progress.emit("engine", step=step, label=label, state="warn", detail=ERRORS[step][:400])
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
