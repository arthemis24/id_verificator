from django.urls import reverse
import pytest
import io
from datetime import date
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from rest_framework import status
from unittest.mock import patch, MagicMock
from PIL import Image

from verification.models import Document, validate_file_extension, validate_file_size, MAX_FILE_SIZE
from verification.ai_utils import _parse_birth_date, _parse_name, ocr_extract_info


# =========================
# FIXTURES
# =========================

@pytest.fixture
def user(db):
    return User.objects.create_user(username="testuser", password="password")


@pytest.fixture
def other_user(db):
    return User.objects.create_user(username="otheruser", password="password")


def generate_test_image():
    file = io.BytesIO()
    image = Image.new("RGB", (100, 100))
    image.save(file, format="JPEG")
    file.seek(0)
    return SimpleUploadedFile("test.jpg", file.read(), content_type="image/jpeg")


@pytest.fixture
def api_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture(autouse=True)
def cleanup_media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def valid_files():
    doc_file = SimpleUploadedFile(
        "test.pdf",
        b"%PDF-1.4 fake pdf content",
        content_type="application/pdf",
    )
    selfie_file = SimpleUploadedFile(
        "selfie.jpg",
        generate_test_image().read(),
        content_type="image/jpeg",
    )
    return doc_file, selfie_file


@pytest.fixture
def existing_doc(user, tmp_path, settings):
    """A Document already in the DB with a known verification result."""
    settings.MEDIA_ROOT = tmp_path
    return Document.objects.create(
        user=user,
        first_name="John",
        last_name="Doe",
        birth_date=date(1990, 1, 1),
        document_type="passport",
        doc_file=SimpleUploadedFile("doc.pdf", b"data", content_type="application/pdf"),
        selfie_file=SimpleUploadedFile("selfie.jpg", b"data", content_type="image/jpeg"),
        verified=True,
        verification_result={"verified": True, "face_score": 0.2, "ocr_verified": True},
    )


# =========================
# MODEL TESTS
# =========================

@pytest.mark.django_db
def test_create_document(user, valid_files):
    doc_file, selfie_file = valid_files

    doc = Document.objects.create(
        user=user,
        first_name="John",
        last_name="Doe",
        birth_date=date(1990, 1, 1),
        document_type="passport",
        doc_file=doc_file,
        selfie_file=selfie_file,
    )

    assert doc.id is not None
    assert doc.verified is False
    assert doc.user.username == "testuser"


# =========================
# VALIDATION TESTS
# =========================

def test_invalid_file_extension():
    file = SimpleUploadedFile("test.exe", b"data")
    with pytest.raises(Exception):
        validate_file_extension(file)


def test_file_too_large():
    file = SimpleUploadedFile("big.jpg", b"a" * (MAX_FILE_SIZE + 1))
    with pytest.raises(Exception):
        validate_file_size(file)


# =========================
# OCR UNIT TESTS
# =========================

def test_parse_birth_date_slash():
    assert _parse_birth_date("Date: 01/01/1990") == "01/01/1990"


def test_parse_birth_date_dash():
    assert _parse_birth_date("DOB: 1990-01-01") == "1990-01-01"


def test_parse_birth_date_not_found():
    assert _parse_birth_date("no date here") is None


def test_parse_name_french_label():
    assert _parse_name("NOM: DOE\nPRENOM: JOHN", "last_name") == "DOE"
    assert _parse_name("NOM: DOE\nPRENOM: JOHN", "first_name") == "JOHN"


def test_parse_name_english_label():
    assert _parse_name("Surname: Smith First Name: Jane", "last_name") == "Smith"


def test_parse_name_not_found():
    assert _parse_name("nothing useful here", "first_name") is None


@patch("verification.ai_utils._extract_text_from_file")
def test_ocr_extract_info_parses_text(mock_extract):
    mock_extract.return_value = "NOM: MBG\nPRENOM: RODDY\nDate: 14/11/1992"
    result = ocr_extract_info("/fake/doc.jpg")

    assert result["last_name"] == "MBG"
    assert result["first_name"] == "RODDY"
    assert result["birth_date"] == "14/11/1992"


@patch("verification.ai_utils._extract_text_from_file", side_effect=Exception("tesseract not found"))
def test_ocr_extract_info_handles_error(mock_extract):
    result = ocr_extract_info("/fake/doc.jpg")

    assert result["first_name"] is None
    assert result["last_name"] is None
    assert "error" in result


# =========================
# API TESTS — UPLOAD
# =========================

@pytest.mark.django_db
class TestVerificationAPI:

    def test_upload_valid_files(self, api_client, valid_files):
        doc_file, selfie_file = valid_files

        data = {
            "first_name": "RODDY",
            "last_name": "MBG",
            "birth_date": "1980-01-24",
            "document_type": "driver license"
            ""
            "",
            "doc_file": doc_file,
            "selfie_file": selfie_file,
        }

        url = reverse("verify")
        response = api_client.post(url, data, format="multipart")

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert "document_id" in response.data
        assert Document.objects.count() == 1

        doc = Document.objects.first()
        assert doc.verified is False
        assert doc.doc_file.name.endswith(".pdf")
        assert doc.selfie_file.name.endswith(".jpg")

    def test_upload_invalid_file_extension(self, api_client):
        doc_file = SimpleUploadedFile("test.txt", b"data")
        selfie_file = SimpleUploadedFile("selfie.bmp", b"data")

        data = {
            "first_name": "RODDY",
            "last_name": "MBG",
            "birth_date": "1980-01-24",
            "document_type": "driver license",
            "doc_file": doc_file,
            "selfie_file": selfie_file,
        }

        response = api_client.post("/api/verify/", data, format="multipart")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Document.objects.count() == 0

    def test_upload_missing_field(self, api_client, valid_files):
        doc_file, _ = valid_files

        data = {
            "first_name": "RODDY",
            "last_name": "MBG",
            "document_type": "driver license",
            "doc_file": doc_file,
        }

        response = api_client.post("/api/verify/", data, format="multipart")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "birth_date" in response.data

    def test_upload_file_too_large(self, api_client):
        big_file = SimpleUploadedFile(
            "big.pdf", b"a" * (6 * 1024 * 1024), content_type="application/pdf"
        )
        selfie_file = SimpleUploadedFile("selfie.jpg", b"content", content_type="image/jpeg")

        data = {
            "first_name": "RODDY",
            "last_name": "MBG",
            "birth_date": "1980-01-24",
            "document_type": "driver license",
            "doc_file": big_file,
            "selfie_file": selfie_file,
        }

        response = api_client.post("/api/verify/", data, format="multipart")
        assert response.status_code == 400

    def test_upload_unauthenticated(self):
        client = APIClient()
        response = client.post("/api/verify/", {}, format="multipart")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @patch("verification.tasks.verify_document.delay")
    def test_async_task_called(self, mock_task, api_client, valid_files):
        doc_file, selfie_file = valid_files

        data = {
            "first_name": "RODDY",
            "last_name": "MBG",
            "birth_date": "1980-01-24",
            "document_type": "driver license",
            "doc_file": doc_file,
            "selfie_file": selfie_file,
        }

        api_client.post("/api/verify/", data, format="multipart")
        assert mock_task.called


# =========================
# API TESTS — STATUS
# =========================

@pytest.mark.django_db
class TestVerificationStatusAPI:

    def test_status_returns_verification_result(self, api_client, existing_doc):
        url = reverse("verify-status", args=[existing_doc.id])
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == existing_doc.id
        assert response.data["verified"] is True
        assert response.data["verification_result"]["face_score"] == 0.2

    def test_status_pending_document(self, api_client, user):
        doc = Document.objects.create(
            user=user,
            first_name="John",
            last_name="Doe",
            birth_date=date(1990, 1, 1),
            document_type="passport",
            doc_file=SimpleUploadedFile("doc.pdf", b"data"),
            selfie_file=SimpleUploadedFile("selfie.jpg", b"data"),
        )

        url = reverse("verify-status", args=[doc.id])
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["verified"] is False
        assert response.data["verification_result"] is None

    def test_status_not_found(self, api_client):
        url = reverse("verify-status", args=[99999])
        response = api_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_status_other_users_document_returns_404(self, api_client, other_user):
        """A user must not be able to read another user's document status."""
        doc = Document.objects.create(
            user=other_user,
            first_name="Other",
            last_name="User",
            birth_date=date(1985, 5, 5),
            document_type="id_card",
            doc_file=SimpleUploadedFile("doc.pdf", b"data"),
            selfie_file=SimpleUploadedFile("selfie.jpg", b"data"),
        )

        url = reverse("verify-status", args=[doc.id])
        response = api_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_status_unauthenticated(self):
        client = APIClient()
        response = client.get("/api/verify/1/status/")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
