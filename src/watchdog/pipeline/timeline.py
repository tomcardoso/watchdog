"""
Watchdog timeline tools.

timeline-collisions  Promote pending raw files to canonical; return collision JSON.
rebuild-timeline     Read all canonical {date}.ndjson files; render timeline.md.

File naming convention in .watchdog/timeline/:
  {date}.ndjson             — canonical (orchestrator-maintained, deduplicated)
  {date}_{sha256[:7]}.ndjson — raw subagent output; always written by subagents

Canonical files have no underscore in the stem (dates use hyphens only).
"""

import json
import sys
from pathlib import Path


def _timeline_dir(vault: Path) -> Path:
    return vault / ".watchdog" / "timeline"


def _group_files(timeline_dir: Path) -> dict[str, dict]:
    """Return {date: {"canonical": Path|None, "raw": [Path, ...]}} for all NDJSON files."""
    groups: dict[str, dict] = {}
    for f in sorted(timeline_dir.glob("*.ndjson")):
        stem = f.stem
        if "_" in stem:
            date = stem.split("_", 1)[0]
            groups.setdefault(date, {"canonical": None, "raw": []})["raw"].append(f)
        else:
            date = stem
            groups.setdefault(date, {"canonical": None, "raw": []})["canonical"] = f
    return groups


def _read_ndjson_lines(path: Path) -> list[str]:
    return [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _stage_dedup_key(date: str, event: str) -> str:
    """Match write_vault's dedup convention: date + full event text (not a prefix — a
    truncated key can collide on a long fact's shared opening while its divergent, material
    part lands past the cutoff)."""
    return f"{date}|{event.lower()}"


def stage_timeline_events(vault: Path, extraction: dict) -> int:
    """Write raw per-date NDJSON timeline files from an extraction blob.

    Reads the dated facts on ``document.key_facts`` (#140): every key_fact carrying a ``date`` is a
    timeline event, with its ``entities`` tags supplying the contributing entity ids. Attaches the
    document sha, deduplicates within the document by (date, event text) while unioning entity ids,
    groups by date, and writes one raw ``.watchdog/timeline/{date}_{sha[:7]}.ndjson`` file per date.
    The existing ``timeline-collisions`` / ``rebuild-timeline`` flow consumes these unchanged.

    Returns the number of dates written.
    """
    doc = extraction.get("document") or {}
    sha = doc.get("sha256", "")
    if not sha:
        return 0

    # date -> dedup_key -> record
    by_date: dict[str, dict[str, dict]] = {}
    for fact in doc.get("key_facts", []):
        date = (fact.get("date") or "").strip()
        event_text = (fact.get("fact") or "").strip()
        if not date or not event_text:
            continue
        tags = [e for e in (fact.get("entities") or []) if e]
        key = _stage_dedup_key(date, event_text)
        bucket = by_date.setdefault(date, {})
        if key in bucket:
            for eid in tags:
                if eid not in bucket[key]["entity_ids"]:
                    bucket[key]["entity_ids"].append(eid)
        else:
            bucket[key] = {
                "date": date,
                "event": event_text,
                "source_sha256": sha,
                "entity_ids": list(tags),
                "basis": fact.get("basis", "stated"),
            }

    if not by_date:
        return 0

    td = _timeline_dir(vault)
    td.mkdir(parents=True, exist_ok=True)
    short = sha[:7]
    for date, bucket in by_date.items():
        path = td / f"{date}_{short}.ndjson"
        lines = [json.dumps(rec, ensure_ascii=False) for rec in bucket.values()]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return len(by_date)


def collisions(vault: Path) -> list[dict]:
    """Promote no-canonical dates to canonical; return the remaining collisions.

    For dates with only raw files: merge → write canonical. For dates that already
    had a canonical: return ``{date, canonical, raw}`` for semantic dedup by the caller.
    """
    td = _timeline_dir(vault)
    if not td.exists():
        td.mkdir(parents=True, exist_ok=True)
        return []

    groups = _group_files(td)
    result = []
    for date, g in sorted(groups.items()):
        raw_files = sorted(g["raw"])
        if not raw_files:
            continue
        if g["canonical"] is None:
            lines: list[str] = []
            for rf in raw_files:
                lines.extend(_read_ndjson_lines(rf))
            (td / f"{date}.ndjson").write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            result.append({
                "date": date,
                "canonical": str(g["canonical"].relative_to(vault)),
                "raw": [str(rf.relative_to(vault)) for rf in raw_files],
            })
    return result


def cmd_timeline_collisions(vault: Path) -> None:
    """Prints a JSON array of collision objects to stdout (CLI wrapper)."""
    print(json.dumps(collisions(vault), ensure_ascii=False))


# Prepended to every rendered timeline.md. The file is fully regenerated from the canonical
# .watchdog/timeline/ NDJSON on each rebuild, so hand edits are silently lost — say so up top.
_TIMELINE_HEADER = (
    "# Timeline\n\n"
    "*Auto-generated from `.watchdog/timeline/`. Do not edit by hand — this file is "
    "overwritten on the next ingest or `watchdog timeline`.*\n\n"
)


def _write_timeline_md(vault: Path, content: str) -> None:
    (vault / "timeline.md").write_text(content, encoding="utf-8")
    try:
        from watchdog.pipeline.fulltext import add_note as fts_add_note
        fts_add_note(vault, "timeline", "timeline", "Timeline", content)
    except Exception as e:
        print(f"  Warning: full-text index update failed for timeline: {e}", file=sys.stderr)


def cmd_rebuild_timeline(vault: Path, quiet: bool = False) -> tuple[int, int]:
    """Read all canonical {date}.ndjson files and render timeline.md.

    Returns (date_count, event_count) so callers can report progress.
    """
    td = _timeline_dir(vault)

    if not td.exists() or not any(td.glob("*.ndjson")):
        _write_timeline_md(vault, _TIMELINE_HEADER + "*No events yet.*\n")
        if not quiet:
            print("timeline.md written — no events yet")
        return (0, 0)

    # Canonical files only: no underscore in stem
    canonical_files = sorted(
        f for f in td.glob("*.ndjson") if "_" not in f.stem
    )

    if not canonical_files:
        _write_timeline_md(vault, _TIMELINE_HEADER + "*No events yet.*\n")
        if not quiet:
            print("timeline.md written — no canonical files yet")
        return (0, 0)

    sections: list[str] = []
    total_events = 0

    for cf in canonical_files:
        date = cf.stem
        events: list[dict] = []
        for line in _read_ndjson_lines(cf):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        if not events:
            continue

        total_events += len(events)
        lines = [f"### {date}", ""]
        for ev in events:
            basis_note = " *(inferred)*" if ev.get("basis") == "inferred" else ""
            lines.append(f"- {ev['event']}{basis_note}")
        sections.append("\n".join(lines))

    content = _TIMELINE_HEADER + "\n\n".join(sections) + "\n"
    _write_timeline_md(vault, content)
    if not quiet:
        print(f"timeline.md rebuilt — {len(canonical_files)} date(s), {total_events} event(s)")
    return (len(canonical_files), total_events)


def main_collisions() -> None:
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")
    cmd_timeline_collisions(vault)


def main_rebuild() -> None:
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")
    cmd_rebuild_timeline(vault)
