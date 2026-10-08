# Getting started

This walkthrough takes you through your first investigation from start to finish: creating an investigation, adding documents, watching them being read, reading the briefing, and asking Claude questions. It assumes Watchdog is already installed and set up; if not, start with the [installation guide](install.md).

Two things to keep in mind before you begin.

> **Public records only.** Your original files never leave your computer — all the file conversion and OCR happens locally. But the extracted text of each document (a plain-text representation of its contents) is sent to a cloud AI model for analysis. That is why Watchdog must only be used on documents that are public or presumptively public. Never process confidential source material, leaked documents, private correspondence, or anything that could identify a source. If in doubt, do not process it.

> **Verify before you publish.** AI extraction makes mistakes. Every fact Watchdog extracts links back to its source document and page, and facts the model inferred rather than read are marked as inferred — those are leads, not findings. Follow the link to the page before you publish anything.

## Create an investigation

Each investigation lives in its own folder on your computer: your documents, plus a linked set of notes Watchdog writes about them. To create one, choose **Create my first investigation** at the end of setup. Later, use **File → New Investigation…**, or open the investigation menu at the top of the sidebar and choose **New investigation…**.

The dialog asks for three things:

- **Name.** Use one that will still make sense in six months.
- **One-line description.** Optional, and you can change it later from the Overview. It is the first thing Watchdog knows about your investigation, which is useful when you have several open at once.
- **Where to keep it.** The default is the investigations folder you chose in setup. If you pick a different folder, Watchdog asks permission to work there; see [Folder access](app.md#folder-access).

Choose **Create investigation**. Watchdog makes a folder named after the investigation (for example `shell-company-investigation`) and sets up its structure, including the `incoming/` and `context/` folders, the starting notes (`hot.md`, `log.md`, `context.md`, `index.md`), and the settings for Claude. The [vault guide](vault.md) explains every folder and file.

The investigation opens on its **Overview**. It is empty until you add documents.

## Seed your context (optional but recommended)

Before adding records, it helps to tell Watchdog what you are investigating. This is especially useful for large or long-running investigations.

Put any background material in the investigation's `context/` folder: prior published stories, notes, screenshots of relevant web pages, anything that describes the scope. The fastest way to open the folder is **File → Show in Folder**. Then open **Ask Claude** and choose **Seed investigation context**.

Claude reads the material and interviews you — who the key people and companies are, what you are looking for, what documents you expect. It then writes `context.md`, an investigative brief that persists across every future conversation and tells Claude what you already know.

Two things to keep in mind about the brief. First, it is sent to the AI model with every document you add, and used for the briefing, so it leaves your computer just as the documents do: keep source names, contact details, unpublished tips and anything confidential out of it, and out of the `context/` folder. Second, write it as open questions rather than conclusions: "I want to understand how the Pier 9 contract was awarded" or "I want to explore who owns the numbered company", not "the councillor hid her conflict". The model reads every document with the brief in view, and a brief that states what you expect to find can lead it to read the documents that way. Claude drafts the brief in this style; you can edit it at any time under **Briefings**. It also proposes a short list of watch-list candidates (names, companies and addresses drawn from the same material) for you to accept, edit or skip. Anything you approve is added to the watch list right away.

This step is optional, but it noticeably improves the quality of summaries and of connection-finding. Once the brief exists, the Overview also shows a prompt to read any background folder not yet read.

## Add documents

You can add documents three ways:

- **Drag them onto the window.** Drop files or whole folders anywhere in the app, and Watchdog adds them to the open investigation.
- **Choose Add documents.** The button is at the top right, and also under **File → Add Documents…**. On an empty investigation, the Overview shows **Choose files…** and **Choose a folder…** buttons that do the same.
- **Put files in the `incoming/` folder** inside the investigation, using your file manager. Watchdog lists anything waiting there on the Overview and in the Add documents dialog.

Watchdog handles PDFs (scanned or not), Word documents, spreadsheets, images, web pages and plain text; see the [supported file types](vault.md#supported-file-types) for the full list.

**Rename files before adding them.** Watchdog uses the filename when labelling documents. `shell-co-annual-report-2023.pdf` is useful; `scan0042.pdf` is not.

**Add a sidecar file for provenance.** To record where a document came from, put a small `.yml` file with the same base name next to it, as in `shell-co-annual-report-2023.pdf` and `shell-co-annual-report-2023.yml`. It can contain a `source`, the date `obtained`, and `notes`. The [vault guide](vault.md#supported-file-types) covers the format in full.

**Duplicates are handled automatically.** A document that is byte-identical to one already added, even under a new name, is set aside in `incoming/skipped/` rather than processed twice. A separate check flags similar-but-not-identical files (a redlined revision, say) for your review, but never skips them.

## Confirm and watch the run

The Add documents dialog takes you through four steps.

1. **Choose.** Pick files or folders. The dialog also shows anything already waiting. **Estimate cost** shows what the run is likely to cost before you commit, and **Options** lets you change the models and other settings for this run only. The defaults are fine for a first run.
2. **Reading documents.** Watchdog copies the files into `incoming/` (your originals stay where they are) and converts them to text on your computer, using OCR for scans. Nothing is sent anywhere during this step.
3. **Before anything is sent.** This is the public-records confirmation. It shows the exact number of documents about to be sent and which model will receive them. Nothing goes until you choose **Acknowledge and add**. If you set Watchdog to skip this pause for runs that use only your Claude subscription, you see a short notice instead; see [Auto-approve](configuration.md#auto-approve).
4. **Adding.** Watchdog extracts entities, facts and timeline events from each document, writes everything to the investigation, and produces a **briefing**.

Large documents can take several minutes each to extract, so a long pause on one document is normal. You can choose **Hide** to close the dialog; the run continues, and its progress stays visible in the corner of the window. **Stop** ends the run cleanly, and adding the documents again picks up where it left off. **Activity** lists every run with its full output.

By default Watchdog uses Sonnet (Claude's mid-tier model) for extraction and Haiku (the fast, inexpensive tier) for classification and the wrap-up steps, so nothing beyond your Claude sign-in is needed. Benchmark testing against real court-and-financial filings found OpenAI's GPT-5.6 Luna the stronger choice for extraction (see [Benchmarks](benchmarks.md)); using it needs its own OpenAI key, which is why it is a recommendation rather than the default. The [configuration guide](configuration.md) covers changing models, tuning how much reasoning each stage spends, and controlling cost.

Three features run alongside every run; each is covered in the [investigating guide](investigating.md):

- **Watch list.** If you have listed terms in the watch list (a name, company, address or phrase) Watchdog scans every newly added document for them and records matches as watch-list hits. See [the watchlist](investigating.md#the-watchlist).
- **Leads.** At the end of each run, Watchdog sweeps the entity graph for things worth chasing — an entity named repeatedly but never profiled, for instance — and records them as leads. See [leads](investigating.md#leads).
- **Resolving.** Once you have dealt with a lead or a hit, you can mark it handled so it stops reappearing, which turns those lists into a shrinking to-do list. See [resolving items](investigating.md#resolving-items).

For a plain-English account of what each stage does to your documents, and what the AI model does and does not see, read [Methodology](methodology.md).

## Read the briefing

When the run finishes, the **Overview** shows the headline from the latest briefing, with a button to read the whole thing. The briefing summarizes what was found, how it connects to entities already in the investigation, and anything worth following up. Read it carefully: the connections section is often where the story is.

The Overview also shows what is waiting on you (contradictions, leads, possible duplicates, possible same entities and watch-list hits) and what is in progress, with each item linking to the screen that deals with it. Every briefing Watchdog writes is kept under **Briefings**.

## Explore the results

The sidebar lists the screens for the open investigation.

- **Documents** shows every document as a thumbnail or a list. Open one to read the original beside the facts taken from it; each fact's page number scrolls the original to that page.
- **Entities** lists every person, organization, place and so on, with the number of documents each appears in. An entity's page shows its summary, its relationships and the documents it appears in.
- **Timeline** lays out every dated event, with links to the page each came from.
- **Network** draws entities and their relationships as a graph. Entities that appear in many documents, or connect to many others, stand out.
- **Search** finds exact words, passages ranked by meaning, and notes. Press Ctrl+K (⌘K on a Mac) anywhere to jump to something by name.

If the same person or company shows up twice under different names, see [duplicate entities](investigating.md#duplicate-entities) for the fix. [The desktop app](app.md) explains every screen.

If you prefer to browse the files themselves, **File → Show in Folder** opens the investigation's folder, and **File → Open in Obsidian** opens it in [Obsidian](https://obsidian.md). The vault guide explains the [folders and notes](vault.md), including the `dashboard.base` file, a set of live tables that Obsidian displays.

## Review what needs a decision

Choose **Review** to work through contradictions, leads, watch-list hits, possible duplicates, possible same entities and document requests, one tab each. A count beside Review in the sidebar shows how many items are waiting. Mark an item handled and it stops appearing in briefings and on the Overview.

## Ask Claude

Choose **Ask Claude** and type a question, such as "Who are the directors of Shell Co Ltd?". Claude answers using only the documents in your investigation, and cites the source for every claim. You can follow up ("what else has she signed?") in the same conversation. Past conversations are listed on the left, and you can start a new one with **New conversation**.

Start a new conversation for a new line of inquiry rather than reusing one that has run long. At the start of each conversation, Claude reads the current-state summary (`hot.md`) automatically, so it knows where the investigation stands without re-reading everything, and a fresh conversation has the most working room for your questions.

If Claude needs to do something your settings do not already allow, Watchdog asks you first.

## Where next

The [investigating guide](investigating.md) covers everything you do from here — asking questions, searching, finding connections, researching on the web, and running the investigation day to day. [The desktop app](app.md) describes each screen, and [Troubleshooting](troubleshooting.md) covers what to do if a run does not go as expected.
