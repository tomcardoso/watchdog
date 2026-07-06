---
description: Audit vault integrity — registry/note consistency, dead links, stale locks, unresolved contradictions, unreviewed near-duplicates
---

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

## 4. Lock file check

Check whether `.watchdog/Registry/.ingest-lock` exists. If it does, read its `started_at` field and compute the age. If older than 30 minutes, report:
`STALE LOCK: .watchdog/Registry/.ingest-lock (created <timestamp>, <N>m ago) — run: watchdog unlock <project-slug>`

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
- `timeline.md` — `MISSING FILE: timeline.md (rebuild with: watchdog timeline)`
- `hot.md` — `MISSING FILE: hot.md (created automatically by watchdog ingest; create manually if needed)`
- `log.md` — `MISSING FILE: log.md (created automatically by watchdog ingest; create manually if needed)`

---

## 7. Entities with no relationships

Find all entities in `entities.json` where `roles` is an empty list or absent. These entities appear in documents but haven't been connected to any other entity via a relationship. Report as a low-priority list: `ISOLATED ENTITY: <id> — appears in <n> documents but has no relationships`

---

## 8. Unresolved contradictions

The pipeline flags conflicts between sources as `> [!contradiction]` callouts in an entity's `## Contradictions` section, verified at extraction time and never auto-removed. They sit there until a journalist resolves them — but nothing surfaces the full list in one place. Find them:

```bash
grep -rn '\[!contradiction\]' entities/ --include="*.md"
```

For each callout, report: `CONTRADICTION: entities/<type>/<id>.md — <first line of the callout>`. These are conflicting claims a journalist should resolve (confirm which source is right; check dates and primary sources). Count them for the summary. They are append-only by design, so a callout the journalist has already worked through may still appear — treat the list as "conflicts on record," highest-value first.

---

## 9. Unreviewed near-duplicates

During chew, MinHash flags a document that closely matches an earlier one by writing `near_duplicate_of` into its registry entry — detection only, never auto-discarded, so the journalist decides whether they are the same document. Read `.watchdog/Registry/documents.json`; for every entry with a non-empty `near_duplicate_of`, report:

`NEAR-DUPLICATE: documents/<slug>.md ~ <near_duplicate_of> — confirm same or different`

Count them for the summary. A flagged pair stays flagged until the journalist acts (delete one, or annotate the document note's `## Notes`), so this list is everything still awaiting that judgement. If the two documents also produced two separate entity records for what turns out to be the same real-world person or company (check the dashboard's "Possible duplicates"/"Single-source entities to review" tables, or entities with near-identical names/aliases in `entities.json`), fix that with `watchdog merge-entities <keep-id> <merge-id>` — deterministic registry surgery that unions aliases, roles, and timeline events onto one id and remaps every relationship elsewhere in the vault that pointed at the losing id. The merge keeps only one entity's prose Summary, so when both entities had one, follow it with `/watchdog-entity <keep-id>` to re-synthesize the Summary and Timeline from every merged source (the command prints this nudge itself when a Summary was dropped).

---

## 10. Report

Print a health summary to the terminal:

```
Vault health check — <date>
===========================
Documents:   <n> registered, <n> notes found
Entities:    <n> registered, <n> notes found
Dead links:  <n>
Missing fields: <n>
Contradictions: <n> unresolved
Near-duplicates: <n> unreviewed

Issues:
  CRITICAL  (<n>): missing notes, orphaned notes, stale lock
  WARNING   (<n>): missing frontmatter fields, dead links, count mismatches, unresolved contradictions
  INFO      (<n>): isolated entities, unreviewed near-duplicates

<list of issues>

Run `/watchdog-surface` to find connections and anomalies.
```

If no issues are found: `Vault is healthy. No issues found.`
