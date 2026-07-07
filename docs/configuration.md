# Configuration

This page covers every Watchdog setting: what it does, what its default is, and when to change it. It also explains model backends — running pipeline stages on providers other than Claude — and how to control what an investigation costs. The defaults are sensible; most people only ever touch a handful of these.

## How configuration works

Settings live in a single file, `~/.watchdog/config.json`, and `watchdog configure` reads and writes it. Run it with no arguments to see every setting and its current value, grouped by section:

```bash
watchdog configure
```

In an interactive terminal, the listing ends with an offer to launch a configuration wizard — an arrow-key menu of every setting. Arrow to a setting, press Enter to read its help and change it, and repeat; press `q` to quit. When the terminal can't support arrow keys, the wizard falls back to a numbered prompt.

To set a value directly:

```bash
watchdog configure <key> <value>
```

Or run `watchdog configure <key>` with no value to see that one key's help and change it interactively.

## The settings

| Key | Default | What it controls |
|---|---|---|
| `projects_dir` | `~/Investigations` | Where `watchdog new` creates investigation vaults; existing vaults are not moved. |
| `ocr_engine` | `auto` | OCR engine for scanned documents: `auto`, `apple_vision`, `tesseract`, `easyocr`, or `rapidocr`. |
| `ocr_languages` | *(auto-detect)* | Languages for Apple Vision OCR, as comma-separated codes (e.g. `en-US,fr-FR`). |
| `garbled_threshold` | `0.75` | Fraction of readable characters below which a PDF's text layer is considered garbled and OCR is triggered (0.0–1.0). |
| `chew_workers` | `auto` | Files processed in parallel during chewing; `auto` adapts to the batch, or set a fixed number. |
| `chunk_size` | `40` | Pages per chunk when splitting large PDFs for parallel processing. |
| `chunk_workers` | `auto` | Parallel subprocesses for large-PDF chunks. |
| `chunk_timeout` | `300` | Seconds before a chunk subprocess is killed. |
| `table_structure` | `true` | Whether the table-detection model runs on PDFs; turn off to speed up text-only documents. |
| `embed_images` | `false` | Embed figures as images in the extracted text so the model can read charts; significantly increases token usage. |
| `extract_concurrency` | `5` | Documents extracted in parallel during `watchdog ingest`. |
| `classify_pages` | `5` | Leading pages of each document shown to the classifier. |
| `default_skill` | *(unset)* | Pin one record skill for every ingested document, skipping classification. |
| `preflight_alias_min_length` | `3` | Shortest entity alias that can match a document during extraction. |
| `section_token_threshold` | *(model-aware)* | Estimated tokens above which a document is split into sections for extraction. Defaults to ~60% of the extraction model's context window; set a number to override. |
| `section_token_budget` | *(model-aware)* | Target estimated tokens per section when a document is sectioned. Defaults to half the threshold; set a number to override. |
| `section_overlap_tokens` | `4000` | Estimated-token overlap between consecutive sections. |
| `classifier_model` | `haiku` | Model that reads a document's first pages and picks its record skill. |
| `extractor_model` | `sonnet` | Model that extracts each document. |
| `finalizer_model` | `haiku` | Model for the post-ingest step: entity synthesis, timeline, briefing. |
| `extractor_effort` | `high` | How hard the extractor model thinks: `low`, `medium`, or `high`. |
| `finalizer_effort` | `high` | How hard the finalizer model thinks: `low`, `medium`, or `high`. |
| `dup_threshold` | `0.85` | Similarity score at which two documents are flagged as near-duplicates (0.0–1.0). |
| `shingle_size` | `3` | Word-sequence length used for near-duplicate fingerprinting. |
| `embed_model` | `BAAI/bge-small-en-v1.5` | Local embedding model that indexes passages and notes for `watchdog search`. |
| `rerank_model` | `BAAI/bge-reranker-base` | Local model that reranks search results for precision; `none` turns reranking off. |
| `research_max_rounds` | `3` | Search rounds a standard `watchdog research` run makes before checking in. |
| `research_max_fetches` | `25` | Roughly how many web sources a standard research run captures into `_INCOMING/`. |
| `wayback_save` | `false` | Also submit every research source to the Internet Archive's Wayback Machine. |
| `wayback_access_key` | *(unset)* | archive.org access key for `wayback_save`; masked in the listing. |
| `wayback_secret_key` | *(unset)* | archive.org secret key, paired with the access key. |

### OCR

`auto` uses Apple Vision on macOS (fast, hardware-accelerated) and Tesseract elsewhere. `easyocr` and `rapidocr` need no system install but are generally less accurate on forms. `ocr_languages` applies to Apple Vision: leave it unset to auto-detect from the image, and set it explicitly only if detection produces poor results. `garbled_threshold` decides when a PDF that claims to have a text layer gets OCR anyway — lower means more aggressive OCR.

### Chewing and large documents

`chew_workers` and `chunk_workers` both default to `auto`: Watchdog scans the batch before starting and picks values based on how large the documents are. They multiply — a batch of large PDFs runs roughly `chew_workers × chunk_workers` subprocesses — so pin them to small numbers on a modest machine. `embed_images` is only worth turning on when documents contain charts, image-based tables, or diagrams that carry investigative value; it raises token usage significantly.

The `section_*` family governs very large documents at ingest. A document estimated under `section_token_threshold` tokens is extracted whole; anything larger is split into sections of roughly `section_token_budget` tokens, extracted sequentially, with `section_overlap_tokens` of overlap so entities and events spanning a boundary aren't lost. The threshold and budget are **model-aware**: rather than a fixed number, they default to a fraction of the extraction model's context window, so a large-window model (DeepSeek V4's 1M) reads far more of a document in one call before sectioning than a 200K Claude window does — fewer calls, less orchestration overhead. Set either key to a fixed number to override the model-aware default (an advanced escape hatch); lower the threshold if extraction of dense documents is overrunning the model's output ceiling.

`shingle_size` controls near-duplicate fingerprinting; changing it invalidates existing fingerprints, so documents already ingested would need re-ingesting to rebuild them.

### Search indexing

Both search models run entirely on your machine — no API calls, no cost, nothing leaves the computer. `embed_model` must be a model the fastembed library can load; stronger options include `BAAI/bge-base-en-v1.5` and `mxbai-embed-large-v1`. Vectors from two models aren't comparable, so after changing it, run `watchdog reindex` to rebuild the index from disk — no re-ingest needed. `rerank_model` is the biggest retrieval-quality lever; it is pre-downloaded by `watchdog setup` (about 300 MB), or on first search if missing. A lighter option is `Xenova/ms-marco-MiniLM-L-6-v2`. A rerank-model change needs no reindex — reranking runs fresh at query time — and `--no-rerank` skips it for a single search.

### Models and cost

The three model keys and two effort keys are the main cost controls — see [Controlling cost](#controlling-cost) below. Each model key takes a Claude tier (`haiku`, `sonnet`, `opus`) or a `backend:model` value (see [Model backends](#model-backends)), and each has a matching per-run flag on `watchdog ingest`. The classifier default is Haiku because picking a skill is easy work; the finalizer default is Haiku because it composes prose from compact digests rather than reading raw documents — raise it if synthesized prose feels thin.

`default_skill` pins one record skill for every document, skipping classification — for vaults that are always one document type. Run `watchdog configure default_skill` with no value to pick from the catalogue interactively. `preflight_alias_min_length` exists because extraction carries forward every known entity a document mentions, and short aliases (initials, abbreviations) false-match common words and quietly inflate the prompt as a vault matures. The canonical name always matches at any length, so short real names like `BP` or `3M` are unaffected; set it to `1` to match all aliases.

### Research

Both research keys are advisory budgets that the interactive research skill limits itself to; the `quick` and `deep` effort tiers scale them down or up per run. `research_max_fetches` bounds scope and later ingest cost, not the research session's own tokens — each captured source is read by the local pipeline afterward, not during the session.

### Wayback archiving

With `wayback_save` on, every source that `watchdog research` or `watchdog fetch` downloads is also submitted to the Internet Archive's Wayback Machine, and the snapshot URL is recorded in the source's provenance sidecar — a citable copy that survives if the original changes or is taken down. It is a no-op until both keys are set; generate a free pair at [archive.org/account/s3.php](https://archive.org/account/s3.php). Archiving is best-effort and never blocks or fails a download.

## Examples

```bash
# Switch to Tesseract on a non-Mac machine
watchdog configure ocr_engine tesseract

# Disable table detection for a project that is all court decisions
watchdog configure table_structure false

# Override OCR languages for a collection of French and Arabic documents
watchdog configure ocr_languages "fr-FR,ar-SA"

# Move investigation storage to an external drive
watchdog configure projects_dir /Volumes/SecureDrive/Investigations

# Use Haiku for extraction by default (faster and cheaper)
watchdog configure extractor_model haiku

# Spend fewer thinking tokens on extraction (the main per-run cost lever)
watchdog configure extractor_effort medium

# Lower parallelism if you hit model rate limits
watchdog configure extract_concurrency 2
```

## Model backends

Backend choice applies only to the `watchdog ingest` pipeline — the bounded reasoning steps that run in your terminal. The interactive investigation commands (`/watchdog-query`, `/watchdog-surface`, `/watchdog-wiki`, `/watchdog-context`, `/watchdog-health`) are not affected: they are open-ended, multi-turn sessions that run inside Claude Code, on Claude, always. The ingest stages are single-shot calls, which tolerate a cheaper provider far better.

Within ingest, Watchdog is designed around Claude and uses it by default, but each stage — classification, extraction, post-ingest — can run on a different provider. A stage's model key takes either a Claude tier (`haiku`, `sonnet`, `opus`, routed by your `watchdog auth` mode) or a `backend:model` value naming the provider and its model:

| Value | Runs on |
|---|---|
| `sonnet` | Claude, via your auth mode (subscription or API key). |
| `claude-api:opus` / `claude-agent-sdk:sonnet` | Claude, forcing a specific backend. |
| `openai:gpt-5-mini` | OpenAI. |
| `deepseek:deepseek-v4-flash` | DeepSeek V4 Flash — non-thinking (append `-thinking` to enable thinking mode). |
| `deepseek:deepseek-v4-pro` | DeepSeek V4 Pro — non-thinking (append `-thinking` to enable thinking mode). |

Store the provider's key first, then point a stage at it — persistently or per run:

```bash
watchdog auth set deepseek                              # store the key (or set DEEPSEEK_API_KEY)
watchdog configure extractor_model deepseek:deepseek-v4-flash
watchdog ingest --extractor-model openai:gpt-5-mini     # one-off override
```

Each stage is independent — you can keep extraction on Claude Sonnet while routing the cheaper classification or post-ingest steps to another provider. One honest caveat: non-Claude backends are unproven on dense legal and financial extraction, so the defaults stay on Claude and nothing routes elsewhere unless you ask. The effort knobs apply where the provider supports them and are ignored where it doesn't. DeepSeek thinking mode is off by default and enabled by appending `-thinking` to the model id (e.g. `deepseek:deepseek-v4-flash-thinking`); extraction is schema-bound structured output, so non-thinking is the cheaper, more predictable default, with thinking available for the judgment-heavy cases.

### claude-batch: bulk extraction at half price

If you run on a metered API key (not a subscription) and are ingesting a large, same-type dump — say, 200 pages of one filing type — setting `extractor_model` to `claude-batch:sonnet` submits every whole-document extraction as one bulk batch at 50 per cent off every token. The tradeoff is latency, not cost: a batch typically finishes within an hour but can take up to 24, so `watchdog ingest` submits it and exits rather than waiting. Run `watchdog ingest` again later (or check `watchdog status`) to collect the results.

Four constraints, each enforced with a clear error:

1. It requires a pinned skill (`--skill` or `default_skill`) — classification is one-document-at-a-time and can't be batched.
2. It requires `api-key` auth mode (`watchdog auth use api-key`) — batching is not available on a subscription.
3. It is valid only as `extractor_model`, not `classifier_model` or `finalizer_model`.
4. A document large enough to need sectioned extraction can't go through the batch, so those extract via the regular API instead, automatically — announced in the run's output, not silent.

This is also the recipe for keeping a Claude subscription's session limits for interactive work only, spending zero subscription tokens on bulk ingest:

```bash
watchdog auth use api-key                                 # a metered key, not your subscription
watchdog configure classifier_model claude-api:haiku
watchdog configure extractor_model claude-batch:sonnet
watchdog configure finalizer_model claude-api:haiku
watchdog ingest --skill court-documents                   # submits the batch, exits
watchdog ingest                                           # later: collects it once ready
```

## Controlling cost

Watchdog is built to keep token costs predictable. Everything mechanical runs locally and costs nothing: OCR, document conversion, search indexing, reranking, the lead sweep, the watchlist scan. The model is called only for the reasoning steps of ingest — classify, extract, synthesize, reconcile the timeline, write the briefing — and each document's classification loads only the single matching domain skill, not all of them.

The main levers, roughly in order of impact:

- **Effort.** Thinking tokens bill as output, so `extractor_effort` is the biggest per-run lever: try `medium` or `low` on a test batch and check whether extraction quality holds. `finalizer_effort` works the same way for the post-ingest prose.
- **Models.** `extractor_model haiku` is cheaper and faster for large batches of straightforward documents; Sonnet handles complex or ambiguous ones better. The classifier and finalizer already default to Haiku.
- **claude-batch.** On a metered key, the [claude-batch recipe](#claude-batch-bulk-extraction-at-half-price) above halves the cost of a bulk same-type ingest.
- **Concurrency.** `extract_concurrency` (default 5) doesn't change total cost, but lowering it — persistently, or with `--concurrency` per run — is the fix when you hit model rate limits.

Before committing to a large run, get a number:

```bash
watchdog ingest --estimate
```

This prints a token estimate for the queue and exits — no lock, no confirmation, no extraction. On a metered key with prior runs in this vault, it adds a rough dollar range projected from your own usage history; on a subscription, only the token estimate is shown. Use it to decide whether to split a batch.

A failed document never sinks a batch — it is set aside and the rest completes — but for very large collections, chew and ingest in groups anyway. On subscriptions: a Pro plan (US$20/month) is sufficient for most journalism work, and if you ingest hundreds of documents at a time, a Max plan gives higher session limits. An unattended overnight batch on a subscription pairs well with `watchdog ingest --wait`, which sleeps through rate limits and resumes — see [Commands](commands.md#watchdog-ingest).

Where next: [Commands](commands.md) for the per-run flags that override these settings, or [Skills](skills.md) for what `default_skill` can be set to.
