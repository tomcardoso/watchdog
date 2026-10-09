# Troubleshooting

When something goes wrong, find your symptom below and follow the fix. Everything here is done in the Watchdog window. If your problem is not listed, see [Getting help](#getting-help) at the bottom.

## Watchdog could not start its engine

Watchdog is built on a program it installs for itself, called the engine. If it does not start, the app shows **Watchdog could not start its engine**, with **What happened** to expand for details.

1. Choose **Try again**.
2. If it fails again, choose **Repair the engine**. This reinstalls the engine. Your investigations, settings and downloaded models are not touched.
3. If you installed the older command-line version and want the app to use that, **Choose Python…** lets you point it at a particular installation.

You can also repair from inside the app: **Settings → Setup → Repair or reinstall the engine**.

## The engine installation stops or fails

Setup, or a repair, shows **The installation did not finish** with the reason. The usual cause is the connection: a VPN, a firewall or a flaky network can block the downloads. Another is running out of disk space; setup needs about 7 GB free.

Choose **Try again**. Whatever was already downloaded is kept, and installation resumes where it stopped. **Start over** begins from scratch. **Show details** displays the installer's log, which is worth copying into an issue if the problem persists.

If setup finishes with **Some optional pieces could not be downloaded**, Watchdog still works. It fetches each missing piece the first time it is needed. To try again now, open **Settings → Setup** and choose **Download missing models**.

If the message says the app cannot install the engine because the installer files are missing, download the app again from the [releases page](https://github.com/tomcardoso/watchdog/releases/latest).

## Add documents is turned off, or "Watchdog is still setting up"

Right after Watchdog is installed or updated, it finishes downloading its document and search libraries and its local models in the background. Until that is done, adding documents is turned off and says "Watchdog is still setting up". Everything else works. The **Finishing setup…** bar at the bottom of the sidebar shows how far along it is; the full download is about 4.5 GB, so on a slow connection it can take a while.

- **The bar is still moving.** Wait for it to finish. You can keep working in the meantime.
- **The bar says "Setup did not finish".** The download stopped, usually because the connection dropped. Choose **Retry**. Whatever was already downloaded is kept. If it keeps failing, open **Settings → Setup**, choose **Show details** and look at the last lines of the log; the causes are the same as in [The engine installation stops or fails](#the-engine-installation-stops-or-fails).
- **You quit Watchdog part-way.** Nothing is lost. Setup continues by itself the next time Watchdog opens.
- **Search shows only exact matches.** Searching by meaning needs the search models, which arrive with the rest of setup. Results ranked by meaning return when it finishes.

## macOS or Windows will not open the app

On the first launch, your computer may say it cannot verify the developer of Watchdog. Follow the steps in [the install guide](install.md#if-your-computer-says-it-cannot-verify-the-app): on a Mac, **System Settings → Privacy & Security → Open Anyway**; on Windows, **More info → Run anyway**.

## "Allow Watchdog to work in this investigation"

Watchdog only changes files in folders you have allowed. When you open an investigation in a folder that is not on the list, the app shows **Allow Watchdog to work in this investigation** instead of the investigation's screens. This happens the first time you open an investigation created before this safeguard existed, and after you move an investigation to a different folder.

Choose **Allow access…** and confirm. If you would rather not, choose **All investigations**; nothing is changed.

## Watchdog needs permission to use a folder

When you choose a folder for investigations in setup or in **New investigation**, Watchdog asks permission to work in it. If you decline, it says it needs your permission and asks you to choose another folder or allow this one. A move or an export to a folder you have not allowed asks in the same way.

To see or change what is allowed, open **Settings → Folder access**. **Allow a folder…** adds one, and the trash button on a row removes it. Removing access deletes nothing; investigations inside that folder ask for access again the next time they are opened. See [Folder access](app.md#folder-access).

## Claude is not signed in

Document reading, **Ask Claude** and **Web research** each need a way to reach a model. If a run says no model sign-in is set up, or **Ask Claude** says Claude may not be signed in, open **Settings → Models & keys** and sign in again, or paste an API key. The sign-in opens in your browser and the app waits for you to finish; if the browser did not open, the app shows a link to the sign-in page.

**Ask Claude** and **Web research** always use Claude, even if the document-reading step is set to another provider, so they need a Claude sign-in either way. The app's sign-in check is approximate, so a question may still work; if one fails to start, this is the usual reason.

## The provider rejected the key, or the account is out of credit

If a run stops with "the provider refused the credentials or account", every document is still queued; nothing was set aside as failed. The model provider turned the request down: the API key is wrong, revoked, or lacks access to the model, or the account has run out of credit or quota. The message includes the provider's own explanation.

Check the key in **Settings → Models & keys**, or top up the account on the provider's site, then add the documents again. The run picks up what is still queued.

## A file landed in incoming/failed/

The file could not be read when Watchdog converted it to text. It sits in `incoming/failed/` alongside an explanation of what went wrong. On the **Documents** screen, the strip above the list shows failed files. Common causes:

- **Password-protected PDF** — remove the password and try again.
- **Corrupted file** — try re-downloading or re-exporting it.
- **Unsupported format** — check the [supported file types](vault.md#supported-file-types).

To retry, fix the problem, move the file from `incoming/failed/` back into `incoming/` (in your file manager; **File → Show in Folder** opens the investigation's folder), then choose **Add documents** again.

## A recording failed or came out wrong

Recordings are transcribed on your computer during pre-processing. What goes wrong, and what to do:

- **"The transcription model could not be downloaded."** The first recording you add downloads the speech-recognition model (1.5 GB by default), and the connection failed. Check the connection, then add the file again, or download the model from **Settings → Setup** with **Download now** first.
- **"This video has no sound track."** The file has pictures but no audio, so there is nothing to transcribe. If it should have sound, export it again from wherever it came from.
- **"This file could not be read as audio or video."** The file is damaged, or is not the format its name says. Try opening it in another player; if that also fails, get a fresh copy.
- **It takes a long time.** Transcription runs on your computer and is the slowest step for a long recording: with the default model, an hour of audio took 20 to 35 minutes on a four-core test computer. Recordings are transcribed one at a time, and the run shows how far it has got ("Transcribing hearing.mp4, 12:05 of 1:02:05"). A smaller model in [Settings](configuration.md#transcription) is faster but makes more mistakes.
- **The transcript is in the wrong language, or is nonsense.** Language detection listens to the start of the recording, and music, silence or a greeting in another language can mislead it. Set **Transcription language** in [Settings](configuration.md#transcription), then add the recording again.
- **Names or figures are wrong.** Speech recognition mishears uncommon names and numbers; this is the normal failure, not a fault. Click the timestamp beside the passage to hear it, and correct the fact in your own notes. A larger model makes fewer of these mistakes.
- **The player says the format can't be played.** The transcript is still there. Some formats (`.avi`, and some `.mkv` files) cannot be played inside Watchdog; use **Open original** to play the recording in another app.

## A file landed in incoming/skipped/

Two things send a file here, and neither is an error:

- **It is an exact duplicate.** Watchdog fingerprints every document by its content, so a file that is byte-identical to one already added, even under a different name, is set aside rather than processed twice. Nothing to do.
- **No text was found.** Watchdog found nothing readable in the file, even after OCR. Open the original and check it is legible; a very poor scan can produce no text at all. For a recording, it means no speech was heard: check that it plays with sound.

## A document failed during a run

A document whose extraction fails is logged and set aside, and the rest of the batch still completes. On the **Documents** screen, the strip above the list shows the failed documents with two buttons. **Retry** puts them back and runs them again. **Requeue** moves them back into the queue without running them. The Overview also shows failed documents with a **Retry** button, and **Activity → Maintenance → Requeue failed documents** does the same as Requeue.

The full output of any run, including the reason a document failed, is under **Activity**: select the run on the **Jobs** tab.

## Hitting rate limits

A rate limit is a cap on how much work the AI provider lets you do in a window of time — specifically, tokens per minute, not documents per minute. A handful of large documents extracted at once can use up that budget even when the number of documents at once looks modest, since one document's token cost can differ from another's by an order of magnitude.

Watchdog already holds new documents back on its own once it is close to your provider's real limit, discovered automatically from the provider's own responses. This works with the Claude API, OpenAI, DeepSeek, Gemini, local and OpenRouter routes, with nothing to configure. **Claude subscription sign-in is the one exception**: it never reports this number, so on that route the automatic backing-off does not engage, and lowering concurrency is the fix.

Two things help when a rate limit stops a run:

- **Lower how many documents are read at once.** In **Add documents**, open **Options** and lower **Documents at once** for that run. To change it permanently, change the concurrency setting in **Settings**; see [Configuration](configuration.md) for `extract_concurrency` and its defaults.
- **Wait out the limit.** In **Options**, switch on **Wait out rate limits**. Instead of stopping, the run sleeps until the limit resets and resumes by itself, repeating until the queue is done. This suits an overnight run.

Without it, the run stops cleanly on a rate limit. Nothing is lost: every document already processed is saved, so adding documents again picks up only what is still queued.

On Claude subscription sign-in you can also set a manual token budget. Check the Claude Console's rate-limits page for your account's tokens-per-minute figure, and see `extract_token_budget` in [Configuration](configuration.md).

## A step routed to a local model fails immediately

A message that the local backend needs a base URL means the address of your model server has not been set. Open **Settings → Models & keys**, choose the local provider, and enter the server's address (for example `http://localhost:11434/v1`).

A connection error (refused, timed out) means the server at that address is not running, is not reachable from this computer, or the port is wrong. Check that your runner (Ollama, LM Studio, llama.cpp's server, vLLM and so on) is actually up and listening before trying again. Most local runners need no API key; if yours does, add one in **Models & keys**. See [Configuration](configuration.md#local-and-self-hosted-models).

## A run was interrupted after extraction

A run has two stages: reading each document with a model (the slow, paid part), then writing everything to the investigation in one pass and producing the briefing. If the second stage never got a chance to run — no briefing appeared, entity summaries look unfinished, or a message said nothing was written yet — the batch can be completed without re-reading anything.

The Overview shows **A batch is waiting to be finished** with a **Finish** button. Choose it, or add documents again, and the app finishes the batch. **Activity → Maintenance → Post-processing** runs only this wrap-up step. It is safe to run more than once: if it hits a rate limit part-way, nothing is written at all, and you run it again once the limit resets. Nothing in an unfinished batch is ever discarded.

## The computer went to sleep during a run

While a run is going, Watchdog asks the computer not to sleep, because a sleep partway through a call stops whatever was in flight. This uses the system's own facilities on a Mac and on Linux systems that have systemd. On other Linux systems and on Windows, there is no equivalent to fall back to, so a run there is not protected against sleep; keep the computer awake until it finishes. If a run is cut off, add the documents again and it resumes.

## A lock is stuck

If a run was interrupted, a lock file can be left behind that blocks the next run. Open **Activity → Maintenance** and choose **Release lock** under **Release a stuck lock**. The card shows whether a pre-processing or processing lock is currently held. The same lock can also be released with **Unlock…** in the strip above the **Documents** list.

A running step refreshes its lock every five minutes, however long it runs, so a lock only ages once the run that held it has stopped. If the lock is recent (under 30 minutes old), Watchdog leaves it alone, because the run may still be going. Check the **Jobs** tab to make sure nothing is running. Once you are sure, switch on **Force** and choose **Force release**.

## A note was deleted or edited by mistake

Entity and document notes are rewritten from Watchdog's own records, so nothing is lost. Choose **Activity → Maintenance → Rebuild notes**. It rewrites every entity and document note, including the AI-written summaries, and calls no AI model. Your own **Notes** sections are kept as they are, but a note you deleted takes its Notes section with it, so keep anything you want to keep in a note that still exists.

## A merge joined two different people or companies

Open **Review → Merges**, find the merge under **Recent merges**, and choose **Undo merge**. If the button is unavailable, the reason is shown beside it; see [Merges](investigating.md#merges).

## Updates fail

If the **Update available** button shows **The update could not be downloaded. Try again in a moment**, check your connection and choose it again. If **Restart to update** says Watchdog could not restart into the update, quit Watchdog completely and open it again to finish updating.

**Check for Updates…** (the Watchdog menu on a Mac, the **Help** menu on Windows and Linux) says "Watchdog could not check for updates" when it cannot reach GitHub. Try again later, and check for a VPN or firewall.

If updating keeps failing, download the newest installer from the [releases page](https://github.com/tomcardoso/watchdog/releases/latest) and install it over the old one; your investigations and settings are kept. An update that has installed but whose engine did not follow shows **Updating the Watchdog engine** on the next start. If that fails, use **Try again** or **Repair the engine**, as above.

## Skills look outdated after an update

The record skills — the document-type knowledge — update with the app. But each investigation also has its own copy of Claude's setup (the commands and instructions Claude uses in that investigation), and that copy keeps its old version. Open **Activity → Maintenance** and choose **Refresh Claude setup** for the investigation. It also brings the investigation's dashboard and Claude settings up to date. If the instructions predate the current format, the old copy is saved as `.claude/CLAUDE.md.before-refresh`.

## An investigation moved or is missing

If you reorganized your files and Watchdog can no longer find an investigation, the **Investigations** list marks it and says what is wrong. Open its **⋯** menu and choose **Move to another folder…**, then pick the folder that now holds it, which points Watchdog at the new location. If the investigation's folder exists but Watchdog does not list it at all, choose **Add existing folder…** on the Investigations screen. **Settings → Check vaults** runs a health check on every investigation and lists any whose folder is missing or broken.

When you point Watchdog at a different folder, it asks permission to work there; see [Folder access](app.md#folder-access).

## Obsidian says "Vault not found"

You chose **Open in Obsidian** and Obsidian showed a "Vault not found" error. Obsidian only reads its list of vaults when it starts, so an investigation created while Obsidian was already running is invisible to it until you restart it.

Quit Obsidian completely (use **Quit**, not just close the window), then choose **Open in Obsidian** again.

## Getting help

The app keeps a log of what it does. Open **Help → Show Log File** to find it; it is worth attaching when you report a problem. The full output of any run is under **Activity**.

If something is not working, open an issue at [github.com/tomcardoso/watchdog/issues](https://github.com/tomcardoso/watchdog/issues) (**Help → Report an Issue** opens the page). Include:

- What you did
- What you expected to happen
- What actually happened — copy and paste any error messages
- Your operating system and version, and the Watchdog version (shown at the bottom of the sidebar and under **Settings → About**)
- The log file, if you can share it. Check it first: it can contain file names and paths from your investigations.

## Where next

[The desktop app](app.md) describes every screen. For settings such as concurrency and models, see [Configuration](configuration.md). If you already use the command line, [Commands](commands.md) is the reference for the command line (being retired).
