import importlib.resources
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

WATCHDOG_HOME = Path.home() / ".watchdog"
CONFIG_FILE = WATCHDOG_HOME / "config.json"

_GREEN  = "\033[0;32m"
_YELLOW = "\033[0;33m"
_BOLD   = "\033[1m"
_RESET  = "\033[0m"

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
    print("Checking dependencies...")
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
    records_dir = commands_dir / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    skills = importlib.resources.files("watchdog") / "skills"
    for item in skills.iterdir():
        if item.name == "records":
            for f in item.iterdir():
                if f.name.endswith(".md") and not f.name.startswith("_"):
                    (records_dir / f.name).write_bytes(f.read_bytes())
        elif item.name.endswith(".md") and not item.name.startswith("_"):
            (commands_dir / item.name).write_bytes(item.read_bytes())


def _ask_projects_dir() -> Path:
    default = Path.home() / "Investigations"
    default_exists = default.exists()

    print()
    print("Where should Watchdog store your investigation projects?")
    if default_exists:
        print(f"  1. Use {default}")
    else:
        print(f"  1. Create {default}")
    print("  2. Enter a different path")
    print()

    while True:
        choice = input("Choice [1]: ").strip() or "1"
        if choice == "1":
            chosen = default
            break
        elif choice == "2":
            raw = input("Path: ").strip()
            if raw:
                chosen = Path(raw).expanduser().resolve()
                break
            print("  Please enter a path.")
        else:
            print("  Enter 1 or 2.")

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


def _install_completion(shell: str, profile: Path | None) -> str | None:
    """Install completions. Returns description of what was done, or None if skipped."""
    if shell == "fish":
        fish_dir = Path.home() / ".config" / "fish" / "completions"
        fish_dir.mkdir(parents=True, exist_ok=True)
        dest = fish_dir / "watchdog.fish"
        try:
            result = subprocess.run(
                ["register-python-argcomplete", "--shell", "fish", "watchdog"],
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

    if profile.exists() and _COMPLETION_MARKER in profile.read_text():
        return None  # already present

    with open(profile, "a") as f:
        f.write(f'\neval "$(register-python-argcomplete watchdog)"\n')
    return str(profile)


def run(force: bool = False) -> None:
    if CONFIG_FILE.exists() and not force:
        print("Watchdog is already set up. Use --force to re-run.")
        return

    print()

    # 1. Dependencies
    blocking = _check_deps()
    if blocking:
        print()
        print("Install missing dependencies before running `watchdog setup`:")
        for label, hint in blocking:
            print(f"\n  {label}:\n    {hint}")
        print()
        sys.exit(1)

    # 2. Projects directory
    projects_dir = _ask_projects_dir()
    _ok(f"Projects directory: {projects_dir}")

    # 4. Shell completions
    print()
    shell, profile = _detect_shell()
    if shell:
        print(f"Installing shell completions for {shell}...")
        result = _install_completion(shell, profile)
        if result:
            _ok(f"Added to {result}")
        else:
            _ok("Already installed")
    else:
        print("Shell not detected — skipping tab completions.")

    # 5. Machine capabilities
    cores = os.cpu_count() or 1
    print()
    print("Detecting machine capabilities...")
    _ok(f"CPU cores: {cores} — worker counts set to auto (adaptive per workload)")

    # 6. OCR engine
    print()
    print("Detecting OCR engine...")
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

    # 6. Write config
    WATCHDOG_HOME.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(
            {"projects_dir": str(projects_dir), "chunk_workers": "auto", "chew_workers": "auto"},
            indent=2,
        ) + "\n"
    )

    # 7. Done
    reload_hint = f"source {profile}" if profile else "reload your shell"
    print()
    print(f"{_GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{_RESET}")
    print()
    print(r"      / \__")
    print(r"     (    @\___")
    print(r"     /         O")
    print(r"    /   (_____/")
    print(r"   /_____/   U")
    print()
    print(f"  {_BOLD}Watchdog is on the scent.{_RESET}")
    print()
    print(f"  Reload your shell:  {reload_hint}")
    print()
    print("  Create your first investigation:")
    print('    watchdog new "My Investigation"')
    print(f"{_GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{_RESET}")
    print()
