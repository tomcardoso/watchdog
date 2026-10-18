# {name}

An investigation built with **[Watchdog](https://github.com/tomcardoso/watchdog)** — document intelligence for investigative journalism. Open it in the Watchdog app to add documents, read the briefings and ask questions. Every note here is ordinary Markdown, so you can also browse the folder in [Obsidian](https://obsidian.md).

> Created with Watchdog `v{version}` · [GitHub](https://github.com/tomcardoso/watchdog) · [Report an issue](https://github.com/tomcardoso/watchdog/issues)

## ⚠️ Public records only

Never add confidential source material, leaked documents, private correspondence, or anything obtained under a promise of confidentiality. **Every document in here is read by an AI** — there is no taking that back.

## How to use it

1. Open the investigation in the Watchdog app and choose **Add documents**, or drop files into `incoming/` and add them from the app.
2. Watchdog pre-processes each file on this computer, processes it with an AI model to pull out people, organizations, places and dates, then post-processes the batch: merging entities, flagging contradictions and writing a briefing.
3. Read the briefing, explore the people and connections, and use **Ask Claude** in the app to ask questions across the whole investigation.

## What's in here

| Path | Purpose |
|------|---------|
| `incoming/` | Drop zone for new documents |
| `context/` | Background material (prior stories, notes) that seeds the investigation |
| `entities/` | One note per person, company, address… |
| `documents/` | One note per added document |
| `briefings/` | A summary of what was found each time documents are added |
| `timeline.md` | Chronology across the whole investigation |
| `morgue/` | Your original files, kept once processed |
| `.watchdog/` | Internal state — leave it alone |
