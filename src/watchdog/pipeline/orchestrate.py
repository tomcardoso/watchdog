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
import datetime
import json
from pathlib import Path

from watchdog import model_client
from watchdog.pipeline import (
    merge, preflight, postflight, prompts, schemas, section, synthesis_bundle, timeline,
)

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


def _write_postflight(vault: Path, sha: str, extraction: dict) -> tuple[bool, list[str]]:
    tmp = vault / ".watchdog" / "tmp" / f"wdg_ex_{sha}.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(extraction, ensure_ascii=False, indent=2), encoding="utf-8")
    outcome = postflight.run(vault, tmp)
    return ("errors" not in outcome), outcome.get("errors", [])


async def _simple_extract(vault, sha, pf, skill_text, brief):
    """Whole-document extraction, with one repair attempt if post-flight rejects."""
    base = prompts.build_extract_prompt(
        pages_text=_pages_text(pf["pages"]), existing_entities=pf.get("existing_entities", []),
        skill_text=skill_text, sidecar=_read_sidecar(vault, pf["filename"]),
        sha256=sha, filename=pf["filename"], original_path=pf.get("original_path"),
        page_count=pf.get("page_count") or len(pf["pages"]), brief=brief,
    )
    cost, errors, extraction, scratchpad = 0.0, [], {}, ""
    for _ in range(2):
        p = base if not errors else (base + "\n\nThe previous extraction was rejected:\n"
                                     + "\n".join(errors) + "\nReturn a corrected JSON object.")
        r = await model_client.acomplete_json(task="extract", prompt=p, schema=schemas.EXTRACTION)
        cost += r.cost_usd or 0.0
        extraction = r.parsed
        scratchpad = extraction.pop("scratchpad", "")
        ok, errors = _write_postflight(vault, sha, extraction)
        if ok:
            return extraction, scratchpad, cost, True, []
    return extraction, scratchpad, cost, False, errors


def _carry_block(part: dict) -> str:
    lines = []
    if part.get("entities"):
        lines.append("Entities so far:")
        lines += [f"- {e.get('id')} | {e.get('name')} | {e.get('type')}" for e in part["entities"]]
    if part.get("observations"):
        lines.append("Observations:\n" + part["observations"])
    return "\n".join(lines) + "\n"


async def _extract_sectioned(vault, sha, pf, skill_text, plan):
    """Sequential per-section extraction with carry-forward, then deterministic merge."""
    parts, carry, cost = [], "", 0.0
    sections = plan["sections"]
    for sec in sections:
        sec_text = (vault / sec["pages_path"]).read_text(encoding="utf-8")
        prompt = prompts.build_section_prompt(
            pages_text=sec_text, existing_entities=pf.get("existing_entities", []),
            skill_text=skill_text, carry_forward=carry, section_label=sec["label"],
            is_first=(sec["index"] == 1), sha256=sha, filename=pf["filename"],
            original_path=pf.get("original_path"), page_count=pf.get("page_count") or len(pf["pages"]),
        )
        r = await model_client.acomplete_json(task="extract-section", prompt=prompt, schema=schemas.SECTION)
        cost += r.cost_usd or 0.0
        parts.append(r.parsed)
        carry += _carry_block(r.parsed)

    extraction = merge.merge_extractions(parts)
    scratchpad = "\n".join(p["observations"] for p in parts if p.get("observations"))
    ok, errors = _write_postflight(vault, sha, extraction)
    return extraction, scratchpad, cost, ok, errors


async def _extract_document(vault: Path, sha: str, brief: str | None) -> dict:
    pf = preflight.run(vault, sha)
    if pf.get("error"):
        return {"sha256": sha, "status": "failed", "reason": pf["error"]}
    if pf.get("already_extracted"):
        return {"sha256": sha, "filename": pf.get("filename"), "status": "skipped"}

    filename = pf["filename"]
    skill_text = _read_skill(vault, await _classify(vault, _pages_text(pf.get("pages", []))[:_CLASSIFY_EXCERPT_CHARS]))

    plan = section.run(vault, sha)
    if plan.get("sectioned"):
        extraction, scratchpad, cost, ok, errors = await _extract_sectioned(vault, sha, pf, skill_text, plan)
    else:
        extraction, scratchpad, cost, ok, errors = await _simple_extract(vault, sha, pf, skill_text, brief)

    if not ok:
        return {"sha256": sha, "filename": filename, "status": "failed",
                "reason": "post-flight rejected: " + "; ".join(errors[:3])}

    if scratchpad:
        (vault / ".watchdog" / "tmp" / f"notes_{sha}.md").write_text(scratchpad, encoding="utf-8")
    (vault / ".watchdog" / "queue" / f"{sha}.json").unlink(missing_ok=True)
    return _compact_result(sha, filename, extraction, pf.get("near_dup", {}), round(cost, 6))


def _lines(items: list) -> str:
    return "\n".join(f"- {x}" for x in items) if items else "_None._"


def _write_briefing(vault: Path, b: dict, results: list, neardup_alerts: list,
                    contradiction_flags: list) -> str:
    now = datetime.datetime.now()
    slug = now.strftime("%Y-%m-%d-%H-%M")
    n_new = len(b.get("new_entities", []))

    body = (
        f"---\ndate: {now.isoformat(timespec='seconds')}\nfiles_ingested: {len(results)}\n"
        f"new_entities: {n_new}\n---\n\n# Ingest briefing — {slug}\n\n"
        f"## What was ingested\n\n{_lines(b.get('what_was_ingested', []))}\n\n"
        f"## New entities\n\n{_lines(b.get('new_entities', []))}\n\n"
        f"## Connections to existing entities\n\n{_lines(b.get('connections', []))}\n\n"
        f"## Leads and follow-up ideas\n\n{_lines(b.get('leads', []))}\n\n"
        f"## Anomalies worth a closer look\n\n"
        f"{_lines(b['anomalies']) if b.get('anomalies') else 'Nothing flagged.'}\n"
    )
    if neardup_alerts:
        body += "\n## Near-duplicate alerts\n\n" + "\n".join(
            f"- {a['filename']}: {a['similarity']:.0%} similar to an existing document"
            for a in neardup_alerts) + "\n"
    (vault / "briefings").mkdir(exist_ok=True)
    (vault / "briefings" / f"{slug}.md").write_text(body, encoding="utf-8")

    (vault / "hot.md").write_text(
        f"# Hot cache\n\n*Last updated: {now.strftime('%Y-%m-%d')} — "
        f"[[briefings/{slug}|Briefing {slug}]]*\n\n"
        f"## Investigation status\n\n{b.get('investigation_status', '')}\n\n"
        f"## Recent additions\n\n{_lines(b.get('new_entities', []))}\n\n"
        f"## Emerging patterns\n\n{_lines(b.get('emerging_patterns', []))}\n\n"
        f"## Open questions\n\n{_lines(b.get('open_questions', []))}\n",
        encoding="utf-8")

    entry = (f"\n## {now.strftime('%Y-%m-%d %H:%M')} — Ingest\n\n"
             f"- **Files:** {len(results)} processed\n- **New entities:** {n_new}\n"
             f"- **Briefing:** [[briefings/{slug}|{slug}]]\n")
    if contradiction_flags:
        entry += f"- **Contradictions flagged:** {len(contradiction_flags)}\n"
    with open(vault / "log.md", "a", encoding="utf-8") as f:
        f.write(entry)
    return f"briefings/{slug}.md"


async def _post_ingest(vault: Path, results: list, brief: str | None) -> dict:
    out: dict = {"synthesized": 0, "timeline_collisions": 0, "briefing": None}

    # 1. Entity synthesis for multi-mention entities (Python builds + applies; model reconciles).
    bundle = synthesis_bundle.build_bundle(vault)
    if bundle.get("entities"):
        r = await model_client.acomplete_json(
            task="entity-synthesis", schema=schemas.SYNTHESIS,
            prompt=prompts.build_synthesis_prompt(bundle))
        res_path = vault / ".watchdog" / "tmp" / "synthesis-result.json"
        res_path.write_text(json.dumps(r.parsed, ensure_ascii=False), encoding="utf-8")
        out["synthesized"] = len(synthesis_bundle.apply_bundle(res_path, vault).get("applied", []))

    # 2. Timeline: promote pending, model-dedup any real collisions, rebuild timeline.md.
    cols = timeline.collisions(vault)
    out["timeline_collisions"] = len(cols)
    for col in cols:
        canonical = vault / col["canonical"]
        events: list[dict] = []
        for p in [canonical, *(vault / r for r in col["raw"])]:
            for line in timeline._read_ndjson_lines(p):
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        if not events:
            continue
        try:
            r = await model_client.acomplete_json(
                task="timeline-dedup", schema=schemas.TIMELINE_DEDUP,
                prompt=prompts.build_timeline_dedup_prompt(col["date"], events))
            kept = r.parsed.get("events") or events
        except model_client.ModelError:
            kept = events   # fall back to the union rather than losing events
        canonical.write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in kept) + "\n", encoding="utf-8")
    timeline.cmd_rebuild_timeline(vault)

    # 3. Briefing + hot.md + log.md (model writes prose; Python writes the files).
    ok = [r for r in results if r.get("status") == "ok"]
    scratchpads = [p.read_text(encoding="utf-8")
                   for p in sorted((vault / ".watchdog" / "tmp").glob("notes_*.md"))]
    neardup_alerts = [{"filename": r["filename"], "similarity": r["near_dup_similarity"]}
                      for r in ok if r.get("near_dup_similarity", 0) >= 0.85]
    contradiction_flags = [{"filename": r["filename"], "entities": r["contradictions"]}
                           for r in ok if r.get("contradictions")]
    try:
        r = await model_client.acomplete_json(
            task="briefing", schema=schemas.BRIEFING,
            prompt=prompts.build_briefing_prompt(
                brief=brief, results=ok, scratchpads=scratchpads,
                neardup_alerts=neardup_alerts, contradiction_flags=contradiction_flags))
        out["briefing"] = _write_briefing(vault, r.parsed, ok, neardup_alerts, contradiction_flags)
    except model_client.ModelError as e:
        out["briefing_error"] = str(e)
    return out


async def run(vault: Path, concurrency: int = DEFAULT_CONCURRENCY) -> dict:
    """Extract every queued document (bounded by `concurrency`), then post-ingest."""
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
    summary = {"results": results, "extracted": by_status("ok"),
               "skipped": by_status("skipped"), "failed": by_status("failed")}
    if summary["extracted"]:
        summary["post_ingest"] = await _post_ingest(vault, results, brief)
    return summary
