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

# Records sharing one name and type are judged as a group, not pair by pair (#755): a name with n
# records is n(n-1)/2 pairs, so a 91-record "Acme" alone was 4,095 pairs and ran most of a large
# batch's identical-name pairs past `_MAX_PAIRS` unjudged. A group is cut into slices of at most
# `_GROUP_MEMBERS` records per call: a member as the model sees it is at most about 3.5 KB (six facts
# of up to 300 characters, eight roles, a summary line; measured on a 2,000-entity investigation),
# so a full slice is about 105 KB, inside the smallest catalogued model's per-call data budget
# (`chunking.prompt_budget_chars`, about 165 KB), and still few enough records to compare at once.
# A group cut into several slices gets further rounds over the survivors (`NameGroups`), at most
# `_GROUP_ROUNDS` rounds in all; what still has not been compared in full is carried to the next run.
_GROUP_MEMBERS = 30
_GROUP_ROUNDS = 3

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
    id, page, warnings and source passage. Facts the reporter marked Disputed are included,
    labelled, so a conflict with one is still reported (the owner's call: disputed facts stay
    visible everywhere, marked)."""

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
            if (f.get("mark") or {}).get("status") == "disputed":
                line += " *(disputed by the reporter)*"
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
        facts = list(self.index.facts_for(eid))
        refs = entity_facts.short_refs(facts)
        new = [f for f in facts if all_new or f["sha"] in self.batch]
        stored = [f for f in facts if not (all_new or f["sha"] in self.batch)]
        stored_text = self._render(entity_facts.chronological(stored), refs)
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
                "rule_merges": [], "candidates": [], "profiles": {}, "groups": []}
    # Records an earlier run could not finish comparing are blocked again, though only for pairs:
    # they are not this batch's, so they hold no new facts to check for contradictions.
    carried = {eid for eid in merge_log.carried_ids(vault) if eid in working}
    block = touched | carried
    ranked = _ranked_pairs(working, block)
    # Pairs of records with the very same name and type are judged as groups (#755); every other
    # pair keeps the pair path, under the pair cap.
    same_key: dict[tuple[str, str], set[str]] = {}
    others = []
    for pair in ranked:
        key = _group_key(working[pair["a"]["id"]])
        if key == _group_key(working[pair["b"]["id"]]):
            same_key.setdefault(key, set()).update((pair["a"]["id"], pair["b"]["id"]))
        else:
            others.append(pair)
    blocked = others[:_MAX_PAIRS]
    over = others[_MAX_PAIRS:]
    # Initialled-name pairs are found separately and capped separately: they are almost all low
    # tier, which costs no model call, so they must not crowd out pairs the model has to see.
    initialled = _person_pairs(working, block, {(p["a"]["id"], p["b"]["id"]) for p in ranked})
    blocked += initialled[:_MAX_PAIRS]
    over += initialled[_MAX_PAIRS:]

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

    groups = _name_groups(same_key, working, _profile, evidence.dismissed(), rule_merges, candidates)
    name_groups = NameGroups(groups, working, profiles)
    return {"entities": entities, "pairs": pairs, "pairs_dropped": len(over),
            "pair_verdicts": verdicts, "rule_merges": rule_merges, "candidates": candidates,
            "profiles": profiles, "groups": name_groups.next_items(), "name_groups": name_groups,
            "carry_pairs": sorted({i for p in over for i in (p["a"]["id"], p["b"]["id"])}),
            "carried_in": sorted(carried)}


def build_group_bundle(vault: Path) -> dict:
    """Every set of committed records sharing one name and type, for "Bring this investigation up
    to date" (D336): the bundle `build_bundle` would make for its same-name groups if every record
    had been touched by the batch, and nothing else (no pairs, no contradiction check). The groups
    go through the same tiers, `NameGroups` rounds, caps and carry (D325). Records an earlier run
    carried that are in no group stay carried, since the carry this pass records replaces the last."""
    working = _read_json_or(vault / ".watchdog" / "registry" / "entities.json", {})
    same_key: dict[tuple[str, str], set[str]] = {}
    for eid, entry in working.items():
        if not isinstance(entry, dict) or entry.get("merged_into"):
            continue
        key = _group_key(entry)
        if key[1]:
            same_key.setdefault(key, set()).add(eid)
    same_key = {k: v for k, v in same_key.items() if len(v) > 1}
    evidence = identity.Evidence(vault, working, staged={})
    profiles: dict[str, identity.Profile] = {}

    def _profile(eid: str) -> identity.Profile:
        if eid not in profiles:
            profiles[eid] = evidence.registry_profile(eid, working[eid])
        return profiles[eid]

    rule_merges: list = []
    candidates: list = []
    groups = _name_groups(same_key, working, _profile, evidence.dismissed(), rule_merges, candidates)
    name_groups = NameGroups(groups, working, profiles)
    grouped = {eid for ids in same_key.values() for eid in ids}
    carried = sorted(eid for eid in merge_log.carried_ids(vault) if eid in working)
    return {"entities": [], "pairs": [], "pairs_dropped": 0, "pair_verdicts": [],
            "rule_merges": rule_merges, "candidates": candidates, "profiles": profiles,
            "groups": name_groups.next_items(), "name_groups": name_groups,
            "carry_pairs": [eid for eid in carried if eid not in grouped], "carried_in": carried,
            "records": len(grouped), "name_sets": len(same_key)}


def _group_key(entry: dict) -> tuple[str, str]:
    """What makes two records "the same name" for group judging: canonical type and the name's
    comparison key (`identity.name_key`, a leading "the" dropped)."""
    return canonical_type(entry.get("type", "")), identity.name_key(entry.get("name", ""))


def _doc_order(profiles, ids) -> list[str]:
    """`ids`, the records named in most documents first, then by id: a fixed order for slicing."""
    return sorted(ids, key=lambda eid: (-len(profiles(eid).documents), eid))


def _name_groups(same_key: dict, working: dict, profile, dismissed, rule_merges: list,
                 candidates: list) -> list[dict]:
    """Settle in code what the tiers settle for each set of same-name records, and return the
    groups left for the model (#755, revising D279's pair-by-pair handling).

    Every pair inside a set is classified, not only the pairs blocking produced, so a conflicting
    identifier or a reporter's "Not the same" between two records no run touched still keeps them
    apart. High pairs merge in code, as before (one rule merge per pair that joins two clusters);
    low pairs (the same short person name, never merged automatically) become "possible same"
    items, each record paired with the set's most-documented one rather than with every other
    record, so a short name in thirty records is 29 items, not 435; and records joined by a medium
    pair form a group of clusters (the records high pairs already joined) for the model."""
    groups = []
    for key in sorted(same_key):
        ids = _doc_order(profile, same_key[key])
        verdicts: dict[frozenset, dict | None] = {}
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                verdicts[frozenset((a, b))] = identity.classify(profile(a), profile(b), dismissed)
        parent = {eid: eid for eid in ids}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        members: set[str] = set()
        low: list[tuple[str, str, dict]] = []
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                v = verdicts[frozenset((a, b))]
                if v is None:
                    continue
                if v["tier"] == "low":
                    low.append((a, b, v))
                    continue
                members.update((a, b))
                if v["tier"] == "high" and find(a) != find(b):
                    parent[find(b)] = find(a)
                    rule_merges.append({"a": a, "b": b, "verdict": v})
        anchor = next((a for a, _, _ in low), None)
        for a, b, v in low:
            if a == anchor:
                candidates.append({"a": a, "b": b, "verdict": v})
        clusters: dict[str, list[str]] = {}
        for eid in ids:
            if eid in members:
                clusters.setdefault(find(eid), []).append(eid)
        if len(clusters) < 2:
            continue
        first = working[ids[0]]
        groups.append({"name": first.get("name", ""), "type": first.get("type", ""),
                       "verdicts": verdicts, "clusters": [sorted(c) for c in clusters.values()]})
    return groups


class NameGroups:
    """The model's judgement of same-name groups, round by round (#755).

    Each group starts as clusters of records the tiers already joined (high pairs), usually one
    record each. `next_items` cuts every unsettled group into slices of at most `_GROUP_MEMBERS`
    clusters and numbers them for the model: in the first round, contiguous slices in a fixed order
    (most documents first, then id); after that, when the group still does not fit one slice, the
    clusters an earlier round grew (at most a third of a slice) go into every slice as anchors and
    the rest are spread over the slices, so every record is compared with the records most likely
    to be the thing it names. `absorb` takes the model's answers: for each slice, the sets of
    members that are one thing (`same`, with the member to keep) and the sets it could not rule
    out (`unsure`). Code checks every answer — indices in range, no member in two sets, the kept
    member inside its set — and rejects a malformed one whole, leaving its records apart. A set
    that would join two records with a conflicting identifier or a reporter's "Not the same" is
    dropped on its own.

    A group is settled when one valid answer covered all of it (compared in full), when a round no
    longer shrinks it, or after `_GROUP_ROUNDS` rounds. One not compared in full, or with a slice
    rejected or unanswered, is carried to the next run. `result` gives the merges, the "possible
    same" pairs and the carried records."""

    def __init__(self, groups: list[dict], working: dict, profiles: dict):
        self.groups = groups
        self.working = working
        self.profiles = profiles
        self.round = 0
        self.slices: list[tuple[int, list[str]]] = []
        self.parent: dict[str, str] = {}
        for group in groups:
            for cluster in group["clusters"]:
                for eid in cluster:
                    self.parent[eid] = cluster[0]
        self.done = [False] * len(groups)
        self.complete = [False] * len(groups)
        self.failed = [False] * len(groups)
        self.last_size = [len(g["clusters"]) for g in groups]
        self.merges: list[tuple[str, str, dict, str]] = []
        self.unsure: list[tuple[int, str, str]] = []
        self.calls_rejected = 0

    def _find(self, eid: str) -> str:
        while self.parent[eid] != eid:
            self.parent[eid] = self.parent[self.parent[eid]]
            eid = self.parent[eid]
        return eid

    def _clusters(self, gi: int) -> dict[str, list[str]]:
        """Each surviving cluster's id (the record that survives) -> its records."""
        out: dict[str, list[str]] = {}
        for cluster in self.groups[gi]["clusters"]:
            for eid in cluster:
                out.setdefault(self._find(eid), []).append(eid)
        return out

    def _verdict(self, gi: int, a: str, b: str) -> dict | None:
        return self.groups[gi]["verdicts"].get(frozenset((a, b)))

    def _docs(self, records: list[str]) -> int:
        return len({d for eid in records if eid in self.profiles for d in self.profiles[eid].documents})

    def _member(self, n: int, keep: str, records: list[str]) -> dict:
        profs = [self.profiles[eid] for eid in [keep, *(r for r in records if r != keep)]
                 if eid in self.profiles]
        facts, digests = [], [p.facts_digest(identity.FACT_CAP) for p in profs]
        for i in range(identity.FACT_CAP):        # round-robin, so every record is represented
            facts += [d[i] for d in digests if i < len(d)]
        roles, names = [], []
        for p in profs:
            roles += [r for r in p.roles_digest() if r not in roles]
            names += [s for s in p.surfaces if s not in names]
        synthesis = (self.working.get(keep) or {}).get("synthesis") or {}
        out = {"member": n, "id": keep, "names": names[:8], "documents": self._docs(records),
               "summary": _orienting_line(synthesis.get("summary") or ""),
               "facts": facts[:identity.FACT_CAP], "roles": roles[:8]}
        if len(records) > 1:
            out["records"] = len(records)
        return out

    def _cut(self, gi: int, clusters: dict[str, list[str]]) -> list[list[str]] | None:
        """This round's slices of group `gi` (cluster ids), or None when it is settled."""
        size = len(clusters)
        if size < 2:
            return None
        if self.round > 0 and (self.complete[gi] or size >= self.last_size[gi]):
            return None               # compared in full, or the last round merged nothing
        if self.round >= _GROUP_ROUNDS:
            return None
        ordered = sorted(clusters, key=lambda c: (-len(clusters[c]), -self._docs(clusters[c]), c))
        if size <= _GROUP_MEMBERS:
            return [ordered]
        if self.round == 0:
            k = math.ceil(size / _GROUP_MEMBERS)
            step = math.ceil(size / k)
            return [ordered[i:i + step] for i in range(0, size, step)]
        anchors = [c for c in ordered if len(clusters[c]) > 1][:_GROUP_MEMBERS // 3]
        rest = [c for c in ordered if c not in anchors]
        room = _GROUP_MEMBERS - len(anchors)
        k = math.ceil(len(rest) / room)
        step = math.ceil(len(rest) / k)
        return [anchors + rest[i:i + step] for i in range(0, len(rest), step)]

    def next_items(self) -> list[dict]:
        """This round's slices, numbered from 0; [] when every group is settled."""
        self.slices = []
        items = []
        for gi, group in enumerate(self.groups):
            if self.done[gi]:
                continue
            clusters = self._clusters(gi)
            cut = self._cut(gi, clusters)
            if cut is None:
                self.done[gi] = True
                continue
            self.last_size[gi] = len(clusters)
            for part in cut:
                self.slices.append((gi, part))
                items.append({"index": len(items), "name": group["name"], "type": group["type"],
                              "members": [self._member(n, c, clusters[c]) for n, c in enumerate(part)]})
        self.round += 1
        return items

    @staticmethod
    def _index(value, size: int) -> int | None:
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < size:
            return value
        return None

    def _parse(self, answer: dict, size: int) -> tuple[list, list] | None:
        """(same sets as (member indices, keep index, reason), unsure sets), or None when the
        answer is malformed in any way."""
        same, unsure, used = [], [], set()
        for item in answer.get("same") or []:
            if not isinstance(item, dict) or not isinstance(item.get("members"), list):
                return None
            idx = [self._index(m, size) for m in item["members"]]
            keep = self._index(item.get("keep"), size)
            if None in idx or len(set(idx)) != len(idx) or len(idx) < 2 or keep not in idx \
                    or used & set(idx):
                return None
            used |= set(idx)
            same.append((idx, keep, str(item.get("reason") or "")))
        seen: set[int] = set()
        for item in answer.get("unsure") or []:
            if not isinstance(item, list):
                return None
            idx = [self._index(m, size) for m in item]
            if None in idx or len(set(idx)) != len(idx) or len(idx) < 2 or seen & set(idx):
                return None
            seen |= set(idx)
            unsure.append(idx)
        return same, unsure

    def absorb(self, answers: list[dict], warn) -> None:
        """Apply one round's answers (each naming its slice by `group`), checked as described on
        the class. A slice with no valid answer leaves its records apart and its group carried."""
        answered: dict[int, dict] = {}
        for answer in answers:
            label = answer.get("group") if isinstance(answer, dict) else None
            idx = self._index(label, len(self.slices))
            if idx is None:
                warn(f"reconcile: a same-name group answer names group {label!r}, which is not "
                     f"in the list — skipped")
                continue
            if idx in answered:
                warn(f"reconcile: same-name group {idx} was answered twice — the first answer is used")
                continue
            answered[idx] = answer
        whole = {gi for gi, _ in self.slices}
        whole = {gi for gi in whole if sum(1 for g, _ in self.slices if g == gi) == 1}
        for si, (gi, part) in enumerate(self.slices):
            name = self.groups[gi]["name"]
            parsed = self._parse(answered[si], len(part)) if si in answered else None
            if parsed is None:
                if si in answered:
                    warn(f"reconcile: the answer for same-name group {si} ({name}) is not a valid "
                         f"partition — none of its records were merged, and they are carried to "
                         f"the next run")
                    self.calls_rejected += 1
                self.failed[gi] = True
                continue
            if gi in whole:
                self.complete[gi] = True
            same, unsure = parsed
            clusters = self._clusters(gi)
            for members, keep, reason in same:
                roots = [self._find(part[m]) for m in members]
                target = self._find(part[keep])
                joined = sorted({eid for r in set(roots) for eid in clusters[r]})
                if any(self._verdict(gi, a, b) is None
                       for i, a in enumerate(joined) for b in joined[i + 1:]):
                    warn(f"reconcile: a merge in same-name group {si} ({name}) would join records "
                         f"that cannot be the same (a different identifier, or marked “Not the "
                         f"same”) — skipped")
                    continue
                for root in dict.fromkeys(roots):
                    if root == target:
                        continue
                    verdict = self._verdict(gi, target, root) or {"tier": "medium", "rule": None}
                    self.merges.append((target, root, verdict, reason))
                    self.parent[root] = target
                    clusters[target] = clusters.get(target, []) + clusters.pop(root, [])
            for members in unsure:
                self.unsure += [(gi, part[members[0]], part[m]) for m in members[1:]]

    def result(self) -> dict:
        """Merges as (keep id, merged id, verdict, reason), in order; "possible same" pairs as
        (a, b, verdict) between records still apart; and the ids carried to the next run (each
        carried group's surviving records)."""
        unsure, seen = [], set()
        for gi, a, b in self.unsure:
            ka, kb = self._find(a), self._find(b)
            pair = frozenset((ka, kb))
            if ka == kb or pair in seen:
                continue
            verdict = self._verdict(gi, ka, kb)
            if verdict is None:
                continue
            seen.add(pair)
            unsure.append((ka, kb, verdict))
        carried = sorted(c for gi in range(len(self.groups))
                         if self.failed[gi] or not self.complete[gi] and len(self._clusters(gi)) > 1
                         for c in self._clusters(gi))
        return {"merges": list(self.merges), "unsure": unsure, "carried": carried,
                "rejected": self.calls_rejected}


# What one group member costs in a reconcile prompt, in characters, for the cost estimate: the mean
# of a member's facts, roles and summary line on a 2,000-entity investigation was about 1,100.
_GROUP_MEMBER_EST_CHARS = 1100


def group_estimate(vault: Path, shas: list[str]) -> dict:
    """A names-only forecast of the same-name group calls the next post-processing makes (#755),
    for `ingest_setup.finalize_cost_estimate`: no extraction is read and no tier is decided, so it
    counts every set of two or more records sharing a name and type that the tiers would leave to
    the model — a non-distinctive name, or a person's full name — and assumes one more round
    over the survivors whenever a group needs more than one slice. Returns ``{"groups",
    "records", "calls", "est_tokens"}``; `calls` counts slices, which share calls when small."""
    registry = _read_json_or(vault / ".watchdog" / "registry" / "entities.json", {})
    counts: dict[tuple[str, str], int] = {}
    staged_keys: set[tuple[str, str]] = set()
    for entry in registry.values():
        if isinstance(entry, dict):
            key = _group_key(entry)
            counts[key] = counts.get(key, 0) + 1
    for _, artifact in _staged_artifacts(vault, shas):
        for entity in artifact.get("entities") or []:
            if isinstance(entity, dict) and entity.get("name"):
                key = _group_key(entity)
                counts[key] = counts.get(key, 0) + 1
                staged_keys.add(key)
    groups = records = calls = chars = 0
    for key in staged_keys:
        n = counts[key]
        etype, name = key
        judged = identity.is_full_person_name(name) if etype == "person" else not identity.is_distinctive(name)
        if n < 2 or not name or not judged:
            continue
        slices = math.ceil(n / _GROUP_MEMBERS)
        rounds = 2 if slices > 1 else 1
        groups += 1
        records += n
        calls += slices + (math.ceil(slices / _GROUP_MEMBERS) if rounds > 1 else 0)
        chars += n * _GROUP_MEMBER_EST_CHARS * rounds
    return {"groups": groups, "records": records, "calls": calls, "est_tokens": chars // 4}


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
    groups = bundle.get("groups") or []
    entities = [_trim_claims(e, budget) for e in bundle.get("entities") or []]
    items = ([("pair", i, p) for i, p in enumerate(pairs)]
             + [("group", i, g) for i, g in enumerate(groups)]
             + [("entity", None, e) for e in entities])
    chunks = []
    for packed in pack(items, budget, size=lambda item: json_size(item[2])):
        chunk_pairs, pair_index, chunk_groups, group_index, chunk_entities = [], [], [], [], []
        for kind, i, item in packed:
            if kind == "pair":
                local = dict(item)
                if "index" in local:
                    local["index"] = len(chunk_pairs)
                chunk_pairs.append(local)
                pair_index.append(i)
            elif kind == "group":
                chunk_groups.append({**item, "index": len(chunk_groups)})
                group_index.append(i)
            else:
                chunk_entities.append(item)
        chunk = {"pairs": chunk_pairs, "entities": chunk_entities, "pair_index": pair_index}
        if chunk_groups:
            chunk["groups"], chunk["group_index"] = chunk_groups, group_index
        chunks.append(chunk)
    return chunks


def merge_chunk_results(chunks: list[dict], parsed: list[dict]) -> dict:
    """Combine each chunk's model answer into one answer over the whole bundle, in the shape
    `apply_merges` already takes: every merge's `pair` translated from its chunk's numbering back
    to the bundle's, contradictions concatenated, and each same-name group answer's `group`
    translated the same way (for `NameGroups.absorb`). A merge or group answer naming an index
    outside its own chunk is passed on as a non-integer label so it is warned about and skipped, as
    any bad index is."""
    merges, contradictions, groups = [], [], []
    for n, (chunk, answer) in enumerate(zip(chunks, parsed)):
        answer = answer if isinstance(answer, dict) else {}
        gindex = chunk.get("group_index") or []
        for item in answer.get("groups") or []:
            if not isinstance(item, dict):
                continue
            local = item.get("group")
            item = dict(item)
            if isinstance(local, int) and not isinstance(local, bool) and 0 <= local < len(gindex):
                item["group"] = gindex[local]
            else:
                item["group"] = f"chunk {n + 1} group {local!r}"
            groups.append(item)
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
    return {"merges": merges, "contradictions": contradictions, "groups": groups}


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
        if not shas:
            # Nothing staged (D336's pass over committed records): written now, under the lock.
            from watchdog.pipeline.write_vault import _registry_lock
            with _registry_lock(vault / ".watchdog" / "registry"):
                merge_log.record(vault, [entry])
        return
    path, artifact = target
    stage_identity_log(artifact, entry)
    path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")


def _carried_in_batch(vault: Path, shas: list[str], merge_id: str) -> list[dict]:
    """`merge_log.carried_items` for `merge_id` in every staged extraction of the batch."""
    out = []
    for sha, artifact in _staged_artifacts(vault, shas):
        item = merge_log.carried_items(artifact, sha, {merge_id})
        if item:
            out.append(item)
    return out


def _snapshot_side(profile, entry: dict | None) -> dict:
    """What a later split needs to know about a merged-away record (D279, D280)."""
    out = {"version": merge_log.UNDO_VERSION, "extracted_id": profile.id,
           "documents": list(profile.documents), "facts": [f["id"] for f in profile.facts]}
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

    Same-name groups (#755) add the merges `NameGroups` checked and applied from the model's
    partitions, as model decisions. Low-tier pairs, same-name medium pairs the model did not merge,
    and a group's records the model could not rule out as one are staged as "possible same"
    candidates (`merge_log.candidate_entry`). Records whose comparisons this run could not finish
    (pairs past the cap, groups not compared in full) are staged as the merge log's `carry`, which
    the next run blocks again.

    Merges chain (a→b then b→c) against an accumulating remap, flattened so every key points
    straight at its final survivor. Returns
    ``{"merged": [...], "remap": {...}, "contradictions": [...], "candidates": n, "carried": {...}}`` —
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
        # The model names a side, never an id: a retyped id can come back "corrected" (D335).
        side = item.get("keep")
        if side not in ("a", "b"):
            warn(f"reconcile: merge of pair {idx} keeps {side!r}, which is neither \"a\" nor "
                 f"\"b\" — skipped")
            continue
        keep_id = pair[side]["id"]
        names.setdefault(pair["a"]["id"], pair["a"].get("name", pair["a"]["id"]))
        names.setdefault(pair["b"]["id"], pair["b"].get("name", pair["b"]["id"]))
        verdict = verdicts[idx] if idx < len(verdicts) else {"tier": "medium", "rule": None}
        model_merged.add(frozenset(ids))
        decisions.append((keep_id, (ids - {keep_id}).pop(), verdict, "model", item.get("reason", "")))
    # Same-name groups (#755): `NameGroups` has already checked every answer.
    name_groups = bundle.get("name_groups")
    group_result = name_groups.result() if name_groups is not None else \
        {"merges": [], "unsure": [], "carried": [], "rejected": 0}
    for keep_id, merge_id, verdict, reason in group_result["merges"]:
        decisions.append((keep_id, merge_id, verdict, "model", reason))

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
        # What in this batch carries the merged id before it is folded away (D280).
        entry["undo"].setdefault("version", merge_log.UNDO_VERSION)
        entry["undo"]["changes"] = _carried_in_batch(vault, shas, merge_id)

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
    # A same-name group's records the model could not tell apart, each still a record of its own.
    pending += [(a, b, verdict, True) for a, b, verdict in group_result["unsure"]]
    for a, b, verdict, declined in pending:
        if a in remap or b in remap or a not in profiles or b not in profiles:
            continue
        entry = merge_log.candidate_entry(a=profiles[a], b=profiles[b], verdict=verdict,
                                          model_declined=declined, run=run_id)
        _stage_on_owner(vault, shas, {a, b}, entry)
        n_candidates += 1

    # What this run could not finish comparing is carried to the next (#755): never dropped
    # unseen. The carry replaces the last run's, so one is staged whenever either is non-empty.
    carry = sorted({_current(eid) for eid in [*(bundle.get("carry_pairs") or []),
                                               *group_result["carried"]]})
    if carry or bundle.get("carried_in"):
        _stage_on_owner(vault, shas, set(), merge_log.carry_entry(carry, run=run_id))

    # Flatten the chain: `apply_contradictions` follows the map one step only, so every key must
    # point straight at its final survivor rather than an intermediate id a later merge in this
    # same batch folded away.
    remap = {eid: _current(eid) for eid in remap}
    return {"merged": applied, "remap": remap, "contradictions": parsed.get("contradictions") or [],
            "candidates": n_candidates,
            "carried": {"pairs": bundle.get("pairs_dropped") or 0,
                        "records": len(group_result["carried"]),
                        "rejected": group_result["rejected"]}}


def apply_contradictions(vault: Path, items: list, remap: dict, warn) -> list[dict]:
    """File each flagged contradiction through `contradiction.run` — the same deterministic writer
    the manual the `add-contradiction` operation command uses (D81).

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
                a_fact=item.get("a_fact"), b_fact=item.get("b_fact"),
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
