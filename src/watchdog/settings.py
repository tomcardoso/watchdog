"""Every setting in `~/.watchdog/config.json`: its type, default and help (as Settings shows it),
how Settings groups them, and the validation a new value goes through.

`SETTINGS[key]` holds `short` (one line), `help` (hard-wrapped prose, continuation lines indented
two spaces), `type` (`path`, `enum`, `int`, `int_or_auto`, `float`, `bool`, `lang_list` or a free
string), a `default` or a `default_fn` computed when shown, and for some keys `choices`, `min`,
`max` or `secret`.
"""

import sys
from pathlib import Path

from watchdog import defaults
from watchdog.pipeline import transcribe as _transcribe


class SettingError(Exception):
    """A value that fails validation; the message says why."""


SETTINGS = {
    # ── Project ───────────────────────────────────────────────────────────────
    "projects_dir": {
        "short": "Path where new investigation vaults are created",
        "help": (
            "The folder where Watchdog creates new investigations.\n"
            "  Set during setup; change it to create future investigations somewhere else.\n"
            "  Existing vaults are not moved."
        ),
        "type": "path",
        # So Settings' "Reset to default" sends a folder: an empty path is refused.
        "default_fn": "watchdog.projects:default_projects_dir",
    },
    # ── OCR ───────────────────────────────────────────────────────────────────
    "ocr_engine": {
        "short": "OCR engine for scanned documents (default: auto)",
        "help": (
            "OCR engine used when processing scanned documents.\n"
            "  auto:         Apple Vision on macOS (if ocrmac installed), Tesseract elsewhere.\n"
            "  apple_vision: Apple Vision only — macOS with ocrmac required.\n"
            "  tesseract:    Tesseract — requires system install (brew or apt install tesseract-ocr).\n"
            "  easyocr:      EasyOCR — pure pip install, no system deps, less accurate on forms.\n"
            "  rapidocr:     RapidOCR — lightweight, no C deps, fast.\n"
            "  Valid values: auto, apple_vision, tesseract, easyocr, rapidocr."
        ),
        "type": "enum",
        "default": "auto",
        "choices": ["auto", "apple_vision", "tesseract", "easyocr", "rapidocr"],
    },
    "ocr_languages": {
        "short": "Apple Vision OCR languages (comma-separated BCP 47 codes, e.g. en-US,fr-FR)",
        "help": (
            "Languages Apple Vision should try when reading scanned documents.\n"
            "  Leave unset to auto-detect from the image (macOS 13+).\n"
            "  Set explicitly if auto-detection produces poor results or you are on macOS 12.\n"
            "  Codes: https://developer.apple.com/documentation/vision/vnrecognizetextrequest"
        ),
        "type": "lang_list",
    },
    "garbled_threshold": {
        "short": "One of three OCR-trigger signals — alphanumeric ratio below which a PDF page counts as unreadable (default: 0.6)",
        "help": (
            "When reading a PDF, Watchdog samples each page's text layer and checks three\n"
            "  independent signals of whether it's actually readable: this alphanumeric ratio, a\n"
            "  word-shape check for tokens that look like language rather than symbol noise, and a\n"
            "  check of the page's embedded font data. OCR is only applied when at least two of\n"
            "  the three agree the page is unreadable — no single signal, including this one, can\n"
            "  trigger it alone. This keeps a table-of-contents page (many dot leaders, low ratio,\n"
            "  but still full of real words) from forcing OCR on an otherwise clean document.\n"
            "  Lower = this signal fires more easily. Valid range: 0.0–1.0. Default: 0.6."
        ),
        "type": "float",
        "default": 0.6,
        "min": 0.0,
        "max": 1.0,
    },
    # ── Pre-processing ────────────────────────────────────────────────────────────
    "chew_workers": {
        "short": "Parallel files during pre-processing ('auto' for adaptive, or a fixed number)",
        "help": (
            "Number of files converted simultaneously during pre-processing.\n"
            "  'auto' (default): Watchdog scans the batch before starting and sets this based on\n"
            "  median document length — more workers for short-doc batches, fewer for large PDFs.\n"
            "  Set to a whole number to pin the value regardless of batch content.\n"
            "  Set to 1 to process files one at a time."
        ),
        "type": "int_or_auto",
        "default": "auto",
        "min": 1,
    },
    "extract_concurrency": {
        "short": "Documents extracted in parallel during processing (default: 20)",
        "help": (
            "How many documents processing extracts simultaneously. Each runs a model\n"
            "  call, so this is bounded by your model rate limits — lower it if you hit throttling,\n"
            "  raise it for throughput.\n"
            "  Default: 20, minimum: 1 (sequential). Watchdog sets this to 3\n"
            "  automatically on Claude subscription auth that keeps processing — concurrent extractions\n"
            "  there share one Claude Code session's rate limit, and the higher metered-path default\n"
            "  reliably throttles it — and restore it to 20 automatically on switching back to an\n"
            "  API key, unless you've set your own value."
        ),
        "type": "int",
        "default": defaults.EXTRACT_CONCURRENCY,
        "min": 1,
    },
    "auto_approve": {
        "short": "Skip the public-records pause when every step runs on your Claude subscription (default: false)",
        "help": (
            "Before documents are sent to a model, Watchdog shows the 'Public records only'\n"
            "  warning and waits for you to acknowledge it. Set this to true and a run where every\n"
            "  step (classify, extract and the finishing steps) uses your Claude subscription goes\n"
            "  ahead with a one-line notice instead. Turning it on means you have already checked\n"
            "  that what you add is public record. A run where any step uses a paid API key still\n"
            "  asks, whatever it would cost. Default: false."
        ),
        "type": "bool",
        "default": False,
    },
    "extract_token_budget": {
        "short": "Cap on tokens/min during processing, overriding auto-discovery (default: auto)",
        "help": (
            "Processing holds back new documents when the run's own recent tokens per minute\n"
            "  get close to your provider's limit, which it learns from the provider's own responses\n"
            "  on the Claude API, OpenAI, DeepSeek, Gemini, local and OpenRouter routes. A Claude\n"
            "  subscription never reports a limit, so there nothing is held back: if a run hits one,\n"
            "  lower Extraction concurrency, or turn on Wait out rate limits in the run's options.\n"
            "  A number here sets the cap yourself, on any route.\n"
            "  Default: auto (use whatever the provider reports, or don't hold documents back at all\n"
            "  if it reports nothing)."
        ),
        "type": "int_or_auto",
        "default": "auto",
        "min": 1,
    },
    "classify_pages": {
        "short": "Pages shown to the document classifier during processing (default: 5)",
        "help": (
            "How many leading pages of each document the classifier reads to pick a record skill.\n"
            "  Uses min(page_count, this). More pages classify ambiguous documents better (e.g. a\n"
            "  cover letter before the real filing) at a small extra cost on the cheap classifier model.\n"
            "  Default: 5. Minimum: 1."
        ),
        "type": "int",
        "default": defaults.CLASSIFY_PAGES,
        "min": 1,
    },
    "default_skill": {
        "short": "Pin a record skill for every added document, skipping classification (default: unset)",
        "help": (
            "When every document in a vault is the same type, set this to a record-skill name\n"
            "  (see Settings → Record skills) or a path to your own skill file, to skip per-document\n"
            "  classification and use that one skill for all of them. Leave unset to classify\n"
            "  each document."
        ),
        "type": "string",
        "default": None,
    },
    "chunk_size": {
        "short": "Pages per chunk when splitting large PDFs for parallel processing (default: 40)",
        "help": (
            "PDFs with more pages than this value are split into chunks and processed in parallel.\n"
            "  Smaller chunks reduce peak memory per worker but add per-chunk overhead.\n"
            "  Larger chunks are more efficient on fast machines with ample RAM.\n"
            "  Pages OCR'd above 216 DPI count for their pixels: a chunk holds about as much as\n"
            "  this many pages at 216 DPI (D364).\n"
            "  Default: 40."
        ),
        "type": "int",
        "default": 40,
        "min": 1,
    },
    "chunk_workers": {
        "short": "Parallel subprocesses for large-PDF chunks ('auto' for adaptive, or a fixed number)",
        "help": (
            "Number of parallel subprocesses used when splitting large PDFs (>chunk_size pages).\n"
            "  'auto' (default): set adaptively based on median document length in the batch.\n"
            "  Works in tandem with chew_workers (pre-processing workers): total subprocess load for large-PDF batches\n"
            "  is approximately chew_workers × chunk_workers.\n"
            "  Set to 1 to disable within-file parallelism."
        ),
        "type": "int_or_auto",
        "default": "auto",
        "min": 1,
    },
    "chunk_timeout": {
        "short": "Seconds before a chunk subprocess is killed (default: 300)",
        "help": (
            "Each chunk subprocess is given this many seconds to complete before being killed.\n"
            "  Increase for very large or complex PDFs on slow machines.\n"
            "  Default: 300 (5 minutes)."
        ),
        "type": "int",
        "default": 300,
        "min": 1,
    },
    # ── Transcription (D273) ──────────────────────────────────────────────────
    "transcription_model": {
        "short": f"Speech-to-text model for audio and video (default: {_transcribe.DEFAULT_MODEL})",
        "help": (
            "The model that transcribes recordings, on this computer. It is downloaded the first\n"
            "  time a recording is added (or ahead of time from Settings, under Setup).\n"
            "  medium:          1.5 GB. The default: the most accurate on names and on noisy\n"
            "                   audio in Watchdog's tests, and the slowest (an hour of audio took\n"
            "                   20 to 35 minutes on a four-core computer).\n"
            "  large-v3-turbo:  1.6 GB. As accurate as medium on noisy audio, less so on names;\n"
            "                   usually faster than medium.\n"
            "  small:           486 MB. Fastest download; noticeably less accurate on poor audio.\n"
            "  distil-large-v3: 1.5 GB. English only.\n"
            "  Recordings already added keep the transcript they were given; changing this\n"
            "  affects recordings added afterwards."
        ),
        "type": "enum",
        "default": _transcribe.DEFAULT_MODEL,
        "choices": list(_transcribe.MODELS),
    },
    "transcription_language": {
        "short": "Language spoken in recordings (default: auto, detected per recording)",
        "help": (
            "The language recordings are transcribed in. 'auto' detects it from the first minute\n"
            "  or so of each recording. Set a language when every recording is in it, or when\n"
            "  detection picks the wrong one (a recording that opens with music, or with a\n"
            "  greeting in another language). Codes: en English, fr French, es Spanish,\n"
            "  de German, pt Portuguese, it Italian, zh Chinese, ar Arabic, uk Ukrainian, and\n"
            "  the other Whisper language codes."
        ),
        "type": "enum",
        "default": "auto",
        "choices": ["auto", *_transcribe.LANGUAGES],
    },
    # ── Extraction ────────────────────────────────────────────────────────────
    "table_structure": {
        "short": "Run table detection model on PDFs (default: true)",
        "help": (
            "When enabled, Docling runs a dedicated ML model to detect and reconstruct tables.\n"
            "  Disable to speed up pre-processing of text-only documents (court decisions, contracts).\n"
            "  Does not affect text extraction — only the table structure model.\n"
            "  Default: true."
        ),
        "type": "bool",
        "default": True,
    },
    "section_token_threshold": {
        "short": "Estimated tokens above which a document is split for sectioned extraction ('auto' or a fixed number)",
        "help": (
            "Documents whose estimated token count is at or under this value are extracted\n"
            "  whole; larger ones are split into overlapping sections and extracted sequentially\n"
            "  with a carried-forward entity list. Token count is estimated as chars/4.\n"
            "  'auto' (default): derived from the extraction model's context window (about 60% of\n"
            "  it), adjusted for how that model's tokenizer counts — so a 1M-window model like\n"
            "  DeepSeek V4 reads far more of a document in one call than a 200K Claude window\n"
            "  (which resolves to 129032). Set a fixed number to pin it as an advanced override —\n"
            "  e.g. lower it if extraction quality drops on long documents, or to hold a call under\n"
            "  a provider's long-context pricing tier.\n"
            "  Note: a fixed number does NOT rescale when you change extractor_model — pin it back to\n"
            "  'auto' (or re-check the value) if you switch to a model with a different context window."
        ),
        "type": "int_or_auto",
        "default": "auto",
        "min": 1,
    },
    "section_token_budget": {
        "short": "Target estimated tokens per section when sectioning a large document ('auto' or a fixed number)",
        "help": (
            "When a document is sectioned (see section_token_threshold), pages are grouped into\n"
            "  sections targeting roughly this many estimated tokens each.\n"
            "  'auto' (default): half the threshold (model-aware), so a document just over the\n"
            "  threshold splits into two sections. Set a fixed number to pin it as an advanced\n"
            "  override.\n"
            "  Note: a fixed number does NOT rescale when you change extractor_model — pin it back to\n"
            "  'auto' (or re-check the value) if you switch to a model with a different context window."
        ),
        "type": "int_or_auto",
        "default": "auto",
        "min": 1,
    },
    "section_overlap_tokens": {
        "short": "Estimated-token overlap between consecutive sections (default: 4000)",
        "help": (
            "Consecutive sections of a large document share this many estimated tokens of overlap,\n"
            "  so entities and events spanning a section boundary aren't lost.\n"
            "  Default: 4000."
        ),
        "type": "int",
        "default": 4_000,
        "min": 0,
    },
    "empty_extraction_min_words": {
        "short": "Source-text word count above which a zero-fact extraction fails post-flight (default: 500)",
        "help": (
            "A document with at least this many words of pre-processed source text but zero extracted\n"
            "  key_facts is treated as a failed extraction, not a genuinely fact-free document —\n"
            "  it gets one automatic repair retry, then fails loudly instead of shipping a silent,\n"
            "  empty result. Gated on actual source-text volume, not page count, so a short but\n"
            "  dense document is still caught and a long but mostly-blank one (a scanned exhibit\n"
            "  set) isn't wrongly flagged.\n"
            "  Lower it if your documents are usually short but substantive (e.g. terse memos);\n"
            "  raise it if you routinely add long documents with legitimately sparse content.\n"
            "  Default: 500."
        ),
        "type": "int",
        "default": 500,
        "min": 0,
    },
    "verify_extraction": {
        "short": "Re-read each document to catch facts the extraction missed (default: false)",
        "help": (
            "When enabled, every document gets a second, cheap read straight after it is\n"
            "  extracted. That second call sees the same document text and the facts just pulled\n"
            "  from it, and answers one question: what material fact is on the page and missing\n"
            "  from the list? Anything it finds is checked against the existing facts in code and\n"
            "  added if it is genuinely new.\n"
            "  It exists because the misses that matter are rarely things the model couldn't see —\n"
            "  they are things it read and judged unimportant, most often an obligation buried in\n"
            "  standard-form wording, a one-line disclosure under a table, or something in a\n"
            "  schedule at the back.\n"
            "  Cost: roughly 15% more per run on the Claude API path, where the re-read reuses\n"
            "  the first call's cached prompt at a fraction of the price, so most of that 15% is\n"
            "  the second call's own reasoning — see verifier_effort. On an OpenAI-compatible\n"
            "  model the re-read doesn't get that discount, so expect a larger increase.\n"
            "  Trade-off: it raises how much gets captured, and it can also add restatements and\n"
            "  true-but-minor detail to your fact lists. Off by default until that noise has been\n"
            "  measured. Not available with a batch extractor model (claude-batch,\n"
            "  openai-batch) — those return their results hours later, in a separate run.\n"
            "  Default: false."
        ),
        "type": "bool",
        "default": False,
    },
    "verifier_effort": {
        "short": "Reasoning effort for the verification pass (default: low)",
        "help": (
            "How hard the model thinks during the second-read pass enabled by verify_extraction.\n"
            "  Low is the default and is where this pass is meant to live: comparing a fact list\n"
            "  against a document it has in front of it is a lookup, not a judgment call, and the\n"
            "  pass's cost is almost entirely its own thinking tokens. Raise it only if the pass\n"
            "  is demonstrably missing things on your corpus, and expect the extra spend to show\n"
            "  up immediately.\n"
            "  The pass always runs on extractor_model — it re-uses the extraction call's prompt\n"
            "  to get the cached-input discount, and a cache belongs to one model.\n"
            "  Valid values: low, medium, high, xhigh, max. Default: low."
        ),
        "type": "enum",
        "default": "low",
        "choices": ["low", "medium", "high", "xhigh", "max"],
    },
    # ── Models ───────────────────────────────────────────────────────────────
    "classifier_model": {
        "short": "Model that picks each document's record skill (default: haiku)",
        "help": (
            "Model used for the cheap classification step that reads a document's first pages\n"
            "  and picks the matching record skill. Haiku is plenty for this; raise it only if\n"
            "  classification is going wrong on ambiguous documents.\n"
            "  Value: a Claude tier (haiku or sonnet), or a backend:model form to route to\n"
            "  another provider (openai:gpt-6-luna, deepseek:deepseek-flash, gemini:gemini-3.5-flash-lite).\n"
            "  Default: haiku. openai:gpt-6-luna is a cheaper alternative (with classifier_effort\n"
            "  low) but needs its own OpenAI key even on a plain Claude subscription."
        ),
        "type": "string",
        "default": defaults.CLASSIFIER_MODEL,
    },
    "extractor_model": {
        "short": "Model for document extraction (default: haiku)",
        "help": (
            "Model used to extract each document during processing.\n"
            "  Haiku (the default, at high effort) found the most in the latest benchmark run\n"
            "  and costs the least of the Claude tiers; Sonnet is faster per document.\n"
            "  Value: a Claude tier (haiku or sonnet), or a backend:model form to route to\n"
            "  another provider (openai:gpt-6-luna, deepseek:deepseek-flash, gemini:gemini-3.5-flash-lite).\n"
            "  Default: haiku. A cheaper alternative is openai:gpt-6-luna (with extractor_effort\n"
            "  medium), which finds less; it needs its own OpenAI key even on a plain Claude\n"
            "  subscription. See docs/models-and-cost.md.\n"
            "  DeepSeek thinking mode is off by default; append -thinking (deepseek:deepseek-flash-thinking)\n"
            "  to enable it."
        ),
        "type": "string",
        "default": defaults.EXTRACTOR_MODEL,
    },
    "finalizer_model": {
        "short": "Model for the finishing step: its writing and its housekeeping (default: haiku)",
        "help": (
            "Model used for every call of the finishing step, unless finalizer_writing_model or\n"
            "  finalizer_housekeeping_model routes one of its two kinds of work elsewhere: writing\n"
            "  (entity summaries, the briefing) and housekeeping (merging duplicate entities,\n"
            "  contradictions, the timeline, dates and the other matching and tidying calls).\n"
            "  This step works from compact digests rather than reading raw documents, so the\n"
            "  cheaper Haiku tier is the default; raise it if synthesized prose feels thin, if\n"
            "  duplicate entities slip through, or if contradictions are being missed.\n"
            "  Value: a Claude tier (haiku or sonnet), or a backend:model form to route to\n"
            "  another provider (openai:gpt-6-luna, deepseek:deepseek-flash, gemini:gemini-3.5-flash-lite).\n"
            "  Default: haiku. This stage has no dedicated benchmark of its own yet, so any\n"
            "  other model is an extrapolation, not a measured recommendation."
        ),
        "type": "string",
        "default": defaults.FINALIZER_MODEL,
    },
    "finalizer_writing_model": {
        "short": "Model override for the finishing step's writing: entity summaries and the briefing (default: unset — uses finalizer_model)",
        "help": (
            "Overrides finalizer_model for the calls that write prose a reporter reads: the summary\n"
            "  of each entity named in more than one document, and the briefing. A stronger model\n"
            "  shows most here. Leave unset to use finalizer_model.\n"
            "  Value: a Claude tier (haiku or sonnet), or a backend:model form to route to\n"
            "  another provider (openai:gpt-6-luna, deepseek:deepseek-flash, gemini:gemini-3.5-flash-lite).\n"
            "  Default: unset."
        ),
        "type": "string",
        "default": None,
    },
    "finalizer_housekeeping_model": {
        "short": "Model override for the finishing step's housekeeping: matching, contradictions, timeline, dates (default: unset — uses finalizer_model)",
        "help": (
            "Overrides finalizer_model for every finishing call that matches, checks or tidies\n"
            "  rather than writes: merging duplicate entities and flagging contradictions, matching\n"
            "  fact tags and relationship targets, grouping relationship labels and request sources,\n"
            "  folding timeline collisions, settling dates code could not read, and the\n"
            "  contradiction re-check. Short judgement calls, where a cheaper model is often enough.\n"
            "  Leave unset to use finalizer_model.\n"
            "  Value: a Claude tier (haiku or sonnet), or a backend:model form to route to\n"
            "  another provider (openai:gpt-6-luna, deepseek:deepseek-flash, gemini:gemini-3.5-flash-lite).\n"
            "  Default: unset."
        ),
        "type": "string",
        "default": None,
    },
    "extractor_effort": {
        "short": "Reasoning effort for document extraction (default: high)",
        "help": (
            "How hard the extractor model thinks. Thinking tokens bill as output, so a lower\n"
            "  effort spends fewer tokens per document — the main cost lever for an extraction run.\n"
            "  'high' is the default: in the latest benchmark run Haiku 5.5, the default model,\n"
            "  found far more at 'high' than at 'medium' (see docs/models-and-cost.md), though it\n"
            "  costs more output and runs slower; lower it if cost matters more than recall.\n"
            "  Left unset, this default is skipped automatically for an extractor that has no\n"
            "  'high' level — but setting it explicitly on a model that doesn't\n"
            "  support that exact level errors rather than silently doing something else.\n"
            "  'xhigh'/'max' need a model that supports them: every current Claude and OpenAI\n"
            "  model does; Gemini stops at 'high'; DeepSeek takes 'max' but not 'xhigh'.\n"
            "  Valid values: low, medium, high, xhigh, max. Default: high."
        ),
        "type": "enum",
        "default": defaults.EXTRACTOR_EFFORT,
        "choices": ["low", "medium", "high", "xhigh", "max"],
    },
    "finalizer_effort": {
        "short": "Reasoning effort for the post-processing step (default: not set)",
        "help": (
            "How hard the finalizer model thinks during post-processing. Reasoning helps the prose\n"
            "  steps, so keep this higher than the extractor unless cost-trimming; lower it to spend\n"
            "  fewer tokens. Left unset, nothing is sent regardless of model — safe on Haiku, the\n"
            "  finalizer default. Setting it explicitly on a model that doesn't support that exact\n"
            "  level errors rather than silently doing something else. 'xhigh'/'max' need a model\n"
            "  that supports them specifically — OpenAI reasoning models (e.g. GPT-6) support\n"
            "  both, as does every current Claude model; Gemini takes neither.\n"
            "  Valid values: low, medium, high, xhigh, max. Default: not set."
        ),
        "type": "enum",
        "default": None,
        "choices": ["low", "medium", "high", "xhigh", "max"],
    },
    "classifier_effort": {
        "short": "Reasoning effort for document classification (default: low)",
        "help": (
            "How hard the classifier model thinks when picking a document's record skill.\n"
            "  'low' is the default: classification is a quick lookup against a short excerpt,\n"
            "  not a task reasoning helps with. Applied only when the resolved model actually\n"
            "  supports it, same as extractor_effort/finalizer_effort, so an unconfigured\n"
            "  pipeline never fails on a model with no effort control at all.\n"
            "  Setting it explicitly on a model that doesn't support that exact level errors\n"
            "  rather than silently doing something else.\n"
            "  Valid values: low, medium, high, xhigh, max. Default: low."
        ),
        "type": "enum",
        "default": defaults.CLASSIFIER_EFFORT,
        "choices": ["low", "medium", "high", "xhigh", "max"],
    },
    # ── Local / self-hosted models (#380) ───────────────────────────────────────
    "local_base_url": {
        "short": "Base URL of a local/self-hosted OpenAI-compatible endpoint (default: unset)",
        "help": (
            "The base URL of a self-hosted model server — Ollama, LM Studio, llama.cpp's server,\n"
            "  vLLM, or anything else that speaks the OpenAI-compatible Chat Completions wire\n"
            "  format. Required before a stage can be routed to the `local` backend\n"
            "  (`local:llama-3.3-70b`) — there is no default, since it depends entirely on where\n"
            "  you're running the server.\n"
            "  Examples: http://localhost:11434/v1 (Ollama), http://localhost:1234/v1 (LM Studio).\n"
            "  Default: unset. Can also be set via the LOCAL_BASE_URL environment variable, which\n"
            "  takes precedence over this value."
        ),
        "type": "string",
        "default": None,
    },
    "local_context_window": {
        "short": "Context window (tokens) of the local model, for sectioning (default: 8000)",
        "help": (
            "A self-hosted model's id carries no vendor namespace Watchdog can infer a context\n"
            "  window from the way it does for hosted models (Claude, DeepSeek, Gemini, ...), so\n"
            "  document sectioning needs to be told the real number to size sections correctly.\n"
            "  Set it to your model's actual context window (check its model card or runner docs).\n"
            "  Left unset, Watchdog assumes a conservative 8000 tokens — safe for most small/\n"
            "  quantized local models, but likely to over-section a model with a genuinely larger\n"
            "  window, costing more calls than necessary.\n"
            "  Only affects stages routed to the `local` backend."
        ),
        "type": "int",
        "default": 8_000,
        "min": 1,
    },
    "openrouter_base_url": {
        "short": "Base URL for OpenRouter (default: https://openrouter.ai/api/v1)",
        "help": (
            "The base URL for the `openrouter` backend (`openrouter:anthropic/claude-3.5-sonnet`,\n"
            "  or any other model id OpenRouter routes to). The default is OpenRouter's own\n"
            "  endpoint; change this only if you're pointing at an OpenRouter-compatible proxy.\n"
            "  Default: https://openrouter.ai/api/v1. Can also be set via the OPENROUTER_BASE_URL\n"
            "  environment variable, which takes precedence over this value."
        ),
        "type": "string",
        "default": "https://openrouter.ai/api/v1",
    },
    # ── Deduplication ─────────────────────────────────────────────────────────
    "dup_threshold": {
        "short": "Near-duplicate Jaccard similarity threshold — score at which documents are flagged (default: 0.85)",
        "help": (
            "Watchdog fingerprints each document and compares it to all previously added documents\n"
            "  using Jaccard similarity on word n-grams. If the score meets or exceeds this threshold,\n"
            "  the document is flagged as a near-duplicate.\n"
            "  Higher = stricter matching (fewer false positives, may miss near-duplicates).\n"
            "  Lower = looser matching (more matches, more false positives).\n"
            "  Valid range: 0.0–1.0. Default: 0.85."
        ),
        "type": "float",
        "default": 0.85,
        "min": 0.0,
        "max": 1.0,
    },
    "shingle_size": {
        "short": "Word n-gram size for near-duplicate fingerprinting (default: 3)",
        "help": (
            "Documents are fingerprinted using overlapping sequences of n consecutive words.\n"
            "  Larger n is more precise but slower and uses more registry storage per document.\n"
            "  Smaller n is faster but produces more false positives.\n"
            "  Changing this invalidates existing shingle data — re-add documents to rebuild fingerprints.\n"
            "  Default: 3 (word trigrams)."
        ),
        "type": "int",
        "default": 3,
        "min": 1,
    },
    # ── Search ────────────────────────────────────────────────────────────────
    "embed_model": {
        "short": "Local embedding model for semantic search (default: BAAI/bge-small-en-v1.5)",
        "help": (
            "The fastembed model used to index source passages and notes for search.\n"
            "  Runs entirely on your machine — no API, no cost, nothing leaves the computer.\n"
            "  bge-small-en-v1.5 is small (67 MB), fast, and a strong retriever; raise it only if\n"
            "  you want more recall on a large vault. Stronger fastembed options, biggest gain first:\n"
            "    mxbai-embed-large-v1   (1024-dim, 640 MB — best quality-per-MB)\n"
            "    BAAI/bge-base-en-v1.5  (768-dim, 210 MB — same family, easy step up)\n"
            "    snowflake/snowflake-arctic-embed-s  (384-dim, 130 MB — same size class)\n"
            "  Must be a model fastembed can load (see `TextEmbedding.list_supported_models()`).\n"
            "  After changing this, rebuild the search index under Activity → Maintenance; vectors from two models aren't\n"
            "  comparable, so the index is rebuilt from disk (no need to add documents again).\n"
            "  Default: BAAI/bge-small-en-v1.5."
        ),
        "type": "str",
        "default": "BAAI/bge-small-en-v1.5",
    },
    "rerank_model": {
        "short": "Local cross-encoder that reranks corpus search results (default: BAAI/bge-reranker-base)",
        "help": (
            "After search fuses the dense (embedding) and sparse (BM25) candidate\n"
            "  lists, a cross-encoder reranks the top of that pool for precision — the biggest\n"
            "  single retrieval-quality lever (Anthropic contextual-retrieval). Runs entirely on\n"
            "  your machine via fastembed — no API, no cost. Downloaded during setup,\n"
            "  otherwise on first search (~300 MB).\n"
            "  Set to `none` (or empty) to turn reranking off and rank by fusion alone.\n"
            "  Other fastembed cross-encoders: Xenova/ms-marco-MiniLM-L-6-v2 (English, ~90 MB),\n"
            "    jinaai/jina-reranker-v2-base-multilingual.\n"
            "  Default: BAAI/bge-reranker-base."
        ),
        "type": "str",
        "default": "BAAI/bge-reranker-base",
    },
    # ── Research ──────────────────────────────────────────────────────────────
    "research_max_rounds": {
        "short": "Default search rounds for a standard web research session (default: 3)",
        "help": (
            "How many search rounds the /watchdog-research skill runs by default before it must\n"
            "  check in and stop. An advisory budget the interactive skill self-limits to — a wider\n"
            "  net (the 'deep' effort tier) overrides it per run. Higher = more thorough, more cost.\n"
            "  Default: 3. Minimum: 1."
        ),
        "type": "int",
        "default": 3,
        "min": 1,
    },
    "research_max_fetches": {
        "short": "Default sources captured per standard web research session (default: 25)",
        "help": (
            "About how many web sources the /watchdog-research skill captures into incoming/ in a\n"
            "  default (standard-effort) run. An advisory budget the interactive skill self-limits to;\n"
            "  the 'quick' and 'deep' effort tiers scale it down or up per run. Each captured source\n"
            "  is later read by the local pipeline, not in the research session, so this bounds scope\n"
            "  and processing cost rather than session tokens. Default: 25. Minimum: 1."
        ),
        "type": "int",
        "default": 25,
        "min": 1,
    },
    # ── Web archiving ─────────────────────────────────────────────────────────
    "wayback_save": {
        "short": "Also save each research source to the Wayback Machine (default: false)",
        "help": (
            "When enabled, web research submits every source it downloads to the Internet\n"
            "  Archive's Wayback Machine (Save Page Now), and records the resulting snapshot URL in\n"
            "  each source's provenance sidecar — a permanent, citable copy that survives if the\n"
            "  original page later changes or is taken down. Off by default, and a no-op until both\n"
            "  wayback_access_key and wayback_secret_key are set. The local download is unaffected —\n"
            "  archiving is a best-effort bonus that never blocks or fails a download.\n"
            "  Default: false."
        ),
        "type": "bool",
        "default": False,
    },
    "wayback_access_key": {
        "short": "archive.org S3 access key (for wayback_save)",
        "help": (
            "The 'S3-like' access key for your archive.org account, used to authenticate Save Page\n"
            "  Now requests when wayback_save is on. Create a free account and generate keys at\n"
            "  https://archive.org/account/s3.php. Stored in this config file; leave unset to disable.\n"
            "  Paired with wayback_secret_key."
        ),
        "type": "string",
        "secret": True,
    },
    "wayback_secret_key": {
        "short": "archive.org S3 secret key (for wayback_save)",
        "help": (
            "The 'S3-like' secret key that pairs with wayback_access_key. Generate both together at\n"
            "  https://archive.org/account/s3.php. Stored in this config file; leave unset to disable."
        ),
        "type": "string",
        "secret": True,
    },
    # ── Verification ──────────────────────────────────────────────────────────
    "reporter_name": {
        "short": "Your name, recorded with each fact you mark verified, disputed or can't verify",
        "help": (
            "When you mark a fact Verified, Disputed or Can't verify, Watchdog records who\n"
            "  marked it and when, in the investigation's verification ledger and its\n"
            "  verification.md. This is the name it records.\n"
            "  Until you set it, Watchdog records \"Journalist\"."
        ),
        "type": "string",
        "default": None,
        "default_fn": "watchdog.pipeline.verification:default_reporter_name",
    },
    # ── Privacy ───────────────────────────────────────────────────────────────
    "telemetry": {
        "short": "Record every model call in ~/.watchdog/telemetry.db (default: true)",
        "help": (
            "Watchdog keeps a local database of every model call it makes, across all your\n"
            "  investigations: the model, tokens, cost and timing, plus the vault's path and name\n"
            "  and the document's filename. It never leaves your computer and holds no document\n"
            "  text; it exists so cost and speed can be compared across runs and models.\n"
            "  Because it lists the documents in every investigation, treat the file as sensitive.\n"
            "  Set to false to stop recording; existing rows stay until you delete the file.\n"
            "  Deleting an investigation's files also removes its rows.\n"
            "  Each investigation's own usage files, which Activity → Usage reads, are unaffected.\n"
            "  Default: true."
        ),
        "type": "bool",
        "default": True,
    },
}



# The settings that choose each step's model. Settings → Models & keys sets them, step by step,
# beside the key that pays (D347), so the Settings listing doesn't show them a second time.
MODEL_STEP_KEYS = ("classifier_model", "extractor_model", "finalizer_model",
                   "finalizer_writing_model", "finalizer_housekeeping_model")

# Display grouping for the Settings listing (presentation only — set/get is
# unaffected). Each section owns an ordered list of keys; any key missing from every
# section is still shown, under "Other", so a newly added key never silently disappears.
SECTIONS = [
    ("Vaults", "Where investigations are created.",
     ["projects_dir"]),
    ("OCR", "Text recognition for scanned documents.",
     ["ocr_engine", "ocr_languages", "garbled_threshold"]),
    ("Pre-processing", "Local conversion — parallelism and large-PDF handling.",
     ["chew_workers", "chunk_size", "chunk_workers", "chunk_timeout", "table_structure"]),
    ("Transcription", "Speech-to-text for audio and video, on this computer.",
     ["transcription_model", "transcription_language"]),
    ("Processing", "Extraction run — parallelism, classification, skill pinning, sectioning.",
     ["auto_approve", "extract_concurrency", "extract_token_budget", "classify_pages", "default_skill",
      "section_token_threshold", "section_token_budget", "section_overlap_tokens",
      "empty_extraction_min_words", "verify_extraction"]),
    ("Model options", "How hard each step's model thinks, and where self-hosted models are. The models themselves are chosen under Models & keys.",
     ["classifier_effort", "extractor_effort", "finalizer_effort", "verifier_effort",
      "local_base_url", "local_context_window", "openrouter_base_url"]),
    ("Deduplication", "Near-duplicate detection.",
     ["dup_threshold", "shingle_size"]),
    ("Search", "Local semantic search over the source corpus.",
     ["embed_model", "rerank_model"]),
    ("Research", "Web research mode — default effort budget for Web research.",
     ["research_max_rounds", "research_max_fetches"]),
    ("Web archiving", "Optionally save research sources to the Wayback Machine.",
     ["wayback_save", "wayback_access_key", "wayback_secret_key"]),
    ("Verification", "How your checks of individual facts are recorded.",
     ["reporter_name"]),
    ("Privacy", "The local record of model calls across your investigations.",
     ["telemetry"]),
]


def default_value(key: str):
    """A setting's default, computing a `default_fn` (`"module:function"`, such as the OS account's
    name for `reporter_name`) when it has one. None if there is none or it can't be computed."""
    meta = SETTINGS.get(key, {})
    if meta.get("default") is not None or not meta.get("default_fn"):
        return meta.get("default")
    import importlib
    module, _, name = meta["default_fn"].partition(":")
    try:
        return getattr(importlib.import_module(module), name)()
    except Exception:  # noqa: BLE001 — a display default must never break settings
        return None


def _auto_resolved(key: str, cfg: dict) -> str:
    """An 'auto' section setting with the value it currently resolves to, e.g. 'auto (129032 —
    sonnet)'; the number depends on the backend as well as the model (D190)."""
    from watchdog import model_client
    from watchdog.pipeline import section
    raw = cfg.get("extractor_model") or SETTINGS["extractor_model"]["default"]
    backend, model = defaults.split_backend_model(raw)
    model = model or None
    threshold, budget = section.model_defaults(model, backend=backend)
    resolved = threshold if key == "section_token_threshold" else budget
    suffix = f"{backend}:{model}" if backend and backend not in model_client.CLAUDE_BACKENDS else (model or "sonnet")
    return f"auto ({resolved} — {suffix})"


def display_value(key: str, value, cfg: dict | None = None) -> str:
    """How Settings shows a setting's value (never a stored secret)."""
    meta = SETTINGS.get(key, {})
    if key in ("section_token_threshold", "section_token_budget") and (value is None or value == "auto"):
        return _auto_resolved(key, cfg) if cfg is not None else "auto"
    if value is None:
        if key == "ocr_languages":
            return "auto-detect (default)"
        d = default_value(key)
        if d is None:
            return "(not set)"
        if meta.get("default") is None:
            return f"{d} (default)"
        value = d
    if key == "ocr_languages":
        return ", ".join(value) if value else "auto-detect (default)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if meta.get("secret") and value:
        return "•••••••• (set)"
    return str(value)


def coerce(cfg: dict, key: str, value: str) -> str:
    """Validate and coerce raw string `value` for `key`, setting it in `cfg` and returning the
    display string. Raises `SettingError` on invalid input. `projects_dir` creates the folder."""
    meta = SETTINGS[key]
    if key == "ocr_languages":
        langs = [lang.strip() for lang in value.split(",") if lang.strip()]
        cfg[key] = langs
        return ", ".join(langs) if langs else "auto-detect"
    if key == "projects_dir":
        path = Path(value).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        cfg[key] = str(path)
        return str(path)
    if meta["type"] == "float":
        try:
            v = float(value)
        except ValueError:
            raise SettingError(f"'{key}' must be a number (e.g. 0.85)")
        if v != v or v in (float("inf"), float("-inf")):      # nan, inf
            raise SettingError(f"'{key}' must be a finite number (e.g. 0.85)")
        lo, hi = meta.get("min"), meta.get("max")
        if lo is not None and v < lo:
            raise SettingError(f"'{key}' must be >= {lo}")
        if hi is not None and v > hi:
            raise SettingError(f"'{key}' must be <= {hi}")
        cfg[key] = v
        return str(v)
    if meta["type"] == "int_or_auto":
        if value.lower() == "auto":
            cfg[key] = "auto"
            return "auto"
        try:
            v = int(value)
        except ValueError:
            raise SettingError(f"'{key}' must be 'auto' or a whole number")
        lo = meta.get("min")
        if lo is not None and v < lo:
            raise SettingError(f"'{key}' must be >= {lo}")
        cfg[key] = v
        return str(v)
    if meta["type"] == "int":
        try:
            v = int(value)
        except ValueError:
            raise SettingError(f"'{key}' must be a whole number")
        lo = meta.get("min")
        if lo is not None and v < lo:
            raise SettingError(f"'{key}' must be >= {lo}")
        cfg[key] = v
        return str(v)
    if meta["type"] == "bool":
        if value.lower() in ("true", "yes", "1", "on"):
            v = True
        elif value.lower() in ("false", "no", "0", "off"):
            v = False
        else:
            raise SettingError(f"'{key}' must be true or false")
        cfg[key] = v
        return "true" if v else "false"
    if meta["type"] == "enum":
        choices = meta.get("choices", [])
        if value not in choices:
            raise SettingError(f"'{key}' must be one of: {', '.join(choices)}")
        cfg[key] = value
        if key == "ocr_engine" and value == "apple_vision" and sys.platform != "darwin":
            raise SettingError("Apple Vision OCR is only available on macOS.")
        return value
    if key == "reporter_name":
        value = " ".join(value.split())[:120]
    cfg[key] = value
    return value

