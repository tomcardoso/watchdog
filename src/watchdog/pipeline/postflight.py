"""Post-flight: validate an extraction and stage it as a durable artifact.

Validates the extraction, resolves quote locators, checks figures, explodes `key_facts` into
per-entity fragments and timeline events, stages raw timeline NDJSON, and writes the result to
`.watchdog/extracted/<sha>.json`. Returns {"ok": true} or {"errors": [...]}.

It never writes to the vault: finalize's commit pass replays `write_vault.run()` over staged
artifacts (D126, I7). The artifact is kept as an audit record. Entity resolution happens later —
the exact-name fold and the reconcile pass (D118, D127)."""

import json
import re
import sys
from pathlib import Path

from watchdog.pipeline.json_io import _read_json
from watchdog import config as user_config

_VALID_BASIS = {"stated", "inferred"}
_PATHLIKE = re.compile(r"[/\\]|\.\.")

# Page-coverage heuristic (skim detection). Advisory only — emits a warning, never a failure.
_COVERAGE_MIN_PAGES = 8         # don't flag short documents
# Flag when the largest run of consecutive uncited pages is at least this share of the document.
# Gap-based rather than tail-based (#339): the old "nothing cited past the halfway point" rule
# missed interior holes — a model that cites pages 1–10 and 40–50 of a 50-pager read nothing in
# between, which is the signature of a skim just as much as a truncated tail is.
_COVERAGE_GAP_FRACTION = 0.4

# Empty-extraction guard (#507/#510) — a hard failure, not advisory. Gated on real source-text
# volume (page_texts, from the chew-time queue descriptor) rather than page count: a nominal
# page count is a poor proxy for how much there was to extract from. 500 words is comfortably
# below every document in corpus-v1 (smallest: a 5-page order at ~1,200 words) while still
# exempting a short filing — a cover letter, a signature page — that can legitimately carry
# nothing to extract. Configurable (`empty_extraction_min_words`, D153) since 500 is a
# heuristic guess, not a measured constant, and a domain with unusually terse-but-substantive
# documents may need a different value.
_EMPTY_EXTRACTION_MIN_WORDS = 500


def _config_get(key: str, default):
    return user_config.get(key, default)


def _find_coverage_gap(extraction: dict, page_count: int | None) -> dict | None:
    """Find a possible skim: when a large consecutive run of a multi-page document's pages —
    leading, interior, or trailing — is cited by no fact, the model likely skipped it (#339).
    A heuristic, deterministic signal for review, not a hard check: a genuinely boilerplate span
    (standard-form clauses, recitals) legitimately goes uncited and trips it too. Facts carry an
    optional `page`; a doc with no page anchors at all can't be assessed. Returns the flagged
    span as ``{"start", "end", "pages"}``, or None when there's no qualifying gap."""
    if not page_count or page_count < _COVERAGE_MIN_PAGES:
        return None
    facts = extraction.get("document", {}).get("key_facts", [])
    cited = sorted({f["page"] for f in facts
                    if isinstance(f, dict) and isinstance(f.get("page"), int)
                    and not isinstance(f.get("page"), bool)
                    and 1 <= f["page"] <= page_count})   # ignore out-of-range citations
    if not cited:
        return None
    # Largest uncited run, including before the first cite and after the last.
    bounds = [0, *cited, page_count + 1]
    gap_len, gap_span = 0, (0, 0)
    for a, b in zip(bounds, bounds[1:]):
        if b - a - 1 > gap_len:
            gap_len, gap_span = b - a - 1, (a + 1, b - 1)
    if gap_len < page_count * _COVERAGE_GAP_FRACTION:
        return None
    start, end = gap_span
    return {"start": start, "end": end, "pages": gap_len}


def _render_coverage_warning(gap: dict, page_count: int) -> str:
    """Render `_find_coverage_gap`'s structured result as the human-readable warning text —
    byte-identical to the pre-#339-persistence wording, since the log format downstream depends
    on it."""
    return (f"no facts cite pages {gap['start']}–{gap['end']} ({gap['pages']} of {page_count} "
            f"pages) — the model may have skipped them; check that span of the source for "
            f"anything missed")


def _validate(data: dict, page_texts: dict[int, str] | None = None) -> list[str]:
    errors: list[str] = []
    page_texts = page_texts or {}

    doc = data.get("document")
    if not isinstance(doc, dict):
        errors.append("missing or invalid 'document' field")
    else:
        for field in ("sha256", "filename"):
            if not doc.get(field):
                errors.append(f"document.{field} is missing or empty")
        for i, fact in enumerate(doc.get("key_facts", [])):
            if not isinstance(fact, dict):
                errors.append(f"document.key_facts[{i}] is not an object")
            else:
                if fact.get("basis") and fact["basis"] not in _VALID_BASIS:
                    errors.append(f"document.key_facts[{i}].basis '{fact['basis']}' must be one of: {', '.join(sorted(_VALID_BASIS))}")
                if "entities" in fact and not isinstance(fact["entities"], list):
                    errors.append(f"document.key_facts[{i}].entities must be a list of entity ids")

        # A near-total extraction failure (#507/#510): a document with substantial extractable
        # text but zero key_facts is not a genuinely fact-free document — it's a degenerate model
        # response (observed: an 8690-token call that billed normally and returned an empty
        # key_facts list with a placeholder summary on a 17-page court order). Unlike the
        # coverage-gap heuristic above, which needs at least one citation to measure a gap
        # against and so can't see this case at all, this is a hard failure: it feeds the same
        # repair-retry loop as any other post-flight rejection (one automatic re-ask, then a
        # loud FAILED instead of a silent OK).
        if not doc.get("key_facts"):
            words = sum(len(t.split()) for t in page_texts.values())
            min_words = _config_get("empty_extraction_min_words", _EMPTY_EXTRACTION_MIN_WORDS)
            if words >= min_words:
                errors.append(
                    f"document.key_facts is empty despite {words} words of source text — this "
                    "looks like a failed or skipped extraction, not a genuinely fact-free "
                    "document; re-read the source and extract its material facts"
                )

    entities = data.get("entities")
    if not isinstance(entities, list):
        errors.append("missing or invalid 'entities' field")
    else:
        for i, ent in enumerate(entities):
            if not isinstance(ent, dict):
                errors.append(f"entities[{i}] is not an object")
                continue
            for field in ("id", "name", "type"):
                if not ent.get(field):
                    errors.append(f"entities[{i}].{field} is missing or empty")
            for j, role in enumerate(ent.get("roles", [])):
                if not isinstance(role, dict):
                    errors.append(f"entities[{i}].roles[{j}] must be an object with relationship/target_id/page/basis/date_range keys — not a string")

    if not data.get("morgue_entity_id"):
        errors.append("morgue_entity_id is missing or empty — this is the kebab-case id of the entity this document is primarily about")
    if not data.get("morgue_document_type"):
        errors.append("morgue_document_type is missing or empty — use a slug like annual-report, court-order, bankruptcy-filing")

    return errors


def _sanitize_entity_ids(extraction: dict) -> list[str]:
    """Slugify every entity ``id`` before it can reach write_vault, which uses it verbatim as a
    filesystem path segment (#303) — an unslugified id like ``"../../../ESCAPED"`` is a
    path-traversal / vault-escape write primitive. Ids are already meant to be kebab-case slugs
    (see extract_instructions.md), so this is a no-op warning-free pass for a well-formed
    extraction; it only changes anything for a malformed or hostile value.

    Falls back to a slug of the entity's ``name``, then a generic placeholder, if the id
    slugifies to empty, and disambiguates any collision this creates between two entities in the
    same extraction (so they don't silently merge). Remaps ``key_facts.entities`` tags and
    ``roles.target_id`` so referential integrity holds after the rewrite. Mutates ``extraction``
    in place; returns a warning per id changed other than by a plain tidy (spaces, case, stray
    punctuation), which is silent."""
    from watchdog.pipeline.write_vault import slugify

    entities = extraction.get("entities", [])
    changes: list[tuple[str, str]] = []   # (old_id, new_id) for every id actually rewritten
    seen: set[str] = set()
    warnings: list[str] = []

    for i, entity in enumerate(entities):
        old_id = entity.get("id", "")
        new_id = slugify(old_id) or slugify(entity.get("name", "")) or f"entity-{i + 1}"
        base_id, suffix = new_id, 2
        while new_id in seen:
            new_id = f"{base_id}-{suffix}"
            suffix += 1
        seen.add(new_id)
        if new_id != old_id:
            # A plain tidy ("Acme Corp" → "acme-corp") is routine and silent (#757); a value that
            # names a path, slugifies to nothing or collides is worth a reader's attention.
            tidy = bool(old_id) and slugify(old_id) == new_id and not _PATHLIKE.search(old_id)
            if not tidy:
                warnings.append(f"entities[{i}].id {old_id!r} sanitized to {new_id!r}")
            if old_id:
                changes.append((old_id, new_id))
            entity["id"] = new_id

    # Only remap references for an old id that no longer names any surviving entity. If a
    # duplicate id was disambiguated (two "acme-corp" entities → the second becomes
    # "acme-corp-2"), the original id still belongs to the first entity, so references to it
    # must stay put rather than being misrouted to the renamed duplicate.
    remap = {old: new for old, new in changes if old not in seen}
    if remap:
        for fact in extraction.get("document", {}).get("key_facts", []):
            tags = fact.get("entities")
            if tags:
                fact["entities"] = [remap.get(t, t) for t in tags]
        for entity in entities:
            for role in entity.get("roles", []):
                tid = role.get("target_id")
                if tid in remap:
                    role["target_id"] = remap[tid]

    return warnings


def _resolve_near_miss_tags(extraction: dict) -> None:
    """Point a fact's entity tag at the entity the document meant when the tag is a near miss of
    an id the same extraction defined (#757): the same id but for spacing, case or punctuation
    ("acme corp" for "acme-corp"), or a shorter or longer form of it ("acme" for "acme-corp",
    "acme-corp-inc" for "acme-corp") that matches exactly one defined id at its start or end.
    A tag that matches no id, or several, is left alone, and `explode_key_facts` warns about it.
    Mutates the tags in place, keeping each fact's original tags in `extracted_entities`."""
    from watchdog.pipeline.write_vault import slugify
    ids = [e.get("id") for e in extraction.get("entities") or [] if isinstance(e, dict) and e.get("id")]
    known = set(ids)
    split = {i: i.split("-") for i in ids}

    def near(tag: str) -> str | None:
        slug = slugify(tag)
        if slug in known:
            return slug
        words = slug.split("-") if slug else []
        if not words:
            return None
        hits = []
        for eid, parts in split.items():
            short, long_ = (words, parts) if len(words) < len(parts) else (parts, words)
            if len(short) == len(long_) or len("-".join(short)) < 4:
                continue
            if long_[:len(short)] == short or long_[-len(short):] == short:
                hits.append(eid)
        return hits[0] if len(hits) == 1 else None

    for fact in (extraction.get("document") or {}).get("key_facts") or []:
        tags = fact.get("entities") if isinstance(fact, dict) else None
        if not isinstance(tags, list) or all(t in known for t in tags):
            continue
        fixed: list = []
        for t in tags:
            r = t if t in known or not isinstance(t, str) else (near(t) or t)
            if r not in fixed:
                fixed.append(r)
        if fixed != tags:
            fact.setdefault("extracted_entities", list(tags))
            fact["entities"] = fixed


def _single_page_citations(extraction: dict, page_texts: dict[int, str]) -> None:
    """A document whose text is one page — a plain-text, CSV or Markdown file
    (`preprocess.process_direct_text`) — has nothing but page 1 to cite. A large one is extracted
    in sections cut by characters, with no page markers, so the model cites the section's part
    number or a page the text itself mentions; its quotes were then looked for on a page that does
    not exist and came back "not found" (#757). Every fact of such a document cites page 1."""
    if len(page_texts) != 1 or 1 not in page_texts:
        return
    for fact in (extraction.get("document") or {}).get("key_facts") or []:
        if isinstance(fact, dict) and fact.get("page") != 1:
            fact["page"] = 1


# The date fields a dated fact carries onto its timeline events (D329, D330): an optional end for
# a range, the printed wording when code or the model rewrote it, and `date_basis: "inferred"`
# when the model settled it.
DATE_FIELDS = ("date_end", "date_text", "date_basis")


def _timeline_event(fact: dict, text: str) -> dict:
    event = {"date": (fact.get("date") or "").strip(), "event": text}
    for k in DATE_FIELDS:
        if fact.get(k):
            event[k] = fact[k]
    if fact.get("page") is not None:
        event["page"] = fact["page"]
    if fact.get("basis"):
        event["basis"] = fact["basis"]
    # The figure annotations ride along too (D215). `leads.py` reads roles and timeline_events —
    # not evidence_fragments — so without this a derived figure is invisible to the lead sweep no
    # matter what that sweep looks for.
    for k in ("figures_unverified", "figures_off_page"):
        if fact.get(k):
            event[k] = fact[k]
    return event


def refresh_timeline_events(extraction: dict) -> None:
    """Rebuild every entity's `timeline_events` from the dated `key_facts` — after the date pass
    (D330) changed a staged extraction's dates, so the entity views agree with the facts."""
    by_id = {e["id"]: e for e in extraction.get("entities", []) if isinstance(e, dict) and e.get("id")}
    for ent in by_id.values():
        ent.pop("timeline_events", None)
    for fact in (extraction.get("document") or {}).get("key_facts") or []:
        text = (fact.get("fact") or "").strip() if isinstance(fact, dict) else ""
        if not text or not (fact.get("date") or "").strip():
            continue
        for eid in dict.fromkeys(fact.get("entities") or []):
            if eid in by_id:
                by_id[eid].setdefault("timeline_events", []).append(_timeline_event(fact, text))


def explode_key_facts(extraction: dict) -> list[str]:
    """Reconstruct the per-entity views from the unified `key_facts` primitive (#140).

    The model emits each material fact once on ``document.key_facts``, tagged with the entity ids
    it concerns and an optional ``date``. Here we deterministically fan those tags back out onto
    each entity as ``evidence_fragments`` (every tagged fact) and ``timeline_events`` (tagged facts
    that carry a date), the per-entity shapes that write_vault already renders. Mutates the entities
    in place. The document-level ``key_facts`` are left intact (the document note and the global
    timeline read them directly).

    A tag naming an id absent from ``entities`` is dropped rather than filed anywhere — there is
    nothing to attach it to, and inventing an entity here would bypass the schema's own required
    fields (name, type). A near miss of a defined id is pointed at it first
    (`_resolve_near_miss_tags`, #757); for the rest this returns one warning per dropped tag, so
    the loss is visible to the ingest log instead of silent.
    """
    by_id = {e["id"]: e for e in extraction.get("entities", []) if e.get("id")}
    warnings: list[str] = []
    for i, fact in enumerate(extraction.get("document", {}).get("key_facts", [])):
        text = (fact.get("fact") or "").strip()
        if not text:
            continue
        page = fact.get("page")
        basis = fact.get("basis")
        quote = fact.get("quote")
        quote_verified = fact.get("quote_verified")
        quote_found_page = fact.get("quote_found_page")
        quote_spans_pages = fact.get("quote_spans_pages")
        figures_unverified = fact.get("figures_unverified")
        figures_off_page = fact.get("figures_off_page")
        date = (fact.get("date") or "").strip()
        for eid in fact.get("entities", []) or []:
            ent = by_id.get(eid)
            if ent is None:
                warnings.append(
                    f"document.key_facts[{i}] tags entity id {eid!r}, which is not in "
                    "'entities' — dropped, not filed under any entity"
                )
                continue
            frag = {"claim": text}
            if page is not None:
                frag["page"] = page
            if basis:
                frag["basis"] = basis
            if quote:
                frag["quote"] = quote
            if quote_verified is False:
                frag["quote_verified"] = quote_verified
            if quote_found_page is not None:
                frag["quote_found_page"] = quote_found_page
            if quote_spans_pages:
                frag["quote_spans_pages"] = quote_spans_pages
            if figures_unverified:
                frag["figures_unverified"] = figures_unverified
            if figures_off_page:
                frag["figures_off_page"] = figures_off_page
            ent.setdefault("evidence_fragments", []).append(frag)
            if date:
                ent.setdefault("timeline_events", []).append(_timeline_event(fact, text))
    return warnings


def run(vault: Path, extraction_path: Path, warn=None) -> dict:
    """`warn`, when given, receives each warning message instead of the default raw
    ``print(..., file=sys.stderr)`` — the caller can route it through a live-region-aware
    printer and the ingest log (a raw stderr print during a LiveRegion redraw can be erased by
    the next redraw's cursor-up/erase, and it never reaches ingest.log)."""
    def _warn(msg: str) -> None:
        if warn is not None:
            warn(msg)
        else:
            print(f"Warning: {msg}", file=sys.stderr)

    if not extraction_path.exists():
        return {"errors": [f"extraction file not found: {extraction_path}"]}

    try:
        extraction = _read_json(extraction_path)
    except json.JSONDecodeError as e:
        return {"errors": [f"invalid JSON: {e}"]}

    # Get page text from the queue file (captured at chew time) — needed by _validate's
    # empty-extraction check below as well as the deterministic checks further down. Read
    # before validation so the empty-extraction check can weigh actual source text volume
    # rather than the document's nominal page count (#507/#510: a page count is a poor proxy
    # for how much there was to extract from — a scanned exhibit-heavy filing can be nominally
    # long but nearly blank, and a dense short order can carry more real text than a longer,
    # sparser one). Near-dup minhash is no longer read here — write_vault (and the neardup_data
    # it needs) now runs at commit time (#403 phase 1), not here.
    sha256 = extraction.get("document", {}).get("sha256", "")
    page_texts: dict[int, str] = {}
    processing: dict = {}
    if sha256:
        queue_file = vault / ".watchdog" / "queue" / f"{sha256}.json"
        if queue_file.exists():
            try:
                q = json.loads(queue_file.read_text(encoding="utf-8"))
                page_texts = {
                    p["page"]: p.get("markdown", "")
                    for p in q.get("pages", []) if p.get("page") is not None
                }
                processing = q.get("metadata", {})
            except Exception:
                pass

    errors = _validate(extraction, page_texts)
    if errors:
        return {"errors": errors}

    # Slugify entity ids before anything downstream uses them as a path segment (#303) —
    # a warning per id actually changed, so a malicious/malformed value is visible, not silent.
    for warning in _sanitize_entity_ids(extraction):
        _warn(warning)

    # Read every printed date before it can reach explode_key_facts or timeline.py's filename
    # construction (D329): ISO or settled by code, else kept as printed and left for finalize's
    # date pass (D330), which reports what it could not place.
    from watchdog.pipeline import dates
    for warning in dates.sanitize(extraction):
        _warn(warning)

    # A fact tag that is a near miss of an id the document defined is pointed at it (#757).
    _resolve_near_miss_tags(extraction)

    # A one-page text has only page 1 to cite (#757); set before quotes are looked up.
    _single_page_citations(extraction, page_texts)

    # Deterministic quote resolution against the morgue text (#267/#529): resolves each
    # key_facts.quote_locator (the first several words of a source sentence) against the
    # cited page's text into a full quote, flagging any that can't be matched on (or near) that
    # page — annotation only, never blocks the document. Runs before explode_key_facts so the
    # fan-out below copies an already-resolved quote onto each entity's evidence fragment.
    from watchdog.pipeline.quote_verify import resolve_quotes
    for warning in resolve_quotes(extraction, page_texts):
        _warn(warning)

    # Deterministic figure grounding against the morgue text (#363): flags any stated
    # key_fact whose numeric figures can't all be found on (or near) the cited page —
    # advisory only, never blocks the document. Ordered between the two neighbours on
    # purpose (#623): after resolve_quotes, whose D177 page correction changes the very page
    # this reads; before explode_key_facts, so the fan-out copies the annotation onto each
    # entity's evidence fragment the way it already copies the resolved quote.
    from watchdog.pipeline.figure_verify import verify_figures
    for warning in verify_figures(extraction, page_texts):
        _warn(warning)

    # A source passage for every fact (D270): the resolved quote where there is one, otherwise
    # the best-matching sentence on the cited page, or "unlocated". After the two steps above,
    # which settle the page and quote this reads; staged only, like everything here (I7).
    from watchdog.pipeline.passages import locate_passages
    for warning in locate_passages(extraction, page_texts):
        _warn(warning)

    # Fan the unified key_facts out into the per-entity evidence_fragments / timeline_events that
    # write_vault and timeline staging consume (#140).
    for warning in explode_key_facts(extraction):
        _warn(warning)

    # Deterministic date-mismatch check (#369): flags a file whose embedded creation date
    # postdates its claimed date_of_document by a suspicious margin — annotation only, never
    # blocks the document, and silent for OCR'd documents (see file_metadata.check_date_mismatch).
    from watchdog.pipeline.file_metadata import check_date_mismatch
    for warning in check_date_mismatch(extraction, processing):
        _warn(warning)

    # Skim-detection heuristic (#339), computed after the sanitization above so it reflects the
    # facts that actually persist. Persisted structurally on the document record — `coverage_gap`
    # is written explicitly even when None, so a record with the key present means "assessed by
    # the gap detector" (#339 follow-up: benchmark scoring and gap-frequency questions no longer
    # need to scrape ingest.log).
    page_count = extraction.get("document", {}).get("page_count")
    gap = _find_coverage_gap(extraction, page_count)
    extraction["document"]["coverage_gap"] = gap
    if gap:
        _warn(_render_coverage_warning(gap, page_count))

    # Stage the validated (and sanitized) extraction durably (#403 phase 1) — a sibling of
    # .watchdog/queue/ and .watchdog/staging/, deliberately not under .watchdog/tmp/ (swept by
    # abort.run) nor .watchdog/staging/<sha>/ (already the chewed-original path, preprocess_batch).
    # Never cleaned up on success: the commit pass (orchestrate._commit_pending) replays
    # write_vault.run() over it, and it stays afterward as an audit record of what the model
    # actually produced.
    extracted_dir = vault / ".watchdog" / "extracted"
    extracted_dir.mkdir(parents=True, exist_ok=True)
    (extracted_dir / f"{sha256}.json").write_text(
        json.dumps(extraction, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Stage raw timeline NDJSON files (replaces the pre-D18 subagent's manual per-date writes).
    # Stays here rather than moving to the commit pass: `.watchdog/timeline/` is not the vault
    # proper (it is cross-document-deduped and rendered into timeline.md only after `_post_ingest`
    # runs, same as before this refactor), and staging is a pure function of this extraction —
    # it needs no committed registry state. A staging failure is reported as a warning rather
    # than failing the whole extraction — erroring here would trigger a retry and double-stage.
    try:
        from watchdog.pipeline.timeline import stage_timeline_events
        stage_timeline_events(vault, extraction)
    except Exception as e:
        print(f"Warning: timeline staging failed: {e}", file=sys.stderr)

    # Clean up temp files. `extraction_path` (the caller's transient tmp copy) is never the
    # durable artifact — that now lives at `extracted_dir / f"{sha256}.json"`, written above.
    for path in [
        extraction_path,
        vault / ".watchdog" / "tmp" / f"wdg_nd_{sha256}.json",
    ]:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    return {"ok": True, "sha256": sha256}
