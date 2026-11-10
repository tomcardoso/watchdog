# Enforced JSON output against a prompted schema, replayed — results (2026-11-10, #804)

One extraction request, captured from Watchdog's own code, sent 20 times as the pipeline sends it
(JSON output enforced by the API) and 20 times with the enforcement removed and the schema written
into the prompt. Collapsed answers (one to three facts) appeared only in the enforced runs. The
sample is small and from one document, so read it as evidence for #804, not as a rate.

## Setup

- **Code:** commit `fb0b8d9` was checked out when this was written (`46b9be1` when the work began;
  the capture used the `src/` of that working tree). Script: `benchmarks/enforced_json_replay.py`
  (`--capture`, `--runs N`, `--budget`, `--reanalyze`).
- **Model and request:** `claude-haiku-5-5`, effort `high`, `max_tokens` 115,200, the pipeline's
  system prompt, the `bankruptcy` record skill (the document's sidecar pin) and the corpus-v1
  brief. No `thinking` parameter is sent for this model. The request is the one
  `orchestrate._simple_extract` builds, recorded by replacing the Anthropic client with a recorder
  (no call was made to capture it). The saved schema equals `schemas.EXTRACTION`. Streaming, as the
  pipeline does it. The SDK's own two retries on 429/5xx were left on; no run needed one.
- **Document:** `CV-21-00656040-00CL Laurentian U Initial Order 1 FEB 2021.pdf`. It has **17 pages**
  (not 5) and no text layer, so every page needs OCR.
- **How the page text was produced (not Watchdog's normal route):** Docling is not installed here.
  Each page was rendered with `pdftoppm` at 200 DPI and read with RapidOCR 3.10.0 (the PP-OCRv6 small
  models Watchdog ships), one text line per OCR line, with no layout or markdown. `onnxruntime` was
  installed outside the project's venv, for this run only. The text is 34,141 characters
  (about 8,500 tokens by the chars/4 rule; the whole request is 24,000 to 25,000 input tokens).
  The candidate checklist holds the regex candidates (dates, amounts, dockets) only: GLiNER is not
  installed, so the names half of the harvest is absent. The planner chose one whole-document call,
  as in the pipeline. The prompt is therefore close to the real one but not identical to it: the
  page text is plainer and the checklist shorter.
- **Mode A, enforced:** exactly as captured, with `output_config = {format: json_schema, effort: high}`.
- **Mode B, prompted:** identical, except `output_config.format` is removed (`effort` kept) and
  `Return JSON matching this schema:` plus the schema (the wording `model_client` uses for a
  backend that cannot enforce one) and a sentence asking for only a JSON object are appended to the
  end of the user message. The cached prefix is unchanged.
- **Order:** A, B, A, B, and so on, four requests at a time, so both modes saw the same conditions.
- **Collapsed:** at most 3 `key_facts`, or any `quote_locator` with fewer than three letters or
  digits (the rule in `postflight._ENDED_EARLY_MAX_FACTS` and `_placeholder_locator`). The
  post-flight rule itself needs both together; the count under it is given separately.
- **Cost:** $0.5452 in total (A $0.3183, B $0.2269), from the usage each response reported and the
  catalogue's Haiku 5.5 rates. Output tokens include thinking tokens.

## Results

| | A: enforced | B: prompted |
|---|---|---|
| Runs | 20 | 20 |
| Collapsed (task rule) | **5** | **0** |
| Collapsed under the post-flight rule (both conditions) | 3 | 0 |
| Facts, min / median / max (answers that parsed) | 1 / 27 / 37 (18 answers) | 24 / 30.5 / 35 (20 answers) |
| Output tokens, min / median / max | 14,445 / 18,862 / 115,200 | 13,794 / 19,534 / 26,607 |
| Stop reason other than `end_turn` | 2 (`max_tokens`) | 0 |
| Not valid JSON as returned | 2 (the two `max_tokens` runs) | 8 |
| Not parseable even after repair | 2 | 0 |
| Answers failing the project's schema | 0 of 18 parsed | 10 of 20 |

Fisher exact test (two-sided), collapsed A against B:

| | A | B | p |
|---|---|---|---|
| These 40 runs | 5 / 20 | 0 / 20 | 0.047 |
| Pooled with the earlier probe (1 / 11 against 0 / 10) | 6 / 31 | 0 / 30 | 0.024 |
| Post-flight rule only: these runs, then pooled | 3 / 20 | 0 / 20 | 0.23, then 0.11 |
| Also counting the two `max_tokens` runs as failures | 7 / 20 | 0 / 20 | 0.008 |

### What the collapsed enforced answers look like

Runs 3, 29 and 35 match the description in #804: valid JSON, `end_turn`, one or two facts, `"x"`
as the locator. Runs 25 and 31 ended with one or two facts but no placeholder locator (run 25's
second fact is a bare `{"fact": "x"}` with no locator; run 31's single locator runs to 5,581
characters), so only the fact count catches them. All five used 12,000 to 18,000 thinking tokens,
the same as healthy runs, so a collapsed answer is not a cheap one.

### Mode B: what code would have to handle

Eight of the 20 prompted answers were not valid JSON as returned. Seven carried one extra closing
brace after a complete object (`json.JSONDecoder.raw_decode` reads them, ignoring one trailing
character); one was missing a closing brace (appending `}` parses it). After that repair all 20
parsed. Ten of the 20 then failed the project's schema, 15 errors in all:

| Kind | Count | Example |
|---|---|---|
| `additionalProperties` | 10 | `morgue_entity_id`, `document_requests` or `scratchpad` placed inside `document`; a stray `basis`, `document_type_note`, or an entity id used as a key |
| `required` | 5 | `scratchpad` missing (3), `morgue_entity_id` missing (2), usually because the same fields had been moved into `document` |

Moving enforcement off would need a tolerant parser (trailing and missing brackets) and a repair
step for fields filed under the wrong object. This run did not test whether the existing repair
retry in `_simple_extract` recovers them.

### The runaway answers in the enforced mode

Runs 7 and 39 (A) did not collapse; they ran to `max_tokens` (115,200 output tokens, 5 to 6
minutes, about $0.06 each) and ended in repeated text: run 7 repeated a sentence ("... is insolvent
and is a company to which the CCAA applies.") and run 39 ended in a long run of newline escapes. The pipeline
treats these as truncated answers. No prompted run did this.

## Reading

- Of 20 enforced runs, 5 gave one or two facts (3 of them with a placeholder locator) and 2 ran to
  the token limit in a loop; of 20 prompted runs, none did either. With the earlier probe, 6 of 31
  enforced runs collapsed and none of 30 prompted ones did. The enforced mode's collapse rate here
  (25%, or 3 in 20 on the stricter post-flight rule) is higher than the 1 in 10 reported in #804,
  which may reflect this document, this OCR text, or chance: the interval on 5 of 20 is wide.
- The two-sided p-values (0.047 for these runs alone, 0.024 pooled) are modest and come from one
  document, one prompt and a single afternoon of API conditions. The stricter post-flight rule gives
  p of 0.23 alone and 0.11 pooled, so the result depends on counting a one-fact answer with a real
  locator as a collapse. Treat it as consistent with enforcement being involved, not as proof of it.
- It does not show that turning enforcement off is a fix. The prompted mode gave complete answers
  every time, but half of them broke the schema or the JSON in a way the pipeline would have to
  repair or retry, and the cost of that was not measured. Prompted answers were also not scored for
  fact quality; only counts and locator lengths were compared.
- Thinking tokens (12,000 to 20,000 a run) were of similar size in both modes, and the collapsed
  answers were not shorter in thinking. This replay says nothing about the cause inside the API.
- Not tested: other documents, other effort levels, a different order of schema fields, the
  Docling-formatted page text, and whether the two token-limit loops are the same fault.

## Per-run results

Input is the whole prompt (uncached, cache-written and cache-read tokens) with the cache-read part
in brackets; run numbers are in launch order. "Facts only" means a collapse by the fact count
without a placeholder locator.

| # | Mode | Request id | Stop | Input (cached) | Output | Thinking | Facts | Entities | Collapsed | Parsed as returned | Schema errors |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | A | req_011CguzhTGqvHDWb87rNeS66 | end_turn | 24,547 (0) | 17,843 | 12,143 | 30 | 13 | no | yes | - |
| 2 | B | req_011CguzhTFrK3qykWWojgQ18 | end_turn | 24,083 (0) | 18,177 | 12,212 | 31 | 10 | no | yes | 1 |
| 3 | A | req_011CguzhTG7DndRTWZQRBZJd | end_turn | 24,547 (0) | 19,455 | 17,506 | 1 | 16 | yes | yes | - |
| 4 | B | req_011CguzhTH6CCDEsHWsT7bNA | end_turn | 24,083 (0) | 19,211 | 12,898 | 34 | 9 | no | yes | 3 |
| 5 | A | req_011CguznrJLTTXCXBsp17gfT | end_turn | 24,547 (11,582) | 18,685 | 12,682 | 28 | 14 | no | yes | - |
| 6 | B | req_011Cguzo41nKxTv1CwpyUqfc | end_turn | 24,083 (10,171) | 17,110 | 10,999 | 26 | 13 | no | yes | 1 |
| 7 | A | req_011Cguzo5dXEA1WX2AUQ9UDj | max_tokens | 24,547 (11,582) | 115,200 | 19,706 | n/a | n/a | n/a | no | - |
| 8 | B | req_011CguzoUVwfV7CCduFPEYJP | end_turn | 24,083 (10,171) | 14,380 | 8,814 | 28 | 13 | no | yes | 1 |
| 9 | A | req_011CguzsjCaZ475wtLM39Bvz | end_turn | 24,547 (11,582) | 19,038 | 13,121 | 23 | 16 | no | yes | - |
| 10 | B | req_011CguzszqS7KmnwDpGLPRir | end_turn | 24,083 (10,171) | 21,192 | 14,147 | 34 | 17 | no | yes | 0 |
| 11 | A | req_011CguztQk6tn97SW1famgHP | end_turn | 24,547 (11,582) | 17,408 | 11,773 | 28 | 14 | no | yes | - |
| 12 | B | req_011CguzyDpv7t4WpSLRiNhCn | end_turn | 24,083 (10,171) | 17,186 | 11,322 | 30 | 16 | no | yes | 0 |
| 13 | A | req_011CguzyaqDepXpoC58QY32K | end_turn | 24,547 (11,582) | 21,439 | 15,657 | 28 | 14 | no | yes | - |
| 14 | B | req_011CguzzGVS4yGANd4nuhbfG | end_turn | 24,083 (10,171) | 22,608 | 15,131 | 34 | 15 | no | no | 3 |
| 15 | A | req_011Cgv14JpST4ZPTTKYWFMFt | end_turn | 24,547 (11,582) | 14,671 | 8,728 | 30 | 13 | no | yes | - |
| 16 | B | req_011Cgv15mZ44tAu8zZbuhqG1 | end_turn | 24,083 (10,171) | 20,048 | 13,666 | 25 | 16 | no | no | 1 |
| 17 | A | req_011Cgv16uoBCiop1Qkfjy6Qk | end_turn | 24,547 (11,582) | 27,350 | 20,612 | 37 | 15 | no | yes | - |
| 18 | B | req_011Cgv18bqz9csxJSVyE4Vr8 | end_turn | 24,083 (10,171) | 19,651 | 12,685 | 28 | 16 | no | yes | 1 |
| 19 | A | req_011Cgv1BcorcBa2C3N9UAYPu | end_turn | 24,547 (11,582) | 17,420 | 11,723 | 32 | 12 | no | yes | - |
| 20 | B | req_011Cgv1EMibueS6Q6peZfCf9 | end_turn | 24,083 (10,171) | 18,094 | 11,312 | 34 | 14 | no | no | 0 |
| 21 | A | req_011Cgv1EdW7PZM8cRK7bHJAC | end_turn | 24,547 (11,582) | 18,630 | 13,517 | 24 | 13 | no | yes | - |
| 22 | B | req_011Cgv1EgZvS4H374rSgMkU9 | end_turn | 24,083 (10,171) | 13,794 | 7,155 | 31 | 14 | no | no | 0 |
| 23 | A | req_011Cgv1GmfJddjbRVzYaMmPc | end_turn | 24,547 (11,582) | 14,951 | 10,042 | 25 | 14 | no | yes | - |
| 24 | B | req_011Cgv1Jj3rMHGY7QM8oEqCP | end_turn | 24,083 (10,171) | 22,956 | 17,267 | 30 | 13 | no | yes | 1 |
| 25 | A | req_011Cgv1KZarvSA8sPqQaLfkC | end_turn | 24,547 (11,582) | 18,665 | 12,112 | 2 | 13 | yes (facts only) | yes | - |
| 26 | B | req_011Cgv1L73xCHRDh5YnqXwMT | end_turn | 24,083 (10,171) | 24,237 | 17,627 | 30 | 16 | no | yes | 2 |
| 27 | A | req_011Cgv1MGRYba4npCA6TnnhB | end_turn | 24,547 (11,582) | 22,442 | 17,344 | 32 | 13 | no | yes | - |
| 28 | B | req_011Cgv1R7Sv7GhRzFw1o9Ke7 | end_turn | 24,083 (10,171) | 21,173 | 13,742 | 34 | 10 | no | yes | 0 |
| 29 | A | req_011Cgv1RPPrJHEwbMJNvToBL | end_turn | 24,547 (11,582) | 14,445 | 12,178 | 1 | 14 | yes | yes | - |
| 30 | B | req_011Cgv1T7d8pku6J8t64j59H | end_turn | 24,083 (10,171) | 18,394 | 12,284 | 24 | 16 | no | no | 0 |
| 31 | A | req_011Cgv1TywogmXaPonz47hyF | end_turn | 24,547 (11,582) | 18,545 | 12,290 | 1 | 16 | yes (facts only) | yes | - |
| 32 | B | req_011Cgv1VrRfw4dVGLHNrCLdD | end_turn | 24,083 (10,171) | 19,732 | 13,153 | 31 | 17 | no | no | 0 |
| 33 | A | req_011Cgv1XNDLTPaBDNYnWW2GT | end_turn | 24,547 (11,582) | 22,580 | 17,164 | 27 | 13 | no | yes | - |
| 34 | B | req_011Cgv1YRBTTide2YTEVjsqc | end_turn | 24,083 (10,171) | 14,706 | 8,589 | 27 | 14 | no | yes | 1 |
| 35 | A | req_011Cgv1ZE324Z7bgELPtDQSp | end_turn | 24,547 (11,582) | 20,400 | 18,095 | 2 | 14 | yes | yes | - |
| 36 | B | req_011Cgv1bdQkhQGjogtC2og5h | end_turn | 24,083 (10,171) | 20,721 | 13,685 | 31 | 14 | no | no | 0 |
| 37 | A | req_011Cgv1ceLLzngmNqTRXAKY9 | end_turn | 24,547 (11,582) | 20,488 | 14,922 | 27 | 14 | no | yes | - |
| 38 | B | req_011Cgv1duXRNJCG2HRPQuQqN | end_turn | 24,083 (10,171) | 19,417 | 12,841 | 29 | 16 | no | yes | 0 |
| 39 | A | req_011Cgv1fNdMm7V2frhVAdY2D | max_tokens | 24,547 (11,582) | 115,200 | 10,987 | n/a | n/a | n/a | no | - |
| 40 | B | req_011Cgv1hhZh7DNj8BtQAWsQp | end_turn | 24,083 (10,171) | 26,607 | 19,507 | 35 | 14 | no | no | 0 |

## Addendum: where the enforced answers go wrong (same 40 responses, no new calls)

Read against the schema's field order for a fact (`fact`, `page`, `basis`, `date`, `entities`,
`quote_locator`; only `fact` is required):

- **Every enforced failure starts at a fact written without `page`.** In all five collapsed
  answers and both token-limit loops, the fact where the answer goes wrong has the fields `fact`,
  `entities`, `quote_locator` and no `page` or `date`. The answer breaks inside that
  `quote_locator`: a placeholder `"x"` (runs 3, 29, 35), page references written into the locator
  (runs 31 and 39, the latter then looping on newlines), a repeated sentence (run 7), or, in run 25,
  the rest of the facts list written *inside* the string with its quotation marks escaped
  (`","page":1}, {"fact": …`), 11,000 characters of it.
- **Run 25 shows the model trying to write `page` after `quote_locator`** and the text landing
  inside the string instead. No enforced fact in any run has its fields out of the schema's order
  (0 of 371 in the answers that did not collapse; 5 of 606 prompted facts do), which is consistent
  with the API allowing fields only in the declared order, so `page` cannot follow `entities`.
- **Starting without `page` nearly always failed.** Eight of the 20 enforced runs wrote their first
  facts without `page`. Seven failed. The eighth completed with 30 facts, none of which has a page.
  The 12 runs whose first fact has `page` all completed, with a page on every fact.
- **Without enforcement the model never left `page` out:** 0 of 606 prompted facts lack it.
- The prompted answers' own departures from the schema are elsewhere: after the facts list ends
  (fields filed inside `document`, an extra closing brace). They do not line up with the collapses.

Reading: the failures follow from one pattern, a fact that skips the optional `page` and then
cannot go back to it. Why the model skips `page` in some enforced runs and never in prompted ones
is not explained by these responses. An untested fix that keeps enforcement: make `page` required
(it is already nullable), so the field after `fact` is always `page`.

## Addendum 2: the same request with `page` required (20 runs, $0.24)

The captured request was replayed 20 more times, enforced as in mode A, with one change to the
schema it sends: a fact's `required` is `["fact", "page"]` instead of `["fact"]` (`page` stays
`integer` or `null`). Same afternoon, four at a time, no errors or retries.

| | A: enforced, as captured | Enforced, `page` required |
|---|---|---|
| Runs | 20 | 20 |
| Collapsed | 5 | 0 |
| Ran to the token limit | 2 | 0 |
| Facts, min / median / max | 1 / 27 / 37 (18 answers) | 24 / 27 / 31 |
| Output tokens, min / median / max | 14,445 / 18,862 / 115,200 | 15,904 / 20,004 / 28,109 |
| Valid JSON passing the schema | 18 | 20 |
| Facts with a page | 341 of 371 (answers that completed) | 548 of 548 |
| Facts with a `quote_locator` | 43 of 371 (12%) | 21 of 548 (4%) |

Failures of either kind: 7 of 20 against 0 of 20 (Fisher exact, two-sided, p = 0.008). No fact
has a null page. Fewer facts carry a `quote_locator` than in mode A (4% against 12%; the prompted
answers had 17%); the facts were not scored for quality.

Still one document and one afternoon, and the modes were run one after the other, not interleaved.
It has not been run through the pipeline or on the six-document benchmark.

Request ids: req_011Cgv2iNBXcVuEU1G2dHVMF, req_011Cgv2iNAH2SrdeuW62evsN,
req_011Cgv2iN9HWXH3FimZdBeRJ, req_011Cgv2iN8ngedos1XQ3aJLK, req_011Cgv2okHCaWb7MQ6gJPM2q,
req_011Cgv2pBLL9hzt5g7WbiuqH, req_011Cgv2q4d2shKSBPDBa23GS, req_011Cgv2qbVfJpNVPPHGHBgdD,
req_011Cgv2twmdj1CG2xadj5FW2, req_011Cgv2uoqve887EjgFDq7ve, req_011Cgv2vc3YSZM2vZ9ujrEQB,
req_011Cgv2y51Krs7HebMU3YpjA, req_011Cgv3117xxXvmV886gHuic, req_011Cgv31qmvWcuwrsA6kpvRA,
req_011Cgv32oifxrGhxJ4K64RNY, req_011Cgv33jRkBaGA19hE68zgS, req_011Cgv375gE3Yz1uGkVR9J8u,
req_011Cgv37cKikW6beihy48yjM, req_011Cgv38zNYwM2m3MSFBt4VN, req_011Cgv393HvHzhpJLxAh4opC.

## Addendum 3: the same request with every field required (20 runs, $0.27)

Twenty more enforced runs, with the schema passed through `model_client._to_strict_schema` (the
variant OpenAI's strict mode already gets: every property required, optional scalars widened to
allow `null`). Anthropic's API rejected that variant as it stands, before any billing: a nullable
`enum` (`basis`, and one other field) written as `{"type": ["string", "null"], "enum": [...]}` is
an invalid schema there. Rewritten as `{"anyOf": [{"type": "string", "enum": [...]}, {"type":
"null"}]}` it was accepted. Sets were run one after the other, the same afternoon.

| | Enforced, as captured | `page` required | Every field required |
|---|---|---|---|
| Runs | 20 | 20 | 20 |
| Collapsed | 5 | 0 | 0 |
| Ran to the token limit | 2 | 0 | 0 |
| Valid JSON passing the schema sent | 18 | 20 | 20 |
| Facts, min / median / max | 1 / 27 / 37 | 24 / 27 / 31 | 21 / 27 / 34 |
| Output tokens, median | 18,862 | 20,004 | 22,101 |
| Facts with a page | 92% | 100% | 100% |
| Facts with a date | 19% | 17% | 22% |
| Facts with a `quote_locator` | 12% | 4% | 6% |
| Facts marked `inferred` | 1 of 371 | 2 of 548 | 2 of 542 |
| Facts tagged with an entity | 98% | 99% | 99% |
| Entities, median; relationships per answer | 14; 15.5 | 14; 14.8 | 14; 16.0 |
| Answers with a `plan` | 3 of 13 | 3 of 20 | 0 of 20 |

(The first column's rates are over the 13 answers that completed with more than three facts. The
prompted answers of the main run: date 21%, locator 18%, 6 of 606 inferred.)

Requiring every field did not make the model fill them: unused fields came back `null`, and the
date, locator and `inferred` rates sit within the range of the other sets. Both required variants
carry fewer locators than the prompted answers; the cause is not known. Output grew by about
2,000 tokens a call at the median over the `page`-only variant (about $0.01 on this model), part
of it the written-out nulls. Fact quality was not scored in any set.

Request ids: req_011Cgv3hgEKHZoEunLpEYbSZ, req_011Cgv3hgE4TiYbfH9NCGi8t,
req_011Cgv3hgEoUif1rKosTvZ3r, req_011Cgv3hgDaJNYxPqcUAEeQP, req_011Cgv3oJYkKFDNY35X9Kz4Z,
req_011Cgv3pNacyGtq9LP6XpVpF, req_011Cgv3qaj8y2knw2MX53tJ8, req_011Cgv3qfXNh38KkyqZZR1kz,
req_011Cgv3uhP6e8FTmGchtgsV6, req_011Cgv3vQ93fngoXP37fbfUD, req_011Cgv3wzjwDd6e5RJP2Ztag,
req_011Cgv3yz5ZMNASeg1hn9sYs, req_011Cgv41w7kx3Qi9CWUkSNZh, req_011Cgv42mdmt76HbMnFQs7WB,
req_011Cgv45fzHgQvf8TzXBAe5S, req_011Cgv47oo9fqh23dSUisz9m, req_011Cgv49xmTDZ6Fh2FJcUPBS,
req_011Cgv4AZfB1259DmPbBi3yV, req_011Cgv4C62aHQBJNgNdbAB6N, req_011Cgv4E3DDWVinHDE7vxzB9.
