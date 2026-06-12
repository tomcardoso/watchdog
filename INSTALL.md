# Installing Watchdog

Watchdog is a tool for managing large collections of public records. Once installed, you drop documents into a folder and Watchdog extracts the names, addresses, companies, and connections — then lets you ask questions in plain language.

This guide assumes you have never used a terminal before. Read through it once before starting.

---

## What you need

| What | Why | Free? |
|------|-----|-------|
| A computer running macOS, Linux, or Windows | Watchdog runs on your computer, not in the cloud | n/a |
| [Obsidian](https://obsidian.md) | The app where you'll read and explore your documents | Free |
| [Claude Code](https://claude.ai/download) | The AI assistant that reads and connects your documents | Free to install |
| A Claude.ai Pro or Max subscription | Powers the AI — required for document processing | Pro ~$20/month; Max from $100/month |

**Obsidian** is a note-taking app that Watchdog uses to organize and display your research. You don't need to know how to use it before starting — Watchdog sets it up for you.

**Claude Code** is the AI assistant that does the document processing. It's made by Anthropic, the same company that makes Claude. You install it once on your computer.

**A subscription** is required because processing documents requires AI. A Pro subscription ($20/month) is enough for most journalism work. If you're ingesting hundreds of documents at a time, Max (from $100/month) gives you higher limits.

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

If you prefer to use an Anthropic API key instead of a Claude.ai subscription, run `claude login` in your terminal after installation and follow the prompts to authenticate with your API key.

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
- Download the ML models for document conversion and semantic search (~600 MB, one-time)

The model download step may take a few minutes on a slow connection. It only happens once.

It will ask one question: where to store your projects. Press Return to accept the default (`~/Investigations`), or type a different path.

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

For a complete walkthrough of a first investigation from start to finish, see [GETTING_STARTED.md](GETTING_STARTED.md).

---

## How to ingest documents

Ingestion happens in two steps: chewing in your terminal, then extraction in Claude Code.

**Step 1 — Drop files and chew**

In your file manager, navigate to your investigation folder. You'll see a folder called `_INCOMING`. Copy any documents you want to process into this folder.

Supported file types: PDF, Word documents, Excel spreadsheets, images (JPG, PNG, TIFF), web pages (HTML), and plain text files.

Then open your terminal, navigate to the investigation folder, and run:

```
cd ~/Investigations/shell-company-investigation
watchdog chew
```

You'll see a progress bar as Watchdog reads each file, runs OCR if needed, and prepares it for extraction. Each file shows its status: `OK` (queued), `SKP` (no text found — moved to `_INCOMING/_SKIPPED/`), or `ERR` (failed — moved to `_INCOMING/_FAILED/` with an explanation). Files where OCR produced noisy output show a `· garbled OCR` note but are still queued for Claude to interpret.

On macOS, you'll receive a notification when chewing completes — useful if you've switched to another app. When it finishes, run `watchdog ingest` from the same directory to set up the extraction session and open Claude Code.

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

Watchdog scans the queue, then prompts you to open Claude Code. Once Claude Code is open, run the extraction skill:

```
/watchdog-ingest
```

Claude will work through each chewed file, extract entities, relationships, and key facts, and write everything to your vault. At the end it produces a briefing showing:
- What documents were processed
- What entities (people, companies, addresses) were found
- Connections between entities that were already in your vault
- Anything that looks unusual

---

## Asking questions

Once documents are ingested, you can ask questions in plain English:

- `/watchdog-query Who are the directors of Shell Co Ltd?`
- `/watchdog-query What address does John Doe use?`
- `/watchdog-query Which companies share the address 123 Main St?`

Claude will answer using only the documents in your vault and will cite the specific page it draws from.

---

## Finding connections

Type `/watchdog-surface` to run a full connection analysis across your entire vault. Claude will look for:

- Addresses shared by companies that have no other apparent connection
- People appearing in unusual roles
- Entities mentioned in many documents but with no documented relationships

---

## Tips

**Ingesting web pages directly from your browser:**
Install the [Obsidian Web Clipper](https://obsidian.md/clipper) browser extension. Point it at your investigation vault and set the destination folder to `_INCOMING`. You can then clip any web page — news articles, company profiles, government announcements — directly into the ingest pipeline with one click, without downloading anything manually.

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
For large batches, use `--limit` to control how many documents are processed per session:
```
/watchdog-ingest --limit 50
```
This extracts 50 documents and then stops cleanly. Start a new session and run the same command to continue. Watchdog moves each file to `morgue/` as soon as it's processed, so re-running always picks up where the previous session stopped.

**Session ended mid-ingest**
If a Claude Code session ends unexpectedly — rate limit hit, window closed, session timed out — start a new session and run `/watchdog-ingest` again. Processed files are already in `morgue/`; only unfinished files remain. The new run picks up from where the previous one stopped.

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
