# corpus-v1 — the benchmark corpus for #361 / #215

Frozen 2026-07-13. **Do not edit, re-save, or re-export any file listed in `corpus-v1.sha256`.**
Document identity in the pipeline is the sha256 — changing a single byte silently makes two runs
incomparable, with nothing to warn you. Verify before every run:

```
cd tests/documents && shasum -a 256 -c corpus-v1.sha256
```

These PDFs are committed to git, so the corpus is version-controlled as well as hashed.

## What this corpus is for

One set of documents, re-ingested under every condition this benchmark runs, to settle three
decisions:

- **#361** — should the shipped Claude default for extraction stay `sonnet` or drop to `haiku`?
- **#361** — is DeepSeek good enough to *document* as the cost-saving route for large ingests?
- **#215** — should the default reasoning effort stay `high` or drop to `medium`?

#217 (`classify_pages`) and #297 (briefing from digests) ride along on the same runs.

## The six documents

| # | File | Pages | Text layer | Role in the benchmark |
|---|------|-------|-----------|----------------------|
| 1 | `Annual-Financial-Report-19-20.pdf` | 36 | born-digital | **Contradiction side A** |
| 2 | `Laurentian Pre-Filing Report of the Proposed Monitor.pdf` | 47 | born-digital | **Contradiction side B** |
| 3 | `Annual-Financial-Report-20-21.pdf` | 70 | born-digital | **The dense 50+ page document** — sectioning, page coverage, output ceiling |
| 4 | `CV-21-00656040-00CL Laurentian U Initial Order 1 FEB 2021.pdf` | 17 | **scanned — OCR** | The OCR arm; a clean-PDF corpus flatters every model |
| 5 | `Laurentian First Report of the Monitor.pdf` | 34 | born-digital | Entity overlap — E&Y, the Board, the DIP facility |
| 6 | `Pension Order Morawetz CJ- March 17 2021(as stamped by Court).PDF` | 5 | born-digital | A short, sharp document for contrast |

**209 pages.** All are public court filings or published institutional financial reports — nothing
source-sensitive, which matters because these get shipped to several model providers, repeatedly.

## The contradiction pair (the thing being scored)

Documents 1 and 2. **Neither document mentions the other**, so a model has to *notice* the conflict
rather than read it off the page — which is the whole point. Both sides are explicit, positive
claims, so the extractor can actually fire on it (a contradiction needs two stated claims; it
cannot fire on silence).

**Side A — Annual Financial Report, FY ended 30 April 2020** (published late 2020):

> "The fiscal 2019-20 approved budget expected **balanced results from operations**… the University
> was **on track** to operating fiscal year 2019-20 with an anticipated **small deficit of $0.9
> million** from operations." *(p. 4)*
>
> "The net impact was an operating deficit of **$5.4 million of which $5.2 million is related to the
> COVID-19 outbreak**." *(p. 4)*
>
> "Laurentian's commitment… is unwavering as it **continues to trace its path to sustainability**."
> *(pp. 3, 10)*
>
> "The University has **$52.8 million in endowment, an increase of $1 million** over 2018-19." *(p. 8)*

**Side B — Pre-Filing Report of the Proposed Monitor (Ernst & Young)**, weeks later:

> "**LU is insolvent** and absent the relief sought in the Initial Order, will not have sufficient
> funding / liquidity to **meet payroll in February**." *(p. 31, ¶165)*

A university claiming a $0.9M planned deficit, blaming COVID, and tracing a "path to sustainability"
— then unable to make payroll within months.

## Deliberately excluded

**The Auditor General of Ontario's Special Report on Laurentian University is NOT in this corpus, by
design.** It *states the contradiction out loud* ("presented budget deficits year over year, while
publicly saying it was presenting balanced budgets"). Including it would let a model copy the answer
instead of deriving it — you would be scoring reading comprehension, not the contradiction machinery.

Use it instead as the **answer-key oracle**: an authoritative, page-cited account of what was
actually true, which is what makes model-drafted keys trustworthy here. It corroborates, among other
things, that deficits dated to 2014 (not COVID), that $73M in donations were not segregated, and that
$36.5M in restricted research money had been spent on capital.
<https://www.auditor.on.ca/en/content/specialreports/specialreports/LaurentianUniversity_EN.pdf>

Also considered and left out of corpus-v1: the Second and Third Reports of the Monitor, the Claims
Process Order, and the Appointment of Mediator Order — all public filings in the same CCAA
proceeding, no longer kept in this folder now that the corpus is settled.

## Provenance

All documents relate to **Laurentian University's CCAA insolvency** (Ontario Superior Court of
Justice, Commercial List, **court file CV-21-00656040-00CL**), filed 1 February 2021 — the first
public university in Canada to use the *Companies' Creditors Arrangement Act*.

- **Annual financial reports** — published by Laurentian University (`laurentian.ca`), audited
  consolidated statements plus management discussion.
- **Monitor's reports and the Pre-Filing Report** — Ernst & Young Inc., court-appointed monitor;
  filed with the court and published to the monitor's public document repository.
- **Court orders** — issued by the Ontario Superior Court of Justice (Commercial List); the Initial
  Order is a court-stamped scan, which is why it carries no text layer.

## Answer keys

Keys live in `keys/` (one YAML per document), drafted from the **source documents** — never from a
pipeline extraction — and **frozen before any condition runs**. See #361 for why model-drafted keys
are legitimate for this benchmark and what their limits are.

The `must_not_miss` list for document 3 (the 70-page FY2020-21 report) gets a **dedicated adversarial
pass** whose only job is hunting buried items — walk the schedules, footnotes, signature blocks, and
appendices. Run that pass on **two model families (Opus and Gemini) and take the union**. The risk
here is *correlation*, not capability: a list drafted by Opus alone omits whatever Opus misses, and
Opus's blind spots correlate with Sonnet's and Haiku's — so Haiku would "pass" on a schedule nobody
ever wrote down. Independence fixes that; human labour doesn't.

A human then **reviews** the union — that is where a journalist's judgement earns its keep (is this
buried item material, or trivia?), and it is the quality gate for the whole benchmark: a frozen key
with a bad fact in it silently corrupts every condition scored against it at once, and nothing
downstream catches it.
