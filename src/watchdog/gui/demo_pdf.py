"""Small pure-Python document writers for the demo vault (`watchdog.gui.demo`).

The demo needs real files in the morgue — PDFs a viewer can open, with a selectable text layer —
without adding a dependency. This module lays out simple documents (title, headings, paragraphs,
key/value rows, ruled tables) on US Letter pages in the PDF base-14 Helvetica fonts and writes
them with a correct cross-reference table. The same page description also renders to the
Markdown the pipeline's chew step would have produced, so the text a search hit or a quoted
passage points at is exactly the text on the page.

A page is a list of blocks, each a tuple whose first item names it:

    ("title", text)                      18 pt bold
    ("h", text)                          12 pt bold heading
    ("p", text)                          wrapped paragraph
    ("small", text)                      8.5 pt grey note
    ("kv", [(key, value), ...])          label/value rows
    ("table", rows, widths)              ruled table; first row is the header, `widths` in points
    ("bullets", [text, ...])             hyphen list
    ("letterhead", name, subtitle)       organization banner (also appears in the Markdown text)
    ("rule",)                            horizontal line
    ("space", points)                    vertical gap

A page that is a single ("scan", lines) block is drawn as a scanned image instead (no text layer),
the way a scanned attachment sits inside an otherwise born-digital PDF; its Markdown is the text
OCR would have read from it.

Text is plain ASCII. A page whose content would run past the bottom margin raises `ValueError`,
so a layout mistake in the story data fails loudly instead of clipping silently.
"""

from __future__ import annotations

import random
import zlib
from pathlib import Path

PAGE_W, PAGE_H = 612, 792
MARGIN_X = 72
TOP = PAGE_H - 70
BOTTOM = 78                   # content must stay above this; the footer sits below it
TEXT_W = PAGE_W - 2 * MARGIN_X

# Helvetica advance widths per 1000 em for ASCII 32..126 (Adobe's standard AFM).
_HELV = [
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
    1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
    333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,
]
_BOLD_FACTOR = 1.06           # Helvetica-Bold runs slightly wider; wrap conservatively


def text_width(text: str, size: float, bold: bool = False) -> float:
    total = sum(_HELV[ord(c) - 32] if 32 <= ord(c) <= 126 else 556 for c in text)
    return total * size / 1000 * (_BOLD_FACTOR if bold else 1.0)


def wrap(text: str, size: float, width: float, bold: bool = False) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and text_width(trial, size, bold) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines or [""]


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class _Page:
    """Accumulates one page's content stream and tracks the cursor."""

    def __init__(self) -> None:
        self.ops: list[str] = []
        self.y = float(TOP)

    def text(self, x: float, y: float, s: str, size: float, font: str = "F1",
             grey: float = 0.0) -> None:
        self.ops.append(f"BT /{font} {size} Tf {grey} g {x:.1f} {y:.1f} Td ({_esc(s)}) Tj ET")

    def line(self, x1: float, y1: float, x2: float, y2: float, width: float = 0.6,
             grey: float = 0.0) -> None:
        self.ops.append(f"{grey} G {width} w {x1:.1f} {y1:.1f} m {x2:.1f} {y2:.1f} l S")

    def rect(self, x: float, y: float, w: float, h: float, fill: float | None = None,
             stroke: bool = False) -> None:
        if fill is not None:
            self.ops.append(f"{fill} g {x:.1f} {y:.1f} {w:.1f} {h:.1f} re f")
        if stroke:
            self.ops.append(f"0.5 G 0.6 w {x:.1f} {y:.1f} {w:.1f} {h:.1f} re S")

    def need(self, height: float) -> None:
        if self.y - height < BOTTOM:
            raise ValueError("demo page layout overflows the bottom margin")


def _render_page(blocks: list[tuple], footer: str | None) -> tuple[_Page, list[str]]:
    """Lay one page out; returns the page and the Markdown lines for the same content."""
    pg = _Page()
    md: list[str] = []
    for block in blocks:
        kind = block[0]
        if kind == "letterhead":
            _, name, sub = block
            pg.need(56)
            pg.rect(MARGIN_X, pg.y - 40, TEXT_W, 46, fill=0.93)
            pg.text(MARGIN_X + 10, pg.y - 16, name, 14, "F2")
            pg.text(MARGIN_X + 10, pg.y - 32, sub, 8.5, "F1", grey=0.35)
            pg.y -= 62
            md.append(f"**{name}**  \n{sub}")
        elif kind == "title":
            lines = wrap(block[1], 17, TEXT_W, bold=True)
            pg.need(24 * len(lines))
            for ln in lines:
                pg.text(MARGIN_X, pg.y - 14, ln, 17, "F2")
                pg.y -= 22
            pg.y -= 6
            md.append("# " + block[1])
        elif kind == "h":
            pg.need(30)
            pg.y -= 8
            pg.text(MARGIN_X, pg.y - 11, block[1], 11.5, "F2")
            pg.y -= 20
            md.append("## " + block[1])
        elif kind == "p":
            lines = wrap(block[1], 10, TEXT_W)
            pg.need(13.5 * len(lines) + 6)
            for ln in lines:
                pg.text(MARGIN_X, pg.y - 10, ln, 10)
                pg.y -= 13.5
            pg.y -= 6
            md.append(block[1])
        elif kind == "small":
            lines = wrap(block[1], 8.5, TEXT_W)
            pg.need(11.5 * len(lines) + 4)
            for ln in lines:
                pg.text(MARGIN_X, pg.y - 8.5, ln, 8.5, "F1", grey=0.35)
                pg.y -= 11.5
            pg.y -= 4
            md.append(block[1])
        elif kind == "kv":
            rows = block[1]
            key_w = 128
            for k, v in rows:
                lines = wrap(v, 10, TEXT_W - key_w)
                pg.need(13.5 * len(lines) + 3)
                pg.text(MARGIN_X, pg.y - 10, k, 10, "F2")
                for ln in lines:
                    pg.text(MARGIN_X + key_w, pg.y - 10, ln, 10)
                    pg.y -= 13.5
                pg.y -= 3
                md.append(f"**{k}** {v}")
            pg.y -= 4
        elif kind == "bullets":
            for item in block[1]:
                lines = wrap(item, 10, TEXT_W - 16)
                pg.need(13.5 * len(lines) + 2)
                pg.text(MARGIN_X + 4, pg.y - 10, "-", 10)
                for ln in lines:
                    pg.text(MARGIN_X + 16, pg.y - 10, ln, 10)
                    pg.y -= 13.5
                pg.y -= 2
                md.append(f"- {item}")
            pg.y -= 4
        elif kind == "table":
            rows, widths = block[1], list(block[2])
            row_h = 17
            pg.need(row_h * len(rows) + 8)
            top = pg.y
            for r, row in enumerate(rows):
                y = pg.y - row_h
                if r == 0:
                    pg.rect(MARGIN_X, y, sum(widths), row_h, fill=0.9)
                x = float(MARGIN_X)
                for c, cell in enumerate(row):
                    bold = r == 0
                    if text_width(str(cell), 8.5, bold) > widths[c] - 8:
                        raise ValueError(f"demo table cell too wide: {cell!r}")
                    pg.text(x + 4, y + 5, str(cell), 8.5, "F2" if bold else "F1")
                    x += widths[c]
                pg.line(MARGIN_X, y, MARGIN_X + sum(widths), y, 0.4, 0.55)
                pg.y = y
            pg.line(MARGIN_X, top, MARGIN_X + sum(widths), top, 0.6)
            x = float(MARGIN_X)
            for w in [*widths, 0]:
                pg.line(x, top, x, pg.y, 0.4, 0.55)
                x += w
            pg.y -= 10
            md.append("| " + " | ".join(str(c) for c in rows[0]) + " |")
            md.append("|" + "|".join(" --- " for _ in rows[0]) + "|")
            for row in rows[1:]:
                md.append("| " + " | ".join(str(c) for c in row) + " |")
        elif kind == "rule":
            pg.need(10)
            pg.line(MARGIN_X, pg.y - 4, MARGIN_X + TEXT_W, pg.y - 4, 0.6, 0.4)
            pg.y -= 12
        elif kind == "space":
            pg.y -= block[1]
        else:
            raise ValueError(f"unknown block {kind!r}")
    if footer:
        pg.line(MARGIN_X, 56, MARGIN_X + TEXT_W, 56, 0.4, 0.6)
        pg.text(MARGIN_X, 42, footer, 8, "F1", grey=0.4)
        md.append(footer)
    return pg, md


def _split_markdown(md: list[str]) -> str:
    """Join Markdown lines the way chew's output reads: blank-line separated, but a table's rows
    stay together."""
    out: list[str] = []
    for i, line in enumerate(md):
        if i and not (line.startswith("|") and md[i - 1].startswith("|")):
            out.append("")
        out.append(line)
    return "\n".join(out).strip()


def render_pdf(pages: list[list[tuple]], *, title: str, author: str = "",
               created: str = "D:20240102093000-05'00'", footer: str | None = None
               ) -> tuple[bytes, list[str]]:
    """Write `pages` as a PDF; returns the file bytes and each page's Markdown.

    `footer` is a format string taking `{n}` and `{total}`, drawn on every page and included in
    that page's Markdown (a page number is part of what a reader sees on the page)."""
    total = len(pages)
    rendered = [(None, [scan_markdown(blocks[0][1])]) if _is_scan(blocks) else
                _render_page(blocks, footer.format(n=i, total=total) if footer else None)
                for i, blocks in enumerate(pages, 1)]
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")          # patched once the page tree exists
    tree = add(b"")
    fonts = {
        "F1": add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"),
        "F2": add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
                  b"/Encoding /WinAnsiEncoding >>"),
    }
    info_fields = [f"/Title ({_esc(title)})", "/Producer (Watchdog demo writer)",
                   f"/CreationDate ({created})", f"/ModDate ({created})"]
    if author:
        info_fields.append(f"/Author ({_esc(author)})")
    info = add(("<< " + " ".join(info_fields) + " >>").encode("latin-1"))

    page_ids = []
    font_res = " ".join(f"/{k} {v} 0 R" for k, v in fonts.items())
    for i, (pg, _) in enumerate(rendered):
        if pg is None:
            # A scanned page: one greyscale image filling the sheet, and no text.
            img, _boxes = scan_image(pages[i][0][1], seed=7 + i, signature=False)
            raw = zlib.compress(img.tobytes(), 9)
            image = add(f"<< /Type /XObject /Subtype /Image /Width {img.width} /Height {img.height} "
                        f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
                        f"/Length {len(raw)} >>\nstream\n".encode() + raw + b"\nendstream")
            ops = zlib.compress(f"q {PAGE_W} 0 0 {PAGE_H} 0 0 cm /Im1 Do Q".encode(), 9)
            content = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(ops)
                          + ops + b"\nendstream")
            page_ids.append(add(
                f"<< /Type /Page /Parent {tree} 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                f"/Resources << /XObject << /Im1 {image} 0 R >> >> /Contents {content} 0 R >>".encode()))
            continue
        stream = zlib.compress("\n".join(pg.ops).encode("latin-1"), 9)
        content = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream)
                      + stream + b"\nendstream")
        page_ids.append(add(
            f"<< /Type /Page /Parent {tree} 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
            f"/Resources << /Font << {font_res} >> >> /Contents {content} 0 R >>".encode()))
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {tree} 0 R >>".encode()
    kids = " ".join(f"{i} 0 R" for i in page_ids)
    objects[tree - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R /Info {info} 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out), [_split_markdown(md) for _, md in rendered]


def _is_scan(blocks: list[tuple]) -> bool:
    return len(blocks) == 1 and blocks[0][0] == "scan"


def scan_pages(pages: list[list[tuple]]) -> dict[str, dict]:
    """The OCR line positions of each scanned page of `pages`, keyed by 1-based page number."""
    return {str(i): scan_page_positions(blocks[0][1], seed=7 + i - 1)
            for i, blocks in enumerate(pages, 1) if _is_scan(blocks)}


def write_pdf(path: Path, pages: list[list[tuple]], **kwargs) -> list[str]:
    """`render_pdf` to a file; returns the per-page Markdown."""
    data, markdown = render_pdf(pages, **kwargs)
    path.write_bytes(data)
    return markdown


SCAN_W, SCAN_H = 1275, 1650                 # 8.5 x 11 in at 150 dpi
_SCAN_ANGLE = -0.8


def scan_image(lines: list[str], *, seed: int = 7, signature: bool = True):
    """Draw `lines` as a slightly skewed, speckled page scan, optionally with a scrawled
    signature. Returns the greyscale Pillow image and each drawn line's box on it as
    `[left, top, right, bottom, text]` in pixels, as an OCR engine would report the line (the
    skew applied). Deterministic for a given `seed`. Pillow is a dependency of the package."""
    import math
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    rng = random.Random(seed)
    w, h = SCAN_W, SCAN_H
    img = Image.new("L", (w, h), 236)
    d = ImageDraw.Draw(img)
    try:
        body = ImageFont.load_default(size=27)
        bold = ImageFont.load_default(size=31)
    except TypeError:                       # very old Pillow: fixed-size bitmap font only
        body = bold = ImageFont.load_default()
    boxes: list[list] = []
    y = 190
    for i, text in enumerate(lines):
        font = bold if text.isupper() and text.strip() else body
        x = 150 + rng.randint(-2, 2)
        d.text((x, y), text, fill=38 + rng.randint(0, 14), font=font)
        if text.strip():
            boxes.append([*d.textbbox((x, y), text, font=font), text])
        y += 44 if text else 26
    if signature:
        # Signature scrawl: a jittery polyline.
        x, sy = 150, y + 30
        pts = []
        for _ in range(26):
            x += rng.randint(8, 16)
            pts.append((x, sy + rng.randint(-26, 26)))
        d.line(pts, fill=40, width=3)
    # Fold line, paper grain and speckle.
    d.line([(0, h // 3), (w, h // 3 + 3)], fill=222, width=2)
    for _ in range(2600):
        px, py = rng.randrange(w), rng.randrange(h)
        d.point((px, py), fill=rng.randint(150, 215))
    img = img.rotate(_SCAN_ANGLE, resample=Image.BICUBIC, fillcolor=228)
    img = img.filter(ImageFilter.GaussianBlur(0.6))

    # The same rotation applied to each line's corners (Pillow turns the picture
    # counter-clockwise about its centre by the angle), then the box around them.
    th = math.radians(_SCAN_ANGLE)
    cx, cy = w / 2, h / 2

    def turn(px: float, py: float) -> tuple[float, float]:
        dx, dy = px - cx, py - cy
        return cx + dx * math.cos(th) + dy * math.sin(th), cy - dx * math.sin(th) + dy * math.cos(th)

    out = []
    for left, top, right, bottom, text in boxes:
        pts = [turn(left, top), turn(right, top), turn(left, bottom), turn(right, bottom)]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        out.append([round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1), text])
    return img, out


def scan_markdown(lines: list[str]) -> str:
    """The text OCR would read from a scan of `lines`: blank-line separated paragraphs."""
    paragraphs: list[list[str]] = [[]]
    for line in lines:
        if line.strip():
            paragraphs[-1].append(line.strip())
        elif paragraphs[-1]:
            paragraphs.append([])
    return "\n\n".join(" ".join(p) for p in paragraphs if p)


def write_scanned_letter(path: Path, lines: list[str], *, seed: int = 7) -> dict:
    """Write `lines` as a scanned letter (PNG). Returns the page's OCR line positions in the
    `text_positions` page form (pixels, top-left origin)."""
    img, boxes = scan_image(lines, seed=seed)
    img.save(path, format="PNG", dpi=(150, 150), optimize=True)
    return {"width": float(SCAN_W), "height": float(SCAN_H), "lines": boxes}


def scan_page_positions(lines: list[str], *, seed: int = 7) -> dict:
    """The OCR line positions of a ("scan", lines) PDF page, in points (top-left origin)."""
    _, boxes = scan_image(lines, seed=seed, signature=False)
    fx, fy = PAGE_W / SCAN_W, PAGE_H / SCAN_H
    return {"width": float(PAGE_W), "height": float(PAGE_H),
            "lines": [[round(a * fx, 1), round(b * fy, 1), round(c * fx, 1), round(e * fy, 1), t]
                      for a, b, c, e, t in boxes]}


def write_docx(path: Path, title: str, paragraphs: list[str],
               table: list[list[str]] | None = None) -> None:
    """A small Word file (python-docx is already a dependency of the package)."""
    import docx

    d = docx.Document()
    d.core_properties.author = "Port Calder Public Works"
    d.core_properties.title = title
    d.add_heading(title, level=1)
    for text in paragraphs:
        d.add_paragraph(text)
    if table:
        t = d.add_table(rows=len(table), cols=len(table[0]))
        t.style = "Table Grid"
        for r, row in enumerate(table):
            for c, cell in enumerate(row):
                t.cell(r, c).text = cell
    d.save(str(path))
