"""When two entity records are the same real-world thing: the confidence tiers (D285).

Every automatic join of two entity records goes through `classify`, which returns a tier:

* **high** — merged by code and logged: the same strong identifier (a registration, licence,
  court-file or parcel number) on both, a person's same full name *plus* a shared role, employer or
  street address, or a non-person's same name when that name is specific enough to pick out one
  thing ("Northgate Civil Works Ltd.", "City of Port Calder").
* **medium** — the reconcile model judges it with both records' facts side by side: a person's same
  full name with nothing else in common, an organization's short or generic name ("the City",
  "Council"), the same words in another order, or (for anything but a person) similar names.
* **low** — never merged automatically; a "possible same" item in Review: one person's name is an
  initialled or partial form of the other's ("J. Smith" / "John Smith"), or both are the same
  short name ("Mr. Pike").

`None` means the pair is not a candidate at all: different types, different people's names, a
conflicting identifier, or a pair the reporter marked "Not the same".

Everything here is deterministic Python over what the extraction already recorded (I1): names,
aliases, roles and facts. Identifiers have no field in the extraction schema yet, so a small,
type-gated harvest reads labelled numbers out of facts that are about exactly one entity
(`harvest_identifiers`).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

from watchdog.pipeline.entity_norm import normalize_entity_name
from watchdog.pipeline.entity_type import canonical_type

TIERS = ("high", "medium", "low")

# ── names ─────────────────────────────────────────────────────────────────────────────────────

# Titles and offices dropped from either end of a person's name before comparing: "Mayor Robert
# Delacroix" is Robert Delacroix, "Cllr. D. Whitcombe" is D. Whitcombe. A bare "M." is *not* here:
# in English documents it is far more often an initial than "Monsieur".
_TITLES = frozenset("""
mr mrs ms miss mx dr doctor prof professor sir dame lord lady madam madame mme mlle hon honourable
honorable rt right the justice judge chief associate master mayor deputy councillor councilor cllr
coun alderman reeve senator sen minister premier president commissioner auditor clerk registrar
city integrity general sgt sergeant cst constable det detective insp inspector supt superintendent
capt captain lt col rev reverend father fr sister rabbi imam pastor mp mpp mla mna kc qc esq phd md
cpa jd llb cfa peng
""".split())
_GENERATIONAL = frozenset({"jr", "sr", "ii", "iii", "iv"})

# Words that say what kind of body or place something is rather than which one. A name made only of
# these ("the City", "Council", "City Hall", "the Court") cannot identify one thing on its own.
_GENERIC = frozenset("""
city town village township county region regional district municipality municipal province
provincial state federal national government council committee board commission authority agency
department dept ministry office bureau service services court tribunal registry police crown public
general central hall street road avenue building centre center company corporation group holdings
partners associates firm bank trust fund union association society foundation university college
school hospital clinic church project program programme plan lands land property site lot parcel
unit estate inquiry review hearing case matter proceeding application appeal investigation audit
report contract agreement bond charge account vessel staff management administration executive team
division branch section secretariat applicant applicants respondent respondents plaintiff defendant
""".split())
# A legal-form suffix makes a single distinctive word a registered name ("Acme Ltd.").
_LEGAL = frozenset("""
inc incorporated ltd limited llc llp lp corp corporation ulc plc gmbh ag sa sarl pty ltee co
""".split())
_STOPWORDS = frozenset({"the", "of", "and", "a", "an", "de", "du", "la", "le", "for"})


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


def person_parts(name: str) -> tuple[tuple[str, ...], str, str]:
    """(given names, surname, generational suffix) of a person's name, titles dropped.

    "Smith, John" is read as John Smith; "R.T. Okafor" has given names ("r", "t"); a hyphenated
    surname is one token."""
    raw = _fold(name)
    if raw.count(",") == 1:
        last, first = (p.strip() for p in raw.split(","))
        first_tokens = re.sub(r"[^\w\s]", " ", first).split()
        if last and first_tokens and not set(first_tokens) <= _GENERATIONAL | _TITLES:
            raw = f"{first} {last}"
    raw = re.sub(r"['’-]", "", raw)
    tokens = re.sub(r"[^\w\s]|_", " ", raw).split()
    while tokens and tokens[0] in _TITLES:
        tokens.pop(0)
    suffix = ""
    while tokens and (tokens[-1] in _TITLES or tokens[-1] in _GENERATIONAL):
        if tokens[-1] in _GENERATIONAL and not suffix:
            suffix = tokens[-1]
        tokens.pop()
    if not tokens:
        return (), "", suffix
    return tuple(tokens[:-1]), tokens[-1], suffix


def is_full_person_name(name: str) -> bool:
    """A first name and a surname, neither an initial ("John Smith", "John A. Smith")."""
    given, surname, _ = person_parts(name)
    return bool(given) and len(given[0]) > 1 and len(surname) > 1


def _given_compatible(short: tuple[str, ...], long: tuple[str, ...]) -> bool:
    """Every given name of `short` matches one of `long`'s, in order — equal, or an initial of it."""
    i = 0
    for tok in short:
        while i < len(long) and not (tok == long[i] or (len(tok) == 1 and long[i][0] == tok)
                                     or (len(long[i]) == 1 and tok[0] == long[i])):
            i += 1
        if i == len(long):
            return False
        i += 1
    return True


def person_relation(a: str, b: str) -> str | None:
    """How two spellings of a person's name relate: ``"same-full"`` (the same full name),
    ``"same-partial"`` (the same short or initialled name), ``"partial"`` (one is an initialled or
    shortened form of the other), or None (different people's names)."""
    ga, sa, xa = person_parts(a)
    gb, sb, xb = person_parts(b)
    if not sa or sa != sb:
        return None
    if xa and xb and xa != xb:
        return None
    if ga == gb:
        if xa != xb:
            return "partial"           # "John Smith" / "John Smith Jr.": maybe father and son
        return "same-full" if is_full_person_name(a) else "same-partial"
    short, long = (ga, gb) if len(ga) <= len(gb) else (gb, ga)
    return "partial" if _given_compatible(short, long) else None


def name_key(name: str) -> str:
    """A non-person name's comparison key: `normalize_entity_name` with a leading "the" dropped."""
    key = normalize_entity_name(name)
    return key[4:] if key.startswith("the ") else key


def _name_tokens(name: str) -> list[str]:
    return [t for t in normalize_entity_name(name).split() if t not in _STOPWORDS]


def is_distinctive(name: str) -> bool:
    """Whether a non-person name is specific enough to identify one thing: two words that are not
    generic ("Northgate Civil Works", "Port Calder City Council"), one such word with a legal-form
    suffix ("Acme Ltd.") or within a name of three or more words ("Office of the City Auditor"),
    or a number with another word ("Pier 9", "14 Dockside Road"). "The City", "Council", "City
    Hall" and "Acme Holdings" are not."""
    # Split on hyphens too: "Toronto-Dominion Bank" is two distinctive words, not one.
    tokens = [t for t in re.split(r"[^\w]+|_", _fold(name).replace("&", " and ")) if t
              and t not in _STOPWORDS]
    digits = [t for t in tokens if any(c.isdigit() for c in t)]
    words = [t for t in tokens if t not in _GENERIC and t not in _LEGAL and len(t) > 1
             and t not in digits]
    if len(words) >= 2:
        return True
    if words and (any(t in _LEGAL for t in tokens) or len(tokens) >= 3):
        return True
    return bool(digits) and len(tokens) >= 2


# ── identifiers ───────────────────────────────────────────────────────────────────────────────

# Labelled identifiers only: a bare number in a fact is far too often an amount or a date. Each
# scheme lists the entity types it can identify, so a parcel number in a fact about a company is
# never taken to be the company's.
_ID_VALUE = r"([A-Z]{0,4}[\s-]?\d[\dA-Z\-]{2,}[\dA-Z])\b"
_SCHEMES: list[tuple[str, str, frozenset[str], re.Pattern]] = [
    ("registration", "registration number", frozenset({"organization"}), re.compile(
        r"\b(?:incorporation|corporation|corporate|company|registration|registry|business|entity)"
        r"\s+(?:no\.?|number|#)\s*:?\s*" + _ID_VALUE, re.IGNORECASE)),
    ("licence", "licence number", frozenset({"person", "organization"}), re.compile(
        r"\blicen[cs]e\s+(?:no\.?|number|#)\s*:?\s*" + _ID_VALUE, re.IGNORECASE)),
    ("court-file", "court file number", frozenset({"proceeding"}), re.compile(
        r"\b(?:court\s+)?(?:file|docket)\s+(?:no\.?|number|#)?\s*:?\s*" + _ID_VALUE, re.IGNORECASE)),
    ("parcel", "parcel identifier", frozenset({"place", "asset"}), re.compile(
        r"\b(?:PID|PIN|parcel\s+(?:identifier|number|no\.?)|roll\s+(?:number|no\.?))\s*:?\s*"
        + _ID_VALUE, re.IGNORECASE)),
]
_SCHEME_LABELS = {s: label for s, label, _, _ in _SCHEMES}
# A court file number written into a proceeding's own name ("Court File JR-2022-0481").
_DOCKET_IN_NAME = re.compile(r"\b[A-Z]{1,4}-\d{2,4}-\d{2,}(?:-[A-Z0-9]+)*\b")


def _id_value(raw: str) -> str:
    return re.sub(r"[\s\-]", "", raw).upper()


def scheme_label(scheme: str) -> str:
    return _SCHEME_LABELS.get(scheme, scheme)


def harvest_identifiers(etype: str, names: list[str], facts: list[dict]) -> dict[str, set[str]]:
    """Identifiers for one entity: labelled numbers in the facts about it alone, and a court file
    number in a proceeding's own name. `facts` are key facts already narrowed to this entity; a
    fact tagged to several entities is skipped, since its number could belong to any of them."""
    etype = canonical_type(etype)
    out: dict[str, set[str]] = {}
    for fact in facts:
        if len(fact.get("entities") or []) != 1:
            continue
        text = fact.get("fact") or ""
        for scheme, _, types, pattern in _SCHEMES:
            if etype in types:
                for m in pattern.finditer(text):
                    out.setdefault(scheme, set()).add(_id_value(m.group(1)))
    if etype == "proceeding":
        for name in names:
            for m in _DOCKET_IN_NAME.finditer(name or ""):
                out.setdefault("court-file", set()).add(_id_value(m.group()))
    return out


# ── profiles: what the documents say about one entity record ──────────────────────────────────

# Relationship words that carry no identifying signal, and residence words that are too weak to
# tell two people apart (two John Smiths can both live in Port Calder).
_REL_STOP = frozenset({"of", "at", "the", "for", "to", "with", "by", "on", "in", "a", "an", "as",
                       "and", "from", "is", "was", "former", "formerly", "previous", "current"})
_REL_WEAK = frozenset({"resident", "resides", "lives", "living", "located", "based", "born",
                       "citizen", "native"})
FACT_CAP = 6


def _rel_tokens(relationship: str) -> frozenset[str]:
    return frozenset(t for t in normalize_entity_name(relationship).split()
                     if t not in _REL_STOP and t not in _REL_WEAK)


class Profile:
    """One entity record as the documents describe it: names, relations, facts and identifiers."""

    def __init__(self, eid: str, name: str, etype: str):
        self.id = eid
        self.name = name
        self.type = canonical_type(etype)
        self.surfaces: list[str] = [name]
        self.relations: dict[str, dict] = {}     # target id -> {"names", "types", "rels"}
        self.facts: list[dict] = []              # {"id", "fact", "page", "sha", "title", "date"}
        self.documents: list[str] = []
        self.identifiers: dict[str, set[str]] = {}

    def add_surface(self, name: str) -> None:
        if name and name.lower() not in {s.lower() for s in self.surfaces}:
            self.surfaces.append(name)

    def add_relation(self, target_id: str, relationship: str, target_name: str, target_type: str) -> None:
        if not target_id or target_id == self.id:
            return
        r = self.relations.setdefault(target_id, {"name": target_name or target_id,
                                                  "type": canonical_type(target_type or ""),
                                                  "rels": {}})
        if relationship:
            r["rels"].setdefault(relationship, _rel_tokens(relationship))

    def absorb(self, other: "Profile") -> None:
        """Fold `other` in — the record after a merge, for the next comparison in the same pass."""
        for s in other.surfaces:
            self.add_surface(s)
        for tid, r in other.relations.items():
            mine = self.relations.setdefault(tid, {"name": r["name"], "type": r["type"], "rels": {}})
            for rel, toks in r["rels"].items():
                mine["rels"].setdefault(rel, toks)
        known = {f["id"] for f in self.facts}
        self.facts += [f for f in other.facts if f["id"] not in known]
        self.documents += [d for d in other.documents if d not in self.documents]
        for scheme, values in other.identifiers.items():
            self.identifiers.setdefault(scheme, set()).update(values)

    def roles_digest(self, limit: int = 8) -> list[dict]:
        out = []
        for tid, r in self.relations.items():
            for rel in r["rels"]:
                out.append({"relationship": rel, "target": r["name"]})
        return out[:limit]

    def facts_digest(self, limit: int = FACT_CAP) -> list[dict]:
        return [{"document": f["title"], "date": f["date"], "page": f["page"],
                 "fact": f["fact"][:300]} for f in self.facts[:limit]]


def _street_address(name: str) -> bool:
    return bool(re.search(r"\d", name or "")) and len(_name_tokens(name)) >= 2


def shared_attributes(a: Profile, b: Profile) -> list[dict]:
    """Relations both records hold to the same third entity that say something about who they are:
    the same role or employer (a relationship word in common), or the same street address."""
    out = []
    for tid in sorted(set(a.relations) & set(b.relations)):
        if tid in (a.id, b.id):
            continue
        ra, rb = a.relations[tid], b.relations[tid]
        common = [rel for rel, toks in ra["rels"].items()
                  if any(toks & t for t in rb["rels"].values())]
        if common:
            out.append({"relationship": common[0], "target_id": tid, "target_name": ra["name"]})
        elif ra["type"] == "place" and _street_address(ra["name"]):
            rel = next(iter(ra["rels"]), "linked to")
            out.append({"relationship": rel, "target_id": tid, "target_name": ra["name"]})
    return out


# ── the tiers ─────────────────────────────────────────────────────────────────────────────────

def pair_id(a: str, b: str) -> str:
    """The Review id of a "possible same" pair, stable whichever side is named first."""
    lo, hi = sorted((a, b))
    return "same:" + hashlib.sha1(f"{lo}|{hi}".encode("utf-8")).hexdigest()[:12]


def _verdict(tier, rule, reason, *, same_name=False, surface=None, identifier=None, shared=None):
    return {"tier": tier, "rule": rule, "reason": reason, "same_name": same_name,
            "evidence": {"surface": surface, "identifier": identifier, "shared": shared or []}}


_TYPE_WORD = {"person": "person", "organization": "organization", "public-body": "public body",
              "place": "place", "asset": "asset", "proceeding": "proceeding"}


def classify(a: Profile, b: Profile, dismissed: frozenset[str] | set[str] = frozenset()) -> dict | None:
    """The tier for joining records `a` and `b`, or None when they are not a candidate pair."""
    if a.type != b.type or a.id == b.id and a is b:
        return None
    if a.id != b.id and pair_id(a.id, b.id) in dismissed:
        return None
    for scheme in set(a.identifiers) & set(b.identifiers):
        if not a.identifiers[scheme] & b.identifiers[scheme]:
            return None          # two different numbers of the same kind: two different things
    word = _TYPE_WORD.get(a.type, "entity")

    if a.type == "person":
        best, surface = None, None
        rank = {"same-full": 3, "partial": 1, "same-partial": 1}
        for sa in a.surfaces:
            for sb in b.surfaces:
                rel = person_relation(sa, sb)
                if rel and rank[rel] > rank.get(best or "", 0):
                    best, surface = rel, (sa if rel != "partial" else f"{sa} / {sb}")
        if best is None:
            return None
    else:
        best, surface = None, None
        keys_b = {name_key(s): s for s in b.surfaces}
        for sa in a.surfaces:
            if name_key(sa) in keys_b:
                if best != "same" or (not is_distinctive(surface) and is_distinctive(sa)):
                    best, surface = "same", sa
        if best is None:
            toks_b = {frozenset(_name_tokens(s)) for s in b.surfaces}
            for sa in a.surfaces:
                if frozenset(_name_tokens(sa)) in toks_b and _name_tokens(sa):
                    best, surface = "same-tokens", sa
                    break
        best = best or "similar"

    shared_ids = sorted((scheme, v) for scheme in set(a.identifiers) & set(b.identifiers)
                        for v in a.identifiers[scheme] & b.identifiers[scheme])
    if shared_ids:
        scheme, value = shared_ids[0]
        return _verdict("high", "same-identifier",
                        f"Both records carry the same {scheme_label(scheme)}, {value}.",
                        same_name=best in ("same-full", "same"), surface=surface,
                        identifier={"scheme": scheme, "value": value})

    if a.type == "person":
        if best == "same-full":
            shared = shared_attributes(a, b)
            if shared:
                s = shared[0]
                return _verdict("high", "same-name-shared-attribute",
                                f"Same full name, and both are recorded as {s['relationship']} "
                                f"{s['target_name']}.", same_name=True, surface=surface, shared=shared)
            return _verdict("medium", "same-name",
                            "Same full name, and nothing else in common in the documents.",
                            same_name=True, surface=surface)
        if best == "same-partial":
            return _verdict("low", "same-partial-name",
                            f"The same short name, {surface}, is not enough to tell one person from another.",
                            same_name=True, surface=surface)
        return _verdict("low", "partial-name",
                        "One name is an initialled or shortened form of the other.", surface=surface)

    if best == "same":
        if is_distinctive(surface):
            return _verdict("high", "distinctive-name",
                            f"The same name, {surface}, specific enough to identify one {word}.",
                            same_name=True, surface=surface)
        return _verdict("medium", "generic-name",
                        f"The same name, {surface}, but too general to identify one {word} on its own.",
                        same_name=True, surface=surface)
    if best == "same-tokens":
        return _verdict("medium", "reordered-name", "The same words in a different order.",
                        same_name=True, surface=surface)
    return _verdict("medium", "similar-name", "Similar names.", surface=surface)


# ── reading the evidence from the vault ───────────────────────────────────────────────────────

class Evidence:
    """Builds `Profile`s from the staged and committed extractions (`.watchdog/extracted/`), the
    registry, and the merge log's id history, reading each extraction at most once.

    `staged` maps a sha to an extraction already in memory (the batch being folded), which wins
    over the file on disk so edits the fold has made are seen."""

    def __init__(self, vault: Path | None, registry: dict | None = None,
                 staged: dict[str, dict] | None = None):
        self.vault = Path(vault) if vault is not None else None
        self.registry = registry if registry is not None else {}
        self.staged = staged if staged is not None else {}
        self._cache: dict[str, dict | None] = {}
        self._former: dict[str, set[str]] | None = None
        self._dismissed: frozenset[str] | None = None

    def artifact(self, sha: str) -> dict | None:
        if sha in self.staged:
            return self.staged[sha]
        if sha not in self._cache:
            data = None
            if self.vault is not None:
                try:
                    data = json.loads((self.vault / ".watchdog" / "extracted" / f"{sha}.json")
                                      .read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    data = None
            if len(self._cache) >= 512:     # bounded: a hub entity can span thousands of documents
                self._cache.clear()
            self._cache[sha] = data if isinstance(data, dict) else None
        return self._cache[sha]

    def former_ids(self, eid: str) -> set[str]:
        """`eid` and every id merged into it, so facts tagged under an old id still count."""
        if self._former is None:
            from watchdog.pipeline import merge_log
            self._former = merge_log.former_ids(self.vault) if self.vault is not None else {}
        return {eid} | self._former.get(eid, set())

    def dismissed(self) -> frozenset[str]:
        """Pairs the reporter marked "Not the same"."""
        if self._dismissed is None:
            if self.vault is None:
                self._dismissed = frozenset()
            else:
                from watchdog.pipeline import resolutions
                self._dismissed = frozenset(r for r in resolutions.resolved_ids(self.vault)
                                            if r.startswith("same:"))
        return self._dismissed

    def profile(self, eid: str, name: str, etype: str, shas, *, entry: dict | None = None,
                aliases=(), ids: set[str] | None = None) -> Profile:
        """The record `eid` as told by documents `shas` (and the registry `entry`, when given).
        `ids` are the tags that mean this record in those documents (default: `eid` and every id
        merged into it)."""
        from watchdog.pipeline.verification import fact_ids
        p = Profile(eid, name, etype)
        for alias in aliases:
            p.add_surface(alias)
        ids = set(ids) if ids else self.former_ids(eid)
        own_facts: list[dict] = []
        if entry:
            for alias in entry.get("aliases") or []:
                p.add_surface(alias)
            for role in entry.get("roles") or []:
                p.add_relation(role.get("target_id"), role.get("relationship", ""),
                               role.get("target_name", ""), role.get("target_type", ""))
        for sha in shas:
            art = self.artifact(sha)
            if not art:
                continue
            ents = art.get("entities") or []
            by_id = {e.get("id"): e for e in ents}
            hit = False
            for e in ents:
                if e.get("id") in ids:
                    hit = True
                    p.add_surface(e.get("name", ""))
                    for alias in e.get("aliases") or []:
                        p.add_surface(alias)
                    for role in e.get("roles") or []:
                        t = by_id.get(role.get("target_id")) or {}
                        p.add_relation(role.get("target_id"), role.get("relationship", ""),
                                       t.get("name") or role.get("target_name", ""),
                                       t.get("type") or role.get("target_type", ""))
                else:
                    for role in e.get("roles") or []:
                        if role.get("target_id") in ids:
                            p.add_relation(e.get("id"), role.get("relationship", ""),
                                           e.get("name", ""), e.get("type", ""))
            doc = art.get("document") or {}
            facts = doc.get("key_facts") or []
            fids = fact_ids(sha, facts)
            for fid, fact in zip(fids, facts):
                if set(fact.get("entities") or []) & ids:
                    hit = True
                    own_facts.append(fact)
                    p.facts.append({"id": fid, "fact": fact.get("fact") or "", "page": fact.get("page"),
                                    "sha": sha, "title": doc.get("title") or doc.get("filename") or sha[:12],
                                    "date": doc.get("date_of_document")})
            if hit and sha not in p.documents:
                p.documents.append(sha)
        p.identifiers = harvest_identifiers(etype, p.surfaces, own_facts)
        return p

    def registry_profile(self, eid: str, entry: dict, exclude: set[str] = frozenset()) -> Profile:
        shas = [s for s in entry.get("appears_in") or [] if s not in exclude]
        return self.profile(eid, entry.get("name", ""), entry.get("type", ""), shas, entry=entry)


def evidence_record(verdict: dict, merged: Profile, sha: str | None = None) -> dict:
    """The evidence a log entry keeps for one occurrence: the matching identifier or shared
    attribute, and the merged record's facts (ids, D271) in the document(s) concerned."""
    facts = [f for f in merged.facts if sha is None or f["sha"] == sha][:5]
    ev = verdict.get("evidence") or {}
    return {"sha": sha, "documents": [sha] if sha else list(merged.documents),
            "facts": [f["id"] for f in facts],
            "identifier": ev.get("identifier"), "shared": ev.get("shared") or []}
