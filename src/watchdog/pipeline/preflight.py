"""
Watchdog pre-flight — packages everything the model needs to extract a document.

Reads the queue file and returns a single JSON blob: the page text plus the document's own
processing facts. The orchestrator sends this output to the model, which does extraction
reasoning and returns the extraction JSON; the orchestrator then calls post-flight.

**Pre-flight reads no vault state** (#381/D118). It used to snapshot the entity registry at the
moment each document's extraction began and hand it to the extractor as EXISTING_ENTITIES /
EXISTING_TIMELINE — for entity dedup and the contradiction check. That made extraction a
function of *ingest order and concurrency wave* rather than of the document: documents in the
same wave could not see each other at all, and a document could only ever be checked against the
documents ahead of it. Both jobs moved to the finalizer (`pipeline/reconcile.py`), which is the
only stage that sees the whole entity set, so extraction is now a pure function of the document.
`known_document_types` is the one registry read that remains — it steers the document_type
vocabulary and is order-insensitive by nature (a growing set of strings to reuse from, which
changes nothing about what the document says).

**Two independent "already done" questions (#403 phase 1).** Extraction now stages its output as
a durable artifact (`.watchdog/extracted/<sha>.json`, written by `postflight.run`) instead of
writing straight to the vault; a separate commit pass at finalize-start replays `write_vault` over
whatever hasn't been committed yet. That splits what used to be one flag into two, each answering
a different question and consulted at a different point in the pipeline:

  - **Has this document been extracted?** — `already_staged`, true when the extraction artifact
    exists. The orchestrator checks this *before* spending a classify/extract call: no artifact,
    no reason to pay for one again. This check is deliberately sha-only — re-extracting under a
    different model/effort/skill needs `--force` (#424, out of scope here).
  - **Has this document been committed to the vault?** — `already_extracted`, true when its sha
    is a key in `registry/documents.json`. This is the pre-existing flag and keeps its pre-existing
    meaning; it answers "is there anything left to do for this document at all."
"""

import json
import sys
from pathlib import Path


def run(vault: Path, sha256: str) -> dict:
    queue_file = vault / ".watchdog" / "queue" / f"{sha256}.json"
    if not queue_file.exists():
        return {"error": f"queue file not found for sha256 {sha256}"}

    queue = json.loads(queue_file.read_text(encoding="utf-8"))

    # Check if already committed to the vault; collect the document types already used in this
    # vault so the extractor can reuse one rather than coining a near-duplicate (keeps the type
    # vocabulary — and the `watchdog status` tally — consistent).
    documents_path = vault / ".watchdog" / "registry" / "documents.json"
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

    # Has this document already been extracted (staged), regardless of whether it has been
    # committed yet? Sha-only, deliberately (#403 phase 1 / #424) — a durable artifact here means
    # no classify/extract call is needed, whatever model/effort/skill produced it.
    already_staged = (vault / ".watchdog" / "extracted" / f"{sha256}.json").exists()

    near_dup = queue.get("near_dup", {})

    return {
        "sha256":             queue.get("sha256", sha256),
        "filename":           queue.get("filename", ""),
        "original_path":      queue.get("source_path", ""),
        "page_count":         queue.get("page_count") or len(queue.get("pages", [])),
        "already_extracted":  already_extracted,
        "already_staged":     already_staged,
        "pages":              queue.get("pages", []),
        "near_dup": {
            "near_duplicates": near_dup.get("near_duplicates", []),
            "top_similarity":  near_dup.get("top_similarity", 0.0),
        },
        "known_document_types": known_document_types,
        # File-intrinsic embedded metadata captured at chew time (#369), and the processing
        # facts (ocr_used/source_type/etc.) the pipeline asserted about how the file was read —
        # both threaded through to the extraction prompt (prompts.py) and, for the former, to
        # the stamped document (orchestrate._stamp_document).
        "file_metadata": queue.get("file_metadata", {}),
        "processing": queue.get("metadata", {}),
        # Already filtered/allowlisted at chew time (pipeline/sidecar.py) — raw text or None,
        # never read from _INCOMING again past this point (D121).
        "sidecar": queue.get("sidecar"),
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
