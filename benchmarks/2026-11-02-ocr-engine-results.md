# OCR engines on macOS — results (2026-11-02, #818)

Every page of corpus-v1 (6 documents, 209 pages) read on one Mac by five readings, each in its own
process, through Watchdog's own pre-processing (`preprocess.process_with_docling`, OCR forced, at
the scale Watchdog picks for the page, D364):

| Reading | What it is |
|---|---|
| `vision` | Apple Vision as shipped (`ocrmac`, language correction on) |
| `vision-nocorr` | Apple Vision with language correction off (`vision_ocr_compare.patch_ocrmac`) |
| `rapidocr` | RapidOCR, `auto`'s engine outside macOS (D373), with the PP-OCRv6 small models Watchdog ships |
| `tesseract` | Tesseract 5.5.2, English data, through its command-line program (D379) |
| `easyocr` | EasyOCR 1.7.2, Docling's default languages; installed separately for this run, never shipped |

Code: 1.0.15 plus D379, run with the installed app's engine Python (docling 2.138.0, ocrmac 1.0.1,
rapidocr 3.10.0). Each page is converted on its own, so pages cannot be misfiled into one another;
#816 (D377) is a pre-processing question, not an engine one.

To run it again: `benchmarks/ocr_engine_compare.py run`, then `score` (its docstring has the
commands). The scanned-page reference is `ocr-reference/corpus-v1-scanned.json`; how it was made
is below and in `ocr_review_lines.py`.

## Read this first

- **One Mac, one run.** Apple Vision's chew of the corpus came out identical twice (D372's
  manifest matched); the other engines were not run twice. Timings are one machine's and include
  loading each engine's models once per page.
- **Born-digital pages are a proxy.** Watchdog reads a born-digital page's text layer and never
  OCRs it. Forcing OCR on those pages gives an exact reference for free (the page's own text), and
  measures each engine on clean, typeset pages, close to a screen capture or a clean scan. It is the
  larger sample (42,863 words). The scanned pages are the real case but a small one (7,411 words,
  89 identifiers).
- **The scanned identifiers cluster.** 89 identifiers on 32 pages, and the misses below land
  mostly on two backsheets that print the same counsel block (names, licence numbers, a postal
  code). Vision's 8 misses are 6 distinct strings. Read the identifier column as a lead.
- **English only.** The corpus has no French pages, so RapidOCR's handling of accents (the D373
  follow-up) is still unmeasured.

## Born-digital pages — 128 pages, 42,863 words

Each engine's reading of a picture of the page, against the page's own text layer.

| Engine | Text lost | WER | Identifiers exact (2,857) | Dropped passages | s/page |
|---|---|---|---|---|---|
| Vision, as shipped | **0.93%** | 1.74% | 99.89% | 3 | 1.4 |
| Vision, correction off | 1.07% | 1.92% | **99.93%** | 3 | 1.3 |
| Tesseract | 1.15% | 2.22% | 99.65% | 3 | 2.6 |
| RapidOCR | 2.70% | 3.82% | 99.44% | 18 | 2.8 |
| EasyOCR | 5.38% | 7.18% | 95.66% | 56 | 11.8 |

- *Text lost*: the share of the page's words an engine misread or missed. WER also counts words an
  engine adds, which on these pages are mostly correct reads of text inside images the text layer
  does not hold, so *text lost* is the fairer figure.
- *Identifiers*: words of three characters or more holding a digit or an @ (amounts, dates, file,
  licence and postal numbers, emails), matched exactly, spacing aside.
- *Dropped passage*: eight or more of the page's words in a row missing from the reading.
- Curly and straight quotes count as the same character.

**49 pages were set aside**, all in the 2020–21 annual report from page 10 on: their text layer
holds text that is not printed (headings twice over, "KPMG" repeated many times on the
auditor's page). There, every engine agrees with every other and none with the text layer, so the
text layer is the wrong reference. A page is set aside when the engine nearest its text layer is
more than 10% from it and over three times as far as the engines are from each other. It also
means Watchdog's model reads that duplicate text for those pages today.

## Scanned pages — 32 pages, 7,411 words, 89 identifiers

The pages with no text layer: the whole Initial Order, the first monitor's report's schedule and
backsheet, and a few covers and stamp pages. The reference was made line by line: each printed line
the three independent engines (Vision as shipped, RapidOCR, Tesseract) read alike was taken as
read (622 lines); every other line (142) was settled by a person from an enlarged crop of the line.
Vision without its correction does not vote, since it would vote twice with Vision. Scored as a
bag of words, so the order in which an engine reads a page's lines does not count.

| Engine | Text lost | Identifiers exact (89) | Dropped lines | s/page |
|---|---|---|---|---|
| Vision, as shipped | **1.12%** | 91.0% (8 missed) | 0 | 1.5 |
| Vision, correction off | 1.21% | 92.1% (7) | 0 | 1.5 |
| Tesseract | 1.15% | 95.5% (4) | 0 | 2.4 |
| RapidOCR | 2.17% | **100%** (0) | 0 | 2.5 |
| EasyOCR | 7.97% | 61.8% (34) | 18 | 11.0 |

A line that three engines all misread the same way is in the reference as read; such a line
flatters every engine alike and does not change the ranking.

## What the numbers say

- **Apple Vision reads the most words correctly**, on clean pages and on scans. Its weakness is
  letters and digits that look alike, in identifiers on scans: `1ST` as `IST`, `5TH` as `STH`,
  `M5K` as `MSK`, `69993I` as `699931`, `-00CL` as `-O0CL`, and a stamped year.
- **Its language correction is not the cause.** With correction off, Vision fixes one of those
  eight and loses slightly more words on both sets. Turning it off is not supported by this data.
- **RapidOCR gets identifiers right on scans but loses twice the words**, and on clean pages it
  drops whole printed lines with no sign of a gap: 18 passages, including the amount
  `$842,018.34` and lines such as "On or about June 25 2020 LU requested and RBC". RapidOCR has
  been `auto`'s engine on every non-Mac computer since D373, so this matters beyond the Mac.
- **Tesseract is the most even**: within a hair of Vision on words, better than Vision on scanned
  identifiers (it shares two of Vision's misreadings), three dropped passages on clean pages (one a
  table of bank maturities). It is not shipped; it needs installing (D379).
- **EasyOCR is not a candidate.** It loses five to seven times Vision's text, misses dollar
  figures in particular, drops 56 passages on clean pages, and takes 11–12 seconds a page.

## Found along the way

- **An identifier broken at a line end loses its hyphen.** "Reg. 81000-2-" at the end of a line
  and "175" on the next come out as `81000-2175` in every engine's page text, because Docling
  joins a hyphenated line break when it assembles lines into paragraphs. The scanned-page
  identifier score leaves such fragments out; the real number is 81000-2-175.
- **The `tesseract` and `easyocr` settings never used those engines in the app** (fixed in D379).

## Open

- The default for Macs: the data favour keeping Apple Vision; whether its identifier misreads on
  scans justify another choice is the owner's call (#818).
- ~~Why RapidOCR drops lines on clean pages~~: answered below (D382).
- Accents: no French pages yet.
- PaddleOCR's larger models (RapidOCR loading PP-OCR's server-size models, run locally) were not
  tried.

## RapidOCR, one orientation per page (2026-11-02, D382)

**Why RapidOCR dropped lines.** Its line-direction classifier (PP-OCRv4 mobile, on by default)
judges each detected line from a 48×192 pixel squeeze of it and turns it 180° when it reads
"upside down" at over 0.9. On some long upright lines it did, and the recogniser read the turned
line as junk that still cleared `text_score` (0.5): page 3 of the pension order came out
`ossssss  sd  d    s   0        n` where the page prints "on March 30, 2021 in the amount of
$842,018.34 in respect of". The lines were detected; they were lost at recognition, and the same
false turns cost words on four pages of the scanned Initial Order. Docling's assembly was not
involved.

**The fix.** The classifier's verdicts are averaged over a page, wide and tall boxes apart, and a
group's lines are turned together or not at all (`preprocess._orient_rapidocr_by_page`). Re-run
with `ocr_engine_compare.py run --engines rapidocr` on the committed code, other engines' readings
as above:

| RapidOCR | Clean: text lost | Clean: identifiers | Clean: passages truly lost / only reordered | Scans: text lost | Scans: identifiers | Sideways backsheet (Initial Order p17): words lost of 100 |
|---|---|---|---|---|---|---|
| Before (line by line) | 2.70% | 99.44% | 11 / 7 | 2.17% | 100% | 0 |
| Classifier off | 1.34% | 99.96% | 2 / 6 | 2.67% | 80.9% | 97 |
| `cls_thresh` 0.99 | 1.43% | 99.86% | 2 / 6 | 1.78% | 97.75% | 31 |
| **One orientation per page** | **1.34%** | **99.96%** | **2 / 6** | **1.36%** | **100%** | **0** |
| Vision, for comparison | 0.93% | 99.89% | 2 / 1 | 1.12% | 91.0% | 5 |
| Tesseract, for comparison | 1.15% | 99.65% | 1 / 2 | 1.15% | 95.5% | 2 |

- *Passages truly lost*: dropped passages (as above) of which under 80% of the words appear
  anywhere on the page. The rest are only reordered: the words are there, in another reading
  order. The 18 dropped passages above were 11 lost and 7 reordered.
- The classifier-off and 0.99 rows were read with Docling's `RapidOcrOptions` (`use_cls=False`)
  and `rapidocr_params` (`Cls.cls_thresh`) on the same code, not committed.
- 2.9 seconds a page, against 2.8 before.

On the sideways backsheet the 27 tall lines average 99% "upside down" and all turn; on the
pension order page the 30 wide lines average 18% and none does. The sideways case is one page;
a single genuinely upside-down line on an upright page is now read upright and lost.
