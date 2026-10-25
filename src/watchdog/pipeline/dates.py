"""Reading the dates the extractor transcribed (D329), and the model pass for the ones code
cannot settle (D330).

The extraction prompt transcribes a fact's date as printed (TRANSCRIBE, DON'T CORRECT), so
`key_facts[].date` arrives as "12 septembre 2019", "3 maggio 2020", "12/09/2019", "Q1 2020" or
"the following Tuesday" as often as ISO. `read` turns what code can settle into ISO shape:

- Words in any language `dateparser` knows, absolute dates only: relative wording ("yesterday",
  "in two days") is never parsed.
- A year must be printed. `dateparser` fills a missing part from today, so "March" would come back
  as this year's March; a wording without a year is left for the model pass instead.
- Only the precision the wording gives: "mai 2020" is ``2020-05``, never a made-up day. The parse
  is run against two different reference dates, and a part that changes between them was filled
  in, not read, so the date is not settled by code.
- A numeric date is read by code, never by `dateparser`. When both readings of a day/month pair
  are real and different (03/04/2021) code does not guess: the date keeps its year and goes to
  the model pass with the two readings, the only answers it may give.

Everything else (ranges, quarters, dates in words, relative dates) is left for the pass: the
fact keeps its printed wording in `date_text` and is marked `date_pending`. The pass
(`CHORE`, run by `housekeeping.run_chore` in finalize before the commit) answers with an ISO
date, a start and an end, or nothing, and `validate` checks every answer. A date the model
settled is marked `date_basis: "inferred"` and keeps its printed wording beside it (I1).
"""

from __future__ import annotations

import json
import re
from datetime import date as _date, datetime
from pathlib import Path

from watchdog.pipeline import housekeeping

ISO_SHAPE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")

PENDING_AMBIGUOUS = "ambiguous"     # a numeric day/month pair with two real readings
PENDING_UNRESOLVED = "unresolved"   # anything else code could not read

_YEAR4 = re.compile(r"(?<!\d)(\d{4})(?!\d)")
_NUMERIC_ONLY = re.compile(r"^[\d\s./-]+$")
# A day/month pair written in figures inside a wording that also has words ("le 03/04/2021"):
# dateparser would pick an order silently, so it goes to the pass.
_NUMERIC_PAIR = re.compile(r"\d\s*[./]\s*\d")
# A span between two figures or two words ("3–7 April 2024", "2019-2021", "April–June 2024").
_SPAN = re.compile(r"\d\s*[-–—]\s*\d|[–—]")
# Year-month(-day) with the Chinese/Japanese or Korean markers ("2021年3月15日", "2021년 3월 15일"),
# read by code: dateparser reads the first and not the second. Gregorian years only — a Japanese
# era year ("令和3年") has no four-digit year and goes to the pass.
_CJK = re.compile(r"^(\d{4})\s*[年년]\s*(\d{1,2})\s*[月월](?:\s*(\d{1,2})\s*[日일])?$")
# A clock time beside a date ("3 June 2019 at 10:00") is not part of the date.
_TIME = re.compile(r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)")
# Words that make a wording something other than one date — a bound ("after March 2020"), an
# approximation ("early June 2019") or several dates ("3 and 7 June 2019"). dateparser skips many
# of them silently, so a wording holding one goes to the pass. The common languages only; a word
# missed here in another language is read as the date it names, which is the pre-D329 risk.
_QUALIFIERS = frozenset("""
before after by until till since from around about approximately approx circa ca early mid late
end beginning start between and or to through
avant après apres depuis vers environ jusqu jusqu'au début debut fin mi entre et ou au
antes después despues desde hasta hacia aproximadamente principios finales mediados y o
prima dopo entro fino verso inizio fine metà meta tra fra e oppure al dal
vor nach seit bis ab etwa ungefähr anfang ende mitte zwischen und oder
""".split())
_TWO_DIGIT_YEAR_FORM =re.compile(r"^\d{1,2}(?:st|nd|rd|th)?[-\s.]+[^\W\d_]+\.?[-\s.]+(\d{2})$")

_SEP = r"\s*([./-])\s*"
_Y_M = re.compile(rf"^(\d{{4}}){_SEP}(\d{{1,2}})$")
_M_Y = re.compile(rf"^(\d{{1,2}}){_SEP}(\d{{4}})$")
_A_B_Y = re.compile(rf"^(\d{{1,2}}){_SEP}(\d{{1,2}}){_SEP}(\d{{4}})$")
_Y_A_B = re.compile(rf"^(\d{{4}}){_SEP}(\d{{1,2}}){_SEP}(\d{{1,2}})$")

# Two reference dates, far apart in month and day: a part dateparser filled in from the reference
# differs between the two parses, a part it read does not.
_BASES = (datetime(2001, 1, 1), datetime(2003, 12, 31))
_PERIODS = {"year": 1, "month": 2, "day": 3, "time": 3}


class Reading:
    """What code made of one printed date. `date` is ISO (or ""), `pending` says whether the pass
    should look at it, and `readings` are the two possible dates of an ambiguous numeric one."""
    __slots__ = ("date", "pending", "readings")

    def __init__(self, date: str = "", pending: str | None = None, readings: tuple = ()):
        self.date, self.pending, self.readings = date, pending, readings

    def __repr__(self) -> str:   # pragma: no cover — debugging aid
        return f"Reading({self.date!r}, {self.pending!r}, {self.readings!r})"


def valid_iso(value) -> bool:
    """True for a real ``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD`` calendar date."""
    if not isinstance(value, str) or not ISO_SHAPE.match(value):
        return False
    parts = [int(p) for p in value.split("-")]
    try:
        _date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
    except ValueError:
        return False
    return 1000 <= parts[0] <= 2199     # a Buddhist-era or mistyped year is not placed by code


def _iso(y: int, m: int | None = None, d: int | None = None) -> str | None:
    s = f"{y:04d}" + (f"-{m:02d}" if m else "") + (f"-{d:02d}" if d else "")
    return s if valid_iso(s) else None


def _numeric(text: str) -> Reading:
    """A date written only in figures."""
    if m := _Y_M.match(text):
        iso = _iso(int(m[1]), int(m[3]))
        return Reading(iso) if iso else Reading(pending=PENDING_UNRESOLVED)
    if m := _M_Y.match(text):
        iso = _iso(int(m[3]), int(m[1]))
        return Reading(iso) if iso else Reading(pending=PENDING_UNRESOLVED)
    if m := _A_B_Y.match(text):
        a, b, y = int(m[1]), int(m[3]), int(m[5])
        dm, md = _iso(y, b, a), _iso(y, a, b)     # day-first and month-first readings
        if dm and md and dm != md:
            return Reading(f"{y:04d}", PENDING_AMBIGUOUS, (dm, md))
        one = dm or md
        return Reading(one) if one else Reading(pending=PENDING_UNRESOLVED)
    if m := _Y_A_B.match(text):
        y, sep, a, b = int(m[1]), m[2], int(m[3]), int(m[5])
        ymd = _iso(y, a, b)
        if sep == "-":                           # year-first with hyphens is ISO's own order
            return Reading(ymd) if ymd else Reading(pending=PENDING_UNRESOLVED)
        ydm = _iso(y, b, a)
        if ymd and ydm and ymd != ydm:
            return Reading(f"{y:04d}", PENDING_AMBIGUOUS, (ymd, ydm))
        one = ymd or ydm
        return Reading(one) if one else Reading(pending=PENDING_UNRESOLVED)
    return Reading(pending=PENDING_UNRESOLVED)


_PARSERS: dict = {}


def _parser(base: datetime):
    """One `DateDataParser` per reference date, built on first use (the import costs ~0.3 s)."""
    p = _PARSERS.get(base)
    if p is None:
        from dateparser.date import DateDataParser
        p = DateDataParser(settings={"PARSERS": ["absolute-time"], "RELATIVE_BASE": base,
                                     "DATE_ORDER": "DMY"})
        _PARSERS[base] = p
    return p


def _words(text: str) -> Reading:
    """A date with words in it, read by dateparser under the guards in the module docstring."""
    text = _TIME.sub(" ", text).strip(" ,")
    if _NUMERIC_PAIR.search(text) or _SPAN.search(text):
        return Reading(pending=PENDING_UNRESOLVED)
    if {w.casefold() for w in re.findall(r"[^\W\d_]+", text)} & _QUALIFIERS:
        return Reading(pending=PENDING_UNRESOLVED)
    years = {int(y) for y in _YEAR4.findall(text)}
    two_digit = _TWO_DIGIT_YEAR_FORM.match(text)
    if not years and not two_digit:
        return Reading(pending=PENDING_UNRESOLVED)
    # English legal phrasing dateparser does not know: "17th day of March, 2021", "17 th".
    cleaned = re.sub(r"\bday\s+of\b", " ", text, flags=re.I)
    cleaned = re.sub(r"(\d)\s+(st|nd|rd|th)\b", r"\1\2", cleaned, flags=re.I)
    results = []
    for base in _BASES:
        try:
            d = _parser(base).get_date_data(cleaned)
        except Exception:   # noqa: BLE001 — a parser failure leaves the date for the pass
            return Reading(pending=PENDING_UNRESOLVED)
        if d is None or d.date_obj is None or d.period not in _PERIODS:
            return Reading(pending=PENDING_UNRESOLVED)
        results.append((d.date_obj, _PERIODS[d.period]))
    (a, pa), (b, pb) = results
    precision = min(pa, pb)
    parts = [(a.year, b.year), (a.month, b.month), (a.day, b.day)][:precision]
    if any(x != y for x, y in parts):
        return Reading(pending=PENDING_UNRESOLVED)    # a part came from the reference date
    year = a.year
    if years and year not in years:
        return Reading(pending=PENDING_UNRESOLVED)
    if not years and (year % 100) != int(two_digit[1]):
        return Reading(pending=PENDING_UNRESOLVED)
    # Every figure in the wording must be one the date used: "3 and 7 June 2019" is two days.
    used = ({year, year % 100} | ({a.month} if precision > 1 else set())
            | ({a.day} if precision > 2 else set()))
    if any(int(n) not in used for n in re.findall(r"\d+", text)):
        return Reading(pending=PENDING_UNRESOLVED)
    iso = _iso(year, a.month if precision > 1 else None, a.day if precision > 2 else None)
    return Reading(iso) if iso else Reading(pending=PENDING_UNRESOLVED)


def read(raw) -> Reading:
    """Read one printed date. ISO shapes pass through untouched (shape only, as before D329)."""
    if not isinstance(raw, str) or not raw.strip():
        return Reading()
    text = raw.strip()
    if ISO_SHAPE.match(text):
        return Reading(text)
    # Punctuation carried over from the sentence the date was lifted out of.
    text = re.sub(r"\s+", " ", text).rstrip(",.;:").strip()
    if ISO_SHAPE.match(text):
        return Reading(text)
    if _NUMERIC_ONLY.match(text):
        return _numeric(text)
    if m := _CJK.match(text):
        iso = _iso(int(m[1]), int(m[2]), int(m[3]) if m[3] else None)
        return Reading(iso) if iso else Reading(pending=PENDING_UNRESOLVED)
    return _words(text)


def sanitize(extraction: dict) -> list[str]:
    """Read every ``key_facts[].date`` (post-flight, before timeline staging). An ISO value is left
    alone; one code reads becomes ISO with its printed wording kept in `date_text`; the rest keep
    their wording, are marked `date_pending` for the pass and are taken off the timeline until it
    settles them (an ambiguous numeric date keeps its year meanwhile). Returns no warnings: what
    the pass cannot settle is reported once, by finalize."""
    for fact in (extraction.get("document") or {}).get("key_facts") or []:
        if not isinstance(fact, dict):
            continue
        raw = fact.get("date")
        if not isinstance(raw, str) or not raw.strip():
            continue
        if ISO_SHAPE.match(raw.strip()):
            fact["date"] = raw.strip()
            continue
        r = read(raw)
        fact["date_text"] = raw.strip()
        fact["date"] = r.date
        if r.pending:
            fact["date_pending"] = r.pending
        if r.readings:
            fact["date_readings"] = list(r.readings)
    return []


# ── the model pass (D330) ────────────────────────────────────────────────────────────────

def _sort_key(d: str) -> str:
    return d + ("-00-00" if len(d) == 4 else "-00" if len(d) == 7 else "")


def _anchor_years(item: dict) -> set[int]:
    years = {int(y) for y in _YEAR4.findall(item.get("fact") or "")}
    doc_year = str(item.get("doc_date") or "")[:4]
    if valid_iso(doc_year):
        years.add(int(doc_year))
    return years


def validate(item: dict, answer: dict):
    """Code's check on one answer: ``{"start", "end"}`` (end may be ""), ``{}`` when the model
    gave no date, or None to reject. A start (and end) must be a real ISO calendar date, the end
    after the start and within a century of it. An ambiguous numeric date may only become one of
    its two readings. When the printed wording names years, every answered year must lie between
    them; when it names none (a relative date), the answer must lie within a year of a year the
    fact's text or the document's own date gives, and with neither there is nothing to anchor it
    and the answer is rejected."""
    start = (answer.get("start") or "").strip() if isinstance(answer.get("start"), str) else None
    end = (answer.get("end") or "").strip() if isinstance(answer.get("end"), (str, type(None))) else None
    if start is None or end is None:
        return None
    if not start:
        return {} if not end else None
    if not valid_iso(start) or (end and not valid_iso(end)):
        return None
    if end and end == start:
        end = ""
    if end and (_sort_key(end) <= _sort_key(start) or int(end[:4]) - int(start[:4]) > 100):
        return None
    if item.get("pending") == PENDING_AMBIGUOUS:
        return {"start": start, "end": ""} if start in (item.get("readings") or []) and not end else None
    years = [int(y) for y in _YEAR4.findall(item.get("text") or "")]
    answered = [int(start[:4])] + ([int(end[:4])] if end else [])
    if years:
        if not all(min(years) <= y <= max(years) for y in answered):
            return None
    else:
        anchors = _anchor_years(item)
        if not anchors or not all(any(abs(y - a) <= 1 for a in anchors) for y in answered):
            return None
    return {"start": start, "end": end}


def build_prompt(items: list[dict]) -> str:
    from watchdog.pipeline import prompts
    lines = []
    for n, it in enumerate(items, 1):
        line = (f"{n}. printed date: {json.dumps(it.get('text') or '', ensure_ascii=False)}\n"
                f"   fact: {json.dumps(it.get('fact') or '', ensure_ascii=False)}\n"
                f"   document dated: {it.get('doc_date') or 'undated'}")
        if it.get("pending") == PENDING_AMBIGUOUS:
            a, b = (it.get("readings") or ["", ""])[:2]
            line += f"\n   the figures can be read as {a} or {b}; answer one of these, or nothing"
        lines.append(line)
    return prompts.build_date_resolve_prompt("\n\n".join(lines))


SCHEMA = housekeeping.answers_schema(
    {"start": {"type": "string"}, "end": {"type": "string"}}, ["start", "end"])

CHORE = housekeeping.Chore(task="date-resolve", schema=SCHEMA, build_prompt=build_prompt,
                           validate=validate, max_items=80)


def pending_items(vault: Path, shas: list[str]) -> list[dict]:
    """Every fact of the staged (uncommitted) extractions `shas` marked `date_pending`, as a pass
    item: ``{"sha", "index", "text", "fact", "doc_date", "pending", "readings"}``."""
    from watchdog.pipeline.json_io import _read_json_or
    items = []
    for sha in shas:
        art = _read_json_or(vault / ".watchdog" / "extracted" / f"{sha}.json", None)
        doc = (art or {}).get("document") if isinstance(art, dict) else None
        if not isinstance(doc, dict):
            continue
        for i, f in enumerate(doc.get("key_facts") or []):
            if isinstance(f, dict) and f.get("date_pending") and (f.get("date_text") or "").strip():
                items.append({"sha": sha, "index": i, "text": f["date_text"],
                              "fact": f.get("fact") or "", "doc_date": doc.get("date_of_document"),
                              "pending": f["date_pending"], "readings": f.get("date_readings") or []})
    return items


def apply(vault: Path, items: list[dict], result: "housekeeping.ChoreResult") -> dict:
    """Write what the pass settled into the staged extractions, before the commit (I7): the fact's
    date (and end), `date_basis: "inferred"`, its entities' timeline events, and the document's
    raw timeline files. An item the pass looked at but did not settle loses its `date_pending`
    mark and keeps what code gave it (the year of an ambiguous date, or no date). Items no call
    covered keep the mark. Returns ``{"resolved", "undated", "documents"}``."""
    from watchdog.pipeline import timeline
    from watchdog.pipeline.json_io import _read_json_or
    from watchdog.pipeline.postflight import refresh_timeline_events
    by_sha: dict[str, list[int]] = {}
    for idx, it in enumerate(items):
        by_sha.setdefault(it["sha"], []).append(idx)
    resolved = undated = 0
    touched = []
    for sha, idxs in by_sha.items():
        path = vault / ".watchdog" / "extracted" / f"{sha}.json"
        art = _read_json_or(path, None)
        if not isinstance(art, dict):
            continue
        facts = (art.get("document") or {}).get("key_facts") or []
        changed = False
        for idx in idxs:
            if idx in result.unanswered:
                continue
            it = items[idx]
            if not (0 <= it["index"] < len(facts)) or not isinstance(facts[it["index"]], dict):
                continue
            f = facts[it["index"]]
            value = result.settled.get(idx) or {}
            f.pop("date_pending", None)
            changed = True
            if value.get("start"):
                f["date"] = value["start"]
                if value.get("end"):
                    f["date_end"] = value["end"]
                else:
                    f.pop("date_end", None)
                f["date_basis"] = "inferred"
                f.pop("date_readings", None)
                resolved += 1
            elif not f.get("date"):
                undated += 1
        if changed:
            refresh_timeline_events(art)
            timeline.restage_document(vault, art)
            path.write_text(json.dumps(art, ensure_ascii=False, indent=2), encoding="utf-8")
            touched.append(sha)
    return {"resolved": resolved, "undated": undated, "documents": touched}


# ── display ──────────────────────────────────────────────────────────────────────────────

_MON = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def render_span(start: str, end: str | None = None) -> str:
    """A date or a range as the notes show it: a single date as `write_vault._render_date` does,
    a range compactly — "Apr–Jun 2024", "3–7 Apr 2024", "3 Apr – 7 Jun 2024", "2019–2021",
    "Dec 2019 – Feb 2020". A malformed end shows the start alone."""
    from watchdog.pipeline.write_vault import _render_date
    if not end or not valid_iso(end) or not valid_iso(start or ""):
        return _render_date(start or "")
    s, e = start.split("-"), end.split("-")
    if len(s) == len(e) == 1:
        return f"{s[0]}–{e[0]}"
    if len(s) == len(e) == 2:
        if s[0] == e[0]:
            return f"{_MON[int(s[1]) - 1]}–{_MON[int(e[1]) - 1]} {s[0]}"
        return f"{_MON[int(s[1]) - 1]} {s[0]} – {_MON[int(e[1]) - 1]} {e[0]}"
    if len(s) == len(e) == 3:
        if s[:2] == e[:2]:
            return f"{int(s[2])}–{int(e[2])} {_MON[int(s[1]) - 1]} {s[0]}"
        if s[0] == e[0]:
            return f"{int(s[2])} {_MON[int(s[1]) - 1]} – {int(e[2])} {_MON[int(e[1]) - 1]} {s[0]}"
    return f"{_render_date(start)} – {_render_date(end)}"


def inferred_note(rec: dict) -> str:
    """The note a date the model settled carries in markdown: it was inferred, from what wording."""
    if rec.get("date_basis") != "inferred":
        return ""
    from watchdog.pipeline.write_vault import _defang
    text = _defang((rec.get("date_text") or "").strip())
    return f" *(date inferred from “{text}”)*" if text else " *(date inferred)*"
