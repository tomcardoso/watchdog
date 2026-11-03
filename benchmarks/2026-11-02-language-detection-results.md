# Language detection on French pages read with English OCR settings (#831, D382)

Measured 2026-11-02 on one Mac (macOS 27, Apple silicon), with the app's engine (Docling 2.138,
ocrmac 1.0.1, RapidOCR 3.10.0 with its PP-OCRv6 small models) and Homebrew Tesseract with
English data only. No French scans exist in any corpus, so the pages are made: seven original
French passages (a court order, a financial report, a letter, council minutes, an affidavit, a
table of figures, a bilingual filing stamp), rendered at 300 DPI in Times and Arial, clean and
scan-like (rotated, blurred, reduced to 200 DPI, speckled, thresholded), plus seven harsher
scans at about 130 DPI. Scripts are in `language-detection/`; network access was blocked while
they ran, so nothing was downloaded.

**These are synthetic pages. Real French scans are still needed to confirm every figure here.**

## Detection

`py3langid` on each reading, with the pipeline's rules (30 words, 0.9 probability):

| Reading | French pages named French |
|---|---|
| Apple Vision, English only | 24 of 24 (probability 1.00 on each) |
| Apple Vision, French and English | 24 of 24 |
| RapidOCR | 24 of 24 |
| Tesseract, English data | 24 of 24 |
| Apple Vision, harsh scans | 5 of 5 prose pages; the harsh table came back empty |

The four bilingual stamps (9 words) were not judged, as intended: they take the document's
majority.

False positives, on English: the six corpus-v1 documents (209 pages) and eleven documents of a
live investigation, 214 judged pages in all. One page was named French, and it is: the French
notice inside a court filing. No document was judged French or mixed.

## Does reading again in French change the text?

- **Apple Vision: no, on these pages.** Its output was byte-identical with no language, English
  only, and French plus English, on all 35 pages, harsh scans included. A German and a Russian
  line read the same way whatever the setting; a Chinese line read nothing at all unless
  `zh-Hans` was set. So on this macOS, the language setting matters for scripts like Chinese,
  not for Latin or Cyrillic text. Older macOS versions were not tested.
- **RapidOCR: no.** Asked for French through Docling (`iso:fr`), it uses the same PP-OCRv6 model
  and returned identical text. It kept accents well (word accuracy 0.81 to 1.00).
- **Tesseract with English data: this is the case reading again helps.** It matched 0.82 to 0.98
  of the words on prose pages and 0.45 to 0.48 on the tables, and dropped about a third of the
  accents on the court order (21 of 34). French data (`fra`) was not installed, so the gain from
  it is not measured here.

## Follow-ups

- Real French scans, read on a Mac and on Windows or Linux, to confirm detection and to see
  whether Vision on an older macOS reads French differently with the language set.
- Tesseract with `fra` data installed, on the same pages, to measure what the reread gains.
