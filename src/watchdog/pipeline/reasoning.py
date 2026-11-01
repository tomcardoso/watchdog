"""Saved model reasoning (D368): what a model wrote while thinking, kept beside each call so a
problem can be diagnosed after the fact.

Off by default: the `save_reasoning` setting turns it on for every run from then on, and every
benchmark run turns it on for itself. Each call's file is
`.watchdog/reasoning/<run id>/<NNNN>-<task>.json`, the run id being the time the run began, and
the call's usage record names the file (`reasoning_file`), so a run's
reasoning, usage and processing-log lines can be put together again. Reasoning can restate what
the documents say, so it stays in the investigation folder; `export` writes one run of it to a
zip only where the reporter chooses, and `clear` deletes it all.
"""

from __future__ import annotations

import datetime
import json
import re
import shutil
import zipfile
from pathlib import Path

from watchdog import config as user_config

SETTING = "save_reasoning"
FORMAT = 1


def root(vault: Path) -> Path:
    return Path(vault) / ".watchdog" / "reasoning"


def enabled() -> bool:
    """Whether the `save_reasoning` setting is on."""
    return bool(user_config.get(SETTING, False))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "call").lower()).strip("-")[:40] or "call"


def write_call(vault: Path, run_id: str, seq: int, *, task: str, model: str | None,
               backend: str | None, effort: str | None, filename: str | None,
               detail: str | None, stop_reason: str | None, usage: dict | None,
               reasoning: str | None, failed: bool = False) -> str | None:
    """Write one call's reasoning record and return its vault-relative path, or None when it
    could not be written (saving reasoning never fails a run). A call whose provider returned no
    reasoning is still recorded, with `reasoning` null, so the folder lists every call."""
    u = usage or {}
    record = {
        "format": FORMAT,
        "run_id": run_id,
        "seq": seq,
        "task": task,
        "filename": filename,
        "detail": detail,
        "model": model,
        "backend": backend,
        "effort": effort,
        "stop_reason": stop_reason,
        "failed": failed,
        "input_tokens": u.get("input_tokens", u.get("prompt_tokens", 0)) or 0,
        "output_tokens": u.get("output_tokens", u.get("completion_tokens", 0)) or 0,
        "reasoning_tokens": (u.get("completion_tokens_details") or {}).get("reasoning_tokens"),
        "saved_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reasoning": reasoning,
    }
    rel = f".watchdog/reasoning/{run_id}/{seq:04d}-{_slug(task)}.json"
    path = Path(vault) / rel
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return None
    return rel


def _usage_files(vault: Path) -> list[Path]:
    d = Path(vault) / ".watchdog" / "registry" / "usage"
    return sorted(d.glob("usage-*.json")) if d.is_dir() else []


def _usage_for_run(vault: Path, run_id: str) -> Path | None:
    """The usage file whose calls name a reasoning file of `run_id` (a run's usage file is named
    for when it ended, its reasoning folder for when it began)."""
    prefix = f".watchdog/reasoning/{run_id}/"
    for f in reversed(_usage_files(vault)):
        try:
            calls = json.loads(f.read_text(encoding="utf-8")).get("calls") or []
        except (OSError, ValueError, AttributeError):
            continue
        if any(isinstance(c, dict) and str(c.get("reasoning_file") or "").startswith(prefix)
               for c in calls):
            return f
    return None


def runs(vault: Path) -> list[dict]:
    """Each run with saved reasoning, newest first: its id, call count, size in bytes, when it
    started and the documents its calls named."""
    out = []
    base = root(vault)
    if not base.is_dir():
        return out
    for d in sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True):
        files = sorted(d.glob("*.json"))
        names: list[str] = []
        with_text = 0
        for f in files:
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if rec.get("reasoning"):
                with_text += 1
            name = rec.get("filename")
            if name and name not in names:
                names.append(name)
        out.append({"run_id": d.name, "calls": len(files), "with_reasoning": with_text,
                    "bytes": sum(f.stat().st_size for f in files),
                    "started": _started(d.name), "documents": names})
    return out


def _started(run_id: str) -> str | None:
    try:
        when = datetime.datetime.strptime(run_id, "%Y%m%dT%H%M%SZ")
    except ValueError:
        return None
    return when.replace(tzinfo=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log_lines(vault: Path, start: str | None, end: str | None) -> list[str]:
    """processing.log's lines stamped from `start` to `end` (ISO, UTC, to the second)."""
    from watchdog.vault_paths import processing_log
    log = processing_log(Path(vault))
    try:
        text = log.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        m = re.match(r"^\[(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)\]", line)
        if not m:
            continue
        if (start is None or m.group(1) >= start) and (end is None or m.group(1) <= end):
            out.append(line)
    return out


def export(vault: Path, run_id: str, output: Path) -> Path:
    """Write one run's reasoning, its usage record and the processing.log lines from while it
    ran into one zip in the folder `output`, and return the zip's path. Nothing is sent anywhere."""
    vault = Path(vault)
    src = root(vault) / run_id
    if not re.fullmatch(r"[0-9TZ]+", run_id or "") or not src.is_dir():
        raise FileNotFoundError(f"no saved reasoning for the run {run_id}")
    files = sorted(src.glob("*.json"))
    usage = _usage_for_run(vault, run_id)
    end = None
    if usage is not None:
        m = re.match(r"usage-(\d{8}T\d{6}Z)", usage.name)
        end = _started(m.group(1)) if m else None
    if end is None:
        stamps = []
        for f in files:
            try:
                stamps.append(json.loads(f.read_text(encoding="utf-8")).get("saved_at"))
            except (OSError, ValueError):
                pass
        end = max((s for s in stamps if s), default=None)
    log = _log_lines(vault, _started(run_id), end)
    target = Path(output) / f"watchdog-reasoning-{run_id}.zip"
    n = 2
    while target.exists():
        target = Path(output) / f"watchdog-reasoning-{run_id}-{n}.zip"
        n += 1
    part = target.with_name(target.name + ".part")
    try:
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                z.write(f, f"reasoning/{f.name}")
            if usage is not None:
                z.write(usage, f"usage/{usage.name}")
            z.writestr("processing.log", "\n".join(log) + ("\n" if log else ""))
            z.writestr("README.txt", _README.format(run=run_id, calls=len(files)))
        part.replace(target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return target


_README = """Watchdog: saved model reasoning for the run {run}

reasoning/       one file per model call ({calls} in all): the task, the document's file name,
                 the model and effort, why the call stopped, its token counts, and what the
                 model wrote while thinking (null when the provider returned none)
usage/           the run's usage record: every call's model, tokens, cost and timing
processing.log   the investigation's processing log while the run was going

The reasoning can restate what the documents say. Share this file only with someone you would
show the documents to, and never attach it to a public issue.
"""


def clear(vault: Path) -> dict:
    """Delete every run's saved reasoning. Returns how many runs and bytes were removed."""
    base = root(vault)
    if not base.is_dir():
        return {"runs": 0, "bytes": 0}
    runs_n = sum(1 for p in base.iterdir() if p.is_dir())
    size = sum(f.stat().st_size for f in base.rglob("*") if f.is_file())
    shutil.rmtree(base, ignore_errors=True)
    return {"runs": runs_n, "bytes": size}
