# Command reference

This page lists every Watchdog command, what it does, and every option it takes. Use it as a lookup — the guided walkthroughs live in [Getting started](getting-started.md) and [Investigating](investigating.md). Every command here runs in your terminal, except the slash commands at the end, which run inside a Claude Code session. Add `--help` after any command (for example `watchdog dig --help`) to see its usage in the terminal.

## Colour output

Watchdog colours some of its terminal output — project names in bold, file paths and commands in cyan, warnings in yellow — to make status easier to scan. This turns on automatically when you're looking at a real terminal, and off automatically when you're not: redirecting output to a file (`watchdog list > projects.txt`) or piping it into another program never includes colour codes, so the text you get stays clean either way.

If you'd rather never see colour, set the standard `NO_COLOR` environment variable to any non-empty value and Watchdog will leave it off everywhere, including in the terminal.

## Exit codes

If you're running Watchdog from a script or a scheduled job, the process exit code tells you how a run went without parsing any text: `0` means it completed and there's nothing left to do; `1` marks a genuine error, such as bad input or a setting that still needs configuring; `130` means it was interrupted with Ctrl+C.

`watchdog dig` and `watchdog bark` use one more code: `2` means the run stopped partway through in a way a re-run picks up automatically — a rate limit paused it, a submitted batch is still waiting on results, some documents were never started, or `bark`'s own post-processing (entity reconciliation, synthesis, the briefing) didn't finish. Running the same command again continues from where it left off. A document that failed extraction and was set aside in `queue/_failed/` doesn't trigger this — that's a completed run with an outcome worth reviewing, not a stalled one. `watchdog requeue` is the fix, and the exit code for that run stays `0`.

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
| `watchdog dig` | Classify and extract queued documents into staged artifacts — see [below](#watchdog-dig). |
| `watchdog bark` | Complete the post-ingest step (merging duplicate entities, flagging contradictions between documents, entity synthesis, timeline, briefing) for a batch staged by `watchdog dig`, or one an interruption left half-done; takes `--finalizer-model` (and its four per-stage overrides), `--finalizer-effort`, `--estimate`, `--estimate-all`, and `--skip-briefing` — see [below](#watchdog-bark). |
| `watchdog requeue` | Move documents quarantined in `queue/_failed/` back into the active queue, ready for the next `watchdog dig`. |
| `watchdog context [name]` | Open Claude Code with the context-seeding skill, which reads `_CONTEXT/`, interviews you, and writes `context.md`; `--model` picks `sonnet`, `opus`, or `haiku` (default: `sonnet`). |
| `watchdog watch [name]` | Watch `_INCOMING/` and chew files automatically as they arrive. |
| `watchdog [--skip-briefing]` | With no subcommand inside a vault: walk the pipeline — offering to seed context, chew, then dig and bark — skipping any stage with no pending work. `--skip-briefing` carries through if the walk reaches that step. |

`watchdog dig` and `watchdog bark` are the two halves of what used to be one `watchdog ingest` command (renamed in favour of the guided `watchdog` walk plus these two manual-control stages — see [`watchdog ingest` (deprecated)](#watchdog-ingest-deprecated) below).

### watchdog dig

`watchdog dig` runs classification and extraction in your terminal — no Claude Code session is involved. It scans the queue, prints a token estimate, then shows the public-records warning below and asks you to acknowledge it before extracting documents in parallel. On a metered API key with at least one prior run, the estimate includes a rough dollar range projected from this vault's own usage history (the last three runs); on a subscription, or before any run has completed, only the token estimate is shown. The token estimate itself sharpens the same way: once a vault has extracted at least one batch, later estimates are scaled by how far this vault's own recent extractions ran over or under the raw estimate, rather than a fixed guess — a new vault falls back to the raw estimate until it has that history.

`dig` stops as soon as the batch is staged: nothing is written to the vault, and post-processing (merging duplicate entities, flagging contradictions, entity synthesis, timeline reconciliation, the briefing) does not run — that's `watchdog bark`, [below](#watchdog-bark). `watchdog status` shows a staged batch as pending finalization until you run it.

**Public records only.** Every `dig` that will call the model shows this warning and requires an explicit acknowledgement — the default choice — before anything is sent:

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

It's shown every time, not just once per vault — the risk is per-document, not per-vault — and it replaces the plain "Ingest now?" prompt rather than adding a second one. `--skip-warning` skips the interactive pause, for repeated or scripted runs on a corpus already vetted as public (benchmarking, `--wait` batches, automation); it still prints a one-line notice naming how many documents were sent, so a skipped run is never silent about what it did. It is a per-invocation flag rather than a `watchdog configure` setting — a persistent "never warn me" default would quietly defeat the safeguard for every future run. (See [Benchmarks](benchmarks.md) for how this flag is used to run the model-comparison suite unattended.)

**Model and effort flags.** Each takes effect for this run only; the persistent defaults live in [Configuration](configuration.md).

- `--extractor-model MODEL` — the model that extracts each document (default: `sonnet`).
- `--classifier-model MODEL` — the model that reads a document's first pages and picks its record skill (default: `haiku`).
- `--extractor-effort low|medium|high|xhigh|max` — how hard the extractor thinks; lower spends fewer tokens (default: `medium`). `xhigh`/`max` need a model that supports them — see [Controlling cost](configuration.md#controlling-cost).

Each model flag takes a Claude tier (`haiku`, `sonnet`, `opus`) or a `backend:model` value that routes the stage to another provider — see [Model backends](configuration.md#model-backends). Left unset, `--extractor-effort` defaults to `medium` only when the extractor supports it, and is skipped automatically for one that doesn't (Haiku); set explicitly, a level the resolved model doesn't support — including `xhigh`/`max` on a model that doesn't reach them — fails with a clear error rather than running silently at a different effort than you asked for.

The "which model runs each stage" line printed before extraction starts shows only classifier and extractor for `dig` — no finalizer row, since `dig` never finalizes in the run it's invoked from; that row appears for `watchdog bark` and the bare guided walk instead, both of which do finalize inline.

**Scope and behaviour flags.**

- `--concurrency N` — documents extracted in parallel (default: 5); lower it if you hit rate limits.
- `--classify-pages N` — leading pages shown to the classifier (default: 5); more pages classify ambiguous documents better.
- `--skill NAME` — pin one record skill for every document, skipping classification; pass a skill name or a path to a skill file, or use `--skill` with no value to pick from a list. A document's own sidecar can pin a different skill for just that document — see [Skills](skills.md#reading-and-pinning-skills).
- `--wait` — on a rate limit, sleep until it resets and resume automatically instead of stopping; for unattended overnight batches. It uses the reset time the provider reports, or a fixed fallback interval when it doesn't, and repeats until the queue drains. Not compatible with a batch-mode extractor model (`claude-batch`/`openai-batch` — see [Batch mode](configuration.md#batch-mode-bulk-extraction-at-half-price)).
- `--estimate` — print the token and cost estimate for the queue and exit; no lock, no confirmation, no extraction.
- `--estimate-all` — like `--estimate`, but also projects the queue's cost against every model in the catalog (`model_catalog.yaml`), cheapest first — see [Comparing model cost across the catalog](#comparing-model-cost-across-the-catalog) below.
- `--force` — re-extract even when a cached extraction already exists — see [Re-extracting with --force](#re-extracting-with---force) below. Costs full extraction spend on every queued document, cache or no. Nothing is committed to the vault by `dig`, so this needs no overwrite warning and takes no document names (unlike the deprecated `watchdog ingest --force`).
- `--verify` / `--no-verify` — turn the second-read verification pass on or off for this run, overriding the `verify_extraction` setting either way — see [Catching what the extractor missed](#catching-what-the-extractor-missed) below.
- `--skip-warning` — skip the public-records acknowledgement pause described above; still prints a one-line notice of what was sent.

**Resumability.** Pressing Ctrl+C, or hitting a rate limit without `--wait`, stops the batch cleanly: finished documents are saved and unfinished ones stay queued, so re-running `watchdog dig` picks up where it left off. A document that genuinely fails extraction is set aside in `queue/_failed/`; the run reports how many, and `watchdog requeue` moves them back to retry — this is surfaced everywhere the queue's state matters: the normal run summary, `--estimate`, and a bare `watchdog dig` with nothing new to read, which offers to requeue and retry right there instead of just reporting an empty queue. On macOS or Linux, dig also keeps the machine from sleeping for the run's duration — see [Troubleshooting](troubleshooting.md#ingest-prevents-the-machine-from-sleeping-during-a-run).

If a previous batch is still pending finalization when you start `watchdog dig`, it asks what to do: **merge** the pending batch into this run (a following `watchdog bark` finalizes both together), or **discard** it. `dig` never finalizes in the run it's invoked from, so it doesn't offer to finalize inline — the bare guided walk (which does finalize inline) offers that as a third option, **finalize** it first and stop.

#### Catching what the extractor missed

Most of what a first read misses is not something it couldn't see. Checked against the exact text the model was given, effectively every missed fact in our test corpus was right there on the page — read, and judged not worth writing down. The pattern repeats: an obligation phrased in standard contract wording, a one-line note under a table, something in a schedule at the back.

The verification pass is a second, cheap read aimed at exactly that. Straight after a document is extracted, it goes back to the same text with the facts just pulled from it in hand, and answers one question: what material fact is here and not on that list? Anything it finds is compared against the existing facts by the program — not by the model a second time — and added if it is genuinely new. Added facts look like any other fact in your notes and are marked so you can tell where they came from.

Turn it on for a single run with `--verify`, or for good with `watchdog configure verify_extraction true`. `--no-verify` turns it off again for one run.

**What it costs.** Roughly 15% more per run on the Claude API path, where the re-read reuses the first call's cached prompt at a fraction of the price, so most of the extra is the second call's own thinking. On an OpenAI-compatible model the re-read doesn't get that discount, so expect a larger increase. It runs at low effort by default to keep the thinking cost down; `verifier_effort` raises it if you need to. Not available with a batch extractor model (`claude-batch`, `openai-batch`), which returns its results hours later, long after there is anything to check them against.

**What to watch for.** It is tuned to over-list rather than under-list, so it will occasionally add a restatement of something you already had, or a true detail too minor to be worth a line. Whether that trade is worth it on your material is the reason it is off by default. If you turn it on, read a document's fact list once with fresh eyes before deciding to leave it on.

### watchdog bark

`watchdog bark` completes post-ingest — merging duplicate entities, flagging contradictions between documents, entity synthesis, timeline reconciliation, the briefing, and (only when the run adds a new document request while others are already open) consolidating differently-worded document requests that name the same real document — for a batch `watchdog dig` staged, or one an interruption (a rate limit mid-run, a Ctrl+C) left half-done. `watchdog status` flags a batch as pending finalization; `bark` completes it without re-extracting anything. Documents land in the vault — entity and document notes, the registry — at the start of this step, not progressively as each one extracted; extraction only stages its output durably.

**Model and effort flags.** Each takes effect for this run only; the persistent defaults live in [Configuration](configuration.md).

- `--finalizer-model MODEL` — the model for this step: merging duplicate entities, flagging contradictions between documents, entity synthesis, timeline reconciliation, and the briefing (default: `haiku`).
- `--finalizer-effort low|medium|high|xhigh|max` — how hard the finalizer thinks; lower spends fewer tokens (default: `high`). `xhigh`/`max` need a model that supports them — see [Controlling cost](configuration.md#controlling-cost).

Takes a Claude tier (`haiku`, `sonnet`, `opus`) or a `backend:model` value routing to another provider — see [Model backends](configuration.md#model-backends). Left unset, `--finalizer-effort` sends nothing regardless of model — safe on Haiku, the finalizer default. Set explicitly, a level the resolved model doesn't support — including `xhigh`/`max` on a model that doesn't reach them — fails with a clear error rather than running silently at a different effort than you asked for.

**Per-stage finalizer overrides.** `--finalizer-model` sets one model for all of post-ingest, but the step is really four separate model calls — reconciliation, synthesis, timeline, and the briefing — and any one of them can be routed to a different model without touching the other three:

- `--finalizer-reconciliation-model MODEL` — just merging duplicate entities and flagging contradictions between documents.
- `--finalizer-synthesis-model MODEL` — just writing prose for entities mentioned across more than one document.
- `--finalizer-timeline-model MODEL` — just deduplicating same-date collisions and folding coarse-precision restatements into their exact date.
- `--finalizer-briefing-model MODEL` — just the briefing.

Each falls back to `--finalizer-model` (and, below that, `finalizer_model` from `watchdog configure`) when left unset — setting only one of these leaves the other three stages on the aggregate finalizer. A stage overridden this way still uses `--finalizer-effort`; effort isn't overridable per stage. When any override is in effect, the "which model runs each stage" line printed before the run starts grows an extra `finalizer:<stage>` row for each stage that actually differs from the aggregate finalizer.

**Other flags.**

- `--estimate` — print a token/cost estimate for the pending batch and exit — no lock, no finalize — the same read-only contract as `dig`'s own `--estimate`. It prices the batch already staged in the vault's working files rather than a queue, so the dollar figure draws only on this vault's history of *standalone* `watchdog bark` runs (a finalize that ran as part of a normal `dig`+`bark` sequence, or the deprecated `watchdog ingest`, doesn't count, since its cost is mixed in with extraction). A vault that has only ever finalized as part of a combined run — never on its own — gets the token count with no dollar figure, until it has that history.
- `--estimate-all` — like `--estimate`, but also projects the staged batch's cost against every model in the catalog, cheapest first — see [Comparing model cost across the catalog](#comparing-model-cost-across-the-catalog) below.
- `--skip-briefing` — finalize as usual (merging duplicate entities, flagging contradictions, entity synthesis, timeline reconciliation) but skip the briefing model call. Useful for bulk backfills or re-ingests where a briefing isn't worth its cost every time. `hot.md` and that run's entry in `log.md` are only written alongside a briefing, so both are skipped too — the run still ends with `briefings/leads-<date>.md`, `requests.md`, and `watchlist.md` alerts, which don't depend on the briefing. Also available as a top-level `watchdog --skip-briefing` when the bare guided walk reaches this step.

A Ctrl+C during `bark`'s sequential post-processing stops it cleanly too; re-run `watchdog bark` once you're ready to pick back up.

#### Comparing finalizer models

Extraction is the expensive part of an ingest — the finalizer's few calls (reconciliation, synthesis, timeline, briefing, and the occasional document-request dedup pass) cost little by comparison. To try more than one finalizer model or effort level against the *same* extraction, without paying for extraction again each time:

1. Run `watchdog dig`. Documents extract and stage durably, but nothing is written to the vault and post-processing does not run — `watchdog status` will show the batch as pending finalization.
2. Run `watchdog bark --finalizer-model <model>` to try one candidate. Do this from inside the vault, or from a copy of the vault folder if you want to test several candidates against the identical extraction — each copy still has the same staged inputs, so pointing a different `--finalizer-model` at each copy compares them fairly.

To isolate just one stage instead of the whole post-ingest step, pass one of the four per-stage overrides described [above](#watchdog-bark) — e.g. `watchdog bark --finalizer-briefing-model opus` tries a different briefing model while reconciliation, synthesis, and the timeline stay on the aggregate finalizer.

`watchdog bark` does not yet support re-running cleanly over a batch it has already finalized — run it once per vault (or vault copy).

#### Comparing model cost across the catalog

`--estimate-all` (on `dig` or `bark`) prints the same token/cost estimate as `--estimate`, followed by a table projecting that same batch's cost across every model in `model_catalog.yaml` — cheapest first, one line per model:

```
  1 document · ~12 pages · est. ~24K tokens in (~$3-4 based on your last 3 runs)

  Projected list price by model, cheapest first (every input token priced as a
  cache miss — a rough ceiling, not what you'd actually pay with caching):
    Gemini 2.5 Flash-Lite   gemini      $0.01
    DeepSeek V4 Flash       deepseek    $0.01
    ...
    Claude Sonnet 4.6       anthropic   $0.14
    GPT-5.5                 openai      $0.24
```

This is a comparison tool, not a billing forecast: every model is priced at its published per-token rate, scaled from this vault's own recent output:input token ratio, as if every input token were a cache miss — cache pricing varies by provider and usage pattern, so it isn't modeled here. It shows every catalog model, including all three Claude tiers, regardless of whether this vault is on subscription auth (where a real Claude run costs nothing extra beyond the subscription) — the table answers "what would each model's list price come to," not "what will I actually be billed." A vault with no usage history yet has nothing to project an output-token ratio from, so `--estimate-all` shows the same "not enough history" message `--estimate` already gives a first-run vault, with no per-model table.

#### Re-extracting with --force

Ordinarily, once a document has been extracted — its output staged, whether or not it has reached the vault yet — `watchdog dig` skips it rather than spending another extraction call on the same bytes. `--force` overrides that, for when you want to re-run a document (or a whole corpus) under a different extractor model, effort level, or record skill.

Plain `watchdog dig --force` simply re-extracts whatever is staged but not yet in the vault, overwriting the staged output — nothing has been written to the vault yet, so there is nothing to confirm. To regenerate a document *already committed* to the vault, use the deprecated `watchdog ingest --force <document>` (a sha256, an unambiguous sha256 prefix, or a filename — you can name more than one), which re-chews the original from the morgue, re-queues it, re-extracts it, then lists the vault notes about to be replaced and asks you to confirm before finalizing — unlike the routine ingest confirmation, this one **defaults to Cancel**, since replacing a note that already carries your analysis is not something to do by accident. Cancelling leaves the re-extracted batch staged; run `watchdog bark` later to complete it once you are ready.

```
watchdog ingest --force report.pdf disclosure-2024.pdf
```

`--estimate` is read-only — no lock, no confirmation, no extraction — so combining it with `--force` does not re-chew or re-queue anything; Watchdog prints a note that the estimate reflects the current queue only, rather than silently ignoring it.

### watchdog ingest (deprecated)

`watchdog ingest` combined `dig` and `bark` into one non-interactive-ish shot: extract everything queued, then finalize automatically. It's deprecated in favour of two clearer paths that cover the same ground — the guided `watchdog` walk (which also seeds context and chews first), or `watchdog dig` followed by `watchdog bark` for manual control. Running it still works during the deprecation window (a warning prints first), with the same flags it always had: every flag listed under [`dig`](#watchdog-dig) and [`bark`](#watchdog-bark) above, plus `--force [DOC …]`, which — unlike `dig --force` — accepts document names to re-queue and re-extract documents already committed to the vault (see [Re-extracting with --force](#re-extracting-with---force)).

### watchdog chew

`watchdog chew` does the local preprocessing: it converts each file in `_INCOMING/` to structured text, applies OCR to the pages that need it, splits large PDFs into chunks processed in parallel, and checks for duplicates. The extracted text is queued in `.watchdog/queue/` and the original file moves to `.watchdog/staging/`. Nothing is sent to a model during chewing.

```bash
watchdog chew                        # everything in _INCOMING/
watchdog chew path/to/file.pdf       # one specific file
```

Two flags override the persistent parallelism settings for a single run:

- `--chew-workers N` — files processed in parallel (the `chew_workers` setting; default: adaptive).
- `--chunk-workers N` — parallel chunks per large PDF (the `chunk_workers` setting; default: adaptive).

Press Ctrl+C to cancel a chew in progress — the lock is cleaned up automatically and unfinished files remain in `_INCOMING/` for the next run. When the batch completes, Watchdog sends a desktop notification (macOS only) and offers to extract right away, so you can move straight to extraction without typing the next command — that offer is the same public-records acknowledgement gate described under [`watchdog dig`](#watchdog-dig) above, not a separate confirmation.

### watchdog fetch

`watchdog fetch` downloads a batch of URLs into `_INCOMING/` — for when you already have the links and don't need a research session. Each URL is validated, size-capped, and saved with a provenance sidecar, and Wayback Machine archiving applies if you have [configured it](configuration.md).

```bash
watchdog fetch https://example.gov/filing https://news.example/article
watchdog fetch links.txt
```

A links file holds one URL per line, or the tab-separated `url⇥title⇥source_type⇥relevance` form. Pass `--project <name>` to target a vault you are not currently inside.

HTML pages get a full rendered snapshot — images, styles, client-rendered content — when the optional capture browser is installed, and fall back to a sanitized plain fetch otherwise; see [Install](install.md) for the optional install. After fetching, run `watchdog chew` and `watchdog dig` as usual.

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

Every item in the leads, alerts, and document-request reports carries a short resolution id (for example `lead:isolated:acme`, or `request:9f8e7d6c1a2b` for a document request). Run `watchdog resolve <id…>` from inside the vault to acknowledge items so the deterministic reports stop re-surfacing them. Two flags change the mode: `--sync` imports any `- [x]` checkboxes you have ticked in the `briefings/` files or the vault-root `requests.md` instead of taking ids, and `--list` shows what is currently acknowledged. `watchdog unresolve <id…>` is the inverse, bringing items back into the active list. Acknowledgments are stored in the vault's registry and follow an entity through `watchdog merge-entities`.

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

Every stage names the model it used **and the backend that served it** — and for Claude, the auth mode alongside it, as in `backend: claude-agent-sdk (subscription)`. This matters because a plain `sonnet` doesn't name a backend: a subscription routes it through Claude Code's harness, a metered key sends it straight to the API, and the two bill different numbers of input tokens for identical documents. The `--all` comparison carries the same information as a compact `Backend` column (`sdk/sub`, `api/key`), so two runs that differ only in how they reached Claude are no longer indistinguishable.

When a run used a Claude subscription, the costs shown are what the same work would cost at published per-token rates — not money that was billed, since a subscription has no per-token charge. Those runs are flagged as such under the run total.

Each call's usage is written to disk as soon as it completes, not just when the run finishes — so a crash, a hard interrupt, or a stop mid-finalize still leaves that run's spend on record. If a run never reaches a clean end, its in-progress file is folded into a normal recorded run the next time you run `watchdog dig` or `watchdog bark`, and shows up in `watchdog usage` from then on.

A batch-collected extractor stage (the Batches API's cheaper, asynchronous extraction path) gets an extra line under its header showing the batch's full lifecycle: when it was submitted, when Anthropic finished processing it, and when this vault actually collected the results — the last two routinely differ by hours, since a batch is submit-and-exit and only a *later* `watchdog dig` invocation notices it has finished and pulls the results in.

### watchdog export

Exports the investigation's entity and relationship graph for network-analysis tools. The default writes Neo4j-import CSV (`nodes.csv` and `relationships.csv`, also loadable in Gephi); `--format cypher` writes a single `graph.cypher` of `MERGE` statements instead, and `--output DIR` sets the destination (default: `<slug>-export/`). The export is deterministic — it reads the registry, with no model calls. Only stated-direction relationships are exported (auto-generated reverse edges are skipped), and edges to never-profiled entities are dropped so the import stays valid.

### watchdog auth

Shows how Watchdog currently authenticates to model providers, then, on a terminal, offers to change it.

- `watchdog auth` — prints a **Claude Code** section (subscription/api-key mode, Claude Code login detection — Claude Code is required for the interactive investigation commands and is the ingestion default), an **Ingestion** section showing which provider each of `classifier_model`/`extractor_model`/`finalizer_model` currently resolves to and whether that provider is ready (✓/✗), and a **Provider keys** section listing every stored key, masked, and marked `(in use)` or `(unused)` depending on whether a stage is routed to it. Off a terminal, it stops there.
- On a terminal it then asks **"Change something?"** — choose **Done — nothing to change** to leave, or pick a service (Anthropic, OpenAI, DeepSeek, or Gemini):
  - For **Anthropic**, choose between your Claude Code subscription (not metered) and a metered API key.
  - For **OpenAI**, **DeepSeek**, or **Gemini**, store a new key, replace an existing one, or delete it.

There is no separate `set`/`get`/`use`/`remove` subcommand — this one interactive flow covers all of it. Keys can also come from the standard environment variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `GEMINI_API_KEY`, `LOCAL_API_KEY`, `OPENROUTER_API_KEY`), which always take precedence over a stored key. `LOCAL_BASE_URL` and `OPENROUTER_BASE_URL` likewise override the `local_base_url`/`openrouter_base_url` `watchdog configure` keys for those two backends. Routing a pipeline stage to another provider is covered in [Model backends](configuration.md#model-backends).

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

These run inside a Claude Code session with your investigation open — they are interactive, multi-turn, and always run on Claude. Extraction is not a slash command; run `watchdog dig` in your terminal instead.

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
