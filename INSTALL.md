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
| A Claude.ai Pro or Max subscription | Powers the AI — required for document processing | ~$20–40/month |

**Obsidian** is a note-taking app that Watchdog uses to organize and display your research. You don't need to know how to use it before starting — Watchdog sets it up for you.

**Claude Code** is the AI assistant that does the document processing. It's made by Anthropic, the same company that makes Claude. You install it once on your computer.

**A subscription** is required because processing documents requires AI. A Pro subscription ($20/month) is enough for most journalism work. If you're ingesting hundreds of documents at a time, Max ($40/month) gives you higher limits.

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

---

## Step 3: Open Terminal

Terminal is a built-in app that lets you type commands to your computer. You'll only need it for the next few steps.

**macOS:** Press **Command + Space**, type **Terminal**, press Return.

**Linux:** Press **Ctrl + Alt + T**, or search for Terminal in your application menu.

**Windows:** Press **Windows + R**, type **cmd**, press Return. Or install [Windows Terminal](https://apps.microsoft.com/detail/9n0dx20hk701) for a better experience.

---

## Step 4: Install prerequisites

Watchdog requires two tools for processing PDFs: **qpdf** and **ghostscript**. It also requires **pipx** to install Python tools.

**macOS:**
```
brew install qpdf ghostscript pipx
```
If you don't have Homebrew, install it first: [brew.sh](https://brew.sh)

**Ubuntu / Debian Linux:**
```
sudo apt install qpdf ghostscript pipx
```

**Fedora / RHEL Linux:**
```
sudo dnf install qpdf ghostscript pipx
```

**Windows:**
- qpdf: [github.com/qpdf/qpdf/releases](https://github.com/qpdf/qpdf/releases) — download the installer
- ghostscript: [ghostscript.com/releases/gsdnld.html](https://ghostscript.com/releases/gsdnld.html) — download the installer
- pipx: open Terminal and run `pip install pipx`, then `pipx ensurepath`

---

## Step 5: Install Watchdog

```
pipx install watchdog-intel
```

Wait for it to finish. You'll see a message saying the installation is complete.

---

## Step 6: Run setup

```
watchdog setup
```

This will:
- Verify that qpdf and ghostscript are installed
- Install the Watchdog skills into Claude Code
- Ask where you want to store your investigation projects
- Set up tab completion in your shell

It will ask one question: where to store your projects. Press Return to accept the default, or type a different path.

When it finishes, reload your shell as instructed (e.g. `source ~/.zshrc`), then:

```
watchdog new "My Investigation"
```

---

## Creating your first investigation

When you're ready to start a new investigation, type:

```
watchdog new "My Investigation Name"
```

Use a descriptive name — it will become the name of your Obsidian vault. For example:
```
watchdog new "Shell Company Investigation"
```

Watchdog will create a folder in your projects directory and print instructions.

**To open the investigation in Obsidian:**
1. Open Obsidian
2. Click the vault icon in the bottom-left corner
3. Click **Open folder as vault**
4. Navigate to your investigation folder and click Open

**To open the investigation in Claude Code:**
1. Open Claude Code
2. Click **Open project** or use File → Open
3. Navigate to your investigation folder and click Open

You're ready to start ingesting documents.

---

## How to ingest documents

**Drop files into the Incoming folder:**

In your file manager, navigate to your investigation folder. You'll see a folder called `_INCOMING`. Drag any documents you want to process into this folder.

Supported file types: PDF, Word documents, Excel spreadsheets, images (JPG, PNG, TIFF), web pages (HTML), and plain text files.

**Start a Claude Code session:**

With Claude Code open and your investigation folder as the project, simply open a session. Claude will automatically check the Incoming folder for files at the start of every session and process them before anything else.

You can also type `/watchdog-ingest` at any time to process files manually.

**Watch for the briefing:**

After processing, Claude will produce a briefing showing:
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

Claude will answer using only the documents in your vault and will cite the specific page it's drawing from.

---

## Finding connections

Type `/watchdog-surface` to run a full connection analysis across your entire vault. Claude will look for:

- Addresses shared by companies that have no other apparent connection
- People appearing in unusual roles
- Entities mentioned in many documents but with no documented relationships

---

## Checking vault health

Type `/watchdog-health` to check for any problems with your vault — missing files, broken links, or incomplete records.

---

## Tips

**Naming your documents before ingesting:**
Watchdog uses the filename to organize documents. A filename like `shell-co-annual-report-2023.pdf` is much more useful than `scan0042.pdf`. Rename files before dropping them into Incoming when possible.

**Adding context with sidecar files:**
If you want to record where a document came from before Claude processes it, create a text file with the same name but `.yml` extension. For example, alongside `shell-co-annual-report-2023.pdf`, create `shell-co-annual-report-2023.yml` containing:

```
source: https://www.sedar.com/filing/xyz
obtained: 2026-06-05
notes: Check the director change on page 12.
```

This context is merged into the document record and preserved even if you re-ingest the document later.

**Multiple investigations:**
Each investigation is a separate vault. Create as many as you need:
```
watchdog new "City Hall Investigation"
watchdog new "Contractor Investigation"
```

To switch between investigations, switch the open folder in both Obsidian and Claude Code.

To list all your investigations:
```
watchdog list
```

To reopen an investigation in Claude Code:
```
watchdog open shell-company-investigation
```

---

## Troubleshooting

**`watchdog: command not found`**
The install didn't add `watchdog` to your path. Try:
```
pipx ensurepath
```
Then close and reopen your terminal, and try again.

**`Watchdog isn't set up yet`**
Run:
```
watchdog setup
```

**`qpdf not found` or `ghostscript not found` during setup**
Install the missing tool for your platform (see Step 4 above), then run `watchdog setup` again.

**`Docling is not installed` when running /watchdog-ingest**
Run:
```
pipx inject watchdog-intel docling
```

**A document lands in `_FAILED/`**
The document couldn't be processed. Common reasons:
- Password-protected PDF — remove the password and try again
- Corrupted file — try re-downloading the document
- Unsupported format — check the supported file types list above

**Rate limit errors during a large ingest**
If you're ingesting many documents at once and Claude hits a rate limit, it will stop and log where it paused. Files that were successfully processed will be in `morgue/`. Files that weren't processed will still be in `_INCOMING/`. Start a new Claude Code session and run `/watchdog-ingest` again — it will pick up where it left off, skipping any already-processed files.

---

## Audio and video transcription (optional)

Watchdog can transcribe audio and video files if you install support for it. This requires **ffmpeg** and adds roughly 2 GB of dependencies.

**macOS:** `brew install ffmpeg`
**Ubuntu/Debian:** `sudo apt install ffmpeg`
**Windows:** [ffmpeg.org/download.html](https://ffmpeg.org/download.html)

Then reinstall Watchdog with transcription support:
```
pipx install watchdog-intel[asr] --force
```

---

## Getting help

If something isn't working, the best place to get help is the Watchdog GitHub repository:

```
https://github.com/tomcardoso/watchdog/issues
```

When reporting a problem, include:
- What you typed or did
- What you expected to happen
- What actually happened (copy and paste any error messages)
- Your operating system and version
