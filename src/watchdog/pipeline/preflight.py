"""Pre-flight: package what the model needs to extract one document.

Reads the queue file and returns the page text and the document's processing facts. It reads no
entity state, so extraction is a pure function of the document (D118); its one registry read is
the set of document types already in use, which the extractor may reuse.

It also answers two separate "already done" questions (D126): `already_staged` (an extraction
artifact exists in `.watchdog/extracted/`, so no classify/extract call is needed) and
`already_extracted` (the sha is committed in `registry/documents.json`, so nothing is left to do)."""

import json
from pathlib import Path



def registry_context(vault: Path) -> dict:
    """What pre-flight needs from `registry/documents.json`: the committed shas and the document
    types already in use. Read once and passed to `run` by a caller pre-flighting many documents,
    so the registry (which carries a minhash per document) isn't re-parsed for each one."""
    documents_path = vault / ".watchdog" / "registry" / "documents.json"
    shas: set[str] = set()
    types: list[str] = []
    if documents_path.exists():
        try:
            docs = json.loads(documents_path.read_text(encoding="utf-8"))
            shas = set(docs)
            types = sorted({t for d in docs.values() if (t := d.get("document_type"))})
        except Exception:
            pass
    return {"shas": shas, "types": types}


def run(vault: Path, sha256: str, registry: dict | None = None) -> dict:
    queue_file = vault / ".watchdog" / "queue" / f"{sha256}.json"
    if not queue_file.exists():
        return {"error": f"queue file not found for sha256 {sha256}"}

    queue = json.loads(queue_file.read_text(encoding="utf-8"))

    # Check if already committed to the vault; collect the document types already used in this
    # vault so the extractor can reuse one rather than coining a near-duplicate (keeps the type
    # vocabulary — and the `watchdog status` tally — consistent).
    if registry is None:
        registry = registry_context(vault)
    already_extracted = sha256 in registry["shas"]
    known_document_types: list[str] = list(registry["types"])

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
