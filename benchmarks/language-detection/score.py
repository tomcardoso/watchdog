"""Detection and word accuracy over ocr.json. Run with the worktree venv (py3langid)."""
import difflib
import json
import re
import unicodedata
from pathlib import Path

from py3langid.langid import MODEL_FILE, LanguageIdentifier

ident = LanguageIdentifier.from_model_file(MODEL_FILE, norm_probs=True)
d = json.loads((Path(__file__).parent / "ocr.json").read_text())


def words(t):
    return re.findall(r"\w+", t.lower())


def acc(src, got):
    a, b = words(src), words(got)
    m = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sum(x.size for x in m.get_matching_blocks()) / max(len(a), 1)


def accents(t):
    return sum(1 for c in unicodedata.normalize("NFC", t) if c in "éèêàâçîôûùëïÉÈÀÇ")


rows = []
for key, r in d.items():
    src_acc = accents(r["source"])
    row = [key, len(words(r["source"]))]
    for eng in ("vision_en", "vision_fr", "rapidocr"):
        lang, conf = ident.classify(r[eng])
        row += [f"{lang}:{conf:.2f}", f"{acc(r['source'], r[eng]):.3f}",
                f"{accents(r[eng])}/{src_acc}"]
    rows.append(row)
print("page words | vision_en det acc accents | vision_fr ... | rapidocr ...")
for row in rows:
    print(" ".join(str(x) for x in row))
