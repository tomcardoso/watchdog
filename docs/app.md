# The desktop app

Watchdog is a desktop app. You use it entirely through its window: you never need a terminal. It shows what a list of files can't — a document's pages beside the facts taken from it, page thumbnails, an entity's connections drawn as a network, the timeline laid out by date, and progress you can watch.

Underneath, the app runs Watchdog's engine, the same program that was once run from the command line. Every button that changes an investigation runs one of its steps, and every list and page reads the same files. Each investigation is still an ordinary folder of Markdown files, so it also opens in [Obsidian](https://obsidian.md). The command line is being retired; [Commands](commands.md) remains as a reference for people who already use it.

> **Public records only.** The app sends the text of your documents to the cloud AI model you chose, and it shows a warning, with the number of documents about to be sent, before it sends anything. The rule is unchanged: use Watchdog only for documents that are public or presumptively public.

## Installing it

[Installing Watchdog](install.md) covers downloading the app, the first-run setup (the engine, the local models, the investigations folder, the model provider and the public-records pause), updating and uninstalling. To repair a damaged engine, or to download a model that failed, open **Settings → Setup**; see [Setup](#setup) below.

## Finding your way around

The sidebar lists the screens for the open investigation. The investigation's name sits at the top; click it to switch to another one, see all of them, or create a new one. Press **⌘K** (Ctrl+K on Windows and Linux) anywhere to jump to an entity, a document or a screen by typing part of its name, or to start a search or a question. The same palette is under **View → Command Palette…**, and **View** also has a shortcut for each main screen (⌘1 to ⌘7, or Ctrl+1 to Ctrl+7).

You can drop files onto the window at any time to add them to the open investigation.

### Investigations

The list of your investigations, with each one's document and entity counts, when documents were last added, and anything that needs attention. The **⋯** menu on each offers **Open**, **Rename…**, **Edit description…**, **Move to another folder…**, **Archive** (and **Unarchive**), **Show in folder**, **Open in Obsidian**, **Processing history**, **Remove from Watchdog…** and **Remove and delete files…**. Removing from Watchdog leaves the folder where it is. **Remove and delete files…** permanently deletes the folder and its usage records, and asks you to type the investigation's name first. An existing investigation folder can be added with **Add existing folder…**, and **Settings → Check vaults** checks that every investigation's folder and registry are readable.

### Overview

The headline from the latest briefing, the briefing itself, what is waiting on you (contradictions, leads, possible duplicates, watch-list hits) and what is in progress (files waiting to be added, documents that failed, research links not yet downloaded). Each item links to the screen that deals with it. You can edit the investigation's description here, and **Ask Claude**, **Search**, **Open in Obsidian** and **Show folder** are a click away.

### Adding documents

**Add documents** (top right, **File → Add Documents…**, or drop files on the window) takes you through four steps:

1. **Choose.** Pick files or folders with **Choose files…** or **Choose folder…**. The dialog also shows anything already waiting in `incoming/`, and offers to retry documents that failed before. **Options** holds every setting for one run — models, effort, verification, the record skill and the rest — and **Estimate cost** shows what the run is likely to cost; **Compare all models** shows the estimate for each model.
2. **Reading documents.** The files are copied into `incoming/` (the originals stay where they are) and converted to text on your computer. Nothing is sent anywhere during this step.
3. **Before anything is sent.** The public-records warning, with the exact number of documents about to be sent and which model receives them. Nothing is sent until you choose **Acknowledge and add**. With auto-approve on and every step running on your Claude subscription, this step is skipped with a short notice; see [Auto-approve](configuration.md#auto-approve).
4. **Adding.** Extraction and the finishing steps run, with each document's progress shown as it goes. **Hide** closes the dialog; the run continues, and its progress stays visible in the corner of the window. **Stop** ends it cleanly, and adding again resumes it.

**File → Fetch Links…** downloads web pages from a list of addresses into `incoming/`, from where you add them like any other document.

### Documents

Every document in the investigation, as a grid of first-page thumbnails or a list you can sort by date, type, pages or entities. A strip above it shows documents not yet in the investigation — waiting, read but not extracted, failed, or set aside — with the fix for each (**Retry**, **Requeue**, **Unlock…**). **Retry** puts failed documents back and runs them again; **Requeue** moves them back without running them.

Open a document to read it. The original is on the left, with page navigation, zoom and find (⌘F or Ctrl+F). On the right are the facts taken from it. Each fact's page number scrolls the original to that page, facts the model inferred rather than read are marked, and figures that could not be found on the cited page carry the same note as in the notes (see [Stated vs inferred](vault.md#stated-vs-inferred)). Other tabs show the summary, the entities it names, its full extracted text, its details and embedded metadata, and your own notes.

### Entities

Every person, organization, public body, place, asset and proceeding, filterable by type, with the number of documents each appears in. **Single-source** filters to entities named in only one document, which is where a duplicate entity usually shows up.

An entity's page shows its summary, analysis, contradictions, timeline and relationships, and thumbnails of the documents it appears in. The **⋯** menu holds **Merge into…** (which cannot be undone, and is explained before it runs) and **Record a contradiction**.

### Network and Timeline

**Network** draws the entities as a graph: each dot is an entity, sized by how many documents name it and coloured by type, and each line is a relationship. Hover to see a dot's connections, click to see its details, double-click to open it.

**Timeline** lays out every dated event by year and month, with links to the page each came from. Filter it to one entity to follow that entity through the record.

### Search

Three kinds of result, kept separate: exact matches (every place the words appear), source passages (ranked by meaning) and notes (what the investigation has concluded). You can set how many results each section shows, a score threshold, and whether results are re-ranked. **Every investigation** searches all of them at once, and **Check a list of names** reports hits for each name in a list. A name that could not be checked is shown as "not checked", never as "no hits".

### Review

Contradictions, leads, watch-list hits, possible duplicates and document requests, one tab each. Mark an item handled and it stops appearing in briefings and on the Overview. The keyboard works here too: J and K move between items, H marks one handled, O opens it, U undoes. The **Handled** tab brings items back, and can pick up checkboxes you ticked in the briefing files. The **Watch list** tab edits the list of names and terms to watch for, and can check every document against it.

### Briefings

Everything Watchdog writes for you to read: the briefing from each run, lead sweeps, watch-list alerts, research memos, and the answers and thread pages Claude has saved. The current-state summary (`hot.md`), the processing history (`log.md`) and the investigation's context (`context.md`, which you can edit here) are pinned at the top.

### Ask Claude and Web research

**Ask Claude** is a conversation with Claude about the investigation, using only its documents and citing the page for each claim. Claude's specialist commands, such as querying the record, surfacing connections and building wiki pages, are offered as buttons. Links in Claude's answers open the entity or document they name. When Claude wants to do something the investigation's settings do not already allow, the app asks you first. Past conversations are listed on the left and can be picked up again; **New conversation** starts a fresh one. **Seed investigation context** has Claude read the `context/` folder and interview you, then write `context.md`.

**Web research** has Claude propose a research mission, work through it with you, and queue the sources it keeps. When you are done, **Download** saves them into `incoming/`, from where you add them like any other document.

Both run on your Claude sign-in. If Claude is not signed in, sign in under **Settings → Models & keys**.

### Activity

Everything the app has run, with the full output of each. **Stop** ends a run cleanly so it can be resumed. The tabs:

- **Jobs.** Running and finished runs. Select one to see its output.
- **Maintenance.** The steps that **Add documents** runs for you, plus repairs, each explained before you run it: **Pre-processing** (read files on this computer), **Processing** (extract with a model), **Post-processing** (write to the investigation and produce the briefing), **Export the graph**, **Release a stuck lock**, **Requeue failed documents**, **Lead sweep**, **Rebuild the timeline**, **Rebuild the search index**, **Usage** and **Refresh Claude setup**. Anything that sends text to a model shows the public-records warning first.
- **Processing history.** What was added, and when.
- **Usage.** What the models used and cost.

### Settings

**Settings** has the options grouped by topic, each explained and shown with its default; [Configuration](configuration.md) covers what they do. Along with those groups are these panels:

- **Models & keys.** How Watchdog signs in to Claude and to each model provider.
- **Folder access.** The folders Watchdog may change; see [Folder access](#folder-access).
- **Record skills.** The built-in document-type guides; see [Domain skills](skills.md).
- **Appearance.** Theme, and which Python the app is using.
- **Check vaults.** A health check on every investigation's folder.
- **Setup.** The state of the engine, local models and helper tools; see [Setup](#setup).
- **About.** The version and project links.

## Folder access

Watchdog only changes files in folders you have allowed. This limits the damage if a document contains instructions meant to trick an AI, or if something goes wrong in the engine. Reading is not limited, but writing, deleting and renaming files are, anywhere under your home folder or on a connected drive.

You allow a folder by choosing it for a purpose: the folder that holds your investigations, an existing investigation you add, a destination you move or export to. Watchdog also asks in a prompt of its own when it needs a folder you have not allowed. Watchdog's own settings, the system's temporary folder and its downloaded models are always available to it.

**Settings → Folder access** lists every allowed folder, what it was allowed for and when. **Allow a folder…** adds one. The trash button on a row, **Remove access**, stops Watchdog changing files there; nothing in the folder is deleted.

If you open an investigation that is outside the allowed folders, which is the case for existing investigations the first time you open them after this feature arrived, or after you have moved one, Watchdog shows **Allow Watchdog to work in this investigation** in place of its screens. Choose **Allow access…** to continue, or **All investigations** to leave. If you decline, nothing is changed, and you can allow it later from the same screen or from Settings.

**Claude sessions.** When you ask Claude a question, it can change files only inside that investigation's folder. Commands it runs on your computer are held to the same limit. On a Mac they run inside the system's own sandbox: they can write only to the investigation, the temporary folder and Watchdog's settings, and they can never change the list of allowed folders or your keys. On Windows and Linux, Claude can run only Watchdog's own pre-approved commands, one at a time. When Claude asks to run a command and you choose **Always**, that covers the exact command, not every command.

This protects against mistakes and against instructions hidden in a document. It does not limit reading, and the few outside programs Watchdog uses to repair damaged PDFs write only to temporary files it chooses.

## Updates

Watchdog checks GitHub for a newer release a few seconds after it opens, and never downloads one without being asked. When an update is ready, **Update available** appears in the top bar, with the version number. Choose it to download; the button shows the progress. When the download finishes, it becomes **Restart to update**.

You can also check yourself with **Check for Updates…**, which is in the Watchdog menu on a Mac and in the **Help** menu on Windows and Linux. It tells you if you already have the latest version. The next time the app starts after an update, it installs the matching engine automatically and shows its progress. See [Updating](install.md#updating) and [Troubleshooting](troubleshooting.md#updates-fail).

## Setup

**Settings → Setup** shows the state of the pieces Watchdog depends on:

- **Engine.** The private Python environment Watchdog runs in: its status, version and location. **Repair or reinstall the engine** removes the environment and installs it again. Your investigations, settings and downloaded models are not touched. It needs an internet connection and can take several minutes. **Download missing models** fetches any local model that failed to download.
- **Local models.** Document conversion (Docling), name detection (GLiNER), the search embedding model and reranker, and text recognition for scans, each marked present or missing. A missing model is downloaded the first time it is needed. [Methodology](methodology.md) explains what each is for.
- **Helper tools.** qpdf and Ghostscript, which are optional and used only for repairing or re-rendering problem PDFs.
- **Optional and downloaded pieces.** The capture browser for full page snapshots, the investigations folder, and the settings file.

## Obsidian

You do not need Obsidian to use the app. Investigations are ordinary folders of Markdown files that Obsidian opens as vaults, so you can use it alongside. **Open in Obsidian** on an investigation, document, entity or note opens it there, and the same item is under **File → Open in Obsidian**.

## When something goes wrong

- **"Watchdog could not start its engine."** Choose **Try again**. If it keeps failing, **Repair the engine** reinstalls it; your investigations and settings are not touched.
- **The installation stops or fails.** The usual cause is the connection: a VPN or a firewall can block the downloads. Choose **Try again**; the app resumes where it stopped.
- **A run failed.** Open **Activity** and select it to see its full output; the fixes in [Troubleshooting](troubleshooting.md) apply.
- **Ask Claude says Claude may not be signed in.** Sign in again from **Settings → Models & keys**.

---

**Where next:** [Investigating](investigating.md) for how to work through what Watchdog finds, or [Troubleshooting](troubleshooting.md) if something is not working.
