"""
verification.services
~~~~~~~~~~~~~~~~~~~~~
Pure business logic for the identity verification pipeline.

Why a service layer?
    The original code mixed orchestration, OCR, face comparison, and DB writes
    all inside a Celery task. That makes each concern impossible to test in
    isolation: you cannot unit-test the OCR name-matching logic without
    spinning up a full task execution context.

    This module owns the *what* (the algorithm). tasks.py owns the *when*
    (async scheduling + DB persistence). Views own the *how* (HTTP protocol).

    Each function here:
      - takes plain Python values as arguments
      - returns plain dicts or scalars
      - has no side effects on the database
      - is independently unit-testable with mocks
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from verification.models import Document

logger = logging.getLogger(__name__)


# ── OCR ───────────────────────────────────────────────────────────────────────

def run_ocr(doc_path: str) -> dict:
    """
    Extract identity fields from a document image or PDF.

    Returns:
        {first_name, last_name, birth_date, expiry_date, raw_text}  — on success
        {first_name, last_name, birth_date, expiry_date, raw_text, error} — on failure
    """
    from verification.ai_utils import ocr_extract_info
    logger.debug("OCR started: %s", doc_path)
    result = ocr_extract_info(doc_path)
    if "error" in result:
        logger.warning("OCR failed for %s: %s", doc_path, result["error"])
    else:
        logger.debug(
            "OCR complete: first_name=%s last_name=%s birth_date=%s expiry_date=%s",
            result.get("first_name"), result.get("last_name"),
            result.get("birth_date"), result.get("expiry_date"),
        )
    return result


def check_ocr_name_match(doc: "Document", ocr_data: dict) -> tuple[bool, dict]:
    """
    Two-level name check against OCR output.

    Level 1 — labelled fields:
        If OCR found a NOM/PRENOM label, compare directly against submitted
        values after Unicode normalisation (strips accents, uppercases).

    Level 2 — full-text presence (fallback):
        If no labelled field was found, check whether both names appear
        anywhere in the normalised OCR text.

    Empty OCR output (OCR produced nothing) is treated as a pass — the check
    is skipped rather than failing a legitimate document that Tesseract could
    not read.

    Returns (match: bool, detail: dict)
    """
    from verification.ai_utils import names_present_in_text, _normalize

    raw_text    = ocr_data.get("raw_text", "")
    label_first = ocr_data.get("first_name")
    label_last  = ocr_data.get("last_name")
    detail: dict = {"method": None}

    if label_first is not None or label_last is not None:
        detail["method"] = "label"
        first_ok = (label_first is None) or (
            _normalize(label_first) == _normalize(doc.first_name)
        )
        last_ok = (label_last is None) or (
            _normalize(label_last) == _normalize(doc.last_name)
        )
        detail.update({"first_name_match": first_ok, "last_name_match": last_ok})
        return (first_ok and last_ok), detail

    if not raw_text.strip():
        detail["method"] = "skipped_no_text"
        return True, detail

    detail["method"] = "fulltext"
    match, presence = names_present_in_text(doc.first_name, doc.last_name, raw_text)
    detail.update(presence)
    return match, detail


# ── Face verification ─────────────────────────────────────────────────────────

def run_face_verification(doc_path: str, selfie_path: str) -> dict:
    """
    Compare the face in the ID document against the selfie using DeepFace.

    Model and threshold are read from Django settings so they can be tuned
    per environment without code changes.

    Returns:
        {verified: bool, score: float, error: str | None}
    """
    from django.conf import settings as django_settings
    from deepface import DeepFace

    model     = getattr(django_settings, "FACE_MODEL", "VGG-Face")
    threshold = _parse_threshold(getattr(django_settings, "FACE_THRESHOLD", None))

    kwargs: dict = dict(
        img1_path=doc_path,
        img2_path=selfie_path,
        model_name=model,
        enforce_detection=False,
    )
    if threshold is not None:
        kwargs["threshold"] = threshold

    try:
        logger.debug("Face verification started: model=%s threshold=%s", model, threshold)
        result = DeepFace.verify(**kwargs)
        verified = result.get("verified", False)
        score    = result.get("distance", 0.0)
        logger.info(
            "Face verification complete: verified=%s score=%.4f model=%s",
            verified, score, model,
        )
        return {"verified": verified, "score": score, "error": None}
    except Exception as exc:
        logger.exception("DeepFace verification failed for %s / %s", doc_path, selfie_path)
        return {"verified": False, "score": 0.0, "error": str(exc)}


def _parse_threshold(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


# ── Expiry & scoring ──────────────────────────────────────────────────────────

def resolve_expiry_date(ocr_expiry_str: str | None, user_expiry_date: date | None) -> date | None:
    """
    Determine the expiry date to use for the ID_EXPIRED check.

    OCR-extracted value takes precedence over the user-submitted value.
    Rationale: a user can lie about an expiry date on the form, but cannot
    alter the date printed on the physical document that Tesseract reads.
    Falls back to user-submitted when OCR could not find a date.

    Returns a datetime.date or None.
    """
    if ocr_expiry_str:
        parsed = _parse_date_str(ocr_expiry_str)
        if parsed:
            logger.debug("Expiry date resolved from OCR: %s", parsed)
            return parsed
    if user_expiry_date:
        logger.debug("Expiry date resolved from user input: %s", user_expiry_date)
    return user_expiry_date


def _parse_date_str(s: str) -> date | None:
    """Parse a date string produced by OCR into a datetime.date."""
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def check_id_expiry(expiry_date: date | None, today: date | None = None) -> str:
    """
    Classify the document's validity based on its expiry date.

    Returns:
        "VALID"      — expiry date is today or in the future
        "ID_EXPIRED" — expiry date is in the past
        "UNKNOWN"    — no expiry date was available (OCR missed it and user
                       did not submit one); verification proceeds but expiry
                       status cannot be determined
    """
    if expiry_date is None:
        return "UNKNOWN"
    return "ID_EXPIRED" if expiry_date < (today or date.today()) else "VALID"


def compute_confidence_score(face_score: float, ocr_match: bool, threshold: float = 0.50) -> float:
    """
    Combine face distance and OCR result into a single 0–100 confidence score.

    Weighting:
        Face contributes 70% — continuous signal (distance vs threshold).
        OCR  contributes 30% — binary pass/fail.

    Formula:
        face_confidence = max(0, (1 - face_score / threshold)) × 100
        ocr_confidence  = 100 if ocr_match else 0
        score           = face_confidence × 0.70 + ocr_confidence × 0.30

    Example:
        face_score=0.032, threshold=0.50, ocr_match=True
        → face_confidence = (1 - 0.032/0.50) × 100 = 93.6
        → score = 93.6 × 0.70 + 100 × 0.30 = 95.52
    """
    face_confidence = max(0.0, (1.0 - face_score / threshold)) * 100.0
    ocr_confidence  = 100.0 if ocr_match else 0.0
    return round(face_confidence * 0.7 + ocr_confidence * 0.3, 2)


# ── Aggregation ───────────────────────────────────────────────────────────────

def build_verification_result(
    ocr_data: dict,
    ocr_match: bool,
    ocr_detail: dict,
    face: dict,
    expiry_date: date | None = None,
    threshold: float = 0.50,
) -> dict:
    """
    Assemble the final verification result stored on the Document and
    delivered via webhook.

    Fields:
        verified        — True only if both face and OCR checks passed
        verdict         — human-readable "MATCH" / "NOT MATCH"
        confidence_score — 0–100 combining face distance (70%) and OCR (30%)
        face_verified   — DeepFace decision
        face_score      — raw ArcFace distance (lower = more similar)
        ocr_verified    — name match result
        id_status       — "VALID" | "ID_EXPIRED" | "UNKNOWN"
        ocr_data        — extracted fields + name check detail
    """
    verified  = face["verified"] and ocr_match
    id_status = check_id_expiry(expiry_date)

    result: dict = {
        "verified":         verified,
        "verdict":          "MATCH" if verified else "NOT MATCH",
        "confidence_score": compute_confidence_score(face["score"], ocr_match, threshold),
        "face_verified":    face["verified"],
        "face_score":       face["score"],
        "ocr_verified":     ocr_match,
        "id_status":        id_status,
        "ocr_data": {
            "first_name":  ocr_data.get("first_name"),
            "last_name":   ocr_data.get("last_name"),
            "birth_date":  ocr_data.get("birth_date"),
            "expiry_date": ocr_data.get("expiry_date"),
            "name_check":  ocr_detail,
            "raw_text":    ocr_data.get("raw_text", ""),
        },
    }
    if face["error"]:
        result["face_error"] = face["error"]
    if "error" in ocr_data:
        result["ocr_error"] = ocr_data["error"]
    return result
