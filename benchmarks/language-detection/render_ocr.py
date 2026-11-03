"""Render the French passages as page images (clean and scan-like), then OCR each with Apple
Vision in English only, Apple Vision in French+English, and RapidOCR's default model.
Run with the app's engine Python (it has ocrmac and rapidocr). Writes ocr.json beside this file."""
import json
import random
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from texts import PAGES  # noqa: E402

HERE = Path(__file__).parent
FONTS = {"times": "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
         "arial": "/System/Library/Fonts/Supplemental/Arial.ttf"}


def render(text: str, font: str, dpi: int = 300) -> Image.Image:
    w, h = int(8.5 * dpi), int(11 * dpi)
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(FONTS[font], int(11 / 72 * dpi))
    y = dpi
    mono = "  " in text  # the table keeps its columns
    if mono:
        f = ImageFont.truetype("/System/Library/Fonts/Supplemental/Courier New.ttf", int(10 / 72 * dpi))
    for para in text.split("\n"):
        lines = [para] if mono else (textwrap.wrap(para, 85) or [""])
        for line in lines:
            d.text((dpi, y), line, font=f, fill=0)
            y += int(f.size * 1.35)
    return img


def scanlike(img: Image.Image, seed: int) -> Image.Image:
    rnd = random.Random(seed)
    img = img.rotate(rnd.uniform(-0.8, 0.8), fillcolor=255, resample=Image.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(1.1))
    img = img.resize((img.width * 2 // 3, img.height * 2 // 3))   # 300 -> 200 DPI
    px = img.load()
    for _ in range(img.width * img.height // 400):                  # speckle
        x, y = rnd.randrange(img.width), rnd.randrange(img.height)
        px[x, y] = rnd.choice((0, 90, 200))
    return img.point(lambda v: 0 if v < 150 else 255)                 # photocopier threshold


def vision(path: Path, langs: list[str]) -> str:
    from ocrmac import ocrmac
    rows = ocrmac.OCR(str(path), language_preference=langs, recognition_level="accurate").recognize()
    rows.sort(key=lambda r: (-round(r[2][1] + r[2][3], 2), r[2][0]))
    return "\n".join(r[0] for r in rows)


_rapid = None


def rapid(path: Path) -> str:
    global _rapid
    if _rapid is None:
        from rapidocr import RapidOCR
        _rapid = RapidOCR()
    out = _rapid(str(path))
    return "\n".join(out.txts or ())


def main():
    out = {}
    for n, (name, text) in enumerate(PAGES.items()):
        for font in ("times", "arial"):
            for kind in ("clean", "scan"):
                img = render(text, font)
                if kind == "scan":
                    img = scanlike(img, n)
                p = HERE / f"{name}-{font}-{kind}.png"
                img.save(p)
                key = p.stem
                out[key] = {"source": text,
                            "vision_en": vision(p, ["en-US"]),
                            "vision_fr": vision(p, ["fr-FR", "en-US"]),
                            "rapidocr": rapid(p)}
                print(key, file=sys.stderr)
    (HERE / "ocr.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
