import datetime

import pytest

from watchdog.pipeline import file_metadata


# ── extract(): per-family readers ────────────────────────────────────────────

def test_extract_docx_normalizes_author_and_created(tmp_path):
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    doc.core_properties.author = "Jane Doe"
    doc.core_properties.created = datetime.datetime(2020, 1, 15, 12, 0, 0)
    doc.core_properties.last_modified_by = "John Smith"
    doc.core_properties.revision = 5
    path = tmp_path / "report.docx"
    doc.save(str(path))

    result = file_metadata.extract(path)
    assert result["author"] == "Jane Doe"
    assert result["created"].startswith("2020-01-15")
    assert result["last_modified_by"] == "John Smith"
    assert result["revision"] == 5


def test_extract_xlsx_normalizes_creator_and_created(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.properties.creator = "Ada Lovelace"
    wb.properties.created = datetime.datetime(2021, 6, 1, 9, 30, 0)
    path = tmp_path / "ledger.xlsx"
    wb.save(str(path))

    result = file_metadata.extract(path)
    assert result["author"] == "Ada Lovelace"
    assert result["created"].startswith("2021-06-01")


def test_extract_pptx_normalizes_author_and_created(tmp_path):
    pptx = pytest.importorskip("pptx")
    pres = pptx.Presentation()
    pres.core_properties.author = "Grace Hopper"
    pres.core_properties.created = datetime.datetime(2019, 3, 3, 0, 0, 0)
    path = tmp_path / "deck.pptx"
    pres.save(str(path))

    result = file_metadata.extract(path)
    assert result["author"] == "Grace Hopper"
    assert result["created"].startswith("2019-03-03")


def test_extract_pdf_normalizes_pdf_date_string_to_iso(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_metadata({
        "/Author": "Jane Doe",
        "/CreationDate": "D:20230115120000-05'00'",
        "/Producer": "Acrobat Distiller",
    })
    path = tmp_path / "resolution.pdf"
    with open(path, "wb") as f:
        writer.write(f)

    result = file_metadata.extract(path)
    assert result["author"] == "Jane Doe"
    assert result["producer"] == "Acrobat Distiller"
    # The raw "D:20230115120000-05'00'" form must never reach downstream consumers —
    # it's normalized to ISO-8601 deterministically in Python.
    assert result["created"] == "2023-01-15T12:00:00-05:00"


def test_extract_pdf_with_no_metadata_returns_partial_or_empty_dict(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=100, height=100)
    path = tmp_path / "blank.pdf"
    with open(path, "wb") as f:
        writer.write(f)

    result = file_metadata.extract(path)
    assert isinstance(result, dict)


def test_extract_jpg_returns_dict_without_raising(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    img = Image.new("RGB", (10, 10))
    path = tmp_path / "photo.jpg"
    img.save(str(path), format="JPEG")

    result = file_metadata.extract(path)
    assert isinstance(result, dict)   # a synthetic image may legitimately have no EXIF at all


# ── Never raises: corrupt/truncated files, unsupported suffix ───────────────

@pytest.mark.parametrize("suffix", [".pdf", ".docx", ".pptx", ".xlsx", ".jpg", ".png"])
def test_extract_corrupt_file_returns_empty_dict_never_raises(tmp_path, suffix):
    path = tmp_path / f"corrupt{suffix}"
    path.write_bytes(b"this is not a valid file of this type at all, just garbage bytes")
    assert file_metadata.extract(path) == {}


def test_extract_unsupported_suffix_returns_empty_dict(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("plain text, no embedded metadata layer")
    assert file_metadata.extract(path) == {}


def test_extract_missing_file_returns_empty_dict(tmp_path):
    # Reader raises (FileNotFoundError or similar) internally — extract() must swallow it.
    assert file_metadata.extract(tmp_path / "does-not-exist.pdf") == {}


# ── Value truncation and coercion (untrusted-content mitigation) ────────────

def test_normalize_truncates_long_string_value_to_200_chars():
    long_author = "A" * 500
    result = file_metadata._normalize({"author": long_author})
    assert result["author"] == "A" * 200
    assert len(result["author"]) == 200


def test_normalize_coerces_non_string_values_to_str_except_numeric_keys():
    result = file_metadata._normalize({"author": 12345, "revision": "7", "duration_seconds": "12.5"})
    assert result["author"] == "12345"
    assert isinstance(result["revision"], int) and result["revision"] == 7
    assert isinstance(result["duration_seconds"], float) and result["duration_seconds"] == 12.5


def test_normalize_drops_keys_outside_the_allowlist():
    result = file_metadata._normalize({"author": "Jane", "xmp_custom_payload": "<script>evil</script>"})
    assert result == {"author": "Jane"}
    assert "xmp_custom_payload" not in result


def test_normalize_drops_empty_and_none_values():
    result = file_metadata._normalize({"author": "", "title": None, "producer": "Real Producer"})
    assert result == {"producer": "Real Producer"}


# ── _parse_pdf_date ───────────────────────────────────────────────────────────

def test_parse_pdf_date_full_form_with_offset():
    assert file_metadata._parse_pdf_date("D:20230115120000-05'00'") == "2023-01-15T12:00:00-05:00"


def test_parse_pdf_date_utc_z_form():
    assert file_metadata._parse_pdf_date("D:20230115120000Z") == "2023-01-15T12:00:00+00:00"


def test_parse_pdf_date_bare_year_month_day_no_time():
    assert file_metadata._parse_pdf_date("D:20230115") == "2023-01-15T00:00:00"


def test_parse_pdf_date_garbage_returns_none():
    assert file_metadata._parse_pdf_date("not a pdf date") is None
    assert file_metadata._parse_pdf_date("") is None
    assert file_metadata._parse_pdf_date(None) is None


# ── check_date_mismatch ───────────────────────────────────────────────────────

def _extraction(created: str, date_of_document: str) -> dict:
    return {"document": {"file_metadata": {"created": created}, "date_of_document": date_of_document}}


def test_check_date_mismatch_warns_on_large_gap():
    ext = _extraction("2023-06-01T00:00:00", "2019-01-01")
    warnings = file_metadata.check_date_mismatch(ext, {"ocr_used": False})
    assert len(warnings) == 1
    assert "2023-06-01" in warnings[0] and "2019-01-01" in warnings[0]


def test_check_date_mismatch_silent_below_threshold():
    # Same document year, plausible re-save a few months later — not a lead.
    ext = _extraction("2019-08-01T00:00:00", "2019-01-01")
    assert file_metadata.check_date_mismatch(ext, {"ocr_used": False}) == []


def test_check_date_mismatch_silent_when_ocr_used():
    """The single most important behaviour: a scanned document's file creation date describes
    the scan, not the original, so the mismatch must never fire when ocr_used is true —
    otherwise every scanned exhibit in the vault produces a false lead."""
    ext = _extraction("2023-06-01T00:00:00", "2019-01-01")
    assert file_metadata.check_date_mismatch(ext, {"ocr_used": True}) == []


def test_check_date_mismatch_handles_bare_year_document_date():
    # Upper bound of "2019" is Dec 31 2019; created 2023-06-01 is well past a year later.
    ext = _extraction("2023-06-01T00:00:00", "2019")
    assert len(file_metadata.check_date_mismatch(ext, {})) == 1
    # A created date just inside a year of Dec 31 2019 must stay silent.
    ext2 = _extraction("2020-06-01T00:00:00", "2019")
    assert file_metadata.check_date_mismatch(ext2, {}) == []


def test_check_date_mismatch_handles_year_month_document_date():
    # Upper bound of "2019-02" is Feb 28 2019.
    ext = _extraction("2023-06-01T00:00:00", "2019-02")
    assert len(file_metadata.check_date_mismatch(ext, {})) == 1


def test_check_date_mismatch_silent_when_created_missing():
    ext = {"document": {"date_of_document": "2019-01-01"}}
    assert file_metadata.check_date_mismatch(ext, {}) == []


def test_check_date_mismatch_silent_when_date_of_document_missing():
    ext = {"document": {"file_metadata": {"created": "2023-06-01T00:00:00"}}}
    assert file_metadata.check_date_mismatch(ext, {}) == []


def test_check_date_mismatch_silent_when_created_unparseable():
    ext = _extraction("not a date", "2019-01-01")
    assert file_metadata.check_date_mismatch(ext, {}) == []


def test_check_date_mismatch_silent_when_date_of_document_unparseable():
    ext = _extraction("2023-06-01T00:00:00", "sometime in 2019")
    assert file_metadata.check_date_mismatch(ext, {}) == []


# ── OOXML extended properties (docProps/app.xml) ─────────────────────────────
#
# `Company` and `TotalTime` are not exposed by python-docx/python-pptx/openpyxl — they live in
# the extended-properties part, which file_metadata reads out of the OOXML zip directly.

def _set_app_xml(path, body: str) -> None:
    """Rewrite an OOXML file's docProps/app.xml with `body`, preserving every other part."""
    import shutil
    import zipfile
    src = path.with_suffix(path.suffix + ".orig")
    shutil.move(str(path), str(src))
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(path, "w") as zout:
        for item in zin.infolist():
            if item.filename != "docProps/app.xml":
                zout.writestr(item, zin.read(item.filename))
        zout.writestr("docProps/app.xml", body)


_APP_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
    'extended-properties"><Company>Shell Holdings Ltd</Company>'
    "<TotalTime>3</TotalTime></Properties>"
)


def test_extract_docx_reads_company_and_total_edit_minutes_from_app_xml(tmp_path):
    docx = pytest.importorskip("docx")
    p = tmp_path / "resolution.docx"
    docx.Document().save(str(p))
    _set_app_xml(p, _APP_XML)

    md = file_metadata.extract(p)
    assert md["company"] == "Shell Holdings Ltd"
    # Coerced to int by _normalize (it is in _NUMERIC_KEYS), not left as the raw XML string.
    assert md["total_edit_minutes"] == 3


def test_extract_xlsx_reads_company_from_app_xml(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    p = tmp_path / "ledger.xlsx"
    openpyxl.Workbook().save(str(p))
    _set_app_xml(p, _APP_XML)

    assert file_metadata.extract(p)["company"] == "Shell Holdings Ltd"


def test_app_xml_billion_laughs_bomb_is_refused_not_expanded(tmp_path):
    """A hostile .docx must not be able to take chew down with an entity-expansion bomb. The
    stdlib xml.etree parser expands internal entities; file_metadata uses defusedxml, which
    refuses — so the reader degrades to {} for app.xml rather than exploding.

    Note this assertion only means something because defusedxml is imported at module scope in
    file_metadata: were it imported lazily inside the reader, a *missing* defusedxml would be
    swallowed by that reader's best-effort `except Exception`, app.xml would yield {}, and this
    test would pass with no XML hardening in place at all."""
    docx = pytest.importorskip("docx")
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE Properties ['
        '<!ENTITY a "AAAAAAAAAA">'
        '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
        '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">'
        ']><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
        'extended-properties"><Company>&c;</Company></Properties>'
    )
    p = tmp_path / "hostile.docx"
    d = docx.Document()
    d.core_properties.author = "J. Doe"
    d.save(str(p))
    _set_app_xml(p, bomb)

    md = file_metadata.extract(p)              # must not raise, must not hang
    assert "company" not in md                 # the bomb was refused, not expanded
    assert md["author"] == "J. Doe"            # core properties still survive the app.xml failure


def test_app_xml_missing_part_degrades_to_core_properties_only(tmp_path):
    docx = pytest.importorskip("docx")
    p = tmp_path / "plain.docx"
    d = docx.Document()
    d.core_properties.author = "J. Doe"
    d.save(str(p))

    md = file_metadata.extract(p)
    assert md["author"] == "J. Doe"
    assert "company" not in md
