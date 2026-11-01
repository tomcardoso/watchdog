#!/usr/bin/env python3
"""The frozen chew's fingerprint, and the check that a run's chew matches it (D372, #808).

The answer keys' `quote` spans mirror the page text of one chew of the corpus (made on macOS,
read by Apple Vision; `keys/README.md`). A chew made elsewhere, by another OCR engine, can read a
scanned page differently, and a quote the keys expect is then missing from the page the model
saw. `run_benchmark.py` checked that the PDFs and keys are frozen, never that the chew is.

`keys/chew-manifest.json` records, per document, a hash of each page's text, the OCR engine that
read it and the platform. It is written from a chewed vault, on the machine whose chew the keys
were checked against:

    python3 benchmarks/chew_manifest.py write <master vault>
    python3 benchmarks/chew_manifest.py check <vault>       # what a run does for itself

A run compares its master vault's chew with the manifest before it asks to spend anything, and
warns (it does not refuse, so a deliberate run on another platform is still possible); REPORT.md
and run.json carry the same finding beside the affected documents.
"""

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "keys" / "chew-manifest.json"
VERSION = 1


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_chew(vault: Path) -> dict:
    """sha256 -> {"filename", "pages": {page: markdown}, "metadata": {...}} from a chewed vault's
    queue (`.watchdog/queue/*.json`), the pre-processing output every arm is seeded from."""
    out = {}
    for f in sorted((Path(vault) / ".watchdog" / "queue").glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or not d.get("sha256") or not isinstance(d.get("pages"), list):
            continue
        out[d["sha256"]] = {
            "filename": d.get("filename") or f.name,
            "pages": {int(p["page"]): p.get("markdown") or "" for p in d["pages"] if "page" in p},
            "metadata": d.get("metadata") if isinstance(d.get("metadata"), dict) else {},
        }
    return out


def fingerprint(chew: dict) -> dict:
    """The manifest for a chew: per document, its page hashes and what read it."""
    docs = {}
    for sha, d in sorted(chew.items()):
        meta = d["metadata"]
        docs[sha] = {
            "filename": d["filename"],
            "pages": {str(n): _hash(t) for n, t in sorted(d["pages"].items())},
            "ocr_engine": meta.get("ocr_engine") or "unknown",
            "platform": meta.get("platform") or "unknown",
        }
    return {"version": VERSION, "created": date.today().isoformat(), "documents": docs}


def load(path: Path | None = None) -> dict | None:
    try:
        return json.loads(Path(path or MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def compare(manifest: dict, chew: dict) -> list[dict]:
    """Every document of the manifest whose chew differs: the pages whose text changed, and the
    engine and platform on each side. A document the chew lacks is listed with `missing`; a
    document the manifest lacks is not checked (the keys were not written against it)."""
    diffs = []
    for sha, frozen in sorted((manifest.get("documents") or {}).items()):
        d = chew.get(sha)
        if d is None:
            diffs.append({"sha256": sha, "filename": frozen.get("filename"), "missing": True,
                          "pages": []})
            continue
        now = {str(n): _hash(t) for n, t in d["pages"].items()}
        then = frozen.get("pages") or {}
        pages = sorted((p for p in set(now) | set(then) if now.get(p) != then.get(p)), key=int)
        if pages:
            meta = d["metadata"]
            diffs.append({
                "sha256": sha, "filename": d["filename"], "missing": False,
                "pages": [int(p) for p in pages],
                "ocr_engine": meta.get("ocr_engine") or "unknown",
                "platform": meta.get("platform") or "unknown",
                "frozen_ocr_engine": frozen.get("ocr_engine") or "unknown",
                "frozen_platform": frozen.get("platform") or "unknown",
            })
    return diffs


def check(vault: Path, manifest_path: Path | None = None) -> dict:
    """{"manifest": bool, "differs": [...]} for a chewed vault. No manifest committed yet means
    nothing can be said, which is reported rather than taken for a match."""
    manifest = load(manifest_path)
    if manifest is None:
        return {"manifest": False, "differs": []}
    return {"manifest": True, "differs": compare(manifest, read_chew(vault))}


def _pages_label(pages: list[int]) -> str:
    return f"page {pages[0]}" if len(pages) == 1 else f"{len(pages)} pages ({', '.join(map(str, pages[:8]))}{'…' if len(pages) > 8 else ''})"


def describe(diff: dict) -> str:
    """One line for a differing document."""
    if diff.get("missing"):
        return f"{diff['filename']}: not in this chew"
    return (f"{diff['filename']}: {_pages_label(diff['pages'])} differ — read by "
            f"{diff['ocr_engine']} on {diff['platform']}, keys checked against "
            f"{diff['frozen_ocr_engine']} on {diff['frozen_platform']}")


def warning_lines(result: dict) -> list[str]:
    """The pre-spend warning for `check`'s result, or [] when the chew matches."""
    if not result["manifest"]:
        return ["Note: no frozen-chew manifest (benchmarks/keys/chew-manifest.json), so this run's "
                "chew cannot be checked against the one the keys' quotes were verified on."]
    if not result["differs"]:
        return []
    return (["WARNING: this run's chew differs from the frozen one the answer keys were checked "
             "against.",
             "Key quotes on these pages may be missing from what the model reads, and their "
             "scores are not comparable with runs on the frozen chew:"]
            + [f"  - {describe(d)}" for d in result["differs"]])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write", help="write the manifest from a chewed vault")
    w.add_argument("vault", type=Path)
    w.add_argument("--out", type=Path, default=MANIFEST)
    c = sub.add_parser("check", help="compare a chewed vault with the manifest")
    c.add_argument("vault", type=Path)
    c.add_argument("--manifest", type=Path, default=None)
    args = ap.parse_args(argv)

    if args.cmd == "write":
        chew = read_chew(args.vault)
        if not chew:
            sys.exit(f"no chewed documents in {args.vault}/.watchdog/queue")
        manifest = fingerprint(chew)
        args.out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        engines = sorted({d["ocr_engine"] for d in manifest["documents"].values()})
        print(f"wrote {args.out} ({len(chew)} documents; engines: {', '.join(engines)})")
        return 0

    result = check(args.vault, args.manifest)
    lines = warning_lines(result)
    print("\n".join(lines) if lines else "chew matches the frozen manifest")
    return 1 if result["differs"] else 0


if __name__ == "__main__":
    sys.exit(main())
