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
import math
from copy import deepcopy
from pathlib import Path

from watchdog.pipeline import contradiction, identity, merge_entities, merge_log
from watchdog.pipeline.chunking import json_size, pack
from watchdog.pipeline.entity_norm import normalize_entity_name
from watchdog.pipeline.entity_type import canonical_type
from watchdog.pipeline.json_io import _read_json, _read_json_or
from watchdog.pipeline.timeline import remap_entity_ids
from watchdog.pipeline.write_vault import _doc_slug, _merge_entity, _new_entity

# Structural words carry no identifying signal, so they are dropped before names are compared —
# otherwise "University of Toronto" and "University of Waterloo" share half their tokens ("university",
# "of") and block as a candidate pair, while "Laurentian University" and "Laurentian University of
# Sudbury" — the case this exists to catch — score no better.
_STOPWORDS = {"the", "of", "and", "a", "an", "de", "du", "la", "le"}

# Token-overlap floor for a pair to be worth a model's judgement. 0.5 admits one differing token
# out of two ("Acme Holdings" / "Acme Holdings International"); it is a recall knob on a set the
# model then filters, not a merge threshold — nothing merges without the model confirming it.
_JACCARD_MIN = 0.5

# Ceiling on candidate pairs per run. Pairs no longer share one call — `chunk_bundle` splits them
# across as many size-bounded calls as they need (#696) — so this is a runaway guard on the number
# of calls a pathological vault (thousands of names sharing a common token) can trigger, not a
# context-window guard. Pairs are ranked by descending overlap first, so what survives the cut is
# the most likely duplicates, and `build_bundle` reports how many were cut.
_MAX_PAIRS = 2000

# Marker prefixed to an entity's claim ledger when `chunk_bundle` had to trim it to fit one call.
_TRIMMED = "[the oldest stored facts are omitted to fit one reconciliation call]\n"

# A passage is shown beside a fact, cut to this many characters.
_PASSAGE_CHARS = 240

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


def _prefix_len(n_tokens: int) -> int:
    """How many of a name's tokens (in the global order) any qualifying Jaccard partner must hit.

    `J(A, B) >= t` implies `|A ∩ B| >= t·|A ∪ B| >= t·|A|`, so at least `ceil(t·|A|)` of A's tokens
    are shared — and therefore any `|A| - ceil(t·|A|) + 1` of them include a shared one. Probing
    just that many (the rarest) is enough to find every partner; the tolerance keeps a float
    product like 0.5·4 from ceiling up to 3."""
    return n_tokens - math.ceil(_JACCARD_MIN * n_tokens - 1e-9) + 1


def candidate_pairs(entities_reg: dict, touched: set[str]) -> list[dict]:
    """`_ranked_pairs`, capped at `_MAX_PAIRS` and numbered by rank."""
    pairs = _ranked_pairs(entities_reg, touched)[:_MAX_PAIRS]
    for index, pair in enumerate(pairs):
        pair["index"] = index
    return pairs


def _ranked_pairs(entities_reg: dict, touched: set[str]) -> list[dict]:
    """Block the duplicate-entity field down to pairs worth a model call.

    Every pair must (1) share a canonical entity type — a `person` and an `organization` are never
    the same thing, whatever their names look like — (2) score above `_JACCARD_MIN` on some pair of
    their known names, and (3) involve at least one entity this run touched, so an ingest does not
    re-litigate the whole vault's history on every run.

    Candidates come from an inverted index keyed on (canonical type, name token) rather than from
    walking every touched entity against the whole registry (#696). That walk was O(touched·n) in
    time *and* memory — 4,000 entities, all touched, took ~1 minute and 2 GB, and a vault of tens
    of thousands ran out of memory. Every qualifying name pair shares a token, so it is enough to
    probe the index for names sharing one, with two filters keeping the probes off the long
    postings lists of common tokens ("inc", "canada"):

    - **Jaccard partners** — a name's rarest `_prefix_len` tokens are probed against every
      indexed token (see `_prefix_len` for why that many suffice).
    - **Subset partners** — the shorter name's rarest token is necessarily in the longer one. The
      probing name's own rarest token is in its prefix, so a longer superset is found by the probe
      above; a shorter subset is found by probing a second index holding each name's *rarest*
      token only, with every one of the probing name's tokens.

    Token rarity only decides which tokens are probed; any fixed order would be exact, rarity just
    keeps the postings short. The candidate set is a superset of the qualifying pairs, and each
    candidate is scored exactly as before, so the output is unchanged — only the work is not.

    Returned strongest-signal-first, uncapped and unnumbered.
    """
    types = {eid: canonical_type(e.get("type", "")) for eid, e in entities_reg.items()}
    touched_ids = sorted(eid for eid in touched if eid in entities_reg)
    touched_set = set(touched_ids)

    surface_tokens: dict[str, list[frozenset[str]]] = {}
    freq: dict[str, int] = {}
    for eid, e in entities_reg.items():
        toks = [t for t in (_tokens(n) for n in _surfaces(e)) if t]
        surface_tokens[eid] = toks
        for ts in toks:
            for tok in ts:
                freq[tok] = freq.get(tok, 0) + 1

    def _order(ts: frozenset[str]) -> list[str]:
        return sorted(ts, key=lambda tok: (freq.get(tok, 0), tok))

    full_index: dict[tuple[str, str], set[str]] = {}
    rare_index: dict[tuple[str, str], set[str]] = {}
    for eid, toks in surface_tokens.items():
        etype = types[eid]
        for ts in toks:
            for tok in ts:
                full_index.setdefault((etype, tok), set()).add(eid)
            rare_index.setdefault((etype, _order(ts)[0]), set()).add(eid)

    scored: list[tuple[float, dict]] = []
    for t_id in touched_ids:
        t_type = types[t_id]
        candidates: set[str] = set()
        for ts in surface_tokens[t_id]:
            ordered = _order(ts)
            for tok in ordered[:_prefix_len(len(ordered))]:
                candidates |= full_index.get((t_type, tok), set())
            for tok in ordered:
                candidates |= rare_index.get((t_type, tok), set())
        candidates.discard(t_id)
        t_names = _surfaces(entities_reg[t_id])
        for o_id in candidates:
            # A pair with both sides touched is found from both; score it once, from the lower id.
            if o_id in touched_set and o_id < t_id:
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
    return [p for _, p in scored]


def _person_pairs(entities_reg: dict, touched: set[str], known: set[tuple[str, str]]) -> list[dict]:
    """People whose names differ only by initials or a dropped given name ("J. Smith" / "John
    Smith"), which share too few tokens for `_ranked_pairs` to block. Found through a surname
    index, kept only when `identity.person_relation` says the names are compatible, and returned
    after the token-blocked pairs, since they are never merged automatically (D279)."""
    by_surname: dict[str, set[str]] = {}
    surfaces: dict[str, list[str]] = {}
    for eid, e in entities_reg.items():
        if canonical_type(e.get("type", "")) != "person":
            continue
        surfaces[eid] = _surfaces(e)
        for name in surfaces[eid]:
            surname = identity.person_parts(name)[1]
            if surname:
                by_surname.setdefault(surname, set()).add(eid)
    out = []
    seen = set(known)
    for t_id in sorted(eid for eid in touched if eid in surfaces):
        candidates = set()
        for name in surfaces[t_id]:
            candidates |= by_surname.get(identity.person_parts(name)[1], set())
        for o_id in sorted(candidates - {t_id}):
            a_id, b_id = sorted((t_id, o_id))
            if (a_id, b_id) in seen:
                continue
            if not any(identity.person_relation(x, y) for x in surfaces[t_id] for y in surfaces[o_id]):
                continue
            seen.add((a_id, b_id))
            a, b = entities_reg[a_id], entities_reg[b_id]
            out.append({
                "a": {"id": a_id, "name": a.get("name", ""), "type": a.get("type", ""),
                      "aliases": a.get("aliases", [])},
                "b": {"id": b_id, "name": b.get("name", ""), "type": b.get("type", ""),
                      "aliases": b.get("aliases", [])},
            })
    return out


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


class _FactLedger:
    """An entity's facts as the contradiction check reads them (D280): from the stored and staged
    extractions, never from a note's prose, grouped by document under a
    `[[documents/<slug>|<title>]]` heading the model copies the slug from, each with its short
    id, page, warnings and source passage. Facts the reporter marked Disputed are left out."""

    def __init__(self, vault: Path, working: dict, staged: dict[str, dict], shas: list[str]):
        from watchdog.pipeline import entity_facts
        from watchdog.pipeline.write_vault import _unique_doc_slug
        documents = dict(_read_json_or(vault / ".watchdog" / "registry" / "documents.json", {}))
        for sha, art in staged.items():
            if sha in documents:
                continue
            doc = art.get("document") or {}
            filename = doc.get("filename", "")
            slug = _unique_doc_slug(vault, _doc_slug(filename), sha, filename, documents)
            documents[sha] = {"title": doc.get("title") or filename, "filename": filename,
                              "document_note": f"documents/{slug}",
                              "date_of_document": doc.get("date_of_document")}
        self.batch = set(shas)
        self.working = working
        self.index = entity_facts.FactIndex(vault, working, documents, overrides=staged)

    def _render(self, facts: list[dict], refs: dict[str, str]) -> str:
        from watchdog.pipeline.write_vault import _defang, _figure_verification_note
        out, current = [], None
        for f in facts:
            if f["sha"] != current:
                current = f["sha"]
                date = f" ({f['doc_date']})" if f.get("doc_date") else ""
                slug = (f.get("note") or "").removeprefix("documents/")
                out.append(f"*[[documents/{slug}|{_defang(f.get('title') or '')}]]{date}:*")
            line = f"- [{refs[f['id']]}] {_defang(f.get('fact') or '')}"
            if f.get("page"):
                line += f" (p. {f['page']})"
            if f.get("date"):
                line += f" (dated {f['date']})"
            if f.get("basis") == "inferred":
                line += " *(inferred)*"
            line += _figure_verification_note(f)
            passage = (f.get("quote") or "").strip() or (
                (f.get("passage") or "").strip() if f.get("passage_method") == "matched" else "")
            if passage:
                passage = _defang(passage)
                if len(passage) > _PASSAGE_CHARS:
                    passage = passage[:_PASSAGE_CHARS].rsplit(" ", 1)[0] + "…"
                line += f" — source passage: “{passage}”"
            out.append(line)
        return "\n".join(out)

    def blocks(self, eid: str, all_new: bool = False) -> tuple[str, str]:
        """(new facts, stored facts) for `eid`: this batch's documents' facts, and every earlier
        document's, each in date order. `all_new` puts every fact in the first (a merged survivor,
        whose two records' facts were never compared)."""
        from watchdog.pipeline import entity_facts
        facts = [f for f in self.index.facts_for(eid)
                 if (f.get("mark") or {}).get("status") != "disputed"]
        refs = entity_facts.short_refs(facts)
        new = [f for f in facts if all_new or f["sha"] in self.batch]
        stored = [f for f in facts if not (all_new or f["sha"] in self.batch)]
        stored_text = self._render(entity_facts.chronological(stored), refs)
        legacy = (self.working.get(eid) or {}).get("legacy_claims")
        if legacy:
            stored_text = (legacy.strip() + "\n\n" + stored_text).strip()
        return self._render(entity_facts.chronological(new), refs), stored_text


def build_bundle(vault: Path, shas: list[str], only: set[str] | None = None) -> dict:
    """Assemble the one reconciliation call's input: candidate duplicate pairs, and the claim
    ledger of every entity that could hold a contradiction — reconstructed from the staged batch
    unioned with the registry, rather than from the committed vault, so a same-batch merge can be
    applied as a staged id rewrite instead of post-commit note surgery (#403 phase 3; see the
    module docstring).

    Builds a **working entity map** — a deep copy of the registry folded with each staged
    artifact's entities, via the same `write_vault._new_entity`/`_merge_entity` the real commit
    uses — standing in for "the registry once this batch commits," without writing anything.

    Each recurring entity's **facts** come from the stored extractions of committed documents and
    the staged extractions of this batch (`_FactLedger`, D280) — never from a note's prose — split
    into this batch's new facts and the stored facts they are checked against.
    """
    entities_path = vault / ".watchdog" / "registry" / "entities.json"
    original_reg = _read_json_or(entities_path, {})

    working = deepcopy(original_reg)
    touched: set[str] = set()
    staged: dict[str, dict] = {}

    for sha, artifact in _staged_artifacts(vault, shas):
        staged[sha] = artifact
        for entity in artifact.get("entities", []):
            eid = entity.get("id")
            if not eid:
                continue
            touched.add(eid)
            if eid in working:
                _merge_entity(working[eid], entity, sha)
            else:
                working[eid] = _new_entity(entity, sha)

    entities = []
    ledger = _FactLedger(vault, working, staged, shas)
    for eid in sorted(touched):
        if only is not None and eid not in only:
            continue
        entry = working.get(eid)
        if entry is None:
            continue
        if len(entry.get("appears_in", [])) < _MIN_DOCS:
            continue           # one document cannot contradict itself
        new, stored = ledger.blocks(eid, all_new=only is not None)
        entities.append({
            "entity_id": eid,
            "name": entry.get("name", ""),
            "type": entry.get("type", ""),
            "aliases": entry.get("aliases", []),
            "new_facts": new,
            "stored_facts": stored,
            "roles": _roles_digest(entry.get("roles", [])),
            "contradictions": entry.get("contradictions") or [],
        })
    entities.sort(key=lambda e: e["name"].lower())

    # Block first, then read summaries only for the entities that actually survive the cap — a
    # pair member is usually not a contradiction candidate too (it may appear in one document, or
    # not have been touched this run), so its summary is not already in hand, and reading every
    # note in the registry to enrich a handful of pairs would be the expensive way round.
    if only is not None:
        # A contradiction-only follow-up (`ledger_for`): no pairs to judge.
        return {"entities": entities, "pairs": [], "pairs_dropped": 0, "pair_verdicts": [],
                "rule_merges": [], "candidates": [], "profiles": {}}
    ranked = _ranked_pairs(working, touched)
    blocked = ranked[:_MAX_PAIRS]
    # Initialled-name pairs are found separately and capped separately: they are almost all low
    # tier, which costs no model call, so they must not crowd out pairs the model has to see.
    blocked += _person_pairs(working, touched, {(p["a"]["id"], p["b"]["id"]) for p in ranked})[:_MAX_PAIRS]

    # Route each blocked pair by its confidence tier (D279): high merges in code, medium goes to the
    # model with both records' facts, low becomes a "possible same" item for the reporter, and a
    # pair that is not a candidate at all (two people's different names, a conflicting
    # identifier, a pair the reporter marked "Not the same") is dropped.
    evidence = identity.Evidence(vault, working, staged=dict(staged))
    profiles: dict[str, identity.Profile] = {}

    def _profile(eid: str) -> identity.Profile:
        if eid not in profiles:
            profiles[eid] = evidence.registry_profile(eid, working[eid])
        return profiles[eid]

    pairs, verdicts, rule_merges, candidates = [], [], [], []
    for pair in blocked:
        a, b = _profile(pair["a"]["id"]), _profile(pair["b"]["id"])
        verdict = identity.classify(a, b, evidence.dismissed())
        if verdict is None:
            continue
        if verdict["tier"] == "high":
            rule_merges.append({"a": pair["a"]["id"], "b": pair["b"]["id"], "verdict": verdict})
        elif verdict["tier"] == "low":
            candidates.append({"a": pair["a"]["id"], "b": pair["b"]["id"], "verdict": verdict})
        else:
            cap = identity.FACT_CAP if verdict["same_name"] else 3
            for side, prof in (("a", a), ("b", b)):
                pair[side]["facts"] = prof.facts_digest(cap)
                pair[side]["roles"] = prof.roles_digest()
            if verdict["same_name"]:
                pair["same_name"] = True
            pairs.append(pair)
            verdicts.append(verdict)
    for index, pair in enumerate(pairs):
        pair["index"] = index
    for pair in pairs:
        for side in ("a", "b"):
            synthesis = working[pair[side]["id"]].get("synthesis") or {}
            pair[side]["summary"] = _orienting_line(synthesis.get("summary") or "")

    return {"entities": entities, "pairs": pairs, "pairs_dropped": max(0, len(ranked) - _MAX_PAIRS),
            "pair_verdicts": verdicts, "rule_merges": rule_merges, "candidates": candidates,
            "profiles": profiles}


def _trim_claims(entity: dict, budget: int) -> dict:
    """Fit one entity into `budget` characters by dropping the *oldest* of its stored facts.

    A hub entity named in hundreds of documents can carry more stored facts than a whole call
    holds. They are listed in date order, so the head is the oldest and is what goes; this
    batch's new facts — the ones the check exists for — are kept whole. Returns the entity
    unchanged when it already fits."""
    size = json_size(entity)
    if size <= budget:
        return entity
    stored = entity.get("stored_facts") or ""
    keep = max(0, len(stored) - (size - budget) - len(_TRIMMED) - 64)
    trimmed = dict(entity)
    trimmed["stored_facts"] = _TRIMMED + stored[len(stored) - keep:] if keep else _TRIMMED
    return trimmed


def chunk_bundle(bundle: dict, budget: int) -> list[dict]:
    """Split a reconciliation bundle into calls of at most `budget` characters of data (#696).

    Pairs and entities are packed in order, pairs first, into as few chunks as fit; a bundle that
    already fits comes back as one chunk identical to the input. Each chunk numbers its pairs from
    0, since the model answers by index into the list it was shown, and records the bundle-wide
    index of each in `pair_index` so `merge_chunk_results` can translate the answers back. An
    entity too large for any one call has its oldest claims trimmed (`_trim_claims`) rather than
    deadlocking the batch on a call that can never fit."""
    pairs = bundle.get("pairs") or []
    entities = [_trim_claims(e, budget) for e in bundle.get("entities") or []]
    items = [("pair", i, p) for i, p in enumerate(pairs)] + [("entity", None, e) for e in entities]
    chunks = []
    for group in pack(items, budget, size=lambda item: json_size(item[2])):
        chunk_pairs, pair_index, chunk_entities = [], [], []
        for kind, i, item in group:
            if kind == "pair":
                local = dict(item)
                if "index" in local:
                    local["index"] = len(chunk_pairs)
                chunk_pairs.append(local)
                pair_index.append(i)
            else:
                chunk_entities.append(item)
        chunks.append({"pairs": chunk_pairs, "entities": chunk_entities, "pair_index": pair_index})
    return chunks


def merge_chunk_results(chunks: list[dict], parsed: list[dict]) -> dict:
    """Combine each chunk's model answer into one answer over the whole bundle, in the shape
    `apply_merges` already takes: every merge's `pair` translated from its chunk's numbering back
    to the bundle's, contradictions concatenated. A merge naming an index outside its own chunk is
    passed on as a non-integer label so `apply_merges` warns and skips it, as it would any bad
    index."""
    merges, contradictions = [], []
    for n, (chunk, answer) in enumerate(zip(chunks, parsed)):
        index = chunk["pair_index"]
        for item in answer.get("merges") or []:
            local = item.get("pair")
            item = dict(item)
            if isinstance(local, int) and not isinstance(local, bool) and 0 <= local < len(index):
                item["pair"] = index[local]
            else:
                item["pair"] = f"chunk {n + 1} pair {local!r}"
            merges.append(item)
        contradictions.extend(answer.get("contradictions") or [])
    return {"merges": merges, "contradictions": contradictions}


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
                entity.setdefault("extracted_id", merge_id)
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
                fact.setdefault("extracted_entities", list(tags))
                fact["entities"] = [keep_id if t == merge_id else t for t in tags]
                changed = True
        if changed:
            # Timeline events were staged at extraction time with the pre-merge id (D243), and
            # only from this artifact's own tags — so an artifact that never named `merge_id`
            # has nothing to remap, and skipping it saves a timeline-folder scan per document
            # per merge. Remapped before the artifact is rewritten, so a crash in between
            # leaves `merge_id` in the artifact and a re-run remaps again.
            remap_entity_ids(vault, {merge_id: keep_id}, sha=sha)
            artifact_path.write_text(
                json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    return merge_name


def _stage_on_owner(vault: Path, shas: list[str], ids: set[str], entry: dict) -> None:
    """Stage a merge-log entry on the first batch document that names one of `ids` (the first
    document of the batch if none does), so it is written when that document commits (I7)."""
    from watchdog.pipeline.orchestrate import stage_identity_log
    extracted_dir = vault / ".watchdog" / "extracted"
    target = None
    for sha in shas:
        p = extracted_dir / f"{sha}.json"
        try:
            artifact = _read_json(p)
        except (OSError, json.JSONDecodeError):
            continue
        if target is None:
            target = (p, artifact)
        if {e.get("id") for e in artifact.get("entities") or []} & ids:
            target = (p, artifact)
            break
    if target is None:
        return
    path, artifact = target
    stage_identity_log(artifact, entry)
    path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")


def _snapshot_side(profile, entry: dict | None) -> dict:
    """What a later split needs to know about a merged-away record (D279)."""
    out = {"extracted_id": profile.id, "documents": list(profile.documents),
           "facts": [f["id"] for f in profile.facts]}
    if entry:
        out["entry"] = merge_entities.undo_snapshot(entry)
    return out


def apply_merges(vault: Path, shas: list[str], parsed: dict, bundle: dict, warn,
                 model: str | None = None) -> dict:
    """Apply every merge this pre-commit pass decided (#403 phase 3, D279), before any of this
    batch has been written to the vault, and stage every "possible same" pair for Review.

    Two sources of merges: the bundle's high-confidence `rule_merges` (decided by code, no model),
    then the model's confirmations of medium-tier pairs. Each names `keep_id` and `merge_id`. At
    this point each id is either **committed** (already a key in `registry/entities.json`) or
    **batch-only** (seen only in this batch's staged JSON so far):

    1. **Normalize direction:** if exactly one id is committed, force it to `keep` — the
       already-written entity always survives, its name stays primary. If both or neither are
       committed, honour the model's `keep_id` (for a rule merge, the lower id).
    2. **Loser is batch-only:** a plain staged id rewrite (`_rewrite_staged_ids`) — no note or
       registry entry exists yet, so `write_vault` merges the two staged entities naturally at
       commit. The common case. Its merge-log entry is staged with the batch and written at commit.
    3. **Loser is committed** (so both are): the full `merge_entities.run` surgery (stub, backup,
       provenance), which logs the merge itself, plus the same staged id rewrite, so this batch's
       own claims about the loser land on the survivor rather than resurrecting the merged-away id.

    Low-tier pairs, and same-name medium pairs the model did not merge, are staged as
    "possible same" candidates (`merge_log.candidate_entry`).

    Merges chain (a→b then b→c) against an accumulating remap, flattened so every key points
    straight at its final survivor. Returns
    ``{"merged": [...], "remap": {...}, "contradictions": [...], "candidates": n}`` —
    contradictions are carried through unapplied, since they need the committed vault to validate
    against (the caller applies them post-commit).
    """
    from watchdog.pipeline import orchestrate
    entities_path = vault / ".watchdog" / "registry" / "entities.json"
    original_reg = _read_json_or(entities_path, {})
    registry_ids = set(original_reg)
    run_id = getattr(orchestrate._run, "run_id", None)

    pairs = bundle.get("pairs", [])
    verdicts = bundle.get("pair_verdicts") or []
    profiles = bundle.get("profiles") or {}
    applied: list[dict] = []
    remap: dict[str, str] = {}
    names: dict[str, str] = {}   # id -> best-known display name, for reporting only
    model_merged: set[frozenset] = set()

    def _current(eid: str) -> str:
        seen = {eid}
        while eid in remap:            # follow the chain to whatever survives today
            eid = remap[eid]
            if eid in seen:            # a cycle can only come from a malformed remap; stop rather than hang
                break
            seen.add(eid)
        return eid

    decisions = []   # (keep_id, merge_id, verdict, decided_by, reason)
    for rm in bundle.get("rule_merges") or []:
        a, b = rm["a"], rm["b"]
        keep_id = min(a, b)
        decisions.append((keep_id, b if keep_id == a else a, rm["verdict"], "rule", rm["verdict"]["reason"]))
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
        names.setdefault(pair["a"]["id"], pair["a"].get("name", pair["a"]["id"]))
        names.setdefault(pair["b"]["id"], pair["b"].get("name", pair["b"]["id"]))
        verdict = verdicts[idx] if idx < len(verdicts) else {"tier": "medium", "rule": None}
        model_merged.add(frozenset(ids))
        decisions.append((keep_id, (ids - {keep_id}).pop(), verdict, "model", item.get("reason", "")))

    # Every original id now inside each surviving record, so a chain of merges (a→b, then c→b)
    # can never join two records the reporter marked "Not the same".
    dismissed = identity.Evidence(vault, {}).dismissed()
    members: dict[str, set[str]] = {}

    def _blocked(x: str, y: str) -> bool:
        xs, ys = members.get(x, {x}), members.get(y, {y})
        return any(identity.pair_id(i, j) in dismissed for i in xs for j in ys)

    for keep_id, merge_id, verdict, decided_by, reason in decisions:
        original_merge_id = merge_id
        keep_id, merge_id = _current(keep_id), _current(merge_id)
        if keep_id == merge_id:
            continue               # an earlier merge in this batch already folded them together
        if _blocked(keep_id, merge_id):
            continue

        # Tom's decision: the already-committed side always survives. If exactly one of the two
        # is committed, force it to `keep` regardless of what the model chose; if both or neither
        # are committed, honour the model's keep_id. `registry_ids` is a fixed snapshot taken
        # above — safe even though `merge_entities.run` below deletes a loser from the real
        # registry as the loop goes, because a merged-away id is always chain-followed via
        # `_current()` before it could be looked up here again.
        if merge_id in registry_ids and keep_id not in registry_ids:
            keep_id, merge_id = merge_id, keep_id

        loser = profiles.get(merge_id) or profiles.get(original_merge_id)
        winner = profiles.get(keep_id)
        keep_ref = {"id": keep_id, "name": winner.name if winner else names.get(keep_id, keep_id),
                    "type": winner.type if winner else ""}
        merged_ref = {"id": merge_id, "name": loser.name if loser else names.get(merge_id, merge_id),
                      "type": loser.type if loser else ""}
        occurrence = (identity.evidence_record(verdict, loser) if loser
                      else {"sha": None, "documents": [], "facts": []})
        entry = merge_log.merge_entry(
            keep=keep_ref, merged=merged_ref, tier=verdict.get("tier") or "medium",
            decided_by=decided_by, rule=verdict.get("rule"), reason=reason,
            occurrence=occurrence, model=model if decided_by == "model" else None,
            undo=_snapshot_side(loser, original_reg.get(merge_id)) if loser else {}, run=run_id)

        if merge_id in registry_ids:
            # Both committed: full merge_entities.run surgery, same as before phase 3 — stub +
            # backup + provenance, since both entities really existed. It logs the merge.
            try:
                report = merge_entities.run(vault, keep_id, merge_id, log_entry=entry)
            except ValueError as e:
                warn(f"reconcile: merge of '{merge_id}' into '{keep_id}' skipped — {e}")
                continue
            names[keep_id], names[merge_id] = report["keep_name"], report["merge_name"]
        else:
            _stage_on_owner(vault, shas, {keep_id, merge_id}, entry)

        # Either way, fold the staged JSON: a batch-only loser has no registry entry at all, so
        # this rewrite *is* the merge for that case; a committed loser's registry side is already
        # folded above, but this batch may still stage claims against it.
        staged_name = _rewrite_staged_ids(vault, shas, merge_id, keep_id)
        if staged_name:
            names[merge_id] = staged_name
        names.setdefault(keep_id, keep_ref["name"])
        names.setdefault(merge_id, merged_ref["name"])

        remap[merge_id] = keep_id
        members[keep_id] = members.get(keep_id, {keep_id}) | members.pop(merge_id, {merge_id})
        applied.append({"keep_id": keep_id, "keep_name": names.get(keep_id, keep_id),
                        "merge_id": merge_id, "merge_name": names.get(merge_id, merge_id),
                        "reason": reason, "decided_by": decided_by,
                        "tier": verdict.get("tier")})

    # "Possible same" pairs for the reporter: every low-tier pair, and every same-name pair the
    # model was shown and did not merge. A pair whose side was merged away this run is skipped —
    # the next run's blocking sees the survivor instead.
    n_candidates = 0
    pending = [(c["a"], c["b"], c["verdict"], False) for c in bundle.get("candidates") or []]
    for idx, pair in enumerate(pairs):
        verdict = verdicts[idx] if idx < len(verdicts) else None
        ids = frozenset((pair["a"]["id"], pair["b"]["id"]))
        if verdict and verdict.get("same_name") and ids not in model_merged:
            pending.append((pair["a"]["id"], pair["b"]["id"], verdict, True))
    for a, b, verdict, declined in pending:
        if a in remap or b in remap or a not in profiles or b not in profiles:
            continue
        entry = merge_log.candidate_entry(a=profiles[a], b=profiles[b], verdict=verdict,
                                          model_declined=declined, run=run_id)
        _stage_on_owner(vault, shas, {a, b}, entry)
        n_candidates += 1

    # Flatten the chain: `apply_contradictions` follows the map one step only, so every key must
    # point straight at its final survivor rather than an intermediate id a later merge in this
    # same batch folded away.
    remap = {eid: _current(eid) for eid in remap}
    return {"merged": applied, "remap": remap, "contradictions": parsed.get("contradictions") or [],
            "candidates": n_candidates}


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
        except (ValueError, OSError) as e:      # OSError: the registry lock timed out (Windows)
            warn(f"reconcile: contradiction on '{eid}' skipped — {e}")
            continue
        if result["added"]:
            applied.append({"entity_id": eid, "entity_name": result["entity_name"],
                            "label": item.get("label", ""),
                            "sources": result.get("sources", []),
                            "note_path": result["note_path"]})
    return applied
