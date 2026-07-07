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
import hashlib
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
    abort, batch_extract, leads, merge, preflight, postflight, prompts, schemas, section,
    synthesis_bundle, timeline, watchlist,
)
from watchdog.pipeline.write_vault import _doc_slug

DEFAULT_CONCURRENCY = 5


# During extraction this holds the live status region (#151); per-document rows redraw in
# place and finished/failed lines + notes scroll above it. None outside extraction (and when
# stdout isn't a TTY), so `_say` falls back to plain append-only printing.
_board: LiveRegion | None = None

# Per-call token/cost telemetry for the current run (A2) — a list of dicts, one per successful
# model call, accumulated by `_call_model`. None outside a `run`/standalone `finalize` call, so
# unit tests that exercise the per-document helpers directly (without going through either) don't
# need to know about it. `ModelResult.usage`/`cost_usd` were previously computed and discarded —
# this is the prerequisite for answering "how many tokens did this ingest spend, by stage?"
# without spelunking Claude Code session logs.
_usage: list[dict] | None = None


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


def _record_usage(task: str, *, model: str, backend: str, usage: dict | None,
                  cost_usd: float | None, attempts: int = 1, latency_s: float = 0.0,
                  effort: str | None = None, auth_mode: str | None = None,
                  filename: str | None = None, detail: str | None = None) -> None:
    """Append one call's usage to the run-scoped `_usage` accumulator, if one is active.
    Tolerates both Anthropic-style (`input_tokens`/`output_tokens`) and OpenAI-compatible
    (`prompt_tokens`/`completion_tokens`) usage dicts (D37) — the latter's cache-token fields
    aren't modelled yet (matches `model_client._openai_cost`'s own v1 simplification).

    Takes explicit fields rather than a `model_client.ModelResult` so a batch-collected item
    (D52, no live model call at this point) can feed it too, not just `_call_model` (D64).
    `filename`/`detail` (e.g. a page range or section label) let a per-run usage file attribute
    each call to a document, not just a task name (#247). `latency_s` is per-call wall-clock
    time; batch-collected items have no live call to time, so they keep the 0.0 default (#317).
    `effort` records the reasoning-effort tier the call was made with (or None if the model/task
    doesn't take one), and `auth_mode` ("subscription"/"api-key") which billing lane paid for it
    — both surfaced per call by `watchdog usage` (#319)."""
    if _usage is None:
        return
    u = usage or {}
    _usage.append({
        "task": task, "model": model, "backend": backend,
        "input_tokens": u.get("input_tokens", u.get("prompt_tokens", 0)) or 0,
        "output_tokens": u.get("output_tokens", u.get("completion_tokens", 0)) or 0,
        "cache_read_tokens": u.get("cache_read_input_tokens", 0) or 0,
        "cache_write_tokens": u.get("cache_creation_input_tokens", 0) or 0,
        "cost_usd": cost_usd, "attempts": attempts, "latency_s": latency_s, "effort": effort,
        "auth_mode": auth_mode, "filename": filename, "detail": detail,
    })


async def _call_model(*, task, prompt, schema, model=None, backend=None,
                      max_retries=1, effort=None, filename=None, detail=None) -> "model_client.ModelResult":
    """Thin wrapper around `model_client.acomplete_json` that also records this call's usage
    (A2) — every reasoning call in the orchestrator goes through here instead of the client
    directly, so telemetry can't silently miss a call site. `filename`/`detail` are passed
    through to `_record_usage` unchanged (#247)."""
    r = await model_client.acomplete_json(task=task, prompt=prompt, schema=schema, model=model,
                                          backend=backend, max_retries=max_retries, effort=effort)
    _record_usage(task, model=r.model, backend=r.backend, usage=r.usage, cost_usd=r.cost_usd,
                 attempts=r.attempts, latency_s=r.latency_s, effort=effort, auth_mode=r.auth_mode,
                 filename=filename, detail=detail)
    return r


def _usage_totals(records: list[dict]) -> dict:
    return {
        "input_tokens": sum(r["input_tokens"] for r in records),
        "output_tokens": sum(r["output_tokens"] for r in records),
        "cache_read_tokens": sum(r["cache_read_tokens"] for r in records),
        "cache_write_tokens": sum(r["cache_write_tokens"] for r in records),
        "cost_usd": round(sum(r["cost_usd"] or 0.0 for r in records), 6) if records else None,
        "latency_s": round(sum(r.get("latency_s") or 0.0 for r in records), 3),
    }


def usage_files(vault: Path) -> list[Path]:
    """Every persisted `usage-<ts>.json` for this vault, oldest first (#319). Checks both the
    current location (`.watchdog/Registry/usage/`) and the pre-move flat location
    (`.watchdog/Registry/`) directly, so a vault ingested before that reorganization doesn't
    lose its older history — filenames sort chronologically regardless of which directory
    they're in, so the two sets merge correctly once combined."""
    reg_dir = vault / ".watchdog" / "Registry"
    usage_dir = reg_dir / "usage"
    files = list(usage_dir.glob("usage-*.json")) if usage_dir.exists() else []
    files += list(reg_dir.glob("usage-*.json")) if reg_dir.exists() else []   # legacy location
    return sorted(files, key=lambda p: p.name)


def _write_usage(vault: Path, records: list[dict]) -> str | None:
    """Persist this run's per-call token/cost telemetry to
    `.watchdog/Registry/usage/usage-<ts>.json` (A2, relocated out of the flat Registry dir
    in #319 since this one accumulates a new file every run, unlike the fixed-size registries
    it used to sit alongside). Returns the vault-relative path, or None if the run made no
    model calls (e.g. an all-skipped batch)."""
    if not records:
        return None
    usage_dir = vault / ".watchdog" / "Registry" / "usage"
    usage_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    relpath = f".watchdog/Registry/usage/usage-{ts}.json"
    (vault / relpath).write_text(
        json.dumps({"calls": records, "totals": _usage_totals(records)}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return relpath


def latest_usage(vault: Path) -> dict | None:
    """The most recent ingest run's token/cost totals (F5, #222), or None if none exist yet —
    `watchdog status` surfaces this so a subscription user can see what a dump cost before
    deciding whether to kick off another one."""
    files = usage_files(vault)
    if not files:
        return None
    try:
        data = json.loads(files[-1].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data.get("totals")


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


def _stamp_document(extraction: dict, *, sha: str, pf: dict, skill_label: str, vault: Path,
                    skill_text: str | None = None, extract_model: str | None = None,
                    extract_effort: str | None = None) -> None:
    """Stamp the deterministic, Python-owned document fields onto the extraction, before
    post-flight consumes it. Identity (sha256/filename/original_path/page_count) and provenance
    (source/obtained) are values the pipeline already holds — the model is no longer asked to
    echo them, so we set the authoritative values here. Stamping the sha in particular removes a
    latent failure mode: write_vault keys the vault write on `document.sha256`, so a model
    mis-transcription of the 64-char hash would desync the write from the queue/registry.

    `record_skill_hash`/`extract_model`/`extract_effort` (#268) are extraction provenance
    alongside `record_skill`: the skill's *content* can change (#68) without its filename
    changing, and per-run usage (D50) records model/effort per call but not per document — so
    without these, a vault can't answer "what produced this extraction" once skills or model
    config move on."""
    from watchdog.pipeline.write_vault import slugify
    doc = extraction.setdefault("document", {})
    doc["record_skill"] = skill_label
    doc["record_skill_hash"] = (
        hashlib.sha256(skill_text.encode("utf-8")).hexdigest()[:12] if skill_text else None)
    doc["extract_model"] = model_client.resolve_model_id(extract_model) if extract_model else None
    doc["extract_effort"] = extract_effort
    doc["sha256"] = sha
    doc["filename"] = pf["filename"]
    doc["original_path"] = pf.get("original_path")
    doc["page_count"] = pf.get("page_count") or len(pf["pages"])
    doc.update(_sidecar_provenance(vault, pf["filename"]))
    # morgue_document_type is just the slug form of document_type — derive it deterministically
    # rather than asking the model for the same fact twice (it names the morgue folder).
    extraction["morgue_document_type"] = slugify(doc.get("document_type") or "") or "document"
    # morgue_entity_id is used raw as a morgue path segment (write_vault) — slugify the model's
    # value here too, so a value with spaces or an embedded path separator (e.g. "Acme Corp" or
    # "acme/subsidiary") can't produce a broken morgue directory layout or wikilinks.
    extraction["morgue_entity_id"] = slugify(extraction.get("morgue_entity_id") or "")


async def _classify(doc_excerpt: str, model: str, backend: str | None = None,
                    filename: str | None = None, sidecar: str | None = None) -> str:
    r = await _call_model(
        task="classify", model=model, backend=backend, schema=schemas.CLASSIFY,
        prompt=prompts.build_classify_prompt(doc_excerpt, skills_catalog.build_index(), sidecar),
        filename=filename,
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
        "record_skill": doc.get("record_skill"),
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


def _append_repair_note(base, errors: list[str]):
    """Append the post-flight repair note to a prompt, whether it's a plain string or a
    content-block list (A1) — the note is inherently volatile (only exists on retry), so it
    never belongs inside the cacheable prefix regardless of representation."""
    note = ("\n\nThe previous extraction was rejected:\n" + "\n".join(errors)
            + "\nReturn a corrected JSON object.")
    if isinstance(base, list):
        return base + [{"type": "text", "text": note}]
    return base + note


async def _simple_extract(vault, sha, pf, skill_text, brief, model, skill_label,
                          effort=None, backend=None):
    """Whole-document extraction, with one repair attempt if post-flight rejects."""
    base = prompts.build_extract_prompt(
        pages_text=_pages_text(pf["pages"]), existing_entities=pf.get("existing_entities", []),
        skill_text=skill_text, sidecar=_read_sidecar(vault, pf["filename"]), brief=brief,
        known_document_types=pf.get("known_document_types", []),
    )
    page_count = pf.get("page_count") or len(pf["pages"])
    cost, errors, extraction, scratchpad = 0.0, [], {}, ""
    for _ in range(2):
        p = base if not errors else _append_repair_note(base, errors)
        detail = f"pages 1–{page_count}" + (" (repair)" if errors else "")
        try:
            r = await _call_model(task="extract", model=model, backend=backend,
                                  prompt=p, schema=schemas.EXTRACTION, effort=effort,
                                  filename=pf["filename"], detail=detail)
        except model_client.ModelError as e:
            # No valid JSON after the client's own retries — often output truncated on a
            # dense doc. Report failure so the caller can fall back to sectioning.
            return extraction, scratchpad, cost, False, [f"extraction returned no valid JSON ({e})"]
        cost += r.cost_usd or 0.0
        extraction = r.parsed
        scratchpad = extraction.pop("scratchpad", "")
        _stamp_document(extraction, sha=sha, pf=pf, skill_label=skill_label, vault=vault,
                        skill_text=skill_text, extract_model=model, extract_effort=effort)
        ok, errors = _write_postflight(vault, sha, extraction)
        if ok:
            return extraction, scratchpad, cost, True, []
    return extraction, scratchpad, cost, False, errors


# Cap on the observations text carried into the next section's prompt (A5) — only the most
# recent section's, not every prior section's concatenated, since observations are
# forward-looking briefing leads (D33), not extraction context the model needs preserved.
_CARRY_OBSERVATIONS_CHARS = 2000


def _carry_text(entities_seen: dict, observations: str) -> str:
    """Build the carry-forward block from the entity ids seen in *any* section so far (deduped,
    one line each — not the running string-concatenation A5 replaced, which re-listed an entity
    once per section it appeared in) plus only the just-produced section's observations."""
    lines = []
    if entities_seen:
        lines.append("Entities so far:")
        lines += [f"- {eid} | {e.get('name')} | {e.get('type')}" for eid, e in entities_seen.items()]
    if observations:
        lines.append("Observations:\n" + observations[-_CARRY_OBSERVATIONS_CHARS:])
    return "\n".join(lines) + "\n" if lines else ""


def _stitch_digest(doc: dict, page_count: int | None) -> str:
    """Deterministic fallback when the digest call fails or returns empty: an orientation line
    from title/type/page_count, then the first few facts as plain sentences. Degraded but
    valid — never worth a retry loop (#279)."""
    head = doc.get("title") or "Untitled document"
    dtype = doc.get("document_type") or ""
    line = f"{head} — {dtype}" if dtype else head
    if page_count:
        line += f", {page_count} pages"
    facts = [t.rstrip(".") + "." for f in doc.get("key_facts", [])[:8]
             if (t := (f.get("fact") or "").strip())]
    return (line + ". " + " ".join(facts)).strip() if facts else line + "."


async def _compose_digest(doc: dict, page_count: int | None, model: str, backend: str | None,
                          filename: str, skill_text: str | None, brief: str | None,
                          sidecar: str | None) -> tuple[str, float]:
    """One small model call composing the whole-document digest from the merged key_facts
    (#279) — no section call ever sees the whole document, so this runs once after merge, on the
    extractor tier (the same model that read the sections), so both digest paths — inline for a
    whole-doc extraction, post-merge here — use one model. It is handed the same context a
    whole-document extractor gets short of the raw text (filename, domain skill, brief, sidecar),
    with the merged key_facts standing in for the text. Falls back to the deterministic stitch
    on any model failure or an empty response; returns (summary, cost)."""
    prompt = prompts.build_digest_prompt(
        filename=filename, title=doc.get("title", ""), document_type=doc.get("document_type", ""),
        page_count=page_count, skill_text=skill_text, brief=brief, sidecar=sidecar,
        key_facts=_briefing_facts(doc))
    try:
        r = await _call_model(task="digest", model=model, backend=backend,
                              prompt=prompt, schema=schemas.DIGEST,
                              filename=filename, detail="digest")
        summary = (r.parsed.get("summary") or "").strip()
        if summary:
            return summary, r.cost_usd or 0.0
        return _stitch_digest(doc, page_count), r.cost_usd or 0.0
    except model_client.ModelError:
        return _stitch_digest(doc, page_count), 0.0


async def _extract_sectioned(vault, sha, pf, skill_text, plan, model, skill_label,
                             effort=None, backend=None, brief=None):
    """Sequential per-section extraction with carry-forward, then deterministic merge."""
    parts, cost = [], 0.0
    entities_seen: dict[str, dict] = {}
    carry = ""
    sections = plan["sections"]
    for sec in sections:
        sec_text = (vault / sec["pages_path"]).read_text(encoding="utf-8")
        prompt = prompts.build_section_prompt(
            pages_text=sec_text, existing_entities=pf.get("existing_entities", []),
            skill_text=skill_text, carry_forward=carry, section_label=sec["label"],
            is_first=(sec["index"] == 1), brief=brief,
            known_document_types=pf.get("known_document_types", []),
        )
        r = await _call_model(task="extract-section", model=model, backend=backend,
                              prompt=prompt, schema=schemas.SECTION, effort=effort,
                              filename=pf["filename"], detail=sec["label"])
        cost += r.cost_usd or 0.0
        parts.append(r.parsed)
        for e in r.parsed.get("entities") or []:
            if e.get("id"):
                entities_seen[e["id"]] = {"name": e.get("name"), "type": e.get("type")}
        carry = _carry_text(entities_seen, r.parsed.get("observations") or "")

    extraction = merge.merge_extractions(parts)
    scratchpad = "\n".join(p["observations"] for p in parts if p.get("observations"))
    doc = extraction.setdefault("document", {})
    page_count = pf.get("page_count") or len(pf.get("pages", []))
    doc["summary"], digest_cost = await _compose_digest(
        doc, page_count, model, backend, pf["filename"],
        skill_text, brief, _read_sidecar(vault, pf["filename"]))
    cost += digest_cost
    _stamp_document(extraction, sha=sha, pf=pf, skill_label=skill_label, vault=vault,
                    skill_text=skill_text, extract_model=model, extract_effort=effort)
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
                            pinned_skill: str | None = None,
                            extract_effort: str | None = None,
                            extract_backend: str | None = None,
                            classify_backend: str | None = None) -> dict:
    pf = preflight.run(vault, sha)
    if pf.get("error"):
        return _fail(vault, sha, "", pf["error"])
    if pf.get("already_extracted"):
        _say(f"{_DIM}–  {pf.get('filename')}  already extracted — skipping{_RESET}")
        (vault / ".watchdog" / "queue" / f"{sha}.json").unlink(missing_ok=True)
        return {"sha256": sha, "filename": pf.get("filename"), "status": "skipped"}

    filename = pf["filename"]
    pages = pf.get("pages", [])
    page_count = pf.get("page_count") or len(pages)
    pg = f"{page_count}p"

    # Digest-size telemetry (#216): how much prior-entity context this extraction carries. Watch it
    # on a mature vault to decide whether per-candidate caps are worth adding, and at what sizes.
    # Silent when there are no candidates (e.g. a fresh vault, or a document with no recurring
    # entities) — "0.0 KB · 0 candidates" on every line is noise, not signal (#317).
    _n_cand = pf.get("existing_entities_count", 0)
    if _n_cand:
        _say(f"{_DIM}   {filename} · prior-entity digest "
             f"{pf.get('existing_entities_bytes', 0) / 1024:.1f} KB · "
             f"{_n_cand} candidate{'s' if _n_cand != 1 else ''}{_RESET}")

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
        skill = await _classify(excerpt, classify_model, classify_backend, filename=filename,
                                sidecar=_read_sidecar(vault, filename))
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
            vault, sha, pf, skill_text, plan, extract_model, skill_label, extract_effort, extract_backend, brief)
    else:
        _step(f"{_DIM}→  {filename}  {flow} · extracting…{_RESET}",
              f"{_DIM}→  {filename}  extracting…{_RESET}")
        extraction, scratchpad, cost, ok, errors = await _simple_extract(
            vault, sha, pf, skill_text, brief, extract_model, skill_label, extract_effort, extract_backend)
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
                    vault, sha, pf, skill_text, fb, extract_model, skill_label, extract_effort, extract_backend, brief)
                cost += whole_cost   # account for the failed whole-doc attempt

    if not ok:
        return _fail(vault, sha, filename, "post-flight rejected: " + "; ".join(errors[:3]))
    return _finish_extraction(vault, sha, filename, extraction, scratchpad, cost, pf, page_count)


def _finish_extraction(vault: Path, sha: str, filename: str, extraction: dict, scratchpad: str,
                       cost: float, pf: dict, page_count: int | None) -> dict:
    """Shared tail once an extraction has passed post-flight: settle-print, coverage warning,
    log, persist `result_<sha>.json`. Used by both the synchronous per-document path
    (`_extract_document`) and the batch-collect path (`_finish_batch_item`, #214) so a
    batch-extracted document produces an identical result shape to a synchronous one."""
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


# ── claude-batch: submit-many/poll/collect bulk extraction (#214) ────────────────────────────
#
# A fundamentally different flow from the concurrent per-document loop above: the Message
# Batches API is submit-many/poll/collect over minutes-to-24h, not one-await-per-document.
# `_run_batch` (called by `run`, not `_extract_document`) resumes a pending batch if one exists,
# otherwise splits the queue into sectioned documents (extracted synchronously via claude-api —
# a section's carry-forward depends on the previous section's result, so it can't be an
# independent batch request) and whole documents (submitted as one batch). Requires a pinned
# skill: classification is inherently one-document-at-a-time and not batchable.

async def _finish_batch_item(vault: Path, sha: str, item: dict | None, skill_text: str,
                             skill_label: str, brief: str | None, api_key: str,
                             model: str | None = None, effort: str | None = None) -> dict:
    """Turn one collected batch result into a finished document. `item` is `batch_extract.collect`'s
    per-sha entry (or None if the batch has no result for this sha at all). A batch response that
    didn't pass schema validation gets exactly one synchronous claude-api repair attempt — not a
    whole new batch submission for a single document — mirroring `_simple_extract`'s own
    single-repair-attempt semantics.

    `model` (the batch's resolved model id) is used both to attribute the batch-collected item's
    own usage (D64) — unlike every other extraction path, this one never calls `_call_model`
    itself when the batch result is already valid, so without this the batch's real token spend
    would silently never reach `usage-<ts>.json` — and, with `effort`, to stamp this document's
    extraction provenance (#268)."""
    pf = preflight.run(vault, sha)
    if pf.get("error"):
        return _fail(vault, sha, "", pf["error"])
    if pf.get("already_extracted"):     # a retried collection pass after a partial rate limit
        filename = pf.get("filename")
        _say(f"{_DIM}–  {filename}  already extracted — skipping{_RESET}")
        (vault / ".watchdog" / "queue" / f"{sha}.json").unlink(missing_ok=True)
        return {"sha256": sha, "filename": filename, "status": "skipped"}

    filename = pf["filename"]
    page_count = pf.get("page_count") or len(pf.get("pages", []))
    if item is None:
        return _fail(vault, sha, filename, "batch result missing for this document")

    extraction, cost = item["parsed"], item.get("cost_usd") or 0.0
    if item.get("usage") is not None:
        # The Batches API requires api-key auth (D52) — never subscription — so this is the
        # one _record_usage call site where auth_mode is a known constant, not a live result field.
        _record_usage("extract", model=model, backend="claude-batch", usage=item["usage"],
                      cost_usd=item.get("cost_usd"), effort=effort, auth_mode="api-key",
                      filename=filename, detail=f"pages 1–{page_count}")
    if not item["ok"]:
        prompt = prompts.build_extract_prompt(
            pages_text=_pages_text(pf["pages"]), existing_entities=pf.get("existing_entities", []),
            skill_text=skill_text, sidecar=_read_sidecar(vault, filename), brief=brief,
            known_document_types=pf.get("known_document_types", []))
        if item.get("error"):
            prompt = _append_repair_note(prompt, [item["error"]])
        try:
            r = await _call_model(task="extract", model=None, backend="claude-api",
                                  prompt=prompt, schema=schemas.EXTRACTION,
                                  filename=filename, detail=f"pages 1–{page_count} (repair)")
        except model_client.ModelError as e:
            return _fail(vault, sha, filename, f"batch result invalid and repair failed: {e}")
        extraction = r.parsed
        cost += r.cost_usd or 0.0

    scratchpad = extraction.pop("scratchpad", "") if isinstance(extraction, dict) else ""
    _stamp_document(extraction, sha=sha, pf=pf, skill_label=skill_label, vault=vault,
                    skill_text=skill_text, extract_model=model, extract_effort=effort)
    ok, errors = _write_postflight(vault, sha, extraction)
    if not ok:
        return _fail(vault, sha, filename, "post-flight rejected: " + "; ".join(errors[:3]))
    return _finish_extraction(vault, sha, filename, extraction, scratchpad, cost, pf, page_count)


async def _resume_batch(vault: Path, state: dict, pinned_skill: str, brief: str | None,
                        api_key: str) -> dict:
    """Check a pending batch's status; collect and write it if `ended`, otherwise report
    progress and return without touching the vault."""
    st = await batch_extract.status(state["batch_id"], api_key)
    if st["processing_status"] != "ended":
        counts = st.get("request_counts", {})
        done = sum(v for k, v in counts.items() if k != "processing")
        _say(f"{_YELLOW}A batch extraction is still processing{_RESET}{_DIM} "
             f"({done}/{len(state['shas'])} finished so far) — re-run {_RESET}"
             f"{_CYAN}watchdog ingest{_RESET}{_DIM} later to check again.{_RESET}")
        return {"results": [], "batch_pending": True}

    _say(f"{_DIM}→  batch {state['batch_id']} finished — collecting {len(state['shas'])} "
         f"document{'s' if len(state['shas']) != 1 else ''}…{_RESET}")
    collected = await batch_extract.collect(state["batch_id"], api_key, state["model"])
    skill_text = Path(pinned_skill).read_text(encoding="utf-8")
    skill_label = Path(pinned_skill).stem

    results = []
    try:
        for sha in state["shas"]:
            results.append(await _finish_batch_item(vault, sha, collected.get(sha), skill_text,
                                                     skill_label, brief, api_key,
                                                     model=state["model"], effort=state.get("effort")))
    except model_client.RateLimitError as e:
        # A repair-retry call (claude-api) hit a rate limit partway through collection. Leave
        # the batch state in place — already-written documents are safe (preflight's
        # already_extracted check skips them on the next pass) — so a later run finishes.
        _say(f"{_YELLOW}Rate limit reached during batch collection{_RESET}{_DIM} — {e} "
             f"{len(results)}/{len(state['shas'])} written; re-run {_RESET}"
             f"{_CYAN}watchdog ingest{_RESET}{_DIM} to finish once it resets.{_RESET}")
        return {"results": results, "batch_pending": True}

    batch_extract.clear_state(vault)
    return {"results": results, "batch_pending": False}


async def _submit_batch(vault: Path, shas: list[str], brief: str | None, extract_model: str,
                        pinned_skill: str, extract_effort: str | None, concurrency: int,
                        classify_model: str, classify_pages: int, classify_backend: str | None,
                        api_key: str) -> dict:
    """Split the queue into sectioned (→ synchronous claude-api, via the normal
    `_extract_document`) and whole-document (→ one batch submission) shas, run the former, then
    submit the latter and return — submit-and-exit, not blocking-poll (a batch can take up to
    24h; a *later* `watchdog ingest` invocation collects it, see `_resume_batch`)."""
    skill_text = Path(pinned_skill).read_text(encoding="utf-8")
    skill_label = Path(pinned_skill).stem

    results: list[dict] = []
    batch_docs: list[dict] = []
    sectioned_shas: list[str] = []
    for sha in shas:
        pf = preflight.run(vault, sha)
        if pf.get("error"):
            results.append(_fail(vault, sha, "", pf["error"]))
            continue
        if pf.get("already_extracted"):
            _say(f"{_DIM}–  {pf.get('filename')}  already extracted — skipping{_RESET}")
            (vault / ".watchdog" / "queue" / f"{sha}.json").unlink(missing_ok=True)
            results.append({"sha256": sha, "filename": pf.get("filename"), "status": "skipped"})
            continue
        if section.run(vault, sha).get("sectioned"):
            sectioned_shas.append(sha)
        else:
            prompt = prompts.build_extract_prompt(
                pages_text=_pages_text(pf["pages"]), existing_entities=pf.get("existing_entities", []),
                skill_text=skill_text, sidecar=_read_sidecar(vault, pf["filename"]), brief=brief,
                known_document_types=pf.get("known_document_types", []), cache_ttl="1h")
            batch_docs.append({"sha": sha, "prompt": prompt})

    if sectioned_shas:
        _say(f"{_DIM}→  {len(sectioned_shas)} large document"
             f"{'s' if len(sectioned_shas) != 1 else ''} need sectioning — not batchable, "
             f"extracting via claude-api{_RESET}")
        sem = asyncio.Semaphore(max(1, concurrency))

        async def _sectioned(sha: str) -> dict:
            async with sem:
                return await _extract_document(vault, sha, brief, extract_model, classify_model,
                                               classify_pages, pinned_skill, extract_effort,
                                               extract_backend="claude-api",
                                               classify_backend=classify_backend)
        results.extend(await asyncio.gather(*[_sectioned(s) for s in sectioned_shas]))

    if not batch_docs:
        return {"results": results, "batch_pending": False}

    _say(f"{_DIM}→  submitting {len(batch_docs)} document"
         f"{'s' if len(batch_docs) != 1 else ''} as one batch ({skill_label})…{_RESET}")
    batch_id = await batch_extract.submit(vault, batch_docs, model=extract_model,
                                          effort=extract_effort, skill_label=skill_label,
                                          api_key=api_key)
    _say(f"{_GREEN}Batch submitted{_RESET}  {_CYAN}{batch_id}{_RESET}{_DIM} — this can take up "
         f"to a few hours (max 24h); re-run {_RESET}{_CYAN}watchdog ingest{_RESET}{_DIM} later "
         f"to collect it.{_RESET}")
    return {"results": results, "batch_pending": True}


async def _run_batch(vault: Path, shas: list[str], brief: str | None, extract_model: str,
                     pinned_skill: str | None, extract_effort: str | None, concurrency: int,
                     classify_model: str, classify_pages: int,
                     classify_backend: str | None) -> dict:
    """Entry point for `run` when `extract_backend == "claude-batch"`. Defense-in-depth guards
    beyond `cmd_ingest`'s own checks — a programmatic caller that skips CLI validation still
    gets a clear error rather than a confusing downstream failure."""
    if not pinned_skill:
        raise model_client.ModelError(
            "claude-batch requires a pinned skill (--skill/default_skill) — classification "
            "is not batchable (#214)")
    from watchdog.cmd import auth
    api_key = auth.resolve_auth().get("key")
    if not api_key:
        raise model_client.ModelError(
            "claude-batch requires api-key auth mode — run `watchdog auth use api-key`")

    state = batch_extract.read_state(vault)
    if state is not None:
        return await _resume_batch(vault, state, pinned_skill, brief, api_key)
    return await _submit_batch(vault, shas, brief, extract_model, pinned_skill, extract_effort,
                               concurrency, classify_model, classify_pages, classify_backend,
                               api_key)


def _nudge_skill_pin(results: list) -> None:
    """If every successfully-extracted document in a run classified to the same record skill,
    tell the user they could have skipped classification with `--skill` (A4) — a batch of one
    filing type is the common case this tool targets, and the flag already exists but nothing
    surfaces that a run *was* homogeneous."""
    ok_skills = [r.get("record_skill") for r in results if r.get("status") == "ok"]
    distinct = {s for s in ok_skills if s}
    if len(ok_skills) > 1 and len(distinct) == 1:
        skill = next(iter(distinct))
        _say(f"{_DIM}All {len(ok_skills)} documents classified as {_RESET}{_CYAN}{skill}{_RESET}"
             f"{_DIM} — next time run {_RESET}{_CYAN}watchdog ingest --skill {skill}{_RESET}"
             f"{_DIM} to skip classification.{_RESET}")


def _lines(items: list) -> str:
    return "\n".join(f"- {x}" for x in items) if items else "_None._"


def _fts_add_note_safe(vault: Path, note_path: str, kind: str, title: str, text: str) -> None:
    """Best-effort full-text index update (#109) — never fails the ingest run over it."""
    try:
        from watchdog.pipeline.fulltext import add_note
        add_note(vault, note_path, kind, title, text)
    except Exception as e:
        print(f"  Warning: full-text index update failed for {note_path}: {e}", file=sys.stderr)


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
    _fts_add_note_safe(vault, f"briefings/{slug}", "briefing", f"Briefing {slug}", body)

    hot_content = (
        f"# Hot cache\n\n*Last updated: {now.strftime('%Y-%m-%d')} — "
        f"[[briefings/{slug}|Briefing {slug}]]*\n\n"
        f"## Investigation status\n\n{b.get('investigation_status', '')}\n\n"
        f"## Recent additions\n\n{_lines(b.get('new_entities', []))}\n\n"
        f"## Emerging patterns\n\n{_lines(b.get('emerging_patterns', []))}\n\n"
        f"## Open questions\n\n{_lines(b.get('open_questions', []))}\n"
    )
    (vault / "hot.md").write_text(hot_content, encoding="utf-8")
    _fts_add_note_safe(vault, "hot", "hot", "Hot cache", hot_content)

    entry = (f"\n## {now.strftime('%Y-%m-%d %H:%M')} — Ingest\n\n"
             f"- **Files:** {len(results)} processed\n- **New entities:** {n_new}\n"
             f"- **Briefing:** [[briefings/{slug}|{slug}]]\n")
    if contradiction_flags:
        entry += f"- **Contradictions flagged:** {len(contradiction_flags)}\n"
    if _usage:   # (F5, #222) — _post_ingest's own calls (synthesis/timeline-dedup/briefing)
        totals = _usage_totals(_usage)   # are already recorded by the time this runs
        cost = f" · ~${totals['cost_usd']:.4f}" if totals.get("cost_usd") else ""
        entry += (f"- **Usage:** {totals['input_tokens']:,} in / "
                 f"{totals['output_tokens']:,} out tokens{cost}\n")
    log_path = vault / "log.md"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)
    _fts_add_note_safe(vault, "log", "log", "Run log", log_path.read_text(encoding="utf-8"))
    return f"briefings/{slug}.md"


def _valid_index(i, n: int) -> bool:
    return isinstance(i, int) and not isinstance(i, bool) and 0 <= i < n


def _select_kept(events: list[dict], groups) -> list[dict]:
    """Map the timeline-dedup model's `groups` back to the original event objects (deduped, in
    original order). Each surviving event carries the union of its own and its dropped duplicates'
    `entity_ids` (#237) — so entity attribution survives a cross-document collapse regardless of
    which restatement the model kept. An index the model never placed in any group is preserved
    as-is; falls back to all events when the output is unusable — dedup must never lose events. The
    originals carry the authoritative page/basis/source_sha256."""
    if not isinstance(groups, list):
        return events
    n = len(events)
    survivors: dict[int, list[int]] = {}   # keep index -> its duplicate indices
    for g in groups:
        if not isinstance(g, dict):
            continue
        ki = g.get("keep")
        if not _valid_index(ki, n) or ki in survivors:
            continue
        dups = g.get("duplicates")
        survivors[ki] = [di for di in dups if _valid_index(di, n) and di != ki] if isinstance(dups, list) else []
    dropped = {di for dups in survivors.values() for di in dups}

    kept: list[dict] = []
    for i, ev in enumerate(events):
        if i in survivors:
            merged = dict(ev)
            entity_ids = list(merged.get("entity_ids", []))
            for di in survivors[i]:
                for eid in events[di].get("entity_ids", []):
                    if eid not in entity_ids:
                        entity_ids.append(eid)
            if "entity_ids" in merged or entity_ids:
                merged["entity_ids"] = entity_ids
            kept.append(merged)
        elif i not in dropped:
            kept.append(ev)   # model never placed it — keep rather than lose it
    return kept or events


async def _post_ingest(vault: Path, results: list, brief: str | None, post_model: str,
                       post_effort: str | None = None, post_backend: str | None = None) -> dict:
    out: dict = {"synthesized": 0, "timeline_collisions": 0, "briefing": None}
    print()
    _say(f"{_BOLD}Post-processing{_RESET}")

    # 1. Entity synthesis for multi-mention entities (Python builds + applies; model reconciles).
    bundle = synthesis_bundle.build_bundle(vault)
    if bundle.get("entities"):
        _say(f"{_DIM}→  synthesizing {len(bundle['entities'])} multi-mention "
             f"entit{'ies' if len(bundle['entities']) != 1 else 'y'}…{_RESET}")
        try:
            r = await _call_model(
                task="entity-synthesis", model=post_model, backend=post_backend, schema=schemas.SYNTHESIS,
                prompt=prompts.build_synthesis_prompt(bundle), effort=post_effort)
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
        raw_paths = [vault / r for r in col["raw"]]
        events: list[dict] = []
        for p in [canonical, *raw_paths]:
            for line in timeline._read_ndjson_lines(p):
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        if not events:
            continue
        try:
            r = await _call_model(
                task="timeline-dedup", model=post_model, backend=post_backend, schema=schemas.TIMELINE_DEDUP,
                prompt=prompts.build_timeline_dedup_prompt(col["date"], events), effort=post_effort,
                detail=col["date"])
            kept = _select_kept(events, r.parsed.get("groups"))
        except (model_client.ModelError, model_client.RateLimitError):
            # Dedup failed (e.g. rate limit): leave the canonical AND its raws untouched so the
            # next ingest retries this collision cleanly. Writing the canonical+raw union back
            # here would bake in duplicate rows that compound on every later run (#250).
            continue
        canonical.write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in kept) + "\n", encoding="utf-8")
        # The raws are now merged into the canonical — consume them so they aren't re-collided.
        for rp in raw_paths:
            rp.unlink(missing_ok=True)

    # 2b. Cross-precision reconciliation (#239, D63): date-keyed buckets never compare a
    # month-precision event against the specific day it restates. For each month holding both, one
    # focused model call folds coarse restatements into their day, unioning entity attribution.
    # Gated on a month carrying both precisions, so most ingests make zero extra calls.
    for grp in timeline.month_precision_groups(vault):
        try:
            r = await _call_model(
                task="timeline-precision", model=post_model, backend=post_backend,
                schema=schemas.TIMELINE_PRECISION_MATCH, effort=post_effort,
                prompt=prompts.build_timeline_precision_prompt(grp["month"], grp["coarse"], grp["precise"]),
                detail=grp["month"])
        except (model_client.ModelError, model_client.RateLimitError):
            continue   # leave the month untouched rather than risk a bad fold
        timeline.apply_precision_matches(vault, grp, r.parsed.get("matches") or [])
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
        r = await _call_model(
            task="briefing", model=post_model, backend=post_backend, schema=schemas.BRIEFING,
            prompt=prompts.build_briefing_prompt(
                brief=brief, results=ok, scratchpads=scratchpads,
                neardup_alerts=neardup_alerts, contradiction_flags=contradiction_flags),
            effort=post_effort)
        out["briefing"] = _write_briefing(vault, r.parsed, ok, neardup_alerts, contradiction_flags)
    except model_client.RateLimitError as e:
        out["briefing_error"] = str(e)
        _say(f"{_YELLOW}briefing skipped{_RESET}{_DIM} — {e}{_RESET}")
    except model_client.ModelError as e:
        # Extraction has already run through this same backend, so a briefing ModelError is
        # almost always an output-cap truncation: the briefing's arrays (what_was_ingested/
        # connections/leads/…) scale with batch size, so a big/dense batch can overrun even the
        # 16k-token ceiling and truncate the JSON. That's deterministic — a plain re-run feeds
        # the identical input into the identical ceiling and fails the same way (#296) — so we
        # fail loudly with the real remedy (a smaller batch) rather than retrying or silently
        # shipping a degraded briefing. Everything else (per-doc facts, entity notes, timeline)
        # is already on disk; only the synthesized briefing is lost, and the pending batch can be
        # discarded on the next ingest to unstick. Streaming (an unbounded ceiling) is future work.
        out["briefing_error"] = str(e)
        _say(f"{_YELLOW}briefing not written{_RESET}{_DIM} — the model's output limit was exceeded "
             f"(this batch is too large to summarize in one pass). Re-ingest it in smaller "
             f"batches; everything else was written.{_RESET}")

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

    # 5. Lead sweep (deterministic, no model; #155). Whole-vault snapshot of entities named
    # but never profiled, recurring-but-unconnected entities, and unresolved contradictions.
    leads_data = leads.scan(vault)
    leads_relpath = leads.write_leads(vault, leads_data)
    if leads_relpath:
        n = leads.total(leads_data)
        out["leads"] = leads_relpath
        _say(f"{_YELLOW}⚠{_RESET}  {_BOLD}{n}{_RESET} lead{'s' if n != 1 else ''} "
             f"{_DIM}(named-but-unprofiled, isolated, contradictions){_RESET} — "
             f"{_CYAN}{leads_relpath}{_RESET}")
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
    """Best-effort counts for an extracted-but-not-finalized batch sitting in tmp.

    Entity count uses the same gate as `synthesis_bundle.build_bundle` — registry
    `appears_in >= 2` (D26) — not the fragment queue's `count`, which is only a
    touched-set marker post-D26 and no longer the synthesis gate."""
    tmp = vault / ".watchdog" / "tmp"
    docs = len(list(tmp.glob("result_*.json")))
    entities = 0
    q = tmp / "entity-fragments" / "_queue.json"
    if q.exists():
        try:
            queue = json.loads(q.read_text(encoding="utf-8"))
            reg_path = vault / ".watchdog" / "Registry" / "entities.json"
            registry = json.loads(reg_path.read_text(encoding="utf-8")) if reg_path.exists() else {}
            entities = sum(1 for eid in queue
                           if len(registry.get(eid, {}).get("appears_in", [])) >= 2)
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    return {"docs": docs, "entities": entities}


async def finalize(vault: Path, *, post_model: str = "haiku", brief: str | None = None,
                   results: list | None = None, post_effort: str | None = None,
                   post_backend: str | None = None) -> dict:
    """Run (or re-run) post-ingest over the current on-disk state: synthesize multi-mention
    entities, reconcile the timeline, and write the briefing/hot.md/log.

    Called at the tail of every ``watchdog ingest`` (with this run's results in memory) and
    standalone by ``watchdog finalize`` (reading persisted ``result_*.json``) to complete a
    post-ingest an earlier rate limit or interrupt left unfinished. On a clean pass the consumed
    inputs (fragments, results, scratchpads) are cleared; if any step failed they are left in
    place so a later finalize can retry.
    """
    global _usage
    standalone_usage = _usage is None   # not nested inside `run` — this call owns the usage file
    if standalone_usage:
        _usage = []
    if brief is None:
        brief = _read_brief(vault)
    if results is None:
        results = _load_results(vault)
    out = await _post_ingest(vault, results, brief, post_model, post_effort, post_backend)
    if not out.get("error") and not out.get("briefing_error"):
        _clear_post_ingest_inputs(vault)
    if standalone_usage:
        out["usage_path"] = _write_usage(vault, _usage)
        out["usage"] = _usage_totals(_usage) if _usage else None
        _usage = None
    return out


async def run(vault: Path, *, concurrency: int = DEFAULT_CONCURRENCY,
              extract_model: str = "sonnet", post_model: str = "sonnet",
              classify_model: str = "haiku",
              classify_pages: int = DEFAULT_CLASSIFY_PAGES,
              pinned_skill: str | None = None,
              extract_effort: str | None = None, post_effort: str | None = None,
              extract_backend: str | None = None, post_backend: str | None = None,
              classify_backend: str | None = None, wait: bool = False) -> dict:
    """Extract every queued document (bounded by `concurrency`), then post-ingest.

    `extract_model` drives extraction (whole-doc + section, and the sectioned-doc digest); `post_model` drives
    synthesis/timeline/briefing; `classify_model` the cheap document classifier,
    which sees the first `classify_pages` pages of each document. `pinned_skill`
    (a path to a skill file) skips classification and uses that skill for every document.
    `extract_effort`/`post_effort` (`low`/`medium`/`high`) tune reasoning depth for the
    extraction and post-ingest stages respectively (D36); classify has no effort knob.
    `*_backend` selects a non-default backend per stage (None → route by auth mode); a
    non-Claude backend's `*_model` is that provider's raw model id (D37).
    `wait` only changes the rate-limit notice text — the caller (`cmd_ingest`) owns the
    actual sleep-and-resume loop; this function always stops cleanly on a rate limit.
    """
    queue_dir = vault / ".watchdog" / "queue"
    shas = [f.stem for f in sorted(queue_dir.glob("*.json"))] if queue_dir.exists() else []

    global _board, _usage

    # claude-batch (#214): submit-many/poll/collect, not one-await-per-document, so it's a
    # genuinely different flow — handled entirely by _run_batch (which also covers a resumed
    # pending batch even when `shas` is empty). Both branches converge on `results` /
    # `cancelled_flag` / `rate_limit_msg` / `extra_summary` and rejoin the shared tail below.
    if extract_backend == "claude-batch":
        _usage = []
        brief = _read_brief(vault)
        batch_out = await _run_batch(vault, shas, brief, extract_model, pinned_skill,
                                     extract_effort, concurrency, classify_model, classify_pages,
                                     classify_backend)
        results = batch_out["results"]
        cancelled_flag = False
        rate_limit_msg = None
        rate_limit_resets_at = None
        extra_summary = {"batch_pending": batch_out.get("batch_pending", False)}
    else:
        if not shas:
            return {"results": [], "extracted": 0, "skipped": 0, "failed": 0}

        brief = _read_brief(vault)
        # Live status region for the concurrent extraction phase (#151): one in-place row per
        # in-flight document, finished/failed lines scrolling above. Auto-disables off a TTY,
        # where it degrades to the previous append-only output.
        _board = LiveRegion()
        _usage = []
        sem = asyncio.Semaphore(max(1, concurrency))
        cancelled = asyncio.Event()
        stop_reason: dict = {}      # {"rate_limit": "<notice>"} when a limit stopped the batch
        tasks: list = []

        def _request_stop(rate_limit: str | None = None, resets_at: int | None = None) -> None:
            """Stop the batch once: flag it, record why, cancel in-flight work. Idempotent."""
            if cancelled.is_set():
                return
            cancelled.set()
            if rate_limit:
                stop_reason["rate_limit"] = rate_limit
                stop_reason["resets_at"] = resets_at
            for t in tasks:
                t.cancel()

        async def _guarded(sha: str) -> dict:
            if cancelled.is_set():                   # never started — leave queue file for resume
                return {"sha256": sha, "filename": "", "status": "cancelled"}
            async with sem:
                if cancelled.is_set():
                    return {"sha256": sha, "filename": "", "status": "cancelled"}
                try:
                    return await _extract_document(vault, sha, brief, extract_model, classify_model,
                                                   classify_pages, pinned_skill, extract_effort,
                                                   extract_backend, classify_backend)
                except model_client.RateLimitError as e:  # session-wide — stop, leave queued for resume
                    if not cancelled.is_set():
                        print()
                        _say(f"{_YELLOW}Rate limit reached{_RESET}{_DIM} — {e}{_RESET}")
                        if wait:
                            _say(f"{_DIM}Stopping; finished documents are saved. "
                                 f"Waiting to resume automatically once it resets.{_RESET}")
                        else:
                            _say(f"{_DIM}Stopping; finished documents are saved. Re-run "
                                 f"{_RESET}{_CYAN}watchdog ingest{_RESET}{_DIM} once it resets to continue.{_RESET}")
                        _request_stop(rate_limit=str(e), resets_at=e.resets_at)
                    return {"sha256": sha, "filename": "", "status": "cancelled"}
                except asyncio.CancelledError:       # ctrl+c mid-document — queue file stays
                    return {"sha256": sha, "filename": "", "status": "cancelled"}
                except Exception as e:               # one bad doc must not sink the batch
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
        except (NotImplementedError, RuntimeError):
            # e.g. non-main thread, or Windows' Proactor event loop, which never supports
            # add_signal_handler. There, every Ctrl+C falls through to cmd_ingest's plain
            # `except KeyboardInterrupt` instead of this module's graceful
            # finish-current-writes path — see that handler's comment for what differs.
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
        cancelled_flag = cancelled.is_set()
        rate_limit_msg = stop_reason.get("rate_limit")
        rate_limit_resets_at = stop_reason.get("resets_at")
        extra_summary = {}

    by_status = lambda s: sum(1 for r in results if r.get("status") == s)
    failed_dir = vault / ".watchdog" / "queue" / "_failed"
    quarantined = len(list(failed_dir.glob("*.json"))) if failed_dir.exists() else 0
    summary = {"results": results, "extracted": by_status("ok"),
               "skipped": by_status("skipped"), "failed": by_status("failed"),
               "cancelled": cancelled_flag,
               "rate_limited": bool(rate_limit_msg),
               "stop_message": rate_limit_msg,
               "rate_limit_resets_at": rate_limit_resets_at,
               "quarantined": quarantined, **extra_summary}
    if not pinned_skill:
        _nudge_skill_pin(results)
    if summary["extracted"] and not cancelled_flag:
        try:
            # Finalize over the persisted per-doc results on disk (not just this run's in-memory
            # ones) so a merged batch — a prior pending run kept via wipe_pending=False — is
            # synthesized and briefed together with this run's documents.
            summary["post_ingest"] = await finalize(vault, post_model=post_model, brief=brief,
                                                    post_effort=post_effort, post_backend=post_backend)
            _update_graph_colours(vault)
        except Exception as e:   # post-ingest is enrichment — never let it crash a saved batch
            summary["post_ingest_error"] = str(e)
            print()
            _say(f"{_YELLOW}Post-processing incomplete{_RESET}{_DIM} — {e}{_RESET}")
            _say(f"{_DIM}Your {summary['extracted']} extracted document"
                 f"{'s are' if summary['extracted'] != 1 else ' is'} saved.{_RESET}")
    state = "rate-limited" if summary["rate_limited"] else ("cancelled" if cancelled_flag else "complete")
    _log(vault, f"INGEST {state} — {summary['extracted']} extracted, "
                f"{summary['skipped']} skipped, {summary['failed']} failed")
    summary["usage"] = _usage_totals(_usage) if _usage else None
    summary["usage_path"] = _write_usage(vault, _usage)
    _usage = None
    return summary
