"""Optional hook that snapshots real model responses hitting specific conditions — truncation,
malformed JSON, schema drift, pagination continuation — for issue #352's curated fixture library.

Inert by default: nothing is captured until `enable()` is called, which only
`benchmarks/run_benchmark.py` does, only around a confirmed real (non-estimate-only) run against
the public `corpus-v1` benchmark corpus. A real `watchdog ingest`/`dig` run against a live
investigation vault never calls `enable()` — this module has no scrubbing step, so it must never
see real document content. `capture()` is a no-op whenever capture isn't enabled, so calling it
unconditionally from `model_client.py` costs nothing in production.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

_capture_dir: Path | None = None

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def enable(directory: Path) -> None:
    """Turn on capture for the current process, writing to `directory` (created if needed)."""
    global _capture_dir
    directory.mkdir(parents=True, exist_ok=True)
    _capture_dir = directory


def disable() -> None:
    """Turn capture back off."""
    global _capture_dir
    _capture_dir = None


def enabled() -> bool:
    return _capture_dir is not None


def capture(condition: str, *, backend: str, model_id: str, task: str | None = None,
           **fields) -> None:
    """Snapshot one payload under `condition` (e.g. "truncation", "malformed_json",
    "schema_drift", "continuation") if capture is enabled; a no-op otherwise. `fields` is
    whatever's relevant to that condition (raw text, usage, prefix, removed keys, …) — kept loose
    since each condition's useful payload shape differs. Best-effort: a write failure never
    interrupts the run this is observing."""
    if _capture_dir is None:
        return
    record = {"condition": condition, "backend": backend, "model_id": model_id, "task": task,
              **fields}
    safe_model = _UNSAFE_CHARS.sub("_", model_id)
    name = f"{condition}-{backend}-{safe_model}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}.json"
    try:
        (_capture_dir / name).write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    except OSError:
        pass
