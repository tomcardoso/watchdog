"""Harsher scans (about 130 DPI, heavy blur), read by Vision with no language (Docling's empty
setting), English only, and French+English. Writes harsh.json."""
import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).parent))
from render_ocr import render, vision  # noqa: E402
from texts import PAGES  # noqa: E402
from ocrmac import ocrmac  # noqa: E402

HERE = Path(__file__).parent


def none_lang(path):
    rows = ocrmac.OCR(str(path), language_preference=None, recognition_level="accurate").recognize()
    rows.sort(key=lambda r: (-round(r[2][1] + r[2][3], 2), r[2][0]))
    return "\n".join(r[0] for r in rows)


out = {}
for n, (name, text) in enumerate(PAGES.items()):
    rnd = random.Random(n)
    img = render(text, "times").rotate(rnd.uniform(-1.2, 1.2), fillcolor=255, resample=Image.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(2.2)).resize((img.width * 13 // 30, img.height * 13 // 30))
    img = img.point(lambda v: 0 if v < 165 else 255)
    p = HERE / f"{name}-harsh.png"
    img.save(p)
    out[p.stem] = {"source": text, "vision_none": none_lang(p), "vision_en": vision(p, ["en-US"]),
                   "vision_fr": vision(p, ["fr-FR", "en-US"])}
    print(p.stem, file=sys.stderr)
(HERE / "harsh.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
