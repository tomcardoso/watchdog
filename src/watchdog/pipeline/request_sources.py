"""Where document requests can be got, grouped across wordings (D334), the fourth housekeeping
chore.

Review groups open document requests by the holder their free-text `likely_source` names
(`requests.source_group`, D328), so a reporter can send one letter per registry or office. Code
folds case, punctuation, a leading article and the "how to ask" tail, but not synonyms: a
registry's official name, an English description of it and the reseller of its records arrive as
three groups. This pass adds a mapping on top; it never rewrites a request's own text.

- **When.** After the request dedup in post-processing, and only when an open request's source key
  has never been put to the model (`evaluated`).
- **The model's input** (`items`). One item per source as Review would group it now: each key not
  in a group, and each existing group under its label, the new ones first, with their wordings.
- **The model's answer.** A short label per item; items naming the same holder get the same label.
- **Code applies it** (`validate`, `apply`). Items whose labels match (case and spacing folded) form
  a group, recorded in ``.watchdog/registry/request_sources.json``::

      {"schema_version": 1,
       "groups": [{"id", "keys": [...], "label", "wordings": [...], "decided_by": "model", "model",
                   "at", "run", "status": "active" | "superseded" | "split",
                   "superseded_at", "split_by", "split_at"}],
       "evaluated": [key, ...]}

  A group that would join two keys the reporter split apart is dropped; a new group that takes in
  an existing one supersedes it.
- **Correction.** The reporter can split a group (`split`), as with relationship wordings (D291);
  its keys are never grouped together again.

`mapping` gives Review the active groups by key; Review shows a group's label as the model's.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from watchdog.pipeline import housekeeping
from watchdog.pipeline.json_io import _read_json_or

SCHEMA_VERSION = 1
LOG_FILE = "request_sources.json"
LABEL_MAX = 80
WORDINGS_SHOWN = 3


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def path(vault: Path) -> Path:
    return Path(vault) / ".watchdog" / "registry" / LOG_FILE


def _empty() -> dict:
    return {"schema_version": SCHEMA_VERSION, "groups": [], "evaluated": []}


def load(vault: Path | None) -> dict:
    """The log, or an empty one when the file is missing or unreadable."""
    if vault is None:
        return _empty()
    data = _read_json_or(path(vault), None)
    if not isinstance(data, dict):
        return _empty()
    data.setdefault("schema_version", SCHEMA_VERSION)
    if not isinstance(data.get("groups"), list):
        data["groups"] = []
    if not isinstance(data.get("evaluated"), list):
        data["evaluated"] = []
    return data


def too_new(data: dict) -> bool:
    v = data.get("schema_version")
    return isinstance(v, int) and v > SCHEMA_VERSION


def _save(vault: Path, data: dict) -> None:
    from watchdog.pipeline.write_vault import _write_json_atomic
    p = path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(p, data)


def group_id(keys) -> str:
    return "src:" + hashlib.sha1("|".join(sorted(keys)).encode("utf-8")).hexdigest()[:12]


def _active(data: dict) -> list[dict]:
    return [g for g in data.get("groups") or []
            if isinstance(g, dict) and g.get("status", "active") == "active" and g.get("keys")]


def mapping(data: dict) -> dict[str, dict]:
    """Source key → the active group holding it (the most recent when two do)."""
    out: dict[str, dict] = {}
    for g in sorted(_active(data), key=lambda g: g.get("at") or "", reverse=True):
        for k in g["keys"]:
            out.setdefault(k, g)
    return out


def split_sets(data: dict) -> list[set[str]]:
    """The key sets the reporter split apart."""
    return [set(g.get("keys") or []) for g in data.get("groups") or []
            if isinstance(g, dict) and g.get("status") == "split"]


# ── what the model is asked ─────────────────────────────────────────────────────────────

def _sources(open_: list[dict]) -> dict[str, dict[str, int]]:
    """Source key → each wording of it (as `source_group` labels it) → how many requests."""
    from watchdog.pipeline.requests import NO_SOURCE, source_group
    out: dict[str, dict[str, int]] = {}
    for r in open_:
        key, label = source_group(r.get("likely_source"))
        if key == NO_SOURCE:
            continue
        words = out.setdefault(key, {})
        words[label] = words.get(label, 0) + 1
    return out


def _ranked(words: dict[str, int]) -> list[str]:
    return sorted(words, key=lambda w: (-words[w], w.casefold(), w))


def items(open_: list[dict], data: dict) -> list[dict]:
    """The sources to put to the model, or [] when every open request's source key was put to it
    before (or fewer than two sources are open). One item per group Review would show: an active
    group under its label (`group` its id), or one key alone. ``{"keys", "label", "wordings",
    "requests", "new", "group"}``; new ones first."""
    sources = _sources(open_)
    evaluated = set(data.get("evaluated") or [])
    if not set(sources) - evaluated:
        return []
    groups = mapping(data)
    out, done = [], set()
    for key in sorted(sources, key=lambda k: (k in evaluated, k)):
        if key in done:
            continue
        g = groups.get(key)
        if g:
            keys = list(g["keys"])
            words: dict[str, int] = {}
            for k in (k for k in keys if k in sources):
                for w, n in sources[k].items():
                    words[w] = words.get(w, 0) + n
            out.append({"keys": keys, "label": g.get("label") or _ranked(words)[0],
                        "wordings": _ranked(words)[:WORDINGS_SHOWN], "requests": sum(words.values()),
                        "new": any(k in sources and k not in evaluated for k in keys), "group": g["id"]})
            done |= set(keys)
        else:
            words = sources[key]
            out.append({"keys": [key], "label": _ranked(words)[0], "wordings": _ranked(words)[:WORDINGS_SHOWN],
                        "requests": sum(words.values()), "new": key not in evaluated, "group": None})
            done.add(key)
    return out if len(out) >= 2 else []


def build_prompt(batch: list[dict]) -> str:
    import json
    from watchdog.pipeline import prompts
    lines = []
    for n, it in enumerate(batch, 1):
        others = [w for w in it["wordings"] if w != it["label"]]
        also = f"; also written {', '.join(json.dumps(w, ensure_ascii=False) for w in others)}" if others else ""
        n_req = it["requests"]
        lines.append(f"{n}. {json.dumps(it['label'], ensure_ascii=False)} "
                     f"({n_req} request{'s' if n_req != 1 else ''}{also})")
    return prompts.build_request_sources_prompt("\n".join(lines))


_SPACE = re.compile(r"\s+")


def validate(item: dict, answer: dict):
    """Code's check on one answer: the label, tidied, or None to reject. A label is one line of
    plain text of at most `LABEL_MAX` characters."""
    label = answer.get("label")
    if not isinstance(label, str):
        return None
    label = _SPACE.sub(" ", label).strip().strip("\"'")
    if not label or len(label) > LABEL_MAX or any(c in label for c in "[]<>{}|*#`"):
        return None
    return label


SCHEMA = housekeeping.answers_schema({"label": {"type": "string"}}, ["label"])

CHORE = housekeeping.Chore(task="request-sources", schema=SCHEMA, build_prompt=build_prompt,
                           validate=validate, max_items=150)


def _fold(label: str) -> str:
    return _SPACE.sub(" ", label.casefold()).strip()


def apply(vault: Path, batch: list[dict], result: "housekeeping.ChoreResult", *,
          model: str | None = None, run: str | None = None) -> dict:
    """Record the groups the answers make, under the registry lock. Items whose labels fold to the
    same text form one group; a group joining two keys the reporter split is dropped, one that
    takes in an existing group supersedes it. Every key of an item a successful call covered is
    marked evaluated. Returns ``{"grouped": [entries], "dropped": n}``."""
    from watchdog.pipeline.write_vault import _registry_lock
    vault = Path(vault)
    registry = vault / ".watchdog" / "registry"
    if not registry.is_dir():
        return {"grouped": [], "dropped": 0}
    with _registry_lock(registry):
        data = load(vault)
        if too_new(data):
            return {"grouped": [], "dropped": 0, "too_new": True}
        at = _now()
        clusters: dict[str, list[int]] = {}
        for idx, label in result.settled.items():
            clusters.setdefault(_fold(label), []).append(idx)
        splits = split_sets(data)
        grouped, dropped = [], 0
        for members in clusters.values():
            if len(members) < 2:
                continue
            members.sort()
            keys = sorted({k for i in members for k in batch[i]["keys"]})
            if any(len(set(keys) & s) >= 2 for s in splits):
                dropped += 1
                continue
            gid = group_id(keys)
            label = result.settled[members[0]]
            wordings: list[str] = []
            for i in members:
                wordings += [w for w in batch[i]["wordings"] if w not in wordings]
            existing = next((g for g in _active(data) if g.get("id") == gid), None)
            if existing:
                if existing.get("label") != label:
                    existing.update({"label": label, "at": at, "model": model})
                continue
            for g in _active(data):
                if set(g["keys"]) & set(keys):
                    g["status"], g["superseded_at"] = "superseded", at
            entry = {"id": gid, "keys": keys, "label": label, "wordings": wordings,
                     "decided_by": "model", "model": model, "at": at, "run": run, "status": "active"}
            data["groups"].append(entry)
            grouped.append(entry)
        seen = set(data.get("evaluated") or [])
        for idx, it in enumerate(batch):
            if idx not in result.unanswered:
                seen |= set(it["keys"])
        data["evaluated"] = sorted(seen)
        data["schema_version"] = SCHEMA_VERSION
        _save(vault, data)
    return {"grouped": grouped, "dropped": dropped}


def split(vault: Path, gid: str, by: str | None = None) -> dict:
    """Undo one grouping: its sources are shown apart in Review again, and no later run may group
    any two of its keys. Recorded as a version of the vault's history. Returns the log entry.
    Raises LookupError for an unknown or inactive group, ValueError for a newer log."""
    from watchdog.pipeline import history
    from watchdog.pipeline.verification import reporter_name
    from watchdog.pipeline.write_vault import _registry_lock
    vault = Path(vault)
    registry = vault / ".watchdog" / "registry"
    with history.recording(vault, {"kind": "request_source_split", "group": gid},
                           [f".watchdog/registry/{LOG_FILE}"]):
        with _registry_lock(registry):
            data = load(vault)
            if too_new(data):
                raise ValueError("This investigation's request-source log was written by a newer "
                                 "version of Watchdog. Update Watchdog to change it.")
            entry = next((g for g in _active(data) if g.get("id") == gid), None)
            if entry is None:
                raise LookupError(f"No current grouping has the id {gid}.")
            entry.update({"status": "split", "split_by": (by or "").strip() or reporter_name(),
                          "split_at": _now()})
            _save(vault, data)
    return entry


def estimate_items(vault: Path, shas: list[str]) -> list[dict]:
    """The items the pass would send once the staged documents `shas` commit their requests: the
    open requests plus each staged extraction's `document_requests`."""
    from watchdog.pipeline import requests
    open_ = list(requests.open_requests(vault))
    for sha in shas:
        art = _read_json_or(Path(vault) / ".watchdog" / "extracted" / f"{sha}.json", None)
        staged = art.get("document_requests") if isinstance(art, dict) else None
        for r in staged if isinstance(staged, list) else []:
            if isinstance(r, dict):
                open_.append({"likely_source": r.get("likely_source")})
    return items(open_, load(vault))
