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
      - returns plain dicts
      - has no side effects on the database
      - is independently unit-testable with mocks
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from verification.models import Document

logger = logging.getLogger(__name__)


# ── OCR ───────────────────────────────────────────────────────────────────────

def run_ocr(doc_path: str) -> dict:
    """
    Extract identity fields from a document image or PDF.

    Returns:
        {first_name, last_name, birth_date, raw_text}  — on success
        {first_name, last_name, birth_date, raw_text, error} — on OCR failure
    """
    from verification.ai_utils import ocr_extract_info
    logger.debug("OCR started: %s", doc_path)
    result = ocr_extract_info(doc_path)
    if "error" in result:
        logger.warning("OCR failed for %s: %s", doc_path, result["error"])
    else:
        logger.debug(
            "OCR complete: first_name=%s last_name=%s birth_date=%s",
            result.get("first_name"), result.get("last_name"), result.get("birth_date"),
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


# ── Aggregation ───────────────────────────────────────────────────────────────

def build_verification_result(
    ocr_data: dict,
    ocr_match: bool,
    ocr_detail: dict,
    face: dict,
) -> dict:
    """
    Assemble the final verification result dict that is stored on the Document
    and optionally delivered via webhook.
    """
    result: dict = {
        "verified":      face["verified"] and ocr_match,
        "face_verified": face["verified"],
        "face_score":    face["score"],
        "ocr_verified":  ocr_match,
        "ocr_data": {
            "first_name": ocr_data.get("first_name"),
            "last_name":  ocr_data.get("last_name"),
            "birth_date": ocr_data.get("birth_date"),
            "name_check": ocr_detail,
            "raw_text":   ocr_data.get("raw_text", ""),
        },
    }
    if face["error"]:
        result["face_error"] = face["error"]
    if "error" in ocr_data:
        result["ocr_error"] = ocr_data["error"]
    return result
