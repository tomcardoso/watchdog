# The desktop app

Watchdog is a desktop app. You use it entirely through its window: you never need a terminal. It shows what a list of files can't — a document's pages beside the facts taken from it, page thumbnails, an entity's connections drawn as a network, the timeline laid out by date, and progress you can watch.

Underneath, the app runs Watchdog's engine, the same program that was once run from the command line. Every button that changes an investigation runs one of its steps, and every list and page reads the same files. Each investigation is still an ordinary folder of Markdown files, so it also opens in [Obsidian](https://obsidian.md). The command line is being retired; [Commands](commands.md) remains as a reference for people who already use it.

> **Public records only.** The app sends the text of your documents to the cloud AI model you chose, and it shows a warning, with the number of documents about to be sent, before it sends anything. The rule is unchanged: use Watchdog only for documents that are public or presumptively public.

## Installing it

[Installing Watchdog](install.md) covers downloading the app, the first-run setup (the engine, the local models, the investigations folder, the model provider and the public-records pause), updating and uninstalling. To repair a damaged engine, or to download a model that failed, open **Settings → Setup**; see [Setup](#setup) below.

## Finding your way around

The sidebar lists the screens for the open investigation. The investigation's name sits at the top; click it to switch to another one, see all of them, or create a new one. Press **⌘K** (Ctrl+K on Windows and Linux) anywhere to jump to an entity, a document or a screen by typing part of its name, or to start a search or a question. The same palette is under **View → Command Palette…**, and **View** also has a shortcut for each main screen (⌘1 to ⌘7, or Ctrl+1 to Ctrl+7).

You can drop files onto the window at any time to add them to the open investigation.

Below the screens, the sidebar shows the app's version. Right after Watchdog is installed or updated, a small **Finishing setup…** bar sits above it while the rest of the engine downloads in the background; choose it to see each stage in [Setup](#setup). It disappears when setup has finished. If the download stops, the bar turns into a **Setup did not finish** note with a **Retry** button.

### Investigations

The list of your investigations, with each one's document and entity counts, how much space its [version history](#history) takes, when documents were last added, and anything that needs attention. The **⋯** menu on each offers **Open**, **Rename…**, **Edit description…**, **Move to another folder…**, **Archive** (and **Unarchive**), **Show in folder**, **Open in Obsidian**, **Processing history**, **Remove from Watchdog…** and **Remove and delete files…**. Removing from Watchdog leaves the folder where it is. **Remove and delete files…** permanently deletes the folder and its usage records, and asks you to type the investigation's name first. An existing investigation folder can be added with **Add existing folder…**, and **Settings → Check vaults** checks that every investigation's folder and registry are readable.

### Overview

The headline from the latest briefing (its one-line account of where things stand, with its citations), the briefing itself, what is waiting on you (contradictions, leads, possible duplicates, possible same entities, watch-list hits, disputed facts) and what is in progress (files waiting to be added, documents that failed, research links not yet downloaded). Each item links to the screen that deals with it. You can edit the investigation's description here, and **Ask Claude**, **Search**, **Open in Obsidian** and **Show folder** are a click away.

### Adding documents

**Add documents** (top right, **File → Add Documents…**, or drop files on the window) takes you through four steps:

1. **Choose.** Pick files or folders with **Choose files…** or **Choose folder…**. The dialog also shows anything already waiting in `incoming/`, and offers to retry documents that failed before. **Options** holds every setting for one run — models, effort, verification, the record skill and the rest — and **Estimate cost** shows what the run is likely to cost; **Compare all models** shows the estimate for each model.
2. **Reading documents.** The files are copied into `incoming/` (the originals stay where they are) and converted to text on your computer. Audio and video are transcribed here, one recording at a time, which is the slowest part of this step for a long recording; the first recording also downloads the transcription model once. Nothing is sent anywhere during this step.
3. **Before anything is sent.** The public-records warning, with the exact number of documents about to be sent and which model receives them. Nothing is sent until you choose **Acknowledge and add**. With auto-approve on and every step running on your Claude subscription, this step is skipped with a short notice; see [Auto-approve](configuration.md#auto-approve).
4. **Adding.** Extraction and the finishing steps run, with each document's progress shown as it goes. **Hide** closes the dialog; the run continues, and its progress stays visible in the corner of the window. **Stop** ends it cleanly, and adding again resumes it.

**File → Fetch Links…** downloads web pages from a list of addresses into `incoming/`, from where you add them like any other document.

**Until setup has finished**, adding documents is turned off: the **Add documents** button, dropping files on the window, **Finish adding** on the Overview, the actions under **Documents → Pipeline**, and in **Activity → Maintenance** the incoming-folder watcher, pre-processing, processing, post-processing, **Requeue**, **Rebuild notes** and **Rebuild the search index**. Each says why: "Watchdog is still setting up. You can add documents when it finishes, in a few minutes." If you open the Add documents window another way, it shows the same note and how far along setup is; files you choose stay listed, ready to read when setup finishes. Merging two entities, and undoing a merge, wait too, because they rewrite the search index. Fetching links still works, since it only downloads into `incoming/`.

### Documents

Every document in the investigation, as a grid of first-page thumbnails or a list you can sort by date, type, pages or entities. A strip above it shows documents not yet in the investigation — waiting, read but not extracted, failed, or set aside — with the fix for each (**Retry**, **Requeue**, **Unlock…**). **Retry** puts failed documents back and runs them again; **Requeue** moves them back without running them.

Open a document to read it. The original is on the left, with page navigation, zoom and a find box (see [Finding words in a document](#finding-words-in-a-document)). On the right are the facts taken from it. Each fact's page number scrolls the original to that page, facts the model inferred rather than read are marked, and figures that could not be found on the cited page carry the same note as in the notes (see [Stated vs inferred](vault.md#stated-vs-inferred)). Under each fact is its source passage, or a note that none was found, and three buttons to mark it **Verified**, **Disputed** or **Can't verify**, with an optional note. A bar above the facts filters them (all, not checked, no passage, inferred, figures). Select a fact and press J and K to move, V, D or C to mark it, and N for its note. [Checking facts](investigating.md#checking-facts) explains how to use them. Other tabs show the summary, the entities it names, its full extracted text, its details and embedded metadata, and [your notes](#your-notes).

<a id="finding-words-in-a-document"></a>**Finding words in a document.** The **Find in document** box sits in the toolbar above the original, beside the page number, in every kind of viewer: PDFs, images, documents shown as extracted text, and recording transcripts. Press ⌘F (Ctrl+F on Windows and Linux) to jump to it. It shows how many matches there are and which one you are on; press Enter for the next, Shift+Enter for the previous, and Esc to clear it. Find ignores capital letters, accents, the style of quotation marks (curly or straight), dashes and hyphens, and spaces, so "Renee" finds "Renée", "twenty five" finds "twenty-five", and a word split across two lines with a hyphen is still found. The **Text** tab's search works the same way.

A scanned page has no text of its own, only a picture of one. When Watchdog read the page with OCR (optical character recognition) during pre-processing, it saved where each line of text sits, so find highlights a match on the scan itself, and you can select and copy text from it as from any other page. The highlight's position within a line is an estimate and can be a few letters off; the words found are exactly what OCR read. A document added with an earlier version of Watchdog has no saved positions: find still searches its scanned pages, using the text extracted from them, and takes you to the page, but says plainly that the match cannot be highlighted there. Adding the same file again does not change this, since Watchdog sets aside a file it already has. When find turns up nothing, **Search the extracted text instead** opens the Text tab with the same words.

An audio or video recording opens in a player, with its transcript below. Each passage of the transcript starts with the time it was spoken; click it to play from there, and the passage being spoken is highlighted as the recording plays. A fact from a recording cites a time range, such as 10:00–15:00, instead of a page number: click it to play the passage the fact came from. In the grid and the list, a recording shows its length instead of a page count. See [Recordings](vault.md#recordings) for what a transcript can and cannot tell you.

### Entities

Every person, organization, public body, place, asset and proceeding, filterable by type, with the number of documents each appears in. **Single-source** filters to entities named in only one document, which is where a duplicate entity usually shows up.

An entity's page shows every fact about it from every document, with your check of each and the source passage on request; a summary written by an AI model from those facts, its citations linked to the facts they rest on (a summary written before a merge into the entity, or before a merge was undone, carries an **Out of date** warning until the next run rewrites it); then contradictions, timeline, [your notes](#your-notes) and relationships, and thumbnails of the documents it appears in. See [Entities](investigating.md#entities). The **⋯** menu holds **Merge into…** (explained before it runs, and undone from Review → Merges if it was wrong), **Record a contradiction** and **Re-check contradictions**, which is also the **Re-check** button beside the Contradictions heading: it asks the AI model to compare every recorded fact about the entity with every other, after showing you how many facts and model calls that takes and what it should cost. See [Re-checking contradictions](investigating.md#re-checking-contradictions).

### Network and Timeline

**Network** draws the entities as a graph: each dot is an entity, sized by how many documents name it and coloured by type, and each line is a relationship. Hover to see a dot's connections, click to see its details, double-click to open it.

**Timeline** lays out every dated event by year and month, with links to the page each came from. An event whose fact you marked Disputed stays in place, labelled **disputed**. Filter it to one entity to follow that entity through the record.

### Search

Three kinds of result, kept separate: exact matches (every place the words appear), source passages (ranked by meaning) and notes (what the investigation has concluded). You can set how many results each section shows, a score threshold, and whether results are re-ranked. **Every investigation** searches all of them at once, and **Check a list of names** reports hits for each name in a list. A name that could not be checked is shown as "not checked", never as "no hits". Until setup has finished, only exact matches are searched; a note under the results says so.

### Review

Contradictions, leads, watch-list hits, possible duplicates, merges and document requests, one tab each, plus a **Verification** tab: every fact in the investigation with your own check of it, a progress bar and filters (not checked, disputed, can't verify, verified, no passage, and changed since marked). See [Checking facts](investigating.md#the-verification-tab). Mark an item handled and it stops appearing in briefings and on the Overview. The keyboard works here too: J and K move between items, H marks one handled, O opens it, U undoes. The **Merges** tab lists pairs of records that may be one person or company, to merge or mark not the same, and every merge Watchdog or you have made, with who decided and why, and **Undo merge** where a merge can be split back exactly; see [Merges](investigating.md#merges). The **Handled** tab brings items back, and can pick up checkboxes you ticked in the briefing files. The **Watch list** tab edits the list of names and terms to watch for, and can check every document against it.

### Briefings

Everything Watchdog writes for you to read: the briefing from each run, lead sweeps, watch-list alerts, research memos, and the answers and thread pages Claude has saved, each with a section for [your notes](#your-notes). Pinned at the top: **Current state**, the account of the whole investigation that Claude is given at the start of each conversation (built from the records when you open it, with no AI model; see [Ask Claude](investigating.md#ask-claude)), the processing history (`log.md`) and the investigation's context (`context.md`, which you can edit here).

### Ask Claude and Web research

**Ask Claude** is a conversation with Claude about the investigation, using only its documents and citing the page for each claim. Claude's specialist commands, such as querying the record, surfacing connections and building wiki pages, are offered as buttons. Links in Claude's answers open the entity or document they name. When Claude wants to do something the investigation's settings do not already allow, the app asks you first. Past conversations are listed on the left and can be picked up again; **New conversation** starts a fresh one. **Seed investigation context** has Claude read the `context/` folder and interview you, then write `context.md`.

**Web research** has Claude propose a research mission, work through it with you, and queue the sources it keeps. When you are done, **Download** saves them into `incoming/`, from where you add them like any other document.

Both run on your Claude sign-in. If Claude is not signed in, sign in under **Settings → Models & keys**.

### Activity

Everything the app has run, with the full output of each. **Stop** ends a run cleanly so it can be resumed. The tabs:

- **Jobs.** Running and finished runs. Select one to see its output.
- **Maintenance.** The steps that **Add documents** runs for you, plus repairs, each explained before you run it: **Pre-processing** (read files on this computer), **Processing** (extract with a model), **Post-processing** (write to the investigation and produce the briefing), **Export the graph**, **Release a stuck lock**, **Requeue failed documents**, **Lead sweep**, **Rebuild the timeline**, **Rebuild notes** (rewrites every entity and document note from Watchdog's records, with no model call), **Re-check contradictions** (the entity page's re-check for every entity with two or more facts, with the cost shown before anything is sent), **Rebuild the search index**, **Usage** and **Refresh Claude setup**. Anything that sends text to a model shows the public-records warning first.
- **Processing history.** What was added, and when.
- **Version history.** Every version Watchdog has recorded for this investigation, newest first, each with what caused it and the files it changed. Select a file to open its history at that version. **Remove this version…** deletes what it recorded, except for files it left as they are now. See [History](#history).
- **Usage.** What the models used and cost.

### Settings

**Settings** has the options grouped by topic, each explained and shown with its default; [Configuration](configuration.md) covers what they do. Along with those groups are these panels:

- **Models & keys.** How Watchdog signs in to Claude and to each model provider.
- **Folder access.** The folders Watchdog may change; see [Folder access](#folder-access).
- **Version history.** How much history the open investigation keeps, and **Clear history…**; see [History](#history).
- **Record skills.** The built-in document-type guides; see [Domain skills](skills.md).
- **Appearance.** Theme, and which Python the app is using.
- **Check vaults.** A health check on every investigation's folder.
- **Setup.** The state of the engine, local models and helper tools; see [Setup](#setup).
- **About.** The version and project links.

## History

Watchdog keeps the earlier versions of what it writes, so you can see what changed and when, and put your own writing back. A **History** button is on every entity page, every document, every briefing, the pages Claude saves, `context.md` and the watch list.

History lists the file's versions, newest first. Each says when it was recorded and why, in plain words: "Documents added: council-minutes.pdf, contract.pdf and 3 more", "Merged The City into City of Port Calder (by you)", "Fact marked disputed", "Your notes edited in the app", "Written in an Ask Claude session", "Restored by you". A change Watchdog did not make itself, such as an edit in Obsidian, is recorded the next time Watchdog looks at the file, as "Changed since the last recorded version".

Select a version to see what it changed: removed lines are tinted red and added lines green, and within a changed line the words that differ are marked. **Compared with now** shows the difference between that version and the file as it is today. **Copy this version** puts the whole of it on the clipboard.

What **Restore** does depends on what the file is:

- **Your pages.** `context.md`, the watch list, briefings and the pages Claude saves in `queries/` and `wiki/` can be put back whole with **Restore this version**.
- **Entity and document notes.** Watchdog rebuilds these from the investigation's facts every time documents are added, so an older version of the facts or summary would only be replaced again. **Restore my notes** puts back just your **Notes** section from that version; the rest can be read and copied.
- **Everything else.** The timeline, `requests.md`, `verification.md`, `merges.md` and Watchdog's records are generated from the investigation's data, so their history can be read and copied but not restored. To reverse a merge, use **Undo merge** in Review; to change a fact's mark, mark it again.

A restore is itself recorded, as "Restored by you", so it can be undone the same way.

The history is kept inside the investigation's folder (see [The vault](vault.md#the-history-store)), with only one copy of each distinct version of a file, so it stays small. The investigations list shows how much space each one's history takes. Text you delete from a note, a page or `context.md` stays in the history until you remove the versions that hold it, or clear the history.

### Removing a version

Sometimes an earlier version holds something that should not be kept at all, such as a source's name typed into your notes. First take the text out of the file itself. Then open the file's **History**, select the version that holds it and choose **Remove…**. You can remove only that version, or that version and every earlier one of the same file. Watchdog says how many stored copies of the text will be deleted before you confirm; the deletion cannot be undone.

What happens:

- The text of that version is deleted from the history on this computer. If another version, of this file or another, has exactly the same text, that copy stays, and Watchdog tells you so. Other versions can hold the same words in a different text, so if you are removing a name, look through the versions that stay as well.
- The file's newest version cannot be removed: it is the file as it is now. Change the file first, and the version it replaces can then be removed.
- The versions either side are then compared with each other directly. The list shows where versions were removed ("1 version removed"), and the comparison says that it includes their changes.
- The description of the version, such as the names of the documents a run added, stays in the history while other files in that version remain.
- Nothing is recorded about what was removed, and a copy of the folder made by a backup or a sync service is not affected. Deleting a file is not a secure wipe: as with any deleted file, the disk may hold traces of it until they are overwritten.

**Activity → Version history** can remove a whole version: everything it recorded, except for the files it left as they are now, whose version stays.

To remove every past version at once, open **Settings → Version history** and choose **Clear history…**. This cannot be undone; the files themselves stay as they are and become the first version of a new history.

## Your notes

Entity pages, a document's **Notes** tab, and the answers and thread pages Claude saves (in `queries/` and `wiki/`) each have a **Your notes** section that is yours alone. It shows your notes as formatted text, with links to entities, documents and facts working as they do elsewhere. **Edit notes** (or **Add notes**) opens it for writing; **Done** or the Esc key closes it.

Notes are plain Markdown, the same text you see in Obsidian: `**bold**`, lists, and `[[links]]` to other notes. They are saved a moment after you stop typing, when you click elsewhere, and when you leave the page, and each save is recorded in the file's [history](#history). If Watchdog is writing the investigation's notes at that moment, the save waits until it has finished. If a save fails, your text is kept and Watchdog tells you, with a way back to it; closing the window while notes are still being saved asks first.

Watchdog never writes to this section. Entity and document notes are rebuilt from the investigation's records every time documents are added, and this section is carried over unchanged. A Claude session that updates a saved page is told to leave it as it is.

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

- **Finishing setup.** Shown while the second part of the install is under way, or if it stopped: each stage (the document and search libraries, then each local model) with its progress, **Show details** for the installer's log, and **Cancel**. Cancelling pauses setup; it continues the next time Watchdog opens, or when you choose **Try again**. Adding documents waits until this has finished.
- **Engine.** The private Python environment Watchdog runs in: its status, version and location. **Repair or reinstall the engine** removes the environment and installs it again. Your investigations, settings and downloaded models are not touched. It needs an internet connection and can take several minutes. **Download missing models** fetches any local model that failed to download.
- **Local models.** Document conversion (Docling), name detection (GLiNER), the search embedding model and reranker, text recognition for scans, and the transcription model for audio and video, each marked present or missing. A missing model is downloaded the first time it is needed; the transcription model also has a **Download now** button, for fetching it ahead of time. [Methodology](methodology.md) explains what each is for.
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
