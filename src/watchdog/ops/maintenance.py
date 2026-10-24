"""Maintenance operations (D298): the timeline and the notes rebuilt from stored data, the lead
sweep, the watch-list check, and an investigation's Claude setup refreshed. None calls a model.

The search index (`ops.reindex`), the graph export (`ops.export`) and the entity operations
(`ops.merge`, `ops.entities`) live in their own modules.
"""

from __future__ import annotations

import json
from pathlib import Path

from watchdog.ops import op, require_vault


def _plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


@op("rebuild-timeline")
def rebuild_timeline(rep, vault: Path) -> dict:
    """Write `timeline.md` again from the canonical event files."""
    from watchdog.pipeline import timeline
    require_vault(vault)
    dates, events = timeline.rebuild_timeline(vault)
    return {"dates": dates, "events": events}


@op("rebuild-notes", engine="index")
def rebuild_notes(rep, vault: Path) -> dict:
    """Rewrite every entity and document note from stored data, with no model call (I13, D280).
    The reporter's own Notes sections are kept."""
    from watchdog.pipeline import entity_notes
    require_vault(vault)
    rep.progress("stage", stage="rebuild-notes", done=None, total=None)
    out = entity_notes.rebuild(vault)
    rep.log(f"Rebuilt {out['entities']} entity notes and {out['documents']} document notes from the "
            "stored facts. No AI model was used.")
    return {"entities": out["entities"], "documents": out["documents"]}


@op("lead-sweep")
def lead_sweep(rep, vault: Path) -> dict:
    """The deterministic lead sweep over the whole investigation; Review shows what it finds."""
    from watchdog.pipeline import leads
    require_vault(vault)
    data = leads.scan(vault)
    counts = {k: len(data.get(k) or []) for k in ("unprofiled", "isolated", "contradictions", "inferred")}
    if not leads.total(data):
        rep.log("No leads: every named entity is profiled, nothing is isolated and no "
                "contradiction is open.")
    else:
        rep.log("Lead sweep: " + ", ".join([
            _plural(counts["unprofiled"], "entity named but never profiled", "entities named but never profiled"),
            _plural(counts["isolated"], "recurring entity with no relationships",
                    "recurring entities with no relationships"),
            _plural(counts["contradictions"], "entity with open contradictions",
                    "entities with open contradictions"),
            _plural(counts["inferred"], "entity with inferred relationships to verify",
                    "entities with inferred relationships to verify"),
        ]) + ". Step through them in Review → Leads.")
    return counts


@op("watchlist-check")
def watchlist_check(rep, vault: Path) -> dict:
    """Check every document in the investigation against the watch list, not only the newest."""
    from watchdog.pipeline import watchlist
    from watchdog.pipeline.json_io import _read_json_or
    require_vault(vault)
    documents = _read_json_or(vault / ".watchdog" / "registry" / "documents.json", {})
    if not documents:
        rep.log("No documents have been added yet, so there is nothing to check.")
        return {"documents": 0, "hits": 0}
    if not watchlist.load_terms(vault):
        rep.log("The watch list is empty, so there is nothing to look for.")
        return {"documents": len(documents), "hits": 0}
    hits = watchlist.scan(vault, [{"sha256": sha, "status": "ok"} for sha in documents])
    alert = watchlist.write_alerts(vault, hits)
    if not alert:
        rep.log(f"Checked {_plural(len(documents), 'document')}: no matches.")
        return {"documents": len(documents), "hits": 0}
    relpath, n_terms, n_docs = alert
    rep.log(f"{_plural(len(hits), 'match', 'matches')} ({_plural(n_terms, 'term')}, "
            f"{_plural(n_docs, 'document')}), listed in Review → Watch-list hits.")
    return {"documents": len(documents), "hits": len(hits), "file": relpath}


@op("refresh-claude-setup", kind="action")
def refresh_claude_setup(rep, vault: Path) -> dict:
    """Bring the investigation's Claude setup up to this version of Watchdog: the shortcut
    commands, `.claude/settings.json` (written whole from the current rules) and Watchdog's
    section of `.claude/CLAUDE.md`. The reporter's notes below the end marker are kept."""
    from watchdog import projects
    from watchdog.vault import claude_settings, install_commands, write_claude_md
    require_vault(vault)
    claude = vault / ".claude"
    install_commands(claude / "commands")
    (claude / "settings.json").write_text(json.dumps(claude_settings(), indent=2) + "\n")
    write_claude_md(vault, projects.name_for(vault))
    rep.log("Updated the shortcuts, Claude's instructions and Claude's settings for this investigation.")
    return {"updated": True}
