"""Does Apple Vision need the language set? One line of Russian, German and Chinese each, read
with no language, English only, and the language plus English. Run with the app's engine Python."""
import textwrap
from pathlib import Path

from ocrmac import ocrmac
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
TEXT = {
    "ru": ("Суд постановил, что компания обязана выплатить кредиторам задолженность в размере "
           "двенадцати миллионов рублей до конца марта.", "ru-RU"),
    "de": ("Das Gericht hat entschieden, dass die Gesellschaft ihren Gläubigern die Schulden in Höhe "
           "von zwölf Millionen Euro bis Ende März zurückzahlen muss. Größere Beträge müssen "
           "gesondert genehmigt werden.", "de-DE"),
    "zh": ("法院裁定该公司必须在三月底之前向债权人偿还一千二百万元的债务。", "zh-Hans"),
}
font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 40)
for code, (text, vision_code) in TEXT.items():
    img = Image.new("L", (2400, 600), 255)
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(textwrap.wrap(text, 60 if code != "zh" else 30)):
        draw.text((60, 60 + i * 60), line, font=font, fill=0)
    path = HERE / f"{code}.png"
    img.save(path)
    for langs in (None, ["en-US"], [vision_code, "en-US"]):
        rows = ocrmac.OCR(str(path), language_preference=langs).recognize()
        print(code, langs, " | ".join(r[0] for r in rows))
