"""Precision scoring for the facts the verification pass adds (#535, step 3).

Not a pytest module — a standalone benchmark tool, run in two phases against a benchmark vault
extracted with `--verify`:

    # 1. build the judging packets (free, local)
    ~/.local/pipx/venvs/watchdog-intel/bin/python benchmarks/verifier_precision.py build \\
        benchmarks/.vaults/bench-ex-gpt-luna-verify --out /tmp/verifier-judge

    # 2. after a judge has written judgment-<doc>.json beside each packet
    ~/.local/pipx/venvs/watchdog-intel/bin/python benchmarks/verifier_precision.py aggregate \\
        /tmp/verifier-judge

**Why this exists at all.** The recall side of the verifier is already measurable — `score_arms.py`
and the qualitative judge both score whether a key item was captured, and a pass that only adds
facts can only move that number up. Precision is the number that can move the wrong way and stay
invisible: a recall-tuned gap-finder produces restatements and true-but-worthless detail, and
noise in the fact ledger costs the reporter reading it. The verifier does not ship on a recall
win alone.

**Why a judge and not the anchor scorer.** `score_arms.py` matches numeric anchors, which settles
"is this figure real" but says nothing about "is this fact worth a reporter's attention" — and the
restatement failure mode is invariably *true*. Only about a third of key items carry an anchor at
all. The grades below are qualitative by necessity.

Each packet is self-contained: the added facts, the facts the extractor already had (the
restatement reference), and the document's full page text (the grounding reference). A judge
never needs the PDF, the vault, or this repo. There is no arm blinding — unlike the
2026-07-29 qualitative run, this grades one arm's output against the source rather than ranking
arms against each other, so there is nothing an arm label could bias.

Grades, one per added fact:

  `grounded_material`  supported by the document text AND worth a reporter's attention —
                       something not already in EXISTING_FACTS in any wording.
  `grounded_trivial`   true and supported, but a restatement of an existing fact, a
                       generalization of one, or detail too minor to be worth a line.
  `unsupported`        not findable in the document text — the fact the pass must never produce.

Precision is `grounded_material / added`. `unsupported` is reported separately and is the
number to treat as a blocker: trivial additions cost attention, unsupported ones cost trust.
"""
import argparse
import glob
import json
import os
import sys


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def added_facts(extraction):
    """The verifier's own additions, with their index in `key_facts` (which is what an item id
    is built from, so a grade can always be traced back to the artifact it came from)."""
    facts = extraction.get("document", {}).get("key_facts", [])
    return [(i, f) for i, f in enumerate(facts)
            if isinstance(f, dict) and f.get("added_by") == "verify"]


def doc_pages(vault, sha256):
    """The document's chewed page text, from the queue descriptor the extractor itself read.
    Missing (a vault whose queue files were cleared by a commit) is not fatal — the packet is
    still judgeable against the source PDF, just not self-contained, and `build` says so."""
    path = os.path.join(vault, ".watchdog", "queue", f"{sha256}.json")
    if not os.path.exists(path):
        return None
    return [{"page": p.get("page"), "text": p.get("markdown", "")}
            for p in _load(path).get("pages", [])]


def build(vault, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    written, total_added, missing_pages = [], 0, []
    for path in sorted(glob.glob(os.path.join(vault, ".watchdog", "extracted", "*.json"))):
        extraction = _load(path)
        document = extraction.get("document", {})
        sha256 = document.get("sha256") or os.path.basename(path).removesuffix(".json")
        added = added_facts(extraction)
        if not added:
            continue
        total_added += len(added)

        slug = os.path.splitext(document.get("filename") or sha256[:12])[0]
        pages = doc_pages(vault, sha256)
        if pages is None:
            missing_pages.append(slug)

        packet = {
            "document": slug,
            "sha256": sha256,
            "filename": document.get("filename"),
            "added_facts": [
                {"id": f"{slug}:v{i}", "fact": fact.get("fact"), "page": fact.get("page"),
                 "quote": fact.get("quote"), "entities": fact.get("entities")}
                for i, fact in added
            ],
            # The restatement reference. Only the extractor's own facts — a candidate judged
            # against another *added* fact would let the pass grade itself.
            "existing_facts": [
                {"fact": f.get("fact"), "page": f.get("page")}
                for f in document.get("key_facts", [])
                if isinstance(f, dict) and f.get("added_by") != "verify"
            ],
            "pages": pages,
        }
        out_path = os.path.join(out_dir, f"packet-{slug}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(packet, f, indent=2, ensure_ascii=False)
        written.append((slug, len(added), out_path))

    for slug, n, out_path in written:
        print(f"{slug}: {n} added fact(s) -> {out_path}")
    if not written:
        print(f"No verifier-added facts found in {vault} — was it extracted with --verify?")
        return
    print(f"\n{total_added} fact(s) to judge across {len(written)} document(s).")
    if missing_pages:
        print(f"WARNING: no queue page text for {', '.join(missing_pages)} — those packets carry "
              f"no grounding reference and must be judged against the source document itself.")
    print(f"\nNext: for each packet, grade every added fact one of "
          f"{'/'.join(_GRADES)} and write the verdicts to "
          f"{os.path.join(out_dir, 'judgment-<document>.json')} as "
          f'{{"judgments": {{"<id>": {{"grade": "...", "note": "..."}}}}}}, then run: '
          f"verifier_precision.py aggregate {out_dir}")


_GRADES = ("grounded_material", "grounded_trivial", "unsupported")


def aggregate(judge_dir):
    tally = dict.fromkeys(_GRADES, 0)
    per_doc, rows, problems = {}, [], []

    for packet_path in sorted(glob.glob(os.path.join(judge_dir, "packet-*.json"))):
        slug = os.path.basename(packet_path)[len("packet-"):-len(".json")]
        judgment_path = os.path.join(judge_dir, f"judgment-{slug}.json")
        packet = _load(packet_path)
        expected = {item["id"] for item in packet["added_facts"]}
        if not os.path.exists(judgment_path):
            problems.append(f"{slug}: no judgment file — {len(expected)} fact(s) ungraded")
            continue

        judgments = _load(judgment_path).get("judgments", {})
        doc_tally = dict.fromkeys(_GRADES, 0)
        for item_id in sorted(expected):
            verdict = judgments.get(item_id)
            if not verdict:
                problems.append(f"{slug}: {item_id} has no grade")
                continue
            grade = verdict.get("grade")
            if grade not in _GRADES:
                problems.append(f"{slug}: {item_id} has unknown grade {grade!r}")
                continue
            tally[grade] += 1
            doc_tally[grade] += 1
            rows.append([slug, item_id, grade, verdict.get("note", "")])
        for extra in sorted(set(judgments) - expected):
            problems.append(f"{slug}: graded {extra}, which is not in the packet")
        per_doc[slug] = doc_tally

    graded = sum(tally.values())
    if not graded:
        sys.exit(f"No graded facts found in {judge_dir}."
                 + ("\n  " + "\n  ".join(problems) if problems else ""))

    print(f"{'document':34}{'material':>10}{'trivial':>9}{'unsupported':>13}{'precision':>11}")
    for slug in sorted(per_doc):
        t = per_doc[slug]
        n = sum(t.values())
        pct = f"{t['grounded_material'] / n * 100:.0f}%" if n else "n/a"
        print(f"{slug:34}{t['grounded_material']:>10}{t['grounded_trivial']:>9}"
              f"{t['unsupported']:>13}{pct:>11}")
    precision = tally["grounded_material"] / graded
    print(f"\n{'ALL':34}{tally['grounded_material']:>10}{tally['grounded_trivial']:>9}"
          f"{tally['unsupported']:>13}{precision * 100:>10.0f}%")
    print(f"\nprecision (material / added)  {tally['grounded_material']}/{graded}  "
          f"({precision * 100:.0f}%)")
    print(f"unsupported rate              {tally['unsupported']}/{graded}  "
          f"({tally['unsupported'] / graded * 100:.0f}%)")

    summary = {"tally": tally, "per_document": per_doc, "graded": graded,
               "precision": round(precision, 4),
               "unsupported_rate": round(tally["unsupported"] / graded, 4)}
    with open(os.path.join(judge_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, indent=2, ensure_ascii=False)
    print(f"\nwrote {os.path.join(judge_dir, 'summary.json')}")

    if problems:
        print("\nPROBLEMS (the numbers above exclude these):")
        for p in problems:
            print(f"  {p}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_build = sub.add_parser("build", help="write judging packets from a --verify benchmark vault")
    p_build.add_argument("vault")
    p_build.add_argument("--out", required=True, help="directory to write packet-<doc>.json into")
    p_aggregate = sub.add_parser("aggregate", help="tally judgment-<doc>.json files into precision")
    p_aggregate.add_argument("judge_dir")

    args = parser.parse_args(argv)
    if args.command == "build":
        build(args.vault, args.out)
    else:
        aggregate(args.judge_dir)


if __name__ == "__main__":
    main()
