"""`watchdog demo` — a bundled sample investigation for onboarding and as a live smoke
test (#273).

Creates a scratch vault named ``watchdog-demo`` through the normal ``watchdog new`` path,
copies a small public-domain corpus into ``_INCOMING/`` (see ``watchdog/demo/PROVENANCE.md``
for the rationale), chews it, then offers the real ingest — reusing ``cmd_new``, ``cmd_chew``,
and the shared post-chew ``_offer_ingest`` prompt rather than reimplementing any of the
pipeline.
"""

import argparse
import importlib.resources
import os
from pathlib import Path

from watchdog.cmd.base import _BOLD, _CYAN, _GREEN, _RESET, load_projects

_DEMO_NAME = "Watchdog Demo"
_DEMO_SLUG = "watchdog-demo"
_DEMO_DESCRIPTION = "Sample investigation — SEC v. Fastow, charge to settlement"

# Case documents plus their provenance sidecars, copied into _INCOMING/. PROVENANCE.md
# (the public-domain rationale for the corpus) is packaging documentation, not a case
# document, so it is deliberately left out.
_DEMO_FILES = (
    "sec-v-fastow-complaint-excerpt.pdf",
    "sec-v-fastow-complaint-excerpt.pdf.yml",
    "sec-litigation-release-18543.txt",
    "sec-litigation-release-18543.txt.yml",
)


def cmd_demo(args) -> None:
    if _DEMO_SLUG in load_projects():
        print(f"\n  {_BOLD}{_DEMO_SLUG}{_RESET} already exists.")
        print(f"  Run {_CYAN}watchdog status {_DEMO_SLUG}{_RESET} to see it, or "
              f"{_CYAN}watchdog delete {_DEMO_SLUG}{_RESET} to start fresh.\n")
        return

    from watchdog.cmd.vault import cmd_new
    cmd_new(argparse.Namespace(name=_DEMO_NAME, description=_DEMO_DESCRIPTION, dir=None))
    vault = Path(load_projects()[_DEMO_SLUG]["path"])

    demo_dir = importlib.resources.files("watchdog") / "demo"
    incoming = vault / "_INCOMING"
    for name in _DEMO_FILES:
        (incoming / name).write_bytes(Path(str(demo_dir / name)).read_bytes())
    print(f"\n  {_GREEN}Copied:{_RESET} {_BOLD}{len(_DEMO_FILES)}{_RESET} sample files into "
          f"{_CYAN}_INCOMING/{_RESET}")

    os.chdir(vault)
    from watchdog.cmd.ingest import cmd_chew
    cmd_chew(args)

    print(f"\n  {_BOLD}Next steps{_RESET}")
    print(f"    1. Run {_CYAN}watchdog obsidian {_DEMO_SLUG}{_RESET} to open the vault in Obsidian")
    print(f"    2. Open a Claude Code session in the vault and try {_CYAN}/watchdog-query{_RESET}")
    print(f"    3. Run {_CYAN}watchdog delete {_DEMO_SLUG}{_RESET} when you're done exploring")
    print()
