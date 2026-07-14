"""
Post-ingest reconciliation — entity resolution + contradiction detection (#381/D118).

These are the two jobs extraction is structurally unable to do, and for the same reason: both
need claims **side by side**, and extraction reads one document at a time. Extraction used to
attempt both against a snapshot of the entity registry taken when that document's own extraction
began — which meant a document could only ever be compared against the documents that happened to
land ahead of it, and documents in the same concurrency wave could not see each other at all.

The finalizer is the only stage that sees the whole entity set, so both jobs live here. It runs
once per ingest, after every document has been written, which makes it concurrency-immune,
order-immune, and near-constant in cost (one call, like the briefing — it does not scale with
page count).

**The split with the deterministic writer.** `write_vault._reconcile_entity_ids` already folds
*exact* normalized-name duplicates together, in-lock, at write time — that pass is untouched and
still does the bulk of the work. What it deliberately will not do is collapse names that differ by
a token ("Laurentian University" / "Laurentian University of Sudbury"), because auto-merging those
carries real false-positive risk. Those are exactly the judgement calls, and they are what this
module sends to the model.

**Bundle size.** Naively, entity resolution is every entity against every other entity — one call
that grows quadratically and eventually will not fit a context window. So Python blocks the field
first (`candidate_pairs`): only pairs sharing a canonical type, with one name a token-subset of the
other or a high token overlap, and with at least one side touched by *this* run, are ever sent. The
model confirms or rejects each pair by index. The contradiction half is bounded by the same
recurrence gate synthesis uses (D26): an entity needs claims in two documents before two of its
claims can disagree.

I1 holds throughout: the model only ever answers *which* — which pairs are the same thing, which
claims conflict. Every write is done by deterministic code that already exists — `merge_entities.
run` for a merge, `contradiction.run` for a callout — so a model that names a nonexistent document
or a stale entity id produces a skipped item and a warning, never a bad note.
"""

import json
from pathlib import Path

from watchdog.pipeline import contradiction, merge_entities
from watchdog.pipeline.entity_norm import normalize_entity_name
from watchdog.pipeline.entity_type import canonical_type
from watchdog.pipeline.write_vault import _extract_analysis, _extract_summary

# Structural words carry no identifying signal, so they are dropped before names are compared —
# otherwise "University of Toronto" and "University of Waterloo" share half their tokens ("university",
# "of") and block as a candidate pair, while "Laurentian University" and "Laurentian University of
# Sudbury" — the case this exists to catch — score no better.
_STOPWORDS = {"the", "of", "and", "a", "an", "de", "du", "la", "le"}

# Token-overlap floor for a pair to be worth a model's judgement. 0.5 admits one differing token
# out of two ("Acme Holdings" / "Acme Holdings International"); it is a recall knob on a set the
# model then filters, not a merge threshold — nothing merges without the model confirming it.
_JACCARD_MIN = 0.5

# Hard ceiling on pairs sent in one call, so a pathological vault (thousands of entities sharing a
# common token) cannot blow the context window. Pairs are ranked by descending overlap first, so
# what survives the cut is the most likely duplicates, not an arbitrary slice.
_MAX_PAIRS = 200

# An entity needs claims in at least this many documents before two of them can disagree — the same
# recurrence gate synthesis uses (D26).
_MIN_DOCS = 2


def _surfaces(entry: dict) -> list[str]:
    """Every name this entity is known by — canonical plus aliases. A duplicate often announces
    itself through an alias rather than the canonical name, so blocking compares all of them."""
    return [entry.get("name", ""), *entry.get("aliases", [])]


def _tokens(name: str) -> frozenset[str]:
    return frozenset(
        t for t in normalize_entity_name(name).split() if t and t not in _STOPWORDS
    )


def _overlap(a: str, b: str) -> float:
    """How strongly two names suggest the same thing, in [0, 1]; 0 means "do not send this pair".

    Two shapes qualify. A **strict token subset** — every token of one name appears in the other,
    plus at least one more — is the abbreviation/partial-name case ("Laurentian University" ⊂
    "Laurentian University of Sudbury"), scored 1.0 because it is the single strongest signal
    available short of an exact match. Otherwise, plain **Jaccard overlap** of the token sets,
    which catches spelling and word-order drift.

    Identical token sets score 0: `write_vault._reconcile_entity_ids` has already merged those
    deterministically, so a pair that reaches here identical is one the deterministic pass
    *declined* — different canonical types — and the model should not be asked to override that.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb or ta == tb:
        return 0.0
    if ta < tb or tb < ta:
        return 1.0
    return len(ta & tb) / len(ta | tb)


def candidate_pairs(entities_reg: dict, touched: set[str]) -> list[dict]:
    """Block the duplicate-entity field down to pairs worth a model call.

    Every pair must (1) share a canonical entity type — a `person` and an `organization` are never
    the same thing, whatever their names look like — (2) score above `_JACCARD_MIN` on some pair of
    their known names, and (3) involve at least one entity this run touched, so an ingest does not
    re-litigate the whole vault's history on every run.

    Returned newest-signal-first (strongest overlap first) and capped at `_MAX_PAIRS`.
    """
    ids = sorted(entities_reg)
    scored: list[tuple[float, dict]] = []

    for i, a_id in enumerate(ids):
        a = entities_reg[a_id]
        a_type = canonical_type(a.get("type", ""))
        a_names = _surfaces(a)
        for b_id in ids[i + 1:]:
            if a_id not in touched and b_id not in touched:
                continue
            b = entities_reg[b_id]
            if canonical_type(b.get("type", "")) != a_type:
                continue
            score = max(
                (_overlap(an, bn) for an in a_names for bn in _surfaces(b)), default=0.0
            )
            if score < _JACCARD_MIN:
                continue
            scored.append((score, {
                "a": {"id": a_id, "name": a.get("name", ""), "type": a.get("type", ""),
                      "aliases": a.get("aliases", [])},
                "b": {"id": b_id, "name": b.get("name", ""), "type": b.get("type", ""),
                      "aliases": b.get("aliases", [])},
            }))

    # Strongest signal first, so a vault that overruns `_MAX_PAIRS` loses its weakest candidates
    # rather than an arbitrary slice. Ties break on id, so the cut is deterministic.
    scored.sort(key=lambda s: (-s[0], s[1]["a"]["id"], s[1]["b"]["id"]))
    pairs = [p for _, p in scored[:_MAX_PAIRS]]
    for index, pair in enumerate(pairs):
        pair["index"] = index
    return pairs


def _orienting_line(text: str, limit: int = 240) -> str:
    """One line of orienting prose per pair member — enough for the model to tell a parent company
    from its subsidiary, without carrying two full summaries per pair into the prompt."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def _roles_digest(roles: list[dict]) -> list[dict]:
    """The comparison-relevant fields of an entity's relationships — a role a document asserts is
    as contradictable as a claim it states ("sole director" vs "resigned as director")."""
    return [
        {
            "relationship": r.get("relationship"),
            "target_name": r.get("target_name"),
            "target_type": r.get("target_type"),
            "date_range": r.get("date_range"),
            "basis": r.get("basis") or "stated",
        }
        for r in roles
    ]


def _touched_ids(vault: Path) -> set[str]:
    """The entities this run wrote to, from the finalizer's fragment queue."""
    q = vault / ".watchdog" / "tmp" / "entity-fragments" / "_queue.json"
    if not q.exists():
        return set()
    try:
        return set(json.loads(q.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return set()


def build_bundle(vault: Path) -> dict:
    """Assemble the one reconciliation call's input: candidate duplicate pairs, and the claim
    ledger of every entity that could hold a contradiction.

    The claim ledger is the entity note's ``## Analysis`` section — `write_vault` appends a
    source-attributed block to it per document (`*<date>, via [[documents/<slug>|<title>]]:*`,
    then that document's claims with page links), so it already *is* the durable, complete,
    per-entity record of what each document said, in the shape this pass needs. This is the same
    content pre-flight used to send to the extractor once per document; assembling it here means
    it is built once per ingest instead, and — unlike the pre-flight digest — it is complete,
    because every document has landed by the time it is read.
    """
    entities_path = vault / ".watchdog" / "Registry" / "entities.json"
    if not entities_path.exists():
        return {"entities": [], "pairs": []}
    try:
        entities_reg = json.loads(entities_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"entities": [], "pairs": []}

    touched = _touched_ids(vault)

    entities = []
    for eid in sorted(touched):
        entry = entities_reg.get(eid)
        if entry is None:      # merged away by an earlier pass, or a stale queue entry
            continue
        if len(entry.get("appears_in", [])) < _MIN_DOCS:
            continue           # one document cannot contradict itself
        note = vault / f"{entry.get('note_path', '')}.md"
        entities.append({
            "entity_id": eid,
            "name": entry.get("name", ""),
            "type": entry.get("type", ""),
            "aliases": entry.get("aliases", []),
            "summary": _extract_summary(note) or "",
            "claims": _extract_analysis(note),
            "roles": _roles_digest(entry.get("roles", [])),
            "contradictions": entry.get("contradictions") or [],
        })
    entities.sort(key=lambda e: e["name"].lower())

    # Block first, then read summaries only for the entities that actually survive the cap — a
    # pair member is usually not a contradiction candidate too (it may appear in one document, or
    # not have been touched this run), so its summary is not already in hand, and reading every
    # note in the registry to enrich a handful of pairs would be the expensive way round.
    pairs = candidate_pairs(entities_reg, touched)
    summaries: dict[str, str] = {e["entity_id"]: e["summary"] for e in entities}
    for pair in pairs:
        for side in ("a", "b"):
            eid = pair[side]["id"]
            if eid not in summaries:
                note = vault / f"{entities_reg[eid].get('note_path', '')}.md"
                summaries[eid] = _extract_summary(note) or ""
            pair[side]["summary"] = _orienting_line(summaries[eid])

    return {"entities": entities, "pairs": pairs}


def _apply_merges(vault: Path, merges: list, pairs: list, warn) -> tuple[list[dict], dict]:
    """Run each confirmed merge through the existing `watchdog merge-entities` surgery.

    Returns the applied merges and a ``{merged_away_id: surviving_id}`` remap, which the
    contradiction pass needs: the model chose its `entity_id`s from a bundle built *before* these
    merges ran, so a contradiction may name an entity that no longer exists.

    Merges chain (a→b then b→c), so each is validated against the registry as it stands *now*,
    not as the bundle described it — an id already merged away is followed to its survivor rather
    than failing the merge.
    """
    applied: list[dict] = []
    remap: dict[str, str] = {}

    def _current(eid: str) -> str:
        seen = {eid}
        while eid in remap:            # follow the chain to whatever survives today
            eid = remap[eid]
            if eid in seen:            # a cycle can only come from a malformed remap; stop rather than hang
                break
            seen.add(eid)
        return eid

    for item in merges or []:
        idx = item.get("pair")
        if not isinstance(idx, int) or not 0 <= idx < len(pairs):
            warn(f"reconcile: merge names pair {idx!r}, which is not in the candidate list — skipped")
            continue
        pair = pairs[idx]
        ids = {pair["a"]["id"], pair["b"]["id"]}
        keep_id = item.get("keep_id")
        if keep_id not in ids:
            warn(f"reconcile: merge keeps '{keep_id}', which is not one of pair {idx} "
                 f"({', '.join(sorted(ids))}) — skipped")
            continue
        merge_id = (ids - {keep_id}).pop()

        keep_id, merge_id = _current(keep_id), _current(merge_id)
        if keep_id == merge_id:
            continue               # an earlier merge in this batch already folded them together

        try:
            report = merge_entities.run(vault, keep_id, merge_id)
        except ValueError as e:
            warn(f"reconcile: merge of '{merge_id}' into '{keep_id}' skipped — {e}")
            continue
        remap[merge_id] = keep_id
        applied.append({"keep_id": keep_id, "keep_name": report["keep_name"],
                        "merge_id": merge_id, "merge_name": report["merge_name"],
                        "reason": item.get("reason", "")})
    return applied, remap


def _fold_fragments(vault: Path, remap: dict) -> None:
    """Follow a merge through the finalizer's fragment queue.

    `merge_entities.run` is registry surgery — it knows nothing about the in-flight fragment files
    the *next* step (synthesis) is about to read. Without this, synthesis would look up a merged-away
    id, find no registry entry, and silently drop the entity it just merged. Concatenates the losing
    entity's fragment file onto the survivor's (so its claims still reach synthesis) and unions the
    queue records.
    """
    frag_dir = vault / ".watchdog" / "tmp" / "entity-fragments"
    queue_path = frag_dir / "_queue.json"
    if not remap or not queue_path.exists():
        return
    try:
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    entities_reg = json.loads(
        (vault / ".watchdog" / "Registry" / "entities.json").read_text(encoding="utf-8"))

    for merge_id, keep_id in remap.items():
        losing = frag_dir / f"{merge_id}.md"
        if losing.exists():
            surviving = frag_dir / f"{keep_id}.md"
            head = surviving.read_text(encoding="utf-8") if surviving.exists() else ""
            surviving.write_text(head + losing.read_text(encoding="utf-8"), encoding="utf-8")
            losing.unlink(missing_ok=True)

        merged_rec = queue.pop(merge_id, None)
        if merged_rec is None:
            continue
        keep_entry = entities_reg.get(keep_id)
        if keep_entry is None:
            continue
        rec = queue.get(keep_id, {"name": keep_entry["name"],
                                  "note_path": keep_entry["note_path"], "shas": []})
        shas = sorted(set(rec.get("shas", [])) | set(merged_rec.get("shas", [])))
        rec.update({"name": keep_entry["name"], "note_path": keep_entry["note_path"],
                    "shas": shas, "count": len(shas)})
        queue[keep_id] = rec

    queue_path.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")


def _apply_contradictions(vault: Path, items: list, remap: dict, warn) -> list[dict]:
    """File each flagged contradiction through `contradiction.run` — the same deterministic writer
    the manual `watchdog contradiction` command uses (D81).

    That writer validates both document slugs against the registry and renders the callout itself,
    so a model that cites a document that does not exist, or an entity that does not exist, gets a
    skipped item and a warning rather than a fabricated citation in a journalist's note.
    """
    applied: list[dict] = []
    for item in items or []:
        eid = item.get("entity_id", "")
        eid = remap.get(eid, eid)          # the entity may have been merged away moments ago
        try:
            result = contradiction.run(
                vault, eid, item.get("label", "Contradiction"),
                item.get("a_value", ""), item.get("a_doc", ""), item.get("a_page"),
                item.get("b_value", ""), item.get("b_doc", ""), item.get("b_page"),
            )
        except ValueError as e:
            warn(f"reconcile: contradiction on '{eid}' skipped — {e}")
            continue
        if result["added"]:
            applied.append({"entity_id": eid, "entity_name": result["entity_name"],
                            "label": item.get("label", ""),
                            "note_path": result["note_path"]})
    return applied


def apply(vault: Path, parsed: dict, bundle: dict, warn=None) -> dict:
    """Apply one reconciliation result: merges first, then contradictions.

    Merges run first because a merge changes what the contradictions are *about* — two halves of a
    split entity become one entity whose claim ledger now holds both documents' claims. Running
    them the other way round would file a callout on a note that is about to be turned into a
    redirect stub.
    """
    def _warn(msg: str) -> None:
        if warn is not None:
            warn(msg)

    merged, remap = _apply_merges(vault, parsed.get("merges"), bundle.get("pairs", []), _warn)
    _fold_fragments(vault, remap)
    flagged = _apply_contradictions(vault, parsed.get("contradictions"), remap, _warn)
    return {"merged": merged, "contradictions": flagged}
