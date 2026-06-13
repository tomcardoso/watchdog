"""
Watchdog pre-flight — packages everything a subagent needs to extract a document.

Reads the queue file, runs entity candidate lookup against the manifest (substring
match — no ML), and returns a single JSON blob. The subagent reads this output,
does extraction reasoning, writes the extraction JSON, then calls post-flight.
"""

import json
import sys
from pathlib import Path


def _digest_events(events: list[dict]) -> list[dict]:
    """Comparison-relevant fields of an entity's timeline events."""
    return [
        {"date": e.get("date"), "event": e.get("event"), "confidence": e.get("confidence")}
        for e in events
    ]


def _digest_roles(roles: list[dict]) -> list[dict]:
    """Comparison-relevant fields of an entity's relationships."""
    return [
        {
            "relationship": r.get("relationship"),
            "target_name": r.get("target_name"),
            "target_type": r.get("target_type"),
            "date_range": r.get("date_range"),
            "confidence": r.get("confidence"),
        }
        for r in roles
    ]


def _existing_analysis(vault: Path, note_path: str) -> str:
    """Return the existing '## Analysis' section of an entity note.

    The Analysis section holds any prior [!contradiction] callouts, so supplying
    it lets the subagent run the contradiction check without reading note files.
    Returns '' if the note or the section is absent.
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


def run(vault: Path, sha256: str) -> dict:
    queue_file = vault / ".watchdog" / "queue" / f"{sha256}.json"
    if not queue_file.exists():
        return {"error": f"queue file not found for sha256 {sha256}"}

    queue = json.loads(queue_file.read_text(encoding="utf-8"))

    # Build full document text for entity candidate matching
    text_lower = " ".join(
        p.get("markdown", "") for p in queue.get("pages", [])
    ).lower()

    # Full registry, read once — supplies each candidate's timeline/roles digest so
    # the subagent can run the contradiction check without reading note files.
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
            names = [entry.get("name", "")] + entry.get("aliases", [])
            if any(n and n.lower() in text_lower for n in names):
                note_path = entry.get("note_path", "")
                reg = entities_reg.get(eid, {})
                candidates.append({
                    "id": eid,
                    "name": entry.get("name", ""),
                    "type": entry.get("type", ""),
                    "aliases": entry.get("aliases", []),
                    "note_path": note_path,
                    "timeline_events": _digest_events(reg.get("timeline_events", [])),
                    "roles": _digest_roles(reg.get("roles", [])),
                    "analysis": _existing_analysis(vault, note_path),
                })

    # Check if already extracted
    documents_path = vault / ".watchdog" / "Registry" / "documents.json"
    already_extracted = False
    if documents_path.exists():
        try:
            docs = json.loads(documents_path.read_text(encoding="utf-8"))
            already_extracted = sha256 in docs
        except Exception:
            pass

    near_dup = queue.get("near_dup", {})

    return {
        "sha256":             queue.get("sha256", sha256),
        "filename":           queue.get("filename", ""),
        "page_count":         queue.get("page_count") or len(queue.get("pages", [])),
        "already_extracted":  already_extracted,
        "pages":              queue.get("pages", []),
        "near_dup": {
            "near_duplicates": near_dup.get("near_duplicates", []),
            "top_similarity":  near_dup.get("top_similarity", 0.0),
        },
        "existing_entities":  candidates,
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
