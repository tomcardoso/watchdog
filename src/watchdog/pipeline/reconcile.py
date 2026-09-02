"""
Post-ingest reconciliation — entity resolution + contradiction detection, run once per ingest
over the whole entity set rather than inside extraction, since both jobs need claims **side by
side** and extraction only ever sees one document at a time (D118 has the full rationale,
including the blocking algorithm that keeps entity-pair comparison off a quadratic path).

**Merges run before commit, contradictions after (#403 phase 3).** `build_bundle` and
`apply_merges` read the staged, not-yet-committed batch (`.watchdog/extracted/<sha>.json`), so a
duplicate between two documents landing in the same ingest is folded before `write_vault` ever
commits them — no post-commit note surgery (redirect stub, backup, "Merged from" provenance) is
needed for a same-batch duplicate. A duplicate against an *already-committed* entity still gets
that surgery (`merge_entities.run`), since that entity's note already exists. Contradictions need
the committed notes/documents to validate against, so `apply_contradictions` runs after the commit
pass, using the merge remap `apply_merges` returned.
"""

import json
from copy import deepcopy
from pathlib import Path

from watchdog.pipeline import contradiction, merge_entities
from watchdog.pipeline.entity_norm import normalize_entity_name
from watchdog.pipeline.entity_type import canonical_type
from watchdog.pipeline.json_io import _read_json, _read_json_or
from watchdog.pipeline.write_vault import (
    _doc_slug, _extract_analysis, _extract_summary, _merge_entity, _new_entity,
    _render_evidence_fragments,
)

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

    Three shapes qualify. **Identical token sets** score 1.0 — the strongest signal available:
    `normalize_entity_name` (see `entity_norm.py`) is order- and stopword-sensitive, so
    `write_vault._reconcile_entity_ids` never folds an inverted person name ("Tom Cardoso" /
    "Cardoso, Tom") or a stopword variant ("The Acme Group" / "Acme Group") — a truly identical
    *normalized name* never coexists in the registry, since that pass already folded it in-lock at
    write time. So a pair that reaches here with identical token sets differs only by word order or
    a dropped stopword, which is exactly the judgement-call territory this pass exists for. A
    **strict token subset** — every token of one name appears in the other, plus at least one more
    — is the abbreviation/partial-name case ("Laurentian University" ⊂ "Laurentian University of
    Sudbury"), also scored 1.0. Otherwise, plain **Jaccard overlap** of the token sets, which
    catches spelling drift.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    if ta == tb or ta < tb or tb < ta:
        return 1.0
    return len(ta & tb) / len(ta | tb)


def candidate_pairs(entities_reg: dict, touched: set[str]) -> list[dict]:
    """Block the duplicate-entity field down to pairs worth a model call.

    Every pair must (1) share a canonical entity type — a `person` and an `organization` are never
    the same thing, whatever their names look like — (2) score above `_JACCARD_MIN` on some pair of
    their known names, and (3) involve at least one entity this run touched, so an ingest does not
    re-litigate the whole vault's history on every run.

    Iterates touched entities against the registry (O(touched·n)) rather than every registry pair
    (O(n²)) — on a vault with thousands of entities, a single-document ingest touches a handful, and
    the untouched-against-untouched pairs that dominate the full cross product can never qualify
    anyway. A pair reachable from both sides (both touched) is scored once.

    Returned newest-signal-first (strongest overlap first) and capped at `_MAX_PAIRS`.
    """
    types = {eid: canonical_type(e.get("type", "")) for eid, e in entities_reg.items()}
    all_ids = sorted(entities_reg)
    touched_ids = sorted(eid for eid in touched if eid in entities_reg)

    scored: list[tuple[float, dict]] = []
    seen: set[frozenset] = set()   # dedup a pair reachable from both touched sides

    for t_id in touched_ids:
        t = entities_reg[t_id]
        t_type = types[t_id]
        t_names = _surfaces(t)
        for o_id in all_ids:
            if o_id == t_id:
                continue
            pair_key = frozenset((t_id, o_id))
            if pair_key in seen:
                continue
            seen.add(pair_key)
            if types[o_id] != t_type:
                continue
            o = entities_reg[o_id]
            score = max(
                (_overlap(tn, on) for tn in t_names for on in _surfaces(o)), default=0.0
            )
            if score < _JACCARD_MIN:
                continue
            a_id, b_id = sorted((t_id, o_id))
            a, b = entities_reg[a_id], entities_reg[b_id]
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


def _staged_artifacts(vault: Path, shas: list[str]) -> list[tuple[str, dict]]:
    """Every staged extraction artifact for `shas` that still exists, parsed, paired with its
    sha. Tolerates a missing/corrupt artifact (defensive; `shas` normally comes straight from
    `orchestrate._pending_commits`, which just listed these files) by skipping it silently —
    the commit pass that follows will surface the same problem loudly if it matters."""
    extracted_dir = vault / ".watchdog" / "extracted"
    out = []
    for sha in shas:
        p = extracted_dir / f"{sha}.json"
        if not p.exists():
            continue
        try:
            out.append((sha, _read_json(p)))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def build_bundle(vault: Path, shas: list[str]) -> dict:
    """Assemble the one reconciliation call's input: candidate duplicate pairs, and the claim
    ledger of every entity that could hold a contradiction — reconstructed from the staged batch
    unioned with the registry, rather than from the committed vault, so a same-batch merge can be
    applied as a staged id rewrite instead of post-commit note surgery (#403 phase 3; see the
    module docstring).

    Builds a **working entity map** — a deep copy of the registry folded with each staged
    artifact's entities, via the same `write_vault._new_entity`/`_merge_entity` the real commit
    uses — standing in for "the registry once this batch commits," without writing anything.

    The **claim ledger** is normally an entity note's ``## Analysis`` section, which `write_vault`
    appends to per document at commit time. Pre-commit that block doesn't exist yet for this
    batch's own claims, so it's reconstructed here from the existing note (if any) plus one
    rendered block per staged document that touched the entity — close enough to feed the model,
    even though it won't be byte-identical to what `write_vault` eventually writes.
    """
    entities_path = vault / ".watchdog" / "registry" / "entities.json"
    original_reg = _read_json_or(entities_path, {})

    working = deepcopy(original_reg)
    # eid -> this batch's (sha, document, entity) contributions, in `shas` order (sorted, D126),
    # so a per-entity claim/summary reconstruction below sees documents in the same order commit
    # will actually process them.
    contributions: dict[str, list[tuple[str, dict, dict]]] = {}
    touched: set[str] = set()

    for sha, artifact in _staged_artifacts(vault, shas):
        doc = artifact.get("document") or {}
        for entity in artifact.get("entities", []):
            eid = entity.get("id")
            if not eid:
                continue
            touched.add(eid)
            if eid in working:
                _merge_entity(working[eid], entity, sha)
            else:
                working[eid] = _new_entity(entity, sha)
            contributions.setdefault(eid, []).append((sha, doc, entity))

    entities = []
    for eid in sorted(touched):
        entry = working.get(eid)
        if entry is None:
            continue
        if len(entry.get("appears_in", [])) < _MIN_DOCS:
            continue           # one document cannot contradict itself
        note = vault / f"{entry.get('note_path', '')}.md"
        claims = _extract_analysis(note) if eid in original_reg else ""
        summary = _extract_summary(note) or ""
        for sha, doc, entity in contributions.get(eid, []):
            if entity.get("summary"):
                summary = entity["summary"]
            rendered = _render_evidence_fragments(entity.get("evidence_fragments") or [])
            if not rendered:
                continue
            slug = _doc_slug(doc.get("filename", ""))
            title = doc.get("title") or doc.get("filename", "")
            date = doc.get("date_of_document")
            header = f"*{date}, via [[documents/{slug}|{title}]]:*" if date \
                else f"*via [[documents/{slug}|{title}]]:*"
            block = f"{header}\n{rendered}"
            claims = (claims.rstrip() + "\n\n" + block).lstrip() if claims else block
        entities.append({
            "entity_id": eid,
            "name": entry.get("name", ""),
            "type": entry.get("type", ""),
            "aliases": entry.get("aliases", []),
            "summary": summary,
            "claims": claims,
            "roles": _roles_digest(entry.get("roles", [])),
            "contradictions": entry.get("contradictions") or [],
        })
    entities.sort(key=lambda e: e["name"].lower())

    # Block first, then read summaries only for the entities that actually survive the cap — a
    # pair member is usually not a contradiction candidate too (it may appear in one document, or
    # not have been touched this run), so its summary is not already in hand, and reading every
    # note in the registry to enrich a handful of pairs would be the expensive way round.
    pairs = candidate_pairs(working, touched)
    summaries: dict[str, str] = {e["entity_id"]: e["summary"] for e in entities}
    for pair in pairs:
        for side in ("a", "b"):
            eid = pair[side]["id"]
            if eid not in summaries:
                note = vault / f"{working[eid].get('note_path', '')}.md"
                summaries[eid] = _extract_summary(note) or ""
            pair[side]["summary"] = _orienting_line(summaries[eid])

    return {"entities": entities, "pairs": pairs}


def _rewrite_staged_ids(vault: Path, shas: list[str], merge_id: str, keep_id: str) -> str | None:
    """Rewrite `merge_id` -> `keep_id` across every staged artifact in the batch — entity `id`,
    role `target_id`, `morgue_entity_id`, and `document.key_facts[].entities` tags (#513) — so the
    loser's claims land on the survivor once the commit pass replays `write_vault.run`, rather
    than resurrecting the merged-away id. Preserves the folded name as an alias, mirroring
    `write_vault._reconcile_entity_ids`.

    Called for both merge-taxonomy branches (#403 phase 3): it *is* the merge when the loser was
    never committed, and a supplementary step alongside `merge_entities.run` when it was already
    committed (that surgery only touches disk, not this batch's still-staged JSON).

    Returns the merged-away entity's display name (for the caller's reporting), or None if the
    batch never staged it.
    """
    extracted_dir = vault / ".watchdog" / "extracted"
    merge_name = None
    for sha in shas:
        artifact_path = extracted_dir / f"{sha}.json"
        if not artifact_path.exists():
            continue
        try:
            artifact = _read_json(artifact_path)
        except (OSError, json.JSONDecodeError):
            continue
        entities = artifact.get("entities") or []
        changed = False
        for entity in entities:
            if entity.get("id") == merge_id:
                if merge_name is None:
                    merge_name = entity.get("name") or merge_id
                entity["id"] = keep_id
                name = entity.get("name", "")
                aliases = entity.setdefault("aliases", [])
                if name and name.lower() not in {a.lower() for a in aliases}:
                    aliases.append(name)
                changed = True
        for entity in entities:
            for role in entity.get("roles", []):
                if role.get("target_id") == merge_id:
                    role["target_id"] = keep_id
                    changed = True
        if artifact.get("morgue_entity_id") == merge_id:
            artifact["morgue_entity_id"] = keep_id
            changed = True
        for fact in artifact.get("document", {}).get("key_facts", []):
            tags = fact.get("entities")
            if tags and merge_id in tags:
                fact["entities"] = [keep_id if t == merge_id else t for t in tags]
                changed = True
        if changed:
            artifact_path.write_text(
                json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    return merge_name


def apply_merges(vault: Path, shas: list[str], parsed: dict, bundle: dict, warn) -> dict:
    """Apply every confirmed merge from a pre-commit reconciliation call (#403 phase 3), before
    any of this batch has been written to the vault.

    Each confirmed merge names `keep_id` and `merge_id`. At this point each id is either
    **committed** (already a key in `registry/entities.json`) or **batch-only** (seen only in this
    batch's staged JSON so far):

    1. **Normalize direction:** if exactly one id is committed, force it to `keep` — the
       already-written entity always survives, its name stays primary. If both or neither are
       committed, honour the model's `keep_id`.
    2. **Loser is batch-only:** a plain staged id rewrite (`_rewrite_staged_ids`) — no note or
       registry entry exists yet, so `write_vault` merges the two staged entities naturally at
       commit. The common case.
    3. **Loser is committed** (so both are): the full `merge_entities.run` surgery (stub, backup,
       provenance), plus the same staged id rewrite, so this batch's own claims about the loser
       land on the survivor rather than resurrecting the merged-away id.

    Merges chain (a→b then b→c) against an accumulating remap, flattened so every key points
    straight at its final survivor. Returns
    ``{"merged": [...], "remap": {...}, "contradictions": parsed.get("contradictions") or []}`` —
    contradictions are carried through unapplied, since they need the committed vault to validate
    against (the caller applies them post-commit).
    """
    entities_path = vault / ".watchdog" / "registry" / "entities.json"
    registry_ids = set(_read_json_or(entities_path, []))

    pairs = bundle.get("pairs", [])
    applied: list[dict] = []
    remap: dict[str, str] = {}
    names: dict[str, str] = {}   # id -> best-known display name, for reporting only

    def _current(eid: str) -> str:
        seen = {eid}
        while eid in remap:            # follow the chain to whatever survives today
            eid = remap[eid]
            if eid in seen:            # a cycle can only come from a malformed remap; stop rather than hang
                break
            seen.add(eid)
        return eid

    for item in parsed.get("merges") or []:
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
        names.setdefault(pair["a"]["id"], pair["a"].get("name", pair["a"]["id"]))
        names.setdefault(pair["b"]["id"], pair["b"].get("name", pair["b"]["id"]))

        keep_id, merge_id = _current(keep_id), _current(merge_id)
        if keep_id == merge_id:
            continue               # an earlier merge in this batch already folded them together

        # Tom's decision: the already-committed side always survives. If exactly one of the two
        # is committed, force it to `keep` regardless of what the model chose; if both or neither
        # are committed, honour the model's keep_id. `registry_ids` is a fixed snapshot taken
        # above — safe even though `merge_entities.run` below deletes a loser from the real
        # registry as the loop goes, because a merged-away id is always chain-followed via
        # `_current()` before it could be looked up here again.
        if merge_id in registry_ids and keep_id not in registry_ids:
            keep_id, merge_id = merge_id, keep_id

        if merge_id in registry_ids:
            # Both committed: full merge_entities.run surgery, same as before phase 3 — stub +
            # backup + provenance, since both entities really existed.
            try:
                report = merge_entities.run(vault, keep_id, merge_id)
            except ValueError as e:
                warn(f"reconcile: merge of '{merge_id}' into '{keep_id}' skipped — {e}")
                continue
            names[keep_id], names[merge_id] = report["keep_name"], report["merge_name"]

        # Either way, fold the staged JSON: a batch-only loser has no registry entry at all, so
        # this rewrite *is* the merge for that case; a committed loser's registry side is already
        # folded above, but this batch may still stage claims against it.
        staged_name = _rewrite_staged_ids(vault, shas, merge_id, keep_id)
        if staged_name:
            names[merge_id] = staged_name

        remap[merge_id] = keep_id
        applied.append({"keep_id": keep_id, "keep_name": names.get(keep_id, keep_id),
                        "merge_id": merge_id, "merge_name": names.get(merge_id, merge_id),
                        "reason": item.get("reason", "")})

    # Flatten the chain: `apply_contradictions` follows the map one step only, so every key must
    # point straight at its final survivor rather than an intermediate id a later merge in this
    # same batch folded away.
    remap = {eid: _current(eid) for eid in remap}
    return {"merged": applied, "remap": remap, "contradictions": parsed.get("contradictions") or []}


def apply_contradictions(vault: Path, items: list, remap: dict, warn) -> list[dict]:
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
