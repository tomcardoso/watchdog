# Watchdog

**Document intelligence for investigative journalists — drop records in, find the connections.**

[![PyPI](https://img.shields.io/pypi/v/watchdog-intel)](https://pypi.org/project/watchdog-intel/) [![CI](https://github.com/tomcardoso/watchdog/actions/workflows/ci.yml/badge.svg)](https://github.com/tomcardoso/watchdog/actions/workflows/ci.yml)

Watchdog is a command-line tool for journalists who accumulate large sets of public records — court filings, corporate records, freedom-of-information responses, land registries. You drop documents into a folder. Watchdog reads every page, pulls out every person, company, address and relationship it finds, and builds them into a linked investigation vault you can search and question in plain English. Every extracted fact cites the document and page it came from.

The vault lives in [Obsidian](https://obsidian.md), a free note-taking app, as ordinary files on your computer. The questions run in [Claude Code](https://claude.ai/download), Anthropic's AI assistant for the terminal.

> **Alpha.** The core pipeline works and has been tested on macOS with real investigation documents. It is not yet battle-hardened. Feedback and contributions are welcome.

## Public records only

Watchdog is careful with your files. The originals never leave your computer, and all document conversion runs locally. But the extracted text of each document is sent to a cloud AI model for analysis, and there is no way to take that back. So Watchdog is only for documents that are public, or presumptively public.

Never use it with confidential source communications, leaked or unpublished material, private correspondence, or anything that could identify a source. If you are unsure whether a document is safe to process, do not process it.

## What it does

- **Reads almost anything.** PDFs (scanned or not), Word documents, spreadsheets, images, web pages, audio and video. Scanned documents are OCR'd automatically; a 400-page PDF is no problem.
- **Extracts entities, not just text.** People, companies, addresses, relationships and dates become linked notes, with a page-level citation on every fact.
- **Builds a timeline.** Datable events from every document are assembled into one chronological view of the investigation.
- **Surfaces what you might miss.** Shared addresses, overlapping directors, an entity that keeps turning up, a new document that contradicts an old one. Contradictions are flagged — they are often stories in themselves.
- **Applies specialist knowledge.** Built-in guides for dozens of document types teach it what an experienced investigative journalist looks for in corporate filings, court records, land registries and more.
- **Leaves you in charge.** The vault is plain files you own and annotate. Facts the AI inferred rather than read are marked as such, and everything links back to the source page for verification.

## How it works

A few steps, all run from your terminal:

```
drop files into _INCOMING/
        ↓
watchdog          reads, OCRs and converts each document, sends the extracted
                  text to the AI model to pull out entities, facts and timeline
                  events, then writes everything to the vault and produces a
                  briefing — one command, confirming before each step
        ↓
your vault        linked notes in Obsidian; ask questions in Claude Code
```

After ingest, you read the briefing, explore the vault in Obsidian, and ask questions inside Claude Code — `/watchdog-query Who are the directors of Shell Co Ltd?` — with every answer cited back to a page.

## What you need

- macOS, Linux or Windows
- [Obsidian](https://obsidian.md) — free
- [Claude Code](https://claude.ai/download) — free to install, and required
- Claude access — a Claude.ai Pro or Max subscription, or an Anthropic API key
- Python 3.10+, plus a few free system tools the [install guide](https://github.com/tomcardoso/watchdog/blob/main/docs/install.md) covers

A Pro subscription (US$20/month) is enough for most journalism work.

## Installation

```bash
pipx install watchdog-intel
watchdog setup
```

Prefer [uv](https://docs.astral.sh/uv/)? Use `uv tool install watchdog-intel` instead of the first line.

Never used a terminal? The [install guide](https://github.com/tomcardoso/watchdog/blob/main/docs/install.md) walks through every step, starting from how to open one.

## Quick start

```bash
watchdog new "Shell Company Investigation"
cd ~/Investigations/shell-company-investigation

# drop documents into _INCOMING/, then:
watchdog
watchdog obsidian
```

For a full first-investigation walkthrough, see [Getting started](https://github.com/tomcardoso/watchdog/blob/main/docs/getting-started.md).

## Documentation

| Guide | What it covers |
|-------|----------------|
| [Install](https://github.com/tomcardoso/watchdog/blob/main/docs/install.md) | Getting Watchdog set up, written for first-time terminal users |
| [Getting started](https://github.com/tomcardoso/watchdog/blob/main/docs/getting-started.md) | Your first investigation, start to finish |
| [Investigating](https://github.com/tomcardoso/watchdog/blob/main/docs/investigating.md) | Day-to-day work: questions, search, leads, web research |
| [Commands](https://github.com/tomcardoso/watchdog/blob/main/docs/commands.md) | The complete command reference |
| [Configuration](https://github.com/tomcardoso/watchdog/blob/main/docs/configuration.md) | Every setting, model choices, controlling cost |
| [Benchmarks](https://github.com/tomcardoso/watchdog/blob/main/docs/benchmarks.md) | How model/effort defaults are measured, and what to expect in time and cost |
| [The vault](https://github.com/tomcardoso/watchdog/blob/main/docs/vault.md) | What Watchdog builds on disk and how to read it |
| [Domain skills](https://github.com/tomcardoso/watchdog/blob/main/docs/skills.md) | The built-in document-type expertise |
| [Troubleshooting](https://github.com/tomcardoso/watchdog/blob/main/docs/troubleshooting.md) | When something goes wrong |

## A note on AI and mistakes

Watchdog uses AI to read documents, and AI makes mistakes — it can misread a name or draw a wrong inference. Every fact it records links to the source document and page, and facts it inferred rather than read are marked *(inferred)* — leads to verify, not findings. Treat the vault as a structured first read, not a finished product, and follow the link before you publish anything.

## Contributing

Three areas where help is most welcome:

- **Domain skills** — if you know a document type deeply, the extraction guides are plain markdown, no code required. Start from [the template](src/watchdog/skills/records/_template.md).
- **Pipeline fixes** — bug reports with a sample document (redacted if needed) are especially useful.
- **Documentation** — corrections, clarifications and translations, particularly to the install guide.

To run from source:

```bash
git clone https://github.com/tomcardoso/watchdog
cd watchdog
pipx install --editable . --force
watchdog setup
```

Please open an issue before starting significant work.

## Acknowledgements

The vault structure and session-context approach were partly inspired by [claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian) by Daniel Agrici. Search is built on [fastembed](https://github.com/qdrant/fastembed) by Qdrant; the passage-window approach, `+`/`-` queries and show-the-source principle are borrowed from [Semantra](https://github.com/freedmand/semantra) by Dylan Freedman. Embedding the raw corpus separately from the knowledge graph was partly informed by [obsidian-smart-connections](https://github.com/brianpetro/obsidian-smart-connections) by Brian Petro, and the structured vault index for entity lookup by [obsidian-claude-code](https://github.com/Roasbeef/obsidian-claude-code). The ASCII dogs shown by `watchdog new` and `watchdog about` were drawn by Felix Lee and Sarah Kearsley.

## License

MIT — see [LICENSE](LICENSE).
