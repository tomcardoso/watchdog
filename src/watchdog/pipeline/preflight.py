"""
Watchdog pre-flight — packages everything a subagent needs to extract a document.

Reads the queue file, runs entity candidate lookup against the manifest (substring
match — no ML), and returns a single JSON blob. The subagent reads this output,
does extraction reasoning, writes the extraction JSON, then calls post-flight.
"""

import json
import sys
from pathlib import Path


def run(vault: Path, sha256: str) -> dict:
    queue_file = vault / ".watchdog" / "queue" / f"{sha256}.json"
    if not queue_file.exists():
        return {"error": f"queue file not found for sha256 {sha256}"}

    queue = json.loads(queue_file.read_text(encoding="utf-8"))

    # Build full document text for entity candidate matching
    text_lower = " ".join(
        p.get("markdown", "") for p in queue.get("pages", [])
    ).lower()

    # Candidate entities: manifest entries whose name or any alias appears in the text
    candidates: list[dict] = []
    manifest_file = vault / ".watchdog" / "Registry" / "manifest.json"
    if manifest_file.exists():
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        for eid, entry in manifest.items():
            names = [entry.get("name", "")] + entry.get("aliases", [])
            if any(n and n.lower() in text_lower for n in names):
                candidates.append({
                    "id": eid,
                    "name": entry.get("name", ""),
                    "type": entry.get("type", ""),
                    "aliases": entry.get("aliases", []),
                    "note_path": entry.get("note_path", ""),
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

    tmp_dir = vault / ".watchdog" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"preflight_{sha256[:7]}.json"
    tmp_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
