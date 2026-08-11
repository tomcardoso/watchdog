import contextlib
import importlib.resources
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from watchdog import interactive
from watchdog.cmd.base import (
    _BOLD,
    _DIM,
    _CYAN,
    _YELLOW,
    _GREEN,
    _RESET,
    _extra_install_cmd,
    _venv_bin,
)

WATCHDOG_HOME = Path.home() / ".watchdog"
CONFIG_FILE = WATCHDOG_HOME / "config.json"

_DEPS = [
    ("qpdf", "qpdf",        "macOS: brew install qpdf  |  Ubuntu/Debian: sudo apt install qpdf  |  Windows: scoop install qpdf"),
    ("gs",   "ghostscript", "macOS: brew install ghostscript  |  Ubuntu/Debian: sudo apt install ghostscript  |  Windows: https://ghostscript.com/releases/gsdnld.html"),
]
if sys.platform != "darwin":
    _DEPS.append((
        "tesseract",
        "Tesseract OCR",
        "Ubuntu/Debian: sudo apt install tesseract-ocr libtesseract-dev  |  Fedora: sudo dnf install tesseract tesseract-devel  |  Windows: https://github.com/UB-Mannheim/tesseract/wiki",
    ))
_DEPS.append(("claude", "Claude Code", "https://claude.ai/download"))


def _ok(msg):   print(f"  {_GREEN}✓{_RESET}  {msg}")
def _warn(msg): print(f"  {_YELLOW}!{_RESET}  {msg}")


def _check_deps() -> list[str]:
    """Print dep status. Returns list of missing blocking dep names (claude is non-blocking)."""
    print("  Checking dependencies...")
    blocking_missing = []
    for binary, label, hint in _DEPS:
        if shutil.which(binary):
            _ok(label)
        else:
            _warn(f"{label} not found")
            if label != "Claude Code":
                blocking_missing.append((label, hint))
    return blocking_missing


def install_skills(commands_dir: Path) -> None:
    """Install the per-vault Claude Code command skills (e.g. /watchdog-query).

    Record (domain extraction) skills are NOT copied here — they live globally and are
    read by the ingest orchestrator from `watchdog.skills_catalog` (see DECISIONS D21).
    """
    commands_dir.mkdir(parents=True, exist_ok=True)
    skills = importlib.resources.files("watchdog") / "skills"
    for item in skills.iterdir():
        if item.name.endswith(".md") and not item.name.startswith("_"):
            (commands_dir / item.name).write_bytes(item.read_bytes())


def _check_playwright() -> None:
    """Check for Playwright + Chromium (the optional, higher-fidelity capture path `watchdog
    research`/`fetch` use — see #200/D61) and offer to install both if missing. Everything else
    works without it; a missing/declined install just means plain-fetch captures instead of
    full-fidelity ones."""
    from watchdog.pipeline import capture

    print()
    print("  Checking web-capture support...")
    if capture.render_available():
        _ok("Playwright + Chromium — faithful web captures ready")
        return

    print(f"  {_DIM}Used by `watchdog research`/`watchdog fetch` to save full-fidelity page\n"
          f"  snapshots (images, styles, client-rendered pages) instead of a plain fetch.\n"
          f"  Optional — everything else works without it. Adds ~150 MB (Chromium browser).{_RESET}")
    if not interactive.confirm("  Install web-capture support now?", default=False):
        print(f"  {_DIM}Skipped. Install later with:{_RESET}")
        print(f"    {_CYAN}{_extra_install_cmd('playwright')}{_RESET}")
        print(f"    {_CYAN}{_venv_bin('playwright')} install chromium{_RESET}")
        return

    print(f"\n  {_DIM}Installing playwright...{_RESET}")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "playwright"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        _warn(f"could not install playwright: {result.stderr.strip()[:300]}")
        return

    print(f"  {_DIM}Downloading Chromium (one-time, ~150 MB)...{_RESET}")
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        _ok("Playwright + Chromium installed")
    else:
        _warn(f"Chromium download failed: {result.stderr.strip()[:300]}")


def _ask_projects_dir() -> Path:
    default = Path.home() / "Investigations"
    default_exists = default.exists()

    print()
    print("  Where should Watchdog store your investigation projects?")
    items = [
        f"{'Use' if default_exists else 'Create'} {default}",
        "Enter a different path",
    ]
    result = interactive.pick(items, 0)
    if result != 1:   # default row, or cancelled — fall back to the default path
        chosen = default
    else:
        while True:
            try:
                raw = input("  Path: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                chosen = default
                break
            if raw:
                chosen = Path(raw).expanduser().resolve()
                break
            print("  Please enter a path.")

    chosen.mkdir(parents=True, exist_ok=True)
    return chosen


def _detect_shell() -> tuple[str | None, Path | None]:
    shell_bin = os.environ.get("SHELL", "")
    if "zsh" in shell_bin:
        return "zsh", Path.home() / ".zshrc"
    if "bash" in shell_bin:
        profile = Path.home() / ".bash_profile"
        if not profile.exists():
            profile = Path.home() / ".bashrc"
        return "bash", profile
    if "fish" in shell_bin:
        return "fish", None
    return None, None


_COMPLETION_MARKER = "register-python-argcomplete watchdog"


def _install_completion(shell: str, profile: Path | None, force: bool = False) -> str | None:
    """Install completions. Returns description of what was done, or None if skipped."""
    # Prefer the register-python-argcomplete binary from the same venv as this Python — both
    # pipx and uv tool install it there but don't expose it on the user's PATH.
    rpa_str = _venv_bin("register-python-argcomplete")

    if shell == "fish":
        fish_dir = Path.home() / ".config" / "fish" / "completions"
        fish_dir.mkdir(parents=True, exist_ok=True)
        dest = fish_dir / "watchdog.fish"
        try:
            result = subprocess.run(
                [rpa_str, "--shell", "fish", "watchdog"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout:
                dest.write_text(result.stdout)
                return str(dest)
        except Exception:
            pass
        return None

    if profile is None:
        return None

    good_line = f'eval "$({rpa_str} watchdog)"'

    if profile.exists():
        content = profile.read_text()
        if good_line in content and not force:
            return None  # already correct
        if _COMPLETION_MARKER in content:
            # Stale entry (wrong path or old form) — strip and rewrite below
            lines = [line for line in content.splitlines() if _COMPLETION_MARKER not in line]
            profile.write_text("\n".join(lines).rstrip() + "\n")

    with open(profile, "a") as f:
        f.write(f"\n{good_line}\n")
    return str(profile)


def _download_gliner_model():
    """Load (downloading if not cached) the GLiNER model, verifying TLS via the OS trust store
    rather than certifi's bundled list — same corporate-proxy fix as D122. `inject_into_ssl`
    patches the global `ssl` module, so it's scoped to just this one-time load via `finally`."""
    import truststore
    truststore.inject_into_ssl()
    try:
        from gliner import GLiNER
        return GLiNER.from_pretrained("urchade/gliner_multi-v2.1")
    finally:
        truststore.extract_from_ssl()


def _ensure_gliner() -> None:
    """Pre-download GLiNER if present; if missing, attempt to install it ourselves, falling
    back to a manual install pointer only if that fails. Split out of `run` so tests of the
    setup flow can stub it — the fallback is a real `pip install` that pulls in PyTorch."""
    try:
        _download_gliner_model()
        _ok("Local name detection model (GLiNER)")
    except ImportError:
        print(f"  {_DIM}Installing GLiNER (local name detection) — this pulls in PyTorch and\n"
              f"  may take several minutes on first run...{_RESET}")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "gliner"],
            capture_output=True, text=True,
        )
        gliner_ready = False
        if result.returncode == 0:
            try:
                _download_gliner_model()
                _ok("Local name detection model (GLiNER)")
                gliner_ready = True
            except Exception:
                pass
        if not gliner_ready:
            _warn("gliner not installed — the candidate harvest will skip name detection")
            print(f"      {_DIM}Install it later with:{_RESET}")
            print(f"        {_CYAN}{_extra_install_cmd('gliner')}{_RESET}")
    except Exception as e:
        _warn(f"GLiNER model download failed: {e}")


def run(force: bool = False) -> None:
    if CONFIG_FILE.exists() and not force:
        print("  Watchdog is already set up. Use --force to re-run.")
        return

    print()

    # 1. Dependencies
    blocking = _check_deps()
    if blocking:
        print()
        print("  Install missing dependencies before running `watchdog setup`:")
        for label, hint in blocking:
            print(f"\n    {label}:\n      {hint}")
        print()
        sys.exit(1)

    # 2. Projects directory
    projects_dir = _ask_projects_dir()
    _ok(f"Projects directory: {projects_dir}")

    # 3. Shell completions
    print()
    shell, profile = _detect_shell()
    if shell:
        print(f"  Installing shell completions for {shell}...")
        result = _install_completion(shell, profile, force=force)
        if result:
            _ok(f"Added to {result}")
        else:
            _ok("Already installed")
    else:
        print("  Shell not detected — skipping tab completions.")

    # 4. Machine capabilities
    cores = os.cpu_count() or 1
    print()
    print("  Detecting machine capabilities...")
    _ok(f"CPU cores: {cores} — worker counts set to auto (adaptive per workload)")

    # 5. OCR engine
    print()
    print("  Detecting OCR engine...")
    if sys.platform == "darwin":
        try:
            import ocrmac  # noqa: F401
            _ok("Apple Vision (ocrmac) — hardware-accelerated OCR on macOS")
        except ImportError:
            _warn("ocrmac not importable — OCR will fall back to EasyOCR (run: pip install ocrmac)")
    else:
        try:
            import tesserocr  # noqa: F401
            _ok("Tesseract (tesserocr) — OCR engine ready")
        except ImportError:
            _warn("tesserocr not importable — OCR will fall back to EasyOCR"
                  " (install Tesseract system package first, then: pip install tesserocr)")

    # 6. Web capture (Playwright/Chromium)
    _check_playwright()

    # 7. Download ML models
    # Pin fastembed cache to ~/.cache/fastembed so it survives reboots.
    # Fastembed 0.8+ defaults to tempfile.gettempdir()/fastembed_cache which is ephemeral.
    _FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"
    _fastembed_cache_root = Path(
        os.environ.get("FASTEMBED_CACHE_PATH", Path.home() / ".cache" / "fastembed")
    )
    os.environ.setdefault("FASTEMBED_CACHE_PATH", str(_fastembed_cache_root))
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    _fastembed_cached = (_fastembed_cache_root / _FASTEMBED_MODEL.replace("/", os.sep)).exists()
    _docling_cache    = Path.home() / ".cache" / "docling" / "models"
    _models_cached    = _fastembed_cached and _docling_cache.exists() and any(_docling_cache.iterdir())
    print()
    if _models_cached:
        print("  Checking ML models...")
    else:
        print("  Downloading ML models (one-time, may take a few minutes)...")
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            from fastembed import TextEmbedding
            TextEmbedding(_FASTEMBED_MODEL)
            _ok(f"Embedding model ({_FASTEMBED_MODEL})")
        except Exception as e:
            _warn(f"Embedding model download failed: {e}")
        # Pre-fetch the search reranker too, so the first `watchdog search` isn't a
        # surprise ~300MB download — and a blocked download surfaces here, with guidance,
        # instead of silently degrading to fusion mid-investigation. Skipped if disabled.
        from watchdog.pipeline import embed as _embed
        if _embed._rerank_enabled():
            try:
                from fastembed.rerank.cross_encoder import TextCrossEncoder
                TextCrossEncoder(_embed._rerank_model_name())
                _ok(f"Search reranker ({_embed._rerank_model_name()})")
            except Exception as e:
                _warn(f"Reranker download failed: {e}\n"
                      f"      Search still works (ranks by BM25 + embedding fusion); the reranker\n"
                      f"      retries on first search. Disable it with `watchdog configure rerank_model none`.")
        try:
            from docling.document_converter import DocumentConverter
            DocumentConverter()
            _ok("Document conversion models (Docling)")
        except Exception as e:
            _warn(f"Docling model download failed: {e}")
        # GLiNER (local named-entity recognition) is an optional extra, not installed by
        # a base `watchdog-intel` install — it feeds the extraction candidate harvest's
        # person/org/location detection (#361).
        _ensure_gliner()

    # 8. Write config
    WATCHDOG_HOME.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(
            {"projects_dir": str(projects_dir), "chunk_workers": "auto", "chew_workers": "auto"},
            indent=2,
        ) + "\n"
    )

    # 9. Authentication
    from watchdog.cmd.auth import setup_auth_interactive
    setup_auth_interactive()

    # 10. Done
    reload_hint = f"{_CYAN}source {profile}{_RESET}" if profile else "reload your shell"
    print()
    print(f"{_GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{_RESET}")
    print()
    print(r"               .--~~,__")
    print(r"  :-....,-------`~~'._.'")
    print(r"   `-,,,  ,_      ;'~U'")
    print(r"    _,-' ,'`-__; '--.  ")
    print(r"   (_/'~~      ''''(;  ")
    print()
    print(f"  {_BOLD}Watchdog is on the scent.{_RESET}")
    print()
    print("  Reload your shell:")
    print(f"    {_CYAN}{reload_hint}{_RESET}")
    print()
    print("  Create your first investigation:")
    print(f"    {_CYAN}watchdog new \"My Investigation\"{_RESET}")
    print(f"{_GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{_RESET}")
    print()
