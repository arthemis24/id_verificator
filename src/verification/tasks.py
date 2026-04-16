import os
from celery import shared_task
from verification.models import Document


def _check_ocr_match(doc: Document, ocr_data: dict) -> tuple[bool, dict]:
    """
    Two-level name check:

    Level 1 — label extraction:
        If OCR found a labelled NOM/PRENOM field, compare it directly
        against the submitted value.

    Level 2 — full-text presence (fallback):
        If no labelled field was found, normalize both the submitted name
        and the entire OCR text and check whether the name appears anywhere
        in the concatenated text.

    A field with no OCR data at all (empty raw_text) is skipped.

    Returns (match: bool, detail: dict).
    """
    from verification.ai_utils import names_present_in_text, _normalize

    raw_text   = ocr_data.get("raw_text", "")
    label_first = ocr_data.get("first_name")
    label_last  = ocr_data.get("last_name")
    detail      = {"method": None}

    # ── Level 1: labelled fields found ───────────────────────────────────────
    if label_first is not None or label_last is not None:
        detail["method"] = "label"
        first_ok = (label_first is None) or (_normalize(label_first) == _normalize(doc.first_name))
        last_ok  = (label_last  is None) or (_normalize(label_last)  == _normalize(doc.last_name))
        detail.update({"first_name_match": first_ok, "last_name_match": last_ok})
        return (first_ok and last_ok), detail

    # ── Level 2: no labels — search full text ────────────────────────────────
    if not raw_text.strip():
        # OCR produced nothing — skip name check entirely
        detail["method"] = "skipped_no_text"
        return True, detail

    detail["method"] = "fulltext"
    match, presence = names_present_in_text(doc.first_name, doc.last_name, raw_text)
    detail.update(presence)
    return match, detail


@shared_task
def verify_document(document_id: int) -> dict:
    """
    Async task that runs:
      1. OCR on the uploaded ID document → two-level name check + date extraction
      2. DeepFace face comparison between the document photo and the selfie

    Stores the result in Document.verification_result and sets Document.verified.
    """
    try:
        doc = Document.objects.get(id=document_id)
    except Document.DoesNotExist:
        return {"verified": False, "error": "Document introuvable"}

    doc_path    = doc.doc_file.path
    selfie_path = doc.selfie_file.path

    if not os.path.exists(doc_path) or not os.path.exists(selfie_path):
        result = {"verified": False, "error": "Fichier manquant"}
        doc.verification_result = result
        doc.verified = False
        doc.save()
        return result

    # ── OCR ──────────────────────────────────────────────────────────────────
    from verification.ai_utils import ocr_extract_info
    ocr_data           = ocr_extract_info(doc_path)
    ocr_match, ocr_detail = _check_ocr_match(doc, ocr_data)

    # ── Face verification ─────────────────────────────────────────────────────
    from django.conf import settings as django_settings
    face_model     = getattr(django_settings, "FACE_MODEL",     "VGG-Face")
    face_threshold = getattr(django_settings, "FACE_THRESHOLD", None)
    if face_threshold is not None:
        try:
            face_threshold = float(face_threshold)
        except (ValueError, TypeError):
            face_threshold = None

    try:
        from deepface import DeepFace
        verify_kwargs = dict(
            img1_path=doc_path,
            img2_path=selfie_path,
            model_name=face_model,
            enforce_detection=False,
        )
        if face_threshold is not None:
            verify_kwargs["threshold"] = face_threshold

        face_result   = DeepFace.verify(**verify_kwargs)
        face_verified = face_result.get("verified", False)
        face_score    = face_result.get("distance", 0.0)
        face_error    = None
    except Exception as e:
        face_verified = False
        face_score    = 0.0
        face_error    = str(e)

    # ── Aggregate ─────────────────────────────────────────────────────────────
    verification_result = {
        "verified":      face_verified and ocr_match,
        "face_verified": face_verified,
        "face_score":    face_score,
        "ocr_verified":  ocr_match,
        "ocr_data": {
            "first_name":    ocr_data.get("first_name"),
            "last_name":     ocr_data.get("last_name"),
            "birth_date":    ocr_data.get("birth_date"),
            "name_check":    ocr_detail,
            "raw_text":      ocr_data.get("raw_text", ""),
        },
    }
    if face_error:
        verification_result["face_error"] = face_error
    if "error" in ocr_data:
        verification_result["ocr_error"] = ocr_data["error"]

    doc.verification_result = verification_result
    doc.verified = verification_result["verified"]
    doc.save()

    return verification_result
