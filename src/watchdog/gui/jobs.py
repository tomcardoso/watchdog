"""Long-running `watchdog` commands, run as subprocesses for the desktop app.

A job is `python -m watchdog <args…>` with the vault as its working directory, stdin closed (a
prompt the CLI would show is declined rather than waited on), colour off, and `WATCHDOG_PROGRESS=1`
so the pipeline writes the structured lines `watchdog.progress` defines. Two reader threads split
the child's output: progress lines update the job's `ProgressState` and become `job.progress`
events; every other line goes to a ring buffer (the last 5,000) and out as batched `job.log` events
(at most ten a second).

Cancelling sends Ctrl+C (SIGINT, or CTRL_BREAK_EVENT on Windows) so the CLI's own graceful stop
runs; a second cancel kills the process.

Keys the app stores encrypted reach a command on its stdin, one JSON line, then end of input
(`WATCHDOG_SECRETS=stdin`, D295): only the key per provider the investigation resolves to, and
never through the environment or argv, which other programs and crash reports can see.
"""

from __future__ import annotations

import collections
import datetime
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from watchdog import progress
from watchdog.gui import rpc
from watchdog.gui.vaultio import strip_ansi

LOG_LINES = 5000
KEEP_FINISHED = 50
LOG_INTERVAL = 0.1          # seconds between job.log batches: at most ten a second

# RunOptions key → CLI flag. Only the flags a command's own parser accepts are emitted.
_VALUE_FLAGS = {
    "extractor_model": "--extractor-model", "classifier_model": "--classifier-model",
    "finalizer_model": "--finalizer-model",
    "finalizer_reconciliation_model": "--finalizer-reconciliation-model",
    "finalizer_synthesis_model": "--finalizer-synthesis-model",
    "finalizer_timeline_model": "--finalizer-timeline-model",
    "finalizer_briefing_model": "--finalizer-briefing-model",
    "extractor_effort": "--extractor-effort", "classifier_effort": "--classifier-effort",
    "finalizer_effort": "--finalizer-effort", "concurrency": "--concurrency",
    "classify_pages": "--classify-pages", "skill": "--skill", "limit": "--limit",
    "chew_workers": "--chew-workers", "chunk_workers": "--chunk-workers",
}
_SWITCH_FLAGS = {"force": "--force", "wait": "--wait", "skip_briefing": "--skip-briefing"}
COMMANDS = ("add", "dig", "bark", "chew")

# Commands that need the engine's phase-2 libraries or models (D272): adding documents in any
# form, the incoming-folder watcher, putting failed documents back in the queue, and anything that
# rewrites the semantic search index. Refused with `engine_not_ready` until the engine is complete.
ADD_COMMANDS = frozenset({"add", "chew", "dig", "bark", "ingest", "watch", "requeue"})
INDEX_COMMANDS = frozenset({"reindex", "merge-entities", "rebuild-notes", "undo-merge",
                            "recheck-contradictions"})
ENGINE_BUSY_OTHER = ("Watchdog is still setting up. This will be available when it finishes, "
                     "in a few minutes.")


def canonical_command(args: list[str]) -> str | None:
    """The command `watchdog <args…>` runs, after grouped forms and aliases are resolved."""
    from watchdog.cmd.base import _ALIASES, _DEPRECATED_ALIASES
    from watchdog.cmd.groups import rewrite

    argv = rewrite(list(args))
    if not argv:
        return None
    head = argv[0]
    return _DEPRECATED_ALIASES.get(head) or _ALIASES.get(head) or head


def require_engine(args: list[str]) -> None:
    """Refuse a command that needs the full engine while the app is still installing it."""
    from watchdog.gui.engine_setup import ENGINE_NOT_READY, engine_ready

    if engine_ready():
        return
    command = canonical_command(args)
    if command in ADD_COMMANDS:
        raise rpc.RpcError(ENGINE_NOT_READY, code="engine_not_ready", data={"command": command})
    if command in INDEX_COMMANDS:
        raise rpc.RpcError(ENGINE_BUSY_OTHER, code="engine_not_ready", data={"command": command})


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


def _hand_over(proc: subprocess.Popen, keys: dict[str, str]) -> None:
    from watchdog.keystore import child_input
    try:
        proc.stdin.write(child_input(keys))
    except OSError:
        pass   # the command already exited; its own error says why
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass


def command_argv(args: list[str]) -> list[str]:
    """The process a job runs. A module-level function so tests can substitute a quick script."""
    return [sys.executable, "-m", "watchdog", *args]


def accepted_flags(command: str) -> set[str]:
    """Every option string `watchdog <command>` accepts, read from the real parser."""
    from watchdog.cli import _subparsers, build_parser
    sub = _subparsers(build_parser()).get(command)
    if sub is None:
        raise rpc.RpcError(f"Unknown command: {command}", code="bad_params")
    return {opt for action in sub._actions for opt in action.option_strings}


def flags_for(command: str, options: dict | None) -> list[str]:
    """CLI arguments for `RunOptions` on `command`: unset values give nothing, `verify` true/false
    gives --verify/--no-verify, and a flag the command does not take is left out."""
    options = options or {}
    accepted = accepted_flags(command)
    out: list[str] = []
    for key, flag in _VALUE_FLAGS.items():
        value = options.get(key)
        if value not in (None, "") and flag in accepted:
            out += [flag, str(value)]
    for key, flag in _SWITCH_FLAGS.items():
        if options.get(key) is True and flag in accepted:
            out.append(flag)
    verify = options.get("verify")
    if verify is True and "--verify" in accepted:
        out.append("--verify")
    elif verify is False and "--no-verify" in accepted:
        out.append("--no-verify")
    return out


class Job:
    def __init__(self, vault: Path | None, args: list[str], label: str, kind: str | None):
        self.id = uuid.uuid4().hex[:12]
        self.vault = vault
        self.args = list(args)
        self.label = label
        self.kind = kind or (args[0] if args else "job")
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
                "vault": str(self.vault) if self.vault else None, "args": self.args,
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
    def start(self, vault: Path | None, args: list[str], label: str, kind: str | None,
              argv: list[str] | None = None) -> Job:
        """`argv` replaces the `python -m watchdog <args>` process, for the one job that runs
        another module (`setup.downloadModel`); `args` still labels the job."""
        require_engine(args)
        _require_granted_cwd(vault)
        job = Job(vault, args, label, kind)
        env = {**os.environ, "NO_COLOR": "1", "WATCHDOG_PROGRESS": "1", "WATCHDOG_APP": "1",
               "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        # `argv` runs another module, which reads no keys.
        keys = _secrets(vault) if argv is None else {}
        if keys:
            env["WATCHDOG_SECRETS"] = "stdin"
        popen_kwargs: dict = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            job.proc = subprocess.Popen(
                argv or command_argv(args), cwd=str(vault) if vault else None, env=env,
                stdin=subprocess.PIPE if keys else subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, **popen_kwargs)
        except OSError as e:
            raise rpc.RpcError(f"Could not start the command: {e}") from e
        if keys:
            _hand_over(job.proc, keys)
        with self.lock:
            self.jobs[job.id] = job
            self._prune()
        if vault is not None:
            from watchdog.gui.runlocks import forget_note
            forget_note(vault)
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


def run_action(vault: Path | None, args: list[str], timeout: float) -> dict:
    """A short command run to completion: `{code, stdout, stderr}` with colour codes removed."""
    require_engine(args)
    _require_granted_cwd(vault)
    env = {**os.environ, "NO_COLOR": "1", "WATCHDOG_APP": "1", "PYTHONIOENCODING": "utf-8",
           "PYTHONUNBUFFERED": "1"}
    env.pop("WATCHDOG_PROGRESS", None)
    keys = _secrets(vault)
    if keys:
        env["WATCHDOG_SECRETS"] = "stdin"
    from watchdog.keystore import child_input
    try:
        done = subprocess.run(command_argv(args), cwd=str(vault) if vault else None, env=env,
                              input=child_input(keys) if keys else None,
                              stdin=None if keys else subprocess.DEVNULL, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise rpc.RpcError(f"The command did not finish within {int(timeout)} seconds.",
                           code="timeout") from e
    except OSError as e:
        raise rpc.RpcError(f"Could not run the command: {e}") from e
    return {"code": done.returncode,
            "stdout": strip_ansi(done.stdout.decode("utf-8", errors="replace")),
            "stderr": strip_ansi(done.stderr.decode("utf-8", errors="replace"))}
