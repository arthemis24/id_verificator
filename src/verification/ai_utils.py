import logging
import re
import unicodedata
import pytesseract
from PIL import Image

logger = logging.getLogger(__name__)


# ── Tesseract setup ───────────────────────────────────────────────────────────

_tesseract_configured = False


def _configure_tesseract():
    """Point pytesseract at the binary declared in Django settings (if any)."""
    global _tesseract_configured
    if _tesseract_configured:
        return
    try:
        from django.conf import settings
        cmd = getattr(settings, "TESSERACT_CMD", None)
        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
    except Exception:
        logger.warning("Could not configure Tesseract from Django settings", exc_info=True)
    _tesseract_configured = True


# ── Text extraction ───────────────────────────────────────────────────────────

def _auto_rotate(img: Image.Image) -> Image.Image:
    """
    Use Tesseract OSD to detect page orientation and rotate the image upright.
    Silently returns the original image if OSD fails or reports 0°.
    """
    try:
        osd = pytesseract.image_to_osd(img, config="--psm 0")
        for line in osd.splitlines():
            if line.startswith("Rotate:"):
                angle = int(line.split(":")[1].strip())
                if angle != 0:
                    return img.rotate(angle, expand=True)
    except Exception:
        pass
    return img


def _extract_text_from_file(doc_path: str) -> str:
    """Return raw OCR text from an image or PDF file."""
    _configure_tesseract()
    # PSM 4 (single column, variable text sizes) reads ID cards more reliably
    # than the default PSM 3 which can skip lines in multi-column layouts.
    config = "--psm 4"
    ext = doc_path.lower().rsplit(".", 1)[-1]
    if ext == "pdf":
        from pdf2image import convert_from_path
        pages = convert_from_path(doc_path, dpi=200)
        return "\n".join(
            pytesseract.image_to_string(_auto_rotate(page), lang="fra+eng", config=config)
            for page in pages
        )
    img = _auto_rotate(Image.open(doc_path))
    return pytesseract.image_to_string(img, lang="fra+eng", config=config)


# ── Normalisation ─────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """
    Uppercase + strip accents + keep only ASCII letters and spaces.
    Allows reliable comparison regardless of OCR accent errors.
    e.g. "Mbögning" → "MBOGNING"
    """
    nfd = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return re.sub(r"[^A-Z\s]", "", stripped.upper())


# ── Label-based field parsers ─────────────────────────────────────────────────

_DATE_PATTERN = (
    r"(\d{2}[/\-\.]\d{2}[/\-\.]\d{4}"   # DD/MM/YYYY
    r"|\d{4}[/\-\.]\d{2}[/\-\.]\d{2})"  # YYYY-MM-DD
)

def _parse_birth_date(text: str):
    """Try to extract a birth date string from OCR text."""
    # Label-based search first (most reliable)
    label_pattern = (
        r"(?:Date\s+de\s+naissance|N[eé]e?\s+le|Birth\s*(?:Date|date)?|DOB)[:\s]+"
        + _DATE_PATTERN
    )
    m = re.search(label_pattern, text, re.IGNORECASE)
    if m:
        return m.group(1)
    # Fallback: first bare date in the text
    m = re.search(r"\b" + _DATE_PATTERN + r"\b", text)
    return m.group(1) if m else None


def _parse_expiry_date(text: str):
    """
    Try to extract a document expiry date from OCR text.

    Searches for labelled fields first (most fraud-resistant — user cannot
    supply a fake value if we read directly from the document image).
    Returns the raw date string so the caller can decide how to parse it.
    """
    label_pattern = (
        r"(?:Date\s+d['‘’]expir(?:ation)?|Expir(?:y(?:\s+date)?|es?(?:\s+(?:le|on))?)|"
        r"Valid(?:ity)?\s+(?:until|through|(?:date\s+de\s+)?validit[eé])?|"
        r"Val\.|Exp\.?|Date\s+de\s+validit[eé])[:\s]+"
        + _DATE_PATTERN
    )
    m = re.search(label_pattern, text, re.IGNORECASE)
    return m.group(1) if m else None


_NAME_VALUE = (
    r"([A-Z][A-Za-z\-]+(?:[ \t]+[A-Z][A-Za-z\-]+)*?)"
    r"(?=[ \t]*(?:\n|$)|[ \t]+[a-z\d]|[ \t]+[A-Z][A-Za-z]*(?:[ \t]+[A-Z][A-Za-z]*)?[ \t]*:)"
)
# Handles ": ", "\n", and " / Alternate label\n" separators (e.g. "Nom / Surname\n").
_NAME_SEP = r"(?:(?:[ \t]*/[^\n]*)?\s*[:\n]\s*|[:\s]+)"
_NAME_PFX = r"(?:\d+\.\s*)?"  # optional numbered prefix: "1. Nom", "2. Prénoms"

_NAME_PATTERNS: dict[str, re.Pattern] = {
    "first_name": re.compile(
        _NAME_PFX + r"(?:Pr[eé]noms?|First\s*Names?|PRENOM|FIRST\s*NAME|Given\s*Names?)" + _NAME_SEP + _NAME_VALUE
    ),
    "last_name": re.compile(
        _NAME_PFX + r"(?:\bNom\b|\bNOM\b|Last\s*Name|LAST\s*NAME|Surname|SURNAME)" + _NAME_SEP + _NAME_VALUE
    ),
}


def _parse_name(text: str, field: str):
    """
    Try to extract a name from a labelled field on the document.
    Supports French and English ID label conventions.
    """
    pattern = _NAME_PATTERNS.get(field)
    if not pattern:
        return None
    m = pattern.search(text)
    return m.group(1) if m else None


# ── Name presence check (fallback) ───────────────────────────────────────────

def names_present_in_text(first_name: str, last_name: str, raw_text: str) -> tuple[bool, dict]:
    """
    Fallback check: verify that both the submitted first and last name
    appear somewhere in the concatenated OCR text, after normalization.

    Returns (match: bool, detail: dict) where detail shows what was compared.
    """
    norm_text  = _normalize(raw_text)
    norm_first = _normalize(first_name)
    norm_last  = _normalize(last_name)

    first_found = norm_first in norm_text
    last_found  = norm_last  in norm_text

    return (first_found and last_found), {
        "first_name_found": first_found,
        "last_name_found":  last_found,
        "normalized_text_sample": norm_text[:300],   # first 300 chars for debugging
    }


# ── Public entry point ────────────────────────────────────────────────────────

def ocr_extract_info(doc_path: str) -> dict:
    """
    Extract identity fields from an ID document using OCR.

    Returns:
        first_name  – from label, or None
        last_name   – from label, or None
        birth_date  – detected date string, or None
        raw_text    – full OCR output
    """
    try:
        raw_text = _extract_text_from_file(doc_path)
    except Exception as e:
        return {
            "first_name": None,
            "last_name":  None,
            "birth_date": None,
            "raw_text":   "",
            "error":      str(e),
        }

    return {
        "first_name":  _parse_name(raw_text, "first_name"),
        "last_name":   _parse_name(raw_text, "last_name"),
        "birth_date":  _parse_birth_date(raw_text),
        "expiry_date": _parse_expiry_date(raw_text),
        "raw_text":    raw_text,
    }


