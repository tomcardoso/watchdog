# Backend API — the contract between the app and `watchdog.gui.server`

The Electron app talks to a Python process (`python -m watchdog.gui.server`) over stdin/stdout,
one JSON object per line. This page lists every method and event. The TypeScript mirror of these
shapes lives in `gui/src/shared/api.ts`; keep the two in step.

## Conventions

- **Request** `{"id": 7, "method": "vault.summary", "params": {...}}` → **response**
  `{"id": 7, "result": ...}` or `{"id": 7, "error": {"message", "code", "data"}}`.
- **Events** `{"event": "job.log", "data": {...}}` arrive unsolicited.
- `vault` params are **absolute paths** to a vault folder. The server checks the folder is a
  Watchdog vault (`vault_paths.is_vault`) and raises `RpcError(code="not_a_vault")` otherwise.
- Paths inside a vault are **vault-relative POSIX strings** (`documents/alpha`,
  `entities/person/jane-doe.md`). Note paths may omit `.md`, as wikilinks do.
- Timestamps are ISO-8601 strings. Counts are integers. Missing optional data is `null`, never an
  absent key.
- **Reads** run in-process. **Mutations** that the CLI already implements run the real
  `watchdog …` command as a subprocess (see `jobs.*` and `action.run`), so the app and the terminal
  can never disagree about what a command does. Thin in-process mutations are used only where the
  CLI's own code is already a library function (resolutions, settings, auth keys, the `## Notes`
  section of a note). App-only features with no CLI command (`history.restore`, `history.remove`,
  `history.clear`) call their library function directly (D286, D288, I10).
- **Folder access (D268).** When the app starts the server with `WATCHDOG_ENFORCE_ACCESS=1`, any
  call naming a vault outside the folders in `~/.watchdog/access.json`, and any `jobs.start` or
  `action.run` there, fails with `RpcError(code="not_granted", data={"path"})`. The server only
  reads that file (`access.list`); grants are made by the main process (`window.watchdog.access`).
- **Engine setup (D272).** While the app's engine is still installing its background phase, the
  main process starts the server with `WATCHDOG_ENGINE_PENDING=1`. `jobs.start` and `action.run`
  then refuse `add`, `chew`, `dig`, `bark`, `ingest`, `watch`, `requeue`, `reindex` and
  `merge-entities` (grouped forms and aliases included) with
  `RpcError(code="engine_not_ready", data={"command"})`, and `search.query` skips the
  meaning-based lane (`semantic_pending: true`). `engine.setReady` lifts it in place.
- Errors meant for the user raise `RpcError("plain sentence")`. A `SystemExit` raised by reused
  CLI code is converted to an error with its message (leading `Error:` stripped).

---

## app

| Method | Params | Result |
|---|---|---|
| `app.ping` | — | `{ok: true}` |
| `app.info` | — | `{version, python, python_version, platform, watchdog_home, config_file, setup_complete, projects_dir, claude_code: {installed, logged_in}, obsidian_installed, engine_ready}` |
| `engine.ready` | — | `{ready}`: false while the engine's background phase is unfinished (D272) |
| `engine.setReady` | — | `{ready: true}`. Called by the main process when the background phase finishes: clears `WATCHDOG_ENGINE_PENDING` for the server and the commands it starts, and drops the import caches so the new packages load |

## access

| Method | Params | Result |
|---|---|---|
| `access.list` | — | `{enforced: bool, file, folders: [{path, label, granted}]}` — read-only |

## projects

```
Project = {
  slug, name, description|null, path, archived: bool, created|null,
  health: null | "missing" | "not_a_vault" | "registry_corrupt",   // _check_project_health
  stats: { documents, entities, last_ingest|null, incoming, awaiting, failed,
           history_bytes|null },   // the version history's size on disk, from a cache (D288); null with none
  access: bool   // false when folder access is enforced and the vault isn't in an allowed folder
}
```

| Method | Params | Result |
|---|---|---|
| `projects.list` | `{all?: bool}` | `Project[]` (archived only when `all`) |
| `projects.get` | `{slug}` | `Project` — exact slug or unique prefix; errors `not_found` / `ambiguous` |
| `projects.forPath` | `{path}` | `Project \| null` — the registered project at that folder |
| `projects.status` | `{slug}` | `{project: Project, by_type: {[entityType]: n}, documents_by_type: {[docType]: n}, locks: {chew: bool, ingest: bool}, pending_finalization: {...}\|null, size_bytes}` |
| `projects.log` | `{slug, lines?: int}` | `{lines: string[]}` — the last `lines` (default 500) of `.watchdog/registry/processing.log` |
| `projects.doctor` | — | `{issues: [{kind: "missing"\|"schema"\|"corrupt_registry", slug, name, path, problem, suggestion}]}` |

Mutations (`new`, `register`, `rename`, `describe`, `move`, `archive`, `unarchive`, `delete`) go
through `action.run` with the CLI's own arguments, e.g.
`["new", "Shell Co", "--description", "…", "--dir", "/x"]`. The app re-reads `projects.list`
afterwards.

## vault — reading an investigation

```
Summary = {               // cmd/home.summary(), made JSON-safe
  name, path,
  briefing: {path, name, date}|null,
  headline|null,         // the latest briefing's status line, Markdown with fact links, from its sidecar (D285)
  contradictions, leads, near_duplicates, possible_same, alerts, // waiting on you
  incoming, awaiting_dig, awaiting_bark, pending_finalize: bool,
  failed, research_urls, context_unseeded: bool,               // in progress
  has_work: bool,                                              // home.has_work()
  totals: {documents, entities, pages, events},
  verification: VerificationSummary,   // verification.summary() over every committed fact (D271)
  recent_documents: DocumentRow[],   // newest 8 by ingested_at
  top_entities: EntityRow[]          // 8 with the most documents
}

DocumentRow = {
  sha, filename, title|null, document_type|null, date_of_document|null,
  page_count|null, record_skill|null, ingested_at|null,
  near_duplicate_of|null,            // display text of the matched document
  media_kind: "audio"|"video"|null, duration_seconds|null,   // recordings (D273)
  note: "documents/<slug>"|null,     // registry document_note
  original: "morgue/…/file.pdf"|null,// registry morgue_path, vault-relative
  fulltext: "morgue/…/file.md"|null, // the full-text sibling, when present
  ext: "pdf"|"docx"|…,               // from filename, lowercase, no dot
  entity_count, source|null, obtained|null, summary|null
}

EntityRow = {
  id, name, type,                    // type is one of the six (entity_type.ENTITY_TYPES) or "other"
  aliases: string[], doc_count, role_count, contradiction_count,
  first_seen|null, last_updated|null, note: "entities/<type>/<id>"|null,
  has_summary: bool, summary|null    // first paragraph of ## Summary
}
```

| Method | Params | Result |
|---|---|---|
| `vault.summary` | `{vault}` | `Summary` |
| `vault.documents` | `{vault}` | `DocumentRow[]` |
| `vault.document` | `{vault, sha}` | `DocumentDetail` (below) |
| `vault.entities` | `{vault}` | `EntityRow[]` |
| `vault.entity` | `{vault, id}` | `EntityDetail` (below) |
| `vault.graph` | `{vault}` | `{nodes: [{id, name, type, doc_count}], edges: [{source, target, role, docs: string[]}]}` — `docs` are document shas, repeated (source, target, role) edges merged; stated-direction edges only, edges to unprofiled ids dropped (as `export._forward_edges`) |
| `vault.timeline` | `{vault}` | `{events: TimelineEvent[]}` sorted by date |
| `vault.note` | `{vault, path}` | `{path, exists, frontmatter: object, body: string, title\|null, kind: "entity"\|"document"\|"briefing"\|"query"\|"wiki"\|"other"}` |
| `vault.saveNotes` | `{vault, path, text}` | `{ok: true}` (existing `entities/…`/`documents/…` notes and saved pages in `queries/…`/`wiki/…` only, else `forbidden`; an empty `text` keeps the placeholder comment) — replaces only the body of the file's `## Notes` section (journalist annotations; neither the pipeline nor the skills write there). An entity or document note save takes the registry lock the commit pass holds, waiting up to 3 s, then fails with `busy` (the app retries). Recorded as a version of the file's history (D286, D288) |
| `vault.resolveLink` | `{vault, target}` | `{path\|null, kind: "document"\|"entity"\|"briefing"\|"query"\|"wiki"\|"note"\|"original"\|"fulltext"\|"missing", sha\|null, page\|null}` — what a wikilink target points at (bare names resolve like Obsidian: entity id/name/alias, document slug/title/filename, top-level note) (`documents/x`, `entities/person/y`, `morgue/…/f.pdf#page=3`) |
| `vault.citations` | `{vault, links: string[]}` | `{[link]: {target, block, status: "found"\|"not_found", fact: {id, sha, fact, page\|null, title\|null, note\|null, date\|null, basis\|null, mark: "verified"\|"disputed"\|"unverifiable"\|null, passage\|null, passage_page\|null}\|null, disputed?}}` — what each fact citation names (D283). A link is `<note>#^f-<id>` as written in `[[<note>#^f-<id>\|…]]`; others are ignored; at most 500. Read-only. The Markdown renderer calls it for every page it shows; a `not_found` citation is shown as "source not found", never as a link |
| `vault.checkCitations` | `{vault}` | `{checked, citations, found, not_found, disputed, unresolved_short, pages: [{path, items, citations, found, not_found, disputed, unresolved_short}]}` — every fact citation in `queries/`, `wiki/` and `briefings/` (Maintenance → Check citations; the function `watchdog check-citations` calls). Changes nothing |
| `vault.pipeline` | `{vault}` | `PipelineState` (below) |
| `vault.briefings` | `{vault}` | `[{path, name, kind: "briefing"\|"leads"\|"alerts"\|"research", date, title}]`, newest first |
| `vault.notes` | `{vault}` | `[{path, kind: "query"\|"wiki", title, modified}]` — pages Claude sessions saved in `queries/` and `wiki/`, most recently modified first |
| `vault.readFile` | `{vault, path}` | `{text, exists}` — only for the journalist-owned files: `context.md`, `watchlist.md`, `requests.md`, `log.md`, `timeline.md`, `index.md`, and anything under `briefings/`, `queries/`, `wiki/` (not `hot.md`, retired in D285) |
| `vault.sessionPrimer` | `{vault}` | `{text, chars, budget}` — the primer every Ask Claude session starts with, built now from the vault's records exactly as `watchdog session-primer` prints it for the SessionStart hook (`cmd/primer.build`, D285). Briefings → Current state shows it. Read-only, no model |
| `vault.writeFile` | `{vault, path, text}` | `{ok}` — only `context.md` and `watchlist.md`. Recorded as a version of the file's history (D286) |
| `vault.requests` | `{vault}` | `{open: [{rid, type\|null, what, why\|null, likely_source\|null, cited_in: [{sha, filename, note}], added\|null}], resolved_count}` |
| `vault.contextFiles` | `{vault}` | `[{name, size, modified}]` in `context/` |
| `vault.migrate` | `{vault}` | `{changes: string[]}` — renames an older vault's `_INCOMING/`/`_CONTEXT/` folders to `incoming/`/`context/` (D266); opening a vault does this once automatically |

```
DocumentDetail = DocumentRow & {
  frontmatter: object, body: string,            // the document note
  facts: [{id, fact, page|null, basis: "stated"|"inferred", date|null, quote|null,
           entities: [{id, name, type}], figure_note|null, quote_note|null, added_by|null,
           passage|null, passage_page|null, passage_method: "quote"|"matched"|"unlocated"|null,
           passage_score|null, mark: FactMark|null}],   // id: the derived fact id (D271); passage*: the source passage found in post-flight (D270), passage_method null when the document predates passages; mark: the reporter's current mark, only if it still matches the fact's words and page   // quote_note: quote-verification warning; facts fall back to the note's `## Key facts` bullets when no staged extraction exists
 
  entities: [{id, name, type, role|null}],
  pages: [{page, text}],                        // morgue full text split on <!-- PAGE n -->
  file_metadata: object, sidecar: object|null, metadata: object|null,
  extract_model|null, extract_effort|null, record_skill_hash|null,
  duplicates: [{sha, filename, note}],          // documents this one nearly duplicates, both ways
  media: null | {kind: "audio"|"video", duration_seconds|null, page_seconds|null,   // recordings only (D273):
          pages: [{page, start, end}], language|null, model|null}   // page n is start..end seconds; unknown fields ignored
}

EntityDetail = EntityRow & {
  frontmatter: object, body: string,            // the entity note
  sections: {summary|null, analysis|null, contradictions|null, timeline|null,
             relationships|null, notes|null},   // raw markdown per ## section; summary also reads the older "## Summary (AI-written)" heading
  documents: DocumentRow[],                     // appears_in, resolved
  relationships: [{role, target_id, target_name|null, target_type|null, direction: "out"|"in",
                   docs: string[]}],
  contradictions: [{rid, summary, text, resolved: bool}],
  timeline: TimelineEvent[],
  facts: (Fact & {sha, title|null, doc_date|null, note|null})[],   // every fact tagged to the entity in the stored extractions, in date order (D280); mark as in DocumentDetail
  synthesis: {summary, analysis|null, by: "model"|"session"|"carried", model|null, made_at|null,
              facts_total|null, facts_shown|null, stale: "merge"|"undo"|null,
              stale_notice|null,                // the out-of-date warning the note shows for a stale summary (D285)
              summary_md, analysis_md|null,     // with citations rendered as fact links (D283); show these
              citations: {cited, linked, unknown, missing, disputed}} | null,   // the entity summary, from the registry; who wrote it is metadata, not shown (D284); unknown fields ignored
  legacy_claims: string|null      // claims an older version recorded for documents with no stored extraction (markdown)
}

TimelineEvent = {
  date, precision: "day"|"month"|"year"|null, text,
  entities: [{id, name, type}], sha|null, filename|null, page|null, note|null,
  disputed: bool         // the reporter disputes the fact behind the event; show the label (D285)
}

PipelineState = {
  incoming:  [{name, path, size, modified, sidecar: bool}],   // incoming/, excluding failed/skipped
  chew_failed: [{name, path, size}],                          // incoming/failed/
  skipped:   [{name, path, size, reason|null}],               // incoming/skipped/
  queued:    [{sha, filename, page_count|null, est_tokens|null, staged: bool}],  // chewed; staged = extracted awaiting bark
  failed:    [{sha, filename, reason|null}],                  // queue/_failed/
  pending_finalization: {docs, entities}|null,
  locks: {chew: bool, ingest: bool},
  research_urls: int,
  batch_pending: object|null                                  // batch_extract.read_state
}
```

## verify — the verification ledger (D271)

```
FactMark = {status: "verified"|"disputed"|"unverifiable", note|null, by|null, at|null}   // at: ISO 8601 with offset
VerificationSummary = {facts, verified, disputed, unverifiable, unmarked, unlocated, orphaned, read_only: bool}
```

| Method | Params | Result |
|---|---|---|
| `verify.mark` | `{vault, id, status: "verified"\|"disputed"\|"unverifiable"\|null, note?}` | `FactMark & {id}` — sets the mark on one current fact, or clears it when `status` is null or empty (the history is kept). `by` is the `reporter_name` setting, else the computer account's name. `note` is trimmed and capped at 2,000 characters. Calls `pipeline/verification.mark`, the function `watchdog verify-fact` calls (I10); it takes the ledger's own lock and regenerates `verification.md`. Errors: `bad_params` (not a fact id), `not_found` (no current fact has that id, for example after the document was processed again), `bad_value` (unknown status), `ledger_too_new` (the ledger was written by a newer Watchdog and is read-only) |
| `verify.facts` | `{vault}` | `{summary: VerificationSummary, facts: LedgerFact[], orphaned: OrphanMark[], reporter}` — every current fact, sorted by document title then reading order, with `{id, fact, page\|null, basis, sha, filename, title, passage_method\|null, passage_page\|null, mark: FactMark\|null}`; `orphaned` lists current marks whose fact's words or page have since changed, each `FactMark & {id, fact, page, sha256, filename}` with the fact as it read when marked; `reporter` is the name `verify.mark` would record |

A fact's `id` is `fact:<scheme>:<12 characters of the document's SHA-256>:<10-character hash>`, with `:2`, `:3` on exact duplicates within one document. It stays the same while the fact's words and page do; see D271.

## history — version history of generated and app-edited files (D286)

App-only: no CLI command. Tracked files are Markdown outside `morgue/`, `incoming/`, `context/` and
hidden folders, plus `.watchdog/registry/{entities,documents,merges,verification,resolutions,requests}.json`.
`path` is vault-relative; a note path may omit `.md`. An untracked path fails with `not_tracked`; a
version the file does not have, `not_found`; a store written by a newer Watchdog refuses writes
with `history_too_new`.

```
HistoryVersion = {version, at, cause: {kind: "run"|"merge"|"undo_merge"|"mark"|"rebuild"|"notes"|"edit"|"review"|"session"|"restore"|"cleared"|"found", first?, incomplete?, …},
                  label,          // the plain-language line to show ("Documents added: a.pdf and 2 more")
                  files,          // how many files the version changed
                  removed}        // how many of its changes were removed from the history (D288)
DiffLine = {op: " "|"-"|"+", old|null, new|null, segments: [{t: "eq"|"del"|"ins", s}]}   // one segment = a whole added or removed line; several = words inside a changed line
```

| Method | Params | Result |
|---|---|---|
| `history.file` | `{vault, path}` | `{path, tracked, restore: "page"\|"notes"\|"none", exists, too_new, versions: (HistoryVersion & {deleted, current, latest, removed_before})[]}` newest first; `latest` is the file's newest version (never removable), `removed_before` how many of its versions just before this one were removed. Records the file first if it changed since its last version (a session's page, an Obsidian edit), unless a run holds the vault |
| `history.read` | `{vault, path, version}` | `{path, version, exists, text}` |
| `history.diff` | `{vault, path, version, against?: "previous"\|"current"}` | `{path, before: {version\|null, exists}, after: {version\|null, exists}, removed_between, hunks: [{old_start, new_start, lines: DiffLine[]}], added, removed, truncated, identical}` — three lines of context; at most 4,000 lines shown (`truncated`), the counts always complete; `removed_between` counts versions removed between the two compared (0 against `current`) |
| `history.restore` | `{vault, path, version}` | `{path, part: "page"\|"notes", version}` — `page` (`context.md`, `watchlist.md`, `briefings/`, `queries/`, `wiki/`) writes the version back whole; `notes` (`entities/`, `documents/`) puts back only its `## Notes` section; anything else fails with `cannot_restore`, as does a version recording a deletion. Recorded as a new version, cause `restore` ("Restored by you") |
| `history.versions` | `{vault, limit?: 100, before?}` | `{versions: (HistoryVersion & {changes: [{path, deleted}]})[], total, more, too_new}` newest first; `before` pages back |
| `history.stats` | `{vault}` | `{versions, files, objects, bytes, since\|null, too_new}` |
| `history.remove` | `{vault, version, path?, older?: false, dry_run?: false}` | `{removed: [{path, version}], kept_current: [path], purged, shared: [{path, version}], freed_bytes, dry_run}` — D288. With `path`: that file's version (with `older`, also every earlier one); the file's newest version fails with `cannot_remove`. Without: every file's entry in `version` except files whose newest version it is (`kept_current`). Rewrites the log without them under the store lock and deletes every stored blob no remaining version names (`purged`; `shared` lists removed versions whose text another version still holds). Irreversible: the app confirms first, from a `dry_run`. `busy` while a run holds the vault; `not_found` for an unknown version |
| `history.clear` | `{vault}` | `{removed_versions, freed_bytes, versions, bytes}` — deletes every past version and records the current files as the first version of a new history (cause `cleared`). Irreversible: the app confirms first. Refused with `busy` while a run holds the vault |

## ingest — before a pipeline run

| Method | Params | Result |
|---|---|---|
| `ingest.preflight` | `{vault, options?: RunOptions}` | `Preflight` |
| `ingest.estimate` | `{vault, stage: "dig"\|"bark", all_models?: bool, options?: RunOptions}` | `{text, estimate: object\|null, all_models: [{label, provider, cost_usd, note\|null}]\|null}` |

```
RunOptions = {                       // mirrors the dig/bark/add flags; unset = configured default
  extractor_model?, classifier_model?, finalizer_model?,
  finalizer_reconciliation_model?, finalizer_synthesis_model?,
  finalizer_timeline_model?, finalizer_briefing_model?,
  extractor_effort?, classifier_effort?, finalizer_effort?,
  concurrency?, classify_pages?, skill?, limit?, verify?: bool|null,
  force?: bool, wait?: bool, skip_briefing?: bool, chew_workers?, chunk_workers?
}

Preflight = {
  documents_to_send: int,            // needs_extraction(scan_queue) — what the warning counts
  incoming: int, queued: int, staged: int, failed: int,
  pending_finalization: {docs, entities}|null,
  auth: {mode: "subscription"|"api-key"|"none"|null, ok: bool, reason|null},
  models: [{stage: "classifier"|"extractor"|"finalizer"|"finalizer:<stage>", backend, model, effort|null, label}],
  auto_approve: {enabled: bool, approve: bool, blocker|null},  // ingest._auto_approve_verdict
  warning_text: string               // the public-records warning, plain text, no ANSI
}
```

`RunOptions` → CLI flags is done by the **app** when it builds a job's `args`; the server exposes
`jobs.flags` to do it so the mapping lives in one place.

## jobs — long-running `watchdog` commands

A job is `python -m watchdog <args…>` run with the vault as its working directory, stdin closed
(any prompt the CLI would show is declined, never hung on), `NO_COLOR=1`, and
`WATCHDOG_PROGRESS=1`, which makes the pipeline write structured progress lines (see
`watchdog/progress.py`) that the server turns into `job.progress` events.

| Method | Params | Result |
|---|---|---|
| `jobs.start` | `{vault\|null, args: string[], label, kind?}` | `Job` |
| `jobs.cancel` | `{id}` | `{ok}` — SIGINT (Ctrl+C), so the CLI's graceful stop runs; a second cancel kills |
| `jobs.list` | — | `Job[]` (running and the last 50 finished) |
| `jobs.get` | `{id}` | `Job & {log: LogLine[]}` (last 5,000 lines) |
| `jobs.flags` | `{command: "add"\|"dig"\|"bark"\|"chew", options: RunOptions}` | `{args: string[]}` |
| `jobs.rebuildNotes` | `{vault}` | `Job` — Maintenance → "Rebuild notes": rewrites every entity and document note from stored data with no model call (`python -m watchdog.pipeline.entity_notes`, the library function, D280); waits for the full engine like `reindex` |
| `jobs.undoMerge` | `{vault, id}` | `Job` — Review → Merges "Undo merge" (`python -m watchdog.pipeline.merge_undo <id>`, D280). Errors: `cannot_undo` with the reason, `not_found` |
| `action.run` | `{vault\|null, args: string[], timeout?: seconds}` | `{code, stdout, stderr}` — a short, synchronous command (rename, archive, unlock…) |

```
Job = { id, label, kind, vault|null, args, state: "running"|"done"|"failed"|"cancelled",
ProgressState = { stage|null, done|null, total|null, current|null, note?: string|null, docs: {[sha]: {filename, state, detail|null}} }
// stage "model" is a one-time model download (current = "Downloading the transcription model (486 MB)", done/total in MB);
// note is a transient detail beside the stage ("Transcribing hearing.mp4, 12:05 of 1:02:05").
LogLine = { t, stream: "out"|"err", text }
ProgressState = { stage|null, done|null, total|null, current|null, docs: {[sha]: {filename, state, detail|null}} }
```

Events: `job.started {job}`, `job.log {id, lines: LogLine[]}` (batched ≤ 10/s),
`job.progress {id, progress: ProgressState, event}`, `job.finished {job}`.

## search

| Method | Params | Result |
|---|---|---|
| `search.query` | `{vault, query, top?: 5, threshold?: number\|null, rerank?: true}` | `{query, index_empty: bool, exact_error\|null, semantic_error\|null, semantic_pending: bool, exact: [...], passages: [...], notes: [...]}` — the `--json` shape, each item enriched with `sha`, `note`, `original` where resolvable |
| `search.batch` | `{vault\|null, terms: string[], everywhere?: bool}` | `{terms: [{term, checked: bool, hits: [{vault_name?, kind, title, note, page, text, path, sha}]}]}` — entity matches are hits with `kind: "entity"`; `top?` limits hits per term |
| `search.everywhere` | `{query, top?}` | `{vaults: [{slug, name, path, entity_hits: [...], exact: [...], error\|null}], skipped: [{slug, reason}]}` |
| `search.status` | `{vault}` | `{total, documents, notes, passages, fulltext: {corpus, notes, total}}` — `embed.index_stats` (`documents` = indexed document files) |

## review

```
ReviewItem = {kind: "contradictions"|"leads"|"alerts"|"duplicates"|"merges", rid, title, detail: string[], note|null,
              pair?: SamePair}                     // pair only on "merges" items
SamePair   = {tier: "high"|"medium"|"low", rule, model_declined: bool,
              evidence: {surface, identifier: {scheme, value}|null, shared: [{relationship, target_id, target_name}]},
              a: SameSide, b: SameSide}
SameSide   = {id, name, type, aliases, documents: sha[], doc_count, note|null,
              facts: [{id, sha, fact, page, document, date, disputed: bool}], roles: [{relationship, target}]}
MergeLogEntry = {id, keep: {id, name, type, exists, note|null}, merged: {id, name, type},
                 tier: "high"|"medium"|"low"|"manual", decided_by: "rule"|"model"|"reporter", rule|null,
                 reason, model|null, reporter|null, same_name: bool, at, first_at,
                 identifier|null, shared: [...], documents: [{sha, title}], document_count,
                 facts: [{id, fact, page, sha, title, disputed: bool}], undo_available: bool,
                 undo_reason: string|null,            // why it cannot be undone now, in plain words (D280)
                 undone: {at, by, split_id}|null}
```

A `merges` item is a "possible same" pair from `.watchdog/registry/merges.json` (D279). Resolving
its rid (`same:<hash>`) is the reporter's "Not the same": the pair is never merged automatically
afterwards. Merging it is the existing `merge-entities` job (I10), which closes the item.

| Method | Params | Result |
|---|---|---|
| `review.items` | `{vault, kinds?: string[]}` | `{items: ReviewItem[], counts: {[kind]: n}}` — `cmd/review.open_items` |
| `review.resolve` | `{vault, rids}` | `{resolved: string[]}` — `resolutions.resolve(label="review")` + `tick_in_briefings`, exactly as the terminal walk does |
| `review.unresolve` | `{vault, rids}` | `{unresolved: string[]}` |
| `review.resolved` | `{vault}` | `{items: [{rid, label, resolved_at, kind}]}` — what `review resolve --list` shows |
| `review.sync` | `{vault}` | `{resolved: string[], unresolved: string[]}` — `resolutions.sync_from_briefings` |
| `review.leads` | `{vault}` | the full lead sweep, `leads.scan` made JSON-safe, plus `total` |
| `review.watchlist` | `{vault}` | `{terms: string[], text}` |
| `review.mergeLog` | `{vault, limit?}` | `{merges: MergeLogEntry[], total, too_new: bool, undo_available: bool}` — newest first, documents and facts resolved for display; each entry says whether `merge_undo` can split it back now (`undo_available`, `undo_reason`) |
| `review.mergePreview` | `{vault, keep, merge}` | `{keep: EntityRow, merge: EntityRow, both_have_summary: bool, type_mismatch: bool}` |

`merge-entities` (with `--force` after the app's own confirmation), `contradiction-add`, `watchlist`
sweeps and `leads` run as jobs.

## settings, auth, skills

| Method | Params | Result |
|---|---|---|
| `settings.schema` | — | `{sections: [{title, blurb, keys: SettingKey[]}]}` |
| `settings.set` | `{key, value: string}` | `{key, value, display}` — validated by the CLI's `_coerce_value`; a bad value raises `RpcError` (code `bad_value`) with the CLI's message. An empty value clears a text/model key to its default. A secret's `value` is `null` and its `display` masked |
| `settings.models` | — | `{models: [{value, label, id, provider, backend (null for Claude tiers), input_per_mtok\|null, output_per_mtok\|null, context_window\|null, efforts: string[], notes\|null}], efforts: string[]}` — for model pickers |
| `auth.status` | — | `{claude: {mode, logged_in, reason\|null, env_key_set, key_masked\|null, key_source}, stages: [{stage, config_key, value, provider, ready, billing\|null}], keys: [{provider, masked, in_use: "in use"\|"unused"\|"inactive", detail, source: "stored"\|"env"}], base_urls: [{provider, url}], providers: [{provider, label, env, requires_key, base_url_setting\|null, base_url\|null, ready}]}` |
| `auth.setAnthropicMode` | `{mode: "subscription"\|"api-key", key?}` | `auth.status` result plus `warning\|null` (as do `setKey`, `deleteKey`, `setBaseUrl`) |
| `auth.setKey` | `{provider, key}` | `auth.status` result |
| `auth.deleteKey` | `{provider}` | `auth.status` result |
| `auth.setBaseUrl` | `{provider: "local"\|"openrouter", url}` | `auth.status` result — an empty `url` removes it |
| `skills.list` | — | `{skills: [{name, description, source: "package"\|"user"}], user_dir}` |
| `skills.read` | `{name}` | `{name, text}` |
| `setup.check` | — | `{deps: [{label, ok, hint\|null, required: false}], playwright: bool, gliner_model: bool, projects_dir\|null, config_exists: bool}` — no dependency blocks the app |
| `setup.models` | — | `{docling, gliner, embedding: bool, reranker: bool\|null, ocr: string\|null, claude_cli: string\|null, transcription: bool, transcription_model, transcription_size_mb}` — what is on disk, no network |
| `setup.downloadModel` | `{model: "transcription"}` | `Job` — downloads an on-demand model ahead of time (D273) as a job running `python -m watchdog.gui.engine_setup models --only transcription`; progress is the `model` stage |
| `setup.complete` | `{projects_dir?, auto_approve?}` | `{projects_dir, ocr_engine\|null, auto_approve}` — writes what `watchdog setup` writes; an existing config file is the "set up" signal |
| `auth.routeIngestion` | `{provider, model: "provider:id"}` | `auth.status` result — points classifier, extractor and finalizer at one model, as the setup wizard does |

```
SettingKey = {key, short, help, is_set: bool, default, current (null for secrets), display, kind: "bool"|"int"|"float"|"choice"|"model"|"effort"|"path"|"text"|"secret", choices: string[]|null}   // default can be computed when read: for `reporter_name` it is the computer account's name
```

## usage

| Method | Params | Result |
|---|---|---|
| `usage.runs` | `{vault}` | `{runs: [{ts, file, calls, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd, latency_s, backends, subscription: bool, stages: {[stage]: cost_usd}}], corpus: {documents, pages}\|null}` newest first |
| `usage.run` | `{vault, ts?}` | `{ts, stages: [{stage, model, backend, calls: [...], totals, wall_seconds\|null, peak_concurrency, batch_note\|null}], totals, subscription_note\|null, corpus, cost_per_page\|null}` (latest when `ts` omitted; `ts` is the timestamp in `usage-<ts>.json`, matched as a substring; error `no_runs` when none exist) |

## research

| Method | Params | Result |
|---|---|---|
| `research.status` | `{vault}` | `{queued: [{url, title\|null, source_type\|null, relevance\|null}], wayback_configured: bool}` |

Downloading runs as a job: `["research-fetch"]` for the queue, `["fetch", url…]` for links.

## chat — Claude Code sessions inside the app

Replaces the terminal hand-off of `watchdog ask`, `ask --context` and `research` with a session
run through the Claude Agent SDK in the vault's folder, so the vault's own `.claude/` settings,
`CLAUDE.md` and `/watchdog-*` commands apply exactly as they do in the terminal.

| Method | Params | Result |
|---|---|---|
| `chat.start` | `{vault, mode: "ask"\|"context"\|"research", model?: "sonnet"\|"opus"\|"haiku"\|null, prompt?: string}` | `{session}` — first prompt built as the CLI builds it (`ask.prompt_for`, `/watchdog-context`, `/watchdog-research <q>`) |
| `chat.send` | `{session, text}` | `{ok}` |
| `chat.interrupt` | `{session}` | `{ok}` |
| `chat.close` | `{session}` | `{ok, research_queued: int}` |
| `chat.permission` | `{session, request_id, allow: bool, always?: bool}` | `{ok}` |
| `chat.list` | `{vault}` | `[{session, mode, title, started, updated, model}]` saved transcripts, newest first |
| `chat.get` | `{session}` | `{session, mode, title, messages: ChatMessage[]}` |
| `chat.resume` | `{session}` | `{session}` — reopen a saved session (SDK `resume`) |
| `chat.delete` | `{session}` | `{ok}` |

```
ChatMessage = {id, role: "user"|"assistant"|"tool"|"system", text, tool?: {name, input, result|null, is_error}, ts}
```

Events: `chat.delta {session, message_id, text}` (streamed assistant text), `chat.message
{session, message: ChatMessage}`, `chat.permission {session, request_id, tool, input,
description}`, `chat.status {session, state: "thinking"|"idle"|"closed"|"error", detail|null,
cost_usd|null}`.
