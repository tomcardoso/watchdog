"""Global record-skill catalog.

Record skills (domain extraction knowledge) live in two **global** places, not per
vault:

  - the package's bundled skills (``watchdog/skills/records/*.md``) — the canonical set;
  - the user's custom dir (``~/.watchdog/skills/records/*.md``) — additions / overrides.

The ingest orchestrator reads skills from here and builds the classification index in
memory; nothing is copied into individual vaults (see ARCHITECTURE D21). A user skill
overrides a package skill of the same name.
"""

import importlib.resources
from pathlib import Path

WATCHDOG_HOME = Path.home() / ".watchdog"
USER_SKILLS_DIR = WATCHDOG_HOME / "skills" / "records"

# Where the canonical skills are browsable. Pinned to a ref by `watchdog show-skills`.
GITHUB_REPO = "tomcardoso/watchdog"
_GITHUB_SKILLS_PATH = "src/watchdog/skills/records"


def github_skills_url(ref: str = "main", name: str | None = None) -> str:
    base = f"https://github.com/{GITHUB_REPO}/tree/{ref}/{_GITHUB_SKILLS_PATH}"
    return f"{base}/{name}.md".replace("/tree/", "/blob/") if name else base


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Parse a leading ``---`` YAML-ish frontmatter block into a flat {key: value} dict
    plus the remaining body. Only simple ``key: value`` lines are read (no nesting)."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text
    meta: dict[str, str] = {}
    for ln in lines[1:end]:
        if ":" in ln:
            k, _, v = ln.partition(":")
            meta[k.strip()] = v.strip().strip('"\'')
    return meta, "\n".join(lines[end + 1:])


def _skill_descriptor(text: str) -> str:
    """A skill's one-line index descriptor.

    Prefers an explicit ``description:`` in YAML frontmatter (so a user can set their own);
    otherwise falls back to the first content sentence after the H1 (the authoring template
    opens with a sentence naming the document types covered).
    """
    meta, body = _split_frontmatter(text)
    if meta.get("description"):
        return meta["description"]
    line = next(
        (s for s in (ln.strip() for ln in body.splitlines()) if s and not s.startswith("#")),
        "",
    )
    for prefix in (
        "This skill is loaded by `/ingest` when the document type is ",
        "Loaded by `/ingest` when the document type is ",
    ):
        line = line.removeprefix(prefix)
    return line.rstrip(".")


def _package_records():
    """Traversable for the package's bundled records dir."""
    return importlib.resources.files("watchdog") / "skills" / "records"


def catalog() -> dict[str, str]:
    """Map skill name (no ``.md``) → file path, from package skills + the user custom dir.

    User skills override package skills of the same name. Internal ``_`` files are excluded.
    """
    out: dict[str, str] = {}
    try:
        for f in _package_records().iterdir():
            if f.name.endswith(".md") and not f.name.startswith("_"):
                out[f.name[:-3]] = str(f)
    except (FileNotFoundError, NotADirectoryError, ModuleNotFoundError, OSError):
        pass
    if USER_SKILLS_DIR.is_dir():
        for f in sorted(USER_SKILLS_DIR.glob("*.md")):
            if not f.name.startswith("_"):
                out[f.stem] = str(f)
    return dict(sorted(out.items()))


def read_skill(name: str) -> str:
    """Full markdown of a skill by name (with or without ``.md``), or '' if unknown."""
    path = catalog().get(name.removesuffix(".md"))
    return Path(path).read_text(encoding="utf-8") if path else ""


def build_index() -> str:
    """The classification index text, generated in memory from the catalog.

    Lists one ``- `name.md` — descriptor`` line per skill; the classifier returns the
    matching ``name.md``.
    """
    lines = [
        "# Record skill index",
        "",
        "One line per domain skill. Match the document to the closest description, then",
        "read that one skill file.",
        "",
    ]
    for name, path in catalog().items():
        try:
            desc = _skill_descriptor(Path(path).read_text(encoding="utf-8"))
        except OSError:
            desc = ""
        lines.append(f"- `{name}.md` — {desc}")
    lines.append("")
    return "\n".join(lines)
