# Troubleshooting

When something goes wrong, find your symptom below and follow the fix. Everything here is done in the Watchdog window. If your problem is not listed, see [Getting help](#getting-help) at the bottom.

## Watchdog could not start its engine

Watchdog is built on a program it installs for itself, called the engine. If it does not start, the app shows **Watchdog could not start its engine**, with **What happened** to expand for details.

1. Choose **Try again**.
2. If it fails again, choose **Repair the engine**. This reinstalls the engine. Your investigations, settings and downloaded models are not touched.

You can also repair from inside the app: **Settings → Setup → Repair or reinstall the engine**.

## The engine installation stops or fails

Setup, or a repair, shows **The installation did not finish** with the reason. The usual cause is the connection: a VPN, a firewall or a flaky network can block the downloads. Another is running out of disk space; setup needs about 7 GB free, and the engine's own update after an app update about 1 GB.

Choose **Try again**. Whatever was already downloaded is kept, and installation resumes where it stopped. **Start over** begins from scratch. **Show details** displays the installer's log, which is worth copying into an issue if the problem persists.

If setup finishes with **Some optional pieces could not be downloaded**, Watchdog still works. It fetches each missing piece the first time it is needed. To try again now, open **Settings → Setup** and choose **Download missing models**. The card stays on **Settings → Setup**, with **Show details** for the log, until you try again.

On a work network that inspects secure connections, a download can fail with a certificate message such as "self-signed certificate in certificate chain". Watchdog trusts the certificates your computer already trusts, so this usually means the network's certificate is not installed on the computer. Ask whoever manages the network to install it, or try another network.

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

Watchdog only changes files in folders you have allowed. When you open an investigation in a folder that is not on the list, the app shows **Allow Watchdog to work in this investigation** instead of the investigation's screens. This happens after you move an investigation to a different folder outside the app, or remove access to the folder that holds it.

Choose **Allow access…** and confirm. If you would rather not, choose **All investigations**; nothing is changed.

## Watchdog needs permission to use a folder

When you choose a folder for investigations in setup or in **New investigation**, Watchdog asks permission to work in it. If you decline, it says it needs your permission and asks you to choose another folder or allow this one. A move or an export to a folder you have not allowed asks in the same way.

To see or change what is allowed, open **Settings → Folder access**. **Allow a folder…** adds one, and the trash button on a row removes it. Removing access deletes nothing; investigations inside that folder ask for access again the next time they are opened. See [Folder access](using-watchdog.md#folder-access).

## Claude is not signed in

Document reading, **Ask Claude** and **Web research** each need a way to reach a model. If a run says no model sign-in is set up, or **Ask Claude** says Claude may not be signed in, open **Settings → Models & keys** and sign in again, or paste an API key. The sign-in opens in your browser and the app waits for you to finish; if the browser did not open, the app shows a link to the sign-in page.

**Ask Claude** and **Web research** always use Claude, even if the document-reading step is set to another provider, so they need a Claude sign-in or key either way. They can also be billed differently from document reading: **Ask Claude and Research** in **Settings → Models & keys** shows which they use. Set to **Claude subscription**, they need you signed in to Claude, and the sign-in button appears there when you are not. The app's sign-in check is approximate, so a question may still work; if one fails to start, this is the usual reason.

## The provider rejected the key, or the account is out of credit

If a run stops with "the provider refused the credentials or account", every document is still queued; nothing was set aside as failed. The model provider turned the request down: the API key is wrong, revoked, or lacks access to the model, or the account has run out of credit or quota. The message includes the provider's own explanation.

Check the key in **Settings → Models & keys**, or top up the account on the provider's site, then add the documents again. The run picks up what is still queued.

## "The key this investigation bills isn't on this computer"

The investigation was set, under **Billing** on its Overview, to bill a named key (for example "Work") that this computer does not have: it was deleted, or the folder came from a colleague whose keys have different names. Watchdog stops before sending anything rather than bill another account. Either add a key with exactly that name under **Settings → Models & keys** (**Add a key**, or **Add key** beside the provider), or choose another key, or **Default**, in the Billing card. See [Billing](models-and-cost.md#billing).

## macOS asks whether Watchdog may use its "Safe Storage" in your Keychain

Watchdog encrypts your API keys with a key kept in your login Keychain, named "Watchdog Safe Storage". macOS lets the app that created that item read it without asking. It asks when a differently signed copy of the app tries: a test build, a copy built from source, or a release signed by a different developer. Choose **Always Allow** if you trust the copy you are running; choose **Deny** and Watchdog can't read your stored keys, so anything that needs one stops without sending anything. Updates to Watchdog's engine never cause this prompt; only the app itself reads the Keychain.

If you denied it by mistake, open Keychain Access, find "Watchdog Safe Storage", add Watchdog under **Access Control**, then restart Watchdog.

## "Watchdog couldn't read the key from this computer's secure storage"

The key was encrypted under another user account or on another computer, or the system's secure storage was reset (on Linux, the keyring may not be running or unlocked). **Settings → Models & keys** marks such keys "can't be read". Replace each one with the key itself, or delete it. On Linux, check that your keyring is unlocked, then restart Watchdog.

## A file could not be read

Watchdog could not convert the file to text, so it set it aside and carried on with the rest. The **Documents** screen lists it under **Couldn't be read** in the strip above the list, and the run's output under **Activity** gives the reason. Common causes:

- **Password-protected PDF** — remove the password and try again.
- **Corrupted file** — try re-downloading or re-exporting it.
- **Unsupported format** — check the [supported file types](using-watchdog.md#supported-file-types).

If you fixed the file yourself (removed the password, or downloaded or exported it again), choose **Remove** under **Couldn't be read**, then add the fixed copy with **Add documents**. **Remove** deletes only Watchdog's copy; a file you chose with Add documents is still wherever you keep it. If the cause has passed without any change to the file, such as a download that failed, choose **Try again**: the files go back with those waiting to be added, and the Add documents dialog opens to read them.

## A recording failed or came out wrong

Recordings are transcribed on your computer during pre-processing. What goes wrong, and what to do:

- **"The transcription model could not be downloaded."** The first recording you add downloads the speech-recognition model (1.5 GB by default), and the connection failed. Check the connection, then add the file again, or download the model from **Settings → Setup** with **Download now** first.
- **"This video has no sound track."** The file has pictures but no audio, so there is nothing to transcribe. If it should have sound, export it again from wherever it came from.
- **"This file could not be read as audio or video."** The file is damaged, or is not the format its name says. Try opening it in another player; if that also fails, get a fresh copy.
- **It takes a long time.** Transcription runs on your computer and is the slowest step for a long recording: with the default model, an hour of audio took 20 to 35 minutes on a four-core test computer. Recordings are transcribed one at a time, and the run shows how far it has got ("Transcribing hearing.mp4, 12:05 of 1:02:05"). A smaller model in [Settings](models-and-cost.md#transcription) is faster but makes more mistakes.
- **The transcript is in the wrong language, or is nonsense.** Language detection listens to the start of the recording, and music, silence or a greeting in another language can mislead it. Set **Transcription language** in [Settings](models-and-cost.md#transcription), then add the recording again.
- **Names or figures are wrong.** Speech recognition mishears uncommon names and numbers; this is the normal failure, not a fault. Click the timestamp beside the passage to hear it, and correct the fact in your own notes. A larger model makes fewer of these mistakes.
- **The player says the format can't be played.** The transcript is still there. Some formats (`.avi`, and some `.mkv` files) cannot be played inside Watchdog; use **Open original** to play the recording in another app.

## A file was set aside

Two things set a file aside, and neither is an error. The **Documents** screen lists these under **Set aside**:

- **It is an exact duplicate.** Watchdog fingerprints every document by its content, so a file that is byte-identical to one already added, even under a different name, is set aside rather than processed twice. Nothing to do.
- **No text was found.** Watchdog found nothing readable in the file, even after OCR. Open the original and check it is legible; a very poor scan can produce no text at all. For a recording, it means no speech was heard: check that it plays with sound.

## A document failed during a run

A document whose extraction fails is logged and set aside, and the rest of the batch still completes. On the **Documents** screen, the strip above the list shows the failed documents with two buttons. **Retry** puts them back and runs them again. **Requeue** moves them back into the queue without running them. The Overview also shows failed documents with a **Retry** button, and **Activity → Maintenance → Requeue failed documents** does the same as Requeue.

The full output of any run, including the reason a document failed, is under **Activity**: select the run on the **Jobs** tab.

## Hitting rate limits

A rate limit is a cap on how much work the AI provider lets you do in a window of time. On a metered API key it is counted in tokens per minute, not documents, so a handful of large documents extracted at once can use it up even when the number of documents looks modest: one document can cost ten times the tokens of another. On a Claude subscription it is your plan's usage limit, which is shared with the rest of your Claude use.

Watchdog already holds new documents back on its own once it is close to your provider's real limit, discovered automatically from the provider's own responses. This works with the Claude API, OpenAI, DeepSeek, Gemini, local and OpenRouter routes, with nothing to configure. **Claude subscription sign-in is the one exception**: it never reports this number, so on that route the automatic backing-off does not engage, and lowering concurrency is the fix.

Two things help when a rate limit stops a run:

- **Lower how many documents are read at once.** In **Add documents**, open **Options** and lower **Documents at once** for that run. To change it for every run, set **Extraction concurrency** under **Settings → Processing**; see [Processing](models-and-cost.md#processing) for its defaults.
- **Wait out the limit.** In **Options**, switch on **Wait out rate limits**. Instead of stopping, the run sleeps until the limit resets and resumes by itself, repeating until the queue is done. This suits an overnight run.

Without it, the run stops cleanly on a rate limit. Nothing is lost: every document already processed is saved, so adding documents again picks up only what is still queued.

## A run stopped at the spending limit

The investigation has reached the spending limit set on its Overview. Nothing is lost: finished documents are kept and the rest stay queued. Raise the limit or choose **Reset the count** on the **Spending limit** card, then add documents again. See [A spending limit for one investigation](models-and-cost.md#spending-limit).

## A step routed to a local model fails immediately

A message that the local backend needs a base URL means the address of your model server has not been set. Open **Settings → Models & keys**, choose the local provider, and enter the server's address (for example `http://localhost:11434/v1`).

A connection error (refused, timed out) means the server at that address is not running, is not reachable from this computer, or the port is wrong. Check that your runner (Ollama, LM Studio, llama.cpp's server, vLLM and so on) is actually up and listening before trying again. Most local runners need no API key; if yours does, add one in **Models & keys**. See [Local and self-hosted models](models-and-cost.md#local-and-self-hosted-models).

## A run was interrupted after extraction

A run has two stages: reading each document with a model (the slow, paid part), then writing everything to the investigation in one pass and producing the briefing. If the second stage never got a chance to run — no briefing appeared, entity summaries look unfinished, or a message said nothing was written yet — the batch can be completed without re-reading anything.

The Overview shows **A batch is waiting to be finished** with a **Finish** button. Choose it, or add documents again, and the app finishes the batch. **Activity → Maintenance → Post-processing** runs only this wrap-up step. It is safe to run more than once: if it hits a rate limit part-way, nothing is written at all, and you run it again once the limit resets. Nothing in an unfinished batch is ever discarded.

## The computer went to sleep during a run

While a run is going, Watchdog asks the computer not to sleep, because a sleep partway through a call stops whatever was in flight. This uses the system's own facilities on a Mac and on Linux systems that have systemd. On other Linux systems and on Windows, there is no equivalent to fall back to, so a run there is not protected against sleep; keep the computer awake until it finishes. If a run is cut off, add the documents again and it resumes.

## Documents can't be added because a run is in progress

While documents are being added, Watchdog keeps a small lock file in the investigation so that a second run cannot start on the same files. There is nothing to release by hand: the lock clears itself.

- **Documents are being added.** A run on this computer is working. The **Jobs** tab in **Activity** shows it if the app started it. You can add more documents once it ends, or stop it there.
- **A run stopped before it finished.** If a run ends without finishing (the app or the computer shut down, or the run crashed), the next time the app looks at the investigation it sees that the run is gone, removes its lock at once and says so in one line on the **Overview** and in the strip above the **Documents** list. Documents the run had finished are kept. Add documents again to finish the rest.
- **Being added on another computer.** The investigation's folder is shared, for instance through a sync service, and a run on another computer holds it. Watchdog cannot see that computer's programs, so it goes by time instead: a run updates its lock every two minutes, and a lock that has not been updated for 15 minutes is treated as left behind and cleared. If the other computer was shut down mid-run, this computer can add documents about 15 minutes later. Don't run on two computers at once: a sync service can deliver one computer's changes after the other has written its own.

## A note was deleted or edited by mistake

Entity and document notes are rewritten from Watchdog's own records, so nothing is lost. Choose **Activity → Maintenance → Rebuild notes**. It rewrites every entity and document note, including the entity summaries, and calls no AI model. Your own **Notes** sections are kept as they are. A note you deleted comes back without its Notes section: open the note's **History**, select a version from before it was deleted, and choose **Restore my notes**. A page of your own (`context.md`, a briefing, a page Claude saved) can be put back the same way with **Restore this version**. See [History and your notes](using-watchdog.md#history-and-your-notes).

## A dated fact is missing from the timeline, or shows only its year

The fact is still on its document and its entities; only its place on the timeline is missing. Watchdog places a date only when it can read it with confidence: the year must be printed, and a date written in figures that could be read two ways (03/04/2021) is placed at its year until a model settles which reading the document means. If that step could not run (a rate limit, the [spending limit](#a-run-stopped-at-the-spending-limit)) or found no confident answer, the date stays as it was. In an investigation started before October 2026, a date the old reader could not read was erased; [bringing the investigation up to date](using-watchdog.md#bringing-an-investigation-up-to-date) reads back the ones the fact's words still state. [How it works](how-it-works.md#dates) explains the rules.

## Bringing an investigation up to date stopped partway

The steps that finished are kept; the rest are still listed under **Activity → Maintenance → Bring this investigation up to date**. A step that stops at the [spending limit](#a-run-stopped-at-the-spending-limit) asks nothing further: raise the limit or reset the count, then run it again. A failed call (a rate limit, a provider outage) leaves its step waiting, and items it had already settled are not asked again. The Overview's banner, once hidden, does not come back for the same steps; Maintenance shows them until they are done. See [Bringing an investigation up to date](using-watchdog.md#bringing-an-investigation-up-to-date).

## A merge joined two different people or companies

Open **Review → Merges**, find the merge under **Recent merges**, and choose **Undo merge**. If the button is unavailable, the reason is shown beside it; see [Merges](using-watchdog.md#merges).

## Updates fail

If the **Update available** button shows **The update could not be downloaded. Try again in a moment**, check your connection and choose it again. If **Restart to update** says Watchdog could not restart into the update, quit Watchdog completely and open it again to finish updating.

**Check for Updates…** (the Watchdog menu on a Mac, the **Help** menu on Windows and Linux) says "Watchdog could not check for updates" when it cannot reach GitHub. Try again later, and check for a VPN or firewall.

If updating keeps failing, download the newest installer from the [releases page](https://github.com/tomcardoso/watchdog/releases/latest) and install it over the old one; your investigations and settings are kept. An update that has installed but whose engine did not follow shows **Updating the Watchdog engine** on the next start. If that fails, use **Try again** or **Repair the engine**, as above.

## Skills look outdated after an update

The record skills — the document-type knowledge — update with the app. Each investigation also has its own copy of Claude's setup (the shortcut commands and instructions Claude uses in that investigation), and Watchdog brings that copy up to date by itself whenever a conversation starts or resumes. It cannot do so in a folder Watchdog hasn't been allowed to work in, and it does not happen if you open the investigation's folder in Claude Code outside the app. In either case, open **Activity → Maintenance** and choose **Refresh Claude setup** for the investigation.

The refresh rewrites the shortcut commands, Watchdog's part of Claude's instructions and Claude's settings. Anything you added to the instructions after Watchdog's part is kept, and so is a shortcut of your own saved under a name that does not start with `watchdog-`. Changes to Watchdog's own shortcuts and to Claude's settings file are replaced.

## An investigation moved or is missing

If you reorganized your files and Watchdog can no longer find an investigation, the **Investigations** list marks it and says what is wrong. Open its **⋯** menu and choose **Move to another folder…**, then pick the folder that now holds it, which points Watchdog at the new location. If the investigation's folder exists but Watchdog does not list it at all, choose **Add existing folder…** on the Investigations screen. **Settings → Check vaults** runs a health check on every investigation and lists any whose folder is missing or broken.

When you point Watchdog at a different folder, it asks permission to work there; see [Folder access](using-watchdog.md#folder-access).

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
- The diagnostics report, if the problem is in what Watchdog produced (see below). It is made to be posted publicly.

### Sending a copy of the investigation

**Activity → Maintenance → Export a copy for troubleshooting** offers two choices, in a folder you choose.

**Diagnostics only (safe to share)** writes one text file of counts and shapes. It is the one to attach to a public issue. It holds:

- how many documents there are of each file type and page count range, and how many facts, Review items, merges, possible duplicates and document requests
- how many records share a name, and how large those groups are
- which kinds of warning the processing log holds and how often, each reduced to its shape: file names and anything in quotes are removed and every number is written as N
- the models, calls, retries, tokens, cost and time of each run, and your operating system, Watchdog version and settings

It never holds a file name, document title, the name of a person or organization, an alias, a relationship, a fact, a quote, a figure, an identifier, a date or a summary. It is built from a fixed list of counts, so a record field added in a later version does not appear in it. No model is used. The dialog shows the whole report before it is saved. Read it before you post it: even counts can hint at things, such as the languages or kinds of documents you work with.

**Full copy (private)** writes one zip file of the investigation's records, for your own use: to keep beside the investigation, or to hand to a colleague you would show the documents to. Much of that is kept in hidden folders inside the investigation, which zipping the folder by hand can leave out. The zip holds:

- Watchdog's records of the documents and entities, including merges, your checks of facts and the processing history
- what the model extracted from each document
- the usage and cost records
- the briefings, and the entity and document notes
- a short note of the Watchdog version, your operating system and your settings

It never contains your API keys or sign-ins, your conversations with Claude, the app's log or earlier versions of files. The original documents, and the text read from them, are left out unless you turn on **Include the original documents**. They make a problem with reading a document possible to reproduce, but the file is much larger and more sensitive.

The dialog shows how large the copy will be before it is written. Even without the originals, the notes and extractions quote the documents and name the people and organizations in them, so treat the copy as investigation material.

When you report a problem, attach the diagnostics report to the issue. Never attach the full copy: issues are public.

## Where next

[Using Watchdog](using-watchdog.md) describes every screen. For settings such as concurrency and models, see [Models, cost and settings](models-and-cost.md).
