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
  section of a note).
- Errors meant for the user raise `RpcError("plain sentence")`. A `SystemExit` raised by reused
  CLI code is converted to an error with its message (leading `Error:` stripped).

---

## app

| Method | Params | Result |
|---|---|---|
| `app.ping` | — | `{ok: true}` |
| `app.info` | — | `{version, python, python_version, platform, watchdog_home, config_file, setup_complete, projects_dir, claude_code: {installed, logged_in}, obsidian_installed}` |

## projects

```
Project = {
  slug, name, description|null, path, archived: bool, created|null,
  health: null | "missing" | "not_a_vault" | "registry_corrupt",   // _check_project_health
  stats: { documents, entities, last_ingest|null, incoming, awaiting, failed }
}
```

| Method | Params | Result |
|---|---|---|
| `projects.list` | `{all?: bool}` | `Project[]` (archived only when `all`) |
| `projects.get` | `{slug}` | `Project` — exact slug or unique prefix; errors `not_found` / `ambiguous` |
| `projects.forPath` | `{path}` | `Project \| null` — the registered project at that folder |
| `projects.status` | `{slug}` | `{project: Project, by_type: {[entityType]: n}, documents_by_type: {[docType]: n}, locks: {chew: bool, ingest: bool}, pending_finalization: {...}\|null, size_bytes}` |
| `projects.log` | `{slug, lines?: int}` | `{lines: string[]}` — the last `lines` (default 500) of `.watchdog/registry/ingest.log` |
| `projects.doctor` | — | `{issues: [{kind: "missing"\|"schema"\|"corrupt_registry", slug, name, path, problem, suggestion}]}` |

Mutations (`new`, `register`, `rename`, `describe`, `move`, `archive`, `unarchive`, `delete`) go
through `action.run` with the CLI's own arguments, e.g.
`["new", "Shell Co", "--description", "…", "--dir", "/x"]`. The app re-reads `projects.list`
afterwards.

## vault — reading an investigation

```
Summary = {               // cmd/home.summary(), made JSON-safe
  name, path,
  briefing: {path, name, date}|null, headline|null,
  contradictions, leads, near_duplicates, alerts,              // waiting on you
  incoming, awaiting_dig, awaiting_bark, pending_finalize: bool,
  failed, research_urls, context_unseeded: bool,               // in progress
  has_work: bool,                                              // home.has_work()
  totals: {documents, entities, pages, events},
  recent_documents: DocumentRow[],   // newest 8 by ingested_at
  top_entities: EntityRow[]          // 8 with the most documents
}

DocumentRow = {
  sha, filename, title|null, document_type|null, date_of_document|null,
  page_count|null, record_skill|null, ingested_at|null,
  near_duplicate_of|null,            // display text of the matched document
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
| `vault.saveNotes` | `{vault, path, text}` | `{ok: true}` (existing `entities/…`/`documents/…` notes only; an empty `text` keeps the placeholder comment) — replaces only the body of the note's `## Notes` section (journalist annotations; the pipeline never writes there) |
| `vault.resolveLink` | `{vault, target}` | `{path\|null, kind: "document"\|"entity"\|"briefing"\|"query"\|"wiki"\|"note"\|"original"\|"fulltext"\|"missing", sha\|null, page\|null}` — what a wikilink target points at (bare names resolve like Obsidian: entity id/name/alias, document slug/title/filename, top-level note) (`documents/x`, `entities/person/y`, `morgue/…/f.pdf#page=3`) |
| `vault.pipeline` | `{vault}` | `PipelineState` (below) |
| `vault.briefings` | `{vault}` | `[{path, name, kind: "briefing"\|"leads"\|"alerts"\|"research", date, title}]`, newest first |
| `vault.notes` | `{vault}` | `[{path, kind: "query"\|"wiki", title, modified}]` — pages Claude sessions saved in `queries/` and `wiki/`, most recently modified first |
| `vault.readFile` | `{vault, path}` | `{text, exists}` — only for the journalist-owned files: `context.md`, `watchlist.md`, `requests.md`, `hot.md`, `log.md`, `timeline.md`, `index.md`, and anything under `briefings/`, `queries/`, `wiki/` |
| `vault.writeFile` | `{vault, path, text}` | `{ok}` — only `context.md` and `watchlist.md` |
| `vault.requests` | `{vault}` | `{open: [{rid, type\|null, what, why\|null, likely_source\|null, cited_in: [{sha, filename, note}], added\|null}], resolved_count}` |
| `vault.contextFiles` | `{vault}` | `[{name, size, modified}]` in `context/` |
| `vault.migrate` | `{vault}` | `{changes: string[]}` — renames an older vault's `_INCOMING/`/`_CONTEXT/` folders to `incoming/`/`context/` (D266); opening a vault does this once automatically |

```
DocumentDetail = DocumentRow & {
  frontmatter: object, body: string,            // the document note
  facts: [{fact, page|null, basis: "stated"|"inferred", date|null, quote|null,
           entities: [{id, name, type}], figure_note|null, quote_note|null, added_by|null}],   // quote_note: quote-verification warning; facts fall back to the note's `## Key facts` bullets when no staged extraction exists
 
  entities: [{id, name, type, role|null}],
  pages: [{page, text}],                        // morgue full text split on <!-- PAGE n -->
  file_metadata: object, sidecar: object|null, metadata: object|null,
  extract_model|null, extract_effort|null, record_skill_hash|null,
  duplicates: [{sha, filename, note}]           // documents this one nearly duplicates, both ways
}

EntityDetail = EntityRow & {
  frontmatter: object, body: string,            // the entity note
  sections: {summary|null, analysis|null, contradictions|null, timeline|null,
             relationships|null, notes|null},   // raw markdown per ## section
  documents: DocumentRow[],                     // appears_in, resolved
  relationships: [{role, target_id, target_name|null, target_type|null, direction: "out"|"in",
                   docs: string[]}],
  contradictions: [{rid, summary, text, resolved: bool}],
  timeline: TimelineEvent[]
}

TimelineEvent = {
  date, precision: "day"|"month"|"year"|null, text,
  entities: [{id, name, type}], sha|null, filename|null, page|null, note|null
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
| `action.run` | `{vault\|null, args: string[], timeout?: seconds}` | `{code, stdout, stderr}` — a short, synchronous command (rename, archive, unlock…) |

```
Job = { id, label, kind, vault|null, args, state: "running"|"done"|"failed"|"cancelled",
        exit_code|null, started, finished|null, progress: ProgressState }
LogLine = { t, stream: "out"|"err", text }
ProgressState = { stage|null, done|null, total|null, current|null, docs: {[sha]: {filename, state, detail|null}} }
```

Events: `job.started {job}`, `job.log {id, lines: LogLine[]}` (batched ≤ 10/s),
`job.progress {id, progress: ProgressState, event}`, `job.finished {job}`.

## search

| Method | Params | Result |
|---|---|---|
| `search.query` | `{vault, query, top?: 5, threshold?: number\|null, rerank?: true}` | `{query, index_empty: bool, exact_error\|null, semantic_error\|null, exact: [...], passages: [...], notes: [...]}` — the `--json` shape, each item enriched with `sha`, `note`, `original` where resolvable |
| `search.batch` | `{vault\|null, terms: string[], everywhere?: bool}` | `{terms: [{term, checked: bool, hits: [{vault_name?, kind, title, note, page, text, path, sha}]}]}` — entity matches are hits with `kind: "entity"`; `top?` limits hits per term |
| `search.everywhere` | `{query, top?}` | `{vaults: [{slug, name, path, entity_hits: [...], exact: [...], error\|null}], skipped: [{slug, reason}]}` |
| `search.status` | `{vault}` | `{total, documents, notes, passages, fulltext: {corpus, notes, total}}` — `embed.index_stats` (`documents` = indexed document files) |

## review

```
ReviewItem = {kind: "contradictions"|"leads"|"alerts"|"duplicates", rid, title, detail: string[], note|null}
```

| Method | Params | Result |
|---|---|---|
| `review.items` | `{vault, kinds?: string[]}` | `{items: ReviewItem[], counts: {[kind]: n}}` — `cmd/review.open_items` |
| `review.resolve` | `{vault, rids}` | `{resolved: string[]}` — `resolutions.resolve(label="review")` + `tick_in_briefings`, exactly as the terminal walk does |
| `review.unresolve` | `{vault, rids}` | `{unresolved: string[]}` |
| `review.resolved` | `{vault}` | `{items: [{rid, label, resolved_at, kind}]}` — what `review resolve --list` shows |
| `review.sync` | `{vault}` | `{resolved: string[], unresolved: string[]}` — `resolutions.sync_from_briefings` |
| `review.leads` | `{vault}` | the full lead sweep, `leads.scan` made JSON-safe, plus `total` |
| `review.watchlist` | `{vault}` | `{terms: string[], text}` |
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
| `setup.check` | — | `{deps: [{label, ok, hint\|null}], playwright: bool, gliner_model: bool, projects_dir\|null, config_exists: bool}` |

```
SettingKey = {key, short, help, is_set: bool, default, current (null for secrets), display, kind: "bool"|"int"|"float"|"choice"|"model"|"effort"|"path"|"text"|"secret", choices: string[]|null}
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
