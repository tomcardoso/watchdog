"""The demo investigation, built once per test session and copied for each module that needs it.

`python -m watchdog.gui.demo` runs the whole pipeline over the sample documents (with a stubbed
model) and, where the search models are installed, downloads and runs them too: about two minutes
a build on a laptop, longer on a busy machine. Nine test modules used to build it each, one of them
once per test. `conftest.demo_build` now builds it once, and `copy_demo` gives each user its own
copy in about a second.

The vault records no absolute paths, so it is copied as it is. The home folder's `.watchdog/*.json`
files (`projects.json`, `access.json`, `config.json`) record the build's location, so a copy
rewrites that prefix. The home's model cache (`.cache`) is hard-linked rather than copied: nothing
writes to it, and it is most of the build's size.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

# One build per session, so it can afford to wait out a busy machine; the per-module builds this
# replaces gave up at 60 to 180 seconds.
BUILD_TIMEOUT = 900


def build_demo(root: Path) -> str:
    """Build the demo investigation at `root/vault`, with its home at `root/home`. Returns the
    build's standard output (the demo's own summary)."""
    proc = subprocess.run(
        [sys.executable, "-m", "watchdog.gui.demo", str(root / "vault"), "--home", str(root / "home")],
        env={**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"},
        capture_output=True, text=True, timeout=BUILD_TIMEOUT)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _link_or_copy(src, dst):
    try:
        os.link(src, dst)
    except OSError:          # another filesystem, or links not allowed: copy instead
        shutil.copy2(src, dst)


def copy_demo(build_root: Path, dest_root: Path) -> tuple[Path, Path]:
    """Copy a built demo (`build_demo`'s `root`) to `dest_root`, which must not exist yet.
    Returns the copy's (vault, home)."""
    shutil.copytree(build_root / "vault", dest_root / "vault", symlinks=True)
    home_src, home = build_root / "home", dest_root / "home"
    home.mkdir(parents=True)
    old, new = str(build_root), str(dest_root)
    for entry in home_src.iterdir():
        target = home / entry.name
        if entry.name == ".cache":
            shutil.copytree(entry, target, symlinks=True, copy_function=_link_or_copy)
        elif entry.is_dir():
            shutil.copytree(entry, target, symlinks=True)
        else:
            shutil.copy2(entry, target)
    for path in (home / ".watchdog").glob("*.json"):
        text = path.read_text(encoding="utf-8")
        if old in text:
            path.write_text(text.replace(old, new), encoding="utf-8")
    return dest_root / "vault", home
