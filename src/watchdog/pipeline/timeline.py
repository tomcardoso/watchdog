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


def cmd_timeline_collisions(vault: Path) -> None:
    """
    For dates with only raw files: merge → write canonical.
    For dates with canonical + raw: return as collision for LLM dedup.
    Prints a JSON array of collision objects to stdout.
    """
    td = _timeline_dir(vault)
    if not td.exists():
        td.mkdir(parents=True, exist_ok=True)
        print("[]")
        return

    groups = _group_files(td)
    collisions = []

    for date, g in sorted(groups.items()):
        raw_files = sorted(g["raw"])
        if not raw_files:
            continue

        if g["canonical"] is None:
            # No canonical yet — merge all raw files for this date into one canonical
            lines: list[str] = []
            for rf in raw_files:
                lines.extend(_read_ndjson_lines(rf))
            canonical_path = td / f"{date}.ndjson"
            canonical_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            # Canonical exists — flag as collision for the orchestrator to dedup
            collisions.append({
                "date": date,
                "canonical": str(g["canonical"].relative_to(vault)),
                "raw": [str(rf.relative_to(vault)) for rf in raw_files],
            })

    print(json.dumps(collisions, ensure_ascii=False))


def cmd_rebuild_timeline(vault: Path) -> None:
    """Read all canonical {date}.ndjson files and render timeline.md."""
    td = _timeline_dir(vault)
    timeline_md = vault / "timeline.md"

    if not td.exists() or not any(td.glob("*.ndjson")):
        timeline_md.write_text(
            "# Timeline\n\n*No events yet.*\n", encoding="utf-8"
        )
        print("timeline.md written — no events yet")
        return

    # Canonical files only: no underscore in stem
    canonical_files = sorted(
        f for f in td.glob("*.ndjson") if "_" not in f.stem
    )

    if not canonical_files:
        timeline_md.write_text(
            "# Timeline\n\n*No events yet.*\n", encoding="utf-8"
        )
        print("timeline.md written — no canonical files yet")
        return

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
            conf = ev.get("confidence", "high")
            conf_note = f" *(confidence: {conf})*" if conf not in ("high", "") else ""
            lines.append(f"- {ev['event']}{conf_note}")
        sections.append("\n".join(lines))

    content = "# Timeline\n\n" + "\n\n".join(sections) + "\n"
    timeline_md.write_text(content, encoding="utf-8")
    print(f"timeline.md rebuilt — {len(canonical_files)} date(s), {total_events} event(s)")


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
