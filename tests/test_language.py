"""Each document's language (D382): page detection, the document's decision, which scanned pages
are read again, what each OCR engine is given, and a sidecar's `language:`."""

import pytest

from watchdog.pipeline import language, sidecar

pytest.importorskip("py3langid")

FRENCH = ("Attendu que la débitrice a déposé une demande visant l'obtention d'une ordonnance "
          "initiale en vertu de la Loi, le tribunal a pris connaissance de la déclaration sous "
          "serment du directeur financier ainsi que des pièces qui y sont jointes, et le contrôleur "
          "proposé a consenti à agir à ce titre.")
# The same passage as an English OCR setting might read it: accents dropped, letters confused.
FRENCH_MISREAD = ("Attendu que la debitrice a depose une demande visant l'obtention d'une ordonnance "
                  "initiale en vertu de la Loi, le tnbunal a pris connaissance de la declaration sous "
                  "serment du directeur financier ainsi que des pieces qui y sont jointes, et le controleur "
                  "propose a consenti a agir a ce titre.")
ENGLISH = ("The applicant filed an application seeking an initial order under the Act, and the court "
           "has reviewed the affidavit of the chief financial officer together with the exhibits "
           "attached to it, and the proposed monitor has consented to act in that capacity.")


def test_page_language_names_french_even_as_an_english_setting_misreads_it():
    assert language.page_language(FRENCH) == "fr"
    assert language.page_language(FRENCH_MISREAD) == "fr"
    assert language.page_language(ENGLISH) == "en"


def test_a_short_page_or_a_page_of_figures_is_not_judged():
    assert language.page_language("DÉPOSÉ / FILED  Le 17 mars / March 17  Greffe / Registry") is None
    figures = " | ".join(f"{n:,} | {n * 3:,}" for n in range(1000, 1400, 7))
    assert language.page_language(figures) is None
    assert language.page_language("") is None


def test_decide_unjudged_pages_take_the_majority():
    d = language.decide({1: "fr", 2: None, 3: "fr", 4: "fr", 5: "fr", 6: "en"})
    assert d["code"] == "fr" and d["mixed"] is False
    assert d["pages"][2] == "fr"


def test_decide_mixed_below_the_mostly_share():
    d = language.decide({1: "en", 2: "en", 3: "en", 4: "fr", 5: "fr"})
    assert d["code"] == "en" and d["mixed"] is True
    assert d["pages"] == {1: "en", 2: "en", 3: "en", 4: "fr", 5: "fr"}


def test_decide_one_other_language_page_among_many_english_is_still_english():
    pages = {p: "en" for p in range(1, 29)}
    pages[29] = "fr"
    d = language.decide(pages)
    assert d["code"] == "en" and d["mixed"] is False


def test_decide_nothing_judged_and_ties_go_to_english():
    assert language.decide({1: None, 2: None}) == {"code": None, "mixed": False, "pages": {}}
    assert language.decide({1: "fr", 2: "en"})["code"] == "en"


def test_reread_plan_mostly_another_language_reads_every_scanned_page_again():
    d = language.decide({1: "fr", 2: "fr", 3: None})
    assert language.reread_plan(d, [2, 3]) == ({"fr": [2, 3]}, {})


def test_reread_plan_mixed_asks_about_its_other_language_scanned_pages_only():
    d = language.decide({1: "en", 2: "en", 3: "en", 4: "fr", 5: "fr"})
    assert language.reread_plan(d, [3, 4]) == ({}, {"fr": [4]})


def test_reread_plan_english_or_undetermined_reads_nothing_again():
    assert language.reread_plan(language.decide({1: "en"}), [1]) == ({}, {})
    assert language.reread_plan(language.decide({1: None}), [1]) == ({}, {})


def test_ocr_codes_apple_vision():
    assert language.ocr_codes("ocrmac", "fr") == (["fr-FR", "en-US"], None)
    codes, why = language.ocr_codes("ocrmac", "hu")
    assert codes is None and "Hungarian" in why


def test_ocr_codes_tesseract_reports_missing_data_rather_than_fetching_it():
    assert language.ocr_codes("tesseract", "fr", ["eng", "fra"]) == (["fra", "eng"], None)
    codes, why = language.ocr_codes("tesseract", "fr", ["eng"])
    assert codes is None and "no French language data" in why and "fra" in why


def test_ocr_codes_rapidocr_reads_latin_scripts_as_they_are():
    assert language.ocr_codes("rapidocr", "fr") == (None, None)
    codes, why = language.ocr_codes("rapidocr", "ru")
    assert codes is None and "downloaded" in why


def test_normalize_accepts_codes_tags_and_names():
    assert language.normalize("fr") == "fr"
    assert language.normalize("fr-CA") == "fr"
    assert language.normalize("FRA") == "fr"
    assert language.normalize("French") == "fr"
    for bad in ("", "xx-yy-zz-q", "Klingon", None, 12):
        assert language.normalize(bad) is None


def test_sidecar_language_is_allowed_and_read():
    clean, dropped = sidecar.filter_and_render("source: court\nlanguage: French\n")
    assert dropped == [] and "language: French" in clean
    assert sidecar.language(clean) == "fr"
    assert sidecar.language("source: court\n") is None
