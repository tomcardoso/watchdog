"""Check that every answer-key quote is really on the page its entry cites.

A key quote is the evidence for a key item, so a quote that isn't where the key
says it is — or isn't in the document at all — is a defect in the KEY. Left
unchecked it reads as evidence about the pipeline: an arm gets marked down for
missing something the key pointed at the wrong page, or a hand review is spent
looking for text that was never there. Running this before a freeze keeps that
class of error out of the numbers entirely.

    python3 benchmarks/verify_keys.py                      # newest run's chew
    python3 benchmarks/verify_keys.py --pages <dir>        # a specific one
    python3 benchmarks/verify_keys.py --keys <dir>

Exits non-zero if anything fails, so it can gate a freeze.

WHAT COUNTS AS "ON THE PAGE"
Not string equality. The chew is a machine conversion and diverges from the PDF
in ways that are artifacts of conversion, not disagreements about content, so
`normalize` removes exactly those and nothing else:

  * HTML entities        the chew emits `&amp;` where the page reads `&`
  * quote marks          a defined term prints as "Initial Order" and converts
                         to `' Initial Order '` — space-padded, different glyph
  * hyphens              a word broken across a line loses its hyphen, so
                         `2019-20` comes back as `201920` and `one-time` as
                         `onetime`. Keeping hyphens fails a correctly copied
                         quote over where the line happened to wrap.
  * all whitespace       discarded outright rather than collapsed: dropping the
                         quote marks above leaves stray spaces, and the chew
                         pads words unpredictably (`On  February  1,`)

Discarding whitespace sounds reckless and isn't: applied to both sides, a
hundred-character despaced span does not match by accident. What it does mean
is that this checks the presence of the text, not its typography.

A `quote` may be a single string or a LIST of spans, and the list is not a
convenience — it is how the key represents text the conversion does not hold
contiguously (a sentence over a page break, a table row scattered across
columns). Every span must be found, and the spans may sit on different pages of
the entry's cite: `pages: [2, 3]` with two spans means one on each.

WHAT ORDER IS AND ISN'T ENFORCED — read this before trusting a green result.
Within ONE span, parts separated by an ellipsis must appear in the order the
quote puts them, or "for the period ... Payroll & Benefits" would verify against
a page saying the reverse. ACROSS the elements of a list, order is deliberately
NOT enforced, because a list exists precisely where the conversion's order
differs from the document's reading order. The clearest case is a two-column
court header: a person reads "THE HONOURABLE CHIEF JUSTICE MORAWETZ" across the
page, but the conversion reads down the columns and interleaves it with the
date beside it. Requiring the key to match conversion order would fail a
correct quote, and requiring it to match reading order would fail a correct
table row.

The honest consequence: a multi-span quote establishes that each piece really
is on the cited page. It does NOT establish that the pieces belong together —
that Morawetz is the judge who sat on 17 March rather than two true facts about
one page. The `fact`/`item` field is what asserts the relationship, and only a
human reading the page can vouch for it. This tool exists to stop a key citing
text that isn't there; it cannot certify the claim built on top of it.
"""
import argparse
import glob
import json
import os
import re
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_KEYS = os.path.join(HERE, "keys")

# Run from a checkout without the package installed — this gates a key freeze,
# so it must not need a working install to answer.
_SRC = os.path.join(os.path.dirname(HERE), "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from watchdog.pipeline.quote_verify import _normalize  # noqa: E402


def normalize(s):
    """Strip everything that is an artifact of conversion, keep the text.

    Shared with the pipeline's own quote checking (`quote_verify._normalize`)
    rather than reimplemented here. These rules were expensive to get right —
    hyphens lost where a word breaks across a line, HTML entities, quote glyphs
    and their padding, accents — and two copies would drift, which is how a key
    could start passing its own checker while failing the pipeline's.

    `collapse_spaces=False` is the one deliberate difference, and it is about
    the input rather than the policy: this reads CHEWED MARKDOWN, where the
    conversion splits words with spaces ("WEDNESDAY, THE 17 th"), which no
    amount of collapsing reconciles with the page's printed "17th".
    """
    return _normalize(str(s or ""), collapse_spaces=False)


def cited_pages(entry):
    """The entry's cited pages. Keys spell this field BOTH `page` and `pages`;
    an audit that reads only one of them reports correct entries as uncited."""
    v = entry.get("page")
    if v is None:
        v = entry.get("pages")
    if isinstance(v, int):
        return [v]
    if isinstance(v, str):
        return [int(t) for t in re.findall(r"\d+", v)]
    if isinstance(v, (list, tuple)):
        return [n for item in v for n in cited_pages({"page": item})]
    return []


def spans_of(entry):
    q = entry.get("quote")
    if q is None:
        return []
    return [str(x) for x in q] if isinstance(q, (list, tuple)) else [str(q)]


_ELLIPSIS = re.compile(r"\s*(?:…|\.\.\.)\s*")


def needles(span):
    """A span's searchable parts, splitting an ELIDED quote at its ellipsis.

    Some key quotes keep both ends of a sentence and cut the middle:
    `"the balance was $54.7 million … designated to support the endowments"`.
    The elided words are still in the page text, so the whole span will never
    be found as one run — each kept part has to be located separately. This
    mirrors what `quote_verify.verify_quote` does for model-supplied quotes
    (#630); a key checker that lacked it would report accurate quotes as
    missing, which is precisely the false alarm this tool exists to prevent.
    """
    return [p for p in _ELLIPSIS.split(span) if normalize(p)]


def _page_holding(parts, norm_pages, order):
    """First page in `order` that carries every part, in sequence.

    Order matters for an elided quote: the kept fragments must appear in the
    document in the order the quote puts them, or the "quote" is two unrelated
    phrases that happen to share a page.
    """
    for n in order:
        text = norm_pages[n]
        at = 0
        for part in parts:
            found = text.find(part, at)
            if found < 0:
                break
            at = found + len(part)
        else:
            return n
    return None


def _locate_across_pages(parts, norm_pages):
    """Pages carrying each part, reading the document strictly forward.

    For an elision that crosses a page break. Progress is one-way — once a part
    is consumed at some offset, the next part is only looked for after it — so
    this cannot rescue parts that appear in the wrong order. That matters: a
    per-part search with no ordering constraint would report "for the period …
    Payroll & Benefits" as a good quote when the document says the reverse.
    """
    pages = sorted(norm_pages)
    out, pi, at = [], 0, 0
    for part in parts:
        while pi < len(pages):
            found = norm_pages[pages[pi]].find(part, at)
            if found >= 0:
                out.append(pages[pi])
                at = found + len(part)
                break
            pi += 1
            at = 0
        else:
            return None
    return out


def check_entry(entry, pages):
    """-> (verdict, {span: page or None}).

    `pages` maps page number -> raw markdown. Verdicts:
      ok        every span sits on one of the cited pages
      offpage   every span located, but at least one is not on a cited page
      fail      at least one span is nowhere in the document
      nocite    spans located but the entry cites no page at all

    A span is located on ONE page where possible. Failing that, its parts are
    located individually, because a quote can legitimately be elided across a
    page break — the key's own `pages: [2, 3]` entries are exactly that.

    Each span in a list is located independently of the others; see the module
    docstring for why order is enforced inside a span but not across a list.
    """
    cited = set(cited_pages(entry))
    norm_pages = {n: normalize(md) for n, md in pages.items()}
    order = sorted(cited & set(norm_pages)) + sorted(set(norm_pages) - cited)
    where, off, missing = {}, False, False

    for span in spans_of(entry):
        parts = [normalize(p) for p in needles(span)]
        if not parts:
            continue
        hit = _page_holding(parts, norm_pages, order)
        if hit is None:
            # No single page holds the whole span — an elision may cross a page
            # break, so read forward through the document instead. Still
            # order-preserving; see `_locate_across_pages`.
            found = _locate_across_pages(parts, norm_pages)
            if found is None:
                where[span] = None
                missing = True
                continue
            hit = found[0]
            off = off or any(f not in cited for f in found)
        where[span] = hit
        if hit not in cited:
            off = True

    if missing:
        return "fail", where
    if not cited:
        return "nocite", where
    return ("offpage" if off else "ok"), where


def load_chew(pages_dir):
    """sha256 -> {"filename": str, "pages": {page_no: markdown}}."""
    out = {}
    for f in sorted(glob.glob(os.path.join(pages_dir, "*.json"))):
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
        if not d.get("sha256"):
            continue
        out[d["sha256"]] = {
            "filename": d.get("filename", os.path.basename(f)),
            "pages": {p["page"]: (p.get("markdown") or "") for p in d.get("pages") or []},
        }
    return out


def newest_pages_dir():
    runs = os.path.join(HERE, "runs")
    dirs = sorted(d for d in glob.glob(os.path.join(runs, "*", "pages"))
                  if os.path.isdir(d))
    return dirs[-1] if dirs else None


def verify(keys_dir, pages_dir):
    """-> (totals, problems). `problems` is a list of (slug, id, verdict, where)."""
    chew = load_chew(pages_dir)
    totals = {"ok": 0, "offpage": 0, "fail": 0, "nocite": 0, "no_quote": 0}
    problems, skipped = [], []

    for path in sorted(glob.glob(os.path.join(keys_dir, "*.yaml"))):
        slug = os.path.basename(path)[:-5]
        with open(path, encoding="utf-8") as fh:
            key = yaml.safe_load(fh)
        sha = (key.get("document") or {}).get("sha256")
        doc = chew.get(sha)
        if not doc:
            skipped.append(slug)
            continue
        for section in ("facts", "must_not_miss"):
            for entry in key.get(section) or []:
                if not spans_of(entry):
                    totals["no_quote"] += 1
                    continue
                verdict, where = check_entry(entry, doc["pages"])
                totals[verdict] += 1
                if verdict != "ok":
                    problems.append((slug, f"{section}:{entry.get('id')}",
                                     verdict, cited_pages(entry), where))
    return totals, problems, skipped


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--keys", default=DEFAULT_KEYS, help="directory of key YAMLs")
    ap.add_argument("--pages", default=None,
                    help="a run's pages/ directory (default: the newest under runs/)")
    args = ap.parse_args(argv)

    pages_dir = args.pages or newest_pages_dir()
    if not pages_dir or not os.path.isdir(pages_dir):
        sys.exit("no chewed pages found — pass --pages <run>/pages "
                 "(benchmarks/runs/ is gitignored, so this needs a local run)")

    totals, problems, skipped = verify(args.keys, pages_dir)
    print(f"chew: {pages_dir}")
    print("  " + "  ".join(f"{k}={v}" for k, v in totals.items()))
    for slug in skipped:
        print(f"  SKIPPED {slug}: its document is not in this chew")
    for slug, qid, verdict, cited, where in problems:
        print(f"\n  {verdict.upper()} {slug} {qid} (cites {cited or 'nothing'})")
        for span, page in where.items():
            mark = f"p{page}" if page else "NOT FOUND"
            print(f"    {mark:>10}  {span[:90]!r}")

    bad = totals["fail"] + totals["offpage"] + totals["nocite"]
    print(f"\n{'FAILED' if bad else 'OK'}: {bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
