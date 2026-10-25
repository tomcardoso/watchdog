"""Where to start in Review, what a batch added, and what is new since Review was last opened (D327).

Everything here is derived from the registry and the app's own files, with no model call (I1).

* **Start here** (`rank`, `start_here`): a short ranked slice of the items that need the reporter's
  judgement (contradictions, leads, watch-list alerts), each with one line on why it is there.
  `rank` is a pure function of the items and a few registry-derived inputs; `start_here` gathers
  those inputs from a vault. Signals, highest first:

    1. the item is tied to a watch-list term (an alert always is; another item when its entity's
       name or an alias matches a term); a term's alerts are one item (D328);
    2. it involves one of the most connected entities (degree in the relationship graph) or one
       that is the main subject (`morgue_entity_id`) of several documents;
    3. it is a contradiction between documents, rather than within one;
    4. it is a lead named in several documents, rather than in one.

  A removed entity (D326) never appears, as an item's own entity or as the reason for another.

* **Judgement and tidy-up** (`groups`): contradictions, leads, alerts and disputed facts need the
  reporter's judgement; possible duplicate documents, possible same entities and document requests
  are tidy-up, worked through in bulk (D328). The Overview's headline number counts judgement only.

* **What a batch added** (`record_batch`, `batch_delta`): after a run writes its briefing, the
  open Review items are recorded in the briefing's sidecar (`.watchdog/briefings/<ts>.json`,
  under `review`), with the ones that were not open after the previous briefing as `added`.

* **New since Review was last opened** (`mark_opened`, `last_opened`, `new_since`): the time the
  reporter last opened Review, per investigation, in `.watchdog/review-seen.json`. It is the app's
  own state: no model input or registry reader looks at it. The Overview shows one of the two,
  never both (`summary_line`, D328).
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

from watchdog.pipeline.json_io import _read_json_or

JUDGEMENT = ("contradictions", "leads", "alerts")
TIDY_UP = ("duplicates", "merges")
REVIEW_KINDS = JUDGEMENT + TIDY_UP

SLICE = 7           # items in Start here: enough to fill a short card, few enough to read first
CENTRAL_TOP = 10    # how many of the most connected entities, and of the main subjects, count
_SUBJECT_MIN = 2    # documents an entity must be the main subject of to count as a subject
_KIND_SHARE = 0.5   # at most this share of the slice from one kind, while others are waiting

SEEN_FILE = "review-seen.json"
_SEEN_VERSION = 1


# ── Start here: the ranking ────────────────────────────────────────────────────────────────────

def central_entities(degree: dict[str, int], subjects: dict[str, int],
                     hidden: frozenset[str] | set[str] = frozenset()) -> tuple[set[str], set[str]]:
    """The most connected entities (top `CENTRAL_TOP` by degree, at least two neighbours) and
    the main subjects (top `CENTRAL_TOP` by documents, at least `_SUBJECT_MIN`), neither
    including a removed entity. Ties are broken by id, so the sets never depend on input order."""
    def top(counts: dict[str, int], floor: int) -> set[str]:
        ranked = sorted(((n, e) for e, n in counts.items() if n >= floor and e not in hidden),
                        key=lambda t: (-t[0], t[1]))
        return {e for _, e in ranked[:CENTRAL_TOP]}
    return top(degree, 2), top(subjects, _SUBJECT_MIN)


def _watch_term(item: dict, ids: list[str], names: dict[str, list[str]], terms: list[dict]) -> str | None:
    if item.get("kind") == "alerts" and item.get("term"):
        return item["term"]
    for eid in ids:
        for name in names.get(eid) or []:
            for t in terms:
                if t["regex"].search(name):
                    return t["term"]
    return None


def _display(eid: str, names: dict[str, list[str]]) -> str:
    return (names.get(eid) or [eid])[0]


def group_alerts(items: list[dict], hidden: frozenset[str] | set[str] = frozenset()) -> list[dict]:
    """Pure: `items` with the alerts folded into one item per watch-list term (D328), in the
    place of the term's first alert. Each group is an alert item with `term`, `rids` (every hit,
    one per document), `documents` (their names, sorted), `doc_count`, `related` (the known
    entities the hits matched) and the title "<term>: N new documents"; its `rid` is its first
    hit's. An alert whose entity is removed is dropped first. Other items pass through."""
    out: list[dict] = []
    by_term: dict[str, list[dict]] = {}
    for item in items:
        if item.get("kind") != "alerts" or not item.get("term"):
            out.append(item)
            continue
        if item.get("entity") in hidden:
            continue
        if item["term"] not in by_term:
            by_term[item["term"]] = []
            out.append({"kind": "alerts", "term": item["term"]})   # a placeholder, filled below
        by_term[item["term"]].append(item)
    for slot in out:
        hits = by_term.get(slot.get("term")) if "rid" not in slot else None
        if hits is None:
            continue
        hits = sorted(hits, key=lambda h: ((h.get("document") or h.get("title") or "").casefold(),
                                           h.get("rid") or ""))
        n = len(hits)
        related = sorted({h["entity"] for h in hits if h.get("entity")})
        slot.update(rid=hits[0]["rid"], rids=[h["rid"] for h in hits],
                    documents=[h.get("document") or h.get("title") or "" for h in hits],
                    title=f"{slot['term']}: {n} new document{'s' if n != 1 else ''}",
                    note=hits[0].get("note") if n == 1 else None, entity=None, related=related,
                    doc_count=n)
    return out


def _signals(item: dict, *, terms, names, degree, central, subjects_set, subjects, hidden) -> dict:
    ids = [e for e in [item.get("entity"), *(item.get("related") or [])] if e and e not in hidden]
    kind = item.get("kind")
    docs = int(item.get("doc_count") or 0)
    term = _watch_term(item, ids, names, terms)
    hub = next((e for e in ids if e in central), None)
    subject = next((e for e in ids if e in subjects_set), None) if hub is None else None
    reasons: list[str] = []
    if term:   # an alert's title already names its term
        reasons.append("On your watch list" if kind == "alerts" else f"On your watch list: “{term}”")
    if hub:
        reasons.append(f"Involves {_display(hub, names)}, one of the most connected entities")
    elif subject:
        reasons.append(f"Involves {_display(subject, names)}, the main subject of "
                       f"{subjects[subject]} documents")
    cross = kind == "contradictions" and docs >= 2
    multi = kind == "leads" and docs >= 2
    if kind == "contradictions":
        reasons.append(f"{docs} documents disagree" if cross
                       else "Two passages of one document disagree" if docs == 1
                       else "Two claims disagree")
    elif kind == "leads":
        reasons.append(f"Named in {docs} documents" if multi
                       else "Named in one document" if docs == 1 else "Not yet followed")
    return {
        "key": (bool(term), bool(hub or subject), cross, multi,
                max((degree.get(e, 0) for e in ids), default=0), docs),
        "reason": " · ".join(reasons[:2]),
    }


def rank(items: list[dict], *, terms: list[dict] = (), names: dict[str, list[str]] | None = None,
         degree: dict[str, int] | None = None, subjects: dict[str, int] | None = None,
         hidden: frozenset[str] | set[str] = frozenset(), limit: int = SLICE) -> list[dict]:
    """Pure: the Start here slice of `items` (Review items of the judgement kinds), best first.

    Each item may carry `entity` (its own entity's id), `related` (other entity ids it involves),
    `doc_count` (documents it is named or cited in) and, for an alert, `term`. `terms` are the
    watch list's `{"term", "regex"}` matchers, `names` maps an entity id to its name and aliases,
    `degree` to its number of neighbours in the relationship graph, and `subjects` to the number of
    documents it is the main subject of.

    Items are ordered by the signals in the module docstring, then by the highest degree among
    their entities and their document count; remaining ties go by kind, title and id, so the
    same registry always gives the same slice whatever order the items came in. An item whose own
    entity is removed (`hidden`) is left out, and a removed entity is never a reason. The alerts
    are first folded into one item per watch-list term (`group_alerts`), so a term takes one place
    however many documents it was found in, and no kind takes more than half the slice while items
    of another kind are waiting. Each result is `{rid, rids, kind, title, note, reason}`, and an
    alert also `term` and `documents`."""
    names, degree, subjects = names or {}, degree or {}, subjects or {}
    central, subjects_set = central_entities(degree, subjects, hidden)
    order = {k: i for i, k in enumerate(JUDGEMENT)}
    scored = []
    for item in group_alerts(items, hidden):
        if item.get("kind") not in order or item.get("entity") in hidden:
            continue
        s = _signals(item, terms=list(terms), names=names, degree=degree, central=central,
                     subjects_set=subjects_set, subjects=subjects, hidden=hidden)
        scored.append((s, item))
    scored.sort(key=lambda t: (tuple(-int(v) for v in t[0]["key"]), order[t[1]["kind"]],
                               (t[1].get("title") or "").casefold(), t[1].get("rid") or ""))
    for n, (s, _item) in enumerate(scored):
        s["pos"] = n

    cap = max(1, int(limit * _KIND_SHARE + 0.5))
    picked: list[tuple[dict, dict]] = []
    per_kind: dict[str, int] = {}
    deferred: list[tuple[dict, dict]] = []
    for s, item in scored:
        if len(picked) >= limit:
            break
        if per_kind.get(item["kind"], 0) >= cap:
            deferred.append((s, item))
            continue
        picked.append((s, item))
        per_kind[item["kind"]] = per_kind.get(item["kind"], 0) + 1
    # Nothing else waiting: fill the slice from what the share kept back, still in rank order.
    for s, item in deferred:
        if len(picked) >= limit:
            break
        picked.append((s, item))
    picked.sort(key=lambda t: t[0]["pos"])
    out = []
    for s, i in picked:
        row = {"rid": i["rid"], "rids": list(i.get("rids") or [i["rid"]]), "kind": i["kind"],
               "title": i.get("title") or "", "note": i.get("note"), "reason": s["reason"]}
        if i["kind"] == "alerts":
            row.update(term=i.get("term"), documents=list(i.get("documents") or []))
        out.append(row)
    return out


# ── Start here: reading the inputs from a vault ────────────────────────────────────────────────

def graph_inputs(entities: dict, documents: dict, hidden: frozenset[str] | set[str]) -> tuple[dict, dict, dict]:
    """Pure: `(names, degree, subjects)` for `rank` from the entity and document registries.

    A relationship is stored on both of its ends, so an entity's distinct targets are its
    neighbours; one to a removed entity does not count. A document's main subject is the first
    folder of its `morgue_path` (`morgue/<morgue_entity_id>/…`)."""
    names: dict[str, list[str]] = {}
    degree: dict[str, int] = {}
    for eid, ent in entities.items():
        if not isinstance(ent, dict):
            continue
        names[eid] = [n for n in [ent.get("name") or eid, *(ent.get("aliases") or [])]
                      if isinstance(n, str) and n.strip()]
        if eid in hidden:
            continue
        targets = {r.get("target_id") for r in ent.get("roles") or [] if isinstance(r, dict)}
        degree[eid] = len({t for t in targets if t and t != eid and t not in hidden})
    subjects: dict[str, int] = {}
    for doc in documents.values():
        parts = str((doc or {}).get("morgue_path") or "").split("/") if isinstance(doc, dict) else []
        if len(parts) > 2 and parts[0] == "morgue" and parts[1] in entities:
            subjects[parts[1]] = subjects.get(parts[1], 0) + 1
    return names, degree, subjects


def start_here(vault: Path, items: list[dict] | None = None, limit: int = SLICE) -> list[dict]:
    """The Start here slice for a vault, from `review.open_items` (or the `items` given)."""
    from watchdog.pipeline import hidden as _hidden
    from watchdog.pipeline import watchlist
    from watchdog.review import open_items

    vault = Path(vault)
    if items is None:
        items = open_items(vault, JUDGEMENT)
    reg = vault / ".watchdog" / "registry"
    removed = _hidden.hidden_ids(vault)
    names, degree, subjects = graph_inputs(_read_json_or(reg / "entities.json", {}),
                                           _read_json_or(reg / "documents.json", {}), removed)
    return rank(items, terms=watchlist.load_terms(vault), names=names, degree=degree,
                subjects=subjects, hidden=removed, limit=limit)


# ── Judgement and tidy-up ──────────────────────────────────────────────────────────────────────

def groups(counts: dict) -> dict:
    """Pure: the Overview's two totals from its counts (the `overview.summary` keys plus
    `disputed`). Judgement: contradictions, leads, watch-list alerts, disputed facts. Tidy-up:
    possible duplicate documents, possible same entities and open document requests (D328)."""
    n = lambda k: int(counts.get(k) or 0)   # noqa: E731
    return {"judgement": n("contradictions") + n("leads") + n("alerts") + n("disputed"),
            "tidy_up": n("near_duplicates") + n("possible_same") + n("requests")}


# ── What a batch added ─────────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _sidecar(vault: Path, briefing: str) -> Path:
    return Path(vault) / ".watchdog" / "briefings" / f"{Path(briefing).stem}.json"


def ids_by_kind(items: list[dict]) -> dict[str, list[str]]:
    """Pure: the ids of Review items, by kind."""
    out: dict[str, set[str]] = {k: set() for k in REVIEW_KINDS}
    for item in items:
        if item.get("kind") in out:
            out[item["kind"]].add(item["rid"])
    return {k: sorted(v) for k, v in out.items()}


def open_ids(vault: Path) -> dict[str, list[str]]:
    """Every open Review item's id, by kind."""
    from watchdog.review import open_items
    return ids_by_kind(open_items(Path(vault), REVIEW_KINDS))


def added_between(before: dict | None, after: dict) -> dict[str, list[str]] | None:
    """Pure: the ids open in `after` that were not open in `before`, by kind; None when there is
    no `before` to compare with (a briefing written before this was recorded)."""
    if before is None:
        return None
    return {k: sorted(set(after.get(k) or []) - set(before.get(k) or [])) for k in REVIEW_KINDS}


def _review_record(vault: Path, briefing: Path) -> dict | None:
    try:
        data = json.loads(_sidecar(vault, briefing.name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rec = data.get("review") if isinstance(data, dict) else None
    return rec if isinstance(rec, dict) and isinstance(rec.get("open"), dict) else None


def record_batch(vault: Path, briefing: str) -> dict | None:
    """After a run's briefing and its watch-list and lead passes: record the open Review items in
    the briefing's sidecar, and which of them the previous briefing's record did not hold.

    The first briefing of an investigation counts everything as added. One whose previous
    briefing has no record (written before D327) records `added` as None, which shows nothing."""
    from watchdog.overview import run_briefings
    from watchdog.pipeline.write_vault import _write_json_atomic

    vault = Path(vault)
    side = _sidecar(vault, briefing)
    try:
        data = json.loads(side.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    stem = Path(briefing).stem
    ordered = run_briefings(vault)   # newest first
    at = next((i for i, b in enumerate(ordered) if b.stem == stem), None)
    earlier = ordered[at + 1:] if at is not None else [b for b in ordered if b.stem != stem]
    after = open_ids(vault)
    if not earlier:
        before = {}                  # the investigation's first briefing: everything is new
    else:
        prev = _review_record(vault, earlier[0])
        before = prev["open"] if prev else None
    rec = {"at": _now(), "open": after, "added": added_between(before, after)}
    data["review"] = rec
    _write_json_atomic(side, data)
    return rec


def batch_delta(vault: Path, briefing: Path | None) -> dict | None:
    """What the given (latest) briefing's batch added, as counts by kind, or None when it has no
    record."""
    if briefing is None:
        return None
    rec = _review_record(Path(vault), briefing)
    if not rec or not isinstance(rec.get("added"), dict):
        return None
    return {"briefing": briefing.stem, "at": rec.get("at"),
            "added": {k: len(rec["added"].get(k) or []) for k in REVIEW_KINDS}}


def summary_line(batch: dict | None, since_review: dict | None) -> dict | None:
    """Pure: the Overview's one line on what is new (D328). Once Review has been opened
    (`since_review` is not None), what is still open and new since then; before the first visit,
    what the latest batch added. Never both. None when the chosen one has nothing to say: a visit
    after which nothing new is open shows no line, rather than falling back to the batch."""
    if since_review is not None:
        added = since_review.get("added") or {}
        if not any(added.values()):
            return None
        return {"kind": "since", "at": since_review.get("since"), "added": added}
    added = (batch or {}).get("added") or {}
    if not any(added.values()):
        return None
    return {"kind": "batch", "at": batch.get("at"), "added": added}


# ── New since Review was last opened ───────────────────────────────────────────────────────────

def _seen_path(vault: Path) -> Path:
    return Path(vault) / ".watchdog" / SEEN_FILE


def last_opened(vault: Path) -> str | None:
    data = _read_json_or(_seen_path(vault), {})
    at = data.get("opened_at") if isinstance(data, dict) else None
    return at if isinstance(at, str) and at else None


def mark_opened(vault: Path, at: str | None = None) -> dict:
    """Record that the reporter opened Review now. Returns `{opened_at, previous}`."""
    previous = last_opened(vault)
    at = at or _now()
    p = _seen_path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"schema_version": _SEEN_VERSION, "opened_at": at}, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)
    return {"opened_at": at, "previous": previous}


def _parse(at: str | None) -> datetime.datetime | None:
    if not at:
        return None
    try:
        t = datetime.datetime.fromisoformat(at)
    except ValueError:
        return None
    return t if t.tzinfo else t.astimezone()


def added_since(vault: Path, since: str | None) -> set[str] | None:
    """The ids the batches recorded after `since` added, or None when `since` is unset."""
    from watchdog.overview import run_briefings
    t = _parse(since)
    if t is None:
        return None
    out: set[str] = set()
    for b in run_briefings(vault):
        rec = _review_record(Path(vault), b)
        when = _parse(rec.get("at")) if rec else None
        if when is None or when <= t:
            continue
        for ids in (rec.get("added") or {}).values():
            out.update(ids or [])
    return out


def new_since(vault: Path, since: str | None, open_now: dict[str, list[str]] | None = None) -> dict | None:
    """Counts by kind of the items open now that a batch added after `since`; None when Review has
    never been opened."""
    added = added_since(vault, since)
    if added is None:
        return None
    open_now = open_now if open_now is not None else open_ids(vault)
    return {"since": since, "added": {k: len(set(open_now.get(k) or []) & added) for k in REVIEW_KINDS}}

