"""Each document's language, detected from its pages' text, and what that means for reading its
scanned pages again (D383).

Detection is `py3langid` (offline, numpy, the same answer every time) over each page's text, the
file's own text layer or a first OCR reading. A detector reads character patterns, so French read
by an English OCR setting is still French. A page with too few words to judge (a stamp, a
signature, a page of figures) takes the document's majority, so a bilingual stamp or heading on a
short page never makes a document count as mixed. Code decides; no model is involved.

Codes are ISO 639-1 (`fr`), the detector's own labels. `ocr_codes` turns one into what an OCR
engine is given, and is the one place that knows each engine's language support."""

from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache

# Fewer words than this and a page is not judged; it takes the document's majority.
MIN_WORDS = 30
# The detector's probability below which a page is not judged either.
MIN_CONFIDENCE = 0.9
# The share of judged pages one language needs for the document to be "mostly" that language.
MOSTLY = 0.8
ENGLISH = "en"

# Names for the languages an OCR engine here can read, for notes and the app's messages.
NAMES = {
    "af": "Afrikaans", "ar": "Arabic", "bg": "Bulgarian", "ca": "Catalan", "cs": "Czech",
    "cy": "Welsh", "da": "Danish", "de": "German", "el": "Greek", "en": "English", "es": "Spanish",
    "et": "Estonian", "eu": "Basque", "fa": "Persian", "fi": "Finnish", "fr": "French",
    "ga": "Irish", "gl": "Galician", "he": "Hebrew", "hi": "Hindi", "hr": "Croatian",
    "hu": "Hungarian", "id": "Indonesian", "is": "Icelandic", "it": "Italian", "ja": "Japanese",
    "ko": "Korean", "la": "Latin", "lt": "Lithuanian", "lv": "Latvian", "mk": "Macedonian",
    "mr": "Marathi", "ms": "Malay", "nl": "Dutch", "no": "Norwegian", "pl": "Polish",
    "pt": "Portuguese", "ro": "Romanian", "ru": "Russian", "sk": "Slovak", "sl": "Slovenian",
    "sq": "Albanian", "sr": "Serbian", "sv": "Swedish", "sw": "Swahili", "th": "Thai",
    "tl": "Tagalog", "tr": "Turkish", "uk": "Ukrainian", "ur": "Urdu", "vi": "Vietnamese",
    "zh": "Chinese",
}

# Apple Vision's recognition languages (the 33 `accurate` reports), by ISO code.
_VISION = {
    "en": "en-US", "fr": "fr-FR", "it": "it-IT", "de": "de-DE", "es": "es-ES", "pt": "pt-BR",
    "zh": "zh-Hans", "ko": "ko-KR", "ja": "ja-JP", "ru": "ru-RU", "uk": "uk-UA", "th": "th-TH",
    "vi": "vi-VT", "ar": "ar-SA", "tr": "tr-TR", "id": "id-ID", "cs": "cs-CZ", "da": "da-DK",
    "nl": "nl-NL", "no": "no-NO", "ms": "ms-MY", "pl": "pl-PL", "ro": "ro-RO", "sv": "sv-SE",
    "fi": "fi-FI", "hi": "hi-IN", "mr": "mr-IN",
}

# Tesseract's language data names, by ISO code.
_TESSERACT = {
    "af": "afr", "ar": "ara", "bg": "bul", "ca": "cat", "cs": "ces", "cy": "cym", "da": "dan",
    "de": "deu", "el": "ell", "en": "eng", "es": "spa", "et": "est", "eu": "eus", "fa": "fas",
    "fi": "fin", "fr": "fra", "ga": "gle", "gl": "glg", "he": "heb", "hi": "hin", "hr": "hrv",
    "hu": "hun", "id": "ind", "is": "isl", "it": "ita", "ja": "jpn", "ko": "kor", "la": "lat",
    "lt": "lit", "lv": "lav", "mk": "mkd", "mr": "mar", "ms": "msa", "nl": "nld", "no": "nor",
    "pl": "pol", "pt": "por", "ro": "ron", "ru": "rus", "sk": "slk", "sl": "slv", "sq": "sqi",
    "sr": "srp", "sv": "swe", "sw": "swa", "th": "tha", "tl": "tgl", "tr": "tur", "uk": "ukr",
    "ur": "urd", "vi": "vie", "zh": "chi_sim",
}

# The languages RapidOCR's PP-OCRv6 model reads as they are, one model for all of them, so a
# reread changes nothing: measured on French, its text is the same with the language set or not.
_RAPIDOCR_AS_IS = frozenset({
    "af", "ca", "cs", "cy", "da", "de", "en", "es", "et", "eu", "fi", "fr", "ga", "gl", "hr", "hu",
    "id", "is", "it", "ja", "la", "lt", "lv", "ms", "mt", "nl", "no", "pl", "pt", "ro", "sk", "sl",
    "sq", "sv", "sw", "tl", "tr", "vi", "zh",
})

_WORD = re.compile(r"[^\W\d_]{2,}")


def name(code: str | None) -> str:
    return NAMES.get(code or "", code or "unknown")


@lru_cache(maxsize=1)
def _identifier():
    from py3langid.langid import MODEL_FILE, LanguageIdentifier
    return LanguageIdentifier.from_model_file(MODEL_FILE, norm_probs=True)


def page_language(text: str) -> str | None:
    """The language of one page's text, or None when the page is too short or the detector
    unsure. Only words are read: figures, table rules and markup carry no language."""
    words = _WORD.findall(text or "")
    if len(words) < MIN_WORDS:
        return None
    code, confidence = _identifier().classify(" ".join(words))
    return code if confidence >= MIN_CONFIDENCE else None


def normalize(value) -> str | None:
    """A sidecar's `language:` as an ISO code: `fr`, `fr-CA`, `fra` or `French`; None for one
    Watchdog has no name for, which no OCR engine here reads either."""
    if not isinstance(value, str) or not value.strip():
        return None
    v = value.strip().lower().replace("_", "-")
    by_name = {n.lower(): c for c, n in NAMES.items()}
    if v in by_name:
        return by_name[v]
    v = v.split("-", 1)[0]
    if len(v) == 3:
        v = next((c for c, t in _TESSERACT.items() if t == v), v)
    return v if v in NAMES else None


def decide(page_langs: dict[int, str | None]) -> dict:
    """The document's language from its pages' (1-indexed page -> code or None): `code` (None
    when no page could be judged), `mixed` (no language has MOSTLY of the judged pages), and
    `pages`, every page's language, an unjudged page taking the majority."""
    judged = {p: lang for p, lang in page_langs.items() if lang}
    if not judged:
        return {"code": None, "mixed": False, "pages": {}}
    counts = Counter(judged.values())
    # Ties go to English, then alphabetically, so the answer never depends on page order.
    top, n = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0] != ENGLISH, kv[0]))[0]
    return {"code": top, "mixed": n / len(judged) < MOSTLY,
            "pages": {p: judged.get(p, top) for p in sorted(page_langs)}}


def reread_plan(decision: dict, scanned: list[int]) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    """(now, ask): the scanned pages to read again, by language. A document mostly in another
    language has every scanned page read again in it, with no question; a mixed one has its
    other-language scanned pages offered to the reporter (`ask`). English is never read again."""
    by_lang: dict[str, list[int]] = {}
    code = decision.get("code")
    if not code:
        return {}, {}
    for p in scanned:
        lang = code if not decision["mixed"] else decision["pages"].get(p, code)
        if lang != ENGLISH:
            by_lang.setdefault(lang, []).append(p)
    return ({}, by_lang) if decision["mixed"] else (by_lang, {})


def ocr_codes(engine: str, code: str, installed: list[str] | None = None) -> tuple[list[str] | None, str | None]:
    """What `engine` (`ocrmac`, `tesseract`, `rapidocr`...) is given to read `code` plus English,
    or (None, why not). `installed` is Tesseract's languages with data on this computer. Nothing is
    ever downloaded: a language without data is reported, not fetched."""
    lang = name(code)
    if engine == "ocrmac":
        if code not in _VISION:
            return None, f"Apple Vision can't read {lang}, so its scanned pages were read as English."
        return list(dict.fromkeys([_VISION[code], _VISION[ENGLISH]])), None
    if engine == "tesseract":
        data = _TESSERACT.get(code)
        if data is None:
            return None, f"Tesseract can't read {lang}, so its scanned pages were read as English."
        if installed is not None and data not in installed:
            return None, (f"Tesseract has no {lang} language data on this computer, so its scanned "
                          f"pages were read as English. Install the {lang} data ({data}) to read "
                          f"documents added after that in {lang}.")
        return list(dict.fromkeys([data, "eng"])), None
    if engine == "rapidocr":
        if code in _RAPIDOCR_AS_IS:
            return None, None   # one model reads it as it is: nothing to read again
        return None, (f"RapidOCR's model doesn't read {lang}, and the one that does would have to be "
                      f"downloaded, so its scanned pages were read as they were.")
    return None, f"The OCR engine in use ({engine}) isn't given a language, so {lang} pages were read as they were."
