"""File-intrinsic embedded metadata capture (#369).

Best-effort, deterministic extraction of a file's own embedded metadata — PDF DocumentInfo,
Office core properties, image EXIF, audio/video container tags. This is a claim the *file*
makes about itself (who supposedly authored it, when it was supposedly created), distinct
from the *processing* facts the pipeline asserts about how it was read (``ocr_used``,
``source_type`` — see ``preprocess.py``'s ``metadata`` key). ``extract()`` never raises: a
corrupt file, an unsupported suffix, or an unparseable field yields ``{}`` or a partial dict,
never an exception that could break chew.

The output is a small, flat, allowlisted dict (see ``_ALLOWED_KEYS``) — never a raw dump of
native metadata. This block later reaches the extraction prompt as untrusted document data
(the same posture the codebase already applies to the ``.yml`` sidecar): every value is
coerced to ``str`` and truncated, since a malicious XMP/EXIF payload could otherwise inject
arbitrary text into the prompt.

Also home to ``check_date_mismatch``, the deterministic post-flight annotation that flags a
document whose embedded creation date postdates its claimed date by a suspicious margin —
modeled directly on ``quote_verify.resolve_quotes``.
"""

import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Imported at module scope, unlike every other reader's library (which are imported lazily inside
# their reader): a missing defusedxml must fail loudly here rather than be swallowed by the
# best-effort `except Exception` in the app.xml reader, which would silently disable the XML
# hardening — and leave the entity-bomb test passing for the wrong reason. See I6.
import defusedxml.ElementTree as _defused_ET

# Untrusted-content mitigation (docstring above): every value is capped at this length before
# it can reach the extraction prompt.
_MAX_VALUE_LEN = 200
_FFPROBE_TIMEOUT = 10   # seconds

# The single flat shape every family's native fields are normalized onto. Anything not in this
# set is dropped, never passed through — this is the allowlist, not a filter of known-bad keys.
_ALLOWED_KEYS = {
    "author", "created", "modified", "creator_tool", "producer", "title", "company",
    "last_modified_by", "revision", "last_printed", "camera_make", "camera_model",
    "gps", "duration_seconds", "encoder", "total_edit_minutes",
}
# May stay int/float; everything else -> str.
_NUMERIC_KEYS = {"revision", "duration_seconds", "total_edit_minutes"}

_PDF_DATE_RE = re.compile(
    r"^D:(?P<year>\d{4})(?P<month>\d{2})?(?P<day>\d{2})?"
    r"(?P<hour>\d{2})?(?P<minute>\d{2})?(?P<second>\d{2})?"
    r"(?:(?P<tzsign>[+\-Zz])(?:(?P<tzhour>\d{2})'?(?P<tzminute>\d{2})?'?)?)?"
)
_EXIF_DATE_RE = re.compile(r"^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})$")
_GPS_NEGATIVE_REFS = {"S", "W"}


def _clip(value: object) -> str:
    return str(value)[:_MAX_VALUE_LEN]


def _normalize(raw: dict) -> dict:
    """Filter native fields down to the allowlist, coerce/truncate values, drop empties."""
    out: dict = {}
    for key, value in raw.items():
        if key not in _ALLOWED_KEYS or value is None or value == "":
            continue
        if key in _NUMERIC_KEYS:
            try:
                out[key] = int(value) if key == "revision" else float(value)
            except (TypeError, ValueError):
                continue
        else:
            out[key] = _clip(value)
    return out


def _parse_pdf_date(raw: str) -> "str | None":
    """Parse a PDF DocumentInfo date (``D:20230115120000-05'00'``) into an ISO-8601 string.
    Returns None for anything that doesn't match — never guesses, never raises."""
    if not raw:
        return None
    m = _PDF_DATE_RE.match(raw.strip())
    if not m:
        return None
    g = m.groupdict()
    try:
        dt = datetime(
            int(g["year"]), int(g["month"] or 1), int(g["day"] or 1),
            int(g["hour"] or 0), int(g["minute"] or 0), int(g["second"] or 0),
        )
    except ValueError:
        return None
    sign = g["tzsign"]
    if sign in ("+", "-"):
        delta = timedelta(hours=int(g["tzhour"] or 0), minutes=int(g["tzminute"] or 0))
        dt = dt.replace(tzinfo=timezone(delta if sign == "+" else -delta))
    elif sign in ("Z", "z"):
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _iso(value: object) -> "str | None":
    """``.isoformat()`` a datetime-like value; anything else (None, a bad parse) is dropped."""
    return value.isoformat() if hasattr(value, "isoformat") else None


# ── PDF ───────────────────────────────────────────────────────────────────────

def _read_pdf(path: Path) -> dict:
    import pypdf
    md = pypdf.PdfReader(str(path)).metadata
    if md is None:
        return {}
    out = {}
    if md.author:
        out["author"] = md.author
    if md.producer:
        out["producer"] = md.producer
    if md.creator:
        out["creator_tool"] = md.creator
    if md.title:
        out["title"] = md.title
    created = _parse_pdf_date(md.get("/CreationDate", "") or "")
    if created:
        out["created"] = created
    modified = _parse_pdf_date(md.get("/ModDate", "") or "")
    if modified:
        out["modified"] = modified
    return out


# ── Office (docx / pptx / xlsx) ──────────────────────────────────────────────

def _core_properties_dict(cp) -> dict:
    """Shared field mapping for python-docx/python-pptx core_properties and openpyxl's
    workbook.properties — the attribute names line up across all three."""
    out = {}
    author = getattr(cp, "author", None) or getattr(cp, "creator", None)
    if author:
        out["author"] = author
    last_modified_by = getattr(cp, "last_modified_by", None) or getattr(cp, "lastModifiedBy", None)
    if last_modified_by:
        out["last_modified_by"] = last_modified_by
    if getattr(cp, "revision", None):
        out["revision"] = cp.revision
    if getattr(cp, "title", None):
        out["title"] = cp.title
    created = _iso(getattr(cp, "created", None))
    if created:
        out["created"] = created
    modified = _iso(getattr(cp, "modified", None))
    if modified:
        out["modified"] = modified
    last_printed = _iso(getattr(cp, "last_printed", None) or getattr(cp, "lastPrinted", None))
    if last_printed:
        out["last_printed"] = last_printed
    return out


_APP_XML_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}"


def _read_ooxml_app_properties(path: Path) -> dict:
    """`Company` and `TotalTime` (total editing time, in minutes) live in OOXML's *extended*
    properties part, `docProps/app.xml` — which none of python-docx, python-pptx, or openpyxl
    expose. All three formats are OOXML zips, so one reader covers them.

    Both are investigative signals the core properties can't give us: a shared `Company` across
    supposedly unrelated documents means a shared template (the same cluster signal as a shared
    registered agent), and a long report with a few minutes of total editing time was assembled,
    not written. Best-effort — a missing part, malformed XML, or a hostile payload yields ``{}``.

    Parsed with `defusedxml`, never the stdlib `xml.etree`: this XML comes straight out of an
    untrusted document, and the stdlib parser expands internal entities, so a malicious `.docx`
    could hand us a billion-laughs bomb and take chew down. Any XML we ever parse from a
    document is attacker-controlled — keep it on defusedxml (imported at module scope, so its
    absence is an ImportError rather than a silent `{}` from the except below). See I6."""
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            root = _defused_ET.fromstring(z.read("docProps/app.xml"))
    except Exception:
        return {}
    out = {}
    company = root.findtext(f"{_APP_XML_NS}Company")
    if company and company.strip():
        out["company"] = company.strip()
    total_time = root.findtext(f"{_APP_XML_NS}TotalTime")
    if total_time and total_time.strip():
        out["total_edit_minutes"] = total_time.strip()   # _normalize coerces to int
    return out


def _read_docx(path: Path) -> dict:
    import docx
    return {**_core_properties_dict(docx.Document(str(path)).core_properties),
            **_read_ooxml_app_properties(path)}


def _read_pptx(path: Path) -> dict:
    import pptx
    return {**_core_properties_dict(pptx.Presentation(str(path)).core_properties),
            **_read_ooxml_app_properties(path)}


def _read_xlsx(path: Path) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(str(path), read_only=True)
    try:
        core = _core_properties_dict(wb.properties)
    finally:
        wb.close()
    return {**core, **_read_ooxml_app_properties(path)}


# ── Images (EXIF) ────────────────────────────────────────────────────────────

def _dms_to_decimal(dms, ref) -> "float | None":
    try:
        degrees, minutes, seconds = (float(v) for v in dms)
    except (TypeError, ValueError):
        return None
    value = degrees + minutes / 60 + seconds / 3600
    return -value if ref in _GPS_NEGATIVE_REFS else value


def _read_image(path: Path) -> dict:
    from PIL import ExifTags, Image
    out: dict = {}
    with Image.open(path) as img:
        exif = img.getexif()
        if not exif:
            return {}
        tags = {ExifTags.TAGS.get(tag_id, tag_id): value for tag_id, value in exif.items()}
        if tags.get("Make"):
            out["camera_make"] = tags["Make"]
        if tags.get("Model"):
            out["camera_model"] = tags["Model"]
        date_raw = tags.get("DateTimeOriginal") or tags.get("DateTime")
        if date_raw:
            m = _EXIF_DATE_RE.match(str(date_raw).strip())
            if m:
                y, mo, d, h, mi, s = (int(x) for x in m.groups())
                try:
                    out["created"] = datetime(y, mo, d, h, mi, s).isoformat()
                except ValueError:
                    pass
        gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo) if hasattr(ExifTags, "IFD") else {}
        if gps_ifd:
            gps = {ExifTags.GPSTAGS.get(tag_id, tag_id): value for tag_id, value in gps_ifd.items()}
            lat = _dms_to_decimal(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef"))
            lon = _dms_to_decimal(gps.get("GPSLongitude"), gps.get("GPSLongitudeRef"))
            if lat is not None and lon is not None:
                out["gps"] = f"{lat:.6f},{lon:.6f}"
    return out


# ── Audio/video (ffprobe container tags) ─────────────────────────────────────

def _parse_ffprobe_time(raw: str) -> "str | None":
    """ffprobe's creation_time tag is already ISO-8601 (e.g. '2023-01-15T12:00:00.000000Z').
    Round-tripped through fromisoformat/isoformat to validate and normalize it deterministically
    rather than trusting the container's formatting verbatim."""
    try:
        return datetime.fromisoformat(raw.strip().replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _read_av(path: Path) -> dict:
    if not shutil.which("ffprobe"):
        return {}
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=_FFPROBE_TIMEOUT,
        )
        data = json.loads(proc.stdout)
    except Exception:
        return {}
    fmt = data.get("format", {}) or {}
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}   # casing varies by container
    out = {}
    author = tags.get("artist") or tags.get("author")
    if author:
        out["author"] = author
    if tags.get("encoder"):
        out["encoder"] = tags["encoder"]
    if tags.get("title"):
        out["title"] = tags["title"]
    created = tags.get("creation_time")
    if created:
        parsed = _parse_ffprobe_time(created)
        if parsed:
            out["created"] = parsed
    duration = fmt.get("duration")
    if duration:
        try:
            out["duration_seconds"] = float(duration)
        except (TypeError, ValueError):
            pass
    return out


_DISPATCH = {}
for _suf in (".pdf",):
    _DISPATCH[_suf] = _read_pdf
_DISPATCH[".docx"] = _read_docx
_DISPATCH[".pptx"] = _read_pptx
_DISPATCH[".xlsx"] = _read_xlsx
for _suf in (".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"):
    _DISPATCH[_suf] = _read_image
for _suf in (".mp4", ".avi", ".mov", ".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac"):
    _DISPATCH[_suf] = _read_av


def extract(path: Path) -> dict:
    """Best-effort, deterministic, never-raising extraction of ``path``'s embedded metadata,
    normalized to the common allowlisted shape (see module docstring). Returns ``{}`` for an
    unsupported suffix, a file carrying no metadata, or on any reader failure whatsoever
    (corrupt file, unparseable field, missing ``ffprobe``, etc.)."""
    reader = _DISPATCH.get(path.suffix.lower())
    if reader is None:
        return {}
    try:
        raw = reader(path)
    except Exception:
        return {}
    return _normalize(raw)


# ── Deterministic post-flight date-mismatch check ────────────────────────────

# Warn only once the file's embedded creation date postdates the claimed document date by at
# least this many days. A tight threshold would fire on every document that was merely scanned
# or re-saved a few months after the fact; a year is the conservative bar where the gap becomes
# a genuine lead rather than routine handling noise.
DATE_MISMATCH_THRESHOLD_DAYS = 365

_YEAR_RE = re.compile(r"^\d{4}$")
_YEAR_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def _doc_date_upper_bound(date_of_document: str) -> "datetime | None":
    """The latest moment `date_of_document` could plausibly mean, so a partial date only
    triggers the warning when the gap is unambiguous even under the most generous reading:
    a bare year -> its last day (Dec 31); a year-month -> the last day of that month."""
    if not date_of_document:
        return None
    if _YEAR_RE.match(date_of_document):
        return datetime(int(date_of_document), 12, 31)
    if _YEAR_MONTH_RE.match(date_of_document):
        year, month = (int(x) for x in date_of_document.split("-"))
        first_of_next = datetime(year + (1 if month == 12 else 0), (month % 12) + 1, 1)
        return first_of_next - timedelta(days=1)
    try:
        return datetime.fromisoformat(date_of_document)
    except ValueError:
        return None


def check_date_mismatch(extraction: dict, processing: dict) -> list[str]:
    """Flag a document whose embedded file creation date postdates its claimed
    ``date_of_document`` by ``DATE_MISMATCH_THRESHOLD_DAYS`` or more — e.g. a "2019 board
    resolution" whose PDF was actually created in 2023. Modeled on
    ``quote_verify.resolve_quotes``: an annotation only, never blocks the document.

    Suppressed entirely when ``processing.ocr_used`` is true — a scanned document's file
    carries the scanner's creation date, so the "mismatch" is expected and meaningless there;
    without this suppression every scanned exhibit in the vault would produce a false lead.
    """
    if (processing or {}).get("ocr_used"):
        return []

    doc = extraction.get("document", {}) or {}
    created_raw = (doc.get("file_metadata") or {}).get("created")
    date_of_document = doc.get("date_of_document")
    if not created_raw or not date_of_document:
        return []

    try:
        created_dt = datetime.fromisoformat(created_raw)
    except ValueError:
        return []
    if created_dt.tzinfo is not None:
        created_dt = created_dt.replace(tzinfo=None)

    upper_bound = _doc_date_upper_bound(date_of_document)
    if upper_bound is None:
        return []

    gap_days = (created_dt - upper_bound).days
    if gap_days < DATE_MISMATCH_THRESHOLD_DAYS:
        return []

    return [
        f"document.file_metadata.created ({created_raw}) postdates document.date_of_document "
        f"({date_of_document}) by {gap_days} days — the file may have been created, rescanned, "
        f"or resaved long after the date the document claims; worth checking whether this is a "
        f"copy, a template, or a backdated record"
    ]
