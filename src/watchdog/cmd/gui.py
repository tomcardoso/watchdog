"""`watchdog gui` — open the Watchdog desktop app.

The app is a separate download that talks to this Python package. This command finds it:
`WATCHDOG_APP` (a path to the app's executable) wins, then the installed app on macOS, then — in a
development checkout of the repository — the app's own dev server. Otherwise it says where to get
the app.
"""

import os
import subprocess
import sys
from pathlib import Path

from watchdog.cmd.base import _BOLD, _CYAN, _DIM, _RESET, _YELLOW


def _repo_root() -> Path | None:
    """The repository root when this package is a development checkout (`<repo>/src/watchdog`
    with the app's dependencies installed in `<repo>/gui`), else None."""
    import watchdog
    pkg = Path(watchdog.__file__).resolve().parent
    repo = pkg.parent.parent
    if pkg.parent.name == "src" and (repo / "gui" / "package.json").is_file() \
            and (repo / "gui" / "node_modules").is_dir():
        return repo
    return None


def _launch_executable(path: str) -> bool:
    try:
        subprocess.Popen([path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=(os.name != "nt"))
    except OSError as e:
        sys.exit(f"Error: could not start the app at {path}: {e.strerror or e}")
    return True


def cmd_gui(args) -> None:
    print()
    app = os.environ.get("WATCHDOG_APP")
    if app:
        if not (Path(app).is_file() and os.access(app, os.X_OK)):
            sys.exit(f"Error: WATCHDOG_APP is set to {app}, which is not an executable file.")
        _launch_executable(app)
        print(f"  {_BOLD}Opening Watchdog…{_RESET}  {_DIM}{app}{_RESET}\n")
        return

    if sys.platform == "darwin":
        done = subprocess.run(["open", "-a", "Watchdog"], capture_output=True)
        if done.returncode == 0:
            print(f"  {_BOLD}Opening Watchdog…{_RESET}\n")
            return

    repo = _repo_root()
    if repo is not None:
        env = {**os.environ, "WATCHDOG_PYTHON": sys.executable, "WATCHDOG_SRC": str(repo / "src")}
        print(f"  {_BOLD}Starting the app from this checkout…{_RESET}  "
              f"{_DIM}(npm run dev in {repo / 'gui'}; Ctrl+C stops it){_RESET}\n")
        try:
            code = subprocess.call(["npm", "run", "dev"], cwd=str(repo / "gui"), env=env)
        except FileNotFoundError:
            sys.exit("Error: npm was not found. Install Node.js to run the app from a checkout.")
        except KeyboardInterrupt:
            print()
            return
        if code:
            sys.exit(code)
        return

    print(f"  {_YELLOW}The Watchdog app is not installed.{_RESET}")
    print(f"  {_DIM}Install it, then run{_RESET} {_CYAN}watchdog gui{_RESET} {_DIM}again. "
          f"Steps are in{_RESET} {_CYAN}docs/app.md{_RESET}{_DIM}.{_RESET}")
    print(f"  {_DIM}If it is installed somewhere unusual, set{_RESET} {_CYAN}WATCHDOG_APP{_RESET} "
          f"{_DIM}to its executable.{_RESET}\n")
    sys.exit(1)
