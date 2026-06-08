import importlib.resources
import json
import os
import shutil
import sys
from pathlib import Path

WATCHDOG_HOME = Path.home() / ".watchdog"
CONFIG_FILE = WATCHDOG_HOME / "config.json"
COMMANDS_DIR = Path.home() / ".claude" / "commands"

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


def _install_skills() -> None:
    records_dir = COMMANDS_DIR / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    skills = importlib.resources.files("watchdog") / "skills"
    for item in skills.iterdir():
        if item.name == "records":
            for f in item.iterdir():
                if f.name.endswith(".md"):
                    (records_dir / f.name).write_bytes(f.read_bytes())
        elif item.name.endswith(".md"):
            (COMMANDS_DIR / item.name).write_bytes(item.read_bytes())


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


def _zsh_snippet() -> str:
    return """
# Watchdog tab completion
_watchdog_complete() {
    local -a subcmds
    subcmds=(new open list status search unlock setup configure about)
    local projects
    projects=$(python3 -c "
import json, os
p = os.path.expanduser('~/.watchdog/projects.json')
if os.path.exists(p):
    data = json.load(open(p))
    print(' '.join(data.keys()))
" 2>/dev/null)
    if (( CURRENT == 2 )); then
        compadd -a subcmds
    elif (( CURRENT == 3 )) && [[ "${words[2]}" == (open|status|search|unlock) ]]; then
        compadd ${=projects}
    fi
}
compdef _watchdog_complete watchdog
"""


def _bash_snippet() -> str:
    return """
# Watchdog tab completion
_watchdog_bash_complete() {
    local cur prev
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    if [[ ${COMP_CWORD} -eq 1 ]]; then
        COMPREPLY=($(compgen -W "new open list status search unlock setup configure about" -- "${cur}"))
    elif [[ "${prev}" == "open" || "${prev}" == "status" || "${prev}" == "search" || "${prev}" == "unlock" ]]; then
        local projects
        projects=$(python3 -c "
import json, os
p = os.path.expanduser('~/.watchdog/projects.json')
if os.path.exists(p):
    data = json.load(open(p))
    print(' '.join(data.keys()))
" 2>/dev/null)
        COMPREPLY=($(compgen -W "${projects}" -- "${cur}"))
    fi
}
complete -F _watchdog_bash_complete watchdog
"""


def _fish_completion() -> str:
    return """\
# Watchdog tab completion
set -l cmds new open list status search unlock setup configure about
set -l proj_cmds open status search unlock
set -l projects (python3 -c "
import json, os
p = os.path.expanduser('~/.watchdog/projects.json')
if os.path.exists(p):
    data = json.load(open(p))
    print(' '.join(data.keys()))
" 2>/dev/null)
complete -c watchdog -f
complete -c watchdog -n "not __fish_seen_subcommand_from $cmds" -a new       -d 'Create a new investigation vault'
complete -c watchdog -n "not __fish_seen_subcommand_from $cmds" -a open      -d 'Open an investigation in Claude Code'
complete -c watchdog -n "not __fish_seen_subcommand_from $cmds" -a list      -d 'List all investigations'
complete -c watchdog -n "not __fish_seen_subcommand_from $cmds" -a status    -d 'Show detailed status for an investigation'
complete -c watchdog -n "not __fish_seen_subcommand_from $cmds" -a search    -d 'Semantic search across ingested documents'
complete -c watchdog -n "not __fish_seen_subcommand_from $cmds" -a unlock    -d 'Release a stale ingest lock'
complete -c watchdog -n "not __fish_seen_subcommand_from $cmds" -a setup     -d 'Set up Watchdog'
complete -c watchdog -n "not __fish_seen_subcommand_from $cmds" -a configure -d 'View or change configuration'
complete -c watchdog -n "not __fish_seen_subcommand_from $cmds" -a about     -d 'Show version and project links'
complete -c watchdog -n "__fish_seen_subcommand_from $proj_cmds" -a "$projects"
"""


def _install_completion(shell: str, profile: Path | None) -> str | None:
    """Install completions. Returns description of what was done, or None if skipped."""
    if shell == "fish":
        fish_dir = Path.home() / ".config" / "fish" / "completions"
        fish_dir.mkdir(parents=True, exist_ok=True)
        dest = fish_dir / "watchdog.fish"
        dest.write_text(_fish_completion())
        return f"~/.config/fish/completions/watchdog.fish"

    if profile is None:
        return None

    marker = "_watchdog_complete" if shell == "zsh" else "_watchdog_bash_complete"
    if profile.exists() and marker in profile.read_text():
        return None  # already present

    snippet = _zsh_snippet() if shell == "zsh" else _bash_snippet()
    with open(profile, "a") as f:
        f.write(snippet)
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

    # 2. Skills
    print()
    print("Installing Watchdog skills...")
    _install_skills()
    _ok(f"Skills installed to {COMMANDS_DIR}/")

    # 3. Projects directory
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
    chunk_workers = max(2, cores // 2)
    print()
    print("Detecting machine capabilities...")
    _ok(f"CPU cores: {cores} → chunk_workers set to {chunk_workers}")

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
            {"projects_dir": str(projects_dir), "chunk_workers": chunk_workers},
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
    print(f"  {_BOLD}Watchdog is ready.{_RESET}")
    print()
    print(f"  Reload your shell:  {reload_hint}")
    print()
    print("  Create your first investigation:")
    print('    watchdog new "My Investigation"')
    print(f"{_GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{_RESET}")
    print()
