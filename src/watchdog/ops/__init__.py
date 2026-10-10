"""Watchdog's operations: every change the desktop app makes to an investigation, as a library
function with typed parameters and a `Reporter` (D298).

An operation is a plain function registered with `@op(name, …)`. Its keyword-only parameters,
with their annotations, are its schema: `schema(name)` describes them, `validate(name, params)`
checks a JSON object against them, and `run(name, params, reporter, vault)` calls it. The app
runs an operation in a worker process (`watchdog.worker`), never in the backend itself, so a crash
or a hung model call can't take the backend down and Stop is a signal to that process.

Output goes through the reporter, not `print`: `log` lines, `warn` lines, `progress` events (the
shapes `watchdog.progress` defines, so the app's stage labels and job dock read them unchanged),
and the return value, a JSON-serializable dict, is the result. `hint(terminal, app)` picks the
wording for where the output is read; under the worker it is always the app's. A question an
operation would ask at a terminal (`confirm`, `pick`) is declined under the worker: the app asks
its questions before it starts an operation and passes the answers as parameters.

Helpers deep inside an operation reach the active reporter with `current()`.
"""

from __future__ import annotations

import contextlib
import contextvars
import inspect
import re
import sys
import types
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from watchdog import progress as _progress

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text or "")


# ── reporters ─────────────────────────────────────────────────────────────────────────────


class Reporter:
    """Where an operation's output goes. The base class prints, as a terminal command does."""

    @property
    def app(self) -> bool:
        from watchdog.appmode import under_app
        return under_app()

    def log(self, text: str = "") -> None:
        print(text)

    def warn(self, text: str) -> None:
        print(text, file=sys.stderr)

    def progress(self, kind: str, **fields) -> None:
        _progress.emit(kind, **fields)

    def hint(self, terminal: str, app: str | None = "") -> str:
        from watchdog.appmode import hint
        return hint(terminal, app)

    def confirm(self, prompt: str, default: bool = False) -> bool:
        from watchdog import interactive
        return interactive.confirm(prompt, default=default)

    def pick(self, items, current: int = 0, *, title: str | None = None):
        from watchdog import interactive
        return interactive.pick(items, current, title=title)


TerminalReporter = Reporter


class AppReporter(Reporter):
    """The worker's reporter: plain app-worded lines on stdout, warnings on stderr, progress as
    `watchdog.progress` lines, and no questions."""

    app = True

    def log(self, text: str = "") -> None:
        sys.stdout.write(strip_ansi(text) + "\n")
        sys.stdout.flush()

    def warn(self, text: str) -> None:
        sys.stderr.write(strip_ansi(text) + "\n")
        sys.stderr.flush()

    def progress(self, kind: str, **fields) -> None:
        _progress.write(kind, **fields)

    def hint(self, terminal: str, app: str | None = "") -> str:
        return app or ""

    def confirm(self, prompt: str, default: bool = False) -> bool:
        return False

    def pick(self, items, current: int = 0, *, title: str | None = None):
        from watchdog import interactive
        return interactive.CANCELLED


class CollectingReporter(Reporter):
    """Keeps everything it is given: for tests and in-process callers."""

    app = True

    def __init__(self):
        self.lines: list[str] = []
        self.warnings: list[str] = []
        self.events: list[dict] = []

    def log(self, text: str = "") -> None:
        self.lines.append(strip_ansi(text))

    def warn(self, text: str) -> None:
        self.warnings.append(strip_ansi(text))

    def progress(self, kind: str, **fields) -> None:
        self.events.append({"kind": kind, **fields})

    def hint(self, terminal: str, app: str | None = "") -> str:
        return app or ""

    def confirm(self, prompt: str, default: bool = False) -> bool:
        return False

    def pick(self, items, current: int = 0, *, title: str | None = None):
        from watchdog import interactive
        return interactive.CANCELLED

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


_current: contextvars.ContextVar[Reporter | None] = contextvars.ContextVar("watchdog_reporter", default=None)
_DEFAULT = Reporter()


def current() -> Reporter:
    """The reporter of the operation running now; a terminal one outside any."""
    return _current.get() or _DEFAULT


@contextlib.contextmanager
def using(reporter: Reporter | None):
    token = _current.set(reporter or _DEFAULT)
    try:
        yield reporter or _DEFAULT
    finally:
        _current.reset(token)


def say(text: str = "") -> None:
    current().log(text)


def hint(terminal: str, app: str | None = "") -> str:
    return current().hint(terminal, app)


# ── the registry ──────────────────────────────────────────────────────────────────────────


class OpError(Exception):
    """A request the operation registry refuses (an unknown operation, a bad parameter)."""


@dataclass
class Op:
    name: str
    fn: Callable
    vault: bool          # runs in an investigation (the worker's working folder)
    engine: str | None   # "add" or "index": needs the full engine (D272)
    secrets: bool        # calls a model, so the worker is handed the investigation's keys (D295)
    kind: str            # "job" (long, shown in the job dock) or "action" (quick)
    exit_code: Callable[[Any], int] | None = None
    params: dict[str, dict] = field(default_factory=dict)


OPS: dict[str, Op] = {}

# The run options the app's forms share (`RunOptions` in gui/src/shared/api.ts). One an operation
# doesn't take is dropped rather than refused, so a form can pass its whole set.
RUN_OPTIONS = frozenset({
    "extractor_model", "classifier_model", "finalizer_model", "finalizer_reconciliation_model",
    "finalizer_synthesis_model", "finalizer_timeline_model", "finalizer_briefing_model",
    "extractor_effort", "classifier_effort", "finalizer_effort", "concurrency", "classify_pages",
    "skill", "limit", "verify", "force", "wait", "skip_briefing", "chew_workers", "chunk_workers",
})


def _type_name(tp) -> tuple[str, bool]:
    """('str' | 'int' | 'bool' | 'list[str]' | 'float', optional?) for an annotation."""
    optional = False
    origin = typing.get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        optional = len(args) != len(typing.get_args(tp))
        tp = args[0] if len(args) == 1 else tp
        origin = typing.get_origin(tp)
    if origin is list:
        (inner,) = typing.get_args(tp) or (str,)
        return f"list[{inner.__name__}]", optional
    if tp in (str, int, bool, float):
        return tp.__name__, optional
    raise TypeError(f"unsupported parameter type {tp!r}")


def op(name: str, *, vault: bool = True, engine: str | None = None, secrets: bool = False,
       kind: str = "job", exit_code: Callable[[Any], int] | None = None):
    """Register `fn(rep, vault, *, …params)` (or `fn(rep, *, …)` when `vault=False`)."""
    def register(fn):
        hints = typing.get_type_hints(fn)
        params: dict[str, dict] = {}
        for p in inspect.signature(fn).parameters.values():
            if p.kind is not inspect.Parameter.KEYWORD_ONLY:
                continue
            type_name, optional = _type_name(hints[p.name])
            required = p.default is inspect.Parameter.empty
            params[p.name] = {"type": type_name, "required": required,
                              "default": None if required else p.default,
                              "nullable": optional}
        OPS[name] = Op(name, fn, vault, engine, secrets, kind, exit_code, params)
        return fn
    return register


def get(name: str) -> Op:
    _load()
    spec = OPS.get(name)
    if spec is None:
        raise OpError(f"Unknown operation: {name}")
    return spec


def schema() -> dict:
    """Every operation's parameters and properties, as the app reads them (`jobs.ops`)."""
    _load()
    return {n: {"params": s.params, "vault": s.vault, "engine": s.engine, "kind": s.kind}
            for n, s in sorted(OPS.items())}


def _check(name: str, key: str, value, spec: dict):
    t = spec["type"]
    if value is None:
        if spec["nullable"] or not spec["required"]:
            return None
        raise OpError(f"{name}: {key} is required.")
    ok = {"str": lambda v: isinstance(v, str),
          "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
          "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
          "bool": lambda v: isinstance(v, bool),
          "list[str]": lambda v: isinstance(v, list) and all(isinstance(i, str) for i in v)}[t]
    if t == "int" and isinstance(value, str) and value.strip().lstrip("-").isdigit():
        value = int(value)
    if not ok(value):
        raise OpError(f"{name}: {key} must be {t}.")
    return value


def validate(name: str, params: dict | None) -> dict:
    """The keyword arguments for `name` from a JSON object: an unset value (None or "") is left
    out, a shared run option the operation doesn't take is dropped, anything else unknown or of
    the wrong type is refused."""
    spec = get(name)
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise OpError("params must be an object.")
    out: dict = {}
    for key, value in params.items():
        if key not in spec.params:
            if key in RUN_OPTIONS:
                continue
            raise OpError(f"{name} takes no parameter “{key}”.")
        if value is None or value == "":
            continue
        out[key] = _check(name, key, value, spec.params[key])
    missing = [k for k, s in spec.params.items() if s["required"] and k not in out]
    if missing:
        raise OpError(f"{name}: {', '.join(missing)} required.")
    return out


def run(name: str, params: dict | None, reporter: Reporter | None = None,
        vault: Path | None = None):
    """Run operation `name` with JSON `params`, its output going to `reporter`."""
    spec = get(name)
    kwargs = validate(name, params)
    with using(reporter):
        if spec.vault:
            return spec.fn(current(), Path(vault or ".").resolve(), **kwargs)
        return spec.fn(current(), **kwargs)


def exit_code(name: str, result) -> int:
    spec = get(name)
    return spec.exit_code(result) if spec.exit_code else 0


_MODULES = ("ingest", "projects", "reindex", "export", "merge", "entities", "maintenance", "research", "setup")
_loaded = False


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    import importlib
    for m in _MODULES:
        importlib.import_module(f"watchdog.ops.{m}")
