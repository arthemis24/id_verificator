"""
verification.tasks
~~~~~~~~~~~~~~~~~~
Celery task definitions.

Responsibility: scheduling, DB persistence, and webhook dispatch only.
All business logic lives in verification.services.
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request

from celery import shared_task

from verification.models import Document
from verification.services import (
    build_verification_result,
    check_ocr_name_match,
    resolve_expiry_date,
    run_face_verification,
    run_ocr,
)

logger = logging.getLogger(__name__)


@shared_task
def verify_document(document_id: int) -> dict:
    """
    Async task: orchestrate OCR + face verification for a submitted document.

    1. Fetch the Document from the database.
    2. Delegate OCR and face comparison to the service layer.
    3. Resolve expiry date (OCR-extracted takes precedence over user-submitted).
    4. Persist the aggregated result.
    5. Fire the webhook if a callback_url was provided.
    """
    logger.info("verify_document started: document_id=%s", document_id)
    try:
        doc = Document.objects.get(id=document_id)
    except Document.DoesNotExist:
        logger.error("verify_document: document_id=%s not found", document_id)
        return {"verified": False, "error": "Document introuvable"}

    doc_path    = doc.doc_file.path
    selfie_path = doc.selfie_file.path

    if not os.path.exists(doc_path) or not os.path.exists(selfie_path):
        result = {"verified": False, "error": "Fichier manquant"}
        doc.verification_result = result
        doc.verified = False
        doc.save()
        return result

    ocr_data              = run_ocr(doc_path)
    ocr_match, ocr_detail = check_ocr_name_match(doc, ocr_data)
    face                  = run_face_verification(doc_path, selfie_path)
    expiry_date           = resolve_expiry_date(ocr_data.get("expiry_date"), doc.expiry_date)
    result                = build_verification_result(ocr_data, ocr_match, ocr_detail, face, expiry_date)

    doc.verification_result = result
    doc.verified = result["verified"]
    doc.save()

    logger.info(
        "verify_document complete: document_id=%s verified=%s verdict=%s "
        "confidence=%.2f face_score=%.4f ocr_verified=%s id_status=%s",
        document_id,
        result["verified"],
        result.get("verdict"),
        result.get("confidence_score", 0.0),
        result.get("face_score", 0.0),
        result.get("ocr_verified"),
        result.get("id_status"),
    )

    if doc.callback_url:
        _dispatch_webhook(doc.callback_url, document_id, doc, result)

    return result


def _dispatch_webhook(
    url: str,
    document_id: int,
    doc: Document,
    result: dict,
    max_retries: int = 3,
) -> None:
    """
    POST the verification result to the caller-supplied callback URL.
    Retries up to max_retries times with exponential back-off (1 s, 2 s, 4 s).
    Never raises — failures must not affect the task's return value.

    Webhook payload shape:
        document_id     — Document PK
        subject         — identity data submitted by the user + expiry_date
        submitted_at    — ISO 8601 timestamp of the upload
        result          — full verification result including verdict,
                          confidence_score, id_status, and ocr_data
    """
    payload = json.dumps({
        "document_id": document_id,
        "subject": {
            "first_name":    doc.first_name,
            "last_name":     doc.last_name,
            "birth_date":    str(doc.birth_date),
            "expiry_date":   str(doc.expiry_date) if doc.expiry_date else None,
            "document_type": doc.document_type,
            "user_id":       doc.user_id,
            "username":      doc.user.username,
        },
        "submitted_at": doc.created_at.isoformat(),
        "result": result,
    }).encode()

    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                logger.info("Webhook delivered to %s (HTTP %s)", url, resp.status)
                return
        except (urllib.error.URLError, OSError) as exc:
            logger.warning(
                "Webhook attempt %d/%d failed for %s: %s", attempt, max_retries, url, exc
            )
            if attempt < max_retries:
                time.sleep(2 ** (attempt - 1))

    logger.error(
        "Webhook delivery permanently failed for document %s → %s", document_id, url
    )
