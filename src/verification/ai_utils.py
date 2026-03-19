from deepface import DeepFace

def compare_faces(selfie_path, doc_face_path):
    try:
        result = DeepFace.verify(selfie_path, doc_face_path)
        return result["verified"], result
    except Exception as e:
        return False, {"error": str(e)}

def ocr_extract_info(doc_path):
    # Placeholder OCR
    extracted = {
        "first_name": "Jean",
        "last_name": "Doe",
        "birth_date": "1990-01-01"
    }
    return extracted
