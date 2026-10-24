"""An investigation's spending limit (D324): a dollar figure at which processing stops.

The limit and the time of its last reset live in the investigation's `.watchdog/settings.json`
(the file D290's choice of key started), so they travel with the folder:

    {"spending_limit": {"usd": 25.0, "reset_at": "2026-10-24T12:00:00Z"}}

The figure it is compared with is **recorded** spend, never an estimate (D251 and D263 removed a
dollar auto-approve because estimates built from past runs could come out far too low): the sum of
`cost_usd` over the call records Activity → Usage reads, `.watchdog/registry/usage/usage-<ts>.json`
plus a running run's `.partial.jsonl`. Three kinds of record do not count:

- calls made before the last reset (by their `end_ts`, or the file's own timestamp without one);
- Ask Claude and Web research turns (`ask`, `research`), which D318 lets the reporter bill apart
  from processing and which live in `usage/sessions/` anyway;
- calls on the Claude subscription (`auth_mode: subscription`): their recorded cost is a
  list-price equivalent, not a charge, so on a subscription the limit does not apply.

The run checks this before each document, between sections of a long document and before each
finishing stage (`orchestrate._spending_stop`), so a call already under way can take spending past
the limit by up to its own cost.
"""

from __future__ import annotations

import datetime
import json
import math
from pathlib import Path

from watchdog.pipeline.json_io import _read_json_or

# Turns of a conversation (D296, D318): billed apart from processing, never counted here.
SESSION_TASKS = frozenset({"ask", "research"})
# A typo guard, not a policy: no investigation needs a limit past this.
MAX_USD = 1_000_000.0
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
_FILE_TS_FMT = "%Y%m%dT%H%M%SZ"


def usage_dir(vault: Path) -> Path:
    return Path(vault) / ".watchdog" / "registry" / "usage"


# ── the setting ──────────────────────────────────────────────────────────────────────────

def limit(vault: Path) -> dict:
    """`{"usd": float | None, "reset_at": str | None}` from the investigation's settings. A value
    that isn't a positive number reads as no limit."""
    from watchdog.auth import read_investigation_settings
    raw = read_investigation_settings(vault).get("spending_limit")
    raw = raw if isinstance(raw, dict) else {}
    usd = raw.get("usd")
    if isinstance(usd, bool) or not isinstance(usd, (int, float)) or not math.isfinite(usd) or usd <= 0:
        usd = None
    reset_at = raw.get("reset_at") if isinstance(raw.get("reset_at"), str) else None
    if reset_at is not None and _parse_ts(reset_at) is None:
        reset_at = None
    return {"usd": float(usd) if usd is not None else None, "reset_at": reset_at}


def parse_usd(value) -> float | None:
    """A limit as the reporter typed it: `25`, `"25.50"`, `"$1,000"`. Blank or None clears it.
    Raises ValueError, with a sentence to show, for anything else."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("The spending limit must be an amount in US dollars, such as 25.")
    if isinstance(value, (int, float)):
        usd = float(value)
    elif isinstance(value, str):
        text = value.strip().replace(",", "").removeprefix("US$").removeprefix("$").strip()
        if not text:
            return None
        try:
            usd = float(text)
        except ValueError:
            raise ValueError("The spending limit must be an amount in US dollars, such as 25.") from None
    else:
        raise ValueError("The spending limit must be an amount in US dollars, such as 25.")
    if not math.isfinite(usd) or round(usd, 2) <= 0:
        raise ValueError("The spending limit must be at least $0.01. Leave it blank for no limit.")
    if usd > MAX_USD:
        raise ValueError(f"The spending limit can be at most ${MAX_USD:,.0f}.")
    return round(usd, 2)


def _write(vault: Path, *, usd=..., reset_at=...) -> None:
    from watchdog.auth import read_investigation_settings, write_investigation_settings
    data = read_investigation_settings(vault)
    cur = data.get("spending_limit") if isinstance(data.get("spending_limit"), dict) else {}
    cur = dict(cur)
    if usd is not ...:
        if usd is None:
            cur.pop("usd", None)
        else:
            cur["usd"] = usd
    if reset_at is not ...:
        cur["reset_at"] = reset_at
    if cur:
        data["spending_limit"] = cur
    else:
        data.pop("spending_limit", None)
    write_investigation_settings(vault, data)


def set_limit(vault: Path, value) -> float | None:
    """Store the limit (`parse_usd`'s rules); None or blank removes it. The reset time is kept."""
    usd = parse_usd(value)
    _write(vault, usd=usd)
    return usd


def reset(vault: Path, now: datetime.datetime | None = None) -> str:
    """Start counting again from now: record the time in the settings, so only spend recorded
    after it counts towards the limit. The usage records themselves are untouched."""
    when = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(datetime.timezone.utc)
    stamp = when.strftime(_TS_FMT)
    _write(vault, reset_at=stamp)
    return stamp


# ── recorded spend ───────────────────────────────────────────────────────────────────────

def _parse_ts(text: str | None, fmt: str = _TS_FMT) -> float | None:
    if not text:
        return None
    try:
        return datetime.datetime.strptime(text, fmt).replace(
            tzinfo=datetime.timezone.utc).timestamp()
    except ValueError:
        return None


def counts(record: dict) -> bool:
    """Whether a call record is processing spend that was charged: not a conversation's turn, and
    not a call on the Claude subscription."""
    if not isinstance(record, dict):
        return False
    if record.get("task") in SESSION_TASKS:
        return False
    return record.get("auth_mode") != "subscription"


def sum_records(records, since: float | None = None, fallback_ts: float | None = None) -> float:
    """Charged processing cost over `records` recorded at or after `since` (epoch seconds; None
    counts everything). A record without `end_ts` is dated by `fallback_ts`, its file's time."""
    total = 0.0
    for r in records or []:
        if not counts(r):
            continue
        if since is not None:
            when = r.get("end_ts") if isinstance(r.get("end_ts"), (int, float)) else fallback_ts
            if when is None or when < since:
                continue
        cost = r.get("cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost):
            total += cost
    return total


def _file_ts(path: Path) -> float | None:
    stem = path.name.removeprefix("usage-").split(".", 1)[0]
    return _parse_ts(stem, _FILE_TS_FMT)


def _partial_records(path: Path) -> list[dict]:
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def recorded(vault: Path, since: str | None = None, *, include_partial: bool = True) -> float:
    """Charged processing spend recorded for the investigation since `since` (a reset time, as
    stored), from every run's usage file and, with `include_partial`, a running or interrupted
    run's partial file. A running process passes `include_partial=False` and adds its own
    in-memory records instead, so its calls are not counted twice."""
    d = usage_dir(vault)
    if not d.is_dir():
        return 0.0
    cutoff = _parse_ts(since)
    total = 0.0
    for f in sorted(d.glob("usage-*.json")):
        data = _read_json_or(f, {})
        calls = data.get("calls") if isinstance(data, dict) else None
        if isinstance(calls, list):
            total += sum_records(calls, cutoff, _file_ts(f))
    if include_partial:
        for f in sorted(d.glob("usage-*.partial.jsonl")):
            if f.with_name(f.name.replace(".partial.jsonl", ".json")).exists():
                continue   # already folded into its usage file
            total += sum_records(_partial_records(f), cutoff, _file_ts(f))
    return total


# ── what the app and the run say ─────────────────────────────────────────────────────────

def stop_message(spent: float, usd: float) -> str:
    return (f"Stopped at this investigation's spending limit: ${spent:,.2f} recorded against a "
            f"limit of ${usd:,.2f}. Finished documents are kept and the rest stay queued. To "
            f"continue, raise or reset the spending limit on the investigation's Overview, then "
            f"add documents again.")


def status(vault: Path) -> dict:
    """What the Overview's spending-limit card shows: the limit, the reset time, what has been
    recorded since it, what is left, and whether the limit has been reached."""
    lim = limit(vault)
    spent = round(recorded(vault, lim["reset_at"]), 6)
    usd = lim["usd"]
    remaining = None if usd is None else round(max(usd - spent, 0.0), 6)
    return {"limit_usd": usd, "reset_at": lim["reset_at"], "spent_usd": spent,
            "remaining_usd": remaining, "reached": usd is not None and spent >= usd}


def estimate_warning(st: dict, cost_high: float | None) -> str | None:
    """A warning, never a stop, when an estimate's upper end would pass what is left under the
    limit (`st` is `status`). None without a limit or a dollar estimate."""
    if st.get("limit_usd") is None:
        return None
    left = st.get("remaining_usd") or 0.0
    if st.get("reached"):
        return (f"This investigation has reached its spending limit (${st['limit_usd']:,.2f}). "
                f"The run will stop before sending anything; raise or reset the limit on the "
                f"Overview first.")
    if cost_high is None or cost_high <= left:
        return None
    return (f"This estimate may pass the ${left:,.2f} left under the investigation's spending limit "
            f"(${st['limit_usd']:,.2f}). The run stops when the limit is reached; the documents not "
            f"yet read stay queued.")
