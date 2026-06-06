# /health — Check vault integrity

Audit the vault for structural problems: orphaned notes, broken links, registry mismatches, and missing required fields.

---

## 1. Registry vs. notes consistency

### Documents

Read `Registry/documents.json`. For each entry:
- Check that `documents/<document_note>.md` exists
- If the file is missing, report: `MISSING NOTE: documents/<slug>.md (registered as <sha256>)`

List all files in `documents/`. For each:
- Check that it has a corresponding entry in `Registry/documents.json` (match on `file` frontmatter field)
- If no registry entry, report: `ORPHANED NOTE: documents/<filename> (no registry entry)`

### Entities

Read `Registry/entities.json`. For each entry:
- Check that `entities/<type-lowercase>/<id>.md` exists
- If missing, report: `MISSING NOTE: entities/<type>/<id>.md (registered as entity)`

List all files in `entities/` recursively. For each:
- Check that it has a corresponding entry in `Registry/entities.json` (match on `id` frontmatter)
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
grep -r '\[\[' entities/ documents/ --include="*.md" -h | grep -oP '\[\[\K[^\]]+' | sort -u
```

For each unique link target, check if the corresponding file exists. Report broken links: `DEAD LINK: <source file> → [[<target>]] (file not found)`

---

## 4. Lock file check

Check whether `Registry/.ingest-lock` exists. If it does, check its timestamp. If older than 30 minutes, report:
`STALE LOCK: Registry/.ingest-lock (created <timestamp>) — safe to delete`

---

## 5. Registry counts

Read `Registry/registry.json`. Compare `document_count` and `entity_count` against the actual counts in `documents.json` and `entities.json`. If they differ, report:
`COUNT MISMATCH: registry.json says <n> documents but documents.json has <m>`

---

## 6. Entities with no relationships

Find all entity notes where `roles` is empty or absent. These entities appear in documents but haven't been connected to any other entity. Report as a low-priority list: `ISOLATED ENTITY: <id> — appears in <n> documents but has no relationships`

---

## 7. Report

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

Run `/surface` to find connections and anomalies.
```

If no issues are found: `Vault is healthy. No issues found.`
