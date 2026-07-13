"""
Watchdog pre-flight — packages everything the model needs to extract a document.

Reads the queue file, runs entity candidate lookup against the manifest (substring
match — no ML), and returns a single JSON blob. The orchestrator sends this output
to the model, which does extraction reasoning and returns the extraction JSON; the
orchestrator then calls post-flight.
"""

import json
import re
import sys
from pathlib import Path

from watchdog.pipeline.write_vault import _extract_summary, _extract_contradictions

# Aliases shorter than this are ignored during candidate matching. Aliases are where short, noisy
# strings accumulate (initials, abbreviations), and each is an independent substring surface that
# drags a whole digest into the extraction prompt on a false hit (#216). The floor applies to
# aliases ONLY — a canonical name matches at any length, so "BP"/"GE"/"3M" stay findable.
DEFAULT_ALIAS_MIN_LENGTH = 3

_ASCII_WORD = re.compile(r"[a-z0-9]")   # doc text and names are lowercased before matching


def _config_get(key: str, default):
    """Read a single key from ~/.watchdog/config.json (best-effort). Local copy of the pipeline's
    config-read convention, to avoid importing the cmd layer (cf. embed.py, section.py)."""
    try:
        cfg = json.loads((Path.home() / ".watchdog" / "config.json").read_text())
    except Exception:
        cfg = {}
    return cfg.get(key, default)


def _name_matches(needle: str, text_lower: str) -> bool:
    """Whole-token containment: `needle` must appear in `text_lower` on token boundaries, not
    buried inside a longer word — so "Lee" no longer matches "asleep" (#216). Boundaries are
    asserted only on edges that are ASCII word chars; a non-ASCII edge (CJK and other unspaced
    scripts, which `\\b`/`\\w` can't segment) falls back to plain substring, so the matcher never
    matches *less* than the old behavior for non-Latin names. `needle`/`text_lower` are lowercased."""
    needle = needle.strip().lower()
    if not needle or needle not in text_lower:   # fast substring reject (also the non-Latin path)
        return False
    left = r"(?<![a-z0-9])" if _ASCII_WORD.match(needle[0]) else ""
    right = r"(?![a-z0-9])" if _ASCII_WORD.match(needle[-1]) else ""
    if not left and not right:
        return True   # non-ASCII edges: substring hit already confirmed above
    return re.search(left + re.escape(needle) + right, text_lower) is not None


def _timeline_dedup_key(date, event: str) -> tuple:
    return (date, event.strip().lower())


def _hoist_timeline(candidates: list[dict], entities_reg: dict) -> list[dict]:
    """Shared, deduplicated prior timeline across all candidates (D109).

    `postflight.explode_key_facts` fans one dated key_fact onto every tagged entity's
    registry timeline, so the same event recurs verbatim across candidates that were all
    mentioned in the same earlier document. Sending it once per candidate — as the
    per-entity digest used to — repeats the exact text N times for a fact tagging N
    entities. Deduplicated here by (date, event text) and tagged with the candidate ids
    it concerns, mirroring the shape the model emits key_facts in to begin with (D26).
    """
    by_key: dict[tuple, dict] = {}
    for c in candidates:
        for e in entities_reg.get(c["id"], {}).get("timeline_events", []):
            date, event = e.get("date"), e.get("event")
            key = _timeline_dedup_key(date, event or "")
            entry = by_key.get(key)
            if entry is None:
                by_key[key] = {"date": date, "event": event,
                               "basis": e.get("basis") or "stated", "entities": [c["id"]]}
            elif c["id"] not in entry["entities"]:
                entry["entities"].append(c["id"])
    return sorted(by_key.values(), key=lambda e: (e["date"] or "", e["event"] or ""))


def _digest_roles(roles: list[dict]) -> list[dict]:
    """Comparison-relevant fields of an entity's relationships."""
    return [
        {
            "relationship": r.get("relationship"),
            "target_name": r.get("target_name"),
            "target_type": r.get("target_type"),
            "date_range": r.get("date_range"),
            "basis": r.get("basis") or "stated",
        }
        for r in roles
    ]


def _existing_analysis(vault: Path, note_path: str) -> str:
    """Return the existing '## Analysis' section of an entity note.

    Supplied alongside the entity's prior contradictions so the model can run
    the contradiction check without reading note files. Returns '' if the note or
    the section is absent.
    """
    if not note_path:
        return ""
    p = vault / f"{note_path}.md"
    if not p.exists():
        return ""
    content = p.read_text(encoding="utf-8", errors="replace")
    idx = content.find("## Analysis")
    if idx == -1:
        return ""
    start = idx + len("## Analysis")
    nxt = content.find("\n## ", start)
    body = content[start:nxt] if nxt != -1 else content[start:]
    return body.strip()


def run(vault: Path, sha256: str, *, alias_min_length: int | None = None) -> dict:
    queue_file = vault / ".watchdog" / "queue" / f"{sha256}.json"
    if not queue_file.exists():
        return {"error": f"queue file not found for sha256 {sha256}"}

    alias_floor = (
        alias_min_length if alias_min_length is not None
        else _config_get("preflight_alias_min_length", DEFAULT_ALIAS_MIN_LENGTH)
    )

    queue = json.loads(queue_file.read_text(encoding="utf-8"))

    # Build full document text for entity candidate matching
    text_lower = " ".join(
        p.get("markdown", "") for p in queue.get("pages", [])
    ).lower()

    # Full registry, read once — supplies each candidate's roles digest and the shared
    # timeline hoist so the model can run the contradiction check without reading note files.
    entities_reg: dict = {}
    entities_file = vault / ".watchdog" / "Registry" / "entities.json"
    if entities_file.exists():
        try:
            entities_reg = json.loads(entities_file.read_text(encoding="utf-8"))
        except Exception:
            entities_reg = {}

    # Candidate entities: manifest entries whose name or any alias appears in the text
    candidates: list[dict] = []
    manifest_file = vault / ".watchdog" / "Registry" / "manifest.json"
    if manifest_file.exists():
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        for eid, entry in manifest.items():
            name = entry.get("name", "")
            aliases = entry.get("aliases", [])
            # Canonical name matches at any length; aliases must clear the floor first (#216).
            if _name_matches(name, text_lower) or any(
                len(a.strip()) >= alias_floor and _name_matches(a, text_lower) for a in aliases
            ):
                note_path = entry.get("note_path", "")
                reg = entities_reg.get(eid, {})
                candidates.append({
                    "id": eid,
                    "name": entry.get("name", ""),
                    "type": entry.get("type", ""),
                    "aliases": entry.get("aliases", []),
                    "note_path": note_path,
                    # Carried forward so the model revises rather than clobbers.
                    "summary": _extract_summary(vault / f"{note_path}.md") if note_path else None,
                    "roles": _digest_roles(reg.get("roles", [])),
                    "analysis": _existing_analysis(vault, note_path),
                    "contradictions": _extract_contradictions(vault / f"{note_path}.md") if note_path else "",
                })

    # Check if already extracted; collect the document types already used in this vault so
    # the extractor can reuse one rather than coining a near-duplicate (keeps the type
    # vocabulary — and the `watchdog status` tally — consistent).
    documents_path = vault / ".watchdog" / "Registry" / "documents.json"
    already_extracted = False
    known_document_types: list[str] = []
    if documents_path.exists():
        try:
            docs = json.loads(documents_path.read_text(encoding="utf-8"))
            already_extracted = sha256 in docs
            known_document_types = sorted(
                {t for d in docs.values() if (t := d.get("document_type"))})
        except Exception:
            pass

    near_dup = queue.get("near_dup", {})

    existing_timeline = _hoist_timeline(candidates, entities_reg)

    # Digest-size telemetry (#216): how many bytes of prior-entity context this document's
    # extraction prompt is carrying — candidates plus the shared timeline — and across how many
    # candidates. Surfaced per-doc during ingest so cap sizes can be chosen from real data on a
    # mature vault rather than guessed.
    existing_entities_bytes = (len(json.dumps(candidates, ensure_ascii=False))
                                + len(json.dumps(existing_timeline, ensure_ascii=False)))

    return {
        "sha256":             queue.get("sha256", sha256),
        "filename":           queue.get("filename", ""),
        "original_path":      queue.get("source_path", ""),
        "page_count":         queue.get("page_count") or len(queue.get("pages", [])),
        "already_extracted":  already_extracted,
        "pages":              queue.get("pages", []),
        "near_dup": {
            "near_duplicates": near_dup.get("near_duplicates", []),
            "top_similarity":  near_dup.get("top_similarity", 0.0),
        },
        "existing_entities":  candidates,
        "existing_timeline":  existing_timeline,
        "existing_entities_bytes": existing_entities_bytes,
        "existing_entities_count": len(candidates),
        "known_document_types": known_document_types,
        # File-intrinsic embedded metadata captured at chew time (#369), and the processing
        # facts (ocr_used/source_type/etc.) the pipeline asserted about how the file was read —
        # both threaded through to the extraction prompt (prompts.py) and, for the former, to
        # the stamped document (orchestrate._stamp_document).
        "file_metadata": queue.get("file_metadata", {}),
        "processing": queue.get("metadata", {}),
    }


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("Usage: watchdog pre-flight <sha256>")

    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")

    sha256 = sys.argv[1]
    result = run(vault, sha256)
    if "error" in result:
        sys.exit(f"Error: {result['error']}")

    # Write pages as a single markdown file with page-break markers
    tmp_dir = vault / ".watchdog" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    pages_path = tmp_dir / f"preflight_{sha256}_pages.md"
    parts = []
    for page in result.get("pages", []):
        parts.append(f"<!-- PAGE {page['page']} -->\n\n{page.get('markdown', '')}")
    pages_path.write_text("\n\n---\n\n".join(parts))

    # Stdout is metadata-only — pages must be read from pages_path
    metadata = {k: v for k, v in result.items() if k != "pages"}
    metadata["pages_path"] = str(pages_path)
    print(json.dumps(metadata, ensure_ascii=False))


if __name__ == "__main__":
    main()
