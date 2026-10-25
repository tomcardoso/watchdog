"""A diagnostics report with no content in it, safe to post publicly (D331): counts and shapes of
an investigation's records, as one Markdown file, with no model call.

The troubleshooting copy (`ops.troubleshoot`, D323) is investigation material, for the reporter's
own use. This report is the one to attach to an issue: it carries aggregates only, so a bug that shows
in counts and shapes (a date form dropped, a cap that cut a group of same-name records, quote
failures concentrated in one file type, the spread of document requests) can be reported
publicly without anonymising anything.

**The report is built from an allowlist, never by filtering.** Every key in it is a constant in
this module or a value picked from a fixed vocabulary (`_pick`), and every value is a number
computed here. A field added to a registry later is never read, so it cannot leak; the only free
text that is read at all is the processing log's warnings, which are reduced to their kind
(`warning_shape`). `build(vault)` returns the report as a dict, `render(report)` writes it as
Markdown, and the app shows `render`'s text in full before it is saved.
"""

from __future__ import annotations

import datetime
import json
import platform
import re
import sys
from collections import Counter
from pathlib import Path

from watchdog.ops import op, require_vault

FORMAT_VERSION = 1

# ── fixed vocabularies: a value outside one is counted as "other", never printed ─────────────────

_FILE_TYPES = frozenset({
    "pdf", "docx", "pptx", "xlsx", "html", "xhtml", "xml", "asciidoc", "adoc", "tex", "vtt",
    "txt", "csv", "md", "jpg", "jpeg", "png", "tiff", "tif", "bmp", "webp",
    "mp3", "wav", "m4a", "mp4", "mov", "mkv", "webm", "flac", "ogg", "aac",
})
_ENTITY_TYPES = frozenset({"person", "organization", "public-body", "place", "asset", "proceeding"})
_BASES = frozenset({"stated", "inferred"})
_TIERS = frozenset({"high", "medium", "low"})
_DECIDED_BY = frozenset({"rule", "model", "reporter"})
# The rule names `pipeline.identity` and the reconciler give a verdict: code's own words.
_RULES = frozenset({
    "same-identifier", "same-name-shared-attribute", "same-name", "same-partial-name",
    "partial-name", "distinctive-name", "generic-name", "reordered-name", "similar-name",
})
_CANDIDATE_STATUS = frozenset({"open", "merged", "dismissed"})
_TASKS = frozenset({
    "classify", "extract", "extract-section", "verify", "reconcile", "entity-synthesis",
    "timeline-dedup", "timeline-precision", "briefing", "request-dedup", "relationship-labels",
    "ask", "research",
})
_BACKENDS = frozenset({"claude-api", "claude-agent-sdk", "claude-batch", "openai", "openai-batch",
                       "deepseek", "gemini", "local", "openrouter"})
_AUTH_MODES = frozenset({"subscription", "api-key"})
_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"})
_STOP_REASONS = frozenset({"end_turn", "max_tokens", "stop_sequence", "tool_use", "length", "stop",
                           "refusal"})
_LOG_KINDS = ("START", "OK", "FAILED", "WARN", "INGEST", "HARVEST", "SECTION", "VERIFY",
              "RECONCILE", "MERGED", "CONTRADICTION", "SPENDING LIMIT")
_REVIEW_KINDS = ("contradictions", "leads", "alerts", "duplicates", "merges")

# Bucket edges (label, upper bound inclusive; None = no bound).
_PAGES = (("1", 1), ("2-5", 5), ("6-20", 20), ("21-100", 100), ("101+", None))
_COUNTS = (("0", 0), ("1", 1), ("2", 2), ("3-5", 5), ("6-10", 10), ("11-30", 30), ("31+", None))
_GROUPS = (("2", 2), ("3", 3), ("4-5", 5), ("6-10", 10), ("11-30", 30), ("31+", None))
_SECONDS = (("under 1s", 1), ("1-10s", 10), ("10-60s", 60), ("1-10 min", 600), ("over 10 min", None))

_MAX_SHAPES = 40


def _bucket(n: int, edges) -> str:
    for label, top in edges:
        if top is None or n <= top:
            return label
    return edges[-1][0]


def _dist(values, edges) -> dict:
    """How many of `values` fall in each bucket, every bucket listed (zero too)."""
    counts = Counter(_bucket(int(v), edges) for v in values)
    return {label: counts.get(label, 0) for label, _ in edges}


def _pick(value, allowed, other: str = "other") -> str:
    return value if isinstance(value, str) and value in allowed else other


def _tally(values, allowed) -> dict:
    c = Counter(_pick(v, allowed) for v in values)
    return dict(sorted(c.items()))


def _num(x) -> float:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else 0.0


def _int(x) -> int:
    return int(_num(x))


def _read(path: Path, default):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def _file_type(filename) -> str:
    ext = Path(filename).suffix.lower().lstrip(".") if isinstance(filename, str) else ""
    return ext if ext in _FILE_TYPES else "other"


def _date_shape(value) -> str:
    """A date's precision, never the date: day, month, year or something else."""
    v = value.strip() if isinstance(value, str) else ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return "day"
    if re.fullmatch(r"\d{4}-\d{2}", v):
        return "month"
    if re.fullmatch(r"\d{4}", v):
        return "year"
    return "other" if v else "none"


# ── warnings reduced to their kind ───────────────────────────────────────────────────────────────

_TS = re.compile(r"^\[[^\]]*\]\s*")
_QUOTED = (
    re.compile(r'"[^"\n]*"'), re.compile(r"`[^`\n]*`"), re.compile(r"“[^”\n]*”"),
    re.compile(r"(?<![A-Za-z0-9])'[^'\n]*'(?![A-Za-z0-9])"), re.compile(r"(?<![A-Za-z0-9])‘[^’\n]*’"),
)
_STRAY_QUOTE = re.compile(r"[\"`“”].*$")
_BRACKETED = re.compile(r"\([^)\n]*\)|\[[^\]\n]*\]|\{[^}\n]*\}")
_FILELIKE = re.compile(r"\S+\.(?:" + "|".join(sorted(_FILE_TYPES)) + r")\b", re.IGNORECASE)
_HEX = re.compile(r"\b(?:0x)?[0-9a-fA-F]{8,}\b")
_DIGITS = re.compile(r"\d+")
_SPACE = re.compile(r"\s+")
_SHAPE_MAX = 140


def warning_shape(line: str, names=None) -> str:
    """One processing-log line reduced to its kind (D331): the level kept, the text up to the
    first ": " dropped (it is the file or item the warning is about), anything in quotes or
    brackets removed, file-like words and long hex runs replaced, every digit run written as N,
    and, when `names` (a compiled pattern over the investigation's own names) is given, any of
    those names removed wherever it appears."""
    text = _TS.sub("", (line or "").strip())
    kind = next((k for k in ("WARN", "FAILED", "SPENDING LIMIT") if text.startswith(k)), None)
    if kind is None:
        return ""
    rest = text[len(kind):].lstrip()
    if ": " in rest:
        rest = rest.split(": ", 1)[1]
    elif kind != "SPENDING LIMIT":
        rest = rest.replace(":", " ", 1) if rest.endswith(":") else rest
    for pat in _QUOTED:
        rest = pat.sub("", rest)
    rest = _STRAY_QUOTE.sub("", rest)
    rest = _BRACKETED.sub("", rest)
    if names is not None:
        rest = names.sub("", rest)
    rest = _FILELIKE.sub("<file>", rest)
    rest = _HEX.sub("N", rest)
    rest = _DIGITS.sub("N", rest)
    rest = _SPACE.sub(" ", rest).strip(" :;,-—–")
    return f"{kind} {rest[:_SHAPE_MAX]}".strip()


def _name_pattern(entities: dict, documents: dict):
    """A pattern over the names this investigation holds (entities, their aliases, document
    titles and file names) so a warning that spells one outside quotes loses it too."""
    found: set[str] = set()
    for e in entities.values():
        if isinstance(e, dict):
            found.add(e.get("name"))
            found.update(a for a in (e.get("aliases") or []) if isinstance(a, str))
    for d in documents.values():
        if isinstance(d, dict):
            found.update((d.get("filename"), d.get("title")))
    names = sorted({n.strip() for n in found if isinstance(n, str) and len(n.strip()) >= 4},
                   key=len, reverse=True)[:3000]
    if not names:
        return None
    return re.compile("|".join(re.escape(n) for n in names), re.IGNORECASE)


def _log_section(vault: Path, entities: dict, documents: dict) -> dict:
    from watchdog.vault_paths import processing_log
    try:
        lines = processing_log(vault).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    kinds: Counter = Counter()
    shapes: Counter = Counter()
    names = None
    for raw in lines:
        text = _TS.sub("", raw.strip())
        k = next((k for k in _LOG_KINDS if text.startswith(k + " ") or text == k), None)
        if k is None:
            if text:
                kinds["other"] += 1
            continue
        kinds[k] += 1
        if k in ("WARN", "FAILED", "SPENDING LIMIT"):
            if names is None:
                names = _name_pattern(entities, documents) or False
            shapes[warning_shape(raw, names or None)] += 1
    ranked = shapes.most_common()
    shown = ranked[:_MAX_SHAPES]
    return {
        "lines": len(lines),
        "by_kind": {k: kinds[k] for k in (*_LOG_KINDS, "other") if kinds.get(k)},
        "warning_shapes": [{"shape": s, "count": n} for s, n in shown],
        "warning_shapes_not_listed": sum(n for _, n in ranked[_MAX_SHAPES:]),
    }


# ── the sections ─────────────────────────────────────────────────────────────────────────────────


def _documents(documents: dict) -> dict:
    from watchdog import model_catalog
    from watchdog.skills_catalog import _package_records
    docs = [d for d in documents.values() if isinstance(d, dict)]
    # Bundled record skills only: a skill the reporter wrote has a name of their choosing.
    skills = {f.name[:-3] for f in _package_records().iterdir() if f.name.endswith(".md")}
    models = {m["id"] for m in model_catalog.all_models()}
    return {
        "total": len(docs),
        "by_file_type": _tally((_file_type(d.get("filename")) for d in docs), _FILE_TYPES),
        "by_page_count": _dist((_int(d.get("page_count")) for d in docs), _PAGES),
        "by_record_skill": _tally((d.get("record_skill") for d in docs), skills),
        "by_extract_model": _tally((d.get("extract_model") for d in docs), models),
        "by_extract_effort": _tally((d.get("extract_effort") for d in docs), _EFFORTS),
        "near_duplicates": sum(1 for d in docs if d.get("near_duplicate_of")),
        "with_coverage_gap": sum(1 for d in docs if d.get("coverage_gap")),
        "without_entities": sum(1 for d in docs if not d.get("entities_extracted")),
    }


def _facts(vault: Path, documents: dict) -> dict:
    ext_dir = vault / ".watchdog" / "extracted"
    types = {sha: _file_type(d.get("filename")) for sha, d in documents.items() if isinstance(d, dict)}
    out = {"documents_with_extraction": 0, "total": 0, "by_basis": Counter(), "with_quote": 0,
           "quote_not_found": 0, "added_by_verify": 0, "with_unverified_figure": 0,
           "date_shape": Counter(), "documents_without_facts": 0}
    per_type: dict[str, Counter] = {}
    for sha, ftype in sorted(types.items()):
        art = _read(ext_dir / f"{sha}.json", {})
        doc = art.get("document") if isinstance(art.get("document"), dict) else None
        facts = doc.get("key_facts") if doc and isinstance(doc.get("key_facts"), list) else None
        if facts is None:
            continue
        out["documents_with_extraction"] += 1
        facts = [f for f in facts if isinstance(f, dict) and str(f.get("fact") or "").strip()]
        if not facts:
            out["documents_without_facts"] += 1
        t = per_type.setdefault(ftype, Counter())
        for f in facts:
            out["total"] += 1
            t["facts"] += 1
            out["by_basis"][_pick(f.get("basis"), _BASES)] += 1
            out["date_shape"][_date_shape(f.get("date"))] += 1
            if str(f.get("quote") or "").strip():
                out["with_quote"] += 1
                t["with_quote"] += 1
                if f.get("quote_verified") is False:
                    out["quote_not_found"] += 1
                    t["quote_not_found"] += 1
            if f.get("added_by") == "verify":
                out["added_by_verify"] += 1
            if f.get("figures_unverified"):
                out["with_unverified_figure"] += 1
    out["by_basis"] = dict(sorted(out["by_basis"].items()))
    out["date_shape"] = {k: out["date_shape"].get(k, 0) for k in ("day", "month", "year", "other", "none")}
    out["by_file_type"] = {
        ft: {"facts": c["facts"], "with_quote": c["with_quote"], "quote_not_found": c["quote_not_found"]}
        for ft, c in sorted(per_type.items()) if c["facts"]}
    return out


def _entities(entities: dict, hidden: frozenset) -> dict:
    from watchdog.pipeline.identity import name_key
    ents = {i: e for i, e in entities.items() if isinstance(e, dict)}
    groups: Counter = Counter()
    for e in ents.values():
        key = name_key(e.get("name") or "")
        if key:
            groups[(_pick(e.get("type"), _ENTITY_TYPES), key)] += 1
    shared = [n for n in groups.values() if n > 1]
    roles = [r for e in ents.values() for r in (e.get("roles") or []) if isinstance(r, dict)]
    events = [ev for e in ents.values() for ev in (e.get("timeline_events") or []) if isinstance(ev, dict)]
    return {
        "records": len(ents),
        "removed_from_investigation": sum(1 for i in ents if i in hidden),
        "by_type": _tally((e.get("type") for e in ents.values()), _ENTITY_TYPES),
        "documents_per_record": _dist((len(e.get("appears_in") or []) for e in ents.values()), _COUNTS),
        "aliases_per_record": _dist((len(e.get("aliases") or []) for e in ents.values()), _COUNTS),
        "with_summary": sum(1 for e in ents.values() if isinstance(e.get("synthesis"), dict)
                            and str(e["synthesis"].get("summary") or "").strip()),
        "with_flagged_contradictions": sum(1 for e in ents.values() if e.get("contradictions")),
        "same_name_groups": len(shared),
        "records_in_same_name_groups": sum(shared),
        "same_name_group_sizes": _dist(shared, _GROUPS),
        "largest_same_name_group": max(shared, default=0),
        "relationships": len(roles),
        "relationships_by_basis": _tally((r.get("basis") for r in roles), _BASES),
        "timeline_events": len(events),
        "timeline_event_dates": dict(sorted(Counter(_date_shape(ev.get("date")) for ev in events).items())),
    }


def _merges(vault: Path, entities: dict) -> dict:
    from watchdog.pipeline import merge_log
    data = merge_log.load(vault)
    merges = [m for m in data["merges"] if isinstance(m, dict)]
    cands = [c for c in data["candidates"].values() if isinstance(c, dict)]
    carry = data.get("carry")
    carried = carry.get("ids") if isinstance(carry, dict) else None
    open_now = merge_log.open_candidates(vault, entities, data=data)
    return {
        "merges": len(merges),
        "by_tier": _tally((m.get("tier") for m in merges), _TIERS),
        "by_decider": _tally((m.get("decided_by") for m in merges), _DECIDED_BY),
        "by_rule": _tally((m.get("rule") for m in merges), _RULES),
        "same_name_merges": sum(1 for m in merges if m.get("same_name")),
        "possible_same_pairs": len(cands),
        "pairs_by_status": _tally((c.get("status") for c in cands), _CANDIDATE_STATUS),
        "pairs_by_tier": _tally((c.get("tier") for c in cands), _TIERS),
        "pairs_by_rule": _tally((c.get("rule") for c in cands), _RULES),
        "pairs_the_model_declined": sum(1 for c in cands if c.get("model_declined")),
        "pairs_open_now": len(open_now),
        "records_carried_to_the_next_run": len(carried) if isinstance(carried, list) else 0,
    }


def _requests(vault: Path) -> dict:
    from watchdog.pipeline import requests, resolutions
    reqs = {rid: r for rid, r in requests.load(vault).get("requests", {}).items() if isinstance(r, dict)}
    resolved = resolutions.resolved_ids(vault)
    sources = [len(r.get("sources") or []) for r in reqs.values()]
    return {
        "total": len(reqs),
        "open": sum(1 for rid in reqs if rid not in resolved),
        "handled": sum(1 for rid in reqs if rid in resolved),
        "sources_per_request": _dist(sources, _COUNTS),
        "with_a_likely_source": sum(1 for r in reqs.values() if str(r.get("likely_source") or "").strip()),
    }


def _review(vault: Path) -> dict:
    from watchdog import review
    from watchdog.pipeline import leads
    items = review.open_items(vault)
    by_kind = Counter(i.get("kind") for i in items)
    found = leads.scan(vault)

    def docs(group, field):
        return [_int(x.get(field)) for x in found.get(group, []) if isinstance(x, dict)]
    return {
        "open_by_kind": {k: by_kind.get(k, 0) for k in _REVIEW_KINDS},
        "leads_by_kind": {
            "named_but_never_profiled": len(found.get("unprofiled", [])),
            "mentioned_often_but_unconnected": len(found.get("isolated", [])),
            "entities_with_contradictions": len(found.get("contradictions", [])),
            "entities_with_inferred_relationships": len(found.get("inferred", [])),
        },
        "documents_per_lead": {
            "named_but_never_profiled": _dist(docs("unprofiled", "doc_count"), _COUNTS),
            "mentioned_often_but_unconnected": _dist(docs("isolated", "doc_count"), _COUNTS),
            "entities_with_inferred_relationships": _dist(docs("inferred", "doc_count"), _COUNTS),
        },
    }


def _run_mechanics(vault: Path) -> dict:
    from watchdog import model_catalog
    from watchdog.pipeline.orchestrate import usage_files
    from watchdog.usage import wall_span
    models = {m["id"] for m in model_catalog.all_models()}
    runs = 0
    spans: list[float] = []
    per: dict[str, dict] = {}
    for f in usage_files(vault):
        data = _read(f, {})
        calls = [c for c in data.get("calls", []) if isinstance(c, dict)] if isinstance(data.get("calls"), list) else []
        runs += 1
        span = wall_span(calls)
        if span is not None:
            spans.append(span)
        for c in calls:
            t = per.setdefault(_pick(c.get("task"), _TASKS), {
                "calls": 0, "failed": 0, "retried": 0, "extra_attempts": 0, "stopped_at_output_limit": 0,
                "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0,
                "cost_usd": 0.0, "seconds": 0.0, "slowest_call_seconds": 0.0,
                "models": Counter(), "backends": Counter(), "efforts": Counter(), "billing": Counter()})
            t["calls"] += 1
            t["failed"] += 1 if c.get("failed") is True else 0
            attempts = max(_int(c.get("attempts")), 1)
            t["retried"] += 1 if attempts > 1 else 0
            t["extra_attempts"] += attempts - 1
            t["stopped_at_output_limit"] += 1 if c.get("stop_reason") in ("max_tokens", "length") else 0
            for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
                t[k] += _int(c.get(k))
            t["cost_usd"] += _num(c.get("cost_usd"))
            t["seconds"] += _num(c.get("latency_s"))
            t["slowest_call_seconds"] = max(t["slowest_call_seconds"], _num(c.get("latency_s")))
            t["models"][_pick(c.get("model"), models, "other")] += 1
            t["backends"][_pick(c.get("backend"), _BACKENDS)] += 1
            t["efforts"][_pick(c.get("effort"), _EFFORTS, "none")] += 1
            t["billing"][_pick(c.get("auth_mode"), _AUTH_MODES)] += 1
    tasks = {}
    for name in sorted(per):
        t = per[name]
        tasks[name] = {**{k: v for k, v in t.items() if not isinstance(v, Counter)},
                       "cost_usd": round(t["cost_usd"], 4), "seconds": round(t["seconds"], 1),
                       "slowest_call_seconds": round(t["slowest_call_seconds"], 1),
                       **{k: dict(sorted(t[k].items())) for k in ("models", "backends", "efforts", "billing")}}
    return {"runs": runs, "run_duration": _dist(spans, _SECONDS), "by_task": tasks}


def _settings() -> dict:
    """The reporter's settings that can be read without reading anything about the investigation:
    the D323 manifest's filter (known, not secret, not a folder), narrowed to numbers, switches
    and choices from a fixed list. A model is named only if the catalog knows it; every other
    text setting (a name, a web address, a language list) says only that it is set."""
    from watchdog import model_catalog
    from watchdog.ops.troubleshoot import _app_settings
    from watchdog.settings import SETTINGS
    models = {m["id"].lower() for m in model_catalog.all_models()} | set(model_catalog._MODEL_IDS)
    out: dict = {}
    for k, v in _app_settings().items():
        meta = SETTINGS[k]
        kind = meta.get("type")
        if kind == "bool" and isinstance(v, bool):
            out[k] = v
        elif kind in ("int", "float", "int_or_auto"):
            numeric = isinstance(v, (int, float)) and not isinstance(v, bool)
            out[k] = v if numeric or v == "auto" else "set"
        elif kind == "enum" and v in (meta.get("choices") or []):
            out[k] = v
        elif k.endswith("_model") and isinstance(v, str):
            out[k] = v if v.split(":")[-1].lower() in models else "custom"
        elif v not in (None, "", []):
            out[k] = "set"
    return dict(sorted(out.items()))


def build(vault: Path) -> dict:
    """The whole report as a dict of counts, from an explicit list of what to read."""
    import watchdog
    from watchdog.pipeline import hidden
    vault = Path(vault)
    reg = vault / ".watchdog" / "registry"
    entities = _read(reg / "entities.json", {})
    documents = _read(reg / "documents.json", {})
    return {
        "format_version": FORMAT_VERSION,
        "watchdog_version": watchdog.__version__,
        "platform": {"system": platform.system(), "release": platform.release(),
                     "machine": platform.machine(), "python": platform.python_version()},
        "settings": _section(_settings),
        "documents": _section(_documents, documents),
        "facts": _section(_facts, vault, documents),
        "entities": _section(_entities, entities, hidden.hidden_ids(vault)),
        "merges": _section(_merges, vault, entities),
        "requests": _section(_requests, vault),
        "review": _section(_review, vault),
        "runs": _section(_run_mechanics, vault),
        "processing_log": _section(_log_section, vault, entities, documents),
    }


def _section(fn, *args) -> dict:
    """One section, or a note that it could not be read: a damaged file loses that section, not
    the report. The error is never quoted, since its text can carry what was in the file."""
    try:
        return fn(*args)
    except Exception:
        return {"could_not_be_read": True}


# ── Markdown ─────────────────────────────────────────────────────────────────────────────────────


def _pretty(key: str) -> str:
    return key.replace("_", " ")


def _lines(value, depth: int = 0) -> list[str]:
    pad = "  " * depth
    out: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, dict):
                if not v:
                    out.append(f"{pad}- {_pretty(str(k))}: none")
                elif all(not isinstance(x, (dict, list)) for x in v.values()):
                    out.append(f"{pad}- {_pretty(str(k))}: " + ", ".join(f"{_pretty(str(a))}: {b}" for a, b in v.items()))
                else:
                    out.append(f"{pad}- {_pretty(str(k))}:")
                    out.extend(_lines(v, depth + 1))
            elif isinstance(v, list):
                out.append(f"{pad}- {_pretty(str(k))}:" + ("" if v else " none"))
                out.extend(_lines(v, depth + 1))
            else:
                out.append(f"{pad}- {_pretty(str(k))}: {v}")
    else:
        for item in value:
            if isinstance(item, dict) and set(item) == {"shape", "count"}:
                out.append(f"{pad}- {item['count']} x {item['shape']}")
            else:
                out.append(f"{pad}- {item}")
    return out


_HEAD = {
    "settings": "Settings", "documents": "Documents", "facts": "Facts", "entities": "Entities",
    "merges": "Merges and possible duplicates", "requests": "Document requests",
    "review": "Review and leads", "runs": "Runs: models, calls, tokens, cost and time",
    "processing_log": "Processing log",
}


def render(report: dict) -> str:
    """The report as Markdown, for the dialog to show and the file to hold."""
    out = [
        "# Watchdog diagnostics report",
        "",
        "Counts and shapes only: no file names, titles, names, quotes, facts, figures, identifiers, "
        "dates or summaries. Read it through before posting it. Even counts can hint at things, "
        "such as the languages or kinds of documents.",
        "",
        f"- Watchdog version: {report['watchdog_version']}",
        "- Platform: " + ", ".join(f"{k} {v}" for k, v in report["platform"].items()),
        f"- Report format: {report['format_version']}",
    ]
    for key, title in _HEAD.items():
        out += ["", f"## {title}", ""]
        out += _lines(report[key]) or ["- none"]
    return "\n".join(out) + "\n"


def text(vault: Path) -> str:
    return render(build(vault))


@op("diagnostics-report")
def diagnostics_report(rep, vault: Path, *, output: str) -> dict:
    """Write the content-free diagnostics report (one Markdown file) into the folder `output`,
    for posting with a public bug report. No model is used."""
    require_vault(vault)
    out_dir = Path(output).expanduser()
    if not out_dir.is_dir():
        sys.exit(f"Error: the folder {out_dir} does not exist.")
    # Named by time alone: the investigation's name can say what it is about.
    stamp = datetime.datetime.now().strftime("%Y-%m-%d-%H%M%S")
    target = out_dir / f"watchdog-diagnostics-{stamp}.md"
    n = 2
    while target.exists():
        target = out_dir / f"watchdog-diagnostics-{stamp}-{n}.md"
        n += 1
    body = text(Path(vault))
    part = target.with_name(target.name + ".part")
    try:
        part.write_text(body, encoding="utf-8")
        part.replace(target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    rep.log(f"Wrote {target.name} to {out_dir}. Read it through before posting it.")
    return {"output": str(target), "bytes": len(body.encode("utf-8"))}
