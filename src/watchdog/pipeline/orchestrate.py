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
import signal
import sys
from pathlib import Path

from watchdog import model_client
from watchdog.cmd.base import _BOLD, _CYAN, _DIM, _GREEN, _RESET, _YELLOW
from watchdog.pipeline import (
    abort, merge, preflight, postflight, prompts, schemas, section, synthesis_bundle, timeline,
)
from watchdog.pipeline.write_vault import _doc_slug

DEFAULT_CONCURRENCY = 5


def _say(msg: str) -> None:
    """Print a styled progress line to the terminal (indented per the CLI style guide)."""
    print(f"  {msg}", flush=True)
DEFAULT_CLASSIFY_PAGES = 5
# Per-section input budget when falling back to sectioning after a whole-doc extraction
# overruns the output ceiling. Small, so each section's output stays well under the cap;
# section.run caps it at half the document so a splittable doc yields ≥2 sections.
_FALLBACK_SECTION_TOKENS = 15_000
# Safety cap on classifier input: bounds a pathological single huge page; the page
# count (classify_pages) is the primary limiter, so keep this generous.
_CLASSIFY_EXCERPT_CHARS = 24000
# 24-bit RGB ints for Obsidian graph entity-type colour groups.
_GRAPH_PALETTE = [0x4C9A2A, 0xC0392B, 0x2980B9, 0x8E44AD, 0xD35400,
                  0x16A085, 0xF39C12, 0x7F8C8D, 0x2C3E50, 0xC2185B]


def _log(vault: Path, msg: str) -> None:
    log = vault / ".watchdog" / "Registry" / "ingest.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except OSError:
        pass


def _update_graph_colours(vault: Path) -> None:
    """Give each entity-type folder a distinct colour group in Obsidian's graph view."""
    gpath = vault / ".obsidian" / "graph.json"
    edir = vault / "entities"
    if not gpath.exists() or not edir.exists():
        return
    try:
        graph = json.loads(gpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    groups = graph.setdefault("colorGroups", [])
    existing = {g.get("query") for g in groups}
    added = False
    for t in sorted(p.name for p in edir.iterdir() if p.is_dir()):
        query = f"path:entities/{t}"
        if query not in existing:
            groups.append({"query": query,
                           "color": {"a": 1, "rgb": _GRAPH_PALETTE[len(groups) % len(_GRAPH_PALETTE)]}})
            existing.add(query)
            added = True
    if added:
        gpath.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")


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


async def _classify(vault: Path, doc_excerpt: str, model: str) -> str:
    index = _records_dir(vault) / "_index.md"
    index_text = index.read_text(encoding="utf-8") if index.exists() else ""
    r = await model_client.acomplete_json(
        task="classify", model=model, schema=schemas.CLASSIFY,
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
    outcome = postflight.run(vault, tmp, quiet=True)
    return ("errors" not in outcome), outcome.get("errors", [])


async def _simple_extract(vault, sha, pf, skill_text, brief, model):
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
        try:
            r = await model_client.acomplete_json(task="extract", model=model, prompt=p, schema=schemas.EXTRACTION)
        except model_client.ModelError as e:
            # No valid JSON after the client's own retries — often output truncated on a
            # dense doc. Report failure so the caller can fall back to sectioning.
            return extraction, scratchpad, cost, False, [f"extraction returned no valid JSON ({e})"]
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


async def _extract_sectioned(vault, sha, pf, skill_text, plan, model):
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
        r = await model_client.acomplete_json(task="extract-section", model=model,
                                              prompt=prompt, schema=schemas.SECTION)
        cost += r.cost_usd or 0.0
        parts.append(r.parsed)
        carry += _carry_block(r.parsed)

    extraction = merge.merge_extractions(parts)
    scratchpad = "\n".join(p["observations"] for p in parts if p.get("observations"))
    ok, errors = _write_postflight(vault, sha, extraction)
    return extraction, scratchpad, cost, ok, errors


def _fail(vault: Path, sha: str, filename: str, reason: str) -> dict:
    """Log the failure and clean up this doc's artifacts (queue → _failed/) for retry."""
    name = filename or sha[:7]
    _say(f"{_YELLOW}✗{_RESET}  {name}  {_DIM}{reason}{_RESET}")
    _log(vault, f"FAILED {name}: {reason}")
    abort.run(vault, sha)   # removes staging/section temp, moves the queue file to _failed/
    return {"sha256": sha, "filename": filename, "status": "failed", "reason": reason}


async def _extract_document(vault: Path, sha: str, brief: str | None,
                            extract_model: str, classify_model: str,
                            classify_pages: int = DEFAULT_CLASSIFY_PAGES,
                            pinned_skill: str | None = None) -> dict:
    pf = preflight.run(vault, sha)
    if pf.get("error"):
        return _fail(vault, sha, "", pf["error"])
    if pf.get("already_extracted"):
        _say(f"{_DIM}–  {pf.get('filename')}  already extracted — skipping{_RESET}")
        return {"sha256": sha, "filename": pf.get("filename"), "status": "skipped"}

    filename = pf["filename"]
    pages = pf.get("pages", [])
    page_count = pf.get("page_count") or len(pages)
    if pinned_skill:
        # Skill pinned for the whole run — skip per-document classification entirely.
        skill = pinned_skill if pinned_skill.endswith(".md") else f"{pinned_skill}.md"
    else:
        _say(f"{_DIM}→  {filename}  classifying ({page_count} page{'s' if page_count != 1 else ''})…{_RESET}")
        # Classify on the first N pages (page-aware, not a mid-page char cut); the char cap is a guard.
        excerpt = _pages_text(pages[:max(1, classify_pages)])[:_CLASSIFY_EXCERPT_CHARS]
        skill = await _classify(vault, excerpt, classify_model)
    skill_text = _read_skill(vault, skill)
    skill_label = skill.removesuffix(".md")

    plan = section.run(vault, sha)
    if plan.get("sectioned"):
        n_sections = len(plan.get("sections", []))
        _say(f"{_DIM}→  {filename}  extracting · {skill_label} · {n_sections} sections…{_RESET}")
        extraction, scratchpad, cost, ok, errors = await _extract_sectioned(
            vault, sha, pf, skill_text, plan, extract_model)
    else:
        _say(f"{_DIM}→  {filename}  extracting · {skill_label}…{_RESET}")
        extraction, scratchpad, cost, ok, errors = await _simple_extract(
            vault, sha, pf, skill_text, brief, extract_model)
        # Whole-document extraction can overrun the model's output ceiling on entity-dense
        # docs (the agent-SDK backend can't cap output) — the JSON truncates and is rejected.
        # Fall back to the sectioned path, which bounds per-call output, before giving up.
        if not ok and page_count > 1:
            fb = section.run(vault, sha, force_budget=_FALLBACK_SECTION_TOKENS)
            if fb.get("sectioned"):
                n_sections = len(fb.get("sections", []))
                _say(f"{_DIM}↻  {filename}  whole-doc extraction rejected — "
                     f"re-extracting in {n_sections} sections…{_RESET}")
                whole_cost = cost
                extraction, scratchpad, cost, ok, errors = await _extract_sectioned(
                    vault, sha, pf, skill_text, fb, extract_model)
                cost += whole_cost   # account for the failed whole-doc attempt

    if not ok:
        return _fail(vault, sha, filename, "post-flight rejected: " + "; ".join(errors[:3]))

    if scratchpad:
        (vault / ".watchdog" / "tmp" / f"notes_{sha}.md").write_text(scratchpad, encoding="utf-8")
    for stale in (vault / ".watchdog" / "tmp").glob(f"section_{sha}_*.md"):
        stale.unlink(missing_ok=True)
    (vault / ".watchdog" / "queue" / f"{sha}.json").unlink(missing_ok=True)
    n_entities = len(extraction.get("entities", []))
    _say(f"{_GREEN}OK{_RESET}  {filename}  {_DIM}{n_entities} entit{'ies' if n_entities != 1 else 'y'}{_RESET}  "
         f"{_CYAN}documents/{_doc_slug(filename)}{_RESET}")
    _log(vault, f"OK {filename}: {n_entities} entities")
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


async def _post_ingest(vault: Path, results: list, brief: str | None, post_model: str) -> dict:
    out: dict = {"synthesized": 0, "timeline_collisions": 0, "briefing": None}
    print()
    _say(f"{_BOLD}Post-processing{_RESET}")

    # 1. Entity synthesis for multi-mention entities (Python builds + applies; model reconciles).
    bundle = synthesis_bundle.build_bundle(vault)
    if bundle.get("entities"):
        _say(f"{_DIM}→  synthesizing {len(bundle['entities'])} multi-mention "
             f"entit{'ies' if len(bundle['entities']) != 1 else 'y'}…{_RESET}")
        r = await model_client.acomplete_json(
            task="entity-synthesis", model=post_model, schema=schemas.SYNTHESIS,
            prompt=prompts.build_synthesis_prompt(bundle))
        res_path = vault / ".watchdog" / "tmp" / "synthesis-result.json"
        res_path.write_text(json.dumps(r.parsed, ensure_ascii=False), encoding="utf-8")
        out["synthesized"] = len(synthesis_bundle.apply_bundle(res_path, vault).get("applied", []))

    # 2. Timeline: promote pending, model-dedup any real collisions, rebuild timeline.md.
    _say(f"{_DIM}→  rebuilding timeline…{_RESET}")
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
                task="timeline-dedup", model=post_model, schema=schemas.TIMELINE_DEDUP,
                prompt=prompts.build_timeline_dedup_prompt(col["date"], events))
            kept = r.parsed.get("events") or events
        except model_client.ModelError:
            kept = events   # fall back to the union rather than losing events
        canonical.write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in kept) + "\n", encoding="utf-8")
    n_dates, n_events = timeline.cmd_rebuild_timeline(vault, quiet=True)
    _say(f"{_DIM}   timeline.md · {n_dates} date{'s' if n_dates != 1 else ''}, "
         f"{n_events} event{'s' if n_events != 1 else ''}{_RESET}")

    # 3. Briefing + hot.md + log.md (model writes prose; Python writes the files).
    _say(f"{_DIM}→  writing briefing…{_RESET}")
    ok = [r for r in results if r.get("status") == "ok"]
    scratchpads = [p.read_text(encoding="utf-8")
                   for p in sorted((vault / ".watchdog" / "tmp").glob("notes_*.md"))]
    neardup_alerts = [{"filename": r["filename"], "similarity": r["near_dup_similarity"]}
                      for r in ok if r.get("near_dup_similarity", 0) >= 0.85]
    contradiction_flags = [{"filename": r["filename"], "entities": r["contradictions"]}
                           for r in ok if r.get("contradictions")]
    try:
        r = await model_client.acomplete_json(
            task="briefing", model=post_model, schema=schemas.BRIEFING,
            prompt=prompts.build_briefing_prompt(
                brief=brief, results=ok, scratchpads=scratchpads,
                neardup_alerts=neardup_alerts, contradiction_flags=contradiction_flags))
        out["briefing"] = _write_briefing(vault, r.parsed, ok, neardup_alerts, contradiction_flags)
    except model_client.ModelError as e:
        out["briefing_error"] = str(e)
    return out


async def run(vault: Path, *, concurrency: int = DEFAULT_CONCURRENCY,
              extract_model: str = "sonnet", post_model: str = "sonnet",
              classify_model: str = "haiku",
              classify_pages: int = DEFAULT_CLASSIFY_PAGES,
              pinned_skill: str | None = None) -> dict:
    """Extract every queued document (bounded by `concurrency`), then post-ingest.

    `extract_model` drives extraction (whole-doc + section); `post_model` drives
    synthesis/timeline/briefing; `classify_model` the cheap document classifier,
    which sees the first `classify_pages` pages of each document. `pinned_skill`
    (a record-skill name) skips classification and uses that skill for every document.
    """
    queue_dir = vault / ".watchdog" / "queue"
    shas = [f.stem for f in sorted(queue_dir.glob("*.json"))] if queue_dir.exists() else []
    if not shas:
        return {"results": [], "extracted": 0, "skipped": 0, "failed": 0}

    brief = _read_brief(vault)
    sem = asyncio.Semaphore(max(1, concurrency))
    cancelled = asyncio.Event()

    async def _guarded(sha: str) -> dict:
        if cancelled.is_set():                       # never started — leave queue file for resume
            return {"sha256": sha, "filename": "", "status": "cancelled"}
        async with sem:
            if cancelled.is_set():
                return {"sha256": sha, "filename": "", "status": "cancelled"}
            try:
                return await _extract_document(vault, sha, brief, extract_model, classify_model,
                                               classify_pages, pinned_skill)
            except asyncio.CancelledError:           # ctrl+c mid-document — queue file stays
                return {"sha256": sha, "filename": "", "status": "cancelled"}
            except Exception as e:                   # one bad doc must not sink the batch
                return _fail(vault, sha, "", f"unexpected error: {e}")

    # On ctrl+c, cancel in-flight work once and shut down cleanly instead of letting
    # KeyboardInterrupt tear through the event loop with a traceback. Finished documents
    # are already written to the vault; unfinished ones keep their queue file for resume.
    tasks = [asyncio.ensure_future(_guarded(s)) for s in shas]

    def _on_interrupt() -> None:
        if cancelled.is_set():
            return
        cancelled.set()
        print()
        _say(f"{_YELLOW}Interrupted{_RESET}{_DIM} — finishing current writes, then stopping…{_RESET}")
        for t in tasks:
            t.cancel()

    loop = asyncio.get_running_loop()
    handler_set = False
    try:
        loop.add_signal_handler(signal.SIGINT, _on_interrupt)
        handler_set = True
    except (NotImplementedError, RuntimeError):       # e.g. non-main thread / unsupported platform
        pass
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        if handler_set:
            loop.remove_signal_handler(signal.SIGINT)

    results = [t.result() for t in tasks if t.done() and not t.cancelled()]
    by_status = lambda s: sum(1 for r in results if r.get("status") == s)
    summary = {"results": results, "extracted": by_status("ok"),
               "skipped": by_status("skipped"), "failed": by_status("failed"),
               "cancelled": cancelled.is_set()}
    if summary["extracted"] and not cancelled.is_set():
        summary["post_ingest"] = await _post_ingest(vault, results, brief, post_model)
        _update_graph_colours(vault)
    _log(vault, f"INGEST {'cancelled' if cancelled.is_set() else 'complete'} — "
                f"{summary['extracted']} extracted, {summary['skipped']} skipped, "
                f"{summary['failed']} failed")
    return summary
