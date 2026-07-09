"""
Canonical entity-type vocabulary.

`entity.type` is the one field the extraction model invents per document, and it is
load-bearing twice over: it is part of the identity key that decides whether two
same-named entities are the same real-world thing (`write_vault._reconcile_entity_ids`),
and it is the on-disk folder segment (`write_vault._type_dir` → ``entities/<type>/``).
When the model's type vocabulary drifts document-to-document — ``company`` in one,
``financialinstitution`` in the next — reconciliation silently fails and the same
entity fragments across two folders (#335).

The fix is a closed vocabulary of six **durable-referent classes**, enforced two ways:
  * the extraction prompt states the six values as the only allowed vocabulary (steers
    every backend, including the ones that don't wire-enforce a JSON-schema enum — D98);
  * `canonical_type()` deterministically collapses whatever the model still emits onto
    one of the six (or ``other``), so correctness never depends on model compliance.

Only *actors, places, and things* earn a type here — occurrences live on the timeline
(a dated `key_fact`) and records live in the document store (`documents/`), so neither
needs a bucket. See ARCHITECTURE §I1 and DECISIONS D105.
"""

import re

# The six canonical buckets, in slug form (each is also its ``entities/<type>/`` folder name).
ENTITY_TYPES = ("person", "organization", "public-body", "place", "asset", "proceeding")

# Deterministic backstop only — never offered to the model. Anything the closed vocabulary
# and the synonym map below don't recognise lands here rather than coining a new folder.
FALLBACK_TYPE = "other"


def _norm(s: str) -> str:
    """Fold a raw type to a match key: lowercase, drop every non-alphanumeric char. So
    ``"Public Body"``, ``"public-body"``, ``"public_body"`` and ``"publicbody"`` all collide."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# raw synonym (any casing/spacing) → canonical bucket. Keys are stored normalised via `_norm`.
_SYNONYMS: dict[str, str] = {}


def _add(bucket: str, *words: str) -> None:
    for w in words:
        _SYNONYMS[_norm(w)] = bucket


# person — a named human actor.
_add("person",
     "person", "individual", "people", "human", "practitioner", "officer", "official",
     "director", "shareholder", "witness", "victim", "complainant", "respondent", "applicant",
     "plaintiff", "defendant", "accused", "counsel", "lawyer", "solicitor", "notary",
     "arbitrator", "adjudicator", "judge", "justice", "councillor", "politician", "candidate",
     "minister", "patient", "employee", "beneficiary", "trustee", "author", "journalist")

# organization — any private collective: firms, banks, unions, NGOs, universities, and the
# institutional financial vehicles (a pension/benefit plan is an institution, not an instrument).
_add("organization",
     "organization", "organisation", "org", "company", "corporation", "corp", "incorporated",
     "firm", "business", "enterprise", "financialinstitution", "bank", "creditunion", "insurer",
     "insurance", "insurancecompany", "reinsurer", "lender", "creditor", "vendor", "supplier",
     "contractor", "subcontractor", "union", "labourunion", "laborunion", "tradeunion",
     "association", "group", "society", "ngo", "nonprofit", "notforprofit", "charity",
     "foundation", "institution", "university", "college", "school", "hospital", "employer",
     "partnership", "trust", "fund", "pensionplan", "pension", "pensionfund", "benefitplan",
     "benefitsplan", "employeebenefitplan", "healthbenefitplan", "estate", "party",
     "politicalparty", "campaign", "outlet", "publisher", "broadcaster", "channel", "conglomerate")

# public-body — any body exercising public/state authority (broader than "government"):
# agencies, regulators, ministries, municipalities, courts-as-institutions, tribunals, boards.
_add("public-body",
     "publicbody", "government", "governmentbody", "governingbody", "governmentagency", "agency",
     "regulator", "regulatorybody", "regulatoryagency", "ministry", "department", "municipality",
     "province", "state", "county", "council", "court", "tribunal", "commission", "committee",
     "board", "authority", "publicauthority", "crowncorporation", "legislature", "parliament",
     "police", "policeservice", "correctionalservice", "paroleboard", "labourboard", "laborboard",
     "auditoffice", "oversightbody", "electioncommission", "publicinstitution", "agencybody")

# place — a physical location or immovable property.
_add("place",
     "place", "address", "location", "property", "realproperty", "realestate", "land", "parcel",
     "lot", "facility", "building", "site", "premises", "region", "city", "town", "country",
     "jurisdiction", "geography", "venue")

# asset — a movable or intangible owned/registered thing (chattels + financial instruments +
# digital identifiers), as opposed to the immovable locations under `place`.
_add("asset",
     "asset", "vehicle", "car", "truck", "aircraft", "airplane", "plane", "vessel", "ship", "boat",
     "domain", "domainname", "website", "ipaddress", "ip", "sslcertificate", "certificate",
     "account", "bankaccount", "security", "share", "stock", "bond", "instrument",
     "financialinstrument", "cryptocurrency", "crypto", "wallet", "artwork", "patent", "trademark")

# proceeding — a legal/adjudicative matter that persists and gathers parties, filings, and
# decisions (litigation, insolvency, arbitration, discipline, inquiry). One entity per matter,
# not per hearing — individual hearings/motions are timeline events tagged to the proceeding.
_add("proceeding",
     "proceeding", "courtcase", "case", "lawsuit", "litigation", "suit", "matter", "action",
     "claim", "bankruptcy", "insolvency", "ccaa", "receivership", "arbitration", "mediation",
     "inquiry", "commissionofinquiry", "investigation", "prosecution", "trial", "appeal",
     "hearing", "docket", "enforcementaction", "disciplinaryproceeding", "disciplinaryaction",
     "grievance", "complaint")

_CANONICAL = {_norm(t): t for t in ENTITY_TYPES}


def canonical_type(raw: str) -> str:
    """Collapse a model-supplied entity type onto one of `ENTITY_TYPES`, else `FALLBACK_TYPE`.

    Idempotent (a canonical value maps to itself) and deterministic, so it can be applied at
    every point ``type`` is load-bearing — the reconciliation key and the folder segment — and
    give the same answer regardless of which backend produced the raw string.
    """
    n = _norm(raw)
    if not n:
        return FALLBACK_TYPE
    if n in _CANONICAL:
        return _CANONICAL[n]
    return _SYNONYMS.get(n, FALLBACK_TYPE)
