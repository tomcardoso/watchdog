#!/usr/bin/env python3
"""
Atomically write all vault artifacts for a single ingested document.

Consumes an extraction JSON blob produced by Claude and handles every vault
write so Claude's per-file work is: read text → output JSON → done.

Usage:
    watchdog-write-vault --extraction /tmp/extraction.json [--vault .]

Extraction JSON schema:
{
  "document": {
    "sha256": str, "filename": str, "original_path": str,
    "title": str, "document_type": str, "date_of_document": str|null,
    "page_count": int, "source": str|null, "obtained": str|null,
    "near_duplicate_of": str|null, "shingles": [],
    "summary": str,
    "key_facts": [{"fact": str, "page": int|null, "confidence": str}]
  },
  "entities": [
    {
      "id": str, "name": str, "type": str, "aliases": [],
      "summary": str|null,
      "analysis": str|null,
      "timeline_events": [
        {
          "date": str,   // YYYY-MM-DD, YYYY-MM, or YYYY
          "event": str,
          "page": int|null,
          "confidence": str
        }
      ],
      "roles": [
        {
          "relationship": str, "target_id": str, "target_type": str,
          "target_name": str, "page": int|null, "confidence": str,
          "date_range": str|null
        }
      ]
    }
  ],
  "morgue_entity_id": str,
  "morgue_document_type": str
}
"""

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


# ── Helpers ───────────────────────────────────────────────────────────────────

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _type_dir(entity_type: str) -> str:
    return entity_type.lower()


def _doc_slug(filename: str) -> str:
    stem = Path(filename).stem
    slug = stem.lower()
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def _frontmatter(data: dict) -> str:
    return "---\n" + yaml.dump(
        data, default_flow_style=False, allow_unicode=True, sort_keys=False
    ) + "---\n"


def _extract_section(content: str, section_name: str) -> str:
    """Return the body of a named ## section, stripped, or empty string."""
    header = f"## {section_name}"
    idx = content.find(header)
    if idx == -1:
        return ""
    start = idx + len(header)
    next_section = content.find("\n## ", start)
    body = content[start:next_section] if next_section != -1 else content[start:]
    return body.strip()


def _extract_notes_section(note_path: Path) -> str:
    """Return the ## Notes section and everything after it, or a default stub."""
    default = "\n## Notes\n\n<!-- Journalist annotations — never overwritten by ingestion. -->\n"
    if not note_path.exists():
        return default
    content = note_path.read_text(encoding="utf-8")
    idx = content.find("## Notes")
    return "\n" + content[idx:] if idx != -1 else default


def _extract_analysis(note_path: Path) -> str:
    """Return the existing ## Analysis body, or empty string."""
    if not note_path.exists():
        return ""
    return _extract_section(note_path.read_text(encoding="utf-8"), "Analysis")


# ── Timeline helpers ──────────────────────────────────────────────────────────

def _date_sort_key(date_str: str) -> str:
    """Pad date string for correct lexicographic chronological sorting."""
    if len(date_str) == 4:    # YYYY
        return date_str + "-00-00"
    if len(date_str) == 7:    # YYYY-MM
        return date_str + "-00"
    return date_str            # YYYY-MM-DD


def _render_date(date_str: str) -> str:
    """Format a date string for human-readable display."""
    try:
        if len(date_str) == 10:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return f"{dt.day} {dt.strftime('%b %Y')}"
        if len(date_str) == 7:
            return datetime.strptime(date_str, "%Y-%m").strftime("%B %Y")
    except ValueError:
        pass
    return date_str  # YYYY or unparseable — return as-is


def _timeline_dedup_key(event: dict) -> str:
    return f"{event.get('date', '')}|{event.get('event', '')[:80].lower()}"


def _merge_timeline_events(existing: list[dict], incoming: list[dict], doc_sha256: str) -> list[dict]:
    """Merge new timeline events into existing list, deduplicating by (date, event text)."""
    existing_keys = {_timeline_dedup_key(e) for e in existing}
    result = list(existing)
    for event in incoming:
        key = _timeline_dedup_key(event)
        if key not in existing_keys:
            result.append({**event, "source_sha256": doc_sha256})
            existing_keys.add(key)
    return result


def _build_timeline_section(events: list[dict], docs_reg: dict, entity_name: str | None = None) -> str:
    """
    Render a ## Timeline section from a list of timeline event dicts.

    If entity_name is None (global timeline), each line includes an entity link.
    Events are sorted chronologically and grouped by year.
    """
    if not events:
        return ""

    sorted_events = sorted(events, key=lambda e: _date_sort_key(e.get("date", "")))

    lines_by_year: dict[str, list[str]] = {}
    for ev in sorted_events:
        date_str = ev.get("date", "")
        year = date_str[:4] if date_str else "Unknown"
        rendered_date = _render_date(date_str)
        conf = f" — confidence: {ev['confidence']}" if ev.get("confidence") else ""

        source_sha = ev.get("source_sha256", "")
        doc_entry = docs_reg.get(source_sha, {})
        doc_note = doc_entry.get("document_note", "")
        doc_title = doc_entry.get("title") or doc_entry.get("filename", "")
        if doc_note and doc_title:
            pg = _page_link(doc_entry.get("morgue_path", ""), ev.get("page"))
            page_part = f", {pg}" if pg else ""
            source_part = f" — *[[{doc_note}|{doc_title}]]{page_part}*"
        else:
            source_part = ""

        if entity_name is not None:
            # Per-entity timeline: no entity link needed
            line = f"- **{rendered_date}** — {ev['event']}{source_part}{conf}"
        else:
            # Global timeline: include entity link
            etype = _type_dir(ev.get("entity_type", ""))
            eid = ev.get("entity_id", "")
            ename = ev.get("entity_name", "")
            entity_link = f"[[entities/{etype}/{eid}|{ename}]]" if etype and eid and ename else ename
            line = f"- **{rendered_date}** — {entity_link} — {ev['event']}{source_part}{conf}"

        lines_by_year.setdefault(year, []).append(line)

    sections = []
    for year in sorted(lines_by_year):
        block = "\n".join(lines_by_year[year])
        sections.append(f"### {year}\n{block}")

    return "\n## Timeline\n\n" + "\n\n".join(sections) + "\n"


def _rebuild_global_timeline(vault_path: Path, entities_reg: dict, docs_reg: dict) -> None:
    """Collect all timeline events from all entities and write timeline.md."""
    all_events: list[dict] = []
    for entry in entities_reg.values():
        for ev in entry.get("timeline_events", []):
            all_events.append({
                **ev,
                "entity_id":   entry["id"],
                "entity_name": entry["name"],
                "entity_type": entry["type"],
            })

    timeline_path = vault_path / "timeline.md"

    if not all_events:
        timeline_path.write_text(
            "# Timeline\n\n*No timeline events yet — events are extracted during document ingest.*\n",
            encoding="utf-8",
        )
        return

    sorted_events = sorted(all_events, key=lambda e: _date_sort_key(e.get("date", "")))

    lines_by_year: dict[str, list[str]] = {}
    for ev in sorted_events:
        date_str = ev.get("date", "")
        year = date_str[:4] if date_str else "Unknown"
        rendered_date = _render_date(date_str)
        conf = f" — confidence: {ev['confidence']}" if ev.get("confidence") else ""

        source_sha = ev.get("source_sha256", "")
        doc_entry = docs_reg.get(source_sha, {})
        doc_note = doc_entry.get("document_note", "")
        doc_title = doc_entry.get("title") or doc_entry.get("filename", "")
        if doc_note and doc_title:
            pg = _page_link(doc_entry.get("morgue_path", ""), ev.get("page"))
            page_part = f", {pg}" if pg else ""
            source_part = f" — *[[{doc_note}|{doc_title}]]{page_part}*"
        else:
            source_part = ""

        etype = _type_dir(ev.get("entity_type", ""))
        eid = ev.get("entity_id", "")
        ename = ev.get("entity_name", "")
        entity_link = f"[[entities/{etype}/{eid}|{ename}]]" if etype and eid and ename else ename

        line = f"- **{rendered_date}** — {entity_link} — {ev['event']}{source_part}{conf}"
        lines_by_year.setdefault(year, []).append(line)

    body = "# Timeline\n\n"
    for year in sorted(lines_by_year):
        body += f"## {year}\n\n" + "\n".join(lines_by_year[year]) + "\n\n"

    timeline_path.write_text(body.rstrip() + "\n", encoding="utf-8")


# ── Relationship helpers ──────────────────────────────────────────────────────

def _page_link(morgue_path: str, page: int | None) -> str:
    """Return a clickable page link if both path and page are known, else plain text."""
    if morgue_path and page:
        return f"[[{morgue_path}#page={page}|p. {page}]]"
    if page:
        return f"p. {page}"
    return ""


def _role_line(role: dict, docs_reg: dict) -> str:
    """Format a role dict as a Markdown relationship line with pretty links."""
    date_part = f" — {role['date_range']}" if role.get("date_range") else ""
    conf_part = f" — confidence: {role['confidence']}"

    target_link = f"[[entities/{_type_dir(role['target_type'])}/{role['target_id']}|{role['target_name']}]]"

    source_sha = role.get("source_sha256", "")
    doc_entry = docs_reg.get(source_sha, {})
    doc_note = doc_entry.get("document_note", "")
    doc_title = doc_entry.get("title") or doc_entry.get("filename", "")

    if doc_note and doc_title:
        pg = _page_link(doc_entry.get("morgue_path", ""), role.get("page"))
        page_part = f", {pg}" if pg else ""
        source_part = f" — via [[{doc_note}|{doc_title}]]{page_part}"
    else:
        pg = _page_link("", role.get("page"))
        source_part = f" — {pg}" if pg else ""

    if role.get("is_reverse"):
        return f"- {target_link} — {role['relationship']}{date_part}{conf_part}{source_part}"
    else:
        return f"- {role['relationship']} {target_link}{date_part}{conf_part}{source_part}"


# ── Entity registry operations ────────────────────────────────────────────────

def _new_entity(entity: dict, doc_sha256: str) -> dict:
    roles = [
        {**r, "source_sha256": doc_sha256, "is_reverse": False}
        for r in entity.get("roles", [])
    ]
    events = [
        {**e, "source_sha256": doc_sha256}
        for e in entity.get("timeline_events", [])
    ]
    return {
        "id":               entity["id"],
        "name":             entity["name"],
        "type":             entity["type"],
        "aliases":          list(entity.get("aliases", [])),
        "appears_in":       [doc_sha256],
        "note_path":        f"entities/{_type_dir(entity['type'])}/{entity['id']}",
        "roles":            roles,
        "timeline_events":  events,
        "date_first_seen":  _today(),
        "date_last_updated": _today(),
    }


def _merge_entity(existing: dict, incoming: dict, doc_sha256: str) -> None:
    """Mutate existing registry entry with data from incoming extraction entity."""
    known = set(existing.get("aliases", []))
    for alias in incoming.get("aliases", []):
        if alias not in known and alias != existing["name"]:
            existing.setdefault("aliases", []).append(alias)
            known.add(alias)

    if doc_sha256 not in existing.get("appears_in", []):
        existing.setdefault("appears_in", []).append(doc_sha256)

    existing_role_keys = {
        (r["relationship"].lower(), r["target_id"])
        for r in existing.get("roles", [])
    }
    for role in incoming.get("roles", []):
        key = (role["relationship"].lower(), role["target_id"])
        if key not in existing_role_keys:
            existing.setdefault("roles", []).append(
                {**role, "source_sha256": doc_sha256, "is_reverse": False}
            )
            existing_role_keys.add(key)

    existing["timeline_events"] = _merge_timeline_events(
        existing.get("timeline_events", []),
        incoming.get("timeline_events", []),
        doc_sha256,
    )

    existing["date_last_updated"] = _today()


def _add_reverse_role(
    entities_reg: dict,
    from_entity: dict,
    role: dict,
    doc_sha256: str,
    modified: set,
) -> None:
    target_id = role.get("target_id")
    if not target_id or target_id not in entities_reg:
        return

    target = entities_reg[target_id]
    reverse_key = (role["relationship"].lower(), from_entity["id"])
    existing_keys = {
        (r["relationship"].lower(), r["target_id"])
        for r in target.get("roles", [])
    }
    if reverse_key in existing_keys:
        return

    target.setdefault("roles", []).append({
        "relationship":  role["relationship"],
        "target_id":     from_entity["id"],
        "target_type":   from_entity["type"],
        "target_name":   from_entity["name"],
        "page":          role.get("page"),
        "confidence":    role["confidence"],
        "date_range":    role.get("date_range"),
        "source_sha256": doc_sha256,
        "is_reverse":    True,
    })
    target["date_last_updated"] = _today()
    modified.add(target_id)


# ── Note builders ─────────────────────────────────────────────────────────────

def build_entity_note(
    entry: dict,
    notes_section: str,
    docs_reg: dict,
    summary: str | None,
    accumulated_analysis: str,
) -> str:
    appears_in_links = []
    for sha in entry.get("appears_in", []):
        doc_entry = docs_reg.get(sha, {})
        note = doc_entry.get("document_note")
        title = doc_entry.get("title") or doc_entry.get("filename", "")
        appears_in_links.append(f"[[{note}|{title}]]" if note and title else sha[:16] + "…")

    fm = _frontmatter({
        "id":               entry["id"],
        "name":             entry["name"],
        "type":             entry["type"],
        "aliases":          entry.get("aliases", []),
        "appears_in":       appears_in_links,
        "date_first_seen":  entry.get("date_first_seen", _today()),
        "date_last_updated": entry.get("date_last_updated", _today()),
    })

    body = f"\n# {entry['name']}\n"

    if summary:
        body += f"\n## Summary\n\n{summary}\n"

    if accumulated_analysis:
        body += f"\n## Analysis\n\n{accumulated_analysis}\n"

    timeline_section = _build_timeline_section(
        entry.get("timeline_events", []), docs_reg, entity_name=entry["name"]
    )
    if timeline_section:
        body += timeline_section

    roles = entry.get("roles", [])
    if roles:
        lines = "\n".join(_role_line(r, docs_reg) for r in roles)
        body += f"\n## Relationships\n\n{lines}\n"

    return fm + body + notes_section


def _build_document_note(doc: dict, entity_entries: list[dict], morgue_path: str | None = None) -> str:
    fm = _frontmatter({
        "title":            doc.get("title", doc["filename"]),
        "type":             "Document",
        "document_type":    doc.get("document_type"),
        "file":             doc["filename"],
        "date_of_document": doc.get("date_of_document"),
        "date_ingested":    _today(),
        "source":           doc.get("source"),
        "obtained":         doc.get("obtained"),
        "entities_mentioned": [
            f"[[entities/{_type_dir(e['type'])}/{e['id']}|{e['name']}]]"
            for e in entity_entries
        ],
        "page_count":       doc.get("page_count"),
        "near_duplicate_of": doc.get("near_duplicate_of"),
    })

    body = ""
    if morgue_path:
        body += f"\n**Source file:** [[{morgue_path}]]\n"

    body += f"\n## Summary\n\n{doc.get('summary', '')}\n"

    key_facts = doc.get("key_facts", [])
    if key_facts:
        body += "\n## Key facts\n\n"
        for kf in key_facts:
            pg = _page_link(morgue_path or "", kf.get("page"))
            page = f" ({pg})" if pg else ""
            conf = f" — confidence: {kf['confidence']}" if kf.get("confidence") else ""
            body += f"- {kf['fact']}{page}{conf}\n"

    if entity_entries:
        body += "\n## Entities mentioned\n\n"
        for e in entity_entries:
            body += f"- [[entities/{_type_dir(e['type'])}/{e['id']}|{e['name']}]]\n"

    body += "\n## Notes\n\n<!-- Reserved for journalist annotations — never overwritten by ingestion. -->\n"

    return fm + body


# ── Main operation ────────────────────────────────────────────────────────────

def run(extraction_path: Path, vault_path: Path) -> None:
    extraction = json.loads(extraction_path.read_text(encoding="utf-8"))
    doc = extraction["document"]
    incoming_entities = extraction.get("entities", [])
    doc_sha256 = doc["sha256"]
    slug = _doc_slug(doc["filename"])
    doc_title = doc.get("title", doc["filename"])

    registry_dir = vault_path / ".watchdog" / "Registry"
    entities_path  = registry_dir / "entities.json"
    documents_path = registry_dir / "documents.json"
    registry_path  = registry_dir / "registry.json"
    log_path       = registry_dir / "ingest.log"

    entities_reg  = json.loads(entities_path.read_text())  if entities_path.exists()  else {}
    documents_reg = json.loads(documents_path.read_text()) if documents_path.exists() else {}

    morgue_relative = (
        f"morgue/{extraction.get('morgue_entity_id', 'unknown')}"
        f"/{extraction.get('morgue_document_type', 'document')}"
        f"/{doc['filename']}"
    )

    # ── 1. Update entity registry ─────────────────────────────────────────────

    modified: set[str] = set()

    for entity in incoming_entities:
        eid = entity["id"]
        if eid in entities_reg:
            _merge_entity(entities_reg[eid], entity, doc_sha256)
        else:
            entities_reg[eid] = _new_entity(entity, doc_sha256)
        modified.add(eid)

    for entity in incoming_entities:
        reg_entry = entities_reg[entity["id"]]
        for role in entity.get("roles", []):
            _add_reverse_role(entities_reg, reg_entry, role, doc_sha256, modified)

    # ── 2. Update document registry ───────────────────────────────────────────

    documents_reg[doc_sha256] = {
        "sha256":           doc_sha256,
        "filename":         doc["filename"],
        "title":            doc_title,
        "original_path":    doc.get("original_path", f"_INCOMING/{doc['filename']}"),
        "document_note":    f"documents/{slug}",
        "ingested_at":      _now_iso(),
        "page_count":       doc.get("page_count"),
        "document_type":    doc.get("document_type"),
        "entities_extracted": [e["id"] for e in incoming_entities],
        "near_duplicate_of": doc.get("near_duplicate_of"),
        "shingles":         doc.get("shingles", []),
        "morgue_path":      morgue_relative,
    }

    # ── 3. Write entity notes ─────────────────────────────────────────────────

    incoming_by_id = {e["id"]: e for e in incoming_entities}

    for eid in modified:
        entry = entities_reg[eid]
        note_path = vault_path / f"{entry['note_path']}.md"
        note_path.parent.mkdir(parents=True, exist_ok=True)

        notes_section = _extract_notes_section(note_path)

        incoming = incoming_by_id.get(eid, {})
        new_summary = incoming.get("summary") or None

        existing_analysis = _extract_analysis(note_path)
        new_analysis_text = incoming.get("analysis") or ""
        if new_analysis_text:
            doc_note = documents_reg[doc_sha256]["document_note"]
            entry_line = f"*{_today()}, via [[{doc_note}|{doc_title}]]:* {new_analysis_text}"
            accumulated = (
                existing_analysis.rstrip() + "\n\n" + entry_line
            ).lstrip() if existing_analysis else entry_line
        else:
            accumulated = existing_analysis

        note_path.write_text(
            build_entity_note(entry, notes_section, documents_reg, new_summary, accumulated),
            encoding="utf-8",
        )

    # ── 4. Write document note ────────────────────────────────────────────────

    doc_note_path = vault_path / "documents" / f"{slug}.md"
    doc_note_path.parent.mkdir(parents=True, exist_ok=True)
    doc_note_path.write_text(
        _build_document_note(doc, [entities_reg[e["id"]] for e in incoming_entities], morgue_relative),
        encoding="utf-8",
    )

    # ── 5. Persist registries ─────────────────────────────────────────────────

    entities_path.write_text(
        json.dumps(entities_reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    documents_path.write_text(
        json.dumps(documents_reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    registry = json.loads(registry_path.read_text()) if registry_path.exists() else {}
    registry.update({
        "last_updated":   _now_iso(),
        "document_count": len(documents_reg),
        "entity_count":   len(entities_reg),
    })
    registry_path.write_text(
        json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(
            f"[{_now_iso()}] INGEST \"{doc['filename']}\" "
            f"sha256={doc_sha256} "
            f"entities={len(incoming_entities)} "
            f"type={doc.get('document_type', 'unknown')}\n"
        )

    # ── 6. Rebuild global timeline ────────────────────────────────────────────

    _rebuild_global_timeline(vault_path, entities_reg, documents_reg)

    # ── 7. Move source file to morgue ─────────────────────────────────────────

    morgue_dir = vault_path / Path(morgue_relative).parent
    morgue_dir.mkdir(parents=True, exist_ok=True)

    source = vault_path / doc.get("original_path", f"_INCOMING/{doc['filename']}")
    if source.exists():
        shutil.move(str(source), str(morgue_dir / source.name))
        sidecar = Path(str(source) + ".yml")
        if sidecar.exists():
            shutil.move(str(sidecar), str(morgue_dir / sidecar.name))

        incoming_dir = vault_path / "_INCOMING"
        parent = source.parent
        while parent != incoming_dir and parent.is_relative_to(incoming_dir):
            try:
                parent.rmdir()
                parent = parent.parent
            except OSError:
                break

    print(
        f"OK  {doc['filename']}  "
        f"entities={len(incoming_entities)}  "
        f"doc=documents/{slug}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write all vault artifacts for an ingested document"
    )
    parser.add_argument("--extraction", required=True, help="Path to extraction JSON")
    parser.add_argument("--vault", default=".", help="Vault root directory (default: .)")
    args = parser.parse_args()

    extraction_path = Path(args.extraction)
    vault_path = Path(args.vault).resolve()

    if not extraction_path.exists():
        sys.exit(f"Error: {extraction_path} not found")
    if not vault_path.exists():
        sys.exit(f"Error: vault directory {vault_path} not found")

    run(extraction_path, vault_path)


if __name__ == "__main__":
    main()
