# Drafting prompt — proposing answer-key entries for a new document

This is the reusable prompt for the first stage of adding a document to the extraction
benchmark, or for widening the coverage of a document already in it. Give it to one agent
per document, with the placeholders filled in.

**Why a prompt and not a habit:** the keys are the fixed reference every benchmark arm is
scored against, so how they are drafted has to stay constant across documents and across
whoever (or whatever) drafts them. A key drafted to a different standard than its
neighbours makes cross-document comparisons meaningless.

## Where this sits

1. **Brief.** Generate an "already covered" brief for the document — every entry the key
   already has, plus which pages currently carry no key item. The drafting agent gets this
   so it never re-proposes what is already scored. For a brand-new document the brief is
   just the document metadata and an empty list.
2. **Draft (this prompt).** One agent reads the **source PDF** — never the chewed markdown,
   never a pipeline extraction, per "How they were drafted" in `README.md` — and proposes
   candidates, each carrying verbatim text copied off the page.
3. **Align.** A deterministic pass locates each verbatim span in the frozen chew and
   rewrites it to match how the chew renders it, so the scorer can match it. Candidates
   whose text cannot be located are dropped before anyone reads them. This is why the
   `pdf_text` rules below are strict.
4. **Dedup.** Candidates that restate an existing key entry are dropped.
5. **Review.** A human reads what survives and decides what goes into the key.

Stage 1 is judgement, stage 3 is arithmetic. Keeping them apart is deliberate: it makes
PDF-versus-chew divergence a measured ratio rather than something a model decides it saw.

## Placeholders

| Placeholder | Meaning |
|---|---|
| `{SLUG}` | The key's filename stem, e.g. `first-report-monitor` |
| `{PDF}` | Absolute path to the source PDF |
| `{PAGES}` | Page count |
| `{BRIEF}` | Absolute path to the already-covered brief |
| `{OUT}` | Absolute path the agent writes its YAML to |

Sonnet is sufficient for this stage and is what the corpus-v1 pass used.

---

## The prompt

> # Stage A — propose answer-key entries for one document
>
> You are extending a benchmark **answer key** for an investigative-journalism tool. The
> key is the ground truth a scorer grades machine extractions against. A key that covers
> only part of a document means extractions that correctly surface material from the rest
> score zero for it. Your job is to close that gap for **one document**.
>
> ## Your inputs
>
> - **Source PDF:** `{PDF}` ({PAGES} pages)
> - **Already-covered brief:** `{BRIEF}` — read this FIRST. It lists every entry the key
>   already has. Do not re-propose any of them.
>
> Read the PDF with the Read tool's `pages` parameter (max 20 pages per call). **Read every
> page, in order, front to back** — including schedules, exhibits, appendices, signature
> pages and notes to the financial statements. The brief flags pages with no key item yet,
> but a page that already carries one item can be hiding three more.
>
> ## What counts as material
>
> Write down anything a working reporter would want, whether or not anyone told you what
> the story is. Concretely:
>
> - money — amounts, deficits, forecasts, salaries, fees, debts, transfers between funds
> - dates and deadlines — filing dates, stays, court return dates, maturity dates
> - named people and organizations, and what each one did or is owed
> - decisions, orders, and legal consequences — what is stayed, permitted, prohibited
> - causes and reasons the document itself gives for the situation
> - numbers that reveal scale — headcount, enrolment, program counts, square footage
> - anything a document of this type would normally state and this one conspicuously does not
>
> **Err heavily toward too many.** A human reviews every proposal afterward and cutting one
> is cheap; a fact you skip is invisible and never comes back. If you catch yourself
> thinking "this is probably minor," propose it anyway. There is **no target number** —
> not the count the key already has, not any number you might infer from the brief.
> Do not stop early because a page felt routine; boilerplate paragraphs in a court order
> routinely carry the operative legal facts.
>
> ## Output
>
> Write a YAML file to `{OUT}`. Nothing else — no commentary in the file.
>
> ```yaml
> document: {SLUG}
> pages_read: "1-{PAGES}"
> candidates:
>   - id: C1
>     kind: fact              # `fact` normally; `must_not_miss` if a reporter who
>                             # missed this would have got the story wrong
>     page: 16                # the PDF page number you read it on, 1-based
>     location: "para 5(a), Terms of Reference"   # human locator within the page
>     claim: >
>       One or two sentences, in your own words, stating the fact plainly.
>     pdf_text:
>       - "first verbatim span exactly as printed"
>       - "second verbatim span exactly as printed"
>     why: >
>       One or two sentences on why a reporter needs this.
> ```
>
> ### The `pdf_text` rules — read these twice
>
> `pdf_text` is a **YAML list of discrete verbatim spans**. A later automated stage locates
> each span in a machine conversion of the PDF and rewrites it to match that conversion, so
> a span that isn't literal text will simply fail to locate and your candidate gets dropped
> before anyone reads it.
>
> 1. **Every span is a literal, contiguous run of characters from the page.** Copy it
>    exactly: same spelling, same capitalization, same punctuation, same figures. Do not
>    fix typos, do not expand abbreviations, do not normalize whitespace inside a phrase.
> 2. **Never put `...` or `…` inside a span.** If you want to skip material, that is two
>    spans — end one, start the next. This is the single most important rule here.
> 3. **Never add anything of your own** — no `[week 13 column]`, no `[emphasis added]`, no
>    `(sic)`, no explanatory parentheses. Anything you write that is not on the page
>    corrupts the alignment.
> 4. The **only** permitted non-document text is a page hint, `[p12]`, immediately before a
>    span that is on a different page from the entry's `page` field. Use it when a claim
>    straddles a page break.
> 5. **Table claims:** give the row label and the figure as **separate spans** —
>    `["Total Receipts", "29,514"]`. The conversion scatters a table row across many
>    columns, so a single span joining them can never match.
> 6. Prefer several short exact spans over one long approximate one. Aim for at least
>    12 characters per prose span; a bare figure is fine as a second-or-later span in a
>    table claim.
>
> ### If the page looks wrong
>
> The PDF may be scanned or badly typeset. Trust what you can see on the page image. If a
> figure is genuinely illegible, say so in `why` and quote what you can read rather than
> guessing.
>
> ## When you're done
>
> Reply with: the document slug, the number of candidates, the page range you read, and
> any page you deliberately skipped and why. Do not summarize the candidates themselves.

---

## Why the rules are shaped this way

Each of these cost real candidates on the corpus-v1 pass and is here so the next pass
doesn't pay again.

- **`pdf_text` as a list, not one string.** The first pass let the agent join spans with
  `...`, and it used the same `...` both as a table-column separator and inside quoted
  prose. Nothing downstream can tell those apart, so the alignment stage had to guess.
- **No bracket asides.** `[week 13 / Total column]` is the agent saying where it looked. It
  is not document text, and leaving it in the span guarantees the span never matches.
- **Table rows split into separate spans.** Docling scatters a table row across many
  markdown columns and, on dense financial tables, merges adjacent rows outright. A span
  that joins a label to its figure has no contiguous counterpart in the chew — which would
  drop the entire cash flow forecast, the most newsworthy table in a CCAA filing.
- **No numeric target.** Any number stated to the agent becomes a stopping point. The
  reviewer can cut; the agent cannot recover what it never wrote down.
