"""`watchdog reindex` — rebuild `.embeddings/` and `.fulltext/` from on-disk vault state,
with zero model calls (#218, #109).

Possible because embedding now runs at `write_vault` time, not chew time (D43) — an
already-ingested vault's title/type/entities live in `documents.json`/`entities.json` and the
full per-page text lives in the morgue `<stem>.md` files (D26), so a full re-embed needs no OCR
re-run and no model tokens, just local `embed.py` calls. This also unlocks retroactively
upgrading an older vault to the hybrid (BM25 + rerank) corpus path (D43) without re-ingesting.
The same on-disk state rebuilds the full-text (exact-term) index (`fulltext.py`) alongside it.
"""

import json
import re
import shutil
import sys
from pathlib import Path

from watchdog.cmd.base import _BOLD, _DIM, _GREEN, _RESET, _YELLOW, _resolve_vault
from watchdog.pipeline.json_io import _read_json_or

_PAGE_MARKER = re.compile(r"<!-- PAGE (\d+) -->\n\n")


def _pages_from_morgue_text(text: str) -> list[dict]:
    """Reconstruct `[{"page": N, "markdown": ...}]` from a morgue `<stem>.md` file's
    `<!-- PAGE N -->` markers — the exact join format `_write_morgue_markdown` writes."""
    matches = list(_PAGE_MARKER.finditer(text))
    pages = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        pages.append({"page": int(m.group(1)), "markdown": text[start:end].rstrip()})
    return pages


def _doc_context(sha: str, doc: dict, entities_reg: dict) -> str:
    """Rebuild the same contextual prefix `write_vault._index_corpus_passages` builds at
    ingest time (D43) — title, type, and the entities the document mentions — from the
    registry alone, via each entity's `appears_in`."""
    names = [e["name"] for e in entities_reg.values()
            if sha in (e.get("appears_in") or []) and e.get("name")][:20]
    title = doc.get("title") or doc.get("filename", "")
    dtype = doc.get("document_type") or "document"
    context = f"{title} — {dtype}."
    if names:
        context += " Mentions: " + ", ".join(names) + "."
    return context


def _note_paths(vault: Path) -> list[Path]:
    paths = list((vault / "documents").glob("*.md")) if (vault / "documents").is_dir() else []
    entities_dir = vault / "entities"
    if entities_dir.is_dir():
        for type_dir in entities_dir.iterdir():
            if type_dir.is_dir():
                paths.extend(type_dir.glob("*.md"))
    return paths


def _title_from_note(text: str) -> str:
    """First `# Heading` in the note, falling back to the frontmatter `name:` line."""
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    for line in text.splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip()
    return ""


def _fixed_note_specs(vault: Path) -> list[tuple[Path, str, str]]:
    """(path, kind, title) for generated notes outside entities/ and documents/ — timeline,
    briefings, hot cache, run log, and the human-authored investigation context (#109: the
    full-text index covers everything a journalist might grep for, not just entity/document
    notes)."""
    specs: list[tuple[Path, str, str]] = []
    if (vault / "timeline.md").exists():
        specs.append((vault / "timeline.md", "timeline", "Timeline"))
    if (vault / "hot.md").exists():
        specs.append((vault / "hot.md", "hot", "Hot cache"))
    if (vault / "log.md").exists():
        specs.append((vault / "log.md", "log", "Run log"))
    if (vault / "context.md").exists():
        specs.append((vault / "context.md", "context", "Context"))
    if (vault / "briefings").is_dir():
        for md_path in sorted((vault / "briefings").glob("*.md")):
            specs.append((md_path, "briefing", f"Briefing {md_path.stem}"))
    return specs


def cmd_reindex(args) -> None:
    _, info, vault = _resolve_vault(getattr(args, "project", None))
    from watchdog.pipeline import embed, fulltext

    reg_dir = vault / ".watchdog" / "registry"
    documents_reg = {}
    documents_path = reg_dir / "documents.json"
    if documents_path.exists():
        documents_reg = _read_json_or(documents_path, {}, catch=(json.JSONDecodeError,))
    if not documents_reg:
        sys.exit("Error: no documents ingested yet — nothing to reindex.")

    entities_reg = {}
    entities_path = reg_dir / "entities.json"
    if entities_path.exists():
        entities_reg = _read_json_or(entities_path, {}, catch=(json.JSONDecodeError,))

    print()
    print(f"  {_BOLD}Reindexing{_RESET} {_DIM}— {info['name']}{_RESET}")
    print(f"  {_DIM}embed_model: {embed._model_name()}{_RESET}")
    print()

    # A full rebuild replaces the index outright, so a note/passage for a document or entity
    # that no longer exists (e.g. after a future merge-entities) doesn't survive alongside it.
    shutil.rmtree(vault / ".embeddings", ignore_errors=True)
    shutil.rmtree(vault / ".fulltext", ignore_errors=True)

    n_docs = n_passages = n_skipped = 0
    for sha, doc in documents_reg.items():
        filename = doc.get("filename") or sha
        morgue_path = doc.get("morgue_path")
        morgue_md = vault / Path(morgue_path).with_suffix(".md") if morgue_path else None
        if not morgue_md or not morgue_md.exists():
            print(f"  {_YELLOW}skip{_RESET}  {filename}  {_DIM}(no morgue text on disk){_RESET}")
            n_skipped += 1
            continue
        pages = _pages_from_morgue_text(morgue_md.read_text(encoding="utf-8"))
        if not pages:
            n_skipped += 1
            continue
        context = _doc_context(sha, doc, entities_reg)
        count = embed.add_document(vault, filename, pages, context=context)
        fulltext.add_document(vault, filename, sha, pages, morgue_path=morgue_path or "")
        n_docs += 1
        n_passages += count
        print(f"  {_GREEN}✓{_RESET}  {filename}  {_DIM}{count} passages{_RESET}")

    n_notes = 0
    for md_path in _note_paths(vault):
        note_path = str(md_path.relative_to(vault).with_suffix(""))
        text = md_path.read_text(encoding="utf-8")
        embed.add_note(vault, note_path, text)
        kind = "document" if note_path.startswith("documents/") else "entity"
        fulltext.add_note(vault, note_path, kind, _title_from_note(text), text)
        n_notes += 1

    for md_path, kind, title in _fixed_note_specs(vault):
        note_path = str(md_path.relative_to(vault).with_suffix(""))
        fulltext.add_note(vault, note_path, kind, title, md_path.read_text(encoding="utf-8"))

    print()
    skipped_note = f"  {_DIM}({n_skipped} skipped — no morgue text){_RESET}" if n_skipped else ""
    print(f"  {_GREEN}Reindexed{_RESET}  {_BOLD}{n_docs}{_RESET} document{'s' if n_docs != 1 else ''} · "
          f"{_BOLD}{n_passages}{_RESET} passage{'s' if n_passages != 1 else ''} · "
          f"{_BOLD}{n_notes}{_RESET} note{'s' if n_notes != 1 else ''}{skipped_note}")
    print()
