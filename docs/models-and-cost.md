# Models, cost and settings

Watchdog uses an AI model at three steps: to classify each document, to extract its facts, and, once per batch, for the finishing steps (matching entities, flagging contradictions, writing summaries, the timeline and the briefing). This page explains how Watchdog reaches those models, which ones it uses, what a run costs and how to control it. The second half is a reference for every field on the **Settings** screen.

The defaults are sensible, and most people only ever change a few of these.

## Claude: subscription or API key

Watchdog is built around Claude and uses it by default. **Settings → Models & keys → How Claude is billed** chooses how Watchdog reaches it:

- **Subscription.** Your Claude [Pro or Max plan](https://claude.com/pricing), signed in through your browser. It is not metered. A Pro plan (US$20 a month) is enough for most journalism work.
- **API key.** A key from the [Anthropic Console](https://console.anthropic.com), billed per token (the units models read and write, roughly three-quarters of a word each) to your Anthropic account.

A subscription has one usage limit, shared with the rest of your Claude use, and a batch of large documents can reach it. When it does, the run stops and keeps every document it finished; switch on **Wait out rate limits** in the Add documents options to have it wait and carry on instead. If you add hundreds of documents at a time, a Max plan raises the limit, and an API key avoids it altogether: on a key, the default model costs roughly US$1 per 1,000 pages. Anthropic's [usage credits](https://support.claude.com/en/articles/12429409-manage-usage-credits-for-paid-claude-plans) can also let a Pro or Max plan continue past its limit at API rates.

**Ask Claude** and **Web research** always run on Claude, so they need a Claude sign-in or key whatever the other steps use.

## Choosing models

Each step has its own model, set under **Settings → Models**. All three default to Claude Haiku 5.5, Claude's fast, inexpensive model, with extraction set to think hard (the `high` effort level). In Watchdog's own testing, that combination found the most of any setting measured (see [How the defaults were chosen](#how-the-defaults-were-chosen)).

**Effort** sets how much a model thinks before it answers. Thinking is billed as output, so extractor effort is the biggest cost lever after the model itself. Lower it to `medium` or `low` to cut cost, at some loss of what is found; `xhigh` and `max` go further for the hardest documents, on the models that support them.

The main alternatives for extraction:

- **Claude Sonnet 5.5** (`sonnet`). Several times faster per page than Haiku at `high`, at about five times the cost, and it found less in testing. The choice when speed matters more than recall.
- **OpenAI's GPT-6 Luna** (`openai:gpt-6-luna`) at `medium` effort. About a fifth of Haiku's cost per page, and it found less than Haiku at `high`. It needs an OpenAI key.

The finishing steps run only a few times per batch, however many documents you add, so raising their model costs far less than raising the extractor's. If the briefing reads thin, or duplicate entities or contradictions keep slipping through, the four per-step finalizer fields raise just that step.

### Using GPT-6 Luna

1. Go to [platform.openai.com](https://platform.openai.com), sign in or create an account, and open **API keys**.
2. Add a payment method under **Settings → Billing**. OpenAI's API has no free tier, so a card must be on file before a key can make paid calls.
3. Create a secret key and copy it. It is shown only once.
4. In Watchdog, open **Settings → Models & keys** and paste the key under OpenAI. Then, under **Settings → Models**, set Extractor model to `openai:gpt-6-luna` and Extractor effort to `medium`.

## Other providers

Any step except Ask Claude and Web research can run on another provider, with that provider's own key. A model field takes a Claude tier (`haiku` or `sonnet`, reached through your Claude billing mode) or a `provider:model` value. The picker lists each provider's current models, grouped by provider with their prices; you can also type a value and press Enter.

| Value | Runs on |
|---|---|
| `haiku` | Claude Haiku 5.5, the default for every step. US$0.10 in and US$0.50 out per million tokens for requests up to 100,000 tokens, which Watchdog keeps each request under. |
| `sonnet` | Claude Sonnet 5.5: US$2 in and US$10 out per million tokens. |
| `haiku-5.5` / `sonnet-5.5` | The same models pinned by name, in case the bare tier ever moves. |
| `openai:gpt-6-luna` | GPT-6 Luna, OpenAI's high-volume model: US$0.10 in, US$0.50 out. `openai:gpt-5.6-luna` is the previous generation. |
| `gemini:gemini-3.8-flash` | Gemini 3.8 Flash: US$0.75 in and US$3.75 out until 31 December 2026, then double ([Google's prices](https://ai.google.dev/gemini-api/docs/pricing)). `gemini:gemini-3.5-flash-lite` is Google's cheapest tier. |
| `deepseek:deepseek-flash` | DeepSeek V4.1 Flash. `deepseek:deepseek-v4-pro` is DeepSeek V4 Pro. Append `-thinking` to either for thinking mode. Rates double during DeepSeek's peak hours (below). |
| `openrouter:<vendor>/<model>` | [OpenRouter](https://openrouter.ai): one key that routes to many hosted models, named as [OpenRouter lists them](https://openrouter.ai/models). |
| `local:<model>` | A model on your own computer or network; see [Local and self-hosted models](#local-and-self-hosted-models). |

Each provider's flagship models are left out: they are built for harder work than any step here, and cost up to five times as much as Sonnet. Other providers' models have been measured only on Watchdog's small test set, so the defaults stay on Claude and nothing goes elsewhere unless you choose it.

Effort levels vary by model. Every current Claude and OpenAI model takes all five (`low` to `max`); Gemini takes `low`, `medium` and `high`; DeepSeek takes `low`, `high` and `max`, and only in thinking mode. Asking a model for a level it does not support fails with a clear error, rather than quietly running at another.

**DeepSeek charges by the clock.** Every rate doubles during its [peak hours](https://api-docs.deepseek.com/quick_start/pricing), 01:00 to 04:00 and 06:00 to 10:00 UTC (21:00 to 00:00 and 02:00 to 06:00 Eastern), Monday to Friday. A daytime run in North America is off-peak; an overnight weekday batch may not be. Watchdog prices each call at the rate in force when it is made.

## Local and self-hosted models

A model on your own computer or network costs nothing per page, but the main reason to use one is text that cannot leave the building. Every provider above, Claude included, sends document text to somebody else's server; a local model, on hardware you control, is the one configuration where it never does.

`local` works with any server that speaks the OpenAI-compatible format: [Ollama](https://ollama.com), [LM Studio](https://lmstudio.ai), [llama.cpp](https://github.com/ggml-org/llama.cpp)'s server, [vLLM](https://github.com/vllm-project/vllm) and others. Set **Local model URL** (for example `http://localhost:11434/v1`, Ollama's default), choose a model such as `local:llama-3.3-70b` for a step, and set **Local context window** to the model's real figure, since Watchdog cannot work it out (left unset, it assumes a conservative 8,000 tokens). Most local servers need no key.

When every step of a run uses `local` and the address is on this computer (`localhost`, `127.0.0.1`), Watchdog skips the public-records pause, since nothing goes to anyone else's server. A server at any other address, even on your own network, is another computer, so the pause still appears. Watchdog cannot see what your server does with the text.

**A caveat:** the pipeline demands strictly structured answers, which is the hardest thing to get reliably from a small local model. When it goes wrong, the run doesn't crash; the extraction just comes back thinner, which is easy to miss. Local models have not been through Watchdog's benchmark. Try the classifier and finishing steps locally first, and be sceptical of a local extractor on dense legal or financial material. Usage shows a local call's cost as $0, with a note that it spent time instead.

## Your keys

**Settings → Models & keys** holds a key for each provider (Claude, OpenAI, DeepSeek, Google Gemini, OpenRouter and Local model), shown masked. A **Processing stages** table shows, for each step, which model and provider it uses and whether that provider is ready.

Keys are kept in a file in Watchdog's settings folder, `credentials.json`, which only your user account can read, and each key is encrypted with your computer's secure storage: your login Keychain on a Mac, Windows' data protection, or your desktop's keyring on Linux. A key encrypted on one computer cannot be read on another; replace it there. On a Linux computer with no keyring, keys stay unencrypted in the private file, and the screen warns you.

**Use a key made for Watchdog, with a spending limit.** Create a separate key for Watchdog with each provider, and give its account or project a monthly limit, so a mistake or a stolen key cannot run up an open-ended bill. In Anthropic's Claude Console, set a monthly spend limit under Settings → Billing, or put the key in its own workspace with its own limit. In OpenAI's platform, create a project for Watchdog with its own key and a hard spend limit; spend alerts only send an email. (Checked against the providers' documentation in October 2026.)

### More than one key, and billing

You can keep several named keys for one provider, to bill different accounts: a personal OpenAI key and one your newsroom pays for. Use **Add another** beside the provider, and give the key a short name. One key is the **default**; the **⋯** menu beside a key makes it the default, renames it, replaces it or deletes it.

<a id="billing"></a>With more than one key for a provider, a **Billing** card on each investigation's Overview chooses which one pays for that investigation's model calls, including Ask Claude when Claude is on an API key. Only the key's name is saved, inside the investigation's folder, so it travels with the folder; on another computer Watchdog uses that computer's key of the same name. If there is none, Watchdog stops before sending anything rather than bill another account. **Activity → Usage** shows the cost per key.

## Controlling cost

Everything mechanical runs on your computer and costs nothing: OCR, conversion, search, the lead sweep and the watch-list scan. The model is called only for classifying, extracting and the finishing steps, and each document loads only the one record skill that matches it.

**Get a number first.** **Estimate cost** in the Add documents dialog quotes a batch before anything is sent, and **Compare all models** quotes it across every model. On a metered key with earlier runs in the investigation, it gives a dollar range from your own history; on a subscription, a token count.

The main levers, roughly in order of effect:

- **The extractor's model and effort.** See [Choosing models](#choosing-models). Thinking is billed as output, so effort matters on every model that thinks.
- **Batch mode,** for large dumps. A batch extractor model (`claude-batch:haiku`, or `openai-batch:gpt-6-luna`) submits every extraction as one bulk job at half price. A batch usually finishes within an hour but can take up to 24, so the run submits it and ends; run **Processing** again later (the Overview shows when documents are waiting) to collect the results. Claude batches need an API key. Documents large enough to be split into sections, and the classifier and finishing steps, run normally.
- **The second-read check** (Verify extraction) adds roughly 15 per cent per run on a Claude API key, more on OpenAI. It is off by default.
- **Documents at once** does not change total cost, but lowering it is the fix when a run keeps hitting a rate limit. On a subscription it defaults to 3, since every extraction shares one usage limit.

On a Claude subscription, **Activity → Usage** shows what the work would have cost at published rates, marked as not billed.

## How the defaults were chosen

The defaults come from running a fixed set of real public documents (six filings from a university's insolvency proceeding, 209 pages) through every candidate model and effort level, and checking each result against an answer key. An AI judge that was not one of the models tested read each result blind. **Main facts** are a document's material content; **must not miss** are the items where a miss would cost the story, often a clause in standard wording or a line under a table.

The latest run, in October 2026, briefed every model with the same short investigation brief:

| Model and effort | Main facts | Must not miss | Cost per page | Time per page |
|---|---|---|---|---|
| Claude Haiku 5.5, high | 74% | 72% | $0.0009 | 4.1 s |
| Claude Sonnet 5.5, high | 67% | 65% | $0.0049 | 0.6 s |
| GPT-6 Luna, high | 55% | 44% | $0.0003 | 1.0 s |
| GPT-6 Luna, medium | 52% | 43% | $0.0002 | 0.5 s |
| Claude Sonnet 5.5, medium | 45% | 47% | $0.0035 | 0.4 s |
| Gemini 3.8 Flash, high | 41% | 44% | $0.0028\* | 1.2 s |
| Claude Haiku 5.5, medium | 39% | 39% | $0.0003 | 0.5 s |

\* At Google's promotional price, which ends on 31 December 2026.

None of these needs to reach 100% for Watchdog to be useful: its job is to surface facts for you to check, not to hand over a finished read. Every setting ran once, and single runs of one setting have differed by up to three times, so read a gap as a lead, not a result; a repeat with three runs each is planned. DeepSeek, local models and the finishing steps have not been measured in this run. The full results and method are in the repository's [`benchmarks/`](https://github.com/tomcardoso/watchdog/tree/main/benchmarks) folder.

## Settings reference

Open **Settings** from the sidebar (or **Settings…** in the Watchdog menu on a Mac, **File → Settings** on Windows and Linux). Settings apply to every investigation on this computer. Each field shows a one-line description with a **More** link, saves as you change it, and has **Reset to default**. Its stored name, such as `extractor_model`, is shown beside it in small grey type; everything is stored in `config.json` in Watchdog's settings folder (`.watchdog/` in your home folder).

For one batch only, the **Options** in the Add documents dialog override the processing and model fields without changing them.

### Vaults

| Field | Default | What it controls |
|---|---|---|
| Investigations folder | `~/Investigations` | Where each new investigation gets its own folder. Choosing a folder also allows Watchdog to write there. |

### OCR

| Field | Default | What it controls |
|---|---|---|
| OCR engine | `auto` | `auto` uses [Apple Vision](https://developer.apple.com/documentation/vision) on a Mac and [Tesseract](https://github.com/tesseract-ocr/tesseract) elsewhere; [`easyocr`](https://github.com/JaidedAI/EasyOCR) and [`rapidocr`](https://github.com/RapidAI/RapidOCR) need no system install but are less accurate on forms. |
| OCR languages | *(auto-detect)* | Languages for Apple Vision, as codes such as `en-US,fr-FR`. Set it only if detection does poorly. |
| Garbled-text threshold | `0.6` | One of three signals Watchdog uses to decide a PDF page's text layer is unreadable and needs OCR; at least two must agree, so dense but readable tables are not sent through OCR. |

### Pre-processing

| Field | Default | What it controls |
|---|---|---|
| Pre-processing workers | `auto` | Files read in parallel. `auto` adapts to the batch. |
| Chunk size | `40` | Pages per chunk when a large PDF is split for parallel reading. |
| Chunk workers | `auto` | Parallel workers for one large PDF's chunks. Workers and chunk workers multiply, so pin both low on a modest computer. |
| Chunk timeout | `300` | Seconds before a chunk is stopped. |
| Table structure | on | Table detection on PDFs. Turn it off to speed up text-only documents. |

### Transcription

| Field | Default | What it controls |
|---|---|---|
| Transcription model | `medium` | The speech-recognition model: `medium`, `large-v3-turbo`, `small` or `distil-large-v3` (English only). |
| Transcription language | `auto` | The recordings' language, as a code such as `en` or `fr`; `auto` detects it from the start of each recording. |

All the models are close on clear speech. The difference shows on a bad line, a busy room or an unusual name, which is most of what a reporter records, so the default is the most accurate rather than the fastest. In Watchdog's tests on a four-core computer, `medium` (1.5 GB) took 20 to 35 minutes for an hour of audio and got the most names right; `small` (486 MB) took 11 to 16 minutes but made about twice as many errors on poor audio. A change affects recordings added afterwards. Set a language when detection picks the wrong one, for example on a recording that opens with music.

### Processing

| Field | Default | What it controls |
|---|---|---|
| <a id="auto-approve"></a>Auto-approve | off | Skips the public-records pause when every step of the run uses your Claude subscription, showing a one-line notice instead. A run where any step uses a paid API key always asks. Turn it on only if you already check that what you add is public record. |
| Extraction concurrency | `20` (`3` on a Claude subscription) | Documents extracted at once. |
| Extraction token budget | `auto` | A cap on tokens per minute. `auto` uses the limit your provider reports; a Claude subscription reports none, so there it sets no cap. |
| Classify pages | `5` | Opening pages shown to the classifier. |
| Default skill | *(unset)* | Pins one [record skill](how-it-works.md#record-skills) for every document, skipping classification. |
| Section token threshold / budget / overlap | `auto` / `auto` / `4000` | How a very large document is split into sections for extraction. `auto` sizes sections from the extractor's context window and keeps each request under any price step. Leave them on `auto`. |
| Empty-extraction minimum words | `500` | A document with more words than this that comes back with no facts is retried once, then failed, rather than accepted as empty. |
| <a id="the-verification-pass"></a>Verify extraction | off | The second-read check: re-reads each document straight after extraction and adds material facts the first read missed. It can add restatements or minor details, which is why it is off by default. Not available with batch mode. |

### Models

| Field | Default | What it controls |
|---|---|---|
| Classifier model | `haiku` | Reads a document's opening pages and picks its record skill. |
| Extractor model | `haiku` | Extracts each document. |
| Finalizer model | `haiku` | The finishing steps. |
| Finalizer reconciliation / synthesis / timeline / briefing model | *(unset)* | Override the finalizer model for one finishing step: entity matching and contradictions, summaries, the timeline, or the briefing. |
| Classifier effort | `low` | How hard the classifier thinks. |
| Extractor effort | `high` | How hard the extractor thinks. |
| Finalizer effort | *(unset)* | Left unset, the model uses its own default. |
| Verifier effort | `low` | How hard the second-read check thinks. It always runs on the extractor model. |
| Local model URL / Local context window | *(unset)* / `8000` | The address and context window of a [local model](#local-and-self-hosted-models). |
| OpenRouter URL | `https://openrouter.ai/api/v1` | Change only to point at an OpenRouter-compatible proxy. |

### Deduplication

| Field | Default | What it controls |
|---|---|---|
| Duplicate threshold | `0.85` | Similarity at which two documents are flagged as near-duplicates. |
| Shingle size | `3` | Word-sequence length used for the comparison. Changing it invalidates existing fingerprints. |

### Search

| Field | Default | What it controls |
|---|---|---|
| Embedding model | `BAAI/bge-small-en-v1.5` | The local model that indexes passages for search by meaning. After changing it, run **Rebuild the search index** under Activity → Maintenance. |
| Reranker model | `BAAI/bge-reranker-base` | The local model that re-orders results. `none` turns it off. |

### Research

| Field | Default | What it controls |
|---|---|---|
| Research max rounds | `3` | Search rounds a standard Web research session makes before checking in. |
| Research max fetches | `25` | Roughly how many sources a standard session captures. |

### Web archiving

| Field | Default | What it controls |
|---|---|---|
| Save to the Wayback Machine | off | Also submits every source from Web research or **Fetch Links** to the Internet Archive's [Wayback Machine](https://web.archive.org), and records the snapshot's address, giving you a citable copy if the original changes. Best-effort; it never blocks a download. |
| Wayback access key / secret key | *(unset)* | A free pair from [archive.org/account/s3.php](https://archive.org/account/s3.php). |

### Verification

| Field | Default | What it controls |
|---|---|---|
| Your name | `Journalist` | The name recorded with each fact you mark Verified, Disputed or Can't verify. |

### Privacy

| Field | Default | What it controls |
|---|---|---|
| Telemetry | on | Keeps a local record of every model call (`telemetry.db` in Watchdog's settings folder): model, tokens, cost, time, and the investigation and file each call was about. It never leaves your computer and holds no document text, but it lists your documents' names, so keep it out of shared folders and backups. |

### App panels

- **Models & keys.** How Claude is billed, each provider's keys, and the Processing stages table; see [Your keys](#your-keys).
- **Folder access.** The folders Watchdog may change; see [Folder access](using-watchdog.md#folder-access).
- **Version history.** How much history the open investigation keeps, and **Clear history…**; see [History and your notes](using-watchdog.md#history-and-your-notes).
- **Record skills.** Every skill, to read, and the folder for your own; see [Record skills](how-it-works.md#record-skills).
- **Appearance.** Light, dark or system theme, and details of the engine the app runs.
- **Check vaults.** Checks every investigation's folder.
- **Setup.** The state of the engine, local models and optional tools. **Repair or reinstall the engine** reinstalls it without touching your investigations, settings or models; **Download missing models** fetches any that failed; **Download now** fetches the transcription model ahead of time.
- **About.** The app's version and the engine's, the one to give when reporting a problem.
