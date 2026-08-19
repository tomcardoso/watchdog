"""
Watchdog timeline tools.

timeline-collisions  Promote pending raw files to canonical; return collision JSON.
rebuild-timeline     Read all canonical {date}.ndjson files; render timeline.md.

File naming convention in .watchdog/timeline/:
  {date}.ndjson             — canonical (orchestrator-maintained, deduplicated)
  {date}_{sha256[:7]}.ndjson — raw per-document output; always written by the extraction task

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
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _stage_dedup_key(date: str, event: str) -> str:
    """Match write_vault's dedup convention: date + full event text (not a prefix — a
    truncated key can collide on a long fact's shared opening while its divergent, material
    part lands past the cutoff)."""
    return f"{date}|{event.lower()}"


def stage_timeline_events(vault: Path, extraction: dict) -> int:
    """Write raw per-date NDJSON timeline files from an extraction blob.

    Reads the dated facts on ``document.key_facts`` (#140): every key_fact carrying a ``date`` is a
    timeline event, with its ``entities`` tags supplying the contributing entity ids and its
    ``page`` the source page. Attaches the document sha, deduplicates within the document by (date,
    event text) while unioning entity ids, groups by date, and writes one raw
    ``.watchdog/timeline/{date}_{sha[:7]}.ndjson`` file per date. The ``timeline-collisions`` /
    ``rebuild-timeline`` flow consumes these; the unified renderer resolves ``source_sha256`` /
    ``page`` / ``entity_ids`` into document and entity attribution (#237).

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
                "page": fact.get("page"),
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

    For dates with only raw files: merge → write canonical, then delete the raws (they are
    consumed, not retained — otherwise the next ingest re-reports the date as a collision
    against its own already-promoted raws). For dates that already had a canonical: return
    ``{date, canonical, raw}`` for semantic dedup by the caller, which deletes those raws only
    after a *successful* dedup write.
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
            # Consume the raws once merged so a later ingest doesn't re-litigate this date
            # as a phantom collision (canonical vs. its own already-promoted raws).
            for rf in raw_files:
                rf.unlink()
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


def _valid_index(i, n: int) -> bool:
    return isinstance(i, int) and not isinstance(i, bool) and 0 <= i < n


def month_precision_groups(vault: Path) -> list[dict]:
    """Find months holding both a month-precision (``YYYY-MM``) event and day-precision
    (``YYYY-MM-DD``) events — the only shape where a coarse date could restate a precise one
    (#239, D63). Date-keyed bucketing never hands these two precisions to the same dedup call, so a
    coarse restatement and the specific day it refines survive as separate lines with un-unioned
    attribution. Returns one group per such month: ``{"month", "coarse": [...], "precise": [...]}``,
    events read from the canonical NDJSON only. Year-precision (``YYYY``) events are deliberately
    excluded — a bare year spans too many days to match a specific occurrence safely."""
    td = _timeline_dir(vault)
    if not td.exists():
        return []
    by_month: dict[str, dict[str, list]] = {}
    for cf in sorted(f for f in td.glob("*.ndjson") if "_" not in f.stem):
        date = cf.stem
        if len(date) == 7:      # YYYY-MM
            bucket, month = "coarse", date
        elif len(date) == 10:   # YYYY-MM-DD
            bucket, month = "precise", date[:7]
        else:
            continue            # YYYY or unparseable — no month-level reconciliation
        recs = []
        for line in _read_ndjson_lines(cf):
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        by_month.setdefault(month, {"coarse": [], "precise": []})[bucket].extend(recs)
    return [
        {"month": m, "coarse": g["coarse"], "precise": g["precise"]}
        for m, g in sorted(by_month.items())
        if g["coarse"] and g["precise"]
    ]


def apply_precision_matches(vault: Path, group: dict, matches: list[dict]) -> int:
    """Fold a month's matched coarse events into the specific day each one restates (#239, D63).

    Each ``{"coarse": i, "precise": j}`` unions ``coarse[i]``'s ``entity_ids`` onto ``precise[j]``
    and drops ``coarse[i]``. Rewrites the affected canonical NDJSON — the month file with its
    survivors (deleted when it empties), each touched day file with the grown attribution. Ignores
    out-of-range or repeat coarse indices (each coarse event folds at most once). A precise event is
    never dropped and two precise events never collapse into each other — the pass can only ever
    remove a coarse restatement. Returns the number of coarse events folded."""
    coarse, precise = group["coarse"], group["precise"]
    nc, npr = len(coarse), len(precise)
    folded: set[int] = set()
    for m in matches:
        if not isinstance(m, dict):
            continue
        ci, pj = m.get("coarse"), m.get("precise")
        if not (_valid_index(ci, nc) and _valid_index(pj, npr)) or ci in folded:
            continue
        eids = precise[pj].get("entity_ids") or []
        for eid in coarse[ci].get("entity_ids", []):
            if eid not in eids:
                eids.append(eid)
        precise[pj]["entity_ids"] = eids
        folded.add(ci)

    if not folded:
        return 0

    td = _timeline_dir(vault)
    coarse_path = td / f"{group['month']}.ndjson"
    survivors = [c for i, c in enumerate(coarse) if i not in folded]
    if survivors:
        coarse_path.write_text(
            "\n".join(json.dumps(c, ensure_ascii=False) for c in survivors) + "\n", encoding="utf-8")
    else:
        coarse_path.unlink(missing_ok=True)

    by_day: dict[str, list[dict]] = {}
    for p in precise:
        by_day.setdefault(p.get("date", ""), []).append(p)
    for day, recs in by_day.items():
        (td / f"{day}.ndjson").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8")
    return len(folded)


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


def _load_registry(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _render_event_line(ev: dict, docs_reg: dict, manifest: dict) -> str:
    """Render one canonical NDJSON record as a timeline bullet with entity and
    document attribution — the same shape write_vault's entity-note timeline uses,
    extended to link *every* entity a cross-document-deduped event concerns (#237)."""
    from watchdog.pipeline.write_vault import _render_date, _page_link

    rendered_date = _render_date(ev.get("date", ""))
    basis_note = " *(inferred)*" if ev.get("basis") == "inferred" else ""

    # Entity links: resolve each tagged id against the manifest (id → name, note_path).
    # An id missing from the manifest (e.g. a stale record) falls back to bare text.
    links: list[str] = []
    for eid in ev.get("entity_ids", []):
        entry = manifest.get(eid)
        if entry and entry.get("note_path") and entry.get("name"):
            links.append(f"[[{entry['note_path']}|{entry['name']}]]")
        elif eid:
            links.append(eid)
    entity_part = f" — {', '.join(links)}" if links else ""

    doc_entry = docs_reg.get(ev.get("source_sha256", ""), {})
    doc_note = doc_entry.get("document_note", "")
    doc_title = doc_entry.get("title") or doc_entry.get("filename", "")
    if doc_note and doc_title:
        pg = _page_link(doc_entry.get("morgue_path", ""), ev.get("page"))
        page_part = f", {pg}" if pg else ""
        source_part = f" — *[[{doc_note}|{doc_title}]]{page_part}*"
    else:
        source_part = ""

    return f"- **{rendered_date}**{entity_part} — {ev.get('event', '')}{source_part}{basis_note}"


def cmd_rebuild_timeline(vault: Path, quiet: bool = False) -> tuple[int, int]:
    """Read all canonical {date}.ndjson files and render timeline.md.

    The single global-timeline renderer (#237): reads the cross-document-deduped canonical
    NDJSON and resolves each record's ``source_sha256`` / ``page`` / ``entity_ids`` into
    document links and entity links, grouped by year. Every command that touches the vault
    (``ingest``, ``merge-entities``, ``write-entity``, standalone ``watchdog timeline``)
    renders through here, so ``timeline.md``'s shape no longer depends on which ran last.

    Returns (date_count, event_count) so callers can report progress.
    """
    from watchdog.pipeline.write_vault import _date_sort_key

    td = _timeline_dir(vault)
    registry_dir = vault / ".watchdog" / "registry"
    docs_reg = _load_registry(registry_dir / "documents.json")
    manifest = _load_registry(registry_dir / "manifest.json")

    # Canonical files only: no underscore in stem
    canonical_files = sorted(
        f for f in td.glob("*.ndjson") if "_" not in f.stem
    ) if td.exists() else []

    events: list[dict] = []
    for cf in canonical_files:
        for line in _read_ndjson_lines(cf):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not events:
        _write_timeline_md(vault, _TIMELINE_HEADER + "*No events yet.*\n")
        if not quiet:
            print("timeline.md written — no events yet")
        return (0, 0)

    events.sort(key=lambda e: _date_sort_key(e.get("date", "")))
    lines_by_year: dict[str, list[str]] = {}
    for ev in events:
        date_str = ev.get("date", "")
        year = date_str[:4] if date_str else "Unknown"
        lines_by_year.setdefault(year, []).append(_render_event_line(ev, docs_reg, manifest))

    sections = [f"## {year}\n" + "\n".join(lines_by_year[year]) for year in sorted(lines_by_year)]
    content = _TIMELINE_HEADER + "\n\n".join(sections) + "\n"
    _write_timeline_md(vault, content)
    if not quiet:
        print(f"timeline.md rebuilt — {len(canonical_files)} date(s), {len(events)} event(s)")
    return (len(canonical_files), len(events))


def main_collisions() -> None:
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")
    cmd_timeline_collisions(vault)


def main_rebuild() -> None:
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: not inside a watchdog project. Run `watchdog timeline <name>` or cd into a project first.")
    cmd_rebuild_timeline(vault)
