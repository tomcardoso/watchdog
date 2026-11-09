"""Placeholder entities and the document with no subject (D393).

An extraction sometimes names an "entity" whose name says only that the document does not show
who or what it is: "Unknown", "Unidentified person (name not shown)", "Personne non
identifiée". That is not an entity. Kept, it becomes a note, a node in the network, a duplicate
candidate and, when it is the document's `morgue_entity_id`, a morgue folder of unrelated
originals. `drop` removes such entities from an extraction before post-flight, with every
reference to them, and clears a `morgue_entity_id` that names one.

The test is deliberately narrow. A name is a placeholder only when it is made of a placeholder
word or phrase ("unknown", "unidentified", "not legible", "inconnu", "non identifiée", …) plus,
at most, generic words ("corporation", "subject", "the", "société", …) or words written in lower
case ("unidentified bidder"). A capitalized word that is not generic keeps the name, so a band
called "Unknown Mortal Orchestra" stays: a proper name's capital letters are the evidence that
it is a name. The cost of that choice is that "Unknown Driver", capitalized by the model, stays
too; erring toward keeping a doubtful entity is the safe side, since the reporter can hide it
and nothing is lost. A real organization literally called "The Unknown Company" would be
dropped; its facts keep their text, only the entity record goes.

Brackets get no special treatment: "Acme (name not shown)" and "Unknown (Northgate Supply)" keep
their capitalized words and stay, while "Unidentified person (name not shown)" is all
placeholder and generic words and goes.
"""
from __future__ import annotations

import re
import unicodedata

# Placeholder words and phrases, accents stripped and lower-cased. English and French.
_PHRASES: tuple[tuple[str, ...], ...] = (
    ("unknown",), ("unidentified",), ("unnamed",), ("undetermined",), ("unspecified",),
    ("illegible",), ("unreadable",), ("redacted",),
    ("not", "legible"), ("not", "identified"), ("not", "shown"), ("not", "known"),
    ("not", "named"), ("not", "specified"), ("not", "determined"), ("not", "stated"),
    ("n", "a"),
    ("inconnu",), ("inconnue",), ("inconnus",), ("inconnues",),
    ("indetermine",), ("indeterminee",), ("illisible",), ("caviarde",), ("caviardee",),
    ("non", "identifie"), ("non", "identifiee"), ("non", "identifies"), ("non", "identifiees"),
    ("non", "precise"), ("non", "precisee"), ("non", "nomme"), ("non", "nommee"),
    ("non", "lisible"), ("non", "indique"), ("non", "indiquee"),
)

# Words that name a kind of thing, not a thing; and the articles and prepositions around them.
_GENERIC = frozenset("""
a an the of and or for by to other third
subject entity entities person persons people individual individuals party parties
organization organisation company corporation corp business firm body agency group
owner author sender recipient applicant registrant registrar holder signatory signer filer
officer official employee name names document source contact
le la les l un une de du des d et ou autre autres tiers
sujet entite personne personnes particulier individu partie parties organisme societe
entreprise compagnie proprietaire auteur expediteur destinataire demandeur requerant
titulaire signataire nom document
""".split())

_MAX_WORDS = 8
_WORD = re.compile(r"[^\W_]+")


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def is_placeholder(name: str, *, case_matters: bool = True) -> bool:
    """True when `name` says only that who or what it names is not shown. `case_matters=False`
    is for a kebab-case id, whose case carries no evidence: there every word other than the
    placeholder must be generic."""
    if not isinstance(name, str):
        return False
    words = _WORD.findall(_fold(name))
    if not words or len(words) > _MAX_WORDS:
        return False
    low = [w.lower() for w in words]
    covered = [False] * len(low)
    for phrase in _PHRASES:
        n = len(phrase)
        for i in range(len(low) - n + 1):
            if tuple(low[i:i + n]) == phrase:
                covered[i:i + n] = [True] * n
    if not any(covered):
        return False
    return all(
        c or lw in _GENERIC or (case_matters and w == lw)
        for w, lw, c in zip(words, low, covered)
    )


def drop(extraction: dict) -> list[str]:
    """Remove placeholder entities from a staged extraction, in place, and return a warning per
    entity removed. A fact keeps its text and its other tags; a relationship whose other end was
    a placeholder goes (it relates to no one). A `morgue_entity_id` naming a removed entity, or
    itself a placeholder id, is cleared, so the original is filed as having no subject."""
    from watchdog.pipeline.write_vault import slugify
    entities = extraction.get("entities")
    if not isinstance(entities, list):
        return []
    gone: set[str] = set()
    kept: list = []
    warnings: list[str] = []
    for ent in entities:
        if isinstance(ent, dict) and is_placeholder(ent.get("name") or ""):
            eid = str(ent.get("id") or "")
            gone.update({eid, slugify(eid), slugify(ent.get("name") or "")} - {""})
            warnings.append(f"entity {ent.get('name')!r} dropped: its name says only that the "
                            "document does not show who or what it is")
            continue
        kept.append(ent)
    # An id a kept entity holds is never treated as gone, whatever a dropped one slugified to.
    held = {i for e in kept if isinstance(e, dict) for i in (str(e.get("id") or ""),
                                                            slugify(str(e.get("id") or "")))}
    gone -= held

    def hit(ref) -> bool:
        return isinstance(ref, str) and (ref in gone or slugify(ref) in gone)

    if gone:
        extraction["entities"] = kept
        for fact in (extraction.get("document") or {}).get("key_facts") or []:
            tags = fact.get("entities") if isinstance(fact, dict) else None
            if isinstance(tags, list) and any(hit(t) for t in tags):
                fact["entities"] = [t for t in tags if not hit(t)]
        for ent in kept:
            roles = ent.get("roles") if isinstance(ent, dict) else None
            if isinstance(roles, list):
                ent["roles"] = [r for r in roles
                                if not (isinstance(r, dict) and hit(r.get("target_id")))]
    subject = extraction.get("morgue_entity_id")
    if isinstance(subject, str) and subject and subject not in held and (
            hit(subject) or is_placeholder(subject, case_matters=False)):
        extraction["morgue_entity_id"] = None
    return warnings
