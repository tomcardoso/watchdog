"""Clickable links from terminal output into Obsidian.

A link is an OSC 8 terminal hyperlink to an `obsidian://open?path=…` URL, so clicking a title opens
that note or file in Obsidian. Links are emitted only when colour is on — a real terminal — so
piped output and `--json` stay plain text (I9). Terminals without OSC 8 support show the text.
"""

from pathlib import Path
from urllib.parse import quote

from watchdog import terminal


def obsidian_url(vault: Path, rel_path: str) -> str:
    """`obsidian://` URL opening `rel_path` (relative to the vault; a note may omit `.md`)."""
    target = Path(vault) / rel_path
    if not target.suffix:
        target = target.with_suffix(".md")
    return f"obsidian://open?path={quote(str(target.resolve()), safe='')}"


def hyperlink(text: str, url: str | None) -> str:
    """`text` as a terminal hyperlink to `url`, or plain `text` when links are off or no URL."""
    if not url or not terminal._COLOR:
        return text
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def note_link(vault: Path, rel_path: str | None, text: str | None = None) -> str:
    """`text` (default: the path) linked to a vault note or file."""
    label = text if text is not None else (rel_path or "")
    return hyperlink(label, obsidian_url(vault, rel_path) if rel_path else None)
