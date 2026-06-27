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
import shutil
import signal
import sys
from pathlib import Path

import yaml

from watchdog import model_client, skills_catalog
from watchdog.cmd.base import _BOLD, _CYAN, _DIM, _GREEN, _RESET, _YELLOW
from watchdog.cmd.live import LiveRegion
from watchdog.pipeline import (
    abort, merge, preflight, postflight, prompts, schemas, section, synthesis_bundle, timeline,
    watchlist,
)
from watchdog.pipeline.write_vault import _doc_slug

DEFAULT_CONCURRENCY = 5


# During extraction this holds the live status region (#151); per-document rows redraw in
# place and finished/failed lines + notes scroll above it. None outside extraction (and when
# stdout isn't a TTY), so `_say` falls back to plain append-only printing.
_board: LiveRegion | None = None


def _say(msg: str) -> None:
    """Print a styled progress line to the terminal (indented per the CLI style guide).
    Routes through the live region as a scrollback note when one is active."""
    line = f"  {msg}"
    if _board is not None:
        _board.note(line)
    else:
        print(line, flush=True)


def _settle(sha: str, line: str) -> None:
    """Print a document's terminal line (OK / ✗), clearing its in-flight live row if present."""
    if _board is not None:
        _board.finish(sha, line)
    else:
        print(line, flush=True)
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


def _read_brief(vault: Path) -> str | None:
    p = vault / "context.md"
    return p.read_text(encoding="utf-8") if p.exists() else None


def _pages_text(pages: list[dict]) -> str:
    return "\n\n---\n\n".join(
        f"<!-- PAGE {pg['page']} -->\n\n{pg.get('markdown', '')}" for pg in pages
    )


def _read_sidecar(vault: Path, filename: str) -> str | None:
    sc = vault / "_INCOMING" / f"{filename}.yml"
    return sc.read_text(encoding="utf-8") if sc.exists() else None


def _sidecar_provenance(vault: Path, filename: str) -> dict:
    """Parse `source`/`obtained` from the document's `.yml` sidecar — deterministically, in
    Python, rather than passing the sidecar text through the model and reading the fields back
    out of its response. The sidecar still reaches the model as extraction context (its `notes`)."""
    text = _read_sidecar(vault, filename)
    if not text:
        return {}
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    # str() coerces YAML's auto-parsed scalars (e.g. `obtained: 2026-06-05` → a date) back to text.
    return {k: str(data[k]) for k in ("source", "obtained") if data.get(k) is not None}


def _stamp_document(extraction: dict, *, sha: str, pf: dict, skill_label: str, vault: Path) -> None:
    """Stamp the deterministic, Python-owned document fields onto the extraction, before
    post-flight consumes it. Identity (sha256/filename/original_path/page_count) and provenance
    (source/obtained) are values the pipeline already holds — the model is no longer asked to
    echo them, so we set the authoritative values here. Stamping the sha in particular removes a
    latent failure mode: write_vault keys the vault write on `document.sha256`, so a model
    mis-transcription of the 64-char hash would desync the write from the queue/registry."""
    from watchdog.pipeline.write_vault import slugify
    doc = extraction.setdefault("document", {})
    doc["record_skill"] = skill_label
    doc["sha256"] = sha
    doc["filename"] = pf["filename"]
    doc["original_path"] = pf.get("original_path")
    doc["page_count"] = pf.get("page_count") or len(pf["pages"])
    doc.update(_sidecar_provenance(vault, pf["filename"]))
    # morgue_document_type is just the slug form of document_type — derive it deterministically
    # rather than asking the model for the same fact twice (it names the morgue folder).
    extraction["morgue_document_type"] = slugify(doc.get("document_type") or "") or "document"


async def _classify(doc_excerpt: str, model: str) -> str:
    r = await model_client.acomplete_json(
        task="classify", model=model, schema=schemas.CLASSIFY,
        prompt=prompts.build_classify_prompt(doc_excerpt, skills_catalog.build_index()),
    )
    return r.parsed.get("skill") or "general-records.md"


# Page-coverage heuristic (skim detection). Advisory only — emits a warning, never a failure.
_COVERAGE_MIN_PAGES = 8         # don't flag short documents
_COVERAGE_TAIL_FRACTION = 0.5   # flag when nothing is cited past roughly the first half


def _coverage_warning(extraction: dict, page_count: int | None) -> str | None:
    """Flag a possible skim: when a multi-page document's facts are front-loaded — nothing cited
    past roughly the first half — the model likely stopped reading. A heuristic, deterministic
    signal for review, not a hard check: a doc whose material genuinely sits up front trips it too.
    Facts carry an optional `page`; a doc with no page anchors at all can't be assessed."""
    if not page_count or page_count < _COVERAGE_MIN_PAGES:
        return None
    facts = extraction.get("document", {}).get("key_facts", [])
    cited = sorted({f["page"] for f in facts
                    if isinstance(f, dict) and isinstance(f.get("page"), int)
                    and not isinstance(f.get("page"), bool)})
    if not cited:
        return None
    max_cited = cited[-1]
    if max_cited >= page_count * _COVERAGE_TAIL_FRACTION:
        return None
    return (f"facts were only extracted from pages {cited[0]}–{max_cited} of {page_count} — the "
            f"model may have stopped reading early; check pages {max_cited + 1}–{page_count} "
            f"of the source for anything missed")


def _briefing_facts(doc: dict) -> list[dict]:
    """Project key_facts down to what the briefing needs — the fact text and, when the fact is a
    datable occurrence, its date (for chronology). Drops page/basis/entities/quote, which are
    noise for narrative briefing. This is what now supplies the briefing its figures and timeline,
    in place of the scratchpad's hand-retyped 'Key figures'/'Chronological' sections (#150)."""
    out = []
    for f in doc.get("key_facts", []):
        item = {"fact": f["fact"]}
        if f.get("date"):
            item["date"] = f["date"]
        out.append(item)
    return out


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
        "key_facts": _briefing_facts(doc),
        "near_dup_similarity": near_dup.get("top_similarity", 0.0),
        "cost_usd": cost,
    }


def _write_postflight(vault: Path, sha: str, extraction: dict) -> tuple[bool, list[str]]:
    tmp = vault / ".watchdog" / "tmp" / f"wdg_ex_{sha}.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(extraction, ensure_ascii=False, indent=2), encoding="utf-8")
    outcome = postflight.run(vault, tmp, quiet=True)
    return ("errors" not in outcome), outcome.get("errors", [])


async def _simple_extract(vault, sha, pf, skill_text, brief, model, skill_label):
    """Whole-document extraction, with one repair attempt if post-flight rejects."""
    base = prompts.build_extract_prompt(
        pages_text=_pages_text(pf["pages"]), existing_entities=pf.get("existing_entities", []),
        skill_text=skill_text, sidecar=_read_sidecar(vault, pf["filename"]), brief=brief,
        known_document_types=pf.get("known_document_types", []),
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
        _stamp_document(extraction, sha=sha, pf=pf, skill_label=skill_label, vault=vault)
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


async def _extract_sectioned(vault, sha, pf, skill_text, plan, model, skill_label):
    """Sequential per-section extraction with carry-forward, then deterministic merge."""
    parts, carry, cost = [], "", 0.0
    sections = plan["sections"]
    for sec in sections:
        sec_text = (vault / sec["pages_path"]).read_text(encoding="utf-8")
        prompt = prompts.build_section_prompt(
            pages_text=sec_text, existing_entities=pf.get("existing_entities", []),
            skill_text=skill_text, carry_forward=carry, section_label=sec["label"],
            is_first=(sec["index"] == 1),
            known_document_types=pf.get("known_document_types", []),
        )
        r = await model_client.acomplete_json(task="extract-section", model=model,
                                              prompt=prompt, schema=schemas.SECTION)
        cost += r.cost_usd or 0.0
        parts.append(r.parsed)
        carry += _carry_block(r.parsed)

    extraction = merge.merge_extractions(parts)
    scratchpad = "\n".join(p["observations"] for p in parts if p.get("observations"))
    _stamp_document(extraction, sha=sha, pf=pf, skill_label=skill_label, vault=vault)
    ok, errors = _write_postflight(vault, sha, extraction)
    return extraction, scratchpad, cost, ok, errors


def _queued_filename(vault: Path, sha: str) -> str | None:
    """Best-effort filename for a sha from its queue descriptor (active or _failed/)."""
    for sub in ("", "_failed"):
        p = vault / ".watchdog" / "queue" / sub / f"{sha}.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8")).get("filename")
            except (OSError, json.JSONDecodeError):
                return None
    return None


def _fail(vault: Path, sha: str, filename: str, reason: str) -> dict:
    """Log the failure and clean up this doc's artifacts (queue → _failed/) for retry."""
    # Resolve the filename (the catch-all path has only the sha) *before* abort.run moves
    # the queue file, so the ✗ line and log name the file instead of a bare sha.
    name = filename or _queued_filename(vault, sha) or sha[:7]
    _settle(sha, f"  {_YELLOW}✗{_RESET}  {name}  {_DIM}{reason}{_RESET}")
    _log(vault, f"FAILED {name}: {reason}")
    abort.run(vault, sha)   # removes staging/section temp, moves the queue file to _failed/
    return {"sha256": sha, "filename": filename or name, "status": "failed", "reason": reason}


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
    pg = f"{page_count}p"

    def _step(tty: str, plain: str) -> None:
        """Mutate this document's single in-flight live row (TTY); append the plain transition
        line when there's no live region (non-TTY) — keeping logged output unchanged."""
        if _board is not None:
            _board.update(sha, f"  {tty}", f"  {plain}")
        else:
            _say(plain)

    if pinned_skill:
        # Skill pinned for the whole run (a resolved skill-file path) — skip classification.
        skill_text = Path(pinned_skill).read_text(encoding="utf-8")
        skill_label = Path(pinned_skill).stem
    else:
        _step(f"{_DIM}→  {filename}  {pg} · classifying…{_RESET}",
              f"{_DIM}→  {filename}  classifying ({page_count} page{'s' if page_count != 1 else ''})…{_RESET}")
        # Classify on the first N pages (page-aware, not a mid-page char cut); the char cap is a guard.
        excerpt = _pages_text(pages[:max(1, classify_pages)])[:_CLASSIFY_EXCERPT_CHARS]
        skill = await _classify(excerpt, classify_model)
        skill_text = skills_catalog.read_skill(skill)
        skill_label = skill.removesuffix(".md")
        _step(f"{_DIM}→  {filename}  {pg} · {skill_label}{_RESET}",
              f"{_DIM}·  {filename}  classified ·{_RESET} {_CYAN}{skill_label}{_RESET}")

    flow = f"{pg} · {skill_label}"        # the accumulated in-flight prefix for this document's row

    plan = section.run(vault, sha)
    if plan.get("sectioned"):
        n_sections = len(plan.get("sections", []))
        _step(f"{_DIM}→  {filename}  {flow} · extracting · {n_sections} sections…{_RESET}",
              f"{_DIM}→  {filename}  extracting · {n_sections} sections…{_RESET}")
        extraction, scratchpad, cost, ok, errors = await _extract_sectioned(
            vault, sha, pf, skill_text, plan, extract_model, skill_label)
    else:
        _step(f"{_DIM}→  {filename}  {flow} · extracting…{_RESET}",
              f"{_DIM}→  {filename}  extracting…{_RESET}")
        extraction, scratchpad, cost, ok, errors = await _simple_extract(
            vault, sha, pf, skill_text, brief, extract_model, skill_label)
        # Whole-document extraction can overrun the model's output ceiling on entity-dense
        # docs (the agent-SDK backend can't cap output) — the JSON truncates and is rejected.
        # Fall back to the sectioned path, which bounds per-call output, before giving up.
        if not ok and page_count > 1:
            fb = section.run(vault, sha, force_budget=_FALLBACK_SECTION_TOKENS)
            if fb.get("sectioned"):
                n_sections = len(fb.get("sections", []))
                _step(f"{_DIM}↻  {filename}  {flow} · re-extracting in {n_sections} sections…{_RESET}",
                      f"{_DIM}↻  {filename}  whole-doc extraction rejected — "
                      f"re-extracting in {n_sections} sections…{_RESET}")
                whole_cost = cost
                extraction, scratchpad, cost, ok, errors = await _extract_sectioned(
                    vault, sha, pf, skill_text, fb, extract_model, skill_label)
                cost += whole_cost   # account for the failed whole-doc attempt

    if not ok:
        return _fail(vault, sha, filename, "post-flight rejected: " + "; ".join(errors[:3]))

    if scratchpad:
        (vault / ".watchdog" / "tmp" / f"notes_{sha}.md").write_text(scratchpad, encoding="utf-8")
    for stale in (vault / ".watchdog" / "tmp").glob(f"section_{sha}_*.md"):
        stale.unlink(missing_ok=True)
    (vault / ".watchdog" / "queue" / f"{sha}.json").unlink(missing_ok=True)
    n_entities = len(extraction.get("entities", []))
    _settle(sha, f"  {_GREEN}OK{_RESET}  {filename}  "
            f"{_DIM}{n_entities} entit{'ies' if n_entities != 1 else 'y'}{_RESET}  "
            f"{_CYAN}documents/{_doc_slug(filename)}{_RESET}")
    _log(vault, f"OK {filename}: {n_entities} entities")
    warn = _coverage_warning(extraction, page_count)
    if warn:
        _say(f"{_YELLOW}⚠{_RESET}  {filename}  {_DIM}{warn}{_RESET}")
        _log(vault, f"WARN {filename}: {warn}")
    result = _compact_result(sha, filename, extraction, pf.get("near_dup", {}), round(cost, 6))
    # Persist the compact result so `watchdog finalize` can run post-ingest from disk alone.
    (vault / ".watchdog" / "tmp" / f"result_{sha}.json").write_text(
        json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


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


def _select_kept(events: list[dict], keep) -> list[dict]:
    """Map the timeline-dedup model's kept-indices back to the original event objects (deduped,
    in order). Falls back to all events when the model returns nothing usable — dedup must never
    lose events. The originals carry the authoritative page/basis/source_sha256."""
    if not isinstance(keep, list):
        return events
    seen: set[int] = set()
    kept: list[dict] = []
    for i in keep:
        if isinstance(i, bool):
            continue
        if isinstance(i, int) and 0 <= i < len(events) and i not in seen:
            seen.add(i)
            kept.append(events[i])
    return kept or events


async def _post_ingest(vault: Path, results: list, brief: str | None, post_model: str) -> dict:
    out: dict = {"synthesized": 0, "timeline_collisions": 0, "briefing": None}
    print()
    _say(f"{_BOLD}Post-processing{_RESET}")

    # 1. Entity synthesis for multi-mention entities (Python builds + applies; model reconciles).
    bundle = synthesis_bundle.build_bundle(vault)
    if bundle.get("entities"):
        _say(f"{_DIM}→  synthesizing {len(bundle['entities'])} multi-mention "
             f"entit{'ies' if len(bundle['entities']) != 1 else 'y'}…{_RESET}")
        try:
            r = await model_client.acomplete_json(
                task="entity-synthesis", model=post_model, schema=schemas.SYNTHESIS,
                prompt=prompts.build_synthesis_prompt(bundle))
        except (model_client.ModelError, model_client.RateLimitError) as e:
            # Synthesis is enrichment: leave the structured claims already in the notes
            # rather than crashing. The fragment queue persists, so a later ingest redoes it.
            out["error"] = str(e)
            _say(f"{_YELLOW}synthesis skipped{_RESET}{_DIM} — {e}{_RESET}")
        else:
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
            kept = _select_kept(events, r.parsed.get("keep"))
        except (model_client.ModelError, model_client.RateLimitError):
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
    except (model_client.ModelError, model_client.RateLimitError) as e:
        out["briefing_error"] = str(e)

    # 4. Watch-word scan (deterministic, no model; #165). Scans this run's documents against
    # the vault-root watchlist.md and writes briefings/alerts-<date>.md. No-op if the list is empty.
    hits = watchlist.scan(vault, results)
    alerts = watchlist.write_alerts(vault, hits)
    if alerts:
        relpath, n_terms, n_docs = alerts
        out["watchlist_alerts"] = relpath
        _say(f"{_YELLOW}⚠{_RESET}  {_BOLD}{len(hits)}{_RESET} watch-word match"
             f"{'es' if len(hits) != 1 else ''} {_DIM}({n_terms} term"
             f"{'s' if n_terms != 1 else ''} in {n_docs} document"
             f"{'s' if n_docs != 1 else ''}){_RESET} — {_CYAN}{relpath}{_RESET}")
    return out


def _load_results(vault: Path) -> list:
    """Load persisted per-doc results (used by a standalone finalize run)."""
    out = []
    for p in sorted((vault / ".watchdog" / "tmp").glob("result_*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    return out


def _clear_post_ingest_inputs(vault: Path) -> None:
    """Remove the per-run post-ingest inputs once they have been finalized cleanly."""
    tmp = vault / ".watchdog" / "tmp"
    shutil.rmtree(tmp / "entity-fragments", ignore_errors=True)
    for p in list(tmp.glob("result_*.json")) + list(tmp.glob("notes_*.md")):
        p.unlink(missing_ok=True)


def has_pending_finalization(vault: Path) -> bool:
    """True if an extracted-but-not-finalized batch is sitting in tmp (e.g. a rate-limited run)."""
    tmp = vault / ".watchdog" / "tmp"
    return ((tmp / "entity-fragments" / "_queue.json").exists()
            or any(tmp.glob("result_*.json")))


def pending_finalization(vault: Path) -> dict:
    """Best-effort counts for an extracted-but-not-finalized batch sitting in tmp."""
    tmp = vault / ".watchdog" / "tmp"
    docs = len(list(tmp.glob("result_*.json")))
    entities = 0
    q = tmp / "entity-fragments" / "_queue.json"
    if q.exists():
        try:
            entities = sum(1 for r in json.loads(q.read_text(encoding="utf-8")).values()
                           if isinstance(r, dict) and r.get("count", 0) >= 2)
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    return {"docs": docs, "entities": entities}


async def finalize(vault: Path, *, post_model: str = "haiku", brief: str | None = None,
                   results: list | None = None) -> dict:
    """Run (or re-run) post-ingest over the current on-disk state: synthesize multi-mention
    entities, reconcile the timeline, and write the briefing/hot.md/log.

    Called at the tail of every ``watchdog ingest`` (with this run's results in memory) and
    standalone by ``watchdog finalize`` (reading persisted ``result_*.json``) to complete a
    post-ingest an earlier rate limit or interrupt left unfinished. On a clean pass the consumed
    inputs (fragments, results, scratchpads) are cleared; if any step failed they are left in
    place so a later finalize can retry.
    """
    if brief is None:
        brief = _read_brief(vault)
    if results is None:
        results = _load_results(vault)
    out = await _post_ingest(vault, results, brief, post_model)
    if not out.get("error") and not out.get("briefing_error"):
        _clear_post_ingest_inputs(vault)
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
    (a path to a skill file) skips classification and uses that skill for every document.
    """
    queue_dir = vault / ".watchdog" / "queue"
    shas = [f.stem for f in sorted(queue_dir.glob("*.json"))] if queue_dir.exists() else []
    if not shas:
        return {"results": [], "extracted": 0, "skipped": 0, "failed": 0}

    brief = _read_brief(vault)
    # Live status region for the concurrent extraction phase (#151): one in-place row per
    # in-flight document, finished/failed lines scrolling above. Auto-disables off a TTY,
    # where it degrades to the previous append-only output.
    global _board
    _board = LiveRegion()
    sem = asyncio.Semaphore(max(1, concurrency))
    cancelled = asyncio.Event()
    stop_reason: dict = {}          # {"rate_limit": "<notice>"} when a limit stopped the batch
    tasks: list = []

    def _request_stop(rate_limit: str | None = None) -> None:
        """Stop the batch once: flag it, record why, cancel in-flight work. Idempotent."""
        if cancelled.is_set():
            return
        cancelled.set()
        if rate_limit:
            stop_reason["rate_limit"] = rate_limit
        for t in tasks:
            t.cancel()

    async def _guarded(sha: str) -> dict:
        if cancelled.is_set():                       # never started — leave queue file for resume
            return {"sha256": sha, "filename": "", "status": "cancelled"}
        async with sem:
            if cancelled.is_set():
                return {"sha256": sha, "filename": "", "status": "cancelled"}
            try:
                return await _extract_document(vault, sha, brief, extract_model, classify_model,
                                               classify_pages, pinned_skill)
            except model_client.RateLimitError as e:  # session-wide — stop, leave queued for resume
                if not cancelled.is_set():
                    print()
                    _say(f"{_YELLOW}Rate limit reached{_RESET}{_DIM} — {e}{_RESET}")
                    _say(f"{_DIM}Stopping; finished documents are saved. Re-run "
                         f"{_RESET}{_CYAN}watchdog ingest{_RESET}{_DIM} once it resets to continue.{_RESET}")
                    _request_stop(rate_limit=str(e))
                return {"sha256": sha, "filename": "", "status": "cancelled"}
            except asyncio.CancelledError:           # ctrl+c mid-document — queue file stays
                return {"sha256": sha, "filename": "", "status": "cancelled"}
            except Exception as e:                   # one bad doc must not sink the batch
                return _fail(vault, sha, "", f"unexpected error: {e}")

    # On ctrl+c, cancel in-flight work once and shut down cleanly instead of letting
    # KeyboardInterrupt tear through the event loop with a traceback. Finished documents
    # are already written to the vault; unfinished ones keep their queue file for resume.
    tasks[:] = [asyncio.ensure_future(_guarded(s)) for s in shas]

    def _on_interrupt() -> None:
        if cancelled.is_set():
            return
        print()
        _say(f"{_YELLOW}Interrupted{_RESET}{_DIM} — finishing current writes, then stopping…{_RESET}")
        _request_stop()

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
        # Close the live region: extraction is done, so post-processing (sequential) and the
        # summary print plainly. _say falls back to plain printing once _board is None.
        _board.stop()
        _board = None

    results = [t.result() for t in tasks if t.done() and not t.cancelled()]
    by_status = lambda s: sum(1 for r in results if r.get("status") == s)
    failed_dir = vault / ".watchdog" / "queue" / "_failed"
    quarantined = len(list(failed_dir.glob("*.json"))) if failed_dir.exists() else 0
    summary = {"results": results, "extracted": by_status("ok"),
               "skipped": by_status("skipped"), "failed": by_status("failed"),
               "cancelled": cancelled.is_set(),
               "rate_limited": bool(stop_reason.get("rate_limit")),
               "stop_message": stop_reason.get("rate_limit"),
               "quarantined": quarantined}
    if summary["extracted"] and not cancelled.is_set():
        try:
            # Finalize over the persisted per-doc results on disk (not just this run's in-memory
            # ones) so a merged batch — a prior pending run kept via wipe_pending=False — is
            # synthesized and briefed together with this run's documents.
            summary["post_ingest"] = await finalize(vault, post_model=post_model, brief=brief)
            _update_graph_colours(vault)
        except Exception as e:   # post-ingest is enrichment — never let it crash a saved batch
            summary["post_ingest_error"] = str(e)
            print()
            _say(f"{_YELLOW}Post-processing incomplete{_RESET}{_DIM} — {e}{_RESET}")
            _say(f"{_DIM}Your {summary['extracted']} extracted document"
                 f"{'s are' if summary['extracted'] != 1 else ' is'} saved.{_RESET}")
    state = "rate-limited" if summary["rate_limited"] else ("cancelled" if cancelled.is_set() else "complete")
    _log(vault, f"INGEST {state} — {summary['extracted']} extracted, "
                f"{summary['skipped']} skipped, {summary['failed']} failed")
    return summary
