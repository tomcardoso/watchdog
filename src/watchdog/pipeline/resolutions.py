"""Resolution / acknowledgment store (#266).

The vault had no memory of what the journalist had already dealt with: `watchdog watchlist`
re-reported every historical hit, the lead sweep re-listed the same items on every ingest, and
contradiction callouts piled up append-only with no way to mark one handled short of editing the
note by hand. This is the one deterministic mechanism serving all three — a small acknowledgment
store the report generators consult, so acknowledged items drop out of the active list and the
reports gain the workable-queue property. Pure I1-side Python: no model calls, no judgement.

Stored at ``.watchdog/Registry/resolutions.json``::

    {"schema_version": 1, "resolved": {"<rid>": {"at": "<iso>", "label": "<human label>"}}}

Resolution ids (``rid``) are stable, human-copyable tokens the reports print next to each item
(embedded as an unobtrusive ``<!--wid:<rid>-->`` marker so a ticked ``- [x]`` checkbox can be
synced back into the store):

  * ``lead:<signal>:<entity_id>`` — ``signal`` ∈ ``unprofiled`` | ``isolated`` | ``inferred``
  * ``contradiction:<hash>``      — ``sha1[:12]`` of the normalized callout text
  * ``alert:<sha7>:<hash>``       — document ``sha256[:7]`` + ``sha1[:8]`` of the watch term
  * ``request:<sha7>:<hash>``     — document ``sha256[:7]`` + ``sha1[:8]`` of the normalized
    ``what`` text (#365)

Three ways to acknowledge, all landing in the same JSON: ``watchdog resolve <id> …``,
``watchdog resolve --sync`` (import ``- [x]`` checkboxes from the briefing files), and
``watchdog unresolve <id> …`` to undo.
"""

import datetime
import hashlib
import json
import re
from pathlib import Path

_SCHEMA_VERSION = 1
_WID_RE = re.compile(r"<!--\s*wid:(\S+?)\s*-->")
_CHECKBOX_RE = re.compile(r"^\s*[-*]\s*\[(?P<mark>[ xX])\]")


def _path(vault: Path) -> Path:
    return vault / ".watchdog" / "Registry" / "resolutions.json"


def _short(text: str, n: int) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]


def _callout_text(callout: str) -> str:
    """Normalize a contradiction callout to stable text for hashing: strip blockquote
    markers and collapse whitespace so cosmetic reflow doesn't change the id."""
    stripped = re.sub(r"(?m)^\s*>\s?", "", callout)
    return re.sub(r"\s+", " ", stripped).strip().lower()


# ── Stable id builders ──────────────────────────────────────────────────────────

def lead_id(signal: str, entity_id: str) -> str:
    return f"lead:{signal}:{entity_id}"


def contradiction_id(callout: str) -> str:
    return f"contradiction:{_short(_callout_text(callout), 12)}"


def alert_id(sha256: str, term: str) -> str:
    return f"alert:{sha256[:7]}:{_short(term, 8)}"


def request_id(sha256: str, what: str) -> str:
    normalized = re.sub(r"\s+", " ", what).strip().lower()
    return f"request:{sha256[:7]}:{_short(normalized, 8)}"


def split_callouts(contradictions: str) -> list[str]:
    """Split an accumulated ``## Contradictions`` body into individual callout blocks
    (blocks are separated by blank lines, the join `dedup_callouts`/note-render uses)."""
    if not contradictions.strip():
        return []
    return [b for b in re.split(r"\n\s*\n", contradictions.strip()) if b.strip()]


def dedup_callouts(callouts: list[str]) -> list[str]:
    """Dedup callout blocks by normalized text (#288), keeping first-seen original wording."""
    seen: set[str] = set()
    result: list[str] = []
    for callout in callouts:
        callout = callout.strip()
        if not callout:
            continue
        key = _callout_text(callout)
        if key not in seen:
            seen.add(key)
            result.append(callout)
    return result


def filter_callouts(callouts: list[str], resolved: frozenset[str]) -> list[str]:
    """Drop resolved callouts from a list (#266 / #288).

    A callout whose ``contradiction_id`` is resolved is removed; the rest keep their order.
    Registry state is untouched — this is a render-time overlay, so unresolving restores it."""
    if not resolved:
        return callouts
    return [c for c in callouts if contradiction_id(c) not in resolved]


# ── Store I/O ───────────────────────────────────────────────────────────────────

def load(vault: Path) -> dict:
    """Load the store, returning a fresh empty structure on missing/corrupt file."""
    try:
        data = json.loads(_path(vault).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": _SCHEMA_VERSION, "resolved": {}}
    if not isinstance(data, dict) or not isinstance(data.get("resolved"), dict):
        return {"schema_version": _SCHEMA_VERSION, "resolved": {}}
    return data


def resolved_ids(vault: Path) -> frozenset[str]:
    """The set of acknowledged rids — the one thing the report generators need."""
    return frozenset(load(vault).get("resolved", {}))


def save(vault: Path, data: dict) -> None:
    path = _path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.rename(path)


def resolve(vault: Path, rids, label: str = "") -> list[str]:
    """Mark rids resolved. Returns the rids newly added (already-resolved ones are skipped)."""
    data = load(vault)
    resolved = data.setdefault("resolved", {})
    now = datetime.datetime.now().isoformat(timespec="seconds")
    added: list[str] = []
    for rid in rids:
        rid = rid.strip()
        if not rid or rid in resolved:
            continue
        resolved[rid] = {"at": now, "label": label}
        added.append(rid)
    if added:
        save(vault, data)
    return added


def unresolve(vault: Path, rids) -> list[str]:
    """Remove rids from the store. Returns the rids actually removed."""
    data = load(vault)
    resolved = data.setdefault("resolved", {})
    removed: list[str] = []
    for rid in rids:
        rid = rid.strip()
        if rid in resolved:
            del resolved[rid]
            removed.append(rid)
    if removed:
        save(vault, data)
    return removed


# ── Merge propagation (#219 / D54) ───────────────────────────────────────────────

def remap_entity(vault: Path, old_id: str, new_id: str) -> int:
    """Follow an entity merge: rewrite ``lead:<signal>:<old_id>`` rids onto ``new_id`` so a
    resolution survives the merge. Contradiction and alert rids are keyed on content, not entity
    id, so they need no remap. Returns the number of rids rewritten."""
    data = load(vault)
    resolved = data.get("resolved", {})
    suffix = f":{old_id}"
    to_move = [rid for rid in resolved if rid.startswith("lead:") and rid.endswith(suffix)]
    if not to_move:
        return 0
    for rid in to_move:
        new_rid = rid[: -len(suffix)] + f":{new_id}"
        resolved.setdefault(new_rid, resolved.pop(rid))
    save(vault, data)
    return len(to_move)


# ── Checkbox sync ────────────────────────────────────────────────────────────────

def sync_from_briefings(vault: Path) -> tuple[list[str], list[str]]:
    """Import ``- [x]`` / ``- [ ]`` checkbox state from the briefing files, plus the vault-root
    ``requests.md``, into the store.

    Every rendered lead/alert/request line carries a ``<!--wid:<rid>-->`` marker. A ticked box
    adds its rid to the store; an un-ticked box for a currently-resolved rid removes it (so the
    journalist can undo a resolution by clearing the checkbox). Returns ``(added, removed)``.
    """
    briefings = vault / "briefings"
    checked: set[str] = set()
    unchecked: set[str] = set()
    for md in sorted(briefings.glob("*.md")) + [vault / "requests.md"]:
        if not md.exists():
            continue
        for line in md.read_text(encoding="utf-8", errors="replace").splitlines():
            box = _CHECKBOX_RE.match(line)
            if not box:
                continue
            wid = _WID_RE.search(line)
            if not wid:
                continue
            (checked if box.group("mark") in "xX" else unchecked).add(wid.group(1))
    added = resolve(vault, sorted(checked), label="checkbox")
    removed = unresolve(vault, sorted(unchecked - checked))
    return added, removed
