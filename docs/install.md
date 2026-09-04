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

**Claude Code** is the AI assistant where you do your investigative work: asking questions of your documents, surfacing connections, and building up wiki threads. It's made by Anthropic, the company that makes Claude, and you install it once. It is required — the interactive investigation commands run inside it. The separate document-processing step defaults to Claude too, so nothing extra is needed to get started, though it can be pointed at another provider — OpenAI's GPT-5.6 Luna benchmarked strongest against real filings — to cut cost or improve recall. See [Model backends](configuration.md#model-backends).

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

If you already use [uv](https://docs.astral.sh/uv/), Astral's Python package and tool manager, you can use it instead of pipx for this step and the next — see [Installing with uv instead of pipx](#optional-installing-with-uv-instead-of-pipx) below, then come back to Step 6.

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

Setup checks that qpdf and Ghostscript are installed, enables tab completion in your terminal, detects your machine's OCR engine, and downloads the models Watchdog uses for document conversion, search, and name detection. The name-detection model (GLiNER, which spots people, organizations, and places as a backstop alongside the AI model's own reading — see [Methodology](methodology.md)) is the largest single download, at roughly 1.1GB; the rest add up to a few hundred megabytes more. All of it is a one-time download that may take a few minutes on a slow connection.

Switching OCR engines (`watchdog configure ocr_engine tesseract`) works the same way: Watchdog installs the faster Tesseract binding for you automatically, provided the system Tesseract headers from the platform install steps above are already in place.

Along the way it asks three questions:

1. **Where to store your investigation projects.** Press Return to accept the default (`~/Investigations`), or type a different path.
2. **Whether to install the optional capture browser** for full page snapshots (an extra ~150 MB). The default is no; see [Full page snapshots](#optional-full-page-snapshots) below.
3. **How to authenticate with Claude.** Setup explains that Claude Code powers the interactive commands and is the ingestion default, and reports whether it detects an existing Claude Code login. Press Return to use your subscription, or choose the metered API key option and paste your key.

If you choose the subscription, setup also warns that ingesting more than a few documents at once can be token-heavy for a Pro plan's session limits, and offers to route ingestion to a cheaper metered provider instead — OpenAI's GPT-5.6 Luna is named first, since it benchmarked strongest against real filings (see [Benchmarks](benchmarks.md)), alongside DeepSeek and Gemini — walking you through picking that provider, pasting its key, and choosing a model for each ingest stage. This is entirely optional; declining leaves everything on your Claude Code subscription, same as before. See [Model backends](configuration.md#model-backends) for the full picture, including changing this later with `watchdog auth` or `watchdog configure`.

If you decline and stay on the subscription, setup also lowers `extract_concurrency` from its default of 20 to 3 and tells you it's doing so: concurrent extractions on a subscription share one Claude Code session's rate limit, and 20 reliably throttles it. `watchdog auth` applies the same tune-down if you switch to subscription auth later, and undoes it automatically if you later switch back to an API key — no need to raise it back by hand unless you set your own value.

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

## Optional: using GPT-5.6 Luna for extraction

Claude on your existing subscription is the default and needs nothing extra — skip this section if that's enough for you. It stops being enough once you're feeding Watchdog real volume: subscription auth shares one Claude Code session's rate limit, and a run of even a handful of documents can trip it — one traced case hit it at just six documents ingested at once, and the run stalls waiting out the limit rather than failing outright. Routing extraction to a metered API key instead avoids that, and lets documents run many at a time rather than a few.

Watchdog benchmarks its own model recommendations against real court and financial filings rather than picking one on reputation — see [Benchmarks](benchmarks.md) for the numbers and the reasoning behind them. The current pick for extraction is OpenAI's GPT-5.6 Luna, at roughly $1 per 1,000 pages. That's today's answer, not a permanent one — cheap models keep improving, and this recommendation will move as better ones come along.

To set it up:

1. Go to [platform.openai.com](https://platform.openai.com), sign in or create an account, and open **API keys** in the left sidebar.
2. Add a payment method under **Settings → Billing**. Unlike Claude's flat monthly subscription, OpenAI's API is pay-as-you-go with no free tier, so a card on file is required before a key can make any paid calls.
3. Click **Create new secret key** and copy it — it's shown only once.
4. Run `watchdog setup` (or, if you've already set up, `watchdog configure extractor_model`) and paste the key when it offers to route ingestion to a metered provider.

See [Model backends](configuration.md#model-backends) for routing the other pipeline stages the same way, and [Controlling cost](configuration.md#controlling-cost) for the full cost picture.

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

## Optional: installing with uv instead of pipx

If you already use [uv](https://docs.astral.sh/uv/) — Astral's Python package and tool manager — you can use it instead of pipx for the whole install. Everything past this point (`watchdog setup`, day-to-day use, upgrades) works exactly the same either way; only the install and upgrade commands differ.

Install uv, if you don't have it already:

**macOS:**

```bash
brew install uv
```

**Linux:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows:**

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then install Watchdog:

```bash
uv tool install watchdog-intel
```

If that prints a warning that its tool directory isn't on your `PATH`, run `uv tool update-shell`, then close and reopen Terminal. From here, skip Step 5 above and continue with Step 6 (`watchdog setup`).

Wherever the rest of this page says a `pipx` command, use its `uv` equivalent instead:

| pipx | uv |
|------|-----|
| `pipx install watchdog-intel` | `uv tool install watchdog-intel` |
| `pipx install "watchdog-intel[asr]" --force` | `uv tool install "watchdog-intel[asr]"` |
| `pipx inject watchdog-intel playwright` | `uv tool install watchdog-intel --with playwright` |
| `pipx upgrade watchdog-intel` | `uv tool upgrade watchdog-intel` |

## Where next

Watchdog is installed. [Getting started](getting-started.md) walks you through your first investigation from start to finish. If anything on this page didn't work, see [Troubleshooting](troubleshooting.md).
