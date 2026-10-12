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

import contextlib
import hashlib
import json
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


# ── running in the sample (D307) ──────────────────────────────────────────────────────────
# Everything a run in the sample would send to a model is answered from the story instead,
# decided here from the folder's marker every time a run starts: no key, no network, no
# public-records question, $0 usage. A file without a prepared answer is refused before any call.

REFUSAL = ("This is a sample investigation, so it only processes its own example files. Create your "
           "own investigation to add your documents.")
NOTE = ("This is the sample investigation: its example files are processed with prepared answers, so "
        "nothing is sent to a model and nothing is charged.")
CHAT_REFUSAL = ("Ask Claude is not available in the sample investigation: it would send the sample to a "
                "real model. Create your own investigation to use it.")


def _marker(vault: Path) -> dict:
    try:
        data = json.loads((Path(vault) / ".watchdog" / _demo().SAMPLE_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def prepared(vault: Path) -> dict[str, str]:
    """`{sha256: filename}` of every file the sample has prepared answers for (empty for a sample
    built before D307, whose files are then all refused)."""
    got = _marker(vault).get("prepared")
    return {k: v for k, v in got.items() if isinstance(k, str) and isinstance(v, str)} \
        if isinstance(got, dict) else {}


def unprepared(vault: Path, queue_files: list[dict]) -> list[str]:
    """The names of queued documents the sample has no prepared answer for."""
    known = prepared(vault)
    return [q.get("filename") or q.get("sha256", "?") for q in queue_files
            if q.get("sha256") not in known]


def refuse_unprepared(vault: Path, queue_files: list[dict]) -> None:
    """Stop the run, before any model call, when a queued document has no prepared answer."""
    names = unprepared(vault, queue_files)
    if names:
        sys.exit(f"Error: {REFUSAL} Not an example file: {', '.join(sorted(names))}.")


def prepared_conversion(vault: Path, path: Path) -> dict | None:
    """Pre-processing for one of the sample's waiting files: the prepared text it was written on,
    in the shape `preprocess` returns, or None for any other file (read as usual)."""
    try:
        sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None
    if sha not in prepared(vault):
        return None
    f = Path(vault) / ".watchdog" / _demo().SAMPLE_CONVERSIONS / f"{sha}.json"
    try:
        result = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    result.update(source_path=str(path), elapsed_s=0.0,
                  char_count=sum(len(p.get("markdown", "")) for p in result.get("pages", [])))
    return result


def _story(vault: Path) -> list[dict]:
    """The story's documents the canned model can answer for, each with the text a run's prompt
    carries: the queue descriptor's pages (what pre-processing read), else the prepared conversion."""
    demo = _demo()
    by_file = {}
    for d in demo.all_docs() + demo.waiting_docs() + [demo.failed_doc()]:
        by_file[d["file"]] = d
    v = Path(vault) / ".watchdog"
    out = []
    for sha, name in prepared(vault).items():
        doc = by_file.get(name)
        if doc is None:
            continue
        pages = None
        for f in (v / "queue" / f"{sha}.json", v / "queue" / "_failed" / f"{sha}.json",
                  v / demo.SAMPLE_CONVERSIONS / f"{sha}.json"):
            try:
                pages = json.loads(f.read_text(encoding="utf-8")).get("pages")
            except (OSError, ValueError):
                continue
            if pages:
                break
        if not pages:
            continue
        doc["md"] = [p.get("markdown", "") for p in pages]
        doc["sha"] = sha
        out.append(doc)
    return out


@contextlib.contextmanager
def answers(vault: Path):
    """Within this block every model call is answered by the sample's canned model, recorded at
    $0 as backend `sample` and kept out of the usage database, as when the sample was built."""
    from watchdog import model_client, telemetry_db
    canned = _demo().CannedModel(_story(vault), Path(vault), free=True)
    real_call, real_telemetry = model_client.acomplete_json, telemetry_db.enabled
    model_client.acomplete_json = canned
    telemetry_db.enabled = lambda: False
    try:
        yield canned
    finally:
        model_client.acomplete_json = real_call
        telemetry_db.enabled = real_telemetry


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

