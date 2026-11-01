"""Whole-prompt sectioning under a long-prompt line (D370): every planned extraction call's prompt,
not only its document text, stays under the model's price line; the cost estimate plans with the
same function; a call that crosses anyway is logged. No model is called and GLiNER never runs."""

import asyncio
import json
import random
from pathlib import Path

import pytest

from watchdog import model_catalog, model_client as mc
from watchdog.pipeline import harvest, ingest_setup, orchestrate, prompts, schemas, section

# Page counts of the six corpus-v1 documents. The real page text lives in a benchmark chew that is
# not in the repository, so these are stand-ins: dense legal/financial pages of 800 to 1,220 est
# tokens (the 70-page one comes to about 70,000 est tokens, about 85,000 real on Haiku 5.5, as the
# 2026-10-31 document did), with figures, money, dates and table rows that give each page a
# candidate-checklist line, so the checklist grows with the document.
CORPUS_PAGES = (5, 17, 34, 36, 47, 70)
_WORDS = ("the court ordered respondent to pay pursuant to section agreement trustee creditor filed "
          "affidavit schedule amount balance director company account notice hearing").split()
_OPENAI_TIERED = ("gpt-6-luna", "gpt-5.6-luna")


def _page(rng: random.Random, n: int, est: int) -> str:
    out, i = [], 0
    while sum(map(len, out)) < est * 4:
        i += 1
        if i % 7 == 0:
            out.append(f"| Line {n}.{i} | ${rng.randint(1, 999):,},{rng.randint(100, 999)} | "
                       f"{rng.randint(1, 99)}.{rng.randint(0, 9)}% |\n")
        elif i % 11 == 0:
            out.append(f"On March {rng.randint(1, 28)}, 20{rng.randint(10, 26)} the sum of "
                       f"${rng.randint(1, 900)}.{rng.randint(1, 9)} million was paid. ")
        else:
            out.append(" ".join(rng.choice(_WORDS) for _ in range(12)) + ". ")
    return "".join(out)[:est * 4]


def _pages(count: int, seed: int | None = None) -> list[dict]:
    rng = random.Random(count if seed is None else seed)
    return [{"page": n, "markdown": _page(rng, n, rng.randint(800, 1_220))}
            for n in range(1, count + 1)]


@pytest.fixture(autouse=True)
def defaults_only(monkeypatch):
    """Model-aware defaults, never the developer's own config; no local NER model."""
    monkeypatch.setattr(section, "_config_get", lambda key, default: default)
    monkeypatch.setattr(harvest, "harvest_entities", lambda *_a, **_k: [])
    # Since D365 extraction reaches GLiNER through `_entity_predictions` (via `harvest.candidates`),
    # not `harvest_entities`; None is "the model is unavailable", the regex-only checklist.
    monkeypatch.setattr(harvest, "_entity_predictions", lambda *_a, **_k: None)


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / ".watchdog" / "queue").mkdir(parents=True)
    (vault / "context.md").write_text("Follow the money between the trustee and the creditors.\n")
    return vault


def _queue(vault: Path, sha: str, pages: list[dict]) -> list[dict]:
    (vault / ".watchdog" / "queue" / f"{sha}.json").write_text(json.dumps(
        {"filename": f"{sha}.pdf", "page_count": len(pages), "pages": pages}))
    return [{"sha256": sha, "filename": f"{sha}.pdf", "page_count": len(pages),
             "est_tokens": section.est_tokens_from_pages(pages)}]


def _rendered_est(vault: Path, result: dict, *, skill_text: str, brief: str, model: str,
                  pf: dict) -> list[int]:
    """Each planned call's prompt as extraction will actually build it — the real prompt builders,
    the real checklist for the text it covers, a carry-forward block filling its whole reserve on
    later sections — in est tokens, schema and system prompt included."""
    extra = (section._est_ceil(json.dumps(schemas.SECTION))
             + section._est_ceil(mc._SYSTEM_PROMPT))
    common = dict(skill_text=skill_text, brief=brief, known_document_types=[],
                  file_metadata={}, processing={}, model=model)
    if not result["sectioned"]:
        text = orchestrate._pages_text(pf["pages"])
        blocks = prompts.build_extract_prompt(
            pages_text=text, sidecar=None, candidates=orchestrate._candidates_checklist(text),
            **common)
        return [sum(section._est_ceil(b["text"]) for b in blocks) + extra]
    out = []
    for i, sec in enumerate(result["sections"]):
        text = (vault / sec["pages_path"]).read_text(encoding="utf-8")
        carry = "" if i == 0 else "x" * ((section._CARRY_RESERVE_EST - 60) * 4)
        blocks = prompts.build_section_prompt(
            pages_text=text, carry_forward=carry, section_label=sec["label"], is_first=(i == 0),
            candidates=orchestrate._candidates_checklist(text), **common)
        out.append(sum(section._est_ceil(b["text"]) for b in blocks) + extra)
    return out


# ── the rule ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model,backend", [("haiku", "claude-api"),
                                           *[(m, "openai") for m in _OPENAI_TIERED]])
def test_no_planned_call_crosses_the_line_on_the_corpus_sizes(tmp_path, model, backend):
    """Every corpus-sized document, and one far larger, planned for each model with a price line:
    each call's prompt, built the way extraction builds it, is no larger than planned, and the
    planned size in real tokens is under the line."""
    vault = _vault(tmp_path)
    skill = section.largest_skill_text()
    brief = (vault / "context.md").read_text()
    line = model_catalog.catalog_long_context_threshold(mc.resolve_model_id(model))
    ratio = mc.tokenizer_ratio(model, backend)
    for count in (*CORPUS_PAGES, 260):
        sha = f"doc{count}"
        pages = _pages(count)
        _queue(vault, sha, pages)
        pf = {"pages": pages, "page_count": count}
        result = section.run(vault, sha, model=model, backend=backend, pf=pf, skill_text=skill,
                             brief=brief)
        planned = result["prompts"]
        assert len(planned) == (len(result["sections"]) if result["sectioned"] else 1)
        for est in planned:
            assert est * ratio <= line * (1 - section._WHOLE_PROMPT_HEADROOM), (model, count)
        built = _rendered_est(vault, result, skill_text=skill, brief=brief, model=model, pf=pf)
        for got, est in zip(built, planned):
            assert got <= est, (model, count, got, est)


def test_the_2026_10_31_document_is_now_sectioned_on_haiku(tmp_path):
    """The 70-page document went to Haiku 5.5 as one call of 113,443 prompt tokens: its text was
    under the text threshold, its prompt was not. It is now two calls, both under the line, and a
    5-page document still goes whole."""
    vault = _vault(tmp_path)
    for count, sectioned in ((5, False), (70, True)):
        pages = _pages(count)
        _queue(vault, f"d{count}", pages)
        threshold, _ = section.model_defaults("haiku")
        assert section.est_tokens_from_pages(pages) <= threshold     # its text alone fits, both times
        result = section.run(vault, f"d{count}", model="haiku", backend="claude-api")
        assert result["sectioned"] is sectioned
    assert len(result["sections"]) == 2


def test_sections_the_line_sizes_are_even(tmp_path):
    """Two calls rather than a full one and a sliver: the line sets the number of sections, then
    the pages are spread evenly across them."""
    vault = _vault(tmp_path)
    pages = _pages(70)
    _queue(vault, "d", pages)
    prompts_ = section.run(vault, "d", model="haiku", backend="claude-api")["prompts"]
    assert len(prompts_) == 2 and max(prompts_) - min(prompts_) < 0.2 * max(prompts_)


def test_plan_calls_holds_text_and_checklist_to_the_line():
    profile = section.PromptProfile(page_text=(1_000,) * 100, page_extra=(200,) * 100, fixed=10_000)
    plan = section.plan_calls(profile, threshold=10**9, budget=10**9, overlap=0, line=50_000)
    assert plan["sectioned"]
    assert all(p <= 50_000 for p in plan["prompts"])
    # The first section carries no carry-forward; every later one keeps the reserve.
    s, e = plan["ranges"][1]
    assert plan["prompts"][1] == 10_000 + section._CARRY_RESERVE_EST + 1_200 * (e - s + 1)
    whole = section.plan_calls(profile, threshold=10**9, budget=10**9, overlap=0, line=200_000)
    assert whole == {"sectioned": False, "ranges": [], "prompts": [10_000 + 120_000]}


def test_a_page_too_large_for_the_line_gets_its_own_call():
    profile = section.PromptProfile(page_text=(1_000, 90_000, 1_000), page_extra=(0, 0, 0),
                                    fixed=10_000)
    plan = section.plan_calls(profile, threshold=10**9, budget=10**9, overlap=0, line=50_000)
    assert (2, 2) in plan["ranges"] and max(plan["prompts"]) > 50_000


def test_the_checklist_is_counted_from_each_page_and_from_a_stored_harvest():
    """The regex half is counted live; the names half from pre-processing's stored harvest when the
    document has one (D365's shape), otherwise a fixed reserve per page."""
    pages = [{"page": 1, "markdown": "Paid $5,000 on March 3, 2024."}]
    bare = section.page_checklist_tokens({"pages": pages, "page_count": 1})
    stored = {"names": {"1": [["person", "Ada Lovelace"]]}}
    with_names = section.page_checklist_tokens({"pages": pages, "page_count": 1, "harvest": stored})
    line = harvest.format_checklist(harvest.harvest("<!-- PAGE 1 -->\n\n" + pages[0]["markdown"])
                                    + [{"page": 1, "kind": "person", "value": "Ada Lovelace"}])
    assert with_names == [section._est_ceil(line) + 1]
    assert bare[0] > with_names[0] - 10 and bare[0] >= section._NAMES_RESERVE_EST_PER_PAGE


def test_the_fixed_part_uses_the_real_prompt_builders():
    """The fixed part grows with the skill and the brief by their own size: it is rendered,
    not fitted."""
    pf = {"pages": [], "page_count": 0}
    base = section.fixed_prompt_tokens(pf, skill_text="", brief=None, model="haiku")
    more = section.fixed_prompt_tokens(pf, skill_text="s" * 4_000, brief="b" * 8_000, model="haiku")
    assert more - base == pytest.approx(3_000, abs=20)      # their text plus the brief's heading
    assert section.fixed_prompt_tokens(pf, skill_text=section.largest_skill_text(), brief=None,
                                       model="haiku") > 9_000


# ── the estimate plans with the same function ─────────────────────────────────────────────

def test_the_estimate_and_the_run_plan_the_same_calls(tmp_path, monkeypatch):
    """`long_prompt_plan` and `section.run` both go through `section.plan_document`: given the same
    skill and brief, the calls the estimate prices are the calls the run makes."""
    vault = _vault(tmp_path)
    seen = []
    real = section.plan_document

    def spy(pf, model, backend=None, vault=None, **kw):
        plan = real(pf, model, backend, vault, **kw)
        seen.append((pf.get("filename") or pf.get("pages")[0]["page"], plan["prompts"]))
        return plan

    monkeypatch.setattr(section, "plan_document", spy)
    queue = []
    for count in CORPUS_PAGES:
        queue += _queue(vault, f"d{count}", _pages(count))
    ingest_setup.long_prompt_plan(queue, "haiku", "claude-api", vault)
    estimated = [p for _, p in seen]
    run = [section.run(vault, f"d{count}", model="haiku", backend="claude-api")["prompts"]
           for count in CORPUS_PAGES]
    assert estimated[:len(CORPUS_PAGES)] == run


# ── flat-priced models: unchanged ─────────────────────────────────────────────────────────

def _text_only_plan(pages, model, backend):
    """Sectioning as it was before D370, by document text alone."""
    threshold, budget = section.model_defaults(model, backend)
    overlap = max(1, budget * section._OVERLAP_NUMERATOR // section._OVERLAP_DENOMINATOR)
    text = [section.est_tokens(p["markdown"]) for p in pages]
    return [] if sum(text) <= threshold else section.plan_ranges(text, budget, overlap)


@pytest.mark.parametrize("model,backend", [("gemini-3.8-flash", "gemini"),
                                           ("deepseek-flash", "deepseek"), ("sonnet", "claude-api")])
def test_flat_priced_models_section_exactly_as_before(tmp_path, model, backend):
    """A model that prices flat has no line, so the whole-prompt rule never applies: every corpus
    size, a document far over the threshold, and one whose text sits just under it while its
    whole prompt would not, plan exactly as text-only sectioning did."""
    vault = _vault(tmp_path)
    threshold, _ = section.model_defaults(model, backend)
    near = [{"page": n, "markdown": "w" * ((threshold // 40) * 4)} for n in range(1, 41)]
    assert section.est_tokens_from_pages(near) <= threshold
    cases = [_pages(c) for c in CORPUS_PAGES] + [_pages(900, seed=1), near]
    for i, pages in enumerate(cases):
        _queue(vault, f"f{i}", pages)
        result = section.run(vault, f"f{i}", model=model, backend=backend)
        expected = _text_only_plan(pages, model, backend)
        got = [tuple(int(x) for x in s["label"].split()[1].split("–")) for s in result["sections"]] \
            if result["sectioned"] else []
        assert got == expected, (model, i)
        assert result["line"] is None
    # The near-threshold document goes whole although its whole prompt is over the text threshold:
    # the window keeps 40% beside it for the prompt's other parts and the answer.
    assert not section.run(vault, f"f{len(cases) - 1}", model=model, backend=backend)["sectioned"]


# ── the guard after each call ─────────────────────────────────────────────────────────────

def _log_text(vault: Path) -> str:
    log = orchestrate.processing_log(vault)
    return log.read_text() if log.exists() else ""


def test_a_call_planned_under_the_line_that_crosses_it_is_logged(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    monkeypatch.setattr(mc, "tokenizer_ratio", lambda *a, **k: 1.21)

    async def fake(**_kw):
        return mc.ModelResult(parsed={}, text="{}", model="claude-haiku-5-5", backend="claude-api",
                              auth_mode="api_key", usage={"input_tokens": 101_113,
                                                          "cache_creation_input_tokens": 12_330,
                                                          "output_tokens": 10})

    monkeypatch.setattr(mc, "acomplete_json", fake)
    prompt = [{"type": "text", "text": "x" * 4 * 70_000}]     # planned ~84,700 real tokens
    asyncio.run(orchestrate._call_model(task="extract-section", prompt=prompt, schema={},
                                        model="haiku", filename="big.pdf", detail="pages 1–36",
                                        vault=vault))
    text = _log_text(vault)
    assert "WARN big.pdf [pages 1–36]: prompt was 113,443 tokens" in text
    assert "100,000-token long-prompt line" in text and "planned at about 84,7" in text


def test_the_guard_is_quiet_under_the_line_on_flat_models_and_on_a_subscription(tmp_path,
                                                                                monkeypatch):
    vault = _vault(tmp_path)
    monkeypatch.setattr(mc, "tokenizer_ratio", lambda *a, **k: 1.21)
    under = {"input_tokens": 90_000, "cache_read_input_tokens": 10_000}
    over = {"input_tokens": 150_000}
    for model_id, backend, usage in (("claude-haiku-5-5", "claude-api", under),
                                     ("claude-sonnet-5-5", "claude-api", over),
                                     ("claude-haiku-5-5", "claude-agent-sdk", over)):
        orchestrate._warn_if_over_line(vault, model_id=model_id, backend=backend, usage=usage,
                                       attempts=1, est_prompt_tokens=1_000, filename="f.pdf",
                                       detail=None)
    # Planned over the line (a single page too large): said when planned, not again here.
    orchestrate._warn_if_over_line(vault, model_id="claude-haiku-5-5", backend="claude-api",
                                   usage=over, attempts=1, est_prompt_tokens=120_000,
                                   filename="f.pdf", detail=None)
    assert "WARN" not in _log_text(vault)
    # A retried call's usage covers every attempt; the line is judged per attempt.
    orchestrate._warn_if_over_line(vault, model_id="claude-haiku-5-5", backend="claude-api",
                                   usage=over, attempts=2, est_prompt_tokens=1_000,
                                   filename="f.pdf", detail=None)
    assert "WARN" not in _log_text(vault)


def test_a_page_planned_over_the_line_is_logged_when_planned(tmp_path):
    vault = _vault(tmp_path)
    orchestrate._log_planned_over_line(vault, "big.pdf", {
        "line": 78_000, "prompts": [60_000, 90_000],
        "sections": [{"label": "pages 1–3"}, {"label": "pages 4–4"}]})
    text = _log_text(vault)
    assert "WARN big.pdf [pages 4–4]: planned prompt of about 90,000" in text
    assert "pages 1–3" not in text


# ── the corrected window does not raise Haiku past its line ───────────────────────────────

def test_haiku_post_processing_chunks_stay_under_the_line():
    """Haiku 5.5's catalogued window is now its real 1M (D370), so a post-processing chunk is
    sized by the line's 10% headroom, not the window: the chunk's data plus the largest
    post-processing instructions stay under 100,000 real tokens."""
    from watchdog.pipeline import chunking
    assert mc.context_window("haiku") == 1_000_000
    ratio = mc.tokenizer_ratio("haiku")
    data = chunking.prompt_budget_chars("haiku") / 4 * ratio
    largest = max(len(p.read_text()) for p in (Path(prompts.__file__).parent.parent / "prompts")
                  .glob("*.md") if p.stem != "extract_instructions")
    assert data + largest / 4 * ratio < 100_000
