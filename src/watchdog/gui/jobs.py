"""The app's operations, each run in a worker process (D298).

A job is `python -m watchdog.worker` with the investigation as its working folder (none for an
operation outside one), colour off, `WATCHDOG_APP=1` and `WATCHDOG_PROGRESS=1`. Its stdin carries
one JSON line, `{"op", "params"}`, then, for an operation that calls a model, the investigation's
keys as a second line (`WATCHDOG_SECRETS=stdin`, D295): only the key per provider the
investigation resolves to, never through the environment or argv, which other programs and crash
reports can see. Then stdin is closed, so nothing the operation runs can wait on it.

Two reader threads split the worker's output: progress lines update the job's `ProgressState` and
become `job.progress` events, the final `result` line becomes the job's `result`, and every other
line goes to a ring buffer (the last 5,000) and out as batched `job.log` events (at most ten a
second).

Cancelling sends Ctrl+C (SIGINT, or CTRL_BREAK_EVENT on Windows) so the pipeline's own graceful
stop runs and finished documents are kept; a second cancel kills the process.
"""

from __future__ import annotations

import collections
import datetime
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from watchdog import ops, progress
from watchdog.gui import rpc
from watchdog.gui.vaultio import strip_ansi

LOG_LINES = 5000
KEEP_FINISHED = 50
LOG_INTERVAL = 0.1          # seconds between job.log batches: at most ten a second

ENGINE_BUSY_OTHER = ("Watchdog is still setting up. This will be available when it finishes, "
                     "in a few minutes.")


def require_engine(op: str) -> None:
    """Refuse an operation that needs the full engine while the app is still installing it
    (D272): adding documents in any form, the watcher and requeue (`engine="add"`), and anything
    that rewrites the search index (`engine="index"`)."""
    from watchdog.gui.engine_setup import ENGINE_NOT_READY, engine_ready

    if engine_ready():
        return
    need = ops.get(op).engine
    if need == "add":
        raise rpc.RpcError(ENGINE_NOT_READY, code="engine_not_ready", data={"op": op})
    if need == "index":
        raise rpc.RpcError(ENGINE_BUSY_OTHER, code="engine_not_ready", data={"op": op})


def prepare(vault: Path | None, op: str, params: dict | None) -> tuple[ops.Op, dict, Path | None]:
    """The operation, its checked parameters and the folder it runs in; refused with `bad_params`,
    or `engine_not_ready` while the engine is still installing."""
    try:
        spec = ops.get(op)
    except ops.OpError as e:
        raise rpc.RpcError(str(e), code="bad_params") from None
    require_engine(op)
    try:
        checked = ops.validate(op, params)
    except ops.OpError as e:
        raise rpc.RpcError(str(e), code="bad_params") from None
    if not spec.vault:
        return spec, checked, None
    if vault is None:
        raise rpc.RpcError("This needs an open investigation.", code="bad_params")
    return spec, checked, vault


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _clock(seconds) -> str:
    """12:05, or 1:02:05 past an hour."""
    s = max(0, int(seconds or 0))
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _secrets(vault: Path | None) -> dict[str, str]:
    """The encrypted keys a command in `vault` may use (`auth.secrets_for_run`). A failure here
    must not stop the command: it then finds the key locked and stops with that reason itself."""
    try:
        from watchdog.cmd.auth import secrets_for_run
        return secrets_for_run(vault)
    except Exception:  # noqa: BLE001
        return {}


def _hand_over(proc: subprocess.Popen, request: dict, keys: dict[str, str]) -> None:
    from watchdog.keystore import child_input
    try:
        proc.stdin.write(_request_line(request) + (child_input(keys) if keys else b""))
    except OSError:
        pass   # the worker already exited; its own error says why
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass


def _request_line(request: dict) -> bytes:
    return (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")


def worker_argv() -> list[str]:
    """The process a job runs. A module-level function so tests can substitute a quick script."""
    return [sys.executable, "-m", "watchdog.worker"]


def _env(keys: bool) -> dict:
    env = {**os.environ, "NO_COLOR": "1", "WATCHDOG_PROGRESS": "1", "WATCHDOG_APP": "1",
           "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    env.pop("WATCHDOG_SECRETS", None)
    if keys:
        env["WATCHDOG_SECRETS"] = "stdin"
    return env


class Job:
    def __init__(self, vault: Path | None, op: str, params: dict, label: str, kind: str | None):
        self.id = uuid.uuid4().hex[:12]
        self.vault = vault
        self.op = op
        self.params = dict(params)
        self.label = label
        self.kind = kind or op
        self.result = None
        self.state = "running"
        self.exit_code: int | None = None
        self.started = _now()
        self.finished: str | None = None
        self.progress = {"stage": None, "done": None, "total": None, "current": None, "note": None,
                         "docs": {}}
        self.log: collections.deque = collections.deque(maxlen=LOG_LINES)
        self.pending: list[dict] = []
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.cancel_requests = 0

    def to_dict(self) -> dict:
        with self.lock:
            snapshot = {**self.progress, "docs": {k: dict(v) for k, v in self.progress["docs"].items()}}
        return {"id": self.id, "label": self.label, "kind": self.kind,
                "vault": str(self.vault) if self.vault else None, "op": self.op,
                "params": self.params, "result": self.result,
                "state": self.state, "exit_code": self.exit_code, "started": self.started,
                "finished": self.finished, "progress": snapshot}

    def apply_progress(self, event: dict) -> None:
        kind = event.get("kind")
        with self.lock:
            p = self.progress
            if kind == "stage":
                p.update(stage=event.get("stage"), done=event.get("done"), total=event.get("total"))
                if event.get("path"):
                    p["current"] = event["path"]
            elif kind == "doc":
                sha = event.get("sha") or event.get("filename") or "?"
                p["docs"][sha] = {"filename": event.get("filename"), "state": event.get("state"),
                                  "detail": event.get("detail")}
                p["current"] = event.get("filename")
                if event.get("state") in ("done", "failed"):
                    finished = sum(1 for d in p["docs"].values() if d["state"] in ("done", "failed"))
                    if p["stage"] == "dig":
                        p["done"] = finished
            elif kind == "chew":
                p["stage"] = "chew"
                if event.get("total") is not None:
                    p["total"] = event["total"]
                if event.get("state") == "file":
                    p["done"] = event.get("done", p["done"])
                    p["current"] = event.get("name")
                    p["note"] = None
            elif kind == "transcribe":
                # A recording being transcribed (D273): kept beside the file count, not over it.
                pos, dur = event.get("position"), event.get("duration")
                where = _clock(pos) + (f" of {_clock(dur)}" if dur else "") if pos is not None else ""
                p["note"] = f"Transcribing {event.get('name') or 'a recording'}" + (f", {where}" if where else "")
            elif kind == "model":
                # A one-time model download, before pre-processing or from Settings (D273).
                if event.get("state") in ("done", "failed"):
                    p["note"] = None
                    if p["stage"] == "model":
                        p.update(stage=None, done=None, total=None)
                else:
                    p.update(stage="model", done=event.get("done"), total=event.get("total"),
                             current=event.get("label"))
            elif kind == "download":
                p.update(stage="download", done=event.get("done"), total=event.get("total"),
                         current=event.get("url"))
            elif kind == "message":
                p["current"] = event.get("text")


class JobManager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    # ── lifecycle ───────────────────────────────────────────────────────────────────────
    def start(self, vault: Path | None, op: str, params: dict | None, label: str,
              kind: str | None = None) -> Job:
        spec, checked, cwd = prepare(vault, op, params)
        _require_granted_cwd(cwd)
        job = Job(cwd, op, checked, label or op, kind)
        keys = _secrets(cwd) if spec.secrets else {}
        popen_kwargs: dict = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            job.proc = subprocess.Popen(
                worker_argv(), cwd=str(cwd) if cwd else None, env=_env(bool(keys)),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **popen_kwargs)
        except OSError as e:
            raise rpc.RpcError(f"Could not start: {e}") from e
        _hand_over(job.proc, {"op": op, "params": checked}, keys)
        with self.lock:
            self.jobs[job.id] = job
            self._prune()
        if cwd is not None:
            from watchdog.gui.runlocks import forget_note
            forget_note(cwd)
        rpc.emit("job.started", {"job": job.to_dict()})
        readers = [threading.Thread(target=self._read, args=(job, job.proc.stdout, "out"), daemon=True),
                   threading.Thread(target=self._read, args=(job, job.proc.stderr, "err"), daemon=True)]
        for t in readers:
            t.start()
        threading.Thread(target=self._supervise, args=(job, readers), daemon=True).start()
        return job

    def _prune(self) -> None:
        finished = [j for j in self.jobs.values() if j.state != "running"]
        for j in finished[:-KEEP_FINISHED] if len(finished) > KEEP_FINISHED else []:
            self.jobs.pop(j.id, None)

    def _read(self, job: Job, stream, name: str) -> None:
        for raw in iter(stream.readline, b""):
            text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            event = progress.parse(text) if name == "out" else None
            if event is not None and event.get("kind") == "result":
                job.result = event.get("result")
                continue
            if event is not None:
                job.apply_progress(event)
                rpc.emit("job.progress", {"id": job.id, "progress": job.to_dict()["progress"],
                                          "event": event})
                continue
            line = {"t": _now(), "stream": name, "text": strip_ansi(text)}
            with job.lock:
                job.log.append(line)
                job.pending.append(line)
        stream.close()

    def _flush(self, job: Job) -> None:
        with job.lock:
            lines, job.pending = job.pending, []
        if lines:
            rpc.emit("job.log", {"id": job.id, "lines": lines})

    def _supervise(self, job: Job, readers: list[threading.Thread]) -> None:
        while any(t.is_alive() for t in readers):
            self._flush(job)
            time.sleep(LOG_INTERVAL)
        code = job.proc.wait()
        self._flush(job)
        job.exit_code = code
        job.finished = _now()
        # A stopped job is "cancelled" however it exits: a graceful Ctrl+C stop often exits 0.
        job.state = "cancelled" if job.cancel_requests else ("done" if code == 0 else "failed")
        rpc.emit("job.finished", {"job": job.to_dict()})

    def cancel(self, job_id: str) -> None:
        job = self.get(job_id)
        if job.state != "running" or job.proc is None or job.proc.poll() is not None:
            return
        job.cancel_requests += 1
        try:
            if job.cancel_requests == 1:
                if os.name == "nt":
                    job.proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    job.proc.send_signal(signal.SIGINT)
            else:
                job.proc.kill()
        except (OSError, ValueError):
            pass

    def get(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if job is None:
            raise rpc.RpcError("No such job.", code="not_found")
        return job

    def shutdown(self) -> None:
        for job in list(self.jobs.values()):
            proc = job.proc
            if job.state == "running" and proc is not None and proc.poll() is None:
                job.cancel_requests += 1
                try:
                    proc.kill()
                except OSError:
                    pass
        for job in list(self.jobs.values()):
            if job.proc is not None:
                try:
                    job.proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    pass


MANAGER = JobManager()


def _require_granted_cwd(vault: Path | None) -> None:
    """A command runs in a folder only once the user has allowed it (watchdog/access.py). The
    subprocess enforces the same rule on every write; refusing up front gives a clear message
    instead of a failed run."""
    if vault is not None:
        from watchdog.gui.vaultio import require_granted
        require_granted(Path(vault))


def _error_text(stderr: str) -> str:
    """The reason a worker gives for failing: its last stderr lines, without a traceback."""
    lines = [ln for ln in strip_ansi(stderr).split("\n") if ln.strip()]
    if any(ln.startswith("Traceback") for ln in lines):
        lines = lines[-1:]
    return "\n".join(lines[-3:]).strip()


def run_action(vault: Path | None, op: str, params: dict | None, timeout: float) -> dict:
    """A quick operation run to completion in a worker: `{code, result, log, error}`, where `log`
    is its output with progress lines removed and `error` the reason it gives when it fails."""
    spec, checked, cwd = prepare(vault, op, params)
    _require_granted_cwd(cwd)
    keys = _secrets(cwd) if spec.secrets else {}
    from watchdog.keystore import child_input
    payload = _request_line({"op": op, "params": checked}) + (child_input(keys) if keys else b"")
    try:
        done = subprocess.run(worker_argv(), cwd=str(cwd) if cwd else None, env=_env(bool(keys)),
                              input=payload, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise rpc.RpcError(f"This did not finish within {int(timeout)} seconds.", code="timeout") from e
    except OSError as e:
        raise rpc.RpcError(f"Could not run it: {e}") from e
    result, log = None, []
    # Not splitlines(): it also breaks at the record separator that starts a progress line.
    for line in done.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n").split("\n"):
        event = progress.parse(line)
        if event is None:
            log.append(strip_ansi(line))
        elif event.get("kind") == "result":
            result = event.get("result")
    stderr = done.stderr.decode("utf-8", errors="replace")
    return {"code": done.returncode, "result": result, "log": "\n".join(log).strip("\n"),
            "error": _error_text(stderr) if done.returncode else None}
