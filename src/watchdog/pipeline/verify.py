"""Deterministic merge of the verification pass's candidate facts (#535).

The verifier (`prompts.build_verify_prompt`) re-reads the document it just extracted and answers
one question: what material fact is on the page and absent from the fact list. Its answer is a
list of candidate `key_facts` — and everything that happens to those candidates afterwards
happens here, in code. Nothing is asked of a model twice: no second call to judge whether a
candidate duplicates an existing fact, no model-authored merge.

Two jobs, in order:

* **Sanitize** — drop anything that isn't a usable fact object, and bound the fields a candidate
  is allowed to assert. Entity tags are filtered against the ids the *extraction* actually
  produced, so a verifier that coins `laurentian-university-board` where the extraction wrote
  `board-of-governors` loses the tag rather than filing the fact under an entity that does not
  exist. `page` is coerced to a real page number or dropped.
* **Suppress near-duplicates** — the failure mode the pass is most prone to. A recall-biased
  gap-finder restates what it was shown: a rewording, a generalization, a sub-clause of a fact
  already captured. Text-identity alone catches almost none of that, so suppression is
  token-set based (Jaccard, plus containment for the sub-clause case), with one carve-out — a
  candidate carrying a numeric token the matched fact doesn't have is kept regardless, since a
  new figure is exactly the kind of buried detail this pass exists to recover.

Suppression is deliberately tuned to let borderline cases through rather than block them: the
prompt tells the verifier to over-list, and precision is measured separately
(`benchmarks/verifier_precision.py`) rather than defended by an aggressive filter here. Every
surviving fact is tagged `added_by: "verify"` — that tag is what makes the precision measurement
possible at all, and what lets a reporter see which facts came from the second read.
"""

import re

# Token-set overlap at or above this is treated as the same fact restated. Chosen loose enough
# that a genuine addition sharing most of its wording with an existing fact survives, since the
# whole point of the pass is recovering facts the extractor was already circling.
_JACCARD_SUPPRESS = 0.75
# A candidate whose content words are almost entirely contained in an existing fact adds nothing
# the reporter can't already read — the "sub-clause of a captured fact" restatement.
#
# Fitted, not hand-picked (#589). All 220 additions from the 2026-08-09 verify arm were graded
# material/trivial by hand, then this threshold swept through `_is_restatement` itself against
# those grades, document-scoped. 0.9 caught 3 trivial additions; 0.6 catches 11, and no material
# one is lost anywhere down to 0.5. It is the numeric carve-out below that makes a threshold this
# low safe — a material fact at high word overlap almost always carries a figure the matched fact
# lacks, which is what the carve-out keys on. 0.6 rather than 0.5 because material containment
# runs to 0.65 in this sample, and the extra margin costs 3 suppressions.
#
# What this does *not* buy: only 73 of the 128 trivial additions restate an existing fact at all,
# and their median containment is 0.39 — far under any threshold that keeps the material ones.
# Word overlap cannot see a paraphrase, so this is a small correction to a guard with a low
# ceiling, not a fix for the ledger's size. That lever is the verifier's materiality criterion.
_CONTAINMENT_SUPPRESS = 0.6
# …but only where the ratio can carry that much precision. Containment over a `k`-token candidate
# is quantized to 1/k, so at four content tokens the only readings are 0, .25, .5, .75, 1 — and a
# fact differing from another in a single content word ("the *report* was filed" against "the
# *order* was filed") reads 0.75, which 0.6 would suppress and 0.9 would not. Below this length
# the strict bar stays, which costs nothing measurable: the shortest of the 220 real additions
# carries 9 content tokens, so every one of them clears the gate.
_CONTAINMENT_MIN_TOKENS = 8
_CONTAINMENT_SUPPRESS_SHORT = 0.9

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9.,/$%-]*")
_NUM_RE = re.compile(r"\d")

# Function words carry no discriminating signal between two statements of the same fact, and
# leaving them in inflates the overlap of any two English sentences toward the threshold.
_STOPWORDS = frozenset("""
a an the and or but of to in on at by for from with without as is are was were be been being
that this these those it its his her their there which who whom whose what when where while
has have had will would shall should may might can could must not no nor if then than so such
""".split())


def _tokens(text: str) -> frozenset[str]:
    """Content tokens of a fact sentence: lowercased, punctuation-trimmed, stopwords removed.
    Numeric tokens keep their `$`/`%`/`,`/`.` so `$5.4` and `5.4` don't collide with `54`."""
    return frozenset(
        t for w in _WORD_RE.findall(text.lower())
        if (t := w.strip(".,")) and t not in _STOPWORDS
    )


def _numeric(tokens: frozenset[str]) -> frozenset[str]:
    return frozenset(t for t in tokens if _NUM_RE.search(t))


def _is_restatement(candidate: frozenset[str], existing: frozenset[str]) -> bool:
    """Whether `candidate` says something `existing` already says.

    True when the two overlap heavily (Jaccard) or the candidate is essentially a subset of the
    existing fact (containment) — unless the candidate carries a figure the existing fact does
    not, in which case it is adding a number to the record and is kept whatever the word overlap
    says."""
    if not candidate or not existing:
        return False
    if _numeric(candidate) - _numeric(existing):
        return False
    shared = len(candidate & existing)
    if shared / len(candidate | existing) >= _JACCARD_SUPPRESS:
        return True
    threshold = (_CONTAINMENT_SUPPRESS if len(candidate) >= _CONTAINMENT_MIN_TOKENS
                 else _CONTAINMENT_SUPPRESS_SHORT)
    return shared / len(candidate) >= threshold


def _sanitize(candidate: dict, known_ids: set[str]) -> dict | None:
    """One candidate reduced to the fields it is allowed to assert, or None if unusable."""
    if not isinstance(candidate, dict):
        return None
    text = (candidate.get("fact") or "").strip() if isinstance(candidate.get("fact"), str) else ""
    if not text:
        return None
    fact: dict = {"fact": text}

    page = candidate.get("page")
    if isinstance(page, int) and not isinstance(page, bool) and page > 0:
        fact["page"] = page

    # `stated` is the omit-default (schemas._BASIS), so only an explicit `inferred` is worth
    # carrying — and an unrecognized value is dropped rather than passed to post-flight, which
    # would reject the whole document over one candidate's typo.
    if candidate.get("basis") == "inferred":
        fact["basis"] = "inferred"

    date = candidate.get("date")
    if isinstance(date, str) and date.strip():
        fact["date"] = date.strip()

    # `quote_locator`, not `quote` (#529/D170): the model emits the first several words and
    # post-flight expands them against the page text — which runs after this merge, over every
    # key fact, so a verified fact's locator is resolved by exactly the same code path as an
    # extracted one's.
    locator = candidate.get("quote_locator")
    if isinstance(locator, str) and locator.strip():
        fact["quote_locator"] = locator.strip()

    entities = candidate.get("entities")
    if isinstance(entities, list):
        tagged = [e for e in entities if isinstance(e, str) and e in known_ids]
        if tagged:
            fact["entities"] = tagged

    fact["added_by"] = "verify"
    return fact


def merge_candidates(extraction: dict, candidates: list, prior_facts: list | None = None) -> dict:
    """Append the verifier's surviving candidates to `extraction`'s `document.key_facts`, in
    place. Returns `{"added": int, "suppressed": int}` for telemetry — `suppressed` counts both
    unusable candidates and near-duplicates, since from the caller's point of view they are the
    same thing: a candidate that did not become a fact.

    Candidates are compared against the extraction's facts *and* against the candidates already
    accepted from the same pass, so a verifier that lists one miss twice adds it once.

    `prior_facts` are facts from earlier sections of the same document, which the caller passes
    on a sectioned extraction. Without them the guard's scope is one section while the ledger's
    scope is the document, and a candidate restating a fact captured in a *different* section can
    never be caught — structurally, not marginally, because section boundaries overlap by a page
    (#589). Every fact in the 2026-08-09 verify arm's ledger that `_is_restatement` scores as a
    duplicate got there this way.
    """
    doc = extraction.setdefault("document", {})
    facts = doc.setdefault("key_facts", [])
    known_ids = {e["id"] for e in extraction.get("entities") or []
                 if isinstance(e, dict) and isinstance(e.get("id"), str)}

    seen = [_tokens(f.get("fact") or "") for f in list(prior_facts or []) + facts
            if isinstance(f, dict)]
    added = 0
    for candidate in candidates or []:
        fact = _sanitize(candidate, known_ids)
        if fact is None:
            continue
        tokens = _tokens(fact["fact"])
        if any(_is_restatement(tokens, other) for other in seen):
            continue
        facts.append(fact)
        seen.append(tokens)
        added += 1
    return {"added": added, "suppressed": len(candidates or []) - added}
