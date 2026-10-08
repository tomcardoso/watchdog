"""A source passage for every fact (D270).

Most facts carry only a page number: the model gives a `quote_locator` only when wording matters
(D170). This module finds, deterministically and without any model call (I1), the sentence or two
on the cited page that best supports each fact, so a reporter can see the evidence beside the claim
and jump to it.

How a passage is found:

* The page text is split into passages: sentences within a block (a paragraph line, a table row, a
  heading), using the same sentence rules as quote expansion (`quote_verify`). A pair of adjacent
  sentences in one block is also a candidate, for a fact that combines two.
* The fact is reduced to its distinctive tokens: figures (normalized as `figure_verify` does, so
  "$3,900,000" matches "3900000"), calendar dates (normalized to ISO, so "February 8, 2022" matches
  "2022-02-08" and "8 February 2022"), and words, lower-cased, lightly stemmed, with stopwords
  dropped. Each token is weighted by how rare it is among the document's passages (inverse document
  frequency), and figures, dates and capitalized names count double.
* A passage's score is the weighted share of the fact's tokens it contains (0 to 1). The cited page
  is tried first; its neighbours, and then the whole document for a fact with no page, are tried
  only when the cited page has nothing above the threshold, at a small discount so the cited page
  wins ties.
* A match is kept only at or above `MATCH_THRESHOLD`, calibrated on the demo investigation's facts,
  whose quote locators give the true source sentence (see D270 for the measurement). A fact with no
  acceptable passage is marked unlocated rather than given a weak guess.

A fact whose `quote` resolved (D170) uses that quote as its passage. The result is stored on the
fact in the staged extraction: `passage`, `passage_page`, `passage_method` ("quote", "matched" or
"unlocated") and `passage_score`. Post-flight stamps it before the commit (I7); `watchdog
locate-passages` computes it for documents committed before this existed, from the morgue's page
text, with no model call."""

from __future__ import annotations

import json
import math
import os
import re
from datetime import date as _date
from pathlib import Path

from watchdog.pipeline.figure_verify import _GROUPED_NUM_RE, _YEAR_RE, _normalize_token
from watchdog.pipeline.quote_verify import _HYPHEN_BREAK_RE, _WS_RE, _is_soft_wrap, _sentence_boundary_end

# Calibrated on the demo investigation (D270): see `benchmarks/passage_accuracy.py`.
MATCH_THRESHOLD = 0.45
_OFF_PAGE_FACTOR = 0.9       # a neighbouring page's passage must beat the cited page's clearly
_PAIR_MARGIN = 0.12          # a two-sentence passage must add this much to replace one sentence
_WEIGHTED_SHARE = 0.5         # score = half rarity-weighted token share, half plain token share
_PASSAGE_CAP = 500           # characters, as for a resolved quote
_MIN_PASSAGE_CHARS = 12      # shorter blocks (a page number, a lone label) are furniture

METHODS = ("quote", "matched", "unlocated")

_STOPWORDS = frozenset("""
a about above after again against all also am an and any are as at be because been before being
below between both but by can could did do does doing down during each few for from further had has
have having he her here hers herself him himself his how i if in into is it its itself just me more
most my myself no nor not now of off on once only or other our ours ourselves out over own same she
should so some such than that the their theirs them themselves then there these they this those
through to too under until up very was we were what when where which while who whom why will with
would you your yours yourself yourselves said says say according per via upon within without
whose whether either neither one two three four five six seven eight nine ten
""".split())

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12, "jan": 1,
    "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10,
    "nov": 11, "dec": 12,
}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})(?:-(\d{2}))?\b")
_MDY_RE = re.compile(rf"\b({_MONTH_ALT})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I)
_DMY_RE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:day\s+of\s+)?({_MONTH_ALT})\.?,?\s+(\d{{4}})\b", re.I)
_MY_RE = re.compile(rf"\b({_MONTH_ALT})\.?,?\s+(\d{{4}})\b", re.I)
_SCALE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(million|billion|thousand|m|bn|k)\b", re.I)
_SCALE = {"thousand": 3, "k": 3, "million": 6, "m": 6, "billion": 9, "bn": 9}
_WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]*")
_TABLE_RULE_RE = re.compile(r"^\s*\|?\s*:?-{2,}")
_MD_PREFIX_RE = re.compile(r"^\s*(?:#{1,6}\s+|>\s*|[-*+]\s+)")


# ── tokens ───────────────────────────────────────────────────────────────────────────────

def _stem(word: str) -> str:
    """A light suffix stripper: enough that "awarded"/"award" and "contracts"/"contract" meet,
    not a linguistic stemmer. Only words of six letters or more are touched."""
    w = word.lower().replace("’", "'").strip("'-")
    if w.endswith("'s"):
        w = w[:-2]
    if len(w) < 6:
        return w
    for suffix in ("ations", "ation", "ments", "ment", "ings", "ing", "edly", "ies", "ied", "ed",
                   "es", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 4:
            return w[: -len(suffix)] + ("y" if suffix in ("ies", "ied") else "")
    return w


def _iso(year: int, month: int, day: int | None = None) -> str | None:
    try:
        if day is not None:
            _date(year, month, day)
            return f"{year:04d}-{month:02d}-{day:02d}"
        if 1 <= month <= 12:
            return f"{year:04d}-{month:02d}"
    except ValueError:
        return None
    return None


def _date_tokens(text: str) -> tuple[set[str], str]:
    """ISO tokens for every calendar date written in `text` (a full date also yields its
    month, so "February 2022" in a fact meets "February 8, 2022" on the page), and `text` with
    those dates blanked out so their parts aren't counted again as figures and words."""
    found: set[str] = set()

    def take(iso: str | None, m: re.Match) -> str:
        if iso:
            found.add(iso)
            if len(iso) == 10:
                found.add(iso[:7])
        return " " * (m.end() - m.start())

    text = _ISO_DATE_RE.sub(lambda m: take(_iso(int(m[1]), int(m[2]), int(m[3]) if m[3] else None), m), text)
    text = _MDY_RE.sub(lambda m: take(_iso(int(m[3]), _MONTHS[m[1].lower()], int(m[2])), m), text)
    text = _DMY_RE.sub(lambda m: take(_iso(int(m[3]), _MONTHS[m[2].lower()], int(m[1])), m), text)
    text = _MY_RE.sub(lambda m: take(_iso(int(m[2]), _MONTHS[m[1].lower()]), m), text)
    return found, text


def _figure_tokens(text: str) -> tuple[set[str], str]:
    """Normalized figures in `text` ("$3,900,000" → "3900000", "$3.9 million" → "3900000"), and
    `text` with them blanked out."""
    found: set[str] = set()

    def scaled(m: re.Match) -> str:
        try:
            value = float(m[1]) * 10 ** _SCALE[m[2].lower()]
        except (ValueError, KeyError):
            return m[0]
        found.add(_normalize_token(f"{value:.2f}"))
        return " " * len(m[0])

    text = _SCALE_RE.sub(scaled, text)
    for m in _GROUPED_NUM_RE.finditer(text):
        tok = _normalize_token(m.group(0))
        if tok != "0":
            found.add(tok)
    return found, _GROUPED_NUM_RE.sub(lambda m: " " * len(m[0]), text)


def tokens(text: str) -> dict[str, bool]:
    """The distinctive tokens of `text`, each mapped to whether it is a strong signal (a figure,
    a date, or a capitalized word that does not open a sentence)."""
    text = _HYPHEN_BREAK_RE.sub("", text or "")
    out: dict[str, bool] = {}
    dates, text = _date_tokens(text)
    for d in dates:
        out["@" + d] = True
    figures, text = _figure_tokens(text)
    for f in figures:
        # A bare year is a date, and weak on its own: it recurs across a document's pages.
        out["#" + f] = not _YEAR_RE.match(f)
    prev_end = ""
    for m in _WORD_RE.finditer(text):
        word = m.group(0)
        low = word.lower().strip("'’-")
        before = text[max(0, m.start() - 2):m.start()].strip()
        sentence_start = not prev_end or before.endswith((".", ":", "?", "!"))
        prev_end = word
        if low in _STOPWORDS or (len(low) < 3 and not word.isupper()):
            continue
        key = _stem(low)
        strong = word[0].isupper() and not sentence_start
        out[key] = out.get(key, False) or strong
    return out


# ── passages ─────────────────────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    text = _HYPHEN_BREAK_RE.sub("", text)
    if text.lstrip().startswith("|"):
        cells = [c.strip() for c in text.strip().strip("|").split("|")]
        text = " · ".join(c for c in cells if c)
    text = _MD_PREFIX_RE.sub("", text)
    text = text.replace("**", "").replace("__", "")
    return _WS_RE.sub(" ", text).strip()


def _blocks(page: str) -> list[str]:
    """The page's blocks: lines, with a hard-wrapped sentence's lines rejoined (`_is_soft_wrap`)."""
    blocks: list[str] = []
    start = 0
    for i, ch in enumerate(page):
        if ch == "\n" and not _is_soft_wrap(page, i):
            blocks.append(page[start:i])
            start = i + 1
    blocks.append(page[start:])
    return [b for b in blocks if b.strip() and not _TABLE_RULE_RE.match(b)]


# A full stop after one of these is an abbreviation, not a sentence end ("Mr. Pike advised").
# Company suffixes (Inc., Ltd.) are deliberately absent: they end sentences as often as not.
_ABBREVIATIONS = frozenset("mr mrs ms dr hon st mt no nos vs gen lt col sgt prof rev fig art sec s ss para cf approx".split())
_ABBREV_RE = re.compile(r"(?:^|[\s(])([A-Za-z]{1,6})\.$")


def _abbreviation_at(block: str, i: int) -> bool:
    m = _ABBREV_RE.search(block, max(0, i - 8), i + 1)
    return bool(m) and m.group(1).lower() in _ABBREVIATIONS


def _sentences(block: str) -> list[str]:
    out, start = [], 0
    for i, ch in enumerate(block):
        if ch in ".!?" and _sentence_boundary_end(block, i) and not (ch == "." and _abbreviation_at(block, i)):
            out.append(block[start:i + 1])
            start = i + 1
    out.append(block[start:])
    return [s for s in (_clean(s) for s in out) if s]


def _page_passages(page: str) -> list[tuple[str, bool]]:
    """Candidate passages on one page as `(text, is_pair)`: each sentence, and each pair of
    adjacent sentences in one block (joined with a space)."""
    out: list[tuple[str, bool]] = []
    for block in _blocks(page or ""):
        sents = [s for s in _sentences(block) if len(s) >= _MIN_PASSAGE_CHARS or any(c.isdigit() for c in s)]
        out.extend((s, False) for s in sents)
        out.extend((f"{a} {b}", True) for a, b in zip(sents, sents[1:]))
    return out


def split_passages(page: str) -> list[str]:
    """Candidate passages on one page, sentences first, then adjacent pairs."""
    return [text for text, _pair in _page_passages(page)]


class _DocIndex:
    """Every page's passages and their token sets, plus document-wide token rarity."""

    def __init__(self, page_texts: dict[int, str]):
        self.pages: dict[int, list[tuple[str, set[str], bool]]] = {}
        df: dict[str, int] = {}
        n = 0
        for page, text in page_texts.items():
            rows = []
            for passage, pair in _page_passages(text):
                rows.append((passage, set(tokens(passage)), pair))
            self.pages[page] = rows
            for passage, toks, pair in rows:
                if pair:
                    continue
                n += 1
                for t in toks:
                    df[t] = df.get(t, 0) + 1
        self.n = max(n, 1)
        self.df = df

    def weight(self, token: str, strong: bool) -> float:
        idf = math.log(1 + self.n / (1 + self.df.get(token, 0)))
        return idf * (2.0 if strong else 1.0)


def _score_page(index: _DocIndex, page: int, fact_tokens: dict[str, float]) -> tuple[float, str | None]:
    """The best passage on `page` for a fact, as (score, passage)."""
    total = sum(fact_tokens.values())
    if not total:
        return 0.0, None
    figures = [t for t in fact_tokens if t.startswith(("#", "@")) and
               not _YEAR_RE.match(t[1:])]
    best_single: tuple[float, str | None] = (0.0, None)
    best_pair: tuple[float, str | None] = (0.0, None)
    for passage, toks, pair in index.pages.get(page, []):
        hit = [t for t in fact_tokens if t in toks]
        score = (_WEIGHTED_SHARE * sum(fact_tokens[t] for t in hit) / total
                 + (1 - _WEIGHTED_SHARE) * len(hit) / len(fact_tokens))
        if figures:
            # A passage missing one of the fact's figures is weaker evidence than its word
            # overlap suggests: "Suite 1100" is not supported by "Suite 1200".
            score *= 0.5 + 0.5 * sum(1 for t in figures if t in toks) / len(figures)
        if pair:
            if score > best_pair[0]:
                best_pair = (score, passage)
        elif score > best_single[0] or (score == best_single[0] and best_single[1] and
                                        len(passage) < len(best_single[1])):
            best_single = (score, passage)
    if best_pair[1] and best_pair[0] >= best_single[0] + _PAIR_MARGIN:
        return best_pair
    return best_single


def _cap(text: str) -> str:
    if len(text) <= _PASSAGE_CAP:
        return text
    cut = text[:_PASSAGE_CAP].rfind(" ")
    return text[:cut if cut > 0 else _PASSAGE_CAP].rstrip() + " …"


def find_passage(index: _DocIndex, fact: dict, threshold: float = MATCH_THRESHOLD) -> dict:
    """`{"passage", "page", "score"}` for the best-supported passage, or `{"passage": None,
    "page": None, "score": best}` when nothing reaches `threshold`."""
    raw = tokens(fact.get("fact") or "")
    fact_tokens = {t: index.weight(t, strong) for t, strong in raw.items()}
    page = fact.get("page") if isinstance(fact.get("page"), int) and not isinstance(fact.get("page"), bool) else None
    best = (0.0, None, None)
    if page is not None and page in index.pages:
        score, passage = _score_page(index, page, fact_tokens)
        best = (score, passage, page)
        if passage is not None and score >= threshold:
            return {"passage": _cap(passage), "page": page, "score": round(score, 3)}
    if page is not None:
        others = [p for p in (page - 1, page + 1) if p in index.pages]
    else:
        others = sorted(index.pages)
    factor = _OFF_PAGE_FACTOR if page is not None else 1.0
    for p in others:
        score, passage = _score_page(index, p, fact_tokens)
        if score * factor > best[0]:
            best = (score * factor, passage, p)
    if best[1] is not None and best[0] >= threshold:
        return {"passage": _cap(best[1]), "page": best[2], "score": round(best[0], 3)}
    return {"passage": None, "page": None, "score": round(best[0], 3)}


def _has_quote(fact: dict) -> bool:
    return bool((fact.get("quote") or "").strip()) and fact.get("quote_verified") is not False


def locate(facts: list[dict], page_texts: dict[int, str], threshold: float = MATCH_THRESHOLD,
           force: bool = False) -> dict[str, int]:
    """Stamp a passage on every fact in `facts` (in place). A fact that already has
    `passage_method` keeps it unless `force`. Returns counts by method."""
    counts = {m: 0 for m in METHODS}
    index: _DocIndex | None = None
    for fact in facts:
        if not isinstance(fact, dict) or not (fact.get("fact") or "").strip():
            continue
        if fact.get("passage_method") in METHODS and not force:
            counts[fact["passage_method"]] += 1
            continue
        if _has_quote(fact):
            fact["passage"] = fact["quote"].strip()
            page = fact.get("quote_found_page") or fact.get("page")
            fact["passage_page"] = page if isinstance(page, int) else None
            fact["passage_method"] = "quote"
            fact["passage_score"] = 1.0
            counts["quote"] += 1
            continue
        if index is None:
            index = _DocIndex({p: t for p, t in page_texts.items() if t})
        found = find_passage(index, fact, threshold)
        if found["passage"]:
            fact["passage"] = found["passage"]
            fact["passage_page"] = found["page"]
            fact["passage_method"] = "matched"
            counts["matched"] += 1
        else:
            fact["passage"] = None
            fact["passage_page"] = None
            fact["passage_method"] = "unlocated"
            counts["unlocated"] += 1
        fact["passage_score"] = found["score"]
    return counts


def locate_passages(extraction: dict, page_texts: dict[int, str]) -> list[str]:
    """Post-flight step: stamp passages on `document.key_facts`. Never a gate; returns no
    warnings (an unlocated fact is shown in the app, not logged), but keeps post-flight's
    `-> list[str]` shape so a future warning has somewhere to go."""
    facts = extraction.get("document", {}).get("key_facts", [])
    if page_texts and isinstance(facts, list):
        locate(facts, page_texts, force=True)
    return []


# ── existing documents ───────────────────────────────────────────────────────────────────

_PAGE_MARKER_RE = re.compile(r"<!--\s*PAGE\s+(\d+)\s*-->")


def morgue_page_texts(vault: Path, record: dict) -> dict[int, str]:
    """A committed document's page text, from the full-text sibling the commit wrote next to the
    original in the morgue (`<!-- PAGE n -->` markers). `{}` when there is none."""
    morgue = record.get("morgue_path") if isinstance(record, dict) else None
    if not morgue:
        return {}
    path = (vault / morgue).with_suffix(".md")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    parts = _PAGE_MARKER_RE.split(text)
    if len(parts) == 1:
        return {1: text} if text.strip() else {}
    return {int(parts[i]): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def backfill(vault: Path, *, force: bool = False, shas: list[str] | None = None) -> dict:
    """Compute passages for committed documents whose staged extraction has facts without one
    (`watchdog locate-passages`). Rewrites only the passage fields of `.watchdog/extracted/
    <sha>.json`, atomically and under the registry lock; the model's own fields are untouched.
    Returns `{"documents", "updated", "skipped": [{sha, filename, reason}], "counts"}`."""
    from watchdog.pipeline.json_io import _read_json_or
    from watchdog.pipeline.write_vault import _registry_lock

    registry = vault / ".watchdog" / "registry"
    docs = _read_json_or(registry / "documents.json", {}, catch=(json.JSONDecodeError,)) \
        if (registry / "documents.json").exists() else {}
    totals = {m: 0 for m in METHODS}
    updated, skipped, seen = 0, [], 0
    wanted = set(shas) if shas else None
    with _registry_lock(registry):
        for sha, rec in sorted(docs.items()):
            if not isinstance(rec, dict) or (wanted is not None and sha not in wanted
                                             and not any(sha.startswith(w) for w in wanted)):
                continue
            seen += 1
            path = vault / ".watchdog" / "extracted" / f"{sha}.json"
            try:
                extraction = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                skipped.append({"sha": sha, "filename": rec.get("filename"),
                                "reason": "no saved extraction"})
                continue
            facts = (extraction.get("document") or {}).get("key_facts")
            if not isinstance(facts, list) or not facts:
                continue
            pending = force or any(isinstance(f, dict) and f.get("passage_method") not in METHODS
                                   for f in facts)
            if not pending:
                for f in facts:
                    if isinstance(f, dict) and f.get("passage_method") in METHODS:
                        totals[f["passage_method"]] += 1
                continue
            pages = morgue_page_texts(vault, rec)
            if not pages:
                skipped.append({"sha": sha, "filename": rec.get("filename"),
                                "reason": "no page text in the morgue"})
                continue
            counts = locate(facts, pages, force=force)
            for k, v in counts.items():
                totals[k] += v
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(extraction, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
            updated += 1
    return {"documents": seen, "updated": updated, "skipped": skipped, "counts": totals}

