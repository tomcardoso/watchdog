"""Re-check contradictions: compare all of an entity's stored facts with one another (D287).

Since D280 each run compares its new facts with the stored ones, and never two stored facts with
each other, so a conflict between two older documents that an earlier run missed is not looked for
again. This is the on-demand pass that does look: every stored fact of an entity (or of every
recurring entity) goes to the finalizer's reconciliation model through the reconcile prompt's
contradiction job and schema, unchanged, and what it finds is filed by `contradiction.run`, the
writer every contradiction goes through.

**Every pair of facts is seen together at least once.** An entity whose facts fit one call is sent
whole as `new_facts` with `stored_facts` empty, which the prompt reads as "compare the new facts
with one another" (the merged-survivor follow-up, D279, does the same). A larger entity is cut, in
date order, into blocks of at most half a call; block *i* is sent as `new_facts` against each later
block as `stored_facts` (packed several to a call when they fit), so the prompt compares *i* with
itself and with every later block; the last block is sent alone. A block's own pairs are therefore
compared once per call it leads, which can find one conflict twice: findings are de-duplicated by
the two facts they cite before filing. An entity that would need more than `MAX_ENTITY_CALLS` calls
is not checked, and the estimate says so, rather than checked in part.

**Nothing comes back that was dismissed.** The entity's recorded contradictions, handled ones
included (marking one handled never removes it from the registry, only from view), are in every
call so the prompt's "already listed, do not repeat" applies; then, in code, a finding citing the
same two facts as a recorded one (or, where either lacks fact links, the same two document pages)
is dropped, and `contradiction.run` drops an identical callout by its id.

App-only (I10, D286): the app runs `python -m watchdog.pipeline.recheck` as a job. It holds the
processing lock for its whole length, as `watchdog bark` does, so it never runs beside a run;
records its calls in the vault's usage files like any post-processing call; and records one
version of the vault's history.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

from watchdog import progress
from watchdog.pipeline import contradiction, entity_facts, reconcile
from watchdog.pipeline.chunking import json_size, pack
from watchdog.pipeline.json_io import _read_json_or

# An entity needing more calls than this is not re-checked (D287): past it, checking every pair
# costs more than a reporter would expect to pay for one entity without a much closer look. At the
# default finalizer's budget this is an entity of roughly 2,000 facts.
MAX_ENTITY_CALLS = 45

BUSY = ("Documents are being added to this investigation. Re-check contradictions when that has "
        "finished.")

# One side of a callout line: the document slug, the fact's block id when the side links to its
# fact (D283), and the page.
_SIDE_RE = re.compile(r"\[\[documents/([^\]|#]+)(?:#\^(f-[0-9a-f]+(?:-\d+)?))?\|[^\]]*\]\](?:, p\. (\d+))?")


class Busy(RuntimeError):
    """A run holds the vault."""


# ── planning ─────────────────────────────────────────────────────────────────────────

def _ledger(vault: Path, entities: dict) -> reconcile._FactLedger:
    return reconcile._FactLedger(vault, entities, {}, [])


def _base(eid: str, entry: dict) -> dict:
    """An entity as the reconcile prompt's ENTITIES list carries it (`reconcile.build_bundle`)."""
    return {"entity_id": eid, "name": entry.get("name", ""), "type": entry.get("type", ""),
            "aliases": entry.get("aliases", []), "new_facts": "", "stored_facts": "",
            "roles": reconcile._roles_digest(entry.get("roles", [])),
            "contradictions": entry.get("contradictions") or []}


def _blocks(items: list, sizes: list[int], limit: int) -> list[list]:
    """Consecutive runs of `items` whose summed size stays within `limit`."""
    out, current, used = [], [], 0
    for item, n in zip(items, sizes):
        if current and used + n > limit:
            out.append(current)
            current, used = [], 0
        current.append(item)
        used += n
    if current:
        out.append(current)
    return out


def entity_calls(ledger: reconcile._FactLedger, eid: str, entry: dict, budget: int) -> tuple[list[dict], int]:
    """The calls that put every pair of `eid`'s facts in front of the model at least once (see the
    module docstring), each one entity in the reconcile prompt's shape, and the number of facts."""
    facts = entity_facts.chronological(ledger.index.facts_for(eid))
    refs = entity_facts.short_refs(facts)
    base = _base(eid, entry)
    legacy = (entry.get("legacy_claims") or "").strip()
    whole = ledger._render(facts, refs)
    if legacy:
        whole = (legacy + "\n\n" + whole).strip()
    single = {**base, "new_facts": whole}
    if json_size(single) <= budget:
        return [single], len(facts)
    half = max(1, (budget - json_size(base)) // 2)
    # Sized one fact at a time, each with its document heading, so a block's real size (headings
    # shared by consecutive facts) is never more than the sum.
    items: list = list(facts)
    sizes = [json_size(ledger._render([f], refs)) for f in facts]
    if legacy:
        if json_size(legacy) > half:
            legacy = legacy[: half - 80].rsplit(" ", 1)[0] + "… [earlier claims shortened]"
        items.insert(0, legacy)
        sizes.insert(0, json_size(legacy))
    blocks = ["\n\n".join(([b[0]] if isinstance(b[0], str) else [])
                          + [ledger._render([f for f in b if isinstance(f, dict)], refs)]).strip()
              for b in _blocks(items, sizes, half)]
    calls = []
    for i, block in enumerate(blocks):
        later = blocks[i + 1:]
        if not later:
            calls.append({**base, "new_facts": block})
            continue
        room = budget - json_size(base) - json_size(block)
        for group in pack(later, room, size=json_size):
            calls.append({**base, "new_facts": block, "stored_facts": "\n".join(group)})
    return calls, len(facts)


def plan(vault: Path, ids: list[str] | None, model: str, backend: str | None) -> dict:
    """What a re-check of `ids` (every recurring entity when None) would send: the calls, packed
    so small entities share one, each entity's fact and call counts, the entities left out and why,
    and the prompt size in chars/4 tokens."""
    from watchdog.pipeline import chunking, prompts
    vault = Path(vault)
    entities = _read_json_or(vault / ".watchdog" / "registry" / "entities.json", {})
    budget = chunking.prompt_budget_chars(model, backend, vault)
    ledger = _ledger(vault, entities)
    wanted = sorted(entities) if ids is None else list(dict.fromkeys(ids))
    rows, skipped, singles, calls = [], [], [], []
    for eid in wanted:
        entry = entities.get(eid)
        if not isinstance(entry, dict) or entry.get("merged_into"):
            skipped.append({"id": eid, "name": (entry or {}).get("name") or eid, "reason": "not_found"})
            continue
        name = entry.get("name") or eid
        docs = len(entry.get("appears_in") or [])
        if docs < reconcile._MIN_DOCS:
            if ids is not None:
                skipped.append({"id": eid, "name": name, "reason": "one_document"})
            continue
        mine, n_facts = entity_calls(ledger, eid, entry, budget)
        if n_facts + (1 if entry.get("legacy_claims") else 0) < 2:
            if ids is not None:
                skipped.append({"id": eid, "name": name, "reason": "too_few_facts"})
            continue
        if len(mine) > MAX_ENTITY_CALLS:
            skipped.append({"id": eid, "name": name, "reason": "too_large", "facts": n_facts,
                            "calls": len(mine)})
            continue
        rows.append({"id": eid, "name": name, "facts": n_facts, "documents": docs, "calls": len(mine)})
        if len(mine) == 1:
            singles.append(mine[0])
        else:
            calls += [{"entities": [c], "pairs": []} for c in mine]
    calls = [{"entities": group, "pairs": []} for group in pack(singles, budget)] + calls
    chars = sum(len(prompts.build_reconcile_prompt(c)) for c in calls)
    return {"entities": rows, "skipped": skipped, "calls": calls, "budget": budget,
            "est_tokens": chars // 4, "facts": sum(r["facts"] for r in rows)}


# ── de-duplication ───────────────────────────────────────────────────────────────────

def _sides(callout: str) -> list[tuple[str, str | None, int | None]]:
    return [(slug, block or None, int(page) if page else None)
            for slug, block, page in _SIDE_RE.findall(callout)]


def _keys(sides) -> tuple[frozenset | None, frozenset | None]:
    """(the two facts' block ids, the two document pages) of a contradiction, each None when a
    side lacks it."""
    if len(sides) < 2:
        return None, None
    (sa, ba, pa), (sb, bb, pb) = sides[:2]
    facts = frozenset((ba, bb)) if ba and bb else None
    pages = frozenset(((sa, pa), (sb, pb))) if pa is not None and pb is not None else None
    return facts, pages


def _same(a: tuple, b: tuple) -> bool:
    """Two contradictions cite the same pair: the same two facts when both link their facts, else
    the same two document pages."""
    (fa, pa), (fb, pb) = a, b
    if fa and fb:
        return fa == fb
    return bool(pa and pa == pb)


class _Seen:
    """The contradictions an entity already has (handled ones included) and those filed in this
    re-check, by the pair they cite."""

    def __init__(self, ledger: reconcile._FactLedger, entities: dict):
        self.ledger = ledger
        self.entities = entities
        self.keys: dict[str, list[tuple]] = {}
        self.facts: dict[str, list[dict]] = {}

    def _entity(self, eid: str) -> list[tuple]:
        if eid not in self.keys:
            callouts = (self.entities.get(eid) or {}).get("contradictions") or []
            self.keys[eid] = [_keys(_sides(c)) for c in callouts if isinstance(c, str)]
            self.facts[eid] = self.ledger.index.facts_for(eid)
        return self.keys[eid]

    def key(self, eid: str, item: dict) -> tuple:
        from watchdog.pipeline import citations
        self._entity(eid)
        sides = []
        for side in ("a", "b"):
            slug = str(item.get(f"{side}_doc") or "").strip().removeprefix("documents/")
            sha = self.ledger.index.sha_for_note(f"documents/{slug}")
            fact = citations.match_short(str(item.get(f"{side}_fact") or ""),
                                         [f for f in self.facts[eid] if f["sha"] == sha]) if sha else None
            page = fact.get("page") if fact and fact.get("page") is not None else item.get(f"{side}_page")
            sides.append((slug, citations.block_id(fact["id"]) if fact else None,
                          page if isinstance(page, int) and not isinstance(page, bool) else None))
        return _keys(sides)

    def known(self, eid: str, key: tuple) -> bool:
        return any(_same(key, k) for k in self._entity(eid))

    def add(self, eid: str, key: tuple) -> None:
        self._entity(eid).append(key)


def _self_contradiction(key: tuple) -> bool:
    facts, _pages = key
    return facts is not None and len(facts) == 1


# ── running ──────────────────────────────────────────────────────────────────────────

def finalizer_stage() -> tuple[str | None, str, str | None]:
    """(backend, model, effort) the re-check runs on: the configured reconciliation model, which
    is the finalizer model unless Settings routes reconciliation elsewhere, at the finalizer
    effort — resolved by the same code `watchdog bark` resolves them with."""
    from watchdog import defaults
    from watchdog.cmd.base import load_config
    from watchdog.cmd.ingest import _effort, _resolve_finalizer_overrides, _resolve_stage
    config = load_config()
    backend, model = _resolve_stage(None, config.get("finalizer_model"), default=defaults.FINALIZER_MODEL)
    overrides = _resolve_finalizer_overrides(argparse.Namespace(), config, backend, model)
    return (overrides["reconciliation_backend"], overrides["reconciliation_model"],
            _effort(None, config.get("finalizer_effort")))


def cause(names: list[str], whole: bool) -> dict:
    return {"kind": "recheck", "all": whole, "names": names[:3], "count": len(names)}


def _file(vault: Path, found: list[tuple[set[str], list]], entities: dict, ledger, warn) -> dict:
    """File each finding through `contradiction.run`, dropping one that names an entity its call
    was not about, cites the same fact twice, or repeats a recorded or already-filed one."""
    seen = _Seen(ledger, entities)
    filed, repeats, rejected = [], 0, 0
    for call_ids, items in found:
        for item in items:
            if not isinstance(item, dict):
                continue
            eid = item.get("entity_id", "")
            if eid not in call_ids:
                rejected += 1
                warn(f"re-check: a finding names '{eid}', which that call was not about — skipped")
                continue
            key = seen.key(eid, item)
            if _self_contradiction(key) or seen.known(eid, key):
                repeats += 1
                continue
            try:
                result = contradiction.run(
                    vault, eid, item.get("label") or "Contradiction",
                    item.get("a_value", ""), item.get("a_doc", ""), item.get("a_page"),
                    item.get("b_value", ""), item.get("b_doc", ""), item.get("b_page"),
                    a_fact=item.get("a_fact"), b_fact=item.get("b_fact"))
            except (ValueError, OSError) as e:
                rejected += 1
                warn(f"re-check: contradiction on '{eid}' skipped — {e}")
                continue
            seen.add(eid, key)
            if not result["added"]:
                repeats += 1
                continue
            filed.append({"entity_id": eid, "entity_name": result["entity_name"],
                          "label": item.get("label") or "", "rid": result["rid"],
                          "note_path": result["note_path"]})
    return {"filed": filed, "repeats": repeats, "rejected": rejected}


def run(vault: Path, ids: list[str] | None = None, *, model: str | None = None,
        backend: str | None = None, effort: str | None = None, say=print, warn=None) -> dict:
    """Re-check `ids` (every recurring entity when None) under the processing lock, then file what
    the model found. Raises `Busy` when a run holds the vault. A stop (Ctrl+C, the app's Cancel) or
    a failed call ends the calls early; what the finished calls found is still filed."""
    from watchdog import model_client
    from watchdog.pipeline import history, orchestrate, prompts, schemas
    from watchdog.pipeline.ingest_setup import STALE_SECONDS, _iso_now
    from watchdog.pipeline.locks import acquire_or_take_stale, heartbeat
    from watchdog.vault_paths import processing_lock

    vault = Path(vault)
    warn = warn or (lambda m: print(m, file=sys.stderr))
    if model is None:
        backend, model, effort = finalizer_stage()
    lock = processing_lock(vault)
    if not acquire_or_take_stale(lock, f"pid: recheck-contradictions\nstarted_at: {_iso_now()}\n",
                                 STALE_SECONDS):
        raise Busy(BUSY)
    try:
        with heartbeat(lock):
            p = plan(vault, ids, model, backend)
            calls = p["calls"]
            names = [r["name"] for r in p["entities"]]
            out = {"entities": len(p["entities"]), "facts": p["facts"], "skipped": p["skipped"],
                   "calls_total": len(calls), "calls_done": 0, "found": 0, "filed": [],
                   "repeats": 0, "rejected": 0, "stopped": False, "error": None}
            if not calls:
                return out
            found: list[tuple[set[str], list]] = []

            async def ask() -> None:
                for n, chunk in enumerate(calls, 1):
                    progress.emit("stage", stage="recheck", done=n - 1, total=len(calls))
                    who = ", ".join(e["name"] for e in chunk["entities"][:3])
                    say(f"Call {n} of {len(calls)}: {who}")
                    r = await orchestrate._call_model(
                        task="reconcile", model=model, backend=backend, schema=schemas.RECONCILE,
                        prompt=prompts.build_reconcile_prompt(chunk), effort=effort, vault=vault,
                        detail=f"contradiction re-check · call {n} of {len(calls)}")
                    found.append(({e["entity_id"] for e in chunk["entities"]},
                                  (r.parsed or {}).get("contradictions") or []))
                    out["calls_done"] = n
                progress.emit("stage", stage="recheck", done=len(calls), total=len(calls))

            orchestrate._begin_usage_run(vault)
            try:
                asyncio.run(ask())
            except KeyboardInterrupt:
                out["stopped"] = True
            except model_client.CALL_FAILURES as e:
                out["error"] = str(e)
            finally:
                orchestrate._end_usage_run(vault)
            out["found"] = sum(len(items) for _ids, items in found)
            whole = ids is None
            c = cause(names, whole)
            if out["stopped"] or out["error"]:
                c["incomplete"] = True
            entities = _read_json_or(vault / ".watchdog" / "registry" / "entities.json", {})
            with history.recording(vault, c):
                out.update(_file(vault, found, entities, _ledger(vault, entities), warn))
            return out
    finally:
        lock.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    """`python -m watchdog.pipeline.recheck (--entity ID … | --all) [--vault DIR]`, the job the
    app's "Re-check contradictions" runs. No terminal command calls it (D287)."""
    from watchdog.vault_paths import is_vault
    parser = argparse.ArgumentParser(description="Re-check stored facts for contradictions.")
    parser.add_argument("--vault", default=".")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--entity", action="append", dest="ids")
    group.add_argument("--all", action="store_true")
    args = parser.parse_args(argv)
    vault = Path(args.vault).resolve()
    if not is_vault(vault):
        print(f"Error: {vault} is not a Watchdog investigation.", file=sys.stderr)
        return 1
    try:
        out = run(vault, None if args.all else args.ids)
    except Busy as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except SystemExit as e:          # a bad model setting, from the shared resolvers
        print(str(e.code or "Error: the model settings could not be read."), file=sys.stderr)
        return 1
    print(summary(out))
    return 1 if out["error"] else 0


def summary(out: dict) -> str:
    """The plain-language result line the job's log ends with."""
    filed = len(out["filed"])
    if not out["calls_total"]:
        return "Nothing to re-check: no entity here has facts from two or more documents."
    head = (f"Re-checked {out['entities']} entit{'ies' if out['entities'] != 1 else 'y'} "
            f"({out['facts']} facts) in {out['calls_done']} of {out['calls_total']} "
            f"call{'s' if out['calls_total'] != 1 else ''}.")
    if not out["calls_done"]:
        why = f"The model call failed: {out['error']}." if out["error"] else "Stopped before the first call finished."
        return f"{head} {why} Nothing was checked, and nothing was filed."
    found = (f" {filed} new contradiction{'s' if filed != 1 else ''} filed for Review."
             if filed else " No new contradictions were found.")
    if out["repeats"]:
        found += (f" {out['repeats']} already recorded (open or handled) "
                  f"{'were' if out['repeats'] != 1 else 'was'} not filed again.")
    if out["stopped"]:
        found += " Stopped before every call finished; what the finished calls found was filed."
    if out["error"]:
        found += f" The model call failed: {out['error']}. What the finished calls found was filed."
    return head + found


if __name__ == "__main__":
    raise SystemExit(main())
