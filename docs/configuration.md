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
| `extract_concurrency` | `20` (`3` if `watchdog setup` or `watchdog auth` puts you on Claude subscription auth) | Documents extracted in parallel during `watchdog dig`. |
| `classify_pages` | `5` | Leading pages of each document shown to the classifier. |
| `default_skill` | *(unset)* | Pin one record skill for every ingested document, skipping classification. |
| `section_token_threshold` | `auto` | Estimated tokens above which a document is split into sections for extraction. `auto` derives it from ~60% of the extraction model's context window, capped tighter for models whose output limit it can't work around; set a number to override. |
| `section_token_budget` | `auto` | Target estimated tokens per section when a document is sectioned. `auto` is half the threshold; set a number to override. |
| `section_overlap_tokens` | `4000` | Estimated-token overlap between consecutive sections. |
| `empty_extraction_min_words` | `500` | Source-text word count above which a document that comes back with zero extracted facts is treated as a failed extraction rather than a genuinely fact-free one. |
| `classifier_model` | `haiku` | Model that reads a document's first pages and picks its record skill. |
| `extractor_model` | `sonnet` | Model that extracts each document. |
| `finalizer_model` | `haiku` | Model for the post-ingest step: entity synthesis, timeline, briefing. |
| `finalizer_reconciliation_model` | *(unset)* | Overrides `finalizer_model` for just entity reconciliation and contradiction flagging. |
| `finalizer_synthesis_model` | *(unset)* | Overrides `finalizer_model` for just multi-mention entity synthesis. |
| `finalizer_timeline_model` | *(unset)* | Overrides `finalizer_model` for just timeline reconciliation. |
| `finalizer_briefing_model` | *(unset)* | Overrides `finalizer_model` for just the briefing. |
| `extractor_effort` | `medium` | How hard the extractor model thinks: `low`, `medium`, `high`, `xhigh`, or `max` — not every model supports every level, see [Controlling cost](#controlling-cost). |
| `finalizer_effort` | `high` | How hard the finalizer model thinks: `low`, `medium`, `high`, `xhigh`, or `max` — not every model supports every level, see [Controlling cost](#controlling-cost). |
| `local_base_url` | *(unset)* | Base URL of a local/self-hosted OpenAI-compatible model server, for the `local` backend. |
| `local_context_window` | `8000` | Context window (tokens) of the local model, since Watchdog can't infer it from an arbitrary self-hosted model id. |
| `openrouter_base_url` | `https://openrouter.ai/api/v1` | Base URL for the `openrouter` backend; change only if pointing at an OpenRouter-compatible proxy. |
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

`chew_workers` and `chunk_workers` both default to `auto`: Watchdog scans the batch before starting and picks values based on how large the documents are. They multiply — a batch of large PDFs runs roughly `chew_workers × chunk_workers` subprocesses — so pin them to small numbers on a modest machine.

The `section_*` family governs very large documents at ingest. A document estimated under `section_token_threshold` tokens is extracted whole; anything larger is split into sections of roughly `section_token_budget` tokens, extracted sequentially, with `section_overlap_tokens` of overlap so entities and events spanning a boundary aren't lost. The threshold and budget default to `auto`: rather than a fixed number, `auto` resolves to a fraction of the extraction model's context window, so a large-window model (DeepSeek V4's 1M) reads far more of a document in one call before sectioning than a 200K Claude window does — fewer calls, less orchestration overhead. For a model that enforces a fixed output limit Watchdog can't page past (currently OpenAI and Gemini models), `auto` also caps the threshold so a whole-document extraction won't outrun that limit, sectioning proactively instead. Set either key to a fixed number to override the model-aware default (an advanced escape hatch). A fixed number does not rescale when you change `extractor_model`, so set it back to `auto` (or re-check the value) if you switch to a model with a different context window.

You generally don't need to touch these. Watchdog will not accept a truncated extraction: it detects when a model's answer was cut off at its output limit, continues the answer where the model supports it (Claude and DeepSeek) or sections the document up front where it doesn't (OpenAI, Gemini), and falls back to sectioning-and-retry if a whole-document pass is ever rejected — so a large, dense document is handled automatically rather than silently losing content.

### Extraction safeguards

`empty_extraction_min_words` catches the opposite failure: a model call that comes back with no errors but nothing in it — zero extracted facts — on a document that plainly has substantial text to draw from. Watchdog measures the actual chewed source text, not the document's page count, since page count is a poor stand-in for how much there is to extract (an exhibit-heavy filing can run long but be mostly blank scans; a short order can be dense). Past the threshold with zero facts, the document gets one automatic retry, then fails loudly instead of silently succeeding with nothing in it. Raise it if you routinely ingest long documents with legitimately sparse content (cover pages, signature-only filings padding out the page count); lower it if your documents tend to be short but substantive.

`shingle_size` controls near-duplicate fingerprinting; changing it invalidates existing fingerprints, so documents already ingested would need re-ingesting to rebuild them.

### Search indexing

Both search models run entirely on your machine — no API calls, no cost, nothing leaves the computer. `embed_model` must be a model the fastembed library can load; stronger options include `BAAI/bge-base-en-v1.5` and `mxbai-embed-large-v1`. Vectors from two models aren't comparable, so after changing it, run `watchdog reindex` to rebuild the index from disk — no re-ingest needed. `rerank_model` is the biggest retrieval-quality lever; it is pre-downloaded by `watchdog setup` (about 300 MB), or on first search if missing. A lighter option is `Xenova/ms-marco-MiniLM-L-6-v2`. A rerank-model change needs no reindex — reranking runs fresh at query time — and `--no-rerank` skips it for a single search.

### Models and cost

The three model keys and two effort keys are the main cost controls — see [Controlling cost](#controlling-cost) below. Each model key takes a Claude tier (`haiku`, `sonnet`, `opus`) or a `backend:model` value (see [Model backends](#model-backends)), and each has a matching per-run flag on `watchdog dig` (classifier, extractor) or `watchdog bark` (finalizer). The classifier default is Haiku because picking a skill is easy work; the finalizer default is Haiku because it works from compact digests rather than raw documents. The finalizer also reconciles duplicate entities and flags contradictions between documents — the pipeline's two hardest judgements — so raise it if synthesized prose feels thin, if duplicate entities are slipping through, or if cross-document contradictions are being missed. It runs only a few times per ingest regardless of how many documents you feed it, so raising it costs far less than raising the extractor.

`default_skill` pins one record skill for every document, skipping classification — for vaults that are always one document type. Run `watchdog configure default_skill` with no value to pick from the catalogue interactively.

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

# Spend even fewer thinking tokens on extraction than the medium default
watchdog configure extractor_effort low

# Lower parallelism if you hit model rate limits
watchdog configure extract_concurrency 2

# Route classification to a local model — documents never leave the machine for this stage
watchdog configure local_base_url http://localhost:11434/v1
watchdog configure classifier_model local:llama-3.3-70b
```

## Model backends

Backend choice applies only to the ingest pipeline (`watchdog dig` and `watchdog bark`) — the bounded reasoning steps that run in your terminal. The interactive investigation commands (`/watchdog-query`, `/watchdog-surface`, `/watchdog-wiki`, `/watchdog-context`, `/watchdog-health`) are not affected: they are open-ended, multi-turn sessions that run inside Claude Code, on Claude, always. The ingest stages are single-shot calls, which tolerate a cheaper provider far better.

Within ingest, Watchdog is designed around Claude and uses it by default, but each stage — classification, extraction, post-ingest — can run on a different provider. A stage's model key takes either a Claude tier (`haiku`, `sonnet`, `opus`, routed by your `watchdog auth` mode) or a `backend:model` value naming the provider and its model:

| Value | Runs on |
|---|---|
| `sonnet` | Claude, via your auth mode (subscription or API key). Currently resolves to Claude Sonnet 4.6. |
| `sonnet-4.6` | Claude Sonnet 4.6, explicitly — same model `sonnet` resolves to today, pinned by name in case the bare `sonnet` default ever moves. |
| `sonnet-5` | Claude Sonnet 5 — accepts a wider range of effort levels (up to `xhigh`) than Sonnet 4.6; see [Controlling cost](#controlling-cost). |
| `claude-api:opus` / `claude-agent-sdk:sonnet` | Claude, forcing a specific backend. |
| `openai:gpt-5-mini` | OpenAI. |
| `deepseek:deepseek-v4-flash` | DeepSeek V4 Flash — non-thinking (append `-thinking` to enable thinking mode). |
| `deepseek:deepseek-v4-pro` | DeepSeek V4 Pro — non-thinking (append `-thinking` to enable thinking mode). |
| `gemini:gemini-2.5-flash` | Gemini 2.5 Flash — 1M-token context window. |
| `gemini:gemini-2.5-pro` | Gemini 2.5 Pro — 1M-token context window. |
| `gemini:gemini-3.5-flash` | Gemini 3.5 Flash — stable, 1M-token context window. |
| `gemini:gemini-3.1-flash-lite` | Gemini 3.1 Flash-Lite — stable, cheapest Gemini tier. |
| `gemini:gemini-3.1-pro-preview` | Gemini 3.1 Pro — preview release; Google may deprecate preview model ids on short notice. |
| `local:llama-3.3-70b` | A model on your own machine or network — Ollama, LM Studio, llama.cpp's server, vLLM, or anything else speaking the OpenAI-compatible wire format. Requires `local_base_url`; usually no key. |
| `openrouter:anthropic/claude-3.5-sonnet` | [OpenRouter](https://openrouter.ai) — one key routes to many hosted models, named exactly as OpenRouter itself lists them. |

Point a stage at a provider — persistently or per run:

```bash
watchdog configure extractor_model                      # interactive: pick the model, then paste the key if it's a new provider
watchdog configure extractor_model deepseek:deepseek-v4-flash
watchdog dig --extractor-model openai:gpt-5-mini        # one-off override
```

If you pick a model interactively from a provider you have no key for yet, `watchdog configure` asks for that key on the spot, so the stage is ready to run rather than failing on the next ingest. Setting the value directly on the command line (the second form above) does not prompt — store the key yourself with `watchdog auth`, or set the provider's environment variable.

`watchdog setup` offers a shortcut for all of this: if you're on a Claude Code subscription, it asks whether to route ingestion to a metered provider instead, then walks through picking that provider, pasting its key, and choosing a model for each of the three ingest stages in one go. `watchdog auth`'s status view shows, per stage, which provider it currently resolves to and whether that provider is ready (a key is stored or its env var is set) — a stage routed to Claude also names its billing mode (`subscription` or `api-key`) — plus every stored key, Anthropic included, marked `(in use)`, `(unused)`, or, for a stored Anthropic key that the current mode can't reach, `(inactive)`.

Each stage is independent — you can keep extraction on Claude Sonnet while routing the cheaper classification or post-ingest steps to another provider. One honest caveat: non-Claude backends are unproven on dense legal and financial extraction, so the defaults stay on Claude and nothing routes elsewhere unless you ask. The effort knobs are model-specific: setting one on a stage routed to a model that doesn't support that level (or doesn't support effort at all — DeepSeek, Claude Haiku) errors rather than running silently at a different effort than you asked for (see [Controlling cost](#controlling-cost)). DeepSeek thinking mode is off by default and enabled by appending `-thinking` to the model id (e.g. `deepseek:deepseek-v4-flash-thinking`); extraction is schema-bound structured output, so non-thinking is the cheaper, more predictable default, with thinking available for the judgment-heavy cases. Gemini has no equivalent thinking toggle — its `reasoning_effort` is driven entirely by the effort knobs.

### Local and self-hosted models

Cost is one reason to run a stage on a model on your own machine or network, and not the interesting one. The real reason is documents that cannot leave the building: a leaked document set, an unpublished investigation, anything with a source's fingerprints on it. Every backend above — including Claude — sends document text to somebody else's server. A `local` model, pointed at a runner on hardware you control, is the one configuration where it never does.

`local` works with any server that speaks the OpenAI-compatible Chat Completions wire format — Ollama, LM Studio, llama.cpp's server, vLLM, and others. Point it at your server and pick a model:

```bash
watchdog configure local_base_url http://localhost:11434/v1   # e.g. Ollama's default port
watchdog configure extractor_model local:llama-3.3-70b
```

Most self-hosted runners don't check for an API key at all, so `local` doesn't ask for one unless you add it yourself with `watchdog auth` (some gateways in front of a local model do check). Because a self-hosted model's id carries no vendor namespace, Watchdog can't infer its context window the way it does for a hosted model — set `local_context_window` to the real figure (check your model's card or your runner's docs) so document sectioning sizes sections correctly; left unset, Watchdog assumes a conservative 8,000 tokens, which errs toward more (smaller) sections rather than risking an overrun on an unknown model.

`watchdog usage` reports a local call's cost as $0 — genuinely accurate, since there's no per-token bill — but $0 is not the same as free: a local model spends wall-clock time instead, and a `local model` note next to the usual figures says so, so a run that took an hour doesn't read as having cost nothing.

**The honest caveat, sharper here than anywhere else in this page:** the pipeline doesn't chat, it demands schema-valid JSON and retries on failure — the hardest thing to get reliably out of a small local model, and the failure mode is easy to miss (not a crash, a quietly thinner extraction: fewer facts, dropped relationships, elided quotes). This has not yet been run through Watchdog's own extraction benchmark (corpus-v1) the way Claude, DeepSeek, and OpenAI have. Until that's done, treat the classifier and finalizer — short input, more forgiving output — as the first things worth trying locally, and be skeptical of a local extractor on dense legal or financial material specifically. (See [Benchmarks](benchmarks.md) for how that comparison is run and what it's found so far.)

OpenRouter (`openrouter:anthropic/claude-3.5-sonnet`, or any model id [OpenRouter](https://openrouter.ai) lists) is the same mechanism with a fixed, hosted endpoint and a required key — useful for reaching a model Watchdog has no dedicated backend for, but it does send document text off-machine to OpenRouter and whichever model it routes to, same as any other hosted provider above.

### Batch mode: bulk extraction at half price

If you're ingesting a large dump — say, 200 pages — a batch-mode `extractor_model` submits every whole-document extraction as one bulk batch at 50 per cent off every token. The tradeoff is latency, not cost: a batch typically finishes within an hour but can take up to 24, so `watchdog dig` submits it and exits rather than waiting. Run `watchdog dig` again later (or check `watchdog status`) to collect the results.

Two batch backends are available, one per provider:

- `claude-batch:sonnet` (or any Claude tier) — Anthropic's Message Batches API. Requires `api-key` auth mode (switch to it with `watchdog auth`) — batching is not available on a Claude subscription.
- `openai-batch:gpt-5.6-luna` (or any OpenAI model id) — OpenAI's Batch API. OpenAI has no subscription mode in Watchdog at all, so this just needs a stored OpenAI key (`watchdog auth`), the same as the plain `openai` backend.

Each needs only that provider's own key — an `openai-batch` extractor needs no Anthropic key, and vice versa.

The documents do not have to be the same type. Each one works out its own record skill before the batch is built — from its own `.yml` sidecar if it has one, otherwise the run-wide `--skill` if you set one, otherwise a quick classification — so a mixed drop of court filings and financial statements batches fine, each read with the right skill.

Two constraints, each enforced with a clear error:

1. A batch backend is valid only as `extractor_model`, not `classifier_model` or `finalizer_model`.
2. A document large enough to need sectioned extraction can't go through the batch, so those extract via that provider's regular single-call backend instead (`claude-api` or `openai`), automatically — announced in the run's output, not silent.

Classification itself is not batched — it stays one quick call per document, at the classifier model's price. That is deliberate: it's a cheap call on a short excerpt, and paying it is what removes the requirement to sort your documents by type before ingesting them.

This is also the recipe for keeping a Claude subscription's session limits for interactive work only, spending zero subscription tokens on bulk ingest:

```bash
watchdog auth                                             # interactive: switch Claude to api-key mode
watchdog configure classifier_model claude-api:haiku
watchdog configure extractor_model claude-batch:sonnet
watchdog configure finalizer_model claude-api:haiku
watchdog dig                                              # submits the batch, exits
watchdog dig                                              # later: collects it once ready
```

The same recipe works with `openai-batch:gpt-5.6-luna` in place of `claude-batch:sonnet` on the third line, for a corpus already routed to OpenAI — no `watchdog auth` switch needed first, since OpenAI has no subscription mode to switch from.

## Controlling cost

Watchdog is built to keep token costs predictable. Everything mechanical runs locally and costs nothing: OCR, document conversion, search indexing, reranking, the lead sweep, the watchlist scan. The model is called only for the reasoning steps of ingest — classify, extract, reconcile entities and contradictions, synthesize, reconcile the timeline, write the briefing — and each document's classification loads only the single matching domain skill, not all of them.

The main levers, roughly in order of impact:

- **Effort.** Thinking tokens bill as output, so `extractor_effort` is the biggest per-run lever. It already defaults to `medium` — benchmark testing found no recall difference against `high`, at meaningfully lower cost — but `low` is worth trying on a test batch if you want to cut cost further. That default is skipped automatically if `extractor_model` is routed to a model with no effort control at all (Haiku); `finalizer_effort` has no such default (nothing is sent unless you set it), which is why the finalizer's default model, Haiku, needs no special case. `xhigh` and `max` push past `high` for the hardest documents, at a further cost premium — support for them isn't universal: OpenAI's GPT-5.6 family takes both, and Claude's coverage varies by model (Sonnet 4.6 takes `max` but not `xhigh`; Sonnet 5 and Opus 4.8 take both). Setting either `extractor_effort`/`finalizer_effort` key explicitly to a level the resolved model doesn't support fails with a clear error rather than running silently at a different effort than you asked for — model and effort are configured together, so changing one is worth a second look at the other. (See [Benchmarks](benchmarks.md) for the methodology behind the `medium`/`high` defaults.)
- **Models.** `extractor_model haiku` is cheaper and faster for large batches of straightforward documents; Sonnet handles complex or ambiguous ones better. The classifier and finalizer already default to Haiku. If just one post-ingest stage needs a stronger model — the briefing reads thin, or duplicate entities keep slipping through reconciliation — `finalizer_reconciliation_model`/`finalizer_synthesis_model`/`finalizer_timeline_model`/`finalizer_briefing_model` raise that one stage without paying a stronger model's cost on the other three. Each falls back to `finalizer_model` when left unset.
- **Batch mode.** The [batch-mode recipe](#batch-mode-bulk-extraction-at-half-price) above halves the cost of a bulk ingest, on either Claude (metered key) or OpenAI.
- **Which Claude backend you're on.** A plain `sonnet` (or `haiku`/`opus`) reaches Claude one of two ways, chosen by your auth mode: a subscription goes through Claude Code's own harness, a metered key goes straight to the API. The two bill different numbers of input tokens for identical documents, so it is worth knowing which one you are on — `watchdog usage` names the backend for every stage. The API path also caches the reusable part of the prompt (the instructions and the record skill) properly, which the subscription path cannot be told to do.
- **Concurrency.** `extract_concurrency` doesn't change total cost, but lowering it — persistently, or with `--concurrency` per run — is the fix when you hit model rate limits. Both `watchdog setup` and `watchdog auth` already lower the default from 20 to 3 when they detect Claude subscription auth and you keep ingestion on it: concurrent extractions on that path share one Claude Code session's rate limit, and the metered-path default of 20 reliably throttles it. Switching back to an API key later restores it to 20 automatically, as long as you never set your own value — `watchdog configure extract_concurrency` always overrides both directions if your plan needs something else.

Before committing to a large run, get a number:

```bash
watchdog dig --estimate
```

This prints a token estimate for the queue and exits — no lock, no confirmation, no extraction. On a metered key with prior runs in this vault, it adds a rough dollar range projected from your own usage history; on a subscription, only the token estimate is shown. Use it to decide whether to split a batch. `watchdog dig --estimate-all` (and `watchdog bark --estimate-all`) goes further, projecting the same estimate across every model in the catalog — see [Comparing model cost across the catalog](commands.md#comparing-model-cost-across-the-catalog).

A failed document never sinks a batch — it is set aside and the rest completes — but for very large collections, chew and ingest in groups anyway. On subscriptions: a Pro plan (US$20/month) is sufficient for most journalism work, and if you ingest hundreds of documents at a time, a Max plan gives higher session limits. An unattended overnight batch on a subscription pairs well with `watchdog dig --wait`, which sleeps through rate limits and resumes — see [Commands](commands.md#watchdog-dig). If you'd rather not wait at all, Anthropic's own [usage credits](https://support.claude.com/en/articles/12429409-manage-usage-credits-for-paid-claude-plans) let a Pro or Max plan keep going past its session/weekly limit at standard API rates once you enable them (Settings → Usage on claude.ai, payment method required) — this is an account-wide setting, not something Watchdog configures, but Watchdog's rate-limit handling only fires on an actual rejection, so it won't interfere once credits are covering the overage.

Where next: [Commands](commands.md) for the per-run flags that override these settings, or [Skills](skills.md) for what `default_skill` can be set to.
