"""Bringing an existing investigation up to date as the pipeline improves (D336).

Most pipeline improvements run while documents are processed, so an investigation never gets them
for the documents it already holds. A **migration** is one registered step that applies the current
pipeline's own code to what an investigation stored: an id, a title and a one-line description the
app shows, whether it calls a model, an `estimate` and an `apply`. `MIGRATIONS` lists them in the
order they run. Every pipeline change that alters stored records adds one here (or says in its
decision why it needs none), so old investigations keep up.

**The stamp.** Each investigation records the migrations it has had in `.watchdog/migrations.json`
(app-owned, outside `registry/`, read by no model input and by no history version)::

    {"schema_version": 1, "applied": {"<id>": {"at", "version", "summary"}},
     "banner_dismissed": ["<id>", ...]}

A new investigation is stamped with every current migration when it is created
(`ops.projects.create`, the sample included), and an older one that had no documents yet when the
stamp arrived is stamped at its first commit (`stamp_if_new`), so neither ever sees the banner.

**The run** (`run`, the `migrate` operation). One estimate up front (`plan`). The pending steps run
in order under the processing lock, as a run holds it, with one version of the vault's history and
the calls recorded in the usage files. A model step checks the spending limit before it starts and
between its calls (D324); a stop, a failed call or Stop leaves that step and every later one
pending, so the next run resumes there. A step is recorded as applied only when it finished. After
the steps the notes and the timeline are rebuilt from stored data (free), unless the reporter
stopped the run.

**Rules for a migration.** It applies the current pipeline's logic only, never more than the same
step would do to the document on a fresh run; it is safe to run twice (an item looked at once is
marked, so it is not asked again); every merge goes through the logged, undoable path; and the
reporter's marks (verification, handled items, removed entities, split groups) are kept, a
committed document's derived records following its extraction through `committed.sync`.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from watchdog import progress
from watchdog.pipeline.json_io import _read_json_or

SCHEMA_VERSION = 1
STAMP_FILE = "migrations.json"

BUSY = ("Documents are being added to this investigation. Bring it up to date when that has "
        "finished.")


class Busy(RuntimeError):
    """A run holds the vault."""


# ── the stamp ────────────────────────────────────────────────────────────────────────────

def stamp_path(vault: Path) -> Path:
    return Path(vault) / ".watchdog" / STAMP_FILE


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(vault: Path) -> dict:
    data = _read_json_or(stamp_path(vault), None)
    if not isinstance(data, dict) or not isinstance(data.get("applied"), dict):
        data = {"schema_version": SCHEMA_VERSION, "applied": {}, **(data if isinstance(data, dict) else {})}
        data["applied"] = {}
    if not isinstance(data.get("banner_dismissed"), list):
        data["banner_dismissed"] = []
    return data


def _save(vault: Path, data: dict) -> None:
    path = stamp_path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _version() -> str:
    try:
        from watchdog.vault import pkg_version
        return pkg_version()
    except Exception:  # noqa: BLE001 — the version is a note, never a reason to fail
        return ""


def stamp_current(vault: Path, how: str = "created") -> None:
    """Record every current migration as applied: a new investigation needs none of them."""
    data = load(vault)
    for m in MIGRATIONS:
        data["applied"].setdefault(m.id, {"at": _now(), "version": _version(), "summary": how})
    _save(vault, data)


def stamp_if_new(vault: Path) -> None:
    """Stamp an investigation that has no stamp and no committed document yet: everything it
    will hold is processed by this pipeline. Called at the start of every commit pass."""
    if stamp_path(vault).exists():
        return
    docs = _read_json_or(Path(vault) / ".watchdog" / "registry" / "documents.json", {})
    if not docs:
        try:
            stamp_current(vault, "first documents")
        except OSError:
            pass


def mark_applied(vault: Path, mid: str, summary: str = "") -> None:
    data = load(vault)
    data["applied"][mid] = {"at": _now(), "version": _version(), "summary": summary}
    _save(vault, data)


def committed_shas(vault: Path) -> list[str]:
    """The committed documents that have a stored extraction, in sha order."""
    docs = _read_json_or(Path(vault) / ".watchdog" / "registry" / "documents.json", {})
    root = Path(vault) / ".watchdog" / "extracted"
    return sorted(sha for sha in (docs if isinstance(docs, dict) else {})
                  if (root / f"{sha}.json").exists())


def pending(vault: Path) -> list["Migration"]:
    """The migrations this investigation has not had, in order. None for the sample (built by
    this version from its prepared story) or for an investigation with no committed document."""
    from watchdog.ops import sample
    vault = Path(vault)
    if sample.is_sample_folder(vault) or not committed_shas(vault):
        return []
    done = set(load(vault)["applied"])
    return [m for m in MIGRATIONS if m.id not in done]


def _fingerprint(vault: Path) -> str:
    """Changes whenever the records a step's estimate reads change: the registry's two main files
    and the folder of saved extractions."""
    parts = []
    for p in (vault / ".watchdog" / "registry" / "documents.json",
              vault / ".watchdog" / "registry" / "entities.json", vault / ".watchdog" / "extracted"):
        try:
            st = p.stat()
            parts.append(f"{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append("-")
    return "|".join(parts)


def settle_empty(vault: Path) -> list[str]:
    """Record as applied every pending step whose estimate finds nothing to do, so the banner and
    Maintenance only offer steps that would change something. The counts are cached against the
    records' fingerprint, so the estimates run again only after the records change. A step whose
    estimate fails is left pending. Returns the ids settled."""
    from watchdog.pipeline import history
    vault = Path(vault)
    steps = pending(vault)
    if not steps or history.run_in_progress(vault):   # never judge records a run is changing
        return []
    data = load(vault)
    fp = _fingerprint(vault)
    checked = data.get("checked") if isinstance(data.get("checked"), dict) else {}
    if checked.get("fp") == fp and all(m.id in (checked.get("items") or {}) for m in steps):
        counts = checked["items"]
    else:
        ctx = _context(vault, say=lambda m: None)
        counts = {}
        for m in steps:
            try:
                counts[m.id] = int(m.estimate(ctx).get("items") or 0)
            except Exception:  # noqa: BLE001 — an estimate that fails leaves the step pending
                counts[m.id] = -1
        data = load(vault)
        data["checked"] = {"fp": fp, "items": counts}
        _save(vault, data)
    settled = [m.id for m in steps if counts.get(m.id) == 0]
    for mid in settled:
        mark_applied(vault, mid, "nothing to do")
    return settled


def status(vault: Path) -> dict:
    """What the app shows: the pending steps, whether the Overview's one-time banner is due (a
    pending step the reporter has not dismissed it for), and how many steps were applied. Steps
    with nothing to do are settled first (`settle_empty`)."""
    settle_empty(vault)
    data = load(vault)
    steps = pending(vault)
    dismissed = set(data["banner_dismissed"])
    return {"pending": [m.public() for m in steps],
            "banner": any(m.id not in dismissed for m in steps),
            "applied": len([m for m in MIGRATIONS if m.id in data["applied"]])}


def dismiss_banner(vault: Path) -> None:
    """Hide the Overview banner for the steps pending now; a later migration shows it again."""
    data = load(vault)
    ids = [m.id for m in pending(vault)]
    data["banner_dismissed"] = sorted(set(data["banner_dismissed"]) | set(ids))
    _save(vault, data)


# ── the context a step runs in ───────────────────────────────────────────────────────────

@dataclass
class Context:
    vault: Path
    shas: list[str]
    stages: dict          # stage -> (backend, model)
    effort: str | None
    say: Callable[[str], None]
    warn: Callable[[str], None]

    def model(self, stage: str) -> tuple[str | None, str]:
        return self.stages[stage]


def stages() -> tuple[dict, str | None]:
    """The models the steps run on, resolved from the settings by the code post-processing uses:
    every step here is finishing-step housekeeping (D339), so all three keys name that model."""
    from watchdog.ops.ingest import RunOptions, resolve_plan
    plan = resolve_plan(RunOptions())
    o = plan.overrides
    housekeeping = (o["housekeeping_backend"], o["housekeeping_model"])
    return ({"reconciliation": housekeeping, "timeline": housekeeping, "requests": housekeeping},
            plan.post_effort)


def _context(vault: Path, say=print, warn=None) -> Context:
    st, effort = stages()
    return Context(Path(vault), committed_shas(vault), st, effort, say, warn or say)


# ── the registry of migrations ───────────────────────────────────────────────────────────

@dataclass
class Migration:
    id: str
    title: str
    description: str
    calls_model: bool
    stage: str | None                                  # the model stage it runs on
    estimate: Callable[[Context], dict]                # {"items", "calls", "est_tokens", "detail"}
    apply: Callable[[Context], Awaitable[dict]]        # {"complete", "summary", ...counts}

    def public(self) -> dict:
        return {"id": self.id, "title": self.title, "description": self.description,
                "calls_model": self.calls_model}


def _plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n:,} {word if n == 1 else (many or word + 's')}"


# 1. Free fixes ───────────────────────────────────────────────────────────────────────────

def _page_texts(vault: Path, record: dict | None) -> dict[int, str]:
    """A committed document's page text, read back from its converted text in the morgue."""
    from watchdog.ops.reindex import _pages_from_morgue_text
    morgue = (record or {}).get("morgue_path")
    md = Path(vault) / Path(morgue).with_suffix(".md") if morgue else None
    if not md or not md.exists():
        return {}
    try:
        return {p["page"]: p["markdown"] for p in _pages_from_morgue_text(md.read_text(encoding="utf-8"))}
    except OSError:
        return {}


def _free_fix(art: dict, page_texts: dict[int, str]) -> dict:
    """Post-flight's newer deterministic fixes on one stored extraction, in place (#757): a fact
    tag that is a near miss of an id the document defined is pointed at it; a one-page text's
    facts cite page 1, their quotes, figure checks and (when the document has passages) passages
    found again on that page. Returns what changed. The silent id tidy changed only which tidies
    are warned about; a stored id that would still change is counted, never rewritten, since the
    registry holds it."""
    from watchdog.pipeline import passages, postflight
    from watchdog.pipeline.figure_verify import verify_figures
    from watchdog.pipeline.quote_verify import resolve_quotes
    out = {"tags": 0, "pages": 0, "untidy_ids": 0}
    facts = (art.get("document") or {}).get("key_facts") or []
    before = [list(f.get("entities") or []) if isinstance(f, dict) else None for f in facts]
    postflight._resolve_near_miss_tags(art)
    out["tags"] = sum(1 for f, b in zip(facts, before)
                      if isinstance(f, dict) and list(f.get("entities") or []) != b)
    if len(page_texts) == 1 and 1 in page_texts:
        moved = [f for f in facts if isinstance(f, dict) and (f.get("fact") or "").strip()
                 and f.get("page") != 1]
        for f in moved:
            f["page"] = 1
            for k in ("quote_verified", "quote_found_page", "quote_spans_pages"):
                f.pop(k, None)
        if moved:
            part = {"document": {"key_facts": moved}}
            resolve_quotes(part, page_texts)
            verify_figures(part, page_texts)
            doc = art.get("document") or {}
            if "passages_version" in doc and passages.readable(doc):
                passages.locate(moved, page_texts, force=True)
        out["pages"] = len(moved)
    probe = deepcopy(art)
    postflight._sanitize_entity_ids(probe)
    out["untidy_ids"] = sum(1 for a, b in zip(probe.get("entities") or [], art.get("entities") or [])
                            if isinstance(a, dict) and isinstance(b, dict) and a.get("id") != b.get("id"))
    if out["tags"] or out["pages"]:
        for e in art.get("entities") or []:
            if isinstance(e, dict):
                e.pop("evidence_fragments", None)
                e.pop("timeline_events", None)
        postflight.explode_key_facts(art)
    return out


def _free_fixes_scan(ctx: Context, write: bool) -> dict:
    from watchdog.pipeline import committed
    docs = _read_json_or(ctx.vault / ".watchdog" / "registry" / "documents.json", {})
    totals = {"documents": 0, "tags": 0, "pages": 0, "untidy_ids": 0}
    tracker = committed.Tracker(ctx.vault, ctx.shas) if write else None
    for sha in ctx.shas:
        art = committed.load(ctx.vault, sha)
        if art is None:
            continue
        got = _free_fix(art, _page_texts(ctx.vault, docs.get(sha)))
        for k in ("tags", "pages", "untidy_ids"):
            totals[k] += got[k]
        if got["tags"] or got["pages"]:
            totals["documents"] += 1
            if write:
                committed.save(ctx.vault, sha, art)
    if write:
        totals["sync"] = committed.sync(ctx.vault, tracker.changed())
    return totals


def _free_fixes_estimate(ctx: Context) -> dict:
    t = _free_fixes_scan(ctx, write=False)
    return {"items": t["tags"] + t["pages"], "calls": 0, "est_tokens": 0,
            "detail": f"{_plural(t['tags'], 'fact tag')} to point at the entity meant, "
                      f"{_plural(t['pages'], 'citation')} to move to page 1"}


async def _free_fixes_apply(ctx: Context) -> dict:
    t = _free_fixes_scan(ctx, write=True)
    moved = (t.get("sync") or {}).get("marks_moved") or 0
    summary = (f"Pointed {_plural(t['tags'], 'fact tag')} at the entity its document meant and moved "
               f"{_plural(t['pages'], 'citation')} of one-page texts to page 1")
    if moved:
        summary += f" (your marks on {_plural(moved, 'fact')} moved with them)"
    if t["untidy_ids"]:
        ctx.warn(f"free fixes: {_plural(t['untidy_ids'], 'stored entity id')} would be tidied "
                 "differently today; left as they are, since the records use them")
    return {"complete": True, "summary": summary + ".", **{k: v for k, v in t.items() if k != "sync"}}


# 2. Erased dates ─────────────────────────────────────────────────────────────────────────

def _erased(f) -> bool:
    """A fact whose printed date the pre-D329 parser erased: `date` is "" (the parser's mark;
    a model leaves an undated fact's date null or out) with no `date_text`, not looked at yet."""
    return (isinstance(f, dict) and f.get("date") == "" and not f.get("date_text")
            and not f.get("date_recovery") and bool((f.get("fact") or "").strip()))


def _date_scan(ctx: Context) -> tuple[list, list, int]:
    """(code finds as (sha, index, iso, wording), pass items, facts with no year in their text)."""
    from watchdog.pipeline import committed, dates
    code, items, no_year = [], [], 0
    for sha in ctx.shas:
        art = committed.load(ctx.vault, sha)
        if art is None:
            continue
        doc = art.get("document") or {}
        for i, f in enumerate(doc.get("key_facts") or []):
            if not _erased(f):
                continue
            got = dates.find_in_text(f["fact"])
            if got == dates.NO_YEAR:
                no_year += 1
            elif got == dates.TO_PASS:
                items.append({"sha": sha, "index": i, "text": f["fact"], "fact": f["fact"],
                              "doc_date": doc.get("date_of_document"),
                              "pending": dates.PENDING_UNRESOLVED, "readings": [], "lost": True})
            else:
                code.append((sha, i, *got))
    return code, items, no_year


def _dates_estimate(ctx: Context) -> dict:
    from watchdog.pipeline import dates, housekeeping
    code, items, no_year = _date_scan(ctx)
    calls, chars = housekeeping.estimate_chars(dates.CHORE, items)
    return {"items": len(code) + len(items), "calls": calls, "est_tokens": chars // 4,
            "detail": f"{_plural(len(code), 'date')} read by code, {_plural(len(items), 'date')} for the "
                      f"model, {_plural(no_year, 'fact')} naming no year"}


async def _dates_apply(ctx: Context) -> dict:
    from watchdog.pipeline import committed, dates, orchestrate
    code, items, no_year = _date_scan(ctx)
    tracker = committed.Tracker(ctx.vault, sorted({c[0] for c in code} | {it["sha"] for it in items}))
    by_sha: dict[str, list] = {}
    for sha, i, iso, wording in code:
        by_sha.setdefault(sha, []).append((i, iso, wording))
    from watchdog.pipeline.postflight import refresh_timeline_events
    for sha, found in by_sha.items():
        art = committed.load(ctx.vault, sha)
        facts = art["document"]["key_facts"]
        for i, iso, wording in found:
            facts[i].update({"date": iso, "date_text": wording, "date_recovery": "code"})
        refresh_timeline_events(art)
        committed.save(ctx.vault, sha, art)
    out = {"complete": True, "code": len(code), "sent": len(items), "model": 0, "unsettled": 0,
           "no_year": no_year}
    if items:
        backend, model = ctx.model("timeline")
        res = await orchestrate._run_chore(ctx.vault, dates.CHORE, items, model=model, backend=backend,
                                           effort=ctx.effort, what="dates", stage="dates")
        if res is None:
            out["complete"] = False
        else:
            applied = dates.apply(ctx.vault, items, res, restage=False)
            out["model"] = applied["resolved"]
            out["unsettled"] = len(items) - applied["resolved"] - len(res.unanswered)
            out["complete"] = not res.unanswered
    out["sync"] = committed.sync(ctx.vault, tracker.changed())
    out["summary"] = (f"Placed {_plural(out['code'], 'erased date')} read from the fact's own text and "
                      f"{_plural(out['model'], 'date')} the model read from it (marked inferred); "
                      f"{_plural(out['unsettled'] + out['no_year'], 'fact')} stay undated "
                      f"({out['no_year']:,} name no year), and only reprocessing their documents "
                      "could recover a date that was printed there.")
    return out


# 3. Identical-name groups ────────────────────────────────────────────────────────────────

def _groups_estimate(ctx: Context) -> dict:
    """The first round exactly, as the run will send it (the bundle the run builds, packed into
    calls), plus one more round over the members of every group too large for one slice, priced
    at `reconcile._GROUP_MEMBER_EST_CHARS` a member as `group_estimate` does."""
    import math
    from watchdog.pipeline import chunking, prompts, reconcile
    b = reconcile.build_group_bundle(ctx.vault)
    backend, model = ctx.model("reconciliation")
    budget = chunking.prompt_budget_chars(model, backend, ctx.vault)
    chunks = reconcile.chunk_bundle({"groups": b["groups"]}, budget) if b["groups"] else []
    chars = sum(len(prompts.build_reconcile_prompt(c)) for c in chunks)
    big = sum(len(g["clusters"]) for g in b["name_groups"].groups
              if len(g["clusters"]) > reconcile._GROUP_MEMBERS)
    extra = big * reconcile._GROUP_MEMBER_EST_CHARS
    calls = len(chunks) + (math.ceil(extra / budget) if extra else 0)
    return {"items": len(b["name_groups"].groups), "calls": calls, "est_tokens": (chars + extra) // 4,
            "detail": f"{_plural(b['name_sets'], 'set')} of records with the same name "
                      f"({b['records']:,} records), {_plural(len(b['name_groups'].groups), 'group')} "
                      f"for the model, {_plural(len(b['rule_merges']), 'merge')} code can make alone"}


async def _groups_apply(ctx: Context) -> dict:
    from watchdog.pipeline import orchestrate, reconcile
    bundle = reconcile.build_group_bundle(ctx.vault)
    if not (bundle["groups"] or bundle["rule_merges"] or bundle["candidates"]):
        return {"complete": True, "merged": 0, "candidates": 0, "carried": 0,
                "summary": "No records with the same name were left to compare."}
    backend, model = ctx.model("reconciliation")
    progress.emit("stage", stage="reconcile", done=None, total=None)
    result = await orchestrate._reconcile_pre_commit(
        ctx.vault, [], model, ctx.effort, backend,
        finalizer_overrides={"reconciliation_model": model, "reconciliation_backend": backend},
        bundle=bundle)
    if result.get("error"):
        return {"complete": False, "merged": 0, "candidates": 0, "carried": 0,
                "summary": f"Same-name groups were not compared: {result['error']}"}
    carried = (result.get("carried") or {}).get("records") or 0
    n = len(result["merged"])
    summary = (f"Compared {_plural(bundle['name_sets'], 'set')} of records with the same name: "
               f"{_plural(n, 'merge')}, {_plural(result.get('candidates') or 0, 'possible same entity', 'possible same entities')} "
               "left for you in Review")
    if carried:
        summary += f", {_plural(carried, 'record')} carried to the next run"
    return {"complete": True, "merged": n, "candidates": result.get("candidates") or 0,
            "carried": carried, "summary": summary + "."}


# 4. Housekeeping over every document ─────────────────────────────────────────────────────

def _housekeeping_estimate(ctx: Context) -> dict:
    from watchdog.pipeline import fact_tags, housekeeping, request_sources, role_targets
    parts = ((fact_tags.CHORE, fact_tags.pending_items(ctx.vault, ctx.shas)),
             (role_targets.CHORE, role_targets.estimate_items(ctx.vault, ctx.shas)),
             (request_sources.CHORE, request_sources.estimate_items(ctx.vault, [])))
    calls = chars = 0
    for chore, items in parts:
        c, n = housekeeping.estimate_chars(chore, items)
        calls, chars = calls + c, chars + n
    return {"items": sum(len(i) for _, i in parts), "calls": calls, "est_tokens": chars // 4,
            "detail": f"{_plural(len(parts[0][1]), 'fact tag')} naming no entity, "
                      f"{_plural(len(parts[1][1]), 'relationship target')} named by an unknown id, "
                      f"{_plural(len(parts[2][1]), 'request source')}"}


async def _housekeeping_apply(ctx: Context) -> dict:
    from watchdog.pipeline import (committed, fact_tags, orchestrate, request_sources, requests,
                                   role_targets)
    out = {"complete": True, "retagged": 0, "targets_exact": 0, "targets_matched": 0, "source_groups": 0}
    backend, model = ctx.model("reconciliation")

    # Fact tags that name no entity of their document (D333).
    items = fact_tags.pending_items(ctx.vault, ctx.shas)
    if items:
        tracker = committed.Tracker(ctx.vault, sorted({it["sha"] for it in items}))
        res = await orchestrate._run_chore(ctx.vault, fact_tags.CHORE, items, model=model, backend=backend,
                                           effort=ctx.effort, what="fact tags", stage="fact-tags")
        if res is None:
            return {**out, "complete": False, "summary": "Stopped before the fact tags were matched."}
        out["retagged"] = fact_tags.apply(ctx.vault, items, res, restage=False)["facts"]
        committed.sync(ctx.vault, tracker.changed())
        out["complete"] &= not res.unanswered

    # Relationship targets named by an id no entity has (D332).
    tracker = committed.Tracker(ctx.vault, ctx.shas)
    batch = role_targets.Batch(ctx.vault, ctx.shas)
    out["targets_exact"] = role_targets.resolve_exact(batch)
    items = role_targets.pending_items(batch)
    res = None
    if items and out["complete"]:
        res = await orchestrate._run_chore(ctx.vault, role_targets.CHORE, items, model=model,
                                           backend=backend, effort=ctx.effort,
                                           what="relationship targets", stage="role-targets")
        if res is None:
            out["complete"] = False
        else:
            out["targets_matched"] = role_targets.apply(batch, items, res)["matched"]
            out["complete"] &= not res.unanswered
    committed.sync(ctx.vault, tracker.changed())

    # Request sources named in several ways (D334): already vault-wide, only never-compared keys.
    if out["complete"]:
        r_backend, r_model = ctx.model("requests")
        state: dict = {}
        await orchestrate._request_source_pass(ctx.vault, state, r_model, r_backend, ctx.effort)
        out["source_groups"] = state.get("request_source_groups") or 0
        left = request_sources.items(requests.open_requests(ctx.vault), request_sources.load(ctx.vault))
        out["complete"] &= not any(it["new"] for it in left)

    out["summary"] = (f"Filed {_plural(out['retagged'], 'fact')} under the entity its tag meant, matched "
                      f"{_plural(out['targets_exact'] + out['targets_matched'], 'relationship target')} "
                      f"to an existing entity ({out['targets_matched']:,} by the model, marked inferred) "
                      f"and grouped request sources into {_plural(out['source_groups'], 'place')}.")
    return out


MIGRATIONS: list[Migration] = [
    Migration("2026-10-free-fixes", "Free fixes to saved extractions",
              "Points fact tags that are near misses at the entity their document meant, and moves "
              "the citations of one-page texts to page 1, with their quotes and passages found again. "
              "No AI model is used.",
              False, None, _free_fixes_estimate, _free_fixes_apply),
    Migration("2026-10-erased-dates", "Dates lost by the old date reader",
              "Before October 2026 a date the date reader could not read was erased. Code reads it "
              "back from the fact's own text when the text states exactly one full date; the AI "
              "model reads the rest, and its answers are checked and marked inferred.",
              True, "timeline", _dates_estimate, _dates_apply),
    Migration("2026-10-same-name-groups", "Records with the same name",
              "Compares every group of records that share a name and type, as processing now does "
              "for the groups a new batch touches. Merges are recorded and can be undone; records it "
              "cannot tell apart go to Review.",
              True, "reconciliation", _groups_estimate, _groups_apply),
    Migration("2026-10-housekeeping", "Housekeeping over every document",
              "Matches fact tags and relationship targets that name no entity to the entity meant, "
              "and groups the places document requests can be got from, over every document rather "
              "than only the latest batch.",
              True, "reconciliation", _housekeeping_estimate, _housekeeping_apply),
]


# ── planning and running ─────────────────────────────────────────────────────────────────

def plan(vault: Path, ctx: Context | None = None) -> dict:
    """The pending steps with each one's estimate, and the totals: what the app shows before it
    asks to start. `est_tokens` is chars/4 of the prompts."""
    steps = pending(vault)
    ctx = ctx or _context(vault)
    rows = []
    for m in steps:
        try:
            est = m.estimate(ctx)
        except OSError as e:
            est = {"items": 0, "calls": 0, "est_tokens": 0, "detail": f"could not be estimated: {e}"}
        backend, model = ctx.stages[m.stage] if m.stage else (None, None)
        rows.append({**m.public(), **est, "backend": backend, "model": model})
    return {"steps": rows, "calls": sum(r["calls"] for r in rows),
            "est_tokens": sum(r["est_tokens"] for r in rows),
            "models": sorted({(r["backend"] or "", r["model"]) for r in rows if r["calls"] and r["model"]})}


def cause(titles: list[str]) -> dict:
    return {"kind": "migrate", "steps": titles[:5], "count": len(titles)}


def run(vault: Path, *, say=print, warn=None, check_keys: bool = True,
        ctx: Context | None = None) -> dict:
    """Run every pending migration in order (see the module docstring). Raises `Busy` while a run
    holds the vault. Returns ``{"steps": [{id, title, complete, summary}], "applied", "pending",
    "stopped", "spending_limit", "error", "rebuilt"}``."""
    from watchdog import model_client
    from watchdog.pipeline import entity_notes, history, orchestrate, timeline
    from watchdog.pipeline.ingest_setup import STALE_SECONDS
    from watchdog.pipeline.locks import acquire_or_take_stale, heartbeat, lock_contents, release_lock
    from watchdog.vault_paths import processing_lock

    vault = Path(vault)
    warn = warn or say
    steps = pending(vault)
    out: dict[str, Any] = {"steps": [], "applied": 0, "pending": len(steps), "stopped": False,
                           "spending_limit": None, "error": None, "rebuilt": False}
    if not steps:
        return out
    ctx = ctx or _context(vault, say, warn)
    if check_keys:
        from watchdog.auth import check_run_keys
        check_run_keys(vault, [ctx.stages[m.stage][0] for m in steps if m.calls_model and m.stage])
    lock = processing_lock(vault)
    if not acquire_or_take_stale(lock, lock_contents("migrate"), STALE_SECONDS):
        raise Busy(BUSY)
    titles: list[str] = []
    try:
        with heartbeat(lock):
            c = cause([m.title for m in steps])
            with history.recording(vault, c):
                orchestrate._begin_usage_run(vault)
                try:
                    async def go() -> None:
                        for n, m in enumerate(steps, 1):
                            if m.calls_model:
                                msg = orchestrate._spending_stop()
                                if msg:
                                    out["spending_limit"] = msg
                                    say(msg)
                                    return
                            progress.emit("stage", stage="migrate", done=n - 1, total=len(steps))
                            say(f"Step {n} of {len(steps)}: {m.title}")
                            res = await m.apply(ctx)
                            out["steps"].append({"id": m.id, "title": m.title,
                                                 "complete": bool(res.get("complete")),
                                                 "summary": res.get("summary") or ""})
                            say(f"   {res.get('summary') or ''}".rstrip())
                            if not res.get("complete"):
                                msg = orchestrate._spending_stop()
                                out["spending_limit"] = out["spending_limit"] or msg
                                return
                            mark_applied(vault, m.id, res.get("summary") or "")
                            titles.append(m.title)
                            out["applied"] += 1
                        progress.emit("stage", stage="migrate", done=len(steps), total=len(steps))
                    asyncio.run(go())
                except KeyboardInterrupt:
                    out["stopped"] = True
                except model_client.CALL_FAILURES as e:
                    out["error"] = str(e)
                finally:
                    orchestrate._end_usage_run(vault)
                if not out["stopped"] and out["steps"]:
                    progress.emit("stage", stage="rebuild-notes", done=None, total=None)
                    try:
                        entity_notes.rebuild(vault)
                        timeline.rebuild_timeline(vault, quiet=True)
                        out["rebuilt"] = True
                    except OSError as e:
                        warn(f"notes and timeline not rebuilt: {e}")
                # The version names the steps that finished, and says when some did not.
                c.update(cause(titles))
                if len(titles) < len(steps):
                    c["incomplete"] = True
            out["pending"] = len(pending(vault))
            return out
    finally:
        release_lock(lock)


def summary(out: dict) -> str:
    """The plain-language line the job's log ends with."""
    if not out["steps"] and not out["pending"]:
        return "This investigation is up to date: nothing needed doing."
    head = (f"Brought up to date: {_plural(out['applied'], 'step')} done"
            + (f", {_plural(out['pending'], 'step')} still to do." if out["pending"] else "."))
    if out.get("spending_limit"):
        head += (" Stopped at the investigation's spending limit; raise or reset it on the Overview "
                 "and run this again to finish the rest.")
    elif out.get("stopped"):
        head += " Stopped before every step finished; run it again to finish the rest."
    elif out.get("error"):
        head += f" A model call failed: {out['error']}. Run it again to finish the rest."
    elif out["pending"]:
        head += " Some items could not be settled this time; run it again to finish them."
    if out.get("rebuilt"):
        head += " Notes and the timeline were rebuilt."
    return head
