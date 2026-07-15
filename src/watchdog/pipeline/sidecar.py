"""Sidecar allowlist and parsing, shared by chew (filters + embeds a document's `.yml` into the
queue JSON) and ingest (reads provenance/skill-pin back out of that embedded copy).

Chew is the only stage that ever reads a sidecar off disk: it parses the raw YAML, keeps only
`ALLOWED_KEYS`, caps each value's length, and re-serializes the result into the queue JSON's
`sidecar` field. Every later stage — classify/extract prompts, provenance stamping, the
per-document skill pin, the morgue copy write_vault re-materializes — reads that already-filtered
text, never the filesystem. This bounds a hand-edited or research-authored sidecar to a known,
reviewed field set before it can reach a model prompt or a permanent vault write (D121)."""

import yaml

ALLOWED_KEYS = {
    "source", "obtained", "notes", "skill",
    "retrieved_by", "source_type", "title", "relevance", "archived", "capture",
}
MAX_VALUE_LEN = 2000


def parse(raw_text: str | None) -> dict:
    """Parse raw sidecar YAML into a dict — {} if absent, malformed, or not a mapping."""
    if not raw_text:
        return {}
    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def filter_and_render(raw_text: str | None) -> tuple[str | None, list[str]]:
    """Keep only `ALLOWED_KEYS`, cap each value's length, and re-serialize to YAML text.
    Returns (clean_text_or_none, dropped_key_names) — the caller warns on a non-empty
    `dropped_key_names` so a field silently excluded from model context isn't a silent loss."""
    data = parse(raw_text)
    if not data:
        return None, []
    dropped = sorted(k for k in data if k not in ALLOWED_KEYS)
    kept = {k: str(v)[:MAX_VALUE_LEN] for k, v in data.items()
            if k in ALLOWED_KEYS and v is not None}
    if not kept:
        return None, dropped
    return yaml.safe_dump(kept, sort_keys=False, allow_unicode=True), dropped


def provenance(sidecar_text: str | None) -> dict:
    """`source`/`obtained` from an already-filtered sidecar. str() coerces YAML's auto-parsed
    scalars (e.g. `obtained: 2026-06-05` → a date) back to text."""
    data = parse(sidecar_text)
    return {k: str(data[k]) for k in ("source", "obtained") if data.get(k) is not None}


def skill_pin(sidecar_text: str | None) -> str | None:
    """The raw `skill:` value from an already-filtered sidecar, or None."""
    data = parse(sidecar_text)
    value = data.get("skill")
    return str(value) if value else None
