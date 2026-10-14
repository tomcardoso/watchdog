"""`sample.*` — the sample investigation (D303): where it is, and what the guided tour points at.

`sample.status` says whether the sample is listed and where it is (or would be built); building
it is the `sample-create` operation. `sample.tour` reads the sample's own registries and returns
the real findings each tour step opens — the scanned document, an entity with checked facts, a
contradiction, the near-duplicate pair, a lead — so the tour never names something the sample
does not hold. `tour_targets` is the same lookup for the test that guards the tour.
"""

from __future__ import annotations

from pathlib import Path

from watchdog.gui import vaultio
from watchdog.gui.rpc import RpcError, method
from watchdog.gui.vaultio import require_vault


@method("sample.status")
def status(dir: str | None = None) -> dict:
    from watchdog.ops import sample
    return sample.status(dir)


@method("sample.tour")
def tour(vault: str) -> dict:
    v = require_vault(vault)
    targets = tour_targets(v)
    missing = [k for k, val in targets.items() if val is None]
    if missing:
        raise RpcError("This investigation does not hold what the tour shows: "
                       + ", ".join(missing) + ". Rebuild the sample and try again.", code="not_found")
    return targets


def _scanned(v: Path, docs: dict) -> dict | None:
    """A PDF with a scanned page whose text was recognized: its sha, the page and a phrase on it."""
    from watchdog.pipeline import text_positions
    for sha, rec in sorted(docs.items(), key=lambda kv: kv[1].get("filename") or ""):
        if not str(rec.get("filename", "")).lower().endswith(".pdf"):
            continue
        pages = text_positions.available_pages(v, sha)
        if not pages:
            continue
        page = min(int(p) for p in pages)
        data = text_positions.read(v, sha, [page])
        lines = (data.get("boxes") or {}).get(str(page), {}).get("lines") or []
        words = [ln[4] for ln in lines if isinstance(ln, list) and len(ln) > 4 and isinstance(ln[4], str)]
        phrase = next((w for w in words if "appraisal" in w.lower()), None) or next(
            (w for w in words if len(w.split()) >= 2), None)
        if phrase:
            return {"sha": sha, "filename": rec.get("filename"), "title": rec.get("title"),
                    "page": page, "find": _find_word(phrase)}
    return None


def _find_word(phrase: str) -> str:
    """One distinctive word from a line of the scanned page, to type into Find."""
    words = [w.strip(".,:;()") for w in phrase.split()]
    words = [w for w in words if len(w) >= 6 and w.isalpha()]
    return (words[0] if words else phrase.split()[0]).lower()


def _entity(v: Path, ents: dict) -> dict | None:
    """An entity with at least one fact the reporter has verified, preferring one with a
    contradiction and more facts, so the page shows facts, passages, checks and a conflict."""
    from watchdog.gui.api import vault as vault_api
    best = None
    for eid, ent in ents.items():
        if not isinstance(ent, dict):
            continue
        try:
            page = vault_api.entity(str(v), eid)
        except RpcError:
            continue
        facts = page.get("facts") or []
        verified = [f for f in facts if (f.get("mark") or {}).get("status") == "verified"]
        if not verified:
            continue
        with_passage = [f for f in verified if f.get("passage")]
        score = (bool(page.get("contradiction_count")), bool(with_passage), len(facts))
        if best is None or score > best[0]:
            best = (score, {"id": eid, "name": page.get("name"), "fact": (with_passage or verified)[0]["id"]})
    return best[1] if best else None


def _review(v: Path) -> tuple[dict | None, dict | None, dict | None]:
    """An open contradiction, the possible-duplicate pair and a lead, from the Review queues."""
    from watchdog.gui.api import review as review_api
    items = review_api.items(str(v)).get("items") or []

    def first(kind: str, prefer: str = "") -> dict | None:
        found = [i for i in items if i.get("kind") == kind]
        found.sort(key=lambda i: (prefer not in (i.get("rid") or ""), i.get("rid") or ""))
        return {"rid": found[0]["rid"], "title": found[0]["title"]} if found else None

    return first("contradictions"), first("duplicates"), first("leads", "lead:unprofiled:")


def tour_targets(v: Path) -> dict:
    """What each tour step opens in the sample at `v`; a value is None when the sample lacks it."""
    from watchdog.ops import sample
    from watchdog.vault_paths import incoming_dir
    docs, ents = vaultio.load_documents(v), vaultio.load_entities(v)
    contradiction, duplicate, lead = _review(v)
    briefings = sorted(p.name for p in (v / "briefings").glob("20*.md"))
    incoming = sorted(p.name for p in incoming_dir(v).iterdir() if p.is_file() and not p.name.startswith(".")) \
        if incoming_dir(v).is_dir() else []
    usage_dir = v / ".watchdog" / "registry" / "usage"
    runs = sorted(usage_dir.glob("usage-*.json")) if usage_dir.is_dir() else []
    return {
        "sample": True if sample.is_sample_folder(v) else None,
        "briefings": len(briefings) or None,
        "scanned": _scanned(v, docs),
        "entity": _entity(v, ents),
        "contradiction": contradiction,
        "duplicate": duplicate,
        "lead": lead,
        "search": "time-critical" if any("time-critical" in (p.read_text(encoding="utf-8", errors="replace"))
                                         for p in (v / "documents").rglob("*.md")) else None,
        # Empty once the reporter has added the waiting files (D307); the tour says so then.
        "incoming": incoming,
        "runs": len(runs) or None,
    }


# ── prepared Ask Claude conversations (D309) ──────────────────────────────────────────────────

def _sample_vault(vault: str) -> Path:
    from watchdog.ops import sample
    v = require_vault(vault)
    if not sample.is_sample_folder(v):
        raise RpcError("Prepared conversations exist only in the sample investigation.", code="not_sample")
    return v


@method("sample.questions")
def questions(vault: str) -> list[dict]:
    """The questions with a prepared conversation: `[{id, question, blurb, writes}]`."""
    from watchdog.gui import demo_conversations
    _sample_vault(vault)
    return demo_conversations.questions()


def _played(fn, *args) -> dict:
    from watchdog.gui import demo_conversations
    try:
        return fn(*args)
    except KeyError:
        raise RpcError("There is no such prepared conversation.", code="not_found") from None
    except demo_conversations.MissingFact as e:
        raise RpcError("This sample does not hold what the prepared answer cites "
                       f"({e}). Rebuild the sample and try again.", code="not_found") from None


@method("sample.conversation")
def conversation(vault: str, id: str) -> dict:
    """A prepared conversation's events, to play in the chat screen: `{session, events, waiting}`.
    No model is called and nothing is sent anywhere."""
    from watchdog.gui import demo_conversations
    return _played(demo_conversations.play, _sample_vault(vault), id)


@method("sample.conversationAnswer")
def conversation_answer(vault: str, id: str, request_id: str, allow: bool) -> dict:
    """The rest of a prepared conversation after its permission card: `{events}`. Allowed, the
    change is made in the sample through the session tool a live session calls."""
    from watchdog.gui import demo_conversations
    return _played(demo_conversations.answer, _sample_vault(vault), id, request_id, bool(allow))
