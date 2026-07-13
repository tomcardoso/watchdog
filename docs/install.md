# Installing Watchdog

This page gets Watchdog installed and set up on your computer, and assumes you have never used a terminal before. Most steps take a minute or two; the one-time model download during setup can take a few minutes on a slow connection. Read the page through once before starting.

## What you need

| What | Why | Free? |
|------|-----|-------|
| A computer running macOS, Linux, or Windows | Watchdog runs on your computer, not in the cloud | n/a |
| [Obsidian](https://obsidian.md) | The app where you read and explore your documents | Free |
| [Claude Code](https://claude.ai/download) | The AI assistant you investigate in — required | Free to install |
| Claude access | Powers the AI that processes your documents | Pro/Max subscription, or an API key |

**Obsidian** is a note-taking app that Watchdog uses to organize and display your research. You don't need to know how to use it before starting — Watchdog sets it up for you.

**Claude Code** is the AI assistant where you do your investigative work: asking questions of your documents, surfacing connections, and building up wiki threads. It's made by Anthropic, the company that makes Claude, and you install it once. It is required — the interactive investigation commands run inside it. The separate document-processing step can be pointed at other AI providers to cut cost, but Claude Code itself is still needed for everything else. See [Model backends](configuration.md#model-backends).

**Claude access** is required because processing documents requires AI. A Pro subscription (US$20/month) is enough for most journalism work; if you're ingesting hundreds of documents at a time, Max (from US$100/month) gives you higher limits. If you have an Anthropic API key (a paid, metered way to access Claude), you can use that instead.

## Step 1: install Obsidian

1. Go to [obsidian.md](https://obsidian.md) and click **Download**.
2. Open the downloaded file and drag Obsidian to your Applications folder.
3. Open Obsidian — it will ask you to create or open a vault. Click **Create new vault** and give it any name for now. You'll create your real investigation vaults later.

## Step 2: install Claude Code and sign in

1. Go to [claude.ai/download](https://claude.ai/download) and download the app.
2. Open the downloaded file and follow the installation instructions.
3. Open Claude Code and sign in with your Claude.ai account.

If you don't have a Claude.ai account yet, create one at [claude.ai](https://claude.ai) and subscribe to Pro or Max before continuing. If you have an Anthropic API key and prefer to use that instead, run `claude login` in your terminal after installation and follow the prompts.

## Step 3: open the terminal

Terminal is a built-in app that lets you type commands to your computer. You'll only need it for the next few steps.

**macOS:** Press **Command + Space**, type **Terminal**, press Return.

**Linux:** Press **Ctrl + Alt + T**, or search for Terminal in your application menu.

**Windows:** Press **Windows + R**, type **cmd**, press Return. Or install [Windows Terminal](https://apps.microsoft.com/detail/9n0dx20hk701) for a better experience.

## Step 4: install the prerequisites

Watchdog needs two tools for processing PDFs — **qpdf** and **Ghostscript** — and **pipx**, a tool for installing Python programs. On Linux and Windows it also needs **Tesseract**, which handles OCR (optical character recognition — turning scanned pages into searchable text).

**macOS:**

```bash
brew install qpdf ghostscript pipx
pipx ensurepath
```

Then close and reopen Terminal so the new `pipx` path takes effect. If you don't have Homebrew, install it first: [brew.sh](https://brew.sh).

**Ubuntu / Debian Linux:**

```bash
sudo apt install qpdf ghostscript pipx tesseract-ocr libtesseract-dev
```

**Fedora / RHEL Linux:**

```bash
sudo dnf install qpdf ghostscript pipx tesseract tesseract-devel
```

**Windows:**

- qpdf: [github.com/qpdf/qpdf/releases](https://github.com/qpdf/qpdf/releases) — download the installer
- Ghostscript: [ghostscript.com/releases/gsdnld.html](https://ghostscript.com/releases/gsdnld.html) — download the installer
- Tesseract: [github.com/UB-Mannheim/tesseract/wiki](https://github.com/UB-Mannheim/tesseract/wiki) — download the installer. Required for OCR on Windows; `watchdog setup` will refuse to continue without it.
- pipx: open the terminal and run `python -m pip install pipx`, then `pipx ensurepath`

## Step 5: install Watchdog

```bash
pipx install watchdog-intel
```

Wait for it to finish — you'll see a message saying the installation is complete. This installs Watchdog from [PyPI](https://pypi.org/project/watchdog-intel/), the standard package repository for Python tools.

## Step 6: run setup

```bash
watchdog setup
```

Setup checks that qpdf and Ghostscript are installed, enables tab completion in your terminal, detects your machine's OCR engine, and downloads the models Watchdog uses for document conversion and search. The model download happens once and may take a few minutes on a slow connection.

Along the way it asks three questions:

1. **Where to store your investigation projects.** Press Return to accept the default (`~/Investigations`), or type a different path.
2. **Whether to install the optional capture browser** for full page snapshots (an extra ~150 MB). The default is no; see [Full page snapshots](#optional-full-page-snapshots) below.
3. **How to authenticate with Claude.** Setup explains that Claude Code powers the interactive commands and is the ingestion default, and reports whether it detects an existing Claude Code login. Press Return to use your subscription, or choose the metered API key option and paste your key.

If you choose the subscription, setup also warns that ingesting more than a few documents at once can be token-heavy for a Pro plan's session limits, and offers to route ingestion to a cheaper metered provider (OpenAI, DeepSeek, or Gemini) instead — walking you through picking that provider, pasting its key, and choosing a model for each ingest stage. This is entirely optional; declining leaves everything on your Claude Code subscription, same as before. See [Model backends](configuration.md#model-backends) for the full picture, including changing this later with `watchdog auth` or `watchdog configure`.

When setup finishes, reload your shell so tab completion takes effect:

**macOS / zsh:**

```bash
source ~/.zshrc
```

**bash:**

```bash
source ~/.bashrc
```

On Windows there is nothing to reload — setup installs tab completion only for the zsh, bash, and fish shells, so it is skipped there.

After the reload, pressing Tab after `watchdog ` shows the available commands, and pressing Tab after `watchdog status ` completes your investigation names.

## Step 7: check it works

```bash
watchdog about
```

You should see Watchdog's version number and project links. If you see `watchdog: command not found` instead, head to [Troubleshooting](troubleshooting.md#watchdog-command-not-found).

## Optional: audio and video transcription

Watchdog can transcribe audio and video files if you install support for it. This requires **ffmpeg** and adds roughly 2 GB of dependencies.

**macOS:**

```bash
brew install ffmpeg
```

**Ubuntu / Debian Linux:**

```bash
sudo apt install ffmpeg
```

**Windows:** download from [ffmpeg.org/download.html](https://ffmpeg.org/download.html).

Then reinstall Watchdog with transcription support:

```bash
pipx install "watchdog-intel[asr]" --force
```

## Optional: full page snapshots

By default, `watchdog research` and `watchdog fetch` save web pages with a plain, sanitized fetch. No JavaScript runs, so pages that build themselves in the browser can come through as an empty shell, and images and styling aren't captured.

Installing the optional capture browser changes that: every web page is rendered in a real (invisible) browser and saved as a faithful, self-contained snapshot, with images, fonts, and stylesheets included and all scripts stripped. It's worth it if the sources you pull in are often JavaScript-heavy or you want the visual layout preserved.

`watchdog setup` asks whether to install it. To install it later, or if you said no the first time:

```bash
pipx inject watchdog-intel playwright
~/.local/pipx/venvs/watchdog-intel/bin/playwright install chromium
```

This adds about 150 MB (the browser itself). If it isn't installed, `watchdog research` and `watchdog fetch` fall back to the plain sanitized fetch automatically — nothing breaks either way.

## Where next

Watchdog is installed. [Getting started](getting-started.md) walks you through your first investigation from start to finish. If anything on this page didn't work, see [Troubleshooting](troubleshooting.md).
