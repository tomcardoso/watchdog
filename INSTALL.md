# Installing Watchdog

Watchdog is a tool for managing large collections of public records. Once installed, you drop documents into a folder and Watchdog extracts the names, addresses, companies, and connections — then lets you ask questions in plain language.

This guide assumes you have never used a terminal before. Read through it once before starting.

---

## What you need

| What | Why | Free? |
|------|-----|-------|
| A Mac running macOS 12 or later | Watchdog runs on your computer, not in the cloud | n/a |
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

1. Go to [claude.ai/download](https://claude.ai/download) and download the Mac app
2. Open the downloaded file and drag Claude Code to your Applications folder
3. Open Claude Code and sign in with your Claude.ai account

If you don't have a Claude.ai account yet, create one at [claude.ai](https://claude.ai) and subscribe to Pro or Max before continuing.

---

## Step 3: Open Terminal

Terminal is a built-in Mac app that lets you type commands to your computer. You'll only need it for the next few steps.

1. Press **Command + Space** to open Spotlight
2. Type **Terminal** and press Return
3. A window with a blinking cursor will appear

You'll type commands here. When this guide shows a command in a grey box, type it exactly as shown and press Return.

---

## Step 4: Download Watchdog

In Terminal, type:

```
cd ~/Downloads && git clone https://github.com/[owner]/watchdog.git
```

Wait for it to finish. You'll see a message saying "done."

---

## Step 5: Run the installer

```
bash ~/Downloads/watchdog/setup.sh
```

The installer will:
- Check that Python is installed (and install it if not)
- Install Docling, the document processing library
- Set up the `watchdog` command so you can use it from anywhere

It will ask for your permission before installing anything. Type `y` and press Return when prompted.

This takes 5–10 minutes the first time. You'll see progress messages. When it finishes, you'll see:

```
  Watchdog is installed.
```

---

## Step 6: Reload your shell

Close the Terminal window and open a new one. Then type:

```
watchdog list
```

You should see: `No projects. Create one with: watchdog new <name>`

If you see an error instead, see [Troubleshooting](#troubleshooting) below.

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

Watchdog will create a folder at `~/Investigations/shell-company-investigation/` and print instructions.

**To open the investigation in Obsidian:**
1. Open Obsidian
2. Click the vault icon in the bottom-left corner
3. Click **Open folder as vault**
4. Navigate to `~/Investigations/shell-company-investigation/` and click Open

**To open the investigation in Claude Code:**
1. Open Claude Code
2. Click **Open project** or use File → Open
3. Navigate to `~/Investigations/shell-company-investigation/` and click Open

You're ready to start ingesting documents.

---

## How to ingest documents

**Drop files into the Incoming folder:**

In Finder, navigate to your investigation folder (e.g. `~/Investigations/shell-company-investigation/`). You'll see a folder called `Incoming`. Drag any documents you want to process into this folder.

Supported file types: PDF, Word documents, Excel spreadsheets, images (JPG, PNG, TIFF), web pages (HTML), and plain text files.

**Start a Claude Code session:**

With Claude Code open and your investigation folder as the project, simply open a session. Claude will automatically check the Incoming folder for files at the start of every session and process them before anything else.

You can also type `/ingest` at any time to process files manually.

**Watch for the briefing:**

After processing, Claude will produce a briefing showing:
- What documents were processed
- What entities (people, companies, addresses) were found
- Connections between entities that were already in your vault
- Anything that looks unusual

---

## Asking questions

Once documents are ingested, you can ask questions in plain English:

- `/query Who are the directors of Shell Co Ltd?`
- `/query What address does John Doe use?`
- `/query Which companies share the address 123 Main St?`

Claude will answer using only the documents in your vault and will cite the specific page it's drawing from.

---

## Finding connections

Type `/surface` to run a full connection analysis across your entire vault. Claude will look for:

- Addresses shared by companies that have no other apparent connection
- People appearing in unusual roles
- Entities mentioned in many documents but with no documented relationships

---

## Checking vault health

Type `/health` to check for any problems with your vault — missing files, broken links, or incomplete records.

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
source ~/.zshrc
```
If that doesn't work, run the installer again: `bash ~/Downloads/watchdog/setup.sh`

**`Docling is not installed` when running /ingest**
Run:
```
pip install docling
```

**Claude says it can't find `preprocess.py`**
The pipeline scripts aren't installed. Run:
```
bash ~/Downloads/watchdog/setup.sh
```

**A document lands in `Incoming/Failed/`**
The document couldn't be processed. Common reasons:
- Password-protected PDF — remove the password and try again
- Corrupted file — try re-downloading the document
- Unsupported format — check the supported file types list above

**Rate limit errors during a large ingest**
If you're ingesting many documents at once and Claude hits a rate limit, it will stop and log where it paused. Files that were successfully processed will be in `Incoming/Processed/`. Files that weren't processed will still be in `Incoming/`. Start a new Claude Code session and run `/ingest` again — it will pick up where it left off, skipping any already-processed files.

---

## Getting help

If something isn't working, the best place to get help is the Watchdog GitHub repository at:

```
https://github.com/YOUR_USERNAME/watchdog/issues
```

When reporting a problem, include:
- What you typed or did
- What you expected to happen
- What actually happened (copy and paste any error messages)
- Your macOS version (Apple menu → About This Mac)
