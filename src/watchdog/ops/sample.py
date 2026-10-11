"""The sample investigation (D303): "Port Calder Waterfront (sample)", a fictional case built by
the real pipeline with prepared model answers, for new users to explore and for the app's guided
tour.

`sample-create` builds it into the investigations folder (or `dir`) and lists it, marked as the
sample. It calls no model and needs no key, sign-in or network: `watchdog.gui.demo` stands in for
the model, records each call at no cost and keeps the calls out of the usage database. When the
sample already exists the operation refuses unless `rebuild` is set; a rebuild deletes only the
sample (the listed investigation marked `sample`, or an unlisted folder carrying the sample's
marker) and builds it again.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from watchdog import projects
from watchdog.ops import op
from watchdog.pipeline.write_vault import slugify
from watchdog.vault_paths import is_vault


def _demo():
    from watchdog.gui import demo
    return demo


def slug() -> str:
    return slugify(_demo().SAMPLE_NAME)


def is_sample_folder(path: Path) -> bool:
    """Whether `path` is a sample investigation's folder: a vault carrying the sample's marker."""
    return is_vault(path) and (path / ".watchdog" / _demo().SAMPLE_MARKER).is_file()


def listed() -> tuple[str, dict] | None:
    """The listed investigation marked as the sample, if any (it may have been renamed or moved)."""
    for key, info in projects.load().items():
        if isinstance(info, dict) and info.get("sample"):
            return key, info
    return None


def status(dir: str | None = None) -> dict:
    """Where the sample is or would go: `{slug, path, listed, exists, archived}`. `slug` is set when
    it is listed; `exists` says whether a sample folder is at `path`."""
    found = listed()
    if found:
        key, info = found
        path = Path(info.get("path") or "")
        return {"slug": key, "path": str(path), "listed": True, "exists": is_sample_folder(path),
                "archived": bool(info.get("archived"))}
    parent = Path(dir).expanduser().resolve() if dir else projects.projects_dir()
    path = parent / slug()
    return {"slug": None, "path": str(path), "listed": False, "exists": is_sample_folder(path),
            "archived": False}


@op("sample-create", vault=False)
def create(rep, *, dir: str | None = None, rebuild: bool = False) -> dict:
    """Build the sample investigation, or with `rebuild` replace the existing sample with a fresh
    one. Never touches anything but the sample."""
    from watchdog.ops import projects as projects_op
    now = status(dir)
    target = Path(now["path"])
    if now["listed"] or now["exists"]:
        if not rebuild:
            sys.exit("Error: the sample investigation already exists. Open it, or rebuild it to "
                     "start again.")
        if now["listed"]:
            projects.check_locks(target)
            if target.exists() and not is_sample_folder(target):
                sys.exit(f"Error: {target} is not the sample investigation's folder; nothing was "
                         "deleted.")
            rep.log("Removing the old sample.")
            projects_op.delete(rep, slug=now["slug"], purge=True)
        else:
            rep.log("Removing the old sample folder.")
            shutil.rmtree(target)
    elif target.exists():
        sys.exit(f"Error: {target} already exists and is not the sample; nothing was changed.")
    else:
        other = projects.load().get(slug())
        if isinstance(other, dict) and not other.get("sample"):
            sys.exit(f"Error: an investigation named “{other.get('name')}” is already listed and is "
                     "not the sample; nothing was changed.")

    target.parent.mkdir(parents=True, exist_ok=True)
    done = [0]
    total = 5

    def step(text: str) -> None:
        done[0] += 1
        rep.log(text + ".")
        rep.progress("message", text=f"{text} ({done[0]} of {total})")

    vault = _demo().build(target, sample=True, on_step=step)
    key = slug()
    info = projects.load().get(key) or {}
    rep.log(f"The sample investigation is ready: {vault}")
    return {"slug": key, "name": info.get("name"), "path": str(vault)}

