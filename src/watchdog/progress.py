"""Structured progress for the desktop app, written alongside the normal terminal output.

When the environment variable `WATCHDOG_PROGRESS` is `1` (the app sets it for every job it
starts), `emit(kind, **fields)` writes one line to the real stdout:

    \\x1eWDP {"kind": "stage", ...}\\n

The record-separator prefix cannot occur in ordinary output, so the app can tell these lines from
the human-readable ones around them and keep them out of the log it shows. With the variable unset
`emit` returns immediately and writes nothing, so the terminal experience is unchanged.

Events (every one has `kind`):

    {"kind": "chew", "state": "start", "total": n}
    {"kind": "chew", "state": "file", "name": str, "outcome": "ok|skipped|failed|duplicate",
     "pages": int|null, "done": n, "total": n}
    {"kind": "chew", "state": "end", "ok": n, "skipped": n, "failed": n}
    {"kind": "doc", "sha": str, "filename": str,
     "state": "started|classified|extracting|section|done|failed", "detail": str|null}
        detail: the skill for `classified`, "i of n" for `section`, "<n> facts" for `done`,
        the reason for `failed`, "rate limit" when a rate limit stopped the run.
    {"kind": "stage", "stage": "dig|fold|reconcile|commit|contradictions|synthesis|timeline|
     briefing|leads|requests|done", "done": n|null, "total": n|null, "path": str|null}
        `dig` carries the run's document count as `total`; `done` is the briefing path.
    {"kind": "download", "done": n, "total": n, "url": str|null}   (research fetch)
    {"kind": "model", "name": "transcription", "label": str, "state": "start|progress|done|failed",
     "done": MB|null, "total": MB|null, "detail": str|null}
        a one-time model download; `label` reads "Downloading the transcription model (486 MB)".
    {"kind": "transcribe", "name": str, "position": seconds, "duration": seconds|null}
        a recording being transcribed. Written by the pre-processing subprocess to its stderr
        (its stdout is the JSON result) and forwarded by preprocess_batch.preprocess_one.
    {"kind": "message", "text": str}
    {"kind": "result", "op": str, "result": object}
        the operation's return value, the worker's last line (D298).
"""

from __future__ import annotations

import json
import os
import sys

PREFIX = "\x1eWDP "


def enabled() -> bool:
    return os.environ.get("WATCHDOG_PROGRESS") == "1"


def emit(kind: str, **fields) -> None:
    if os.environ.get("WATCHDOG_PROGRESS") != "1":
        return
    write(kind, **fields)


def write(kind: str, **fields) -> None:
    """Write one event whatever the environment says (the worker's own lines, D298)."""
    try:
        out = sys.__stdout__
        out.write(PREFIX + json.dumps({"kind": kind, **fields}, ensure_ascii=False, default=str) + "\n")
        out.flush()
    except Exception:  # noqa: BLE001 — progress must never break a run
        pass


def parse(line: str) -> dict | None:
    """The event in a progress line, or None for any other line."""
    if not line.startswith(PREFIX):
        return None
    try:
        event = json.loads(line[len(PREFIX):])
    except ValueError:
        return None
    return event if isinstance(event, dict) else None
