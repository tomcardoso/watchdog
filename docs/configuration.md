# Settings

This page is the reference for the **Settings** screen: every tab, every field, what it does and what its default is. It also explains model backends (running steps of the pipeline on providers other than Claude) and how to control what an investigation costs. The defaults are sensible, and most people only ever touch a handful of these.

## How Settings works

Open **Settings** from the sidebar (or **Settings…** in the Watchdog menu on macOS, **File → Settings** on Windows and Linux). Settings describe how Watchdog works on this computer, so a change applies to every investigation, and you can open Settings with no investigation open.

The tabs are in two groups. **General** holds the pipeline's settings, one tab per area. **App** holds sign-in and keys, folder access, record skills, appearance, a health check, setup and an About page.

Each field in General shows its name, a one-line description, and a **More** link for the full help. The control saves as you use it: a switch or a menu saves straight away, and a text box shows a **Save** button (or press Enter). A small "Saved" appears when it takes. Below each field the app shows either the current value or "Using the default", and **Reset to default** puts a changed value back.

Each field shows its label and, in small grey type beside it, the setting's stored name (such as `extractor_model`). The stored names are the ones the older command line uses. Everything is stored in one file, `config.json`, in Watchdog's settings folder (`.watchdog/` in your home folder). **Settings → Appearance** shows its exact location.

## General settings

### Vaults

Where investigations are created.

| Field | Default | What it controls |
|---|---|---|
| Investigations folder | `~/Investigations` (an `Investigations` folder in your home folder) | The folder where each new investigation gets its own folder. Existing investigations are not moved. Choosing a folder with the folder button also allows Watchdog to write there; see [Folder access](#folder-access). |

### OCR

Text recognition for scanned documents. OCR (optical character recognition) is the same idea as a scanner turning a photo of a page into text you can copy.

| Field | Default | What it controls |
|---|---|---|
| OCR engine | `auto` | The OCR engine for scanned documents: `auto`, `apple_vision`, `tesseract`, `easyocr` or `rapidocr`. |
| OCR languages | *(auto-detect)* | Languages for Apple Vision OCR, as comma-separated codes (for example `en-US,fr-FR`). |
| Garbled-text threshold | `0.6` | One of three signals used to decide that a PDF page's text layer is garbled: the fraction of readable characters, which must fall below this value (0.0 to 1.0). No single signal can trigger OCR by itself; see below. |

`auto` uses Apple Vision on macOS (fast, hardware-accelerated) and Tesseract elsewhere. `easyocr` and `rapidocr` need no system install but are generally less accurate on forms. OCR languages applies to Apple Vision: leave it unset to auto-detect from the image, and set it explicitly only if detection produces poor results.

Watchdog decides whether a PDF's text layer is trustworthy, or needs OCR instead, by checking three independent signals on the page. They are what fraction of characters are letters, digits or whitespace (Garbled-text threshold sets this one); what fraction of whitespace-separated tokens look like real words or numbers rather than symbol noise; and whether the page's embedded fonts carry the data needed to map their glyphs back to characters at all. OCR is only triggered when at least two of the three signals agree the page is unreadable.

This matters because a lot of what Watchdog reads is dense: financial tables, tables of contents with dot leaders, citation lists. Those can score badly on the character ratio alone while being perfectly readable, so a single badly-behaved check used to be able to send a whole document through OCR and needlessly break up clean tables. If you had already tuned Garbled-text threshold, your value carries forward unchanged for the character-ratio signal. It now needs one of the other two signals to agree before OCR fires. The default itself moved from `0.75` to `0.6` for the same reason.

**The decision is made page by page, and OCR is applied only to the pages that need it.** Every page is scored, not a sample of the first few. Reading a page's text and fonts is cheap and local, around a hundred pages a second, against seconds per page for the conversion itself. Pages whose text layer is sound keep it, and pages with no text layer or a broken one are OCR'd in a second pass and stitched back in. This matters for a mixed document, such as a 200-page filing with two scanned exhibits bound into it. One verdict for the whole document would mean either OCRing all 200 pages and losing table fidelity on the 198 that were fine, or leaving the two exhibits unread.

### Chew

Local preprocessing: parallelism and large-PDF handling. "Chew" is the first stage, where files are converted to text on your computer; see [Methodology](methodology.md).

| Field | Default | What it controls |
|---|---|---|
| Chew workers | `auto` | Files processed in parallel while reading; `auto` adapts to the batch, or set a fixed number. |
| Chunk size | `40` | Pages per chunk when a large PDF is split for parallel processing. |
| Chunk workers | `auto` | Parallel workers for the chunks of a large PDF. |
| Chunk timeout | `300` | Seconds before a chunk is stopped. |
| Table structure | on | Whether the table-detection model runs on PDFs. Turn it off to speed up text-only documents. |

Chew workers and Chunk workers both default to `auto`: Watchdog scans the batch before starting and picks values based on how large the documents are. They multiply, so a batch of large PDFs runs roughly Chew workers times Chunk workers processes at once. Pin them to small numbers on a modest computer.

### Ingest

The extraction run: parallelism, classification, skill pinning and sectioning.

| Field | Default | What it controls |
|---|---|---|
| Auto-approve | off | Skip the public-records pause when you are signed in with your Claude subscription and every step of the run uses it, showing a one-line notice instead. A run where any step uses a paid API key still asks. See [Auto-approve](#auto-approve). |
| Extraction concurrency | `20` (`3` when Claude is on subscription sign-in) | Documents extracted in parallel. |
| Extraction token budget | `auto` | A cap on tokens per minute during extraction. `auto` discovers it from your provider's own responses. It is not available on Claude subscription sign-in, so set a number by hand there if you hit rate limits. |
| Classify pages | `5` | Leading pages of each document shown to the classifier. |
| Default skill | *(unset)* | Pin one record skill for every document, skipping classification. Choose from the catalogue in [Skills](skills.md). |
| Section token threshold | `auto` | Estimated tokens above which a document is split into sections for extraction. `auto` derives it from about 60 per cent of the extraction model's context window, adjusted for how that model counts tokens. Set a number to override. |
| Section token budget | `auto` | Target estimated tokens per section when a document is sectioned. `auto` is half the threshold. Set a number to override. |
| Section overlap tokens | `4000` | Estimated-token overlap between consecutive sections. |
| Empty-extraction minimum words | `500` | Source-text word count above which a document that comes back with zero extracted facts is treated as a failed extraction rather than a genuinely fact-free one. |
| Verify extraction | off | Re-read each document after extracting it and add material facts the first read missed. See [The verification pass](#the-verification-pass). |

#### Large documents and sectioning

The three Section fields govern very large documents. A document estimated under Section token threshold is extracted whole. Anything larger is split into sections of roughly Section token budget tokens, extracted one after another, with Section overlap tokens of overlap so entities and events spanning a boundary are not lost.

The threshold and budget default to `auto`: rather than a fixed number, `auto` resolves to a fraction of the extraction model's context window (the amount of text a model can take in one call). A large-window model such as GPT-5.6 Luna, at 1.05 million tokens, reads far more of a document in one call than a 200,000-token Claude window does. That means fewer calls and less overhead.

`auto` also adjusts for the fact that different models count tokens differently. Watchdog estimates a document's size with a rule of thumb of roughly four characters per token, and each model's real count drifts from that by a fixed factor, measured against a fixed set of test documents. Claude Opus 4.8, Opus 5.5 and Sonnet 5 use a newer tokenizer that produces about 28 per cent more tokens per character than the estimate assumes, so their resolved number comes out smaller on the same context window, and a large document sections before it can overrun the real limit. Every other model runs the other way: the rule of thumb over-counts them, by around 7 per cent for Claude Haiku 4.5 and Sonnet 4.6, 9 per cent for Gemini, and about 20 per cent for the GPT-5 and DeepSeek families, so their resolved number is a little larger. Once an investigation has extracted enough documents on a given model, Watchdog switches to that investigation's own measured ratio for it, which reflects your documents rather than the test set.

Setting either field to a fixed number overrides all this, and is an advanced escape hatch. A fixed number does not rescale when you change the extractor model, so set it back to `auto` (or re-check the value) if you switch to a model with a different context window. While a field is on `auto`, the Settings screen shows the number it currently resolves to for your extractor, for example `auto (93750 — sonnet)`. For a non-Claude provider it names the backend too, because the backend decides which model the name resolves to. This preview always uses the published figures, never an investigation's own measured correction.

Some models charge roughly double above a certain input length: GPT-5.4, GPT-5.5, GPT-5.6 Luna and GPT-5.6 Terra above about 272,000 tokens, and Gemini 3.1 Pro above 200,000. `auto` holds a call under whichever line applies, with 10 per cent to spare, so a long document is split rather than sent whole at the higher rate. That is also what keeps cost estimates honest. If you pin Section token budget to a fixed number, that protection goes with it.

You generally do not need to touch any of these. Watchdog will not accept a truncated extraction. It detects when a model's answer was cut off at its output limit, continues the answer where the model supports it (Claude and DeepSeek), and where it does not (OpenAI, Gemini) splits the section in half and retries, dropping one effort level if the answer was crowded out by the model's own thinking. A large, dense document is handled automatically rather than silently losing content.

#### Extraction safeguards

Empty-extraction minimum words catches a quiet failure: a model call that comes back with no errors but nothing in it, zero extracted facts, on a document that plainly has substantial text. Watchdog measures the actual chewed text, not the page count, since page count is a poor stand-in for how much there is to extract (an exhibit-heavy filing can run long but be mostly blank scans, and a short order can be dense). Past the threshold with zero facts, the document gets one automatic retry, then fails loudly instead of silently succeeding with nothing in it. Raise the value if you routinely add long documents with legitimately sparse content, such as cover pages or signature-only filings padding out the page count. Lower it if your documents tend to be short but substantive.

#### The verification pass

Verify extraction turns on a second, cheap read of every document, straight after it is extracted. That second call sees the same text and the facts just pulled from it, and answers one question: what material fact is on the page and missing from this list? Anything it finds is checked against the existing facts by the program, not by the model again, and added if it is genuinely new.

It exists because of what a close look at missed facts showed: they were almost never things the model could not see. Checked against the exact text the model was given, effectively every miss was on the page and had simply been judged unimportant, most often an obligation buried in standard-form wording, a one-line disclosure under a table, or something in a schedule at the back.

Cost is roughly 15 per cent more per run on the Claude API path, where the re-read reuses the extraction call's cached prompt at a fraction of the price, so most of the increase is the second call's own thinking (what Verifier effort controls). On an OpenAI model the re-read does not get that discount, so expect a larger increase. A provider only charges the reduced rate when a new request opens with exactly the same text as an earlier one, and OpenAI puts a description of the answer format right at the front, different for the verification call than for the extraction call. That difference disqualifies everything after it, the document included, so the second call pays full price to read the document again. Low is the default effort either way and where the pass is meant to live: comparing a list against a document in front of it is a lookup, not a judgement call. The pass always uses the extractor model, deliberately, because on Claude the discount depends on reusing that model's cached prompt, and a cache belongs to one model.

Two limits are worth knowing. It is not available with a batch extractor model (`claude-batch`, `openai-batch`), whose results come back hours later, long after there is anything live to check them against. And it is tuned to over-list rather than under-list, so it can add a restatement of a fact you already had, or a true detail too minor to be worth a line. That trade-off is why it is off by default. To try it on one batch, set **Second-read check** to On in the Options of the Add documents dialog (or the Dig card), read the resulting facts once with fresh eyes, and decide.

#### Auto-approve

Before Watchdog sends documents to a model, it shows the public-records warning and waits for you to acknowledge it. That pause is the check that what you are sending is public record.

Turning on Auto-approve skips the pause when you are signed in with your Claude subscription and every step of the run (classifying, extracting and the finishing steps) uses it. Instead of the warning, Watchdog shows one line saying how many documents it is sending. The setup screen asks about this, with no as the default; this field changes it later. Turn it on only if you already check that what you add is public record.

A run where any step uses a paid API key always asks, however small it is, and says why. Watchdog does not estimate a run's cost to decide this: an estimate from past runs can come out low, and the pause it would skip is the only check on what leaves your computer.

### Models

Which model runs each step, and how hard it thinks. The three steps are classification (a document's opening pages are read to pick its record skill), extraction (each document is read and its facts listed), and the finishing step (entity matching, summaries, timeline and briefing). See [Methodology](methodology.md) for what each does.

| Field | Default | What it controls |
|---|---|---|
| Classifier model | `haiku` | The model that reads a document's first pages and picks its record skill. |
| Extractor model | `sonnet` | The model that extracts each document. |
| Finalizer model | `haiku` | The model for the finishing step: entity synthesis, timeline and briefing. |
| Finalizer reconciliation model | *(unset)* | Overrides the finalizer model for just entity matching and contradiction flagging. |
| Finalizer synthesis model | *(unset)* | Overrides it for just the summaries of entities named in more than one document. |
| Finalizer timeline model | *(unset)* | Overrides it for just timeline reconciliation. |
| Finalizer briefing model | *(unset)* | Overrides it for just the briefing. |
| Classifier effort | `low` | How hard the classifier thinks: `low`, `medium`, `high`, `xhigh` or `max`. Not every model supports every level; see [Controlling cost](#controlling-cost). |
| Extractor effort | `medium` | How hard the extractor thinks (same levels). |
| Finalizer effort | `high` | How hard the finalizer thinks (same levels). |
| Verifier effort | `low` | How hard the verification pass thinks, when Verify extraction is on. It always runs on the extractor model. |
| Local model URL | *(unset)* | Address of a local or self-hosted OpenAI-compatible model server, for the `local` backend. |
| Local context window | `8000` | Context window (in tokens) of the local model, since Watchdog cannot infer it from an arbitrary self-hosted model name. |
| OpenRouter URL | `https://openrouter.ai/api/v1` | Address for the OpenRouter backend. Change only to point at an OpenRouter-compatible proxy. |

Each model field is a picker: models are grouped by provider, with price per million tokens and context window, a search box, and room to type any `backend:model` value (see [Model backends](#model-backends)). A model field takes a Claude tier (`haiku`, `sonnet`, `opus`) or a `backend:model` value.

The classifier default is Haiku because picking a skill is easy work. The finalizer default is Haiku because it works from compact digests rather than raw documents. The finalizer also reconciles duplicate entities and flags contradictions between documents, the pipeline's two hardest judgements, so raise it if synthesized prose feels thin, if duplicate entities are slipping through, or if cross-document contradictions are being missed. It runs only a few times per batch regardless of how many documents you feed it, so raising it costs far less than raising the extractor.

When choosing among models, [Benchmarks](benchmarks.md) shows how each model and effort level actually performs on real documents, not just its price.

For one batch only, the Options in the **Add documents** dialog and on the Dig and Bark cards under Activity → Maintenance override these fields without changing them.

### Deduplication

Near-duplicate detection.

| Field | Default | What it controls |
|---|---|---|
| Duplicate threshold | `0.85` | Similarity score at which two documents are flagged as near-duplicates (0.0 to 1.0). |
| Shingle size | `3` | Word-sequence length used for near-duplicate fingerprinting. |

Changing Shingle size invalidates existing fingerprints, so documents already added would need adding again to rebuild them.

### Search

Local semantic search over the documents. Both search models run entirely on your computer: no API calls, no cost, nothing leaves the machine.

| Field | Default | What it controls |
|---|---|---|
| Embedding model | `BAAI/bge-small-en-v1.5` | The local embedding model that indexes passages and notes for Search. |
| Reranker model | `BAAI/bge-reranker-base` | The local model that reranks search results for precision. `none` turns reranking off. |

Embedding model must be one the fastembed library can load. Stronger options include `BAAI/bge-base-en-v1.5` and `mxbai-embed-large-v1`. Vectors from two models are not comparable, so after changing it, run **Rebuild the search index** under Activity → Maintenance. It rebuilds from disk, with no need to add documents again.

Reranker model is the biggest retrieval-quality lever. It is downloaded during setup (about 300 MB), or on first search if missing. A lighter option is `Xenova/ms-marco-MiniLM-L-6-v2`. A change needs no rebuild, because reranking runs fresh at search time, and the **Re-rank passages** switch on the Search screen skips it for a single search.

### Research

Web research mode: the default effort budget.

| Field | Default | What it controls |
|---|---|---|
| Research max rounds | `3` | Search rounds a standard research session makes before checking in. |
| Research max fetches | `25` | Roughly how many web sources a standard research session captures into `incoming/`. |

Both are advisory budgets that the research session limits itself to. The quick and deep effort tiers (chosen in the session) scale them down or up. Research max fetches bounds scope and later extraction cost, not the research session's own tokens, because each captured source is read by the local pipeline afterward.

### Web archiving

Optionally save research sources to the Internet Archive's Wayback Machine.

| Field | Default | What it controls |
|---|---|---|
| Save to the Wayback Machine | off | Also submit every research source to the Wayback Machine. |
| Wayback access key | *(unset)* | Your archive.org access key. Shown masked. |
| Wayback secret key | *(unset)* | The matching archive.org secret key. |

With Save to the Wayback Machine on, every source that Web research or **Fetch Links** downloads is also submitted to the Internet Archive, and the snapshot address is recorded in the source's provenance record. That gives you a citable copy that survives if the original changes or is taken down. It does nothing until both keys are set; a free pair can be generated at [archive.org/account/s3.php](https://archive.org/account/s3.php). Archiving is best-effort and never blocks or fails a download.

### Privacy

The local record of model calls across your investigations.

| Field | Default | What it controls |
|---|---|---|
| Telemetry | on | Keep a local record of every model call. It never leaves your computer. |

Watchdog keeps a database of every model call it makes, `telemetry.db` in its settings folder. Each row records the model, the tokens used, the cost and the time taken, along with the path and name of the investigation and the filename of the document the call was about. It holds no document text. It exists so cost and speed can be compared across runs and models.

Because it lists the documents in every one of your investigations, treat the file as sensitive: keep it out of shared folders and backups that others can reach. Turning Telemetry off stops recording; rows already written stay until you delete the file. **Remove and delete files…** on the All investigations screen removes an investigation's rows along with the investigation, even if you have already deleted its folder yourself. Renaming or moving an investigation carries its rows along, so a later purge still finds them. Each investigation's own usage files, which **Activity → Usage** reads, are separate and unaffected.

## App settings

### Models & keys

How Watchdog signs in to Claude and to each model provider.

**Claude Code** is required for Ask Claude and Web research, and it is the default for adding documents. A badge shows whether you are signed in. **How Claude is billed** chooses between two modes:

- **Subscription.** Uses your Claude subscription, which is not metered. A Pro plan (US$20 a month) is enough for most journalism work. If you add hundreds of documents at a time, a Max plan gives higher session limits.
- **API key.** Bills per token to your Anthropic account. Paste the key when asked.

If Claude Code is not installed, the tab says so; it ships with the engine, and **Settings → Setup** can repair it. Switching to subscription mode and keeping ingestion on it lowers Extraction concurrency from 20 to 3, because concurrent extractions on that path share one Claude Code session's rate limit. Switching back to an API key restores it to 20 automatically, as long as you never set your own value.

**Ingestion stages** is a read-only table: for each step, which model it uses, which provider that is, whether the provider is ready (a key is stored, or its environment variable is set), and how it is billed. Change the models themselves under the **Models** tab.

**Provider keys** lists each provider (Claude, OpenAI, DeepSeek, Google Gemini, OpenRouter and Local model) with the key stored for it, shown masked. **Add** or **Replace** a key, or delete a stored one. Keys are kept on your computer in a file only you can read, and are never shown in full. A key set in your environment (for example `OPENAI_API_KEY`) always takes precedence over a stored one, and cannot be removed here. A stored Anthropic key is only used while Claude is in API-key mode. Each badge says whether a key is in use.

**Custom endpoints** sets the base addresses for a self-hosted model and for an OpenRouter-compatible proxy (the same as Local model URL and OpenRouter URL on the Models tab).

### Folder access

The folders Watchdog may read and change. Watchdog only changes files in folders you have allowed. Choosing a folder for it to use (a home for new investigations, an existing investigation, an export destination) allows it. Anything else, including a Claude session following instructions found in a document, is refused. Watchdog's own settings, the system's temporary folder and its downloaded models are always available to it.

**Allow a folder…** adds one. The table lists each allowed folder, what it was allowed for and since when, and the bin button removes access. Removing access deletes nothing; investigations inside that folder ask for access again before Watchdog works in them. The protection stops mistakes and prompt injection from changing files through Watchdog itself. It is not a full sandbox, and it does not limit what Watchdog can read.

### Record skills

Lists every record skill, with a filter box. Select one to read it. Your own skills carry a **Yours** label, and **Show in folder** opens the folder where you add them. See [Skills](skills.md).

### Appearance

**Theme** is System (which follows your computer's light or dark setting), Light or Dark.

**Python backend** describes the Watchdog program the app runs behind the scenes: its location, version, how the app found it (its own engine, a separate Watchdog installation, or one you chose), the platform, the settings file and the data folder. **Restart backend** restarts it, and **Choose Python…** points the app at a different Python, which only developers need.

### Check vaults

Checks every registered investigation for a folder that has moved or been deleted, or one that is no longer a Watchdog investigation. Nothing is changed; each problem comes with the fix. **Check again** reruns it.

### Setup

The state of the engine and its parts.

- **Engine.** The private Python environment Watchdog runs in, installed and kept up to date by the app. It shows the status, version and location. **Repair or reinstall the engine** removes the environment and installs it again without touching your investigations, settings or downloaded models. It needs an internet connection and can take several minutes. **Download missing models** fetches any local model that is not yet on the computer.
- **Local models.** Whether the document-conversion models (Docling), the name-detection model (GLiNER), the search embedding model and the search reranker are present, along with the OCR engine and Claude Code.
- **Helper tools.** Optional programs: qpdf (repairs damaged or protected PDFs), Ghostscript (re-renders problem PDFs) and Tesseract OCR. Watchdog reads scanned pages with its own built-in engine, so none is required.
- **Optional and downloaded pieces.** The capture browser (lets web pages be saved as full snapshots), whether an investigations folder is chosen, and whether the settings file exists.

### About

The installed version, and links to the project's page, the issue tracker and the install guide. To check for a new version of the app, choose **Check for Updates…** (in the Watchdog menu on macOS, in the Help menu on Windows and Linux). The app checks quietly after it starts and never downloads an update without being asked.

## Common changes

- **Use a different OCR engine.** Settings → OCR → OCR engine.
- **Move new investigations to an external drive.** Settings → Vaults → Investigations folder, then choose the folder.
- **Speed up a batch of text-only documents.** Settings → Chew → turn off Table structure.
- **Use Haiku for extraction** (faster and cheaper). Settings → Models → Extractor model.
- **Spend fewer thinking tokens on extraction.** Settings → Models → Extractor effort → `low`.
- **Hit model rate limits.** Settings → Ingest → lower Extraction concurrency.
- **Keep classification on your own computer.** Settings → Models → set Local model URL, then choose a `local:` model as the Classifier model. See [Local and self-hosted models](#local-and-self-hosted-models).

## Model backends

Backend choice applies only to the extraction pipeline, the bounded reasoning steps that run when you add documents. **Ask Claude** and **Web research** are not affected: they are open-ended, multi-turn sessions that run inside Claude Code, on Claude, always. The pipeline steps are single-shot calls, which tolerate a cheaper provider far better.

Watchdog is designed around Claude and uses it by default, with no setup beyond signing in. But each step (classification, extraction, finishing) can run on a different provider, and each has its own model field. See [Controlling cost](#controlling-cost) for OpenAI's GPT-5.6 Luna, the benchmark-recommended alternative for extraction. A model field takes either a Claude tier (`haiku`, `sonnet`, `opus`, routed by your Claude billing mode) or a `backend:model` value naming the provider and its model:

| Value | Runs on |
|---|---|
| `sonnet` | Claude, via your billing mode (subscription or API key). Currently resolves to Claude Sonnet 5.5. |
| `sonnet-4.6` | Claude Sonnet 4.6, explicitly. It was the `sonnet` default until Sonnet 5.5 took over; use this to keep an existing setup on 4.6. |
| `sonnet-5.5` | Claude Sonnet 5.5, explicitly. This is the model `sonnet` resolves to today, pinned by name in case the bare default ever moves. It accepts a wider range of effort levels (up to `xhigh`) than Sonnet 4.6; see [Controlling cost](#controlling-cost). |
| `sonnet-5` | Claude Sonnet 5, the previous generation, at the same price as 5.5. |
| `opus` | Claude, via your billing mode. Currently resolves to Claude Opus 5.5. |
| `opus-4.8` | Claude Opus 4.8, explicitly. It was the `opus` default until Opus 5.5 took over; use this to keep an existing setup on 4.8. |
| `opus-5.5` | Claude Opus 5.5, explicitly. This is the model `opus` resolves to today, pinned by name. |
| `opus-5` | Claude Opus 5, the previous generation, at a higher price than 5.5. |
| `claude-api:opus` / `claude-agent-sdk:sonnet` | Claude, forcing a specific backend. |
| `openai:gpt-5-mini` | OpenAI. |
| `openai:gpt-6-luna` | GPT-6 Luna, OpenAI's current high-volume model, cheaper than GPT-5.6 Luna ($0.10 against $0.20 per million input tokens, $0.50 against $1.20 for output). The benchmark figures below were measured on GPT-5.6 Luna, not this model. |
| `deepseek:deepseek-flash` | DeepSeek V4.1 Flash, non-thinking (append `-thinking` to enable thinking mode). Rates double during DeepSeek's peak hours; see [Controlling cost](#controlling-cost). DeepSeek has retired the older `deepseek-v4-flash` name and answers it with this model at the same price, so a setup that still names it keeps working and is priced correctly. |
| `deepseek:deepseek-v4-pro` | DeepSeek V4 Pro, non-thinking (append `-thinking` to enable thinking mode). Same peak-hour pricing as Flash. |
| `gemini:gemini-3.7-flash` | Gemini 3.7 Flash, stable, 1-million-token context window. |
| `gemini:gemini-3.5-flash-lite` | Gemini 3.5 Flash-Lite, stable, the cheapest Gemini tier. |
| `gemini:gemini-3.1-pro-preview` | Gemini 3.1 Pro, a preview release; Google may deprecate preview model ids on short notice. |
| `local:llama-3.3-70b` | A model on your own computer or network: Ollama, LM Studio, llama.cpp's server, vLLM, or anything else speaking the OpenAI-compatible wire format. Requires Local model URL; usually no key. |
| `openrouter:anthropic/claude-3.5-sonnet` | [OpenRouter](https://openrouter.ai): one key routes to many hosted models, named exactly as OpenRouter itself lists them. |

To point a step at a provider, open **Settings → Models**, open that step's model picker, and choose a model, or type a `backend:model` value into its search box and press Enter. If you pick a model from a provider you have no key for yet, add the key under **Settings → Models & keys → Provider keys**; the Ingestion stages table there shows whether each step is ready. First-run setup walks through the same choices: first how Claude Code itself signs in (required regardless of what ingestion uses, because Ask Claude always runs on Claude), then, independently, which provider handles ingestion. For one batch only, use the model pickers in the Options of the **Add documents** dialog.

Each step is independent: you can keep extraction on Claude Sonnet while routing the cheaper classification or finishing steps to another provider. One honest caveat: non-Claude backends are unproven on dense legal and financial extraction, so the defaults stay on Claude and nothing routes elsewhere unless you ask.

The effort fields are model-specific. Setting one on a step routed to a model that does not support that level (or does not support effort at all, like Claude Haiku) fails with an error rather than running silently at a different effort than you asked for; see [Controlling cost](#controlling-cost). DeepSeek thinking mode is off by default and enabled by appending `-thinking` to the model name (for example `deepseek:deepseek-flash-thinking`). Extraction is schema-bound structured output, so non-thinking is the cheaper, more predictable default, with thinking available for judgement-heavy cases. Thinking mode also changes the extraction prompt: a model that can reason privately gets a short instruction to do so, while one that cannot is walked through the same steps in its visible answer. DeepSeek's effort control works only in thinking mode, and takes `low`, `high` or `max`. Asking for effort on a non-thinking DeepSeek model fails, because there is no thinking for it to tune, and `medium` and `xhigh` fail too, since DeepSeek treats both as another name for `high`. Gemini has no equivalent thinking toggle; its reasoning effort is driven entirely by the effort fields.

### Local and self-hosted models

Cost is one reason to run a step on a model on your own computer or network, and not the interesting one. The real reason is documents that cannot leave the building: a leaked document set, an unpublished investigation, anything with a source's fingerprints on it. Every backend above, Claude included, sends document text to somebody else's server. A `local` model, pointed at a runner on hardware you control, is the one configuration where it never does.

`local` works with any server that speaks the OpenAI-compatible Chat Completions wire format: Ollama, LM Studio, llama.cpp's server, vLLM and others. Set **Local model URL** (for example `http://localhost:11434/v1`, Ollama's default) and choose a model such as `local:llama-3.3-70b` for a step.

Most self-hosted runners do not check for an API key at all, so `local` does not ask for one unless you add it under Provider keys (some gateways in front of a local model do check). Because a self-hosted model's name carries no vendor namespace, Watchdog cannot infer its context window the way it does for a hosted model. Set **Local context window** to the real figure (check your model's card or your runner's documentation) so sectioning sizes sections correctly. Left unset, Watchdog assumes a conservative 8,000 tokens, which errs toward more, smaller sections rather than risking an overrun.

Usage reports a local call's cost as $0. That is accurate, since there is no per-token bill, but $0 is not the same as free: a local model spends wall-clock time instead, and a "local model" note next to the usual figures says so, so a run that took an hour does not read as having cost nothing.

**The honest caveat, sharper here than anywhere else on this page:** the pipeline does not chat, it demands schema-valid JSON and retries on failure. That is the hardest thing to get reliably out of a small local model, and the failure mode is easy to miss: not a crash, but a quietly thinner extraction, with fewer facts, dropped relationships and elided quotes. This has not yet been run through Watchdog's own extraction benchmark the way Claude, DeepSeek and OpenAI have. Until it is, treat the classifier and finalizer (short input, more forgiving output) as the first things worth trying locally, and be skeptical of a local extractor on dense legal or financial material. See [Benchmarks](benchmarks.md) for how that comparison is run and what it has found so far.

OpenRouter is the same mechanism with a fixed, hosted endpoint and a required key. It is useful for reaching a model Watchdog has no dedicated backend for, but it does send document text off your computer to OpenRouter and whichever model it routes to, like any other hosted provider.

### Batch mode: bulk extraction at half price

If you are adding a large dump, say 200 pages or more, a batch-mode Extractor model submits every whole-document extraction as one bulk batch at 50 per cent off every token. The tradeoff is latency, not cost: a batch typically finishes within an hour but can take up to 24, so the run submits it and ends rather than waiting. Run **Dig** again later (the Overview shows when documents are waiting) to collect the results.

Two batch backends are available, one per provider:

- `claude-batch:sonnet` (or any Claude tier) uses Anthropic's Message Batches API. It requires Claude to be in API-key mode, because batching is not available on a Claude subscription.
- `openai-batch:gpt-5.6-luna` (or any OpenAI model) uses OpenAI's Batch API. OpenAI has no subscription mode in Watchdog at all, so this just needs a stored OpenAI key.

Each needs only that provider's own key: an `openai-batch` extractor needs no Anthropic key, and the other way round.

The documents do not have to be the same type. Each one works out its own record skill before the batch is built, from its own sidecar file if it has one, otherwise the batch's Record skill option if you set one, otherwise a quick classification. A mixed drop of court filings and financial statements batches fine, each read with the right skill.

Two constraints are enforced with a clear error:

1. A batch backend is valid only as the Extractor model, not the Classifier model or Finalizer model.
2. A document large enough to need sectioned extraction cannot go through the batch, so those are extracted through that provider's regular single-call backend (`claude-api` or `openai`) instead, automatically, and the run's output says so.

Classification itself is not batched: it stays one quick call per document, at the classifier model's price. That is deliberate. It is a cheap call on a short excerpt, and paying it is what removes the need to sort your documents by type first.

This is also the recipe for keeping a Claude subscription's session limits for interactive work only, spending none on bulk extraction:

1. In **Settings → Models & keys**, switch **How Claude is billed** to API key.
2. In **Settings → Models**, set Classifier model to `claude-api:haiku`, Extractor model to `claude-batch:sonnet` and Finalizer model to `claude-api:haiku`.
3. Under **Activity → Maintenance**, choose **Run dig…** on the Dig card. It submits the batch and ends.
4. Later, run Dig again to collect the batch once it is ready.

The same recipe works with `openai-batch:gpt-5.6-luna` in place of `claude-batch:sonnet`, for a corpus already routed to OpenAI. Step 1 is not needed, since OpenAI has no subscription mode to switch from.

## Controlling cost

Watchdog is built to keep token costs predictable. Everything mechanical runs on your computer and costs nothing: OCR, document conversion, search indexing, reranking, the lead sweep and the watch-list scan. The model is called only for the reasoning steps: classify, extract, reconcile entities and contradictions, synthesize, reconcile the timeline and write the briefing. Each document's classification loads only the single matching record skill, not all of them.

The main levers, roughly in order of impact:

- **Effort.** Thinking tokens bill as output, so Extractor effort is the biggest per-run lever. It already defaults to `medium`, because benchmark testing found no recall difference against `high` for Sonnet, the shipped default model, at meaningfully lower cost. `low` is worth trying on a test batch if you want to cut cost further. That default is skipped automatically if the Extractor model is routed to a model with no effort control at all (Haiku). Finalizer effort has no such default (nothing is sent unless you set it), which is why the finalizer's default model, Haiku, needs no special case. Classifier effort defaults to `low`, applied only when the classifier is routed to a model that supports it (Haiku, the default, does not). `xhigh` and `max` push past `high` for the hardest documents at a further cost premium, but support is not universal. OpenAI's GPT-5.6 family takes both, and Claude's coverage varies by model: Sonnet 4.6 takes `max` but not `xhigh`, while Sonnet 5, Opus 4.8 and Opus 5.5 take both. Setting any effort field to a level the resolved model does not support fails with a clear error rather than running at a different effort than you asked for. Model and effort are configured together, so changing one is worth a second look at the other. See [Benchmarks](benchmarks.md) for the methodology behind the `medium` and `high` defaults, and for `high` as the recommended effort if you switch the extractor to Luna below, whose recall climbs with effort on the benchmark corpus, unlike Sonnet's flatter curve.
- **Thinking is on for every Claude model.** Sonnet 4.6 and Opus 4.8 previously shipped with Anthropic's extended thinking off, so the effort fields tuned response length on them but never actually turned reasoning on. Watchdog now enables it on both, matching Sonnet 5, Opus 5 and every other current Claude tier. Thinking bills as output, so if you were relying on the old effort-only cost profile, expect the effort levers to matter more than they used to.
- **Models.** Setting the Extractor model to Haiku is cheaper and faster for large batches of straightforward documents; Sonnet handles complex or ambiguous ones better. The classifier and finalizer already default to Haiku. **Recommended alternative: `openai:gpt-5.6-luna`**, with Extractor effort `high` and Classifier effort `low`, for extraction and classification. It beat Sonnet at low effort outright in benchmark testing against real court-and-financial filings; see [Benchmarks](benchmarks.md) for the numbers and what is still unmeasured (the finalizer default, and every effort tier below `high`). It is not the shipped default because it needs its own OpenAI key even on a plain Claude subscription. If just one finishing stage needs a stronger model (the briefing reads thin, or duplicate entities keep slipping through reconciliation), the four per-stage finalizer fields raise that one stage without paying a stronger model's cost on the other three. Each falls back to the Finalizer model when left unset.
- **The verification pass.** Verify extraction is off by default and costs roughly 15 per cent more per run on the Claude API path, more on an OpenAI-compatible model; see [The verification pass](#the-verification-pass). If you turn it on, leave Verifier effort at `low`.
- **Batch mode.** The [batch-mode recipe](#batch-mode-bulk-extraction-at-half-price) halves the cost of a bulk run, on either Claude (metered key) or OpenAI.
- **When you run it, if you are on DeepSeek.** DeepSeek is the one provider here that charges by the clock: every rate doubles during its peak hours, 01:00 to 04:00 and 06:00 to 10:00 UTC (21:00 to 00:00 and 02:00 to 06:00 Eastern), Monday to Friday, and is half that the rest of the time, including all weekend. A daytime run in North America is already off-peak; an overnight batch on a weekday may not be. DeepSeek also treats Chinese public holidays as off-peak, which Watchdog does not model, so a run on one is quoted at the peak rate and the figure is an over-estimate. Watchdog prices each call at the rate in force when the call is made, so Usage reports what you were actually billed, and estimates quote the rate in force when you ask, marking the figure when it is the peak one.
- **Which Claude backend you are on.** A plain `sonnet` (or `haiku` or `opus`) reaches Claude one of two ways, chosen by your billing mode: a subscription goes through Claude Code's own harness, and a metered key goes straight to the API. The two bill different numbers of input tokens for identical documents, so it is worth knowing which one you are on; Usage names the backend for every stage. The API path also caches the reusable part of the prompt (the instructions and the record skill) properly, which the subscription path cannot be told to do.
- **Concurrency.** Extraction concurrency does not change total cost, but lowering it (in Settings, or per batch with **Documents at once** in the Options) is the fix when you hit model rate limits. The app lowers the default from 20 to 3 when you keep ingestion on Claude subscription sign-in, since concurrent extractions on that path share one Claude Code session's rate limit. Setting your own value always overrides both directions.
- **Token budget.** Extraction concurrency caps how many documents run at once, but a rate limit is really a cap on tokens per minute, and a batch of large documents can trip it well under your concurrency limit. Watchdog holds back a new document automatically once the run's own recent pace gets close to your provider's real limit, discovered from the provider's own responses. There is nothing to configure on the Claude API, OpenAI, DeepSeek, Gemini, local and OpenRouter routes. Claude subscription sign-in does not report this number, so if you are on that path and still hit rate limits after lowering concurrency, set Extraction token budget to a number from your account's rate-limits page.

Before committing to a large run, get a number. In the **Add documents** dialog, **Estimate cost** quotes the batch before anything is sent; the **Dig** and **Bark** cards under Activity → Maintenance have **Estimate** buttons too. The estimate gives a token count for the queue. On a metered key with prior runs in the investigation, it adds a rough dollar range projected from your own usage history; on a subscription, only the token estimate is shown. Use it to decide whether to split a batch. **Compare all models** projects the same estimate across every model in the catalogue.

A failed document never sinks a batch: it is set aside and the rest completes. But for very large collections, add documents in groups anyway. For an unattended overnight batch on a subscription, switch on **Wait out rate limits** in the Options of the Add documents dialog, which sleeps through rate limits and resumes. If you would rather not wait at all, Anthropic's own [usage credits](https://support.claude.com/en/articles/12429409-manage-usage-credits-for-paid-claude-plans) let a Pro or Max plan keep going past its session or weekly limit at standard API rates once you enable them (Settings → Usage on claude.ai, payment method required). That is an account-wide setting, not something Watchdog configures, but Watchdog's rate-limit handling only fires on an actual rejection, so it will not interfere once credits are covering the overage.

Where next: [The desktop app](app.md) for a tour of the screens, or [Skills](skills.md) for what Default skill can be set to.
