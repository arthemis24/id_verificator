from celery import shared_task
from verification.models import Document
from deepface import DeepFace
import os

@shared_task
def verify_document(document_id):
    """
    Vérifie le document et le selfie associé via DeepFace.
    Retourne un dictionnaire contenant:
        - verified: True/False
        - score: similarité faciale (0 à 1)
    """
    try:
        doc = Document.objects.get(id=document_id)

        doc_path = doc.doc_file.path
        selfie_path = doc.selfie_file.path

        # Vérification que les fichiers existent
        if not os.path.exists(doc_path) or not os.path.exists(selfie_path):
            return {"verified": False, "score": 0.0, "error": "Fichier manquant"}

        # Appel de DeepFace
        try:
            result = DeepFace.verify(img1_path=doc_path, img2_path=selfie_path)
            verification_result = {
                "verified": result.get("verified", False),
                "score": result.get("distance", 0.0)
            }
        except Exception as e:
            verification_result = {
                "verified": False,
                "score": 0.0,
                "error": str(e)
            }

        # Mise à jour du document
        doc.verification_result = verification_result
        doc.verified = verification_result.get("verified", False)
        doc.save()

        return verification_result

    except Document.DoesNotExist:
        return {"verified": False, "score": 0.0, "error": "Document introuvable"}

