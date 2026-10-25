"""Housekeeping passes: small, bounded model chores over items code could not settle (D330).

A chore is a list of items (one per thing to settle), a prompt that shows them numbered, a schema
whose answer is a list of ``{"item": <number>, ...}`` objects, and a validator that code runs on
every answer before anything is applied. ``run_chore`` does the rest the same way for every chore:
it makes no call when there is nothing to do, splits the items into calls of at most
``Chore.max_items``, checks the spending limit before every call after the first (D324), records
each call's usage under the chore's own task name, and keeps going past a malformed or rejected
answer — a bad answer leaves its item unsettled, it never fails the batch.

One chore per call. The answer shape (numbered items, one list) is the same for every chore so
that two small chores could later share a call, but nothing here does that yet.

The first chore is the date pass (``dates.CHORE``). Code, not the model, applies what a validator
accepts; the model only ever proposes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


def answers_schema(answer_properties: dict, required: list[str]) -> dict:
    """The schema of a chore's answer: ``{"answers": [{"item": int, ...}]}``. `answer_properties`
    are the chore's own fields beside `item`; `required` names which of them must be present."""
    props = {"item": {"type": "integer"}, **answer_properties}
    return {
        "type": "object",
        "properties": {"answers": {"type": "array", "items": {
            "type": "object", "properties": props, "required": ["item", *required],
            "additionalProperties": False}}},
        "required": ["answers"],
        "additionalProperties": False,
    }


@dataclass
class Chore:
    """One kind of housekeeping job.

    - `task`: the usage task name its calls are recorded under (Activity → Usage, FINALIZE_TASKS).
    - `schema`: the answer schema, normally from `answers_schema`.
    - `build_prompt(items)`: the prompt for one call; items are shown numbered from 1.
    - `validate(item, answer)`: the settled value for `item`, or None to reject `answer`.
    - `max_items`: the most items one call is shown.
    """
    task: str
    schema: dict
    build_prompt: Callable[[list[Any]], str]
    validate: Callable[[Any, dict], Any]
    max_items: int = 80


@dataclass
class ChoreResult:
    """What a chore settled: `settled` maps an item's index in the input list to the value its
    validator returned. `rejected` counts answers that failed validation or were malformed;
    `unanswered` holds the indices no successful call covered (a failed call, or a stop);
    `stopped` is the spending-limit message when the chore stopped early."""
    settled: dict[int, Any] = field(default_factory=dict)
    rejected: int = 0
    unanswered: set[int] = field(default_factory=set)
    calls: int = 0
    stopped: str | None = None
    error: str | None = None


def windows(n: int, size: int) -> list[list[int]]:
    """Index windows of at most `size` over `n` items, in order."""
    return [list(range(i, min(i + size, n))) for i in range(0, n, size)]


def estimate_chars(chore: Chore, items: list) -> tuple[int, int]:
    """``(calls, prompt characters)`` the chore would send for `items`, for the cost estimate."""
    if not items:
        return 0, 0
    ws = windows(len(items), chore.max_items)
    return len(ws), sum(len(chore.build_prompt([items[i] for i in w])) for w in ws)


async def run_chore(chore: Chore, items: list, *,
                    call: Callable[..., Awaitable[Any]],
                    stop: Callable[[], str | None] = lambda: None,
                    failures: tuple = (Exception,),
                    halting: tuple = ()) -> ChoreResult:
    """Run `chore` over `items`. `call(task=, schema=, prompt=, detail=)` makes one model call and
    returns an object with `.parsed`; `stop()` returns the spending-limit message or None;
    `failures` are the exceptions a call may raise that leave its window unanswered, and
    `halting` the subset after which no later call is attempted (a rate limit, a rejected key)."""
    result = ChoreResult()
    if not items:
        return result
    ws = windows(len(items), chore.max_items)
    for n, window in enumerate(ws, 1):
        if result.calls:
            msg = stop()
            if msg:
                result.stopped = msg
                for w in ws[n - 1:]:
                    result.unanswered.update(w)
                break
        try:
            r = await call(task=chore.task, schema=chore.schema,
                           prompt=chore.build_prompt([items[i] for i in window]),
                           detail=f"call {n} of {len(ws)}" if len(ws) > 1 else None)
        except failures as e:   # one failed window must not lose the others
            result.error = str(e)
            result.unanswered.update(window)
            if halting and isinstance(e, halting):
                for w in ws[n:]:
                    result.unanswered.update(w)
                break
            continue
        result.calls += 1
        seen: set[int] = set()
        answers = (getattr(r, "parsed", None) or {}).get("answers")
        for a in answers if isinstance(answers, list) else []:
            k = a.get("item") if isinstance(a, dict) else None
            if not (isinstance(k, int) and not isinstance(k, bool) and 1 <= k <= len(window)) or k in seen:
                result.rejected += 1
                continue
            seen.add(k)
            idx = window[k - 1]
            try:
                value = chore.validate(items[idx], a)
            except Exception:   # noqa: BLE001 — a validator bug rejects the answer, never the batch
                value = None
            if value is None:
                result.rejected += 1
                continue
            result.settled[idx] = value
    return result
