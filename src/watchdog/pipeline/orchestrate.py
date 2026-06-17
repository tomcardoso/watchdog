"""Python ingest orchestrator (#118 Workstream 3).

Replaces the Claude Code `/watchdog-ingest` skill: Python drives the loop and calls the
model (`model_client.acomplete_json`) only for reasoning — classification, extraction,
and (in `_post_ingest`, added in a later phase) synthesis/timeline/briefing. Everything
mechanical reuses the existing deterministic pieces: `preflight.run`, `postflight.run`
(validates + writes the vault), and the registry write-lock inside `write_vault`.

Documents are extracted concurrently, bounded by a semaphore; the registry lock already
serializes the vault writes and `_reconcile_entity_ids` handles the parallel new-id race.
"""

import asyncio
import json
from pathlib import Path

from watchdog import model_client
from watchdog.pipeline import preflight, postflight, prompts, schemas

DEFAULT_CONCURRENCY = 5
_CLASSIFY_EXCERPT_CHARS = 6000


def _records_dir(vault: Path) -> Path:
    return vault / ".claude" / "commands" / "records"


def _read_brief(vault: Path) -> str | None:
    p = vault / "context.md"
    return p.read_text(encoding="utf-8") if p.exists() else None


def _read_skill(vault: Path, skill_filename: str) -> str:
    p = _records_dir(vault) / skill_filename
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _pages_text(pages: list[dict]) -> str:
    return "\n\n---\n\n".join(
        f"<!-- PAGE {pg['page']} -->\n\n{pg.get('markdown', '')}" for pg in pages
    )


def _read_sidecar(vault: Path, filename: str) -> str | None:
    sc = vault / "_INCOMING" / f"{filename}.yml"
    return sc.read_text(encoding="utf-8") if sc.exists() else None


async def _classify(vault: Path, doc_excerpt: str) -> str:
    index = _records_dir(vault) / "_index.md"
    index_text = index.read_text(encoding="utf-8") if index.exists() else ""
    r = await model_client.acomplete_json(
        task="classify", model="haiku", schema=schemas.CLASSIFY,
        prompt=prompts.build_classify_prompt(doc_excerpt, index_text),
    )
    return r.parsed.get("skill") or "general-records.md"


def _compact_result(sha: str, filename: str, extraction: dict, near_dup: dict, cost: float | None) -> dict:
    entities = extraction.get("entities", [])
    doc = extraction.get("document", {})
    return {
        "sha256": sha, "filename": filename, "status": "ok",
        "document_type": doc.get("document_type"),
        "date": doc.get("date_of_document"),
        "entity_count": len(entities),
        "new_entities": [e["id"] for e in entities if not e.get("match_id")],
        "updated_entities": [e["match_id"] for e in entities if e.get("match_id")],
        "contradictions": [e["id"] for e in entities if e.get("contradictions")],
        "near_dup_similarity": near_dup.get("top_similarity", 0.0),
        "cost_usd": cost,
    }


async def _extract_document(vault: Path, sha: str, brief: str | None) -> dict:
    pf = preflight.run(vault, sha)
    if pf.get("error"):
        return {"sha256": sha, "status": "failed", "reason": pf["error"]}
    if pf.get("already_extracted"):
        return {"sha256": sha, "filename": pf.get("filename"), "status": "skipped"}

    filename = pf["filename"]
    pages = pf.get("pages", [])
    pages_text = _pages_text(pages)

    skill_file = await _classify(vault, pages_text[:_CLASSIFY_EXCERPT_CHARS])
    prompt = prompts.build_extract_prompt(
        pages_text=pages_text, existing_entities=pf.get("existing_entities", []),
        skill_text=_read_skill(vault, skill_file), sidecar=_read_sidecar(vault, filename),
        sha256=sha, filename=filename, original_path=pf.get("original_path"),
        page_count=pf.get("page_count") or len(pages), brief=brief,
    )

    last_errors: list[str] = []
    cost = 0.0
    for repair in range(2):   # extract, then one repair attempt if post-flight rejects
        p = prompt if not last_errors else (
            prompt + "\n\nThe previous extraction was rejected:\n" + "\n".join(last_errors)
            + "\nReturn a corrected JSON object.")
        r = await model_client.acomplete_json(task="extract", prompt=p, schema=schemas.EXTRACTION)
        cost += r.cost_usd or 0.0
        extraction = r.parsed
        scratchpad = extraction.pop("scratchpad", "")

        tmp = vault / ".watchdog" / "tmp" / f"wdg_ex_{sha}.json"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(extraction, ensure_ascii=False, indent=2), encoding="utf-8")

        outcome = postflight.run(vault, tmp)
        if "errors" not in outcome:
            if scratchpad:
                (vault / ".watchdog" / "tmp" / f"notes_{sha}.md").write_text(scratchpad, encoding="utf-8")
            (vault / ".watchdog" / "queue" / f"{sha}.json").unlink(missing_ok=True)
            return _compact_result(sha, filename, extraction, pf.get("near_dup", {}), round(cost, 6))
        last_errors = outcome["errors"]

    return {"sha256": sha, "filename": filename, "status": "failed",
            "reason": "post-flight rejected: " + "; ".join(last_errors[:3])}


async def run(vault: Path, concurrency: int = DEFAULT_CONCURRENCY) -> dict:
    """Extract every queued document, bounded by `concurrency`. Returns a summary.

    Post-ingest synthesis/timeline/briefing is added in a later phase.
    """
    queue_dir = vault / ".watchdog" / "queue"
    shas = [f.stem for f in sorted(queue_dir.glob("*.json"))] if queue_dir.exists() else []
    if not shas:
        return {"results": [], "extracted": 0, "skipped": 0, "failed": 0}

    brief = _read_brief(vault)
    sem = asyncio.Semaphore(concurrency)

    async def _guarded(sha: str) -> dict:
        async with sem:
            try:
                return await _extract_document(vault, sha, brief)
            except Exception as e:   # one bad doc must not sink the batch
                return {"sha256": sha, "status": "failed", "reason": str(e)}

    results = await asyncio.gather(*[_guarded(s) for s in shas])
    by_status = lambda s: sum(1 for r in results if r.get("status") == s)
    return {"results": results, "extracted": by_status("ok"),
            "skipped": by_status("skipped"), "failed": by_status("failed")}
