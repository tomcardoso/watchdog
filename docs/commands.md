# Command reference

This page lists every Watchdog command, what it does, and every option it takes. Use it as a lookup — the guided walkthroughs live in [Getting started](getting-started.md) and [Investigating](investigating.md). Every command here runs in your terminal, except the slash commands at the end, which run inside a Claude Code session. Add `--help` after any command (for example `watchdog ingest --help`) to see its usage in the terminal.

## Investigation management

| Command | What it does |
|---|---|
| `watchdog new [name]` | Create a new investigation vault; omit the name to be prompted, or pass `--description "text"` and `--dir <path>` to set the description and parent directory up front. |
| `watchdog obsidian [name]` | Open the vault in Obsidian; omit the name when you are inside the vault directory. |
| `watchdog open [name]` | Open the vault folder in Finder or your file explorer; omit the name when inside the vault directory. |
| `watchdog list` | List all active investigations; `--all` includes archived ones. |
| `watchdog status [name]` | Show detailed status for one investigation, or all of them when the name is omitted. |
| `watchdog log [name]` | Show the ingest history; `--lines N` shows only the last N lines. |
| `watchdog archive <name>` | Mark an investigation complete, hiding it from `watchdog list`. |
| `watchdog unarchive <name>` | Restore an archived investigation. |
| `watchdog rename [name] [new-name]` | Rename an investigation, updating the folder, registry, and Obsidian entry; omit the current name when inside the vault, and omit the new name to be prompted. |
| `watchdog describe [name] ["text"]` | Set or update an investigation's one-line description; omit the text to be prompted. |
| `watchdog move <name> <path>` | Move the vault to a new path and update the registry; if the files are already at the new path, it just updates the registry. |
| `watchdog delete <name>` | Remove an investigation from the registry, leaving the vault files on disk; `--purge` also permanently deletes the files. |
| `watchdog register [path]` | Register an existing vault folder with Watchdog; omit the path when inside the vault, and pass `--name` to set the name without being prompted. |

Investigation names tab-complete in zsh and bash once `watchdog setup` has run.

## Processing

| Command | What it does |
|---|---|
| `watchdog fetch <url…>` | Download one or more URLs (or a links file) into `_INCOMING/` — see [below](#watchdog-fetch). |
| `watchdog chew` | Convert everything in `_INCOMING/` into extracted text queued for ingest — see [below](#watchdog-chew). |
| `watchdog ingest` | Extract all queued documents into the vault — see [below](#watchdog-ingest). |
| `watchdog extract` | Classify and extract queued documents into staged artifacts, then stop — see [below](#watchdog-extract). |
| `watchdog finalize` | Complete the post-ingest step (merging duplicate entities, flagging contradictions between documents, entity synthesis, timeline, briefing) for a batch that was interrupted after extraction, or deliberately staged with `watchdog extract`; takes the same `--finalizer-model`, `--finalizer-effort`, `--estimate`, and `--skip-briefing` as ingest. |
| `watchdog requeue` | Move documents quarantined in `queue/_failed/` back into the active queue, ready for the next `watchdog ingest`. |
| `watchdog context [name]` | Open Claude Code with the context-seeding skill, which reads `_CONTEXT/`, interviews you, and writes `context.md`; `--model` picks `sonnet`, `opus`, or `haiku` (default: `sonnet`). |
| `watchdog watch [name]` | Watch `_INCOMING/` and chew files automatically as they arrive. |
| `watchdog [--skip-briefing]` | With no subcommand inside a vault: walk the pipeline — offering to seed context, chew, then ingest — skipping any stage with no pending work. `--skip-briefing` carries through to the ingest step if the walk reaches it. |

### watchdog ingest

`watchdog ingest` runs the extraction pipeline in your terminal — no Claude Code session is involved. It scans the queue, prints a token estimate, then shows the public-records warning below and asks you to acknowledge it before extracting documents in parallel. On a metered API key with at least one prior run, the estimate includes a rough dollar range projected from this vault's own usage history (the last three runs); on a subscription, or before any run has completed, only the token estimate is shown. The token estimate itself sharpens the same way: once a vault has extracted at least one batch, later estimates are scaled by how far this vault's own recent extractions ran over or under the raw estimate, rather than a fixed guess — a new vault falls back to the raw estimate until it has that history.

**Public records only.** Every ingest that will call the model shows this warning and requires an explicit acknowledgement — the default choice — before anything is sent:

```
  ⚠  Public records only

  The extracted text of every queued document will be sent to a
  cloud AI model. This cannot be undone. Use Watchdog only for
  documents that are public, or presumptively public — never for
  confidential source material, leaks, or anything that could
  identify a source.

  6 documents will be sent to the model.

    ›  Acknowledge and ingest
       Cancel
```

It's shown every time, not just once per vault — the risk is per-document, not per-vault — and it replaces the plain "Ingest now?" prompt rather than adding a second one. `--skip-warning` skips the interactive pause, for repeated or scripted runs on a corpus already vetted as public (benchmarking, `--wait` batches, automation); it still prints a one-line notice naming how many documents were sent, so a skipped run is never silent about what it did. It is a per-invocation flag rather than a `watchdog configure` setting — a persistent "never warn me" default would quietly defeat the safeguard for every future run. `watchdog extract` takes the same flag, for the same reason.

**Model and effort flags.** Each takes effect for this run only; the persistent defaults live in [Configuration](configuration.md).

- `--extractor-model MODEL` — the model that extracts each document (default: `sonnet`).
- `--finalizer-model MODEL` — the model for the post-ingest step: merging duplicate entities, flagging contradictions between documents, entity synthesis, timeline reconciliation, and the briefing (default: `haiku`).
- `--classifier-model MODEL` — the model that reads a document's first pages and picks its record skill (default: `haiku`).
- `--extractor-effort low|medium|high` — how hard the extractor thinks; lower spends fewer tokens (default: `high`).
- `--finalizer-effort low|medium|high` — the same knob for the post-ingest step (default: `high`).

Each model flag takes a Claude tier (`haiku`, `sonnet`, `opus`) or a `backend:model` value that routes the stage to another provider — see [Model backends](configuration.md#model-backends). The effort flags are ignored when the stage runs on Haiku, which has no effort control.

**Scope and behaviour flags.**

- `--concurrency N` — documents extracted in parallel (default: 5); lower it if you hit rate limits.
- `--classify-pages N` — leading pages shown to the classifier (default: 5); more pages classify ambiguous documents better.
- `--skill NAME` — pin one record skill for every document, skipping classification; pass a skill name or a path to a skill file, or use `--skill` with no value to pick from a list. A document's own sidecar can pin a different skill for just that document — see [Skills](skills.md#reading-and-pinning-skills).
- `--wait` — on a rate limit, sleep until it resets and resume automatically instead of stopping; for unattended overnight batches. It uses the reset time the provider reports, or a fixed fallback interval when it doesn't, and repeats until the queue drains. Not compatible with a `claude-batch` extractor model.
- `--estimate` — print the token and cost estimate for the queue and exit; no lock, no confirmation, no extraction.
- `--force [DOC …]` — re-extract a document even when a cached extraction (or a note already committed to the vault) exists for it — see [Re-extracting with --force](#re-extracting-with---force) below. Costs full extraction spend on every queued document, cache or no. Bare `--force` re-extracts whatever is already queued; naming one or more documents (a sha256, an unambiguous sha256 prefix, or a filename) also re-queues and re-extracts documents already committed to the vault.
- `--skip-warning` — skip the public-records acknowledgement pause described below; still prints a one-line notice of what was sent.
- `--skip-briefing` — finalize as usual (merging duplicate entities, flagging contradictions, entity synthesis, timeline reconciliation) but skip the briefing model call. Useful for bulk backfills or re-ingests where a briefing isn't worth its cost every time. `hot.md` and that run's entry in `log.md` are only written alongside a briefing, so both are skipped too — the run still ends with `briefings/leads-<date>.md`, `requests.md`, and `watchlist.md` alerts, which don't depend on the briefing. Also available on `watchdog finalize --skip-briefing`, and as a top-level `watchdog --skip-briefing` when the bare guided walk reaches ingest.

**Resumability.** Pressing Ctrl+C, or hitting a rate limit without `--wait`, stops the batch cleanly: finished documents are saved and unfinished ones stay queued, so re-running `watchdog ingest` picks up where it left off. A document that genuinely fails extraction is set aside in `queue/_failed/`; the run reports how many, and `watchdog requeue` moves them back to retry — this is surfaced everywhere the queue's state matters: the normal run summary, a Ctrl+C even during the finalize wrap-up below, `--estimate`, and a bare `watchdog ingest` with nothing new to read, which offers to requeue and retry right there instead of just reporting an empty queue. On macOS or Linux, ingest also keeps the machine from sleeping for the run's duration — see [Troubleshooting](troubleshooting.md#ingest-prevents-the-machine-from-sleeping-during-a-run).

**Finalization.** Documents land in the vault — entity and document notes, the registry — at the start of this step, not progressively as each one extracts; extraction only stages its output durably. The step then continues with merging duplicate entities, flagging contradictions between documents, entity synthesis, timeline reconciliation, and the briefing. If it's interrupted (for example, a rate limit hits after the documents extract), the batch is left finalizable: `watchdog status` flags it, and `watchdog finalize` completes it without re-extracting anything. If you start another `watchdog ingest` while a batch is pending, it asks what to do: **merge** the pending batch into the new run and finalize everything together, **finalize** it first and stop, or **discard** it. Running `watchdog extract` instead of `watchdog ingest` leaves a batch in this same finalizable state deliberately, instead of as a side effect of an interruption — see below.

`watchdog finalize --estimate` prints a token/cost estimate for the pending batch and exits — no lock, no finalize — the same read-only contract as ingest's own `--estimate`. It prices the batch already staged in the vault's working files rather than a queue, so the dollar figure draws only on this vault's history of *standalone* `watchdog finalize` runs (a finalize that ran as part of a normal `watchdog ingest` doesn't count, since its cost is mixed in with extraction). A vault that has only ever finalized as part of `watchdog ingest` — never on its own — gets the token count with no dollar figure, until it has that history.

#### Comparing finalizer models

Extraction is the expensive part of an ingest — the finalizer's few calls (reconciliation, synthesis, timeline, briefing) cost little by comparison. To try more than one finalizer model or effort level against the *same* extraction, without paying for extraction again each time:

1. Run `watchdog extract`. Documents extract and stage durably, but nothing is written to the vault and post-processing does not run — `watchdog status` will show the batch as pending finalization.
2. Run `watchdog finalize --finalizer-model <model>` to try one candidate. Do this from inside the vault, or from a copy of the vault folder if you want to test several candidates against the identical extraction — each copy still has the same staged inputs, so pointing a different `--finalizer-model` at each copy compares them fairly.

`watchdog finalize` does not yet support re-running cleanly over a batch it has already finalized — run it once per vault (or vault copy).

#### Re-extracting with --force

Ordinarily, once a document has been extracted — its output staged, whether or not it has reached the vault yet — `watchdog ingest` and `watchdog extract` skip it rather than spending another extraction call on the same bytes. `--force` overrides that, for when you want to re-run a document (or a whole corpus) under a different extractor model, effort level, or record skill.

What `--force` does depends on the document's state:

- **Staged but not yet in the vault** — `watchdog ingest --force` and `watchdog extract --force` both simply re-extract it, overwriting the staged output. Nothing has been written to the vault yet, so there is nothing to confirm.
- **Already committed to the vault** — name the document as an argument to `--force` (`watchdog ingest --force report.pdf`, or a sha256/sha256 prefix). Watchdog re-chews the original from the morgue — the same OCR pass `watchdog chew` would run, just pointed at the archived file instead of `_INCOMING/`, so it costs nothing beyond the usual time — and re-queues it, then re-extracts it. Because re-extracting only matters if the resulting note actually replaces the old one, `watchdog ingest --force <document>` then lists the vault notes about to be replaced and asks you to confirm before finalizing. Unlike the routine ingest confirmation, this one **defaults to Cancel** — replacing a note that already carries your analysis is not something to do by accident. Cancelling leaves the re-extracted batch staged; run `watchdog finalize` later to complete it once you are ready.

A document name can be its sha256 (or an unambiguous prefix of one), or its original filename — Watchdog looks it up in the registry either way. Naming a document that isn't a committed document at all is a clear error, not a silent no-op. You can name more than one:

```
watchdog ingest --force report.pdf disclosure-2024.pdf
```

`watchdog extract --force` does not take document names, and never shows the overwrite confirmation — `extract` never touches the vault, so there is nothing on record to replace, and no way to strand a re-queued document past extraction (a plain `watchdog finalize` afterward wouldn't know to recommit it). If you want to regenerate a committed document's note, use `watchdog ingest --force <document>`, which re-chews, re-extracts, and finalizes it as one step.

`--estimate` is read-only — no lock, no confirmation, no extraction — so naming a document alongside it (`watchdog ingest --force report.pdf --estimate`) does not re-chew or re-queue anything; Watchdog prints a note that the estimate reflects the current queue only, rather than silently ignoring the name. Drop `--estimate` to actually re-queue and re-extract it.

### watchdog extract

`watchdog extract` runs classification and extraction exactly like `watchdog ingest` — same queue, same extractor and classifier models — but stops as soon as the batch is staged. Nothing is written to the vault, and post-processing (merging duplicate entities, flagging contradictions, entity synthesis, timeline reconciliation, the briefing) does not run.

It takes the extraction-side flags from `watchdog ingest`: `--extractor-model`, `--extractor-effort`, `--classifier-model`, `--concurrency`, `--classify-pages`, `--skill`, `--wait`, `--estimate`, `--skip-warning`, and `--force` (bare only here — see [Re-extracting with --force](#re-extracting-with---force) for why `extract --force` doesn't take document names). There is no `--finalizer-model` or `--finalizer-effort` here — those belong to `watchdog finalize`, run afterward to complete the batch. It shows the same public-records acknowledgement gate as `watchdog ingest`, since it makes the same model calls.

Use `watchdog extract` when you want to inspect what got extracted before it lands in the vault, or to compare finalizer models against a fixed extraction without paying for extraction again — see [Comparing finalizer models](#comparing-finalizer-models) above. `watchdog status` shows a staged batch as pending finalization until you run `watchdog finalize`.

### watchdog chew

`watchdog chew` does the local preprocessing: it converts each file in `_INCOMING/` to structured text, applies OCR to scanned documents, splits large PDFs into chunks processed in parallel, and checks for duplicates. The extracted text is queued in `.watchdog/queue/` and the original file moves to `.watchdog/staging/`. Nothing is sent to a model during chewing.

```bash
watchdog chew                        # everything in _INCOMING/
watchdog chew path/to/file.pdf       # one specific file
```

Two flags override the persistent parallelism settings for a single run:

- `--chew-workers N` — files processed in parallel (the `chew_workers` setting; default: adaptive).
- `--chunk-workers N` — parallel chunks per large PDF (the `chunk_workers` setting; default: adaptive).

Press Ctrl+C to cancel a chew in progress — the lock is cleaned up automatically and unfinished files remain in `_INCOMING/` for the next run. When the batch completes, Watchdog sends a desktop notification (macOS only) and offers to ingest right away, so you can move straight to extraction without typing the next command — that offer is the same public-records acknowledgement gate described under [`watchdog ingest`](#watchdog-ingest) below, not a separate confirmation.

### watchdog fetch

`watchdog fetch` downloads a batch of URLs into `_INCOMING/` — for when you already have the links and don't need a research session. Each URL is validated, size-capped, and saved with a provenance sidecar, and Wayback Machine archiving applies if you have [configured it](configuration.md).

```bash
watchdog fetch https://example.gov/filing https://news.example/article
watchdog fetch links.txt
```

A links file holds one URL per line, or the tab-separated `url⇥title⇥source_type⇥relevance` form. Pass `--project <name>` to target a vault you are not currently inside.

HTML pages get a full rendered snapshot — images, styles, client-rendered content — when the optional capture browser is installed, and fall back to a sanitized plain fetch otherwise; see [Install](install.md) for the optional install. After fetching, run `watchdog chew` and `watchdog ingest` as usual.

## Info and settings

| Command | What it does |
|---|---|
| `watchdog search <name> "<query>"` | Search ingested documents by meaning and exact terms — see [below](#watchdog-search). |
| `watchdog leads [name]` | Print the deterministic lead sweep over the entity graph — see [below](#watchdog-leads). |
| `watchdog resolve <id…>` | Acknowledge leads, alerts, or contradictions so reports stop re-surfacing them — see [below](#watchdog-resolve-and-unresolve). |
| `watchdog unresolve <id…>` | Bring acknowledged items back into the active list. |
| `watchdog merge-entities <keep-id> <merge-id>` | Fold a duplicate entity into another — see [below](#watchdog-merge-entities). |
| `watchdog timeline [name]` | Rebuild `timeline.md` from the canonical event files; deterministic, no model call. |
| `watchdog reindex [name]` | Rebuild the search indexes from disk — see [below](#watchdog-reindex). |
| `watchdog research [name]` | Open Claude Code to research the vault's open questions on the web — see [below](#watchdog-research). |
| `watchdog watchlist [name]` | Sweep every already-ingested document against the current `watchlist.md` — see [below](#watchdog-watchlist). |
| `watchdog usage [name]` | Per-call token/cost/latency breakdown for ingest runs — see [below](#watchdog-usage). |
| `watchdog export [name]` | Export the entity and relationship graph for network-analysis tools — see [below](#watchdog-export). |
| `watchdog doctor` | Check all registered investigations for missing or broken vaults, suggesting `watchdog move` or `watchdog delete` for each issue. |
| `watchdog auth` | Show or change how Watchdog authenticates to model providers, interactively — see [below](#watchdog-auth). |
| `watchdog unlock [name]` | Release a stale chew or ingest lock; `--force` removes it even if recent. |
| `watchdog setup` | Set up Watchdog after installation; `--force` re-runs it. |
| `watchdog refresh-skills [name]` | Update a vault's Claude Code command skills after a Watchdog upgrade. |
| `watchdog show-skills [name]` | List the record skills, or print one in full. |
| `watchdog about` | Show the installed version and project links. |
| `watchdog configure [key] [value]` | View or change settings — the full reference is in [Configuration](configuration.md). |

### watchdog search

`watchdog search` finds material by meaning as well as by exact wording, and prints results in three sections: **exact matches** (every literal occurrence of the term, from a local full-text index, with a page link back to the source), **source passages** (ranked by meaning and by exact terms, then reranked locally), and **notes** (what the investigation has concluded). How to use it well — steering with `+`/`-` phrases, quoted phrases for exact matching — is covered in [Investigating](investigating.md).

```bash
watchdog search my-investigation "shell company -real estate"
```

Flags:

- `--top N` — results per section (default: 5).
- `--threshold S` — hide semantic results scoring below S (0.0–1.0).
- `--no-rerank` — skip the local reranking step; faster, lower quality.
- `--full` — print the complete passage or note instead of a snippet.
- `--batch FILE` — read terms from a file (one per line) and report hits per term instead of ranking a single query; useful for checking a list of names.
- `--everywhere` — search every registered, non-archived investigation instead of one; only the entity-lookup and exact-match lanes run (semantic ranking doesn't scale across vaults), results are grouped by investigation, and vaults with a broken path are skipped. Combine with `--batch` to check a term list across every vault.
- `--json` — machine-readable output.

Omit the project name when running from inside the vault directory; with `--everywhere`, no project name is used at all.

### watchdog leads

Prints the deterministic lead sweep over the vault's entity graph — no model call. It flags four things: entities named as a relationship target but never profiled, entities recurring across documents with no relationships, entities carrying unresolved contradiction flags, and entities carrying facts or roles marked as inferred. The same sweep runs automatically at the end of every ingest, writing `briefings/leads-<date>.md`; this command re-runs it on demand. Items acknowledged with `watchdog resolve` drop out of the list. See [Investigating](investigating.md) for how leads fit the working rhythm.

### watchdog resolve and unresolve

Every item in the leads, alerts, and document-request reports carries a short resolution id (for example `lead:isolated:acme`, or `request:1a2b3c4:9f8e7d6c` for a document request). Run `watchdog resolve <id…>` from inside the vault to acknowledge items so the deterministic reports stop re-surfacing them. Two flags change the mode: `--sync` imports any `- [x]` checkboxes you have ticked in the `briefings/` files or the vault-root `requests.md` instead of taking ids, and `--list` shows what is currently acknowledged. `watchdog unresolve <id…>` is the inverse, bringing items back into the active list. Acknowledgments are stored in the vault's registry and follow an entity through `watchdog merge-entities`.

### watchdog merge-entities

Folds a duplicate entity into another when the same real-world person or company was extracted under two ids. It unions aliases, document appearances, roles, and timeline events onto the surviving entity; remaps every relationship anywhere in the registry that targeted the losing id; carries the losing entity's Analysis section over with provenance intact; and redirects the losing note to a stub pointing at the survivor.

```bash
watchdog merge-entities <keep-id> <merge-id>
```

The command prints both entities and asks for confirmation, since the merge is irreversible; `--force` skips the prompt. Run `watchdog reindex` afterward to drop the merged entity's stale search-index entries. When both entities had a prose Summary, it prints a reminder to run `/watchdog-entity <keep-id>` in a Claude Code session, which re-synthesizes the survivor from every merged source. See [Investigating](investigating.md) for when duplicates happen and how to spot them.

### watchdog timeline

Rebuilds `timeline.md` from the canonical event files in `.watchdog/timeline/` — deterministic, no model call. Useful if the rendered timeline is ever deleted or edited by mistake; nothing is lost, because the note is a generated output.

### watchdog reindex

Rebuilds the vault's semantic and full-text search indexes from what is already on disk — no OCR re-run, no model calls, no tokens. It reads the registries and the full extracted text stored in the morgue and rebuilds every index entry from scratch. Run it after changing `embed_model` in [Configuration](configuration.md), since vectors from two different embedding models can't be mixed; a `rerank_model` change needs no reindex, because reranking runs fresh at query time. Documents ingested by very old Watchdog versions that left no full text in the morgue are skipped and reported; their notes still reindex.

### watchdog research

Opens Claude Code to research the vault's open questions on the web. Claude queues the sources it finds rather than writing anything to the vault; when the session ends, Watchdog downloads them into `_INCOMING/` so the findings flow through the normal chew-and-ingest pipeline. `--question "<q>"` (or `-q`) seeds a research question, and `--model` overrides the model (`sonnet`, `opus`, or `haiku`; default: `sonnet`). The full treatment — effort tiers, interrupted-session recovery, what research deliberately does not do — is in [Investigating](investigating.md).

### watchdog watchlist

Sweeps every already-ingested document against the current `watchlist.md` — deterministic, no model call. The scan that runs automatically at the end of each ingest only sees that run's documents, so a term added afterward is never checked against the existing corpus; this command covers the whole vault instead, writing to the same `briefings/alerts-<date>.md`. Terms already acknowledged with `watchdog resolve` don't re-report. The watchlist format and workflow live in [Investigating](investigating.md).

### watchdog usage

Prints a per-call token, cost, and latency breakdown for ingest runs, reading `.watchdog/registry/usage/usage-<ts>.json` — no model call. Calls are grouped by stage (classifier/extractor/finalizer), and extraction rows show the filename and the page range or section each call covered, plus cost per page across the vault's whole document registry. Each stage's subtotal shows the summed per-call latency; when that stage's calls overlapped in time (documents extract in parallel), a second line reports the wall-clock elapsed — the real time the stage took — so the summed figure is not mistaken for how long you waited. The run total shows both figures side by side. A call that never returned usable output appears as its own row, marked `✗ failed`, so the tokens and cost it spent before failing are still counted in the totals rather than disappearing. Shows the latest run by default; `--all` compares every recorded run, and `--run TIMESTAMP` inspects one specific past run. Also available as `watchdog telemetry`.

Each call's usage is written to disk as soon as it completes, not just when the run finishes — so a crash, a hard interrupt, or a stop mid-finalize still leaves that run's spend on record. If a run never reaches a clean end, its in-progress file is folded into a normal recorded run the next time you run `watchdog ingest` or `watchdog finalize`, and shows up in `watchdog usage` from then on.

### watchdog export

Exports the investigation's entity and relationship graph for network-analysis tools. The default writes Neo4j-import CSV (`nodes.csv` and `relationships.csv`, also loadable in Gephi); `--format cypher` writes a single `graph.cypher` of `MERGE` statements instead, and `--output DIR` sets the destination (default: `<slug>-export/`). The export is deterministic — it reads the registry, with no model calls. Only stated-direction relationships are exported (auto-generated reverse edges are skipped), and edges to never-profiled entities are dropped so the import stays valid.

### watchdog auth

Shows how Watchdog currently authenticates to model providers, then, on a terminal, offers to change it.

- `watchdog auth` — prints a **Claude Code** section (subscription/api-key mode, Claude Code login detection — Claude Code is required for the interactive investigation commands and is the ingestion default), an **Ingestion** section showing which provider each of `classifier_model`/`extractor_model`/`finalizer_model` currently resolves to and whether that provider is ready (✓/✗), and a **Provider keys** section listing every stored key, masked, and marked `(in use)` or `(unused)` depending on whether a stage is routed to it. Off a terminal, it stops there.
- On a terminal it then asks **"Change something?"** — choose **Done — nothing to change** to leave, or pick a service (Anthropic, OpenAI, DeepSeek, or Gemini):
  - For **Anthropic**, choose between your Claude Code subscription (not metered) and a metered API key.
  - For **OpenAI**, **DeepSeek**, or **Gemini**, store a new key, replace an existing one, or delete it.

There is no separate `set`/`get`/`use`/`remove` subcommand — this one interactive flow covers all of it. Keys can also come from the standard environment variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `GEMINI_API_KEY`), which always take precedence over a stored key. Routing a pipeline stage to another provider is covered in [Model backends](configuration.md#model-backends).

### watchdog unlock

Releases a stale lock left behind by an interrupted chew or ingest — both lock types are checked. A lock that looks recent is left alone unless you pass `--force`. Run it from inside the vault, or pass the investigation name.

### watchdog setup

The one-time setup after installation: it verifies system dependencies, configures your projects directory, sets up Claude authentication, offers the optional capture browser, downloads the local models used for document conversion and search, and enables shell tab completion. `--force` re-runs it after it has already completed. The step-by-step walkthrough is in [Install](install.md).

### watchdog refresh-skills

Updates a vault's Claude Code command skills (the `/watchdog-*` commands) after upgrading Watchdog. Record skills are global — read straight from the installed package — so they never need refreshing; only the per-vault command skills do.

### watchdog show-skills

With no argument, lists every record skill with a one-line description, prints where to add your own, and opens the skills folder on GitHub so the full text is easy to read. Pass a skill name to print that skill in full in the terminal. The skills themselves are covered in [Skills](skills.md).

### watchdog about

Prints the installed version, plus links to the project's GitHub page, issue tracker, and install guide.

### watchdog configure

Views and changes Watchdog's settings — run it with no arguments to see everything, or `watchdog configure <key> <value>` to set one. The full key reference, model backends, and cost guidance are in [Configuration](configuration.md).

## Slash commands

These run inside a Claude Code session with your investigation open — they are interactive, multi-turn, and always run on Claude. Extraction is not a slash command; run `watchdog ingest` in your terminal instead.

| Command | What it does |
|---|---|
| `/watchdog-query [question]` | Answer a question from your vault, with sources. |
| `/watchdog-surface` | Find connections and anomalies across the full vault. |
| `/watchdog-entity [id…]` | Refresh an entity's Summary and Timeline from all its source documents. |
| `/watchdog-wiki` | Create or update investigation thread pages in `wiki/`. |
| `/watchdog-context` | Seed `context.md` from background files in `_CONTEXT/` (launch with `watchdog context`). |
| `/watchdog-health` | Check vault integrity — orphaned notes, broken links, registry mismatches, unresolved contradictions, unreviewed near-duplicates. |
| `/watchdog-research [question]` | Research open questions on the web, queuing sources for download into `_INCOMING/` (launch with `watchdog research`). |

Query examples:

```
/watchdog-query Who are the directors of Shell Co Ltd?
/watchdog-query Which companies share the address 123 Main St?
/watchdog-query What happened in 2019 involving Alice Smith?
/watchdog-surface
```

Where next: [Configuration](configuration.md) for every setting and its default, or [Investigating](investigating.md) for how these commands fit day-to-day work.
