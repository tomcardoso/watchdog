# Installing Watchdog

Watchdog is a tool for managing large collections of public records. Once installed, you drop documents into a folder and Watchdog extracts the names, addresses, companies, and connections — then lets you ask questions in plain language.

This guide assumes you have never used a terminal before. Read through it once before starting.

---

## What you need

| What | Why | Free? |
|------|-----|-------|
| A computer running macOS, Linux, or Windows | Watchdog runs on your computer, not in the cloud | n/a |
| [Obsidian](https://obsidian.md) | The app where you'll read and explore your documents | Free |
| [Claude Code](https://claude.ai/download) | The AI assistant you ask questions, surface connections, and build your investigation in — **required** | Free to install |
| Claude access | Powers the AI — required for document processing | Pro/Max subscription, or Anthropic API key |

**Obsidian** is a note-taking app that Watchdog uses to organize and display your research. You don't need to know how to use it before starting — Watchdog sets it up for you.

**Claude Code** is the AI assistant where you do your investigative work — asking questions of your documents, surfacing connections, and building up wiki threads. It's made by Anthropic, the same company that makes Claude, and you install it once on your computer. It is required: the interactive investigation commands run inside it. (If you want to cut cost, the separate document-*ingestion* step can be pointed at other AI providers like OpenAI or DeepSeek — see the README's Model backends section — but Claude Code itself is still needed for everything else.)

**Claude access** is required because processing documents requires AI. A Pro subscription ($20/month) is enough for most journalism work. If you're ingesting hundreds of documents at a time, Max (from $100/month) gives you higher limits. If you have an Anthropic API key, you can use that instead — see Step 2.

---

## Step 1: Install Obsidian

1. Go to [obsidian.md](https://obsidian.md) and click **Download**
2. Open the downloaded file and drag Obsidian to your Applications folder
3. Open Obsidian — it will ask you to create or open a vault. Click **Create new vault** and give it any name for now (you'll create your real investigation vaults later)

---

## Step 2: Install Claude Code

1. Go to [claude.ai/download](https://claude.ai/download) and download the app
2. Open the downloaded file and follow the installation instructions
3. Open Claude Code and sign in with your Claude.ai account

If you don't have a Claude.ai account yet, create one at [claude.ai](https://claude.ai) and subscribe to Pro or Max before continuing.

If you have an Anthropic API key and prefer to use that instead, run `claude login` in your terminal after installation and follow the prompts to authenticate with your API key.

Watchdog is built around Claude and recommends it — Claude Code also powers the interactive investigation tools (`watchdog-context`, `watchdog-query`, and the rest). The document-processing pipeline can additionally route individual stages to other model providers (OpenAI, DeepSeek) once you add a key; see [Model backends](README.md#model-backends). You don't need to set those up now.

---

## Step 3: Open Terminal

Terminal is a built-in app that lets you type commands to your computer. You'll only need it for the next few steps.

**macOS:** Press **Command + Space**, type **Terminal**, press Return.

**Linux:** Press **Ctrl + Alt + T**, or search for Terminal in your application menu.

**Windows:** Press **Windows + R**, type **cmd**, press Return. Or install [Windows Terminal](https://apps.microsoft.com/detail/9n0dx20hk701) for a better experience.

---

## Step 4: Install prerequisites

Watchdog requires two tools for processing PDFs — **qpdf** and **Ghostscript** — and **pipx** to install Python tools.

**macOS:**
```
brew install qpdf ghostscript pipx
pipx ensurepath
```
Then close and reopen Terminal so the new `pipx` path takes effect.

If you don't have Homebrew, install it first: [brew.sh](https://brew.sh)

**Ubuntu / Debian Linux:**
```
sudo apt install qpdf ghostscript pipx tesseract-ocr libtesseract-dev
```

**Fedora / RHEL Linux:**
```
sudo dnf install qpdf ghostscript pipx tesseract tesseract-devel
```

**Windows:**
- qpdf: [github.com/qpdf/qpdf/releases](https://github.com/qpdf/qpdf/releases) — download the installer
- Ghostscript: [ghostscript.com/releases/gsdnld.html](https://ghostscript.com/releases/gsdnld.html) — download the installer
- pipx: open Terminal and run `python -m pip install pipx`, then `pipx ensurepath`

---

## Step 5: Install Watchdog

```
pipx install watchdog-intel
```

Wait for it to finish. You'll see a message saying the installation is complete. This installs Watchdog from [PyPI](https://pypi.org/project/watchdog-intel/) — the standard package repository for Python tools.

---

## Step 6: Run setup

```
watchdog setup
```

This will:
- Verify that qpdf and Ghostscript are installed
- Ask where you want to store your investigation projects
- Enable tab completion in your shell automatically
- Offer to install the optional capture browser for full page snapshots (see [Full page snapshots](#full-page-snapshots-optional) below)
- Download the ML models for document conversion and semantic search (one-time, may take a few minutes on a slow connection)

It will ask two questions: where to store your projects, and whether to install the optional capture browser. Press Return to accept the projects default (`~/Investigations`), or type a different path; the capture browser defaults to no (type `y` to install it, an extra ~150 MB).

When setup finishes, reload your shell so the tab completion takes effect:

**macOS / zsh:** `source ~/.zshrc`
**bash:** `source ~/.bashrc`

After that, pressing Tab after `watchdog ` shows available commands; pressing Tab after `watchdog status ` completes project names.

---

## Creating your first investigation

When you're ready to start a new investigation:

```
watchdog new "My Investigation Name"
```

Use a descriptive name — it will become the name of your Obsidian vault. For example:

```
watchdog new "Shell Company Investigation"
```

Watchdog creates the vault directory, sets up the folder structure, and registers it in Obsidian automatically. You'll see the vault path and next steps printed in your terminal.

To open the vault in Obsidian immediately:

```
watchdog obsidian shell-company-investigation
```

You can also run `watchdog obsidian` with no arguments from inside the vault directory. If Obsidian opens and the vault isn't visible, go to **Open folder as vault** in Obsidian, navigate to the investigation folder, and click Open. Once you've done that once, `watchdog obsidian` will open it directly in future.

**The dashboard.** The vault's `dashboard.base` is a dashboard of live tables (most-mentioned entities, recent documents, possible duplicates, and more); `index.md` links to it. It uses **Obsidian Bases**, a core Obsidian feature (version 1.9 and up), so there is nothing to install — open the dashboard and the tables are already there. Click a column header to sort; click a row to open the note. If a "Possible duplicates" row turns out to be the same entity extracted twice, `watchdog merge-entities <keep-id> <merge-id>` folds them into one, deterministically.

For a complete walkthrough of a first investigation from start to finish, see [GETTING_STARTED.md](GETTING_STARTED.md).

---

## How to ingest documents

Ingestion happens in two steps: chewing in your terminal, then extraction in Claude Code.

**Step 1 — Drop files and chew**

In your file manager, navigate to your investigation folder. You'll see a folder called `_INCOMING`. Copy any documents you want to process into this folder.

For the full list of supported file types, see [Supported file types](README.md#supported-file-types) in the README.

Then open your terminal, navigate to the investigation folder, and run:

```
cd ~/Investigations/shell-company-investigation
watchdog chew
```

Watchdog reads the files in parallel, showing one live status row per file as it's read, OCR'd if needed, and prepared for extraction, with an overall progress row beneath; finished files settle into the log above. Each settled file shows its status: `OK` (queued), `SKP` (no text found — moved to `_INCOMING/_SKIPPED/`), or `ERR` (failed — moved to `_INCOMING/_FAILED/` with an explanation). Files where OCR produced noisy output show a `· garbled OCR` note but are still queued for Claude to interpret.

On macOS, you'll receive a notification when chewing completes — useful if you've switched to another app. When it finishes, Watchdog asks `Ingest now? [Y/n]` — press Enter to extract the queued documents right away (it runs in your terminal, no Claude Code session needed). Decline and it prints the `watchdog ingest` command to run later.

To cancel a chew in progress, press **Ctrl+C** — the lock is cleaned up automatically and unfinished files stay in `_INCOMING/` for the next run.

To control parallelism for a single run:

```
watchdog chew --chew-workers 4    # parallel files
watchdog chew --chunk-workers 2   # parallel chunks per file (affects large PDFs)
```

These override the persistent settings from `watchdog configure` for that run only.

**Step 2 — Set up the extraction session**

From inside the vault directory, run:

```
watchdog ingest
```

Watchdog scans the queue, confirms, and runs the extraction pipeline in your terminal — there's no Claude Code session to open.

By default, Watchdog uses Sonnet for extraction, and Haiku for classification and post-ingest. Configure persistent defaults with `watchdog configure` (e.g. `watchdog configure extractor_model haiku`), or override per run with `--extractor-model`, `--finalizer-model`, and `--concurrency`. If you'd rather not remember key names, run `watchdog configure` with no arguments: it lists every setting and then offers an arrow-key wizard to browse and change them. To trim cost, `extractor_effort` / `finalizer_effort` (`low`/`medium`/`high`, default `high`) tune how many thinking tokens each stage spends — thinking bills as output, so a lower effort is the main per-run cost lever (e.g. `watchdog configure extractor_effort medium` or `watchdog ingest --extractor-effort medium`). See the [Commands](README.md#processing) and [Configuration](README.md#configuration) sections of the README for details.

Watchdog is built around Claude and uses it by default, but any stage can run on another provider. Store a provider key with `watchdog auth set openai` / `watchdog auth set deepseek` (or the `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` environment variables), then point a stage at it by giving its model knob a `backend:model` value — e.g. `watchdog configure extractor_model deepseek:deepseek-chat`, or `watchdog ingest --extractor-model openai:gpt-5-mini`. A plain tier (`sonnet`/`opus`/`haiku`) keeps that stage on Claude.

Watchdog works through the chewed files in parallel, showing one live status row per document as it moves from classifying to extracting to done; finished files settle into the log above. Large documents can take several minutes each, so a long pause on a row is normal, not a stall. It extracts entities, relationships, and key facts and writes everything to your vault. At the end it produces a briefing showing:
- What documents were processed
- What entities (people, companies, addresses) were found
- Connections between entities that were already in your vault
- Anything that looks unusual

If you've added terms to the vault's `watchlist.md` (one per line), Watchdog scans each new document for them and writes any matches — with document, page, and context — to `briefings/alerts-<date>.md`, flagging them in the terminal too. Added a term after documents were already ingested? Run `watchdog watchlist` to sweep the whole vault instead of just new documents.

---

## Asking questions

Once documents are ingested, you can ask questions in plain English:

- `/watchdog-query Who are the directors of Shell Co Ltd?`
- `/watchdog-query What address does John Doe use?`
- `/watchdog-query Which companies share the address 123 Main St?`

Claude will answer using only the documents in your vault and will cite the specific page it draws from. Substantive answers are saved to the `queries/` folder so your work builds up over time instead of disappearing into the chat; the big ones graduate into `wiki/` thread pages.

---

## Finding connections

Type `/watchdog-surface` to run a full connection analysis across your entire vault. Claude will look for:

- Addresses shared by companies that have no other apparent connection
- People appearing in unusual roles
- Entities mentioned in many documents but with no documented relationships

---

## Researching on the web

When your documents raise a question they can't answer, run `watchdog research` (from inside the vault, or `watchdog research <name>`). Seeded by what's already in your vault, Claude conducts bounded web research and **queues the sources it finds**; when you exit the session, watchdog downloads them into `_INCOMING/` (validated, with provenance and a reliability tag) — so the findings flow through the same `chew → ingest` pipeline as documents you obtained yourself. HTML pages are captured as a full rendered snapshot (images, styles, client-rendered content) when the optional capture browser is installed — see [Full page snapshots](#full-page-snapshots-optional) below — falling back to a sanitized plain fetch otherwise. It never writes vault notes directly, and skips sources the vault already has. After the download, run `watchdog chew` then `watchdog ingest` to fold the sources in. If a session is interrupted before the download runs, the queued sources are held safely and `watchdog`, `watchdog chew`, and `watchdog status` warn you they're still pending. Optionally, set `wayback_save` (with archive.org S3 keys via `watchdog configure`) to also archive each source to the Wayback Machine for a citable permanent copy. See [GETTING_STARTED.md](GETTING_STARTED.md#researching-open-questions-on-the-web) for the full walkthrough.

---

## Tips

**Ingesting web pages directly from your browser:**
Install the [Obsidian Web Clipper](https://obsidian.md/clipper) browser extension. Point it at your investigation vault and set the destination folder to `_INCOMING`. You can then clip any web page — news articles, company profiles, government announcements — directly into the ingest pipeline with one click, without downloading anything manually.

**Pulling in a list of links:**
If you already have a batch of URLs, run `watchdog fetch <url…>` or `watchdog fetch links.txt` (one URL per line) to download them into `_INCOMING/` — validated and stamped with provenance — then `watchdog chew` and `watchdog ingest` as usual. See [Full page snapshots](#full-page-snapshots-optional) below to capture HTML pages faithfully (images, styles, client-rendered content).

**Rename files before dropping them in:**
Watchdog uses the filename to help organize and label documents. A filename like `shell-co-annual-report-2023.pdf` is much more useful than `scan0042.pdf`. Rename files before dropping them into `_INCOMING` when possible.

**Adding context with sidecar files:**
If you want to record where a document came from before Claude processes it, create a text file with the same name but `.yml` extension. For example, alongside `shell-co-annual-report-2023.pdf`, create `shell-co-annual-report-2023.yml` containing:

```
source: https://www.sedar.com/filing/xyz
obtained: 2026-06-05
notes: Check the director change on page 12.
```

This context is merged into the document record and preserved even if you re-ingest the document later.

**Watching for new files automatically:**
If you're dropping files into a vault over a period of time and want them chewed as they arrive:

```
watchdog watch shell-company-investigation
```

This monitors `_INCOMING/` and chews any new files automatically. Press Ctrl+C to stop.

**Multiple investigations:**
Each investigation is a separate vault. Create as many as you need:

```
watchdog new "City Hall Investigation"
watchdog new "Contractor Investigation"
```

To see all your investigations:
```
watchdog list
```

When an investigation concludes, archive it to keep your list tidy:
```
watchdog archive shell-company-investigation
watchdog list --all   # shows archived investigations when you need them
```

---

## Troubleshooting

**`watchdog: command not found`**
The install didn't add `watchdog` to your path. Try:
```
pipx ensurepath
```
Then close and reopen your terminal.

**`Watchdog isn't set up yet`**
Run:
```
watchdog setup
```

**`qpdf not found` or `ghostscript not found` during setup**
Install the missing tool for your platform (see Step 4 above), then run `watchdog setup` again.

**A document lands in `_FAILED/`**
The document couldn't be processed. Common reasons:
- Password-protected PDF — remove the password and try again
- Corrupted file — try re-downloading
- Unsupported format — check the supported file types list above

To retry: move the file from `_INCOMING/_FAILED/` back to `_INCOMING/`, then run `watchdog chew` again.

**Ingesting a large batch (hundreds of documents)**
`watchdog ingest` extracts the whole queue, processing `extract_concurrency` documents in parallel (default 5). If you hit model rate limits, lower it (`watchdog configure extract_concurrency 2` or `watchdog ingest --concurrency 2`) or chew and ingest in groups. Each document is moved to `morgue/` as soon as it's processed, so re-running `watchdog ingest` only picks up what's still queued.

**A document failed during ingest**
A document whose extraction fails is logged to `.watchdog/Registry/ingest.log` and moved to `.watchdog/queue/_failed/`; the rest of the batch still completes. To retry it, move its queue file back: `mv .watchdog/queue/_failed/<sha>.json .watchdog/queue/` and run `watchdog ingest` again.

**Skills look outdated after a Watchdog upgrade**
When you upgrade Watchdog (`pipx upgrade watchdog-intel`), existing vaults keep their old skill files. Refresh them from inside the vault:
```
cd ~/Investigations/your-investigation
watchdog refresh-skills
```

**Lock stuck**
If a chew or ingest was interrupted, a lock file may be left behind. Remove it with:
```
watchdog unlock <name>
```
If the lock is recent (under 30 minutes old), Watchdog will warn you — use `--force` to remove it anyway:
```
watchdog unlock <name> --force
```

---

## Audio and video transcription (optional)

Watchdog can transcribe audio and video files if you install support for it. This requires **ffmpeg** and adds roughly 2 GB of dependencies.

**macOS:** `brew install ffmpeg`
**Ubuntu/Debian:** `sudo apt install ffmpeg`
**Windows:** [ffmpeg.org/download.html](https://ffmpeg.org/download.html)

Then reinstall Watchdog with transcription support:
```
pipx install "watchdog-intel[asr]" --force
```

---

## Full page snapshots (optional)

By default, `watchdog research` and `watchdog fetch` save HTML pages with a plain, sanitized fetch — no JavaScript runs, so client-rendered pages (single-page apps) can deposit as an empty shell, and images/styling aren't captured. Installing the optional capture browser renders every HTML page in headless Chromium instead and saves a faithful, self-contained snapshot (images, fonts, and stylesheets inlined; all scripts stripped) — worth it if the sources you're pulling in are often JavaScript-heavy or you want the visual layout preserved.

`watchdog setup` asks whether to install it (see [Step 6](#step-6-run-setup) above). To install it later, or if you said no the first time:

```
pipx inject watchdog-intel playwright
~/.local/pipx/venvs/watchdog-intel/bin/playwright install chromium
```

This adds about 150 MB (the Chromium binary). If it isn't installed, `watchdog research` and `watchdog fetch` fall back to the plain sanitized fetch automatically — nothing breaks either way.

---

## Getting help

If something isn't working, open an issue at:

```
https://github.com/tomcardoso/watchdog/issues
```

When reporting a problem, include:
- What you typed or did
- What you expected to happen
- What actually happened (copy and paste any error messages)
- Your operating system and version
