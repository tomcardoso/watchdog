#!/usr/bin/env python3
"""
Atomically write all vault artifacts for a single ingested document.

Consumes an extraction JSON blob produced by Claude and handles every vault
write so Claude's per-file work is: read text → output JSON → done.

Usage:
    watchdog-write-vault --extraction .watchdog/tmp/extraction.json [--vault .]

Extraction JSON schema (as consumed here — i.e. AFTER postflight.explode_key_facts has fanned the
unified `key_facts` out into per-entity `evidence_fragments` / `timeline_events`, #140). The model
itself emits only `key_facts` (with `date` / `entities` tags) plus the entity graph; it no longer
emits per-entity summaries, fragments, or timeline events:
{
  "document": {
    "sha256": str, "filename": str, "original_path": str,
    "title": str, "document_type": str, "date_of_document": str|null,
    "page_count": int, "source": str|null, "obtained": str|null,
    "near_duplicate_of": str|null, "shingles": [],
    "summary": str,
    "key_facts": [{"fact": str, "page": int|null, "basis": "stated"|"inferred",
                   "date": str|null, "entities": [str], "quote": str|null}]
  },
  "entities": [
    {
      "id": str, "name": str, "type": str, "aliases": [],
      // summary is no longer emitted by the model — synthesized post-ingest, with a provisional
      // one-liner from the entity's top tagged fact in the meantime.
      "evidence_fragments": [          // reconstructed by postflight from facts tagged to this id
        {"claim": str, "page": int|null, "basis": "stated"|"inferred", "quote": str|null}
      ],
      "contradictions": [str]|null,    // each a `> [!contradiction]` callout block
      "timeline_events": [             // reconstructed by postflight from this id's dated facts
        {
          "date": str,   // YYYY-MM-DD, YYYY-MM, or YYYY
          "event": str,
          "page": int|null,
          "basis": "stated"|"inferred"
        }
      ],
      "roles": [
        {
          "relationship": str, "target_id": str,   // target_name/target_type resolved from id
          "page": int|null, "basis": "stated"|"inferred",
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
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import yaml

from watchdog.pipeline.entity_norm import normalize_entity_name
from watchdog.pipeline.entity_type import canonical_type

try:
    from fcntl import flock as _flock, LOCK_EX as _LOCK_EX, LOCK_UN as _LOCK_UN
    _HAS_FLOCK = True
except ImportError:
    _HAS_FLOCK = False  # Windows

try:
    import msvcrt as _msvcrt  # Windows-only stdlib module
except ImportError:
    _msvcrt = None  # macOS/Linux


# ── Helpers ───────────────────────────────────────────────────────────────────

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(text: str) -> str:
    """Convert arbitrary text to a URL-safe kebab-case slug."""
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


def _type_dir(entity_type: str) -> str:
    """Directory name for an entity type. Entity ``id`` is slugified upstream in postflight
    (#303), but ``type`` is not — it stays a display value (e.g. "Person"), so this is the
    one place it becomes a path segment; route it through the full ``slugify`` (not just
    ``.lower()``) so a hostile value (e.g. containing ``../``) can't traverse out of the
    entities directory."""
    return slugify(entity_type) or "entity"


def _doc_slug(filename: str) -> str:
    return slugify(Path(filename).stem) or "document"


def _defang(text: str) -> str:
    """Defang ``[[``/``]]`` in model-supplied name/title text before it is interpolated into
    wikilink display text or a heading (#305) — otherwise a hostile value can close a wikilink
    early and forge a second one pointing elsewhere in the vault."""
    from watchdog.pipeline.research import neutralize
    return neutralize(text or "")


def _assert_in_vault(path: Path, vault_path: Path, label: str) -> Path:
    """Refuse to write outside the vault (#303) — the resolve-based backstop behind entity
    id/type slugification, in case a malicious value slips past it. Matches the existing
    ``--extraction``/``--neardup-file`` containment guards in ``main()``, below."""
    resolved = path.resolve()
    if not resolved.is_relative_to(vault_path.resolve()):
        sys.exit(f"Error: refusing to write outside the vault ({label}): {resolved}")
    return path


def _reconcile_entity_ids(incoming_entities: list[dict], entities_reg: dict) -> None:
    """
    Remap incoming entities that name an existing entity under a different slug.

    Documents extract in parallel from a pre-flight snapshot taken at launch, so two
    documents referencing the same real-world entity can coin different ids (e.g.
    'ernst-and-young-inc' vs 'ernst-young-inc'). write_vault runs inside the registry
    lock with a fresh read of entities_reg — the one place that sees entities written
    by concurrent extraction tasks earlier in the batch — so we reconcile here: any incoming
    *new* entity whose normalized (name, type) matches an existing one is remapped to
    that existing id, routing it through the merge path instead of creating a duplicate.

    The type half of the key is canonicalized (#335) so a real-world entity labelled with
    drifting near-synonyms across documents (``company`` vs ``financialinstitution``) still
    reconciles instead of forking into a second folder — see entity_type.canonical_type.
    """
    norm_index: dict[tuple[str, str], str] = {}
    for eid, entry in entities_reg.items():
        for n in [entry["name"], *entry.get("aliases", [])]:
            norm_index.setdefault((normalize_entity_name(n), canonical_type(entry["type"])), eid)

    remap: dict[str, str] = {}
    for entity in incoming_entities:
        if entity["id"] in entities_reg:
            continue
        key = (normalize_entity_name(entity["name"]), canonical_type(entity["type"]))
        existing_id = norm_index.get(key)
        if existing_id and existing_id != entity["id"]:
            remap[entity["id"]] = existing_id
            entity["id"] = existing_id
            # Preserve the variant spelling so the entity stays findable next time.
            entity.setdefault("aliases", []).append(entity["name"])

    # Keep intra-document role targets pointing at the reconciled ids.
    if remap:
        for entity in incoming_entities:
            for role in entity.get("roles", []):
                if role.get("target_id") in remap:
                    role["target_id"] = remap[role["target_id"]]


def _resolve_role_targets(incoming_entities: list[dict], entities_reg: dict) -> None:
    """Re-inflate the role fields the extractor no longer emits.

    Extraction identifies each role's target by ``target_id`` only; ``target_name`` and
    ``target_type`` are derivable from the target entity (this batch's entities first, then
    the registry). Filling them in here — after id reconciliation, before anything reads
    roles — keeps the slim extraction wire format while leaving every downstream consumer
    (note rendering, pre-flight context, the synthesis digest) unchanged. A dangling target
    falls back to the id as name and ``Unknown`` as type.
    """
    lookup: dict[str, tuple] = {e["id"]: (e.get("name"), e.get("type")) for e in incoming_entities}
    for eid, entry in entities_reg.items():
        lookup.setdefault(eid, (entry.get("name"), entry.get("type")))
    for entity in incoming_entities:
        for role in entity.get("roles", []):
            tid = role.get("target_id")
            name, typ = lookup.get(tid, (None, None))
            if not role.get("target_name"):
                role["target_name"] = name or tid or ""
            if not role.get("target_type"):
                role["target_type"] = typ or "Unknown"


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


def _extract_summary(note_path: Path) -> str | None:
    """Return the existing ## Summary body, or None if absent."""
    if not note_path.exists():
        return None
    text = _extract_section(note_path.read_text(encoding="utf-8"), "Summary")
    return text or None


def _extract_contradictions(note_path: Path) -> str:
    """Return the existing ## Contradictions body, or empty string."""
    if not note_path.exists():
        return ""
    return _extract_section(note_path.read_text(encoding="utf-8"), "Contradictions")


# Header a document contributes to an entity note's ## Analysis section, e.g.
# ``*3 May 2026, via [[documents/acme-annual-report|Acme Annual Report]]:*``. The doc-note
# link is stable per document (1:1 with its sha via the slug), so it keys the block for
# replace-not-append rewrites (#259).
_ANALYSIS_HEADER_RE = re.compile(r"^\*[^\n]*?via \[\[([^\]|]+)[|\]]", re.MULTILINE)


def _drop_analysis_entry(analysis: str, doc_note: str) -> str:
    """Remove any prior ## Analysis block this document contributed, so re-running write_vault
    for the same document replaces its entry instead of appending a duplicate (#259). Blocks are
    delimited by their ``*…via [[doc_note|…]]:*`` header; everything from one header up to the
    next is one document's contribution."""
    if not analysis:
        return analysis
    matches = list(_ANALYSIS_HEADER_RE.finditer(analysis))
    if not matches:
        return analysis
    kept = analysis[: matches[0].start()]  # preamble before the first header (normally empty)
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(analysis)
        if m.group(1) != doc_note:
            kept += analysis[m.start():end]
    return kept.strip()


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
    # Full text, not a prefix — a truncated key can collide on a long fact's shared opening
    # clause (e.g. "On March 3, 2019, Acme Corp transferred...") while the divergent, material
    # part of the sentence lands past the cutoff, silently losing the second event.
    return f"{event.get('date', '')}|{event.get('event', '').lower()}"


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


def _build_timeline_section(events: list[dict], docs_reg: dict) -> str:
    """Render an entity note's ``## Timeline`` section from its dated tagged facts, year-grouped
    and attributed to each event's source document. The *global* timeline is rendered separately
    from the cross-document-deduped NDJSON (`timeline.cmd_rebuild_timeline`, #237)."""
    if not events:
        return ""

    sorted_events = sorted(events, key=lambda e: _date_sort_key(e.get("date", "")))

    lines_by_year: dict[str, list[str]] = {}
    for ev in sorted_events:
        date_str = ev.get("date", "")
        year = date_str[:4] if date_str else "Unknown"
        rendered_date = _render_date(date_str)
        basis_note = " *(inferred)*" if ev.get("basis") == "inferred" else ""

        doc_entry = docs_reg.get(ev.get("source_sha256", ""), {})
        doc_note = doc_entry.get("document_note", "")
        doc_title = doc_entry.get("title") or doc_entry.get("filename", "")
        if doc_note and doc_title:
            pg = _page_link(doc_entry.get("morgue_path", ""), ev.get("page"))
            page_part = f", {pg}" if pg else ""
            source_part = f" — *[[{doc_note}|{_defang(doc_title)}]]{page_part}*"
        else:
            source_part = ""

        line = f"- **{rendered_date}** — {ev['event']}{source_part}{basis_note}"
        lines_by_year.setdefault(year, []).append(line)

    sections = [f"### {year}\n" + "\n".join(lines_by_year[year]) for year in sorted(lines_by_year)]
    return "\n## Timeline\n\n" + "\n\n".join(sections) + "\n"


@contextmanager
def _registry_lock(registry_dir: Path):
    """Exclusive per-vault lock so concurrent write-vault calls serialize safely.

    Uses `fcntl.flock` on macOS/Linux (blocks indefinitely until acquired) and
    `msvcrt.locking` on Windows (locks a 1-byte region; blocks in ~1s retries, raising
    OSError after ~10s of contention rather than waiting indefinitely — a real
    behavioural difference from flock, not just a different API). If neither is
    available, this is a no-op and callers rely on in-process serialization only
    (D18) — cross-process writers are not locked out."""
    lock_path = registry_dir / ".write-lock"
    with open(lock_path, "w") as fh:
        if _HAS_FLOCK:
            _flock(fh, _LOCK_EX)
        elif _msvcrt is not None:
            fh.write(" ")
            fh.flush()
            fh.seek(0)
            _msvcrt.locking(fh.fileno(), _msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if _HAS_FLOCK:
                _flock(fh, _LOCK_UN)
            elif _msvcrt is not None:
                fh.seek(0)
                _msvcrt.locking(fh.fileno(), _msvcrt.LK_UNLCK, 1)


def _update_manifest(vault_path: Path, entities_reg: dict) -> None:
    """Write a lightweight lookup index: id → name, type, aliases, note_path only."""
    manifest = {
        eid: {
            "name":      entry["name"],
            "type":      entry["type"],
            "aliases":   entry.get("aliases", []),
            "note_path": entry["note_path"],
        }
        for eid, entry in entities_reg.items()
    }
    manifest_path = vault_path / ".watchdog" / "Registry" / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


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
    basis_part = " *(inferred)*" if role.get("basis") == "inferred" else ""

    # target_id sits in the wikilink *target* position. It's postflight-slugified for a profiled
    # target, but a role can point at an unprofiled entity (leads.py: "named but never profiled"),
    # whose target_id never passed through that pass — slugify here so a hostile dangling id can't
    # close the wikilink early and forge a second one (#305, target side).
    target_link = f"[[entities/{_type_dir(role['target_type'])}/{slugify(role['target_id'])}|{_defang(role['target_name'])}]]"

    source_sha = role.get("source_sha256", "")
    doc_entry = docs_reg.get(source_sha, {})
    doc_note = doc_entry.get("document_note", "")
    doc_title = doc_entry.get("title") or doc_entry.get("filename", "")

    if doc_note and doc_title:
        pg = _page_link(doc_entry.get("morgue_path", ""), role.get("page"))
        page_part = f", {pg}" if pg else ""
        source_part = f" — via [[{doc_note}|{_defang(doc_title)}]]{page_part}"
    else:
        pg = _page_link("", role.get("page"))
        source_part = f" — {pg}" if pg else ""

    if role.get("is_reverse"):
        return f"- {target_link} — {role['relationship']}{date_part}{basis_part}{source_part}"
    else:
        return f"- {role['relationship']} {target_link}{date_part}{basis_part}{source_part}"


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
        "contradictions":   [c.strip() for c in entity.get("contradictions") or [] if c.strip()],
        "date_first_seen":  _today(),
        "date_last_updated": _today(),
    }


def _merge_entity(existing: dict, incoming: dict, doc_sha256: str) -> None:
    """Mutate existing registry entry with data from incoming extraction entity."""
    known_lower = {a.lower() for a in existing.get("aliases", [])}
    for alias in incoming.get("aliases", []):
        if alias.lower() not in known_lower and alias.lower() != existing["name"].lower():
            existing.setdefault("aliases", []).append(alias)
            known_lower.add(alias.lower())

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

    existing_contradictions = existing.setdefault("contradictions", [])
    for callout in incoming.get("contradictions") or []:
        callout = callout.strip()
        if callout and callout not in existing_contradictions:
            existing_contradictions.append(callout)

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
        "basis":         role.get("basis", "stated"),
        "date_range":    role.get("date_range"),
        "source_sha256": doc_sha256,
        "is_reverse":    True,
    })
    target["date_last_updated"] = _today()
    modified.add(target_id)


# ── Note builders ─────────────────────────────────────────────────────────────

def _write_morgue_markdown(vault_path: Path, sha256: str, morgue_dir: Path, stem: str) -> None:
    """Write the Docling per-page markdown next to the original in the morgue (#140).

    Best-effort: the page markdown lives in the chew-time queue descriptor, which is present during
    ingest but gone on a re-run from disk (`watchdog finalize`); skip silently if unavailable.
    Pages are joined with `<!-- PAGE N -->` markers so the file is both greppable and page-aligned.
    """
    queue_file = vault_path / ".watchdog" / "queue" / f"{sha256}.json"
    if not queue_file.exists():
        return
    try:
        pages = json.loads(queue_file.read_text(encoding="utf-8")).get("pages", [])
    except (OSError, json.JSONDecodeError):
        return
    if not pages:
        return
    body = "\n\n".join(
        f"<!-- PAGE {p.get('page')} -->\n\n{p.get('markdown', '')}".rstrip() for p in pages
    )
    try:
        (morgue_dir / f"{stem}.md").write_text(body + "\n", encoding="utf-8")
    except OSError:
        pass


def _index_corpus_passages(vault_path: Path, doc: dict, entity_entries: list[dict],
                            morgue_path: str = "") -> None:
    """Embed this document's source passages into the semantic index, with a contextual
    prefix built from what extraction produced — the document's title, type, and the
    entities it names. The prefix anchors a passage that lacks the document's who/what,
    improving retrieval (Anthropic contextual-retrieval). Also indexes the same raw pages
    into the full-text (exact-term) index (#109) — the two lanes are built from the same
    page text but serve different recall needs. Best-effort: the page text lives in the
    chew-time queue descriptor, gone on a finalize re-run from disk; skip if absent.
    """
    sha256 = doc["sha256"]
    queue_file = vault_path / ".watchdog" / "queue" / f"{sha256}.json"
    if not queue_file.exists():
        return
    pages = json.loads(queue_file.read_text(encoding="utf-8")).get("pages", [])
    if not pages:
        return
    names = [e.get("name", "") for e in entity_entries if e.get("name")][:20]
    title = doc.get("title") or doc.get("filename", "")
    dtype = doc.get("document_type") or "document"
    context = f"{title} — {dtype}."
    if names:
        context += " Mentions: " + ", ".join(names) + "."
    from watchdog.pipeline.embed import add_document
    add_document(vault_path, doc["filename"], pages, context=context)
    from watchdog.pipeline.fulltext import add_document as fts_add_document
    fts_add_document(vault_path, doc["filename"], sha256, pages, morgue_path=morgue_path)


def _quote_verification_note(f: dict) -> str:
    """Suffix for a rendered quote, from the deterministic post-flight check (#267).

    ``quote_verified is False`` means the quote couldn't be matched on or near its cited
    page; ``quote_found_page`` means it was only found (via a normalized match) on a
    different page than cited. Neither key present means either verification wasn't run
    (e.g. no page text available) or the quote matched exactly — nothing to flag.
    """
    if f.get("quote_verified") is False:
        return " *(quote not found on cited page — verify against source)*"
    found_page = f.get("quote_found_page")
    if found_page is not None:
        return f" *(found on p. {found_page}, not the cited page)*"
    return ""


def _render_evidence_fragments(fragments: list, morgue_path: str = "") -> str:
    """Render evidence-fragment claims as Markdown bullets.

    Each claim becomes a bullet with an optional page link and `— reason`; an optional
    verbatim quote renders as a blockquote beneath it. With no morgue_path, pages render
    as plain "p. N" (used for the finalizer digest, which needs no clickable links).
    """
    lines = []
    for f in fragments:
        claim = (f.get("claim") or "").strip()
        if not claim:
            continue
        pg = _page_link(morgue_path, f.get("page"))
        page = f" ({pg})" if pg else ""
        reason = f" — {f['reason'].strip()}" if f.get("reason") else ""
        basis_note = " *(inferred)*" if f.get("basis") == "inferred" else ""
        line = f"- {claim}{page}{reason}{basis_note}"
        quote = (f.get("quote") or "").strip()
        if quote:
            line += f"\n  > {quote}{_quote_verification_note(f)}"
        lines.append(line)
    return "\n".join(lines)


def build_entity_note(
    entry: dict,
    notes_section: str,
    docs_reg: dict,
    summary: str | None,
    accumulated_analysis: str,
    contradictions: str = "",
) -> str:
    appears_in_links = []
    for sha in entry.get("appears_in", []):
        doc_entry = docs_reg.get(sha, {})
        note = doc_entry.get("document_note")
        title = doc_entry.get("title") or doc_entry.get("filename", "")
        appears_in_links.append(f"[[{note}|{_defang(title)}]]" if note and title else sha[:16] + "…")

    fm = _frontmatter({
        "id":               entry["id"],
        "name":             entry["name"],
        "type":             entry["type"],
        "aliases":          entry.get("aliases", []),
        "appears_in":       appears_in_links,
        "date_first_seen":  entry.get("date_first_seen", _today()),
        "date_last_updated": entry.get("date_last_updated", _today()),
    })

    body = f"\n# {_defang(entry['name'])}\n"

    if summary:
        body += f"\n## Summary\n\n{summary}\n"

    if accumulated_analysis:
        body += f"\n## Analysis\n\n{accumulated_analysis}\n"

    if contradictions:
        body += f"\n## Contradictions\n\n{contradictions}\n"

    timeline_section = _build_timeline_section(entry.get("timeline_events", []), docs_reg)
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
            f"[[entities/{_type_dir(e['type'])}/{e['id']}|{_defang(e['name'])}]]"
            for e in entity_entries
        ],
        "page_count":       doc.get("page_count"),
        "near_duplicate_of": doc.get("near_duplicate_of"),
        "record_skill":     doc.get("record_skill"),
        "record_skill_hash": doc.get("record_skill_hash"),
        "extract_model":    doc.get("extract_model"),
        "extract_effort":   doc.get("extract_effort"),
    })

    body = ""
    if morgue_path:
        body += f"\n**Source file:** [[{morgue_path}]]\n"
        md_path = str(Path(morgue_path).with_suffix(".md"))
        body += f"\n**Full text:** [[{md_path}]]\n"

    body += f"\n## Summary\n\n{doc.get('summary', '')}\n"

    key_facts = doc.get("key_facts", [])
    if key_facts:
        body += "\n## Key facts\n\n"
        for kf in key_facts:
            pg = _page_link(morgue_path or "", kf.get("page"))
            page = f" ({pg})" if pg else ""
            basis_note = " *(inferred)*" if kf.get("basis") == "inferred" else ""
            body += f"- {kf['fact']}{page}{basis_note}\n"
            quote = (kf.get("quote") or "").strip()
            if quote:
                body += f"  > {quote}{_quote_verification_note(kf)}\n"

    if entity_entries:
        body += "\n## Entities mentioned\n\n"
        for e in entity_entries:
            body += f"- [[entities/{_type_dir(e['type'])}/{e['id']}|{_defang(e['name'])}]]\n"

    body += "\n## Notes\n\n<!-- Reserved for journalist annotations — never overwritten by ingestion. -->\n"

    return fm + body


# ── Entity fragments (post-ingest finalizer input) ───────────────────────────

def _fragments_dir(vault_path: Path) -> Path:
    return vault_path / ".watchdog" / "tmp" / "entity-fragments"


# Header of one document's block in an entity's fragment file, e.g.
# ``### Acme Annual Report — annual-report, 2024-12-31 (sha 1a2b3c4)``. The 7-hex sha keys the
# block for replace-not-append rewrites (#259).
_FRAG_BLOCK_RE = re.compile(r"^### .*\(sha ([0-9a-f]{7})\)", re.MULTILINE)


def _drop_fragment_block(text: str, sha256: str) -> str:
    """Remove any prior fragment block this document (``sha256``) contributed, so re-running
    write_vault replaces its block instead of appending a duplicate (#259)."""
    if not text:
        return text
    matches = list(_FRAG_BLOCK_RE.finditer(text))
    if not matches:
        return text
    short = sha256[:7]
    kept = text[: matches[0].start()]  # preamble before the first block (leading newline)
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        if m.group(1) != short:
            kept += text[m.start():end]
    return kept


def _record_entity_fragment(
    vault_path: Path, eid: str, entry: dict, incoming: dict, doc: dict, doc_title: str
) -> None:
    """Record this document's view of an entity in its fragment file and mark the entity in the
    finalizer queue. Runs after the registries persist, and is idempotent per document: a re-run
    (repair retry) replaces this document's block rather than appending a second copy (#259).

    The fragments are the digest the post-ingest finalizer synthesizes from — it never
    re-reads the source document.
    """
    frag_dir = _fragments_dir(vault_path)
    frag_dir.mkdir(parents=True, exist_ok=True)
    sha256 = doc["sha256"]

    dtype = doc.get("document_type") or "document"
    ddate = doc.get("date_of_document") or "undated"
    parts = [f"\n### {_defang(doc_title)} — {dtype}, {ddate} (sha {sha256[:7]})\n"]
    if incoming.get("summary"):
        parts.append(incoming["summary"].strip() + "\n")
    fragments = incoming.get("evidence_fragments") or []
    if fragments:
        parts.append("\nClaims:\n" + _render_evidence_fragments(fragments) + "\n")
    roles = incoming.get("roles", [])
    if roles:
        rendered = "; ".join(
            f"{r.get('relationship', '')} {r.get('target_name', '')}".strip() for r in roles
        )
        parts.append(f"\nRoles: {rendered}\n")

    frag_path = frag_dir / f"{eid}.md"
    existing = frag_path.read_text(encoding="utf-8") if frag_path.exists() else ""
    frag_path.write_text(_drop_fragment_block(existing, sha256) + "".join(parts), encoding="utf-8")

    # Mark the entity as touched this run. The queue keys on the set of contributing shas so a
    # repair retry cannot inflate the count; the count is now only a touched-set marker (post-D26
    # the synthesis gate reads registry appears_in, not this).
    queue_path = frag_dir / "_queue.json"
    queue = json.loads(queue_path.read_text(encoding="utf-8")) if queue_path.exists() else {}
    rec = queue.get(eid, {})
    shas = set(rec.get("shas", []))
    shas.add(sha256)
    rec.update({"name": entry["name"], "note_path": entry["note_path"],
                "shas": sorted(shas), "count": len(shas)})
    queue[eid] = rec
    queue_path.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Main operation ────────────────────────────────────────────────────────────

def run(extraction_path: Path, vault_path: Path, neardup_file: Path | None = None, neardup_data: dict | None = None, quiet: bool = False) -> None:
    extraction = json.loads(extraction_path.read_text(encoding="utf-8"))
    doc = extraction.get("document")
    if not doc:
        sys.exit(f"Error: extraction JSON missing required 'document' key ({extraction_path.name})")
    incoming_entities = extraction.get("entities", [])
    # Collapse each entity's model-invented type onto the closed vocabulary (#335) before it
    # becomes load-bearing — the stored registry type, the `entities/<type>/` folder, the note
    # frontmatter, and the graph colour group all follow from this single canonical value.
    for _entity in incoming_entities:
        _entity["type"] = canonical_type(_entity.get("type", ""))
    doc_sha256 = doc["sha256"]
    slug = _doc_slug(doc["filename"])
    doc_title = doc.get("title", doc["filename"])

    registry_dir = vault_path / ".watchdog" / "Registry"
    registry_dir.mkdir(parents=True, exist_ok=True)

    with _registry_lock(registry_dir):
        # Detect slug collision: if a note with this slug already exists for a different
        # file, append a short SHA prefix to disambiguate.
        _candidate = vault_path / "documents" / f"{slug}.md"
        if _candidate.exists():
            try:
                _head = _candidate.read_text(encoding="utf-8", errors="replace")
                if f"file: {doc['filename']}" not in _head:
                    slug = f"{slug}-{doc_sha256[:6]}"
                    if not quiet:
                        print(f"WARN  slug collision — using documents/{slug}.md for {doc['filename']}")
            except OSError:
                pass

        entities_path  = registry_dir / "entities.json"
        documents_path = registry_dir / "documents.json"
        registry_path  = registry_dir / "registry.json"
        log_path       = registry_dir / "ingest.log"

        entities_reg  = json.loads(entities_path.read_text())  if entities_path.exists()  else {}
        documents_reg = json.loads(documents_path.read_text()) if documents_path.exists() else {}

        # Resolved-contradiction overlay (#266): callouts the journalist has acknowledged are
        # dropped from the rendered note body. The registry keeps the full list, so unresolving
        # restores them on the next write.
        from watchdog.pipeline import resolutions
        resolved_ids = resolutions.resolved_ids(vault_path)

        morgue_relative = (
            f"morgue/{extraction.get('morgue_entity_id', 'unknown')}"
            f"/{extraction.get('morgue_document_type', 'document')}"
            f"/{doc['filename']}"
        )

        # ── 1. Update entity registry ─────────────────────────────────────────

        # Reconcile near-duplicate slugs coined by concurrent extraction tasks before merging.
        _reconcile_entity_ids(incoming_entities, entities_reg)
        # Roles arrive as target_id only; re-inflate target_name/target_type deterministically.
        _resolve_role_targets(incoming_entities, entities_reg)

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

        # ── 2. Update document registry ──────────────────────────────────────

        # Prefer minhash from neardup_data dict, then sidecar file, then extraction field.
        if neardup_data and neardup_data.get("candidate_minhash"):
            sig = neardup_data["candidate_minhash"]
        elif neardup_file and neardup_file.exists():
            try:
                sig = json.loads(neardup_file.read_text()).get("candidate_minhash", [])
            except Exception:
                sig = doc.get("minhash", [])
        else:
            sig = doc.get("minhash", [])

        documents_reg[doc_sha256] = {
            "sha256":           doc_sha256,
            "filename":         doc["filename"],
            "title":            doc_title,
            "original_path":    doc.get("original_path", f"_INCOMING/{doc['filename']}"),
            "document_note":    f"documents/{slug}",
            "ingested_at":      _now_iso(),
            "page_count":       doc.get("page_count"),
            "document_type":    doc.get("document_type"),
            "record_skill":     doc.get("record_skill"),
            "record_skill_hash": doc.get("record_skill_hash"),
            "extract_model":    doc.get("extract_model"),
            "extract_effort":   doc.get("extract_effort"),
            "file_metadata":    doc.get("file_metadata") or {},
            "coverage_gap":     doc.get("coverage_gap"),
            "entities_extracted": [e["id"] for e in incoming_entities],
            "near_duplicate_of": doc.get("near_duplicate_of"),
            "minhash":          sig,
            "morgue_path":      morgue_relative,
        }

        # ── 3. Write entity notes ─────────────────────────────────────────────
        #
        # Steps 3–4 write the vault notes (the human-facing artifacts); the derived search
        # indexes and finalizer fragments are deferred to step 6, *after* the registries
        # persist, since they are rebuilt-from-source data that a re-run regenerates (#259).
        # The notes themselves are idempotent per document: the ## Analysis block and each
        # fragment block are keyed by this document (doc-note link / sha) and replaced, not
        # appended, so a repair retry converges instead of doubling claims.

        incoming_by_id = {e["id"]: e for e in incoming_entities}

        # (note_path, kind, title, content) tuples replayed into the embed + FTS indexes
        # after the commit point; and the entities whose fragment blocks to (re)write.
        note_index_jobs: list[tuple[str, str, str, str]] = []
        fragment_jobs: list[tuple[str, dict, dict]] = []

        for eid in modified:
            entry = entities_reg[eid]
            note_path = _assert_in_vault(
                vault_path / f"{entry['note_path']}.md", vault_path, "entity note_path"
            )
            note_path.parent.mkdir(parents=True, exist_ok=True)

            notes_section = _extract_notes_section(note_path)

            incoming = incoming_by_id.get(eid, {})
            # Extraction no longer emits a per-entity summary (#140). A recurring entity
            # (appears_in >= 2) gets a model-synthesized summary in post-ingest; a single-document
            # entity is a deterministic stub with no Summary section (its facts live in ## Analysis).
            # So the only summary at write time is a carried one from a prior synthesis.
            new_summary = incoming.get("summary") or _extract_summary(note_path)

            doc_note = documents_reg[doc_sha256]["document_note"]
            # Replace-not-append: drop any block this document contributed on a prior (crashed)
            # attempt before adding it back, so a repair retry doesn't duplicate the entry (#259).
            existing_analysis = _drop_analysis_entry(_extract_analysis(note_path), doc_note)
            new_analysis_text = _render_evidence_fragments(
                incoming.get("evidence_fragments") or [],
                documents_reg[doc_sha256]["morgue_path"],
            )
            if new_analysis_text:
                entry_line = f"*{_today()}, via [[{doc_note}|{_defang(doc_title)}]]:*\n{new_analysis_text}"
                accumulated = (
                    existing_analysis.rstrip() + "\n\n" + entry_line
                ).lstrip() if existing_analysis else entry_line
            else:
                accumulated = existing_analysis

            # The registry entry is the contradiction ledger (#282); the note body is a
            # filtered render of it. Fold in any note-only callouts too (self-healing
            # backfill for pre-#282 vaults, or a stray hand-edit) before filtering (#288).
            all_callouts = resolutions.dedup_callouts(
                list(entry.get("contradictions") or [])
                + resolutions.split_callouts(_extract_contradictions(note_path))
            )
            entry["contradictions"] = all_callouts
            contradictions = "\n\n".join(resolutions.filter_callouts(all_callouts, resolved_ids))

            note_content = build_entity_note(
                entry, notes_section, documents_reg, new_summary, accumulated, contradictions
            )
            note_path.write_text(note_content, encoding="utf-8")
            note_index_jobs.append((entry["note_path"], "entity", entry["name"], note_content))

            # Fragment for the post-ingest finalizer — only for entities actually extracted
            # from this document, not reverse-role touches. Deferred to step 6.
            if incoming:
                fragment_jobs.append((eid, entry, incoming))

        # ── 4. Write document note ────────────────────────────────────────────

        doc_note_path = _assert_in_vault(
            vault_path / "documents" / f"{slug}.md", vault_path, "document note_path"
        )
        doc_note_path.parent.mkdir(parents=True, exist_ok=True)
        entity_entries_for_note = [entities_reg[e["id"]] for e in incoming_entities if e["id"] in entities_reg]
        doc_note_content = _build_document_note(doc, entity_entries_for_note, morgue_relative)
        doc_note_path.write_text(doc_note_content, encoding="utf-8")
        note_index_jobs.append((f"documents/{slug}", "document", doc_title, doc_note_content))

        # ── 5. Persist registries (atomic temp-then-rename) ──────────────────

        def _write_atomic(path: Path, data) -> None:
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            tmp.rename(path)

        _write_atomic(entities_path, entities_reg)
        _write_atomic(documents_path, documents_reg)

        try:
            existing_registry = json.loads(registry_path.read_text()) if registry_path.exists() else {}
        except json.JSONDecodeError:
            existing_registry = {}
        existing_registry.update({
            "last_updated":   _now_iso(),
            "document_count": len(documents_reg),
            "entity_count":   len(entities_reg),
        })
        _write_atomic(registry_path, existing_registry)

        _update_manifest(vault_path, entities_reg)

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(
                f"[{_now_iso()}] INGEST \"{doc['filename']}\" "
                f"sha256={doc_sha256} "
                f"entities={len(incoming_entities)} "
                f"type={doc.get('document_type', 'unknown')}\n"
            )

        # ── 6. Update derived data (search indexes + finalizer fragments) ─────
        #
        # These are rebuilt-from-source: the embed/FTS indexes are keyed by note_path (upsert)
        # and the fragment blocks are keyed by sha (replace), so replaying them after the commit
        # point is idempotent — a repair retry converges instead of doubling (#259). Kept inside
        # the registry lock so fragment writes stay race-free with other write_vault calls.
        for idx_note_path, kind, idx_title, idx_content in note_index_jobs:
            try:
                from watchdog.pipeline.embed import add_note
                add_note(vault_path, idx_note_path, idx_content)
            except Exception as e:
                print(f"  Warning: embed index update failed for {idx_note_path}: {e}", file=sys.stderr)
            try:
                from watchdog.pipeline.fulltext import add_note as fts_add_note
                fts_add_note(vault_path, idx_note_path, kind, idx_title, idx_content)
            except Exception as e:
                print(f"  Warning: full-text index update failed for {idx_note_path}: {e}", file=sys.stderr)

        # Index the source passages (corpus stream) with a contextual prefix — now that
        # extraction has supplied the title, type, and entities the prefix needs (D43).
        try:
            _index_corpus_passages(vault_path, doc, entity_entries_for_note, morgue_path=morgue_relative)
        except Exception as e:
            print(f"  Warning: corpus index update failed for {doc['filename']}: {e}", file=sys.stderr)

        for eid, entry, incoming in fragment_jobs:
            _record_entity_fragment(vault_path, eid, entry, incoming, doc, doc_title)

    # The global timeline is no longer rebuilt per document (#237): it is rendered
    # exclusively from the cross-document-deduped canonical NDJSON, which only exists after
    # `_post_ingest` runs the dedup pass at the end of a batch. A standalone write-vault
    # therefore leaves timeline.md to the next `watchdog ingest` or explicit `watchdog timeline`.

    # ── 7. Move source file to morgue ─────────────────────────────────────────

    morgue_dir = _assert_in_vault(
        vault_path / Path(morgue_relative).parent, vault_path, "morgue path"
    )
    morgue_dir.mkdir(parents=True, exist_ok=True)

    source = vault_path / doc.get("original_path", f"_INCOMING/{doc['filename']}")
    if source.exists():
        shutil.move(str(source), str(morgue_dir / source.name))
        sidecar = Path(str(source) + ".yml")
        if sidecar.exists():
            shutil.move(str(sidecar), str(morgue_dir / sidecar.name))
        # Preserve the Docling text alongside the original so the full document stays greppable in
        # the vault — extraction now indexes this substrate rather than restating it (#140).
        _write_morgue_markdown(vault_path, doc_sha256, morgue_dir, source.stem)

        incoming_dir = vault_path / "_INCOMING"
        parent = source.parent
        while parent != incoming_dir and parent.is_relative_to(incoming_dir):
            try:
                parent.rmdir()
                parent = parent.parent
            except OSError:
                break

        staging_dir = vault_path / ".watchdog" / "staging"
        if parent.parent == staging_dir:
            try:
                parent.rmdir()
            except OSError:
                pass

    if not quiet:
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
    parser.add_argument("--neardup-file", metavar="PATH",
                        help="Path to near-dup JSON output — shingles are read from here instead of the extraction JSON")
    args = parser.parse_args()

    extraction_path = Path(args.extraction).resolve()
    vault_path = Path(args.vault).resolve()
    neardup_file = Path(args.neardup_file).resolve() if args.neardup_file else None

    if not vault_path.exists():
        sys.exit(f"Error: vault directory {vault_path} not found")
    if not (vault_path / ".watchdog").is_dir():
        sys.exit(f"Error: {vault_path} is not a Watchdog vault directory")
    for label, p in [("--extraction", extraction_path), ("--neardup-file", neardup_file)]:
        if p is None:
            continue
        if not str(p).startswith(str(vault_path) + "/"):
            sys.exit(f"Error: {label} path must be inside the vault directory ({vault_path})")
    if not extraction_path.exists():
        sys.exit(f"Error: {extraction_path} not found")

    run(extraction_path, vault_path, neardup_file=neardup_file)


if __name__ == "__main__":
    main()
