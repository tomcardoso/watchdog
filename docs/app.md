# The desktop app

Watchdog has a desktop app as well as the terminal commands. It is the same program underneath: every button that changes an investigation runs the same `watchdog` command you would type, and every list and page reads the same files. What the app adds is what a terminal can't show — a document's pages beside the facts taken from it, page thumbnails, an entity's connections drawn as a network, the timeline laid out by date, and progress you can watch.

You can switch between the two freely. An investigation created in the app opens in the terminal and in Obsidian, and the other way round.

> **Public records only.** The app sends documents to a cloud AI model exactly as `watchdog add` does, and shows the same warning, with the number of documents about to be sent, before it sends anything. The rule is unchanged: use Watchdog only for documents that are public or presumptively public.

## Installing it

Install the app like any other. The first time it opens, it walks you through setup; you never need a terminal.

1. **Welcome.** A short explanation of what Watchdog does, and the public-records rule.
2. **Install the engine.** The app downloads Python, Watchdog's libraries and the models that run on your computer (document conversion, name detection and search). This is a one-time download of about 5 GB and needs about 7 GB of free space and an internet connection. The app keeps everything in its own folder, so nothing else on your computer changes. You can watch each stage, open the details, cancel, and try again; whatever was already downloaded is kept. If you close the app part-way, it picks up at the same step when you reopen it.
3. **Where your investigations live.** The folder where each investigation gets its own folder. The default is `Investigations` in your home folder.
4. **Connect a model.** Sign in with a Claude subscription (no per-run charge), paste an Anthropic API key (billed per use), or use another provider such as OpenAI, Gemini, DeepSeek, OpenRouter or a model on your own computer. The Ask Claude screens always need a Claude sign-in; if you pick another provider, setup offers that sign-in as an optional extra.
5. **Confirm what you send.** Whether to skip the public-records pause for runs that use only your Claude subscription. The default is to ask every time.
6. **Ready.** Create your first investigation or open the app.

When a new version of the app needs a newer engine, the app updates it on its own at the next start and shows its progress. To repair a damaged engine, or to download a model that failed, open **Settings → Setup**: **Repair or reinstall the engine** keeps your investigations, settings and models.

### Running the app from source

This is for developers. You need Node.js 20 or later and, to run against your own checkout, a Python with Watchdog's dependencies:

```bash
git clone https://github.com/tomcardoso/watchdog.git
cd watchdog/gui
npm install
WATCHDOG_PYTHON=/path/to/python npm run dev
```

Without `WATCHDOG_PYTHON`, the app looks for an existing Watchdog installation and, if it finds none, offers the same setup as above. Settings → Appearance shows which Python it is using.

## Finding your way around

The sidebar lists the screens for the open investigation. The investigation's name sits at the top; click it to switch to another one or create a new one. Press **⌘K** (Ctrl+K on Windows and Linux) anywhere to jump to an entity, a document or a screen by typing part of its name, or to start a search or a question.

You can drop files onto the window at any time to add them to the open investigation.

### Investigations

The list of your investigations — the same list as `watchdog projects list` — with each one's document and entity counts, when documents were last added, and anything that needs attention. The **⋯** menu on each covers the rest of `watchdog projects`: rename, change the description, move the folder, archive, show the ingest history, and remove it from Watchdog. **Remove and delete files** permanently deletes the folder and its usage records, and asks you to type the investigation's name first. **Check vaults** runs the same checks as `watchdog settings doctor`.

### Overview

What `watchdog` on its own shows in the terminal: the headline from the latest briefing, the briefing itself, what is waiting on you (contradictions, leads, possible duplicates, watch-list hits) and what is in progress (files waiting to be added, documents that failed, research links not yet downloaded). Each item links to the screen that deals with it.

### Adding documents

**Add documents** (top right, or drop files on the window) is `watchdog add`, in four steps:

1. **Choose.** Pick files or folders. The dialog also shows anything already waiting, and offers to retry documents that failed before (`--retry`). **Options** holds every setting `watchdog add` accepts for one run — models, effort, verification, the record skill and the rest — and **Estimate cost** gives the same estimate as `--estimate`.
2. **Read.** The files are copied into `incoming/` (the originals stay where they are) and converted to text on your computer. Nothing is sent anywhere during this step.
3. **Confirm.** The public-records warning, with the exact number of documents about to be sent and which model receives them. Nothing is sent until you acknowledge it. With `auto_approve` on and every step running on your Claude subscription, this step is skipped with a one-line notice, as in the terminal — see [Auto-approve](configuration.md#auto-approve).
4. **Add.** Extraction and the finishing steps run, with each document's progress shown as it goes. You can close the dialog; the run continues, and its progress stays visible in the corner of the window.

### Documents

Every document in the investigation, as a grid of first-page thumbnails or a list you can sort by date, type, pages or entities. A strip above it shows documents not yet in the vault — waiting, read but not extracted, failed, or set aside — with the fix for each (Retry, Requeue, Unlock).

Open a document to read it. The original is on the left, with page navigation, zoom and find (⌘F or Ctrl+F). On the right are the facts taken from it. Each fact's page number scrolls the original to that page, facts the model inferred rather than read are marked, and figures that couldn't be found on the cited page carry the same note as in the vault (see [Stated vs inferred](vault.md#stated-vs-inferred)). Other tabs show the summary, the entities it names, its full extracted text, its details and embedded metadata, and your own notes.

### Entities

Every person, organization, public body, place, asset and proceeding, filterable by type, with the number of documents each appears in. **Single-source** filters to entities named in only one document, which is where a duplicate entity usually shows up.

An entity's page shows its summary, analysis, contradictions, timeline and relationships, and thumbnails of the documents it appears in. The **⋯** menu holds **Merge into…** (`watchdog review merge-entities`, which is irreversible and is explained before it runs) and **Record a contradiction** (`watchdog review add-contradiction`).

### Network and Timeline

**Network** draws the entities as a graph: each dot is an entity, sized by how many documents name it and coloured by type, and each line is a relationship. Hover to see a dot's connections, click to see its details, double-click to open it.

**Timeline** lays out every dated event by year and month, with links to the page each came from. Filter it to one entity to follow that entity through the record.

### Search

`watchdog search`, with its three kinds of result kept separate: exact matches (every place the words appear), source passages (ranked by meaning) and notes (what the investigation has concluded). The same options are available: results per section, a score threshold, re-ranking on or off. **Every investigation** searches all of them at once, and **Check a list of names** reports hits for each name in a list, as `--batch` does. A name that couldn't be checked is shown as "not checked", never as "no hits".

### Review

`watchdog review`: contradictions, leads, watch-list hits, possible duplicates and document requests, one tab each. Mark an item handled and it stops appearing in briefings and on the Overview, exactly as in the terminal. The keyboard works here too: J and K move between items, H marks one handled, O opens it, U undoes. The **Handled** tab brings items back, and can pick up checkboxes you ticked in the briefing files (`--sync`). The **Watch list** tab edits `watchlist.md` and can check every document against it.

### Briefings

Everything Watchdog writes for you to read: the briefing from each run, lead sweeps, watch-list alerts, research memos, and the answers and thread pages Claude has saved. The current-state summary (`hot.md`), the ingest history (`log.md`) and the investigation's context (`context.md`, which you can edit here) are pinned at the top.

### Ask Claude and Web research

**Ask Claude** is `watchdog ask` inside the app: a conversation with Claude Code about the investigation, with the same `/watchdog-query`, `/watchdog-surface`, `/watchdog-wiki` and other commands, which are offered as buttons. Links in Claude's answers open the entity or document they name. When Claude wants to do something the investigation's settings don't already allow, the app asks you first. Past conversations are listed on the left and can be picked up again. **Seed investigation context** is `watchdog ask --context`.

**Web research** is `watchdog research`. Claude proposes a research mission, works through it with you, and queues the sources it keeps. When you're done, **Download** saves them into `incoming/`, from where you add them like any other document.

Both run on whatever Claude Code is signed in with, like the terminal commands. **Open in Terminal** starts the same session in a terminal instead, if you prefer.

### Activity

Everything the app has run, with the full output of each — the same text the terminal would show. **Stop** sends the equivalent of Ctrl+C, so a run stops cleanly and can be resumed. The **Maintenance** tab has the commands from `watchdog help maintenance` — `chew`, `dig`, `bark`, `requeue`, `leads`, `timeline`, `reindex`, `export`, `unlock` — each explained before you run it. Running `dig` shows the public-records warning first, as it does in the terminal. **Ingest history** and **Usage** show the same information as `watchdog projects log` and `watchdog usage`.

### Settings

Every setting from `watchdog settings`, grouped and explained, with each one's default. **Models & keys** is `watchdog settings auth`, **Record skills** lists the skills (`watchdog settings skills`), and **Setup** shows the state of the engine, the local models and the optional helper tools, and repairs them.

## Obsidian

You don't need Obsidian to use the app. Investigations are still ordinary Obsidian vaults, so you can keep using it alongside: **Open in Obsidian** on a document, entity or note opens it there.

## When something goes wrong

- **"Watchdog could not start its engine."** Choose **Try again**. If it keeps failing, **Repair the engine** reinstalls it; your investigations and settings are not touched.
- **The installation stops or fails.** The usual cause is the connection: a VPN or a firewall can block the downloads. Choose **Try again**; the app resumes where it stopped.
- **A run failed.** Open **Activity** and select it to see its full output; the fixes in [Troubleshooting](troubleshooting.md) apply unchanged.
- **Ask Claude says Claude Code may not be signed in.** Sign in again from **Settings → Models & keys**. The app's check is approximate, so a question may still work; if one fails to start, this is why.

---

**Where next:** [Investigating](investigating.md) for how to work through what Watchdog finds, or [Command reference](commands.md) for the terminal command behind each screen.
