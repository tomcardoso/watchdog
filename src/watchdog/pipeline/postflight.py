"""
Watchdog post-flight — validates an extraction JSON and stages it as a durable artifact.

Handles everything after Claude produces the extraction JSON:
  1. Validates the extraction (schema + required fields)
  2. Reads near-dup minhash from the queue file
  3. Writes the validated/sanitized/exploded extraction to `.watchdog/extracted/<sha>.json`
  4. Cleans up temp files
  5. Returns {"ok": true} or {"errors": [...]}

**Post-flight no longer writes to the vault** (#403 phase 1). It used to call `write_vault.run()`
directly, so the vault populated progressively, one document at a time, as extraction completed.
Now it stages the validated extraction as a durable artifact instead, and a serial commit pass at
the top of `orchestrate.finalize` replays `write_vault.run()` over every staged-but-uncommitted
artifact, sorted by sha. The artifact is never cleaned up on success — it doubles as an audit
record of what the model actually produced, and it is what makes a document's extraction durable
and reusable rather than transient pipeline state.

Entity *resolution* is not done here (#381/D118). Post-flight used to apply the extractor's
`match_id` merge decisions, but an extractor that reads one document can only resolve against the
documents that happened to land before it. Two deterministic passes now cover it instead:
`write_vault._reconcile_entity_ids` folds exact normalized-name duplicates in-lock at write time,
and the finalizer's `reconcile` pass resolves the name *variants* that need judgement, once, with
every document's entities in view.
"""

import json
import re
import sys
from datetime import date as _date
from pathlib import Path

_VALID_BASIS = {"stated", "inferred"}
_DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")

# A *precise* non-ISO date — full day-month-year, or (deliberately, per the extraction prompt)
# month-year when the source itself gave no day — spelled with a named month, so the word order
# resolves the day/month ambiguity a numeric date (03/04/2020) can't. Deliberately does NOT
# attempt to parse numeric or slash-separated dates ("2024/03", "03/04/2020"): those are
# genuinely ambiguous (DD/MM vs MM/YY vs YY/MM) and guessing would silently substitute a value
# the source didn't unambiguously state — exactly what TRANSCRIBE, DON'T CORRECT forbids.
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_ORDINAL_SUFFIX = r"(?:\s*(?:st|nd|rd|th))?"
_MONTH_DAY_YEAR_RE = re.compile(
    rf"^(?P<month>[A-Za-z]+)\.?\s+(?P<day>\d{{1,2}}){_ORDINAL_SUFFIX},?\s+(?P<year>\d{{4}})$"
)
_DAY_MONTH_YEAR_RE = re.compile(
    rf"^(?P<day>\d{{1,2}}){_ORDINAL_SUFFIX}\s+(?:day\s+of\s+)?(?P<month>[A-Za-z]+)\.?,?\s+(?P<year>\d{{4}})$"
)
_MONTH_YEAR_RE = re.compile(r"^(?P<month>[A-Za-z]+)\.?,?\s+(?P<year>\d{4})$")


def _parse_precise_date(raw: str) -> str | None:
    """Parse `raw` into ISO shape when — and only when — it is a *precise* date rather than an
    imprecise one the extractor was always going to hand back with less-than-day granularity
    (a bare year, a month-year, per extract_instructions.md's `date` field). A fully-qualified
    date in ordinary prose ("April 30, 2020", "17th day of March, 2021") is precise, just not
    ISO-shaped, and converting it loses nothing. A fiscal-year range ("2020-2021"), a quarter
    ("Q1 2020"), or a bare numeric date is genuinely imprecise or ambiguous and must stay
    dropped rather than have this guess at what it means — this function returns None for those,
    and the caller's existing drop+warn behaviour is unchanged. Named-month forms only (see
    `_MONTHS`); returns None on no match or an invalid day-of-month (e.g. "February 30")."""
    text = raw.strip()
    for rx, has_day in ((_MONTH_DAY_YEAR_RE, True), (_DAY_MONTH_YEAR_RE, True), (_MONTH_YEAR_RE, False)):
        m = rx.match(text)
        if not m:
            continue
        month = _MONTHS.get(m.group("month").lower())
        if month is None:
            continue
        year = int(m.group("year"))
        if not has_day:
            return f"{year:04d}-{month:02d}"
        day = int(m.group("day"))
        try:
            _date(year, month, day)   # validates day-in-month (rejects "February 30, 2020")
        except ValueError:
            return None
        return f"{year:04d}-{month:02d}-{day:02d}"
    return None

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
    try:
        cfg = json.loads((Path.home() / ".watchdog" / "config.json").read_text())
    except Exception:
        cfg = {}
    return cfg.get(key, default)


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
    in place; returns a warning per changed id."""
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


def _sanitize_dates(extraction: dict) -> list[str]:
    """Normalize or drop ``key_facts.date`` values that aren't already ISO-shaped (``YYYY``,
    ``YYYY-MM``, or ``YYYY-MM-DD``) before they can reach timeline.py's ``{date}_{sha7}.ndjson``
    filename construction — a value like ``"2024/03"`` or free text would otherwise produce a
    broken or nested file write.

    The extraction prompt deliberately transcribes dates as printed rather than reformatting
    them (TRANSCRIBE, DON'T CORRECT), so a non-ISO value here is routinely a *precise* date the
    source just didn't print in ISO form ("April 30, 2020") rather than an imprecise one — the
    prompt already tells the model to omit the day when the source doesn't give one, so a bare
    "May 2019" is doing that correctly, just not ISO-shaped either (#560). ``_parse_precise_date``
    converts either shape losslessly; only a value it can't parse as unambiguous — a fiscal-year
    range, a quarter, free text — is dropped. Mutates ``key_facts`` in place; returns a warning
    only for an actual drop, so a silent, lossless reformat doesn't add warning noise."""
    warnings: list[str] = []
    for i, fact in enumerate(extraction.get("document", {}).get("key_facts", [])):
        date = fact.get("date")
        if not date or _DATE_RE.match(date):
            continue
        parsed = _parse_precise_date(date)
        if parsed is not None:
            fact["date"] = parsed
            continue
        warnings.append(
            f"document.key_facts[{i}].date '{date}' is not a recognizable ISO or precise "
            "calendar date — dropped from timeline placement"
        )
        fact["date"] = ""
    return warnings


def explode_key_facts(extraction: dict) -> None:
    """Reconstruct the per-entity views from the unified `key_facts` primitive (#140).

    The model emits each material fact once on ``document.key_facts``, tagged with the entity ids
    it concerns and an optional ``date``. Here we deterministically fan those tags back out onto
    each entity as ``evidence_fragments`` (every tagged fact) and ``timeline_events`` (tagged facts
    that carry a date), the per-entity shapes that write_vault already renders. Mutates the entities
    in place. The document-level ``key_facts`` are left intact (the document note and the global
    timeline read them directly).
    """
    by_id = {e["id"]: e for e in extraction.get("entities", []) if e.get("id")}
    for fact in extraction.get("document", {}).get("key_facts", []):
        text = (fact.get("fact") or "").strip()
        if not text:
            continue
        page = fact.get("page")
        basis = fact.get("basis")
        quote = fact.get("quote")
        quote_verified = fact.get("quote_verified")
        quote_found_page = fact.get("quote_found_page")
        date = (fact.get("date") or "").strip()
        for eid in fact.get("entities", []) or []:
            ent = by_id.get(eid)
            if ent is None:
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
            ent.setdefault("evidence_fragments", []).append(frag)
            if date:
                event = {"date": date, "event": text}
                if page is not None:
                    event["page"] = page
                if basis:
                    event["basis"] = basis
                ent.setdefault("timeline_events", []).append(event)


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
        extraction = json.loads(extraction_path.read_text(encoding="utf-8"))
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

    # Drop non-ISO-shaped key_facts dates before they can reach explode_key_facts or
    # timeline.py's filename construction — a malformed date is a visible warning, not a
    # silent event loss.
    for warning in _sanitize_dates(extraction):
        _warn(warning)

    # Deterministic quote resolution against the morgue text (#267/#529): resolves each
    # key_facts.quote_locator (the first several words of a source sentence) against the
    # cited page's text into a full quote, flagging any that can't be matched on (or near) that
    # page — annotation only, never blocks the document. Runs before explode_key_facts so the
    # fan-out below copies an already-resolved quote onto each entity's evidence fragment.
    from watchdog.pipeline.quote_verify import resolve_quotes
    for warning in resolve_quotes(extraction, page_texts):
        _warn(warning)

    # Fan the unified key_facts out into the per-entity evidence_fragments / timeline_events that
    # write_vault and timeline staging consume (#140).
    explode_key_facts(extraction)

    # Deterministic figure grounding against the morgue text (#363): flags any stated
    # key_fact whose numeric figures can't all be found on (or near) the cited page —
    # advisory only, never blocks the document.
    from watchdog.pipeline.figure_verify import verify_figures
    for warning in verify_figures(extraction, page_texts):
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


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Watchdog post-flight processor")
    parser.add_argument("--extraction", required=True, help="Path to extraction JSON")
    args = parser.parse_args()

    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")

    extraction_path = Path(args.extraction).resolve()
    if not str(extraction_path).startswith(str(vault) + "/"):
        sys.exit(f"Error: --extraction must be inside the vault directory ({vault})")

    result = run(vault, extraction_path)
    print(json.dumps(result, ensure_ascii=False))
    if "errors" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
