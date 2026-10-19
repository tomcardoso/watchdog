# Installing Watchdog

Watchdog is a desktop app. You download an installer, open it like any other program, and the app does the rest the first time it runs. You never need a terminal, and you do not install Python or anything else beforehand.

The first run includes a one-time download of the libraries and models that run on your computer. Only a small part of it, about 200 MB, has to finish before you can carry on; the rest, about 4.5 GB, downloads in the background while you finish setting up and look around. Read the page through once before starting.

## What you need

| What | Why | Free? |
|------|-----|-------|
| A Mac, Windows or Linux computer | Watchdog runs on your computer, not in the cloud | n/a |
| About 7 GB of free disk space and an internet connection | Setup downloads about 4.7 GB in all, and needs room to unpack it. Transcribing recordings takes 1.5 GB more, downloaded the first time you add one | n/a |
| Claude access | Powers the AI that reads your documents and answers your questions | Pro/Max subscription, or an API key |
| [Obsidian](https://obsidian.md) (optional) | A second way to browse an investigation's files | Free |

**Claude access** is how Watchdog signs in to Claude. A Pro subscription (US$20/month) is enough for most journalism work; if you are adding hundreds of documents at a time, Max (from US$100/month) gives you higher limits. If you have an Anthropic API key (a paid, metered way to use Claude), you can use that instead.

Asking questions of your investigation always runs on Claude. The step that reads each document can use a different provider if you prefer — OpenAI, Google Gemini, DeepSeek, OpenRouter or a model running on your own computer — and you choose that during setup or later in Settings. See [Model backends](configuration.md#model-backends) for the options and [Benchmarks](benchmarks.md) for how they compare.

**Obsidian** is a free note-taking app. Every investigation is a folder of Markdown files that Obsidian can open, but you do not need it to use Watchdog.

## Step 1: download the installer

Go to the [latest Watchdog release](https://github.com/tomcardoso/watchdog/releases/latest) on GitHub and download the file for your computer from the list of assets. This page always holds the newest release:

| Computer | File |
|----------|------|
| Mac with Apple silicon (M1 and later) | `Watchdog-<version>-arm64.dmg` |
| Mac with an Intel processor | `Watchdog-<version>-x64.dmg` |
| Windows (64-bit) | `Watchdog-Setup-<version>.exe` |
| Linux | `Watchdog-<version>.AppImage` |

`<version>` is the release number, such as `1.0.4`. If you are not sure which Mac you have, open the Apple menu and choose **About This Mac**: it lists either an Apple chip (M1, M2 and so on) or an Intel processor.

The release page also lists other files, such as ones ending in `.blockmap`, `.zip` and `latest*.yml`. The app's updater uses those; you do not need them.

## Step 2: install it

**Mac.** Open the `.dmg` file and drag Watchdog into your Applications folder. Open Watchdog from there.

**Windows.** Run `Watchdog-Setup-<version>.exe` and follow the installer.

**Linux.** An AppImage is a single file that runs without installing. Make it executable, then open it. In most file managers you right-click the file, open its properties, and tick the box that allows it to run as a program.

### If your computer says it cannot verify the app

Operating systems check who published an app, and they may stop Watchdog on its first launch. This is a general safeguard, and it has a way past it.

- **macOS** may say it cannot verify the developer, or that Watchdog cannot be opened. Open **System Settings**, choose **Privacy & Security**, scroll to the message about Watchdog, and choose **Open Anyway**. Then open Watchdog again and confirm.
- **Windows** may show a SmartScreen message reading "Windows protected your PC". Choose **More info**, then **Run anyway**.

Download Watchdog only from the project's [GitHub release page](https://github.com/tomcardoso/watchdog/releases/latest).

## Step 3: first-run setup

The first time Watchdog opens, it walks through setup. Each step has a **Continue** or **Back** button, and the app remembers where you were if you close it part-way.

1. **Welcome.** A short description of what Watchdog does, and the public-records rule (see below).
2. **Install the Watchdog engine.** Watchdog is built on a Python program. The app installs its own private copy, in its own folder, along with the libraries and models it needs. Nothing else on your computer changes. Choose **Install**.

   The install comes in two parts. The first, about 200 MB, is Python and Watchdog itself; it usually takes a minute or two, and setup moves on to the next step by itself as soon as it is done. The second, about 4.5 GB, carries on in the background: the libraries that read documents, and the models for document conversion (Docling), name detection (GLiNER), search (an embedding model and a reranker) and text recognition for scans. These are described in [Methodology](methodology.md). It needs about 7 GB of free space in all.

   While the first part runs you can watch each stage, choose **Show details** to see the installer's log, and **Cancel** at any point. After a cancel or a failure, choose **Try again** and it resumes where it stopped; **Start over** begins from scratch.

   While the second part runs, you can finish setup, open investigations, read, search for exact words, review and ask questions. **Adding documents waits until it has finished**: the **Add documents** button and the other ways of adding are turned off, with a note saying why. A small bar at the bottom of the sidebar, **Finishing setup…**, shows how far along it is; choose it to see each stage in **Settings → Setup**. The bar disappears when everything is in place. If you quit Watchdog or lose the connection part-way, it picks up where it stopped the next time Watchdog opens. If an optional model cannot be downloaded, Watchdog says so and fetches it the first time it is needed.
3. **Where your investigations live.** Each investigation is a folder inside one parent folder. The default is a folder named `Investigations` in your home folder. Choose **Choose…** to pick another, then **Continue**. Watchdog changes files only in folders you have allowed, so choosing a folder here also gives Watchdog permission to create investigations in it. See [Folder access](app.md#folder-access).
4. **Connect a model.** Pick one:
   - **Claude subscription.** Choose **Sign in with Claude**. Your browser opens so you can sign in to your Claude account; the app waits and continues when you finish. If the browser does not open, the app shows a link to the sign-in page. If you are already signed in to Claude on this computer with a subscription (for example through Claude Code), Watchdog finds that when this step opens, selects this option and shows the account and plan, so there is nothing to sign in to. A sign-in made with an API key does not count as a subscription; Watchdog says so and offers the sign-in button.
   - **Anthropic API key.** Paste a key that starts with `sk-ant-` and choose **Save key**. You create keys in the Anthropic Console, under API keys; **Open the Console** takes you there. Cost depends on the documents and models you choose.
   - **Another provider.** Choose OpenAI, Google Gemini, DeepSeek, OpenRouter or a local model, then paste its key (or, for a local model, the server's address) and pick a model. The question-and-answer screens still need a Claude sign-in; this step offers one as an optional extra, and you can do it later in **Settings → Models & keys**.

   If you are not ready, **Set up later** skips this step, and Watchdog asks again when it needs a model.
5. **Confirm what you send.** Before documents go to a model, Watchdog normally pauses and asks you to confirm they are public records. You can choose to skip that pause for runs that use only your Claude subscription. The default, **Ask before every run**, is recommended. A run that uses a paid API key always asks. Choose **Finish setup**. You can change this later; see [Auto-approve](configuration.md#auto-approve).
6. **Ready.** Choose **Get started**, which offers the sample investigation: a made-up case with a short guided tour (see [Try the sample investigation](getting-started.md#try-the-sample-investigation)). Choose **Cancel** there to go straight to your own work. **Explore a sample investigation** starts the tour without asking, and **Open Watchdog** just opens the app.

> **Public records only.** The originals of your files never leave your computer, and document conversion runs locally. But the extracted text of each document is sent to the AI model you chose, and that cannot be taken back. Use Watchdog only on documents that are public, or presumptively public. Never use it with confidential source communications, leaked or unpublished material, private correspondence, or anything that could identify a source.

## Optional: a metered key for large batches

Claude on your existing subscription is the default and needs nothing extra. It stops being enough once you are adding real volume: a subscription has one usage limit, shared with the rest of your Claude use, and a run of even a handful of large documents can reach it. By default the run then stops, keeping every document it finished; **Wait out rate limits**, in the Options of the Add documents dialog, has it wait and carry on instead (see [Hitting rate limits](troubleshooting.md#hitting-rate-limits)). Routing the reading step to a metered API key (one billed per token) avoids the limit, and lets many documents be read at once.

There are two easy choices. An Anthropic API key keeps the default model, Claude Haiku 5.5 at high effort, at roughly US$1 per 1,000 pages: switch **How Claude is billed** to API key under **Settings → Models & keys**. OpenAI's GPT-6 Luna at medium effort costs about a fifth of that, and found less in Watchdog's benchmark. [Controlling cost](configuration.md#controlling-cost) compares them, and [Benchmarks](benchmarks.md) has the numbers.

To set up GPT-6 Luna:

1. Go to [platform.openai.com](https://platform.openai.com), sign in or create an account, and open **API keys**.
2. Add a payment method under **Settings → Billing**. OpenAI's API is pay-as-you-go with no free tier, so a card must be on file before a key can make any paid calls.
3. Create a secret key and copy it. It is shown only once.
4. In Watchdog, open **Settings → Models & keys** and paste the key under OpenAI. Then, under **Settings → Models**, set Extractor model to `openai:gpt-6-luna` and Extractor effort to `medium`.

## The transcription model

Audio and video work out of the box: the engine includes everything needed to transcribe them, with no separate program to install. The speech-recognition model itself is not part of the first-run download, because only investigations with recordings need it. It downloads the first time you add a recording, and the run shows "Downloading the transcription model (1,531 MB)" while it does; after that it is on your computer for good.

The default model is 1.5 GB; the alternatives in [Settings](configuration.md#transcription) range from 486 MB to 1.6 GB. To download it ahead of time, for example before working somewhere without a reliable connection, open **Settings → Setup** and choose **Download now** beside the transcription model.

## Optional: full page snapshots

By default, saving a web page from **Web research** or **Fetch web links…** is a plain, sanitized fetch. No scripts run, so pages that build themselves in the browser can come through as an empty shell, and images and styling are not captured.

An optional capture browser changes that: each page is rendered in a real (invisible) browser and saved as a self-contained snapshot, with images, fonts and stylesheets included and all scripts stripped. It adds about 150 MB. **Settings → Setup** shows whether it is installed. Without it, Watchdog falls back to the plain fetch, so nothing breaks either way.

## Updating

Watchdog checks GitHub for a newer release shortly after it opens, and never downloads anything without being asked. When one is ready, an **Update available** button appears in the top bar. Choose it to download the update, then choose **Restart to update**. You can also check yourself: on a Mac, choose **Check for Updates…** from the Watchdog menu; on Windows and Linux, from the **Help** menu. Watchdog tells you if you already have the latest version.

An update replaces the app and the engine it carries. The next time Watchdog starts after an update, it installs the matching engine automatically and shows its progress. As on the first run, Watchdog opens as soon as the first part is in place and finishes the rest in the background; the sidebar bar shows it, and adding documents waits for it, usually for less than a minute when the models are already on your computer. Your investigations, settings and downloaded models are untouched.

If an update does not install, see [Troubleshooting](troubleshooting.md#updates-fail). You can always download the newest installer from the [release page](https://github.com/tomcardoso/watchdog/releases/latest) and install it over the old one.

## Uninstalling

Uninstalling Watchdog does not touch your investigations, which are ordinary folders you own. To remove it completely:

1. **Remove the app.** On a Mac, drag Watchdog from Applications to the Trash. On Windows, use **Settings → Apps** and uninstall Watchdog. On Linux, delete the AppImage file.
2. **Remove the app's data folder**, which holds the private engine, downloaded models and preferences (several gigabytes):
   - Mac: `~/Library/Application Support/Watchdog`
   - Windows: `%APPDATA%\Watchdog`
   - Linux: `~/.config/Watchdog`
3. **Remove the `.watchdog` folder in your home folder** (`~/.watchdog`, or `C:\Users\<you>\.watchdog` on Windows). It holds your settings, the list of your investigations, your saved API keys and the list of allowed folders. Delete it only if you want to start from nothing.
4. **Keep or delete your investigations.** They are the folders you chose in setup (by default `Investigations` in your home folder). Delete them only if you no longer need the work; this cannot be undone.

On a Mac, the Library folder is hidden. In Finder, choose **Go → Go to Folder…** and paste the path. 

## Where next

Watchdog is installed. [Getting started](getting-started.md) walks you through your first investigation from start to finish. If anything on this page did not work, see [Troubleshooting](troubleshooting.md).
