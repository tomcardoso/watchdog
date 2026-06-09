# Documentation site brief — Watchdog

## What this is

Watchdog is a command-line tool for investigative journalists who accumulate large sets of public records. You install it once, point it at a folder, and it reads every document — PDFs, Word files, court filings, spreadsheets — extracts every person, company, address, and relationship it finds, and stores them as linked notes in an Obsidian vault. When you ask it questions in plain English, it answers using only the documents in front of it and cites the exact page.

It runs entirely on your computer. Documents never leave your machine.

## Audience

**Primary:** Investigative reporters and data journalists who already work with large document sets — FOIA dumps, leaked files, court record collections, public registry data. They are technically competent (comfortable in a terminal, familiar with command-line tools) but not software developers. Many already use Obsidian.

**Secondary:** Journalism educators and newsroom data teams evaluating tools for reporters.

**Not the audience:** General public. Casual users. Developers looking for an API.

The tone should match the audience: serious, precise, professional. Not startup-y. No exclamation marks. No "supercharge your workflow." Think Reuters style guide, not Product Hunt launch copy.

## The core problem it solves

Investigative reporters drown in documents. A FOIA request returns 40,000 pages. A court case has 800 filings. A corporate investigation spans five jurisdictions and a dozen registries. Reading everything is impossible; missing a connection is the story you don't write.

Watchdog doesn't replace the journalist's judgment — it handles the mechanical reading so the journalist can focus on the connections that matter.

## Key messages (in order of importance)

1. **Your documents stay on your computer.** Privacy and security are paramount for investigative journalists. This is the first objection to address.
2. **It extracts entities and connections, not just text.** The output is a linked knowledge graph — people, companies, addresses, and relationships — not a search index.
3. **It cites its sources.** Every extracted fact links to the source document and page number. Nothing is asserted without evidence.
4. **It applies specialist knowledge.** 34 domain skills encode what an experienced investigative journalist looks for in corporate filings, court documents, land registries, etc.
5. **It works with what you already have.** Obsidian for reading the vault. Claude Code for the AI. No new subscriptions beyond Claude.

## Pages needed

### Home
- What it is (one clear paragraph)
- The core value proposition (private, local, cites sources)
- Short demo or screenshot showing the vault
- Install command
- Link to full install guide

### How it works
- The pipeline: drop files in → preprocess → extract → vault
- What "extraction" means concretely (entities, relationships, timeline)
- The domain knowledge skills — what they are and why they matter
- The Obsidian vault structure (what gets created)

### Installing
- This can link to or embed `INSTALL.md`, which is already written for non-technical users
- Prerequisites, step-by-step, troubleshooting

### Skills reference
- The 34 domain knowledge skills, organized by category
- Each links to the actual skill file in the repo
- Brief description of what each covers

### Contributing
- How to add a new domain skill (plain markdown, use the template)
- How to report bugs
- Link to GitHub issues

## Design direction

- **Clean and readable** — long-form text is part of the product; make it easy to read
- **Dark/neutral palette** — consistent with the terminal-tool aesthetic; journalists work at night
- **No stock photography** — especially no generic "data" imagery (circuit boards, glowing networks). Screenshots of the actual tool are fine.
- **Monospace for code and commands** — these need to be clearly distinguished
- **No hero animations or scroll-jacking** — this is documentation, not a landing page

## Technical constraints

- GitHub Pages (static site, no server-side rendering)
- Should work well as a Jekyll or similar static site generator that GitHub Pages supports natively, OR as a simple set of HTML/CSS files
- The existing repo is at `github.com/tomcardoso/watchdog` — the docs site should live at `tomcardoso.github.io/watchdog` or a custom domain if one is configured later

## What already exists

- `README.md` — comprehensive developer/contributor overview; not the right tone for the docs site but contains good factual content to draw from
- `INSTALL.md` — step-by-step install guide written for non-technical journalists; this content is ready to use nearly as-is
- `src/watchdog/skills/records/` — all 34 skill files; the skills reference page can be generated from these
- `CLAUDE.md` — developer notes; not for the docs site

## What success looks like

A journalist who has never heard of Watchdog lands on the home page, understands what it does and whether it's for them within 30 seconds, can find the install instructions without clicking more than once, and feels confident that their documents will stay private.
