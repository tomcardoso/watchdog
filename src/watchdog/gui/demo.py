"""`python -m watchdog.gui.demo <dir> [--home <fake-home>]` - build a populated demo vault.

The vault is a made-up investigation (a municipal procurement story set in the fictional city of
Port Calder; every person, company, body, address and court matter is invented) so the desktop app
has something realistic to show without anyone's real research. It is built by the real pipeline:
creating an investigation creates the vault, real PDF and image files are written to the staging area chew
would have used, and `orchestrate.run` / `orchestrate.finalize` do the extraction, commit,
reconciliation, synthesis, timeline and briefing work - with the model's answers canned. Nothing
calls a model or the network, and the same inputs give the same vault (apart from timestamps).

`--home` points HOME at a scratch directory first, so the project registry, config file and usage
database land there instead of in the real `~/.watchdog`.

The ingest is run as two batches, the way a reporter would: `add` for the first four documents, then
`dig` and `bark` separately for the other ten. That leaves two briefings, three usage records and
cross-batch contradictions and near-duplicates in the vault. Afterwards the vault is given the
loose ends a working investigation has: files waiting in `incoming/`, one failed document, context
files and a queued research URL.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import hashlib
import io
import json
import os
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from watchdog.vault_paths import context_dir, incoming_dir

PROJECT_NAME = "Port Calder Waterfront"
PROJECT_DESCRIPTION = (
    "How a city council bought a harbourfront lot at three times its appraised value and awarded a "
    "$48.6-million no-tender contract to a developer's subsidiary, and who was on both sides."
)

# The same investigation built inside the app as the sample new users explore (`ops/sample.py`,
# D303): named and described so that nobody mistakes it for real research.
SAMPLE_NAME = "Port Calder Waterfront (sample)"
SAMPLE_DESCRIPTION = ("A sample investigation for learning Watchdog; every person and company in it "
                      "is invented.")
SAMPLE_MARKER = "sample.json"     # in `.watchdog/`: this folder is the app's sample, safe to rebuild
SAMPLE_CONVERSIONS = "sample-conversions"   # in `.watchdog/`: the waiting files' prepared text (D307)

# The first ingest batch (`add`); everything else arrives in the second (`dig` + `bark`).
BATCH_ONE = {
    "news-release-pier-9-award.pdf",
    "capital-payment-register-2022.pdf",
    "foi-response-lot-14-appraisal.pdf",
    "contract-c-2022-041-pier-9-servicing.pdf",
}

CONTEXT_MD = """\
# Port Calder Waterfront - context

## What I'm investigating

I want to understand how Port Calder City Council came to buy 14 Dockside Road in February 2022, and why,
two months later, it awarded the $48.6-million Pier 9 marine servicing contract without a public tender.
I want to explore whether the developer Meridian Shoreline Developments, or companies connected to it,
stood on either side of these decisions, and what Council was told before each vote.

## Key questions I'm trying to answer

- Who owns 7714882 Holdings Ltd., the numbered company that sold the City the property?
- How did the price the City paid compare with the property's appraised value?
- Did any councillor have a connection to the companies involved, and was it disclosed?
- Does the contract price match what Council approved?
- What was the "time-critical" reason given for skipping a tender, and does the record support it?

## Entities I already know are relevant

- Meridian Shoreline Developments Inc. and its subsidiary Northgate Civil Works Ltd.
- Strathmore Public Affairs Inc., Meridian's registered lobbyist.
- Councillor Dana Whitcombe, who chairs the Planning and Procurement Committee.
- Leonard Pike, the City's Director of Procurement and Real Property.

## Documents I'm expecting or looking for

- The City Clerk's record of any conflict disclosure in April 2022.
- The unsevered appraisal of 14 Dockside Road and the "second valuation" staff e-mail.
- The Harbour Authority's record of the Pier 9 berth permits, including any renewal in 2022.
- The Northgate-Tideway subcontract.

## What I don't yet understand

- How the price rose between what the company paid for the property and what the City paid.
"""

WATCH_TERMS = ["Strathmore Public Affairs", "Marcus Teague", "410 Wharf Street", "7714882", "time-critical",
               "Blackwater Capital"]

CONTEXT_NOTE = """\
# Background - published coverage

Summary of "City skips tender for $48.6M Pier 9 contract", Port Calder Ledger, April 28, 2022.
A published news story, kept here as background, not as evidence.

- Council voted 7 to 3 on April 26, 2022 to award the Pier 9 marine servicing contract to Northgate
  Civil Works Ltd. without a public tender.
- City staff told Council the work was time-critical because the Pier 9 berth permits expire on
  September 30, 2022.
- The Harbour Authority, which issues the berth permits, declined to comment on their status.
- The story does not say who owns Northgate or whether it is related to Meridian Shoreline Developments.
- Worth requesting: the Harbour Authority's record of the Pier 9 berth permits, including any renewal.
"""

CONTEXT_TIMELINE = """\
Harbourfront timeline (working notes)

2019-06  Harbour Authority declares the Harbourfront Lands surplus
2021-04  7714882 Holdings buys 14 Dockside Road from the Kessler estate
2021-10  Strathmore registers to lobby for Meridian
2022-02  Council votes 8-3 to buy Lot 14
2022-04  Council votes 7-3 to award Pier 9 to Northgate
2023-06  City Auditor report
2023-11  Court sets the award aside
"""


# ── story helpers ───────────────────────────────────────────────────────────────────────────

def all_docs() -> list[dict]:
    from watchdog.gui.demo_docs_a import DOCS_A
    from watchdog.gui.demo_docs_b import DOCS_B
    docs = copy.deepcopy(DOCS_A + DOCS_B)
    for doc in docs:
        typo = doc.get("typo")
        if typo:       # a misspelling the source really contains, so an alias is extracted
            for page in doc["pages"]:
                for i, block in enumerate(page):
                    if block[0] in ("p", "small", "title") and typo[0] in block[1]:
                        page[i] = (block[0], block[1].replace(typo[0], typo[1]))
    return docs


def _letter_markdown(lines: list[str]) -> str:
    paragraphs: list[list[str]] = [[]]
    for line in lines:
        if line.strip():
            paragraphs[-1].append(line.strip())
        elif paragraphs[-1]:
            paragraphs.append([])
    return "\n\n".join(" ".join(p) for p in paragraphs if p)


def render_files(docs: list[dict], out_dir: Path) -> None:
    """Write each document's real file into `out_dir` and attach its `path` and per-page `md`."""
    from watchdog.gui import demo_pdf
    out_dir.mkdir(parents=True, exist_ok=True)
    for doc in docs:
        path = out_dir / doc["file"]
        if doc["kind"] == "docx":
            demo_pdf.write_docx(path, doc["title"], doc["paras"], doc.get("table"))
            doc["positions"] = {}
        elif doc["kind"] == "pdf":
            # The file's own creation date is the date on the document, as for a file saved when issued.
            created = "D:" + doc["date"].replace("-", "") + "090000-05'00'"
            doc["md"] = demo_pdf.write_pdf(path, doc["pages"], title=doc["title"],
                                           author=doc.get("author") or "", footer=doc.get("footer"),
                                           created=created)
            # A scanned page's OCR line positions, as pre-processing saves them (D289).
            doc["positions"] = demo_pdf.scan_pages(doc["pages"])
        else:
            doc["positions"] = {"1": demo_pdf.write_scanned_letter(path, doc["pages"][0])}
            doc["md"] = [_letter_markdown(doc["pages"][0])]
        doc["path"] = path


def entity_list(doc: dict) -> list[dict]:
    """The `entities` array a careful extraction of `doc` would return."""
    from watchdog.gui.demo_cast import ENT
    text = "\n".join(doc["md"]).lower()
    roles: dict[str, list[dict]] = defaultdict(list)
    for r in doc["roles"]:
        role = {"relationship": r["rel"], "target_id": r["tgt"], "page": r["page"]}
        if r["inferred"]:
            role["basis"] = "inferred"
        if r["date_range"]:
            role["date_range"] = r["date_range"]
        if r["tname"]:
            role["target_name"], role["target_type"] = r["tname"], r["ttype"]
        roles[r["src"]].append(role)
    out = []
    for eid in doc["ents"]:
        name, etype, aliases = ENT[eid]
        out.append({"id": eid, "name": name, "type": etype,
                    "aliases": [a for a in aliases if a.lower() in text], "roles": roles.get(eid, [])})
    return out


# Every fact in the story names its source sentence (`q`), but a real extraction gives a locator
# only when the wording matters (D170), so most facts arrive with just a page. The demo keeps the
# locator on every third fact and lets the rest be matched to their passage (D270).
LOCATOR_EVERY = 3


def extraction_for(doc: dict) -> dict:
    facts = []
    for i, f in enumerate(doc["facts"]):
        fact = {"fact": f["text"], "page": f["page"], "entities": f["ents"]}
        if f["inferred"]:
            fact["basis"] = "inferred"
        if f["date"]:
            fact["date"] = f["date"]
        if f["q"] and i % LOCATOR_EVERY == 0:
            fact["quote_locator"] = f["q"]
        facts.append(fact)
    out = {
        "entities": entity_list(doc),
        "document": {"title": doc["title"], "document_type": doc["dtype"], "date_of_document": doc["date"],
                     "summary": doc["summary"], "key_facts": facts},
        "morgue_entity_id": doc["morgue"],
        "scratchpad": doc["scratch"],
    }
    if doc.get("requests"):
        out["document_requests"] = doc["requests"]
    return out


def check_story(docs: list[dict]) -> list[str]:
    """Authoring checks: every fact's locator is on its page, every tag and role names a listed entity."""
    from watchdog.gui.demo_cast import ENT
    from watchdog.pipeline.quote_verify import _normalize
    problems = []
    for doc in docs:
        names = doc["file"]
        ids = set(doc["ents"])
        for e in ids - set(ENT):
            problems.append(f"{names}: unknown entity {e}")
        for f in doc["facts"]:
            if f["q"]:
                page_text = doc["md"][f["page"] - 1] if f["page"] <= len(doc["md"]) else ""
                if _normalize(f["q"]) not in _normalize(page_text):
                    problems.append(f"{names} p{f['page']}: locator not on page: {f['q']!r}")
            for e in f["ents"]:
                if e not in ids:
                    problems.append(f"{names}: fact tags {e} which is not in the document's entities")
        for r in doc["roles"]:
            for e in (r["src"], r["tgt"]):
                if e not in ids and not (e == r["tgt"] and r["tname"]):
                    problems.append(f"{names}: role uses {e} which is not in the document's entities")
    return problems


# People the canned reconcile model is not confident about when two documents share only their
# name: the corporate profile names Tomasz Wieczorek as director of the numbered company, the
# contracts as the developer's chief executive, and nothing in the facts ties the two roles
# together. That link is the story, and it is the reporter's to confirm.
UNSURE_SAME_NAME = {"tomasz-wieczorek"}


def cite(text: str, lines: list[str]) -> str:
    """Canned prose with each `{{phrase}}` replaced by the short citation (`[f:3a9c]`) of the one
    fact line in `lines` that contains the phrase, as a model citing its facts would (D283). A
    phrase no line contains is left out, as a model can cite only the facts it is shown (the early
    batch sees fewer); one that matches several lines is a mistake in the story data."""
    def ref(m: re.Match) -> str:
        phrase = m.group(2).lower()
        hits = {r.group(1) for ln in lines if phrase in ln.lower()
                for r in [re.match(r"\[(f:[0-9a-f:]+)\]", ln.lstrip("- "))] if r}
        if len(hits) > 1:
            raise RuntimeError(f"demo: citation {m.group(2)!r} matches {len(hits)} facts")
        return f"{m.group(1)}[{hits.pop()}]" if hits else ""
    return re.sub(r"([ \t]*)\{\{(.+?)\}\}", ref, text)


def _ledger_ref(ledger: str, slug: str, phrase: str | None) -> str | None:
    """The short id of the fact under document `slug` in a reconcile ledger that contains
    `phrase`, or None."""
    if not phrase:
        return None
    current = None
    for line in ledger.splitlines():
        head = re.match(r"\*\[\[documents/([^|\]]+)\|", line)
        if head:
            current = head.group(1)
            continue
        ref = re.match(r"- \[(f:[0-9a-f:]+)\]", line)
        if current == slug and ref and phrase.lower() in line.lower():
            return ref.group(1)
    return None


# ── the canned model ────────────────────────────────────────────────────────────────────────

class CannedModel:
    """Stands in for `model_client.acomplete_json`: answers each task from the story data."""

    def __init__(self, docs: list[dict], vault: Path, *, free: bool = False):
        self.docs = docs
        self.vault = vault
        # The app's sample (D303) records its calls at no cost under the backend "sample", so its
        # Usage reads $0.00 and says what answered; the developers' demo prices them like a run.
        self.free = free
        self.calls: dict[str, int] = defaultdict(int)
        self.unsynthesized: set[str] = set()

    def _doc_for(self, text: str) -> dict:
        for doc in self.docs:
            if "<!-- PAGE 1 -->\n\n" + doc["md"][0][:160] in text:
                return doc
        raise RuntimeError("demo: could not tell which document a prompt is about")

    # Each handler returns the parsed JSON the real task would.
    def classify(self, text: str) -> dict:
        return {"skill": self._doc_for(text)["skill"]}

    def extract(self, text: str) -> dict:
        doc = self._doc_for(text)
        if doc.get("fails"):
            # The illegible scan (D307): a prepared failure, so a retry ends where it started.
            raise RuntimeError("the text of this scan is illegible, so no facts could be read from it")
        return extraction_for(doc)

    def verify(self, text: str) -> dict:
        # The second read finds nothing missing: the prepared extraction is already complete.
        return {"missing_facts": []}

    def reconcile(self, text: str) -> dict:
        from watchdog.gui import demo_story
        # The instructions mention both headings too, so read from the last occurrence of each.
        head = text[text.rindex("CANDIDATE PAIRS (possible"):]
        pair_json, rest = head.split("\n\nSAME-NAME GROUPS (records", 1)
        group_json, ent_json = rest.split("\n\nENTITIES (each", 1)
        pairs = json.loads(pair_json.split("\n", 1)[1])
        groups = json.loads(group_json.split("\n", 1)[1])
        entities = json.loads(ent_json.split("\n", 1)[1])
        merges = []
        for a, b, keep, reason in demo_story.MERGES:
            for i, pair in enumerate(pairs):
                if {pair["a"]["id"], pair["b"]["id"]} == {a, b}:
                    merges.append({"pair": i, "keep_id": keep, "reason": reason})
        # A same-name pair the rules could not settle (D279) is a record the fold kept apart under
        # a fresh slug (`marcus-teague-2`). The story has one entity per cast id, so a careful model
        # reading both sides' facts would confirm it — except where the facts give it nothing to go
        # on (`UNSURE_SAME_NAME`), which leaves one "possible same person" in Review.
        for i, pair in enumerate(pairs):
            ids = sorted((pair["a"]["id"], pair["b"]["id"]), key=len)
            if ids[0] in UNSURE_SAME_NAME:
                continue
            if pair.get("same_name") and re.fullmatch(re.escape(ids[0]) + r"-\d+", ids[1]):
                merges.append({"pair": i, "keep_id": ids[0],
                               "reason": "The same person, described consistently in both documents."})
        # Records with the very same name come as a group (#755): every record of one cast id
        # (`marcus-teague`, `marcus-teague-2`, …) is one thing, unless it is an unsure one.
        answered = []
        for group in groups:
            by_base: dict[str, list[int]] = {}
            for m in group["members"]:
                by_base.setdefault(re.sub(r"-\d+$", "", m["id"]), []).append(m["member"])
            same, unsure = [], []
            for base, nums in sorted(by_base.items()):
                if len(nums) < 2:
                    continue
                if base in UNSURE_SAME_NAME:
                    unsure.append(nums)
                    continue
                keep = next((m["member"] for m in group["members"] if m["id"] == base), nums[0])
                same.append({"members": nums, "keep": keep,
                             "reason": "The same person, described consistently in both documents."})
            answered.append({"group": group["index"], "same": same, "unsure": unsure})
        ledger_ids = {e["entity_id"] for e in entities}
        # A document is "known" once it is committed to the vault or cited in this batch's claims.
        docs_path = self.vault / ".watchdog" / "registry" / "documents.json"
        committed = {d.get("document_note", "").removeprefix("documents/")
                     for d in json.loads(docs_path.read_text(encoding="utf-8")).values()}
        known = committed | set(re.findall(r"documents/([a-z0-9-]+)\|", text))
        found = []
        for c in demo_story.CONTRADICTIONS:
            if c["entity_id"] in ledger_ids and c["a_doc"] in known and c["b_doc"] in known:
                item = {k: c[k] for k in ("entity_id", "label", "a_value", "a_doc", "a_page",
                                          "b_value", "b_doc", "b_page")}
                ent = next(e for e in entities if e["entity_id"] == c["entity_id"])
                ledger = f"{ent.get('new_facts') or ''}\n{ent.get('stored_facts') or ''}"
                for side in ("a", "b"):
                    item[f"{side}_fact"] = _ledger_ref(ledger, c[f"{side}_doc"], c.get(f"{side}_cite"))
                found.append(item)
        return {"merges": merges, "groups": answered, "contradictions": found}

    def synthesis(self, text: str) -> dict:
        from watchdog.gui import demo_story
        bundle = json.loads(text.split("Entities:\n", 1)[1])
        out = []
        for ent in bundle:
            canned = demo_story.SYNTHESIS.get(ent["entity_id"])
            later = demo_story.SYNTHESIS_LATER.get(ent["entity_id"])
            if later and any(later[0] in ln.lower() for ln in ent["facts"]):
                canned = later[1:]
            if canned is None:
                self.unsynthesized.add(ent["entity_id"])
                continue
            item = {"entity_id": ent["entity_id"], "summary": cite(canned[0], ent["facts"])}
            if canned[1]:
                item["analysis"] = cite(canned[1], ent["facts"])
            out.append(item)
        return {"entity_syntheses": out}

    @staticmethod
    def _events(text: str, header: str | None = None) -> list[tuple[int, str]]:
        body = text.split(header, 1)[1] if header else text.split("Events:\n", 1)[1]
        return [(int(m.group(1)), m.group(2)) for m in re.finditer(r"^\[(\d+)\] (.*)$", body, re.M)]

    def timeline_dedup(self, text: str) -> dict:
        from watchdog.gui import demo_story
        events = self._events(text)
        clusters: list[list[int]] = []
        for i, ev in events:
            low = re.sub(r"\s+\(p\.\d+\)$", "", ev).lower()
            home = None
            for cl in clusters:
                other = re.sub(r"\s+\(p\.\d+\)$", "", dict(events)[cl[0]]).lower()
                same_text = other == low
                same_group = any(any(p in other for p in g) and any(p in low for p in g)
                                 for g in demo_story.DEDUP_GROUPS)
                if same_text or same_group:
                    home = cl
                    break
            if home is None:
                clusters.append([i])
            else:
                home.append(i)
        return {"groups": [{"keep": cl[0], "duplicates": cl[1:]} for cl in clusters if len(cl) > 1]}

    def timeline_precision(self, text: str) -> dict:
        from watchdog.gui import demo_story
        coarse_part, precise_part = text.split("DAY-DATED events", 1)
        coarse = self._events(coarse_part, "MONTH-DATED events")
        precise = self._events(precise_part, ":\n")
        matches = []
        for cphrase, pphrase in demo_story.PRECISION_PAIRS:
            for ci, ctext in coarse:
                for pi, ptext in precise:
                    if cphrase in ctext.lower() and pphrase in ptext.lower():
                        matches.append({"coarse": ci, "precise": pi})
        return {"matches": matches}

    def request_dedup(self, text: str) -> dict:
        from watchdog.gui import demo_story
        items = self._events(text, "Open document requests:\n")
        groups = []
        for phrases in demo_story.REQUEST_GROUPS:
            hit = [i for i, t in items if any(p in t.lower() for p in phrases)]
            if len(hit) > 1:
                groups.append({"keep": hit[0], "duplicates": hit[1:]})
        return {"groups": groups}

    def briefing(self, text: str) -> dict:
        from watchdog.gui import demo_story
        rows_text = text.split("RESULTS:\n", 1)[1].split("\n\nNEAR-DUP ALERTS:", 1)[0]
        which = ("waiting" if any(d["file"] in rows_text for d in waiting_docs())
                 else "main" if "council-minutes" in text else "early")
        canned = demo_story.BRIEFINGS[which]
        rows = json.loads(text.split("RESULTS:\n", 1)[1].split("\n\nNEAR-DUP ALERTS:", 1)[0])
        lines = [ln for r in rows for ln in r.get("key_facts") or [] if isinstance(ln, str)]
        return {k: cite(v, lines) if isinstance(v, str) else [cite(x, lines) for x in v]
                for k, v in canned.items()}

    def relationship_labels(self, text: str) -> dict:
        from watchdog.gui import demo_story
        body = text.split("\nPairs:\n", 1)[1]
        out = []
        for block in re.split(r"\n(?=P\d+: )", body.strip()):
            head = re.match(r"P(\d+): ", block)
            if not head:
                continue
            labels = {m.group(2).lower(): int(m.group(1))
                      for m in re.finditer(r'^  (\d+)\. "(.*)" \(', block, re.M)}
            groups = []
            for words in demo_story.RELATIONSHIP_GROUPS:
                nums = [labels[w] for w in words if w in labels]
                if len(nums) > 1:
                    groups.append({"labels": nums, "canonical": nums[0],
                                   "reason": "Both describe the same lawyer acting for the same client."})
            if groups:
                out.append({"pair": int(head.group(1)), "groups": groups})
        return {"pairs": out}

    def request_sources(self, text: str) -> dict:
        from watchdog.gui import demo_story
        answers = []
        for m in re.finditer(r'^(\d+)\. "(.*?)" \(', text, re.M):
            label = m.group(2)
            for phrase, canonical in demo_story.SOURCE_GROUPS:
                if phrase in label.lower():
                    label = canonical
            answers.append({"item": int(m.group(1)), "label": label})
        return {"answers": answers}

    def role_targets(self, text: str) -> dict:
        # The sample's relationship targets without a record (a surety, a council committee) are
        # leads by design: nothing in the investigation is them, so a careful model matches none.
        items = re.findall(r"^(\d+)\. target id: ", text, re.M)
        return {"answers": [{"item": int(n), "match": "", "kind": ""} for n in items]}

    async def __call__(self, *, task, prompt, schema, model=None, backend=None, max_retries=1,
                       effort=None):
        from watchdog import model_client
        text = model_client._flatten_prompt(prompt)
        handlers = {"classify": self.classify, "extract": self.extract, "verify": self.verify,
                    "reconcile": self.reconcile,
                    "entity-synthesis": self.synthesis, "timeline-dedup": self.timeline_dedup,
                    "timeline-precision": self.timeline_precision, "request-dedup": self.request_dedup,
                    "briefing": self.briefing, "relationship-labels": self.relationship_labels,
                    "date-resolve": lambda text: {"answers": []},
                    "role-targets": self.role_targets,
                    # Every fact in the sample tags an entity its document defines (`check_story`).
                    "fact-tags": lambda text: {"answers": []},
                    "request-sources": self.request_sources}
        handler = handlers.get(task)
        if handler is None:
            raise RuntimeError(f"demo: no canned answer for task {task!r}")
        self.calls[task] += 1
        parsed = copy.deepcopy(handler(text))
        try:
            model_id = model_client.resolve_model_id(model) if model else "claude-haiku-5-5"
        except Exception:  # noqa: BLE001
            model_id = "claude-haiku-5-5"
        in_tok = max(60, len(text) // 4)
        out_tok = max(20, len(json.dumps(parsed)) // 4)
        usage = {"input_tokens": in_tok, "output_tokens": out_tok}
        try:
            cost = model_client._api_cost(model_id, usage)
        except Exception:  # noqa: BLE001
            cost = None
        if cost is None:
            cost = round(in_tok * 3e-6 + out_tok * 15e-6, 6)
        latency = round(0.6 + (out_tok / 160), 2)
        if self.free:
            return model_client.ModelResult(parsed=parsed, text="", model="sample", backend="sample",
                                            auth_mode=None, usage=usage, cost_usd=0.0, latency_s=0.0)
        return model_client.ModelResult(parsed=parsed, text="", model=model_id, backend="claude-api",
                                        auth_mode="api-key", usage=usage, cost_usd=cost,
                                        latency_s=latency)


# ── building the vault ──────────────────────────────────────────────────────────────────────

def conversion(doc: dict) -> dict:
    """What pre-processing reads from `doc`'s rendered file: its sha, pages and metadata, the
    shape `preprocess` returns (no staging path, near-duplicate matches or sidecar yet)."""
    from watchdog.pipeline import file_metadata
    data = doc["path"].read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    pages = [{"page": i, "markdown": md} for i, md in enumerate(doc["md"], 1)]
    result = {
        "filename": doc["file"], "sha256": sha, "page_count": len(pages), "pages": pages,
        "metadata": ({"ocr_used": True, "garbled_detected": False, "source_type": "docling",
                      "chunked": False} if doc.get("ocr") or doc["kind"] == "docx" else
                     {"ocr_used": False, "garbled_detected": False, "source_type": "direct_text",
                      "chunked": False}),
        "file_metadata": file_metadata.extract(doc["path"]),
    }
    if doc["kind"] == "docx":
        result["metadata"]["ocr_used"] = False
    scanned = sorted(int(n) for n in doc.get("positions") or {})
    if scanned and doc["kind"] == "pdf":
        result["metadata"].update(ocr_used=True, source_type="docling", ocr_pages=scanned)
    return result


def _stage_chewed(vault: Path, docs: list[dict]) -> None:
    """Do what chew does for each document: stage the file, compute its near-duplicate matches
    against the vault and the rest of the batch, and write the queue descriptor."""
    from watchdog.pipeline import sidecar, text_positions
    from watchdog.pipeline.preprocess_batch import NearDupIndex, _compute_near_dup
    index = NearDupIndex.from_vault(vault)
    for doc in docs:
        result = conversion(doc)
        sha = doc["sha"] = result["sha256"]
        staging = vault / ".watchdog" / "staging" / sha
        staging.mkdir(parents=True, exist_ok=True)
        (staging / doc["file"]).write_bytes(doc["path"].read_bytes())
        result["source_path"] = f".watchdog/staging/{sha}/{doc['file']}"
        text_positions.write(vault, sha, text_positions.block(doc.get("positions") or {}, "demo"))
        result["near_dup"] = _compute_near_dup(result, vault, index=index)
        result["document_type"] = None
        raw_sidecar = f"source: {doc['source']}\nobtained: {doc['obtained']}\n"
        result["sidecar"], _ = sidecar.filter_and_render(raw_sidecar)
        (vault / ".watchdog" / "queue" / f"{sha}.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8")


def waiting_docs() -> list[dict]:
    """The two files left waiting in `incoming/`, with prepared answers (D307)."""
    from watchdog.gui.demo_docs_waiting import WAITING
    return copy.deepcopy(WAITING)


def failed_doc() -> dict:
    from watchdog.gui.demo_docs_waiting import FAILED
    return copy.deepcopy(FAILED)


def _loose_ends(vault: Path) -> list[dict]:
    """The state a vault is in mid-investigation: unchewed files, a failure, context, research.
    Returns the waiting files' story entries, rendered into `incoming/`."""
    from watchdog.pipeline import research
    from watchdog.pipeline import orchestrate

    waiting = waiting_docs()
    render_files(waiting, incoming_dir(vault))

    context = context_dir(vault)
    (context / "ledger-story-pier-9-award.md").write_text(CONTEXT_NOTE, encoding="utf-8")
    (context / "harbourfront-timeline.txt").write_text(CONTEXT_TIMELINE, encoding="utf-8")

    sha = FAILED_SHA
    failed = vault / ".watchdog" / "queue" / "_failed"
    failed.mkdir(parents=True, exist_ok=True)
    (failed / f"{sha}.json").write_text(json.dumps({
        "filename": "scanned-memo-illegible.pdf", "sha256": sha, "page_count": 2,
        "pages": [{"page": 1, "markdown": "Ihe C1ty of P0rt Ca1der  --  rnerno  --  [illegible]"},
                  {"page": 2, "markdown": "[illegible]"}],
        "metadata": {"ocr_used": True, "garbled_detected": True, "source_type": "docling", "chunked": False},
        "file_metadata": {}, "near_dup": {"near_duplicates": [], "top_similarity": 0.0},
        "source_path": f".watchdog/staging/{sha}/scanned-memo-illegible.pdf", "sidecar": None,
    }, ensure_ascii=False), encoding="utf-8")
    orchestrate._log(vault, "START scanned-memo-illegible.pdf")
    orchestrate._log(vault, "FAILED scanned-memo-illegible.pdf: post-flight rejected: document.key_facts is "
                            "empty despite 517 words of source text - this looks like a failed or skipped "
                            "extraction, not a genuinely fact-free document")

    queue = research.queue_path(vault)
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text(research.serialize_worklist([{
        "url": "https://www.portcalder.example/council/agendas/2022-04-26-agenda-package.pdf",
        "title": "Council agenda package, April 26, 2022 (staff report CR-2022-029)",
        "source_type": "government", "relevance": "Attachments to the Pier 9 award report"}]),
        encoding="utf-8")
    return waiting


FAILED_SHA = hashlib.sha256(b"scanned-memo-illegible.pdf").hexdigest()

REPORTER = "Jordan Ellis"   # the fictional reporter whose checks the demo's ledger records

# (file, fact index in the story, status, note): the checks the demo reporter has made so far.
MARKS = [
    ("council-minutes-2022-02-08.pdf", 0, "verified", None),
    ("council-minutes-2022-02-08.pdf", 1, "verified", None),
    ("council-minutes-2022-02-08.pdf", 2, "verified", "Matches the report number in the agenda index."),
    ("parcel-register-14-dockside-road.pdf", 0, "verified", None),
    ("contract-c-2022-041-pier-9-servicing.pdf", 0, "verified", None),
    ("capital-payment-register-2022.pdf", 0, "disputed",
     "The register's running total does not add up to this figure. Asked Finance for the ledger."),
    ("news-release-pier-9-award.pdf", 0, "unverifiable",
     "The release is no longer on the City's site and the archived copy is undated."),
]


def _verification(vault: Path, docs: list[dict]) -> None:
    """Record the demo reporter's checks through the ledger's own writer."""
    from watchdog.pipeline import verification
    by_file = {d["file"]: d for d in docs}
    reg = json.loads((vault / ".watchdog" / "registry" / "documents.json").read_text(encoding="utf-8"))
    sha_for = {rec["filename"]: sha for sha, rec in reg.items()}
    for file, idx, status, note in MARKS:
        sha = sha_for[file]
        facts = verification.document_facts(vault, sha, reg[sha])
        want = by_file[file]["facts"][idx]["text"]
        fid = next(i for i, f in zip(verification.fact_ids(sha, facts), facts) if f["fact"] == want)
        verification.mark(vault, fid, status, note=note, by=REPORTER)


def build(target: Path, *, verbose: bool = False, sample: bool = False,
          on_step: "Callable[[str], None] | None" = None) -> Path:
    """Build the demo vault at `target` (which must not exist) and return its path.

    With `sample` it is the app's sample investigation (D303), built in the user's own Watchdog
    folder: named and described as a sample, marked so a rebuild can tell it apart, listed with
    Obsidian like any investigation, its model calls recorded at no cost and kept out of the usage
    database, and the settings and folder-access files left alone (the app owns those). Without
    it, the developers' demo: a scratch HOME's settings are written for the screenshots.
    `on_step` is told each phase as it starts."""
    from watchdog import config, model_client, obsidian, projects, telemetry_db
    from watchdog.ops import projects as projects_op
    from watchdog.pipeline import orchestrate, watchlist

    step = on_step or (lambda _text: None)
    target = target.resolve()
    if target.exists():
        raise SystemExit(f"Error: {target} already exists.")

    step("Writing the sample documents")
    docs = all_docs()
    work = target.parent / f".{target.name}-demo-build"
    shutil.rmtree(work, ignore_errors=True)
    render_files(docs, work / "files")
    problems = check_story(docs)
    if problems:
        raise SystemExit("demo story is inconsistent:\n  " + "\n  ".join(problems))

    log = sys.stdout if verbose else io.StringIO()

    # The vault itself, through the real operation, created in the work folder and moved into
    # place; Obsidian is told about it only at its final path (the demo never tells it).
    parent = work / "new"
    real_register = obsidian.register
    obsidian.register = lambda _vault: None
    try:
        made = projects_op.create(SAMPLE_NAME if sample else PROJECT_NAME,
                                  description=SAMPLE_DESCRIPTION if sample else PROJECT_DESCRIPTION,
                                  parent=parent)
    finally:
        obsidian.register = real_register
    slug = made.name
    shutil.move(str(made), str(target))
    listed = projects.load()
    listed[slug]["path"] = str(target)
    if sample:
        listed[slug]["sample"] = True
    projects.save(listed)
    vault = target

    if not sample:
        config.home().mkdir(parents=True, exist_ok=True)
        (config.home() / "config.json").write_text(json.dumps(
            {"projects_dir": str(target.parent), "chunk_workers": "auto", "chew_workers": "auto",
             "reporter_name": REPORTER}, indent=2) + "\n")
        # Allow the demo's folder, as the app would after the user chose it (gui/src/main/access.ts).
        (config.home() / "access.json").write_text(json.dumps({"version": 1, "folders": [
            {"path": str(target.parent.resolve()), "label": "demo investigations",
             "granted": datetime.now(timezone.utc).isoformat()}]}, indent=2) + "\n")

    (vault / "context.md").write_text(CONTEXT_MD, encoding="utf-8")
    watchlist.add_terms(vault, WATCH_TERMS)

    batch_one = [d for d in docs if d["file"] in BATCH_ONE]
    batch_two = [d for d in docs if d["file"] not in BATCH_ONE]
    canned = CannedModel(docs, vault, free=sample)
    real_call = model_client.acomplete_json
    real_telemetry = telemetry_db.enabled
    model_client.acomplete_json = canned
    if sample:
        telemetry_db.enabled = lambda: False
    try:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            step("Adding the first four documents")
            _stage_chewed(vault, batch_one)
            asyncio.run(orchestrate.run(vault, extract_model="haiku"))
            step("Adding the other ten documents")
            _stage_chewed(vault, batch_two)
            asyncio.run(orchestrate.run(vault, extract_model="haiku", skip_finalize=True))
            step("Post-processing: entities, contradictions, the timeline and the briefing")
            asyncio.run(orchestrate.finalize(vault))
    finally:
        model_client.acomplete_json = real_call
        telemetry_db.enabled = real_telemetry

    step("Recording checks and loose ends")
    _verification(vault, docs)
    waiting = _loose_ends(vault)
    shutil.rmtree(work, ignore_errors=True)
    if sample:
        # Every file the sample has a prepared answer for (D307), by content: the fourteen built
        # in, the two waiting and the failed one. The waiting files' conversions are kept too, so
        # pre-processing them in the app gives exactly the text the prepared answers were written on.
        prepared = {d["sha"]: d["file"] for d in docs}
        prepared[FAILED_SHA] = failed_doc()["file"]
        conv_dir = vault / ".watchdog" / SAMPLE_CONVERSIONS
        conv_dir.mkdir(parents=True, exist_ok=True)
        for d in waiting:
            c = conversion(d)
            prepared[c["sha256"]] = d["file"]
            (conv_dir / f"{c['sha256']}.json").write_text(json.dumps(c, ensure_ascii=False),
                                                          encoding="utf-8")
        (vault / ".watchdog" / SAMPLE_MARKER).write_text(json.dumps(
            {"sample": "port-calder", "built": datetime.now(timezone.utc).isoformat(),
             "prepared": prepared}, indent=1) + "\n", encoding="utf-8")
        obsidian.register(vault)
    if canned.unsynthesized and not sample:
        print("demo: entities with no canned synthesis: " + ", ".join(sorted(canned.unsynthesized)),
              file=sys.stderr)
    return vault


def summarize(vault: Path) -> dict:
    """Counts a caller (or a test) can check the vault against."""
    reg = vault / ".watchdog" / "registry"
    entities = json.loads((reg / "entities.json").read_text(encoding="utf-8"))
    documents = json.loads((reg / "documents.json").read_text(encoding="utf-8"))
    by_type: dict[str, int] = defaultdict(int)
    for e in entities.values():
        by_type[e["type"]] += 1
    return {
        "documents": len(documents), "entities": len(entities), "entity_types": dict(by_type),
        "contradictions": sum(len(e.get("contradictions") or []) for e in entities.values()),
        "briefings": sorted(p.name for p in (vault / "briefings").glob("20*.md")),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m watchdog.gui.demo",
                                 description="Build a populated, fictional demo investigation vault.")
    ap.add_argument("dir", help="where to create the vault (must not exist)")
    ap.add_argument("--home", help="use this directory as HOME, so the project registry, config and usage "
                                   "database stay out of the real ~/.watchdog")
    ap.add_argument("--verbose", action="store_true", help="show the pipeline's own output")
    args = ap.parse_args(argv)
    if args.home:
        home = Path(args.home).resolve()
        home.mkdir(parents=True, exist_ok=True)
        # Before any watchdog module is imported: some paths under ~/.watchdog are fixed at import.
        os.environ["HOME"] = os.environ["USERPROFILE"] = str(home)
    vault = build(Path(args.dir), verbose=args.verbose)
    info = summarize(vault)
    print(f"Demo vault: {vault}")
    print(f"  {info['documents']} documents, {info['entities']} entities {info['entity_types']}, "
          f"{info['contradictions']} contradictions, {len(info['briefings'])} briefings")
    if args.home:
        print(f"  home: {os.environ['HOME']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
