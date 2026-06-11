# /watchdog-health — Check vault integrity

Audit the vault for structural problems: orphaned notes, broken links, registry mismatches, and missing required fields.

---

## 1. Registry vs. notes consistency

### Documents

Read `.watchdog/Registry/documents.json`. For each entry:
- Check that `documents/<document_note>.md` exists
- If the file is missing, report: `MISSING NOTE: documents/<slug>.md (registered as <sha256>)`

List all files in `documents/`. For each:
- Check that it has a corresponding entry in `.watchdog/Registry/documents.json` (match on `file` frontmatter field)
- If no registry entry, report: `ORPHANED NOTE: documents/<filename> (no registry entry)`

### Entities

Read `.watchdog/Registry/entities.json`. For each entry:
- Check that `entities/<type-lowercase>/<id>.md` exists
- If missing, report: `MISSING NOTE: entities/<type>/<id>.md (registered as entity)`

List all files in `entities/` recursively. For each:
- Check that it has a corresponding entry in `.watchdog/Registry/entities.json` (match on `id` frontmatter)
- If no registry entry, report: `ORPHANED NOTE: <path> (no registry entry)`

---

## 2. Frontmatter completeness

For each entity note, check that all required frontmatter fields are present:
- `id`, `name`, `type`, `aliases`, `appears_in`, `date_first_seen`, `date_last_updated`

For each document note, check:
- `title`, `type`, `document_type`, `file`, `date_ingested`, `entities_mentioned`, `page_count`

Report any missing field: `MISSING FIELD: <file> — missing: <field>`

---

## 3. Dead wiki links

Scan all `.md` files in `entities/` and `documents/` for `[[...]]` links.

For each link, check that the target file exists:
```bash
grep -r '\[\[' entities/ documents/ --include="*.md" -h | grep -oP '\[\[\K[^\]|]+' | sort -u
```

Note: links use pipe-alias syntax (`[[path|Display Name]]`). Extract only the path portion before any `|` when checking file existence. Report broken links: `DEAD LINK: <source file> → [[<target>]] (file not found)`

---

## 4. Lock file and temp file check

**Ingest lock** — check whether `.watchdog/Registry/.ingest-lock` exists. If it does, read its `started_at` field and compute the age. If older than 30 minutes, report:
`STALE LOCK: .watchdog/Registry/.ingest-lock (created <timestamp>, <N>m ago) — run: watchdog unlock <project-slug>`

**Orphaned extraction files** — check for leftover temp files from an interrupted ingest:

```bash
ls .watchdog/tmp/watchdog-extraction-*.json 2>/dev/null
```

For each file found, report:
`ORPHANED TEMP: .watchdog/tmp/watchdog-extraction-<sha256>.json — safe to delete`

These are left behind when Claude wrote the extraction JSON but never called `watchdog write-vault`. They are safe to delete; if you want to recover the extraction, open the file to inspect it first. To clean up all of them:
```bash
rm .watchdog/tmp/watchdog-extraction-*.json
```

---

## 5. Manifest consistency

Read `.watchdog/Registry/manifest.json` and `.watchdog/Registry/entities.json`.

For each entity in `entities.json`, check that:
- An entry with the same `id` exists in `manifest.json`
- The `name` matches
- The `type` matches
- All aliases in `entities.json` are present in the manifest's `aliases` list
- The `note_path` in the manifest resolves to an existing file

For each entry in `manifest.json`, check that a corresponding entity exists in `entities.json`.

Report mismatches as warnings:
- `MANIFEST STALE: entity <id> — name mismatch (manifest: "<a>", entities.json: "<b>")`
- `MANIFEST STALE: entity <id> — missing from manifest.json`
- `MANIFEST STALE: entity <id> — alias "<alias>" in entities.json but not in manifest`
- `MANIFEST ORPHAN: entity <id> in manifest.json but not in entities.json`

---

## 6. Registry counts

Read `.watchdog/Registry/registry.json`. Compare `document_count` and `entity_count` against the actual counts in `documents.json` and `entities.json`. If they differ, report:
`COUNT MISMATCH: registry.json says <n> documents but documents.json has <m>`

Also check that the following files exist at the vault root. Report any that are missing:
- `timeline.md` — `MISSING FILE: timeline.md (rebuild by running /watchdog-entity on any entity)`
- `hot.md` — `MISSING FILE: hot.md (created automatically by /watchdog-ingest; create manually if needed)`
- `log.md` — `MISSING FILE: log.md (created automatically by /watchdog-ingest; create manually if needed)`

---

## 7. Entities with no relationships

Find all entities in `entities.json` where `roles` is an empty list or absent. These entities appear in documents but haven't been connected to any other entity via a relationship. Report as a low-priority list: `ISOLATED ENTITY: <id> — appears in <n> documents but has no relationships`

---

## 8. Report

Print a health summary to the terminal:

```
Vault health check — <date>
===========================
Documents:   <n> registered, <n> notes found
Entities:    <n> registered, <n> notes found
Dead links:  <n>
Missing fields: <n>

Issues:
  CRITICAL  (<n>): missing notes, orphaned notes, stale lock
  WARNING   (<n>): missing frontmatter fields, dead links, count mismatches
  INFO      (<n>): isolated entities

<list of issues>

Run `/watchdog-surface` to find connections and anomalies.
```

If no issues are found: `Vault is healthy. No issues found.`
