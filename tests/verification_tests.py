from django.urls import reverse
import json
import pytest
import io
import os
from datetime import date
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from rest_framework import status
from unittest.mock import patch, MagicMock
from PIL import Image

from verification.models import Document, validate_file_extension, validate_file_size, MAX_FILE_SIZE
from verification.ai_utils import _parse_birth_date, _parse_name, ocr_extract_info
from verification.validators import validate_no_ssrf, validate_file_content


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
# MAGIC-BYTE VALIDATION
# =========================

def test_valid_pdf_magic_bytes():
    file = SimpleUploadedFile("doc.pdf", b"%PDF-1.4 fake content")
    validate_file_content(file)  # must not raise


def test_valid_jpeg_magic_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="JPEG")
    file = SimpleUploadedFile("selfie.jpg", buf.getvalue())
    validate_file_content(file)  # must not raise


def test_valid_png_magic_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="PNG")
    file = SimpleUploadedFile("img.png", buf.getvalue())
    validate_file_content(file)  # must not raise


def test_disguised_file_rejected_jpg_extension_with_pdf_content():
    """A PDF file renamed to .jpg must be rejected."""
    with pytest.raises(ValidationError, match="does not match"):
        validate_file_content(SimpleUploadedFile("evil.jpg", b"%PDF-1.4 payload"))


def test_disguised_file_rejected_pdf_extension_with_jpeg_content():
    """A JPEG file renamed to .pdf must be rejected."""
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="JPEG")
    with pytest.raises(ValidationError, match="does not match"):
        validate_file_content(SimpleUploadedFile("evil.pdf", buf.getvalue()))


def test_disguised_html_as_jpg_rejected():
    """An HTML/JS payload disguised as an image must be rejected."""
    html_payload = b"<html><script>alert(1)</script></html>"
    with pytest.raises(ValidationError, match="does not match"):
        validate_file_content(SimpleUploadedFile("xss.jpg", html_payload))


def test_disguised_script_as_pdf_rejected():
    """A shell script disguised as a PDF must be rejected."""
    with pytest.raises(ValidationError, match="does not match"):
        validate_file_content(SimpleUploadedFile("shell.pdf", b"#!/bin/bash\nrm -rf /"))


def test_unsupported_extension_rejected():
    with pytest.raises(ValidationError, match="Unsupported"):
        validate_file_content(SimpleUploadedFile("file.exe", b"MZ\x90\x00"))


@pytest.mark.django_db
def test_api_rejects_disguised_file(api_client):
    """API must return 400 when a file's content does not match its extension."""
    # A real PDF content uploaded with a .jpg extension
    doc_file = SimpleUploadedFile("evil.jpg", b"%PDF-1.4 fake", content_type="image/jpeg")
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="JPEG")
    selfie_file = SimpleUploadedFile("selfie.jpg", buf.getvalue(), content_type="image/jpeg")

    response = api_client.post(reverse("verify"), {
        "first_name": "John", "last_name": "Doe",
        "birth_date": "1990-01-01", "document_type": "passport",
        "doc_file": doc_file, "selfie_file": selfie_file,
    }, format="multipart")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "doc_file" in response.data


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
            "document_type": "driver_license"
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
            "document_type": "driver_license",
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
            "document_type": "driver_license",
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
            "document_type": "driver_license",
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
            "document_type": "driver_license",
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


# =========================
# SSRF VALIDATOR TESTS
# =========================

_PUBLIC_IP_ADDRINFO = [
    (None, None, None, None, ("1.1.1.1", 0)),  # Cloudflare — genuinely public
]


class TestSSRFValidator:

    @patch("verification.validators.socket.getaddrinfo", return_value=_PUBLIC_IP_ADDRINFO)
    def test_public_https_url_is_allowed(self, _mock):
        validate_no_ssrf("https://webhook.example.com/callback")

    @patch("verification.validators.socket.getaddrinfo", return_value=_PUBLIC_IP_ADDRINFO)
    def test_public_http_url_is_allowed(self, _mock):
        validate_no_ssrf("http://webhook.example.com/callback")

    def test_loopback_ipv4_is_blocked(self):
        with pytest.raises(ValidationError, match="restricted"):
            validate_no_ssrf("http://127.0.0.1/admin")

    def test_loopback_localhost_is_blocked(self):
        with pytest.raises(ValidationError):
            validate_no_ssrf("http://localhost/internal")

    def test_private_rfc1918_10_is_blocked(self):
        with pytest.raises(ValidationError, match="restricted"):
            validate_no_ssrf("http://10.0.0.1/secret")

    def test_private_rfc1918_192_168_is_blocked(self):
        with pytest.raises(ValidationError, match="restricted"):
            validate_no_ssrf("http://192.168.1.1/secret")

    def test_private_rfc1918_172_is_blocked(self):
        with pytest.raises(ValidationError, match="restricted"):
            validate_no_ssrf("http://172.16.0.1/secret")

    def test_aws_metadata_endpoint_is_blocked(self):
        with pytest.raises(ValidationError, match="restricted"):
            validate_no_ssrf("http://169.254.169.254/latest/meta-data/")

    def test_non_http_scheme_is_blocked(self):
        with pytest.raises(ValidationError, match="http or https"):
            validate_no_ssrf("ftp://example.com/file")

    def test_file_scheme_is_blocked(self):
        with pytest.raises(ValidationError, match="http or https"):
            validate_no_ssrf("file:///etc/passwd")

    def test_invalid_url_is_rejected(self):
        with pytest.raises(ValidationError):
            validate_no_ssrf("not-a-url")

    @pytest.mark.django_db
    def test_api_rejects_ssrf_callback_url(self, api_client, valid_files):
        doc_file, selfie_file = valid_files
        data = {
            "first_name": "John",
            "last_name": "Doe",
            "birth_date": "1990-01-01",
            "document_type": "passport",
            "doc_file": doc_file,
            "selfie_file": selfie_file,
            "callback_url": "http://169.254.169.254/latest/meta-data/",
        }
        response = api_client.post(reverse("verify"), data, format="multipart")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "callback_url" in response.data

    @pytest.mark.django_db
    @patch("verification.validators.socket.getaddrinfo", return_value=_PUBLIC_IP_ADDRINFO)
    def test_api_accepts_valid_callback_url(self, _mock, api_client, valid_files):
        doc_file, selfie_file = valid_files
        data = {
            "first_name": "John",
            "last_name": "Doe",
            "birth_date": "1990-01-01",
            "document_type": "passport",
            "doc_file": doc_file,
            "selfie_file": selfie_file,
            "callback_url": "https://webhook.example.com/id-result",
        }
        with patch("verification.tasks.verify_document.delay"):
            response = api_client.post(reverse("verify"), data, format="multipart")
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.data["callback_url"] == "https://webhook.example.com/id-result"


# =========================
# PROTECTED MEDIA TESTS
# =========================

@pytest.mark.django_db
class TestProtectedMedia:

    def _make_doc(self, user, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        doc_dir = tmp_path / "documents" / f"user_{user.id}"
        doc_dir.mkdir(parents=True)
        selfie_dir = tmp_path / "selfies" / f"user_{user.id}"
        selfie_dir.mkdir(parents=True)

        doc_file_path = doc_dir / "test_doc.pdf"
        selfie_file_path = selfie_dir / "test_selfie.jpg"
        doc_file_path.write_bytes(b"fake pdf content")
        selfie_file_path.write_bytes(b"fake jpg content")

        return Document.objects.create(
            user=user,
            first_name="John",
            last_name="Doe",
            birth_date=date(1990, 1, 1),
            document_type="passport",
            doc_file=f"documents/user_{user.id}/test_doc.pdf",
            selfie_file=f"selfies/user_{user.id}/test_selfie.jpg",
        )

    def test_unauthenticated_request_returns_401(self):
        client = APIClient()
        response = client.get("/media/documents/user_1/somefile.pdf")
        assert response.status_code == 401

    def test_owner_can_access_document(self, user, tmp_path, settings):
        doc = self._make_doc(user, tmp_path, settings)
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(f"/media/documents/user_{user.id}/test_doc.pdf")
        assert response.status_code == 200

    def test_owner_can_access_selfie(self, user, tmp_path, settings):
        doc = self._make_doc(user, tmp_path, settings)
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(f"/media/selfies/user_{user.id}/test_selfie.jpg")
        assert response.status_code == 200

    def test_other_user_cannot_access_document(self, user, other_user, tmp_path, settings):
        doc = self._make_doc(user, tmp_path, settings)
        client = APIClient()
        client.force_authenticate(user=other_user)
        response = client.get(f"/media/documents/user_{user.id}/test_doc.pdf")
        assert response.status_code == 403

    def test_path_traversal_is_blocked(self, user, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get("/media/../../etc/passwd")
        assert response.status_code in (404, 403)

    def test_nonexistent_file_returns_404(self, user, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        client = APIClient()
        client.force_authenticate(user=user)
        # Fake a DB record pointing at a file that does not exist on disk
        Document.objects.create(
            user=user,
            first_name="John",
            last_name="Doe",
            birth_date=date(1990, 1, 1),
            document_type="passport",
            doc_file="documents/user_99/ghost.pdf",
            selfie_file="selfies/user_99/ghost.jpg",
        )
        response = client.get("/media/documents/user_99/ghost.pdf")
        assert response.status_code == 404


# =========================
# THROTTLE TESTS
# =========================

class TestThrottleConfiguration:
    """
    Verify throttle classes are correctly scoped and wired to their views.

    We test configuration, not DRF's throttle mechanics (which are already
    covered by DRF's own test suite). The integration tests below verify
    that a throttled request produces a 429 response using mocks.
    """

    def test_login_throttle_scope(self):
        from verification.throttles import LoginRateThrottle
        assert LoginRateThrottle.scope == "login"

    def test_upload_throttle_scope(self):
        from verification.throttles import UploadRateThrottle
        assert UploadRateThrottle.scope == "upload"

    def test_upload_view_has_upload_throttle(self):
        from verification.views import VerificationView
        from verification.throttles import UploadRateThrottle
        assert UploadRateThrottle in VerificationView.throttle_classes

    def test_login_view_has_login_throttle(self):
        from id_verificator.urls import AnnotatedTokenObtainPairView
        from verification.throttles import LoginRateThrottle
        assert LoginRateThrottle in AnnotatedTokenObtainPairView.throttle_classes

    @pytest.mark.django_db
    def test_throttled_upload_returns_429(self, api_client, valid_files):
        """A throttled upload request receives a 429 response."""
        from rest_framework.exceptions import Throttled
        from verification.views import VerificationView

        doc_file, selfie_file = valid_files
        data = {
            "first_name": "John", "last_name": "Doe",
            "birth_date": "1990-01-01", "document_type": "passport",
            "doc_file": doc_file, "selfie_file": selfie_file,
        }
        with patch.object(VerificationView, "check_throttles", side_effect=Throttled()):
            response = api_client.post(reverse("verify"), data, format="multipart")
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS

    def test_throttled_login_returns_429(self, db):
        """A throttled login request receives a 429 response."""
        from rest_framework.exceptions import Throttled
        from id_verificator.urls import AnnotatedTokenObtainPairView

        client = APIClient()
        with patch.object(AnnotatedTokenObtainPairView, "check_throttles", side_effect=Throttled()):
            response = client.post(
                reverse("token_obtain_pair"),
                {"username": "u", "password": "p"},
                format="json",
            )
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


# =========================
# MODEL — CHOICES & STR
# =========================

@pytest.mark.django_db
class TestDocumentModel:

    def test_str_pending(self, user, valid_files):
        doc_file, selfie_file = valid_files
        doc = Document.objects.create(
            user=user, first_name="Jane", last_name="Smith",
            birth_date=date(1990, 1, 1), document_type="passport",
            doc_file=doc_file, selfie_file=selfie_file,
        )
        assert "Jane" in str(doc)
        assert "Smith" in str(doc)
        assert "pending" in str(doc)

    def test_str_verified(self, user, valid_files):
        doc_file, selfie_file = valid_files
        doc = Document.objects.create(
            user=user, first_name="Jane", last_name="Smith",
            birth_date=date(1990, 1, 1), document_type="passport",
            doc_file=doc_file, selfie_file=selfie_file,
            verified=True,
        )
        assert "✓" in str(doc)

    def test_updated_at_is_set_on_save(self, user, valid_files):
        doc_file, selfie_file = valid_files
        doc = Document.objects.create(
            user=user, first_name="Jane", last_name="Smith",
            birth_date=date(1990, 1, 1), document_type="passport",
            doc_file=doc_file, selfie_file=selfie_file,
        )
        assert doc.updated_at is not None

    def test_invalid_document_type_rejected(self, api_client, valid_files):
        doc_file, selfie_file = valid_files
        response = api_client.post(reverse("verify"), {
            "first_name": "John", "last_name": "Doe",
            "birth_date": "1990-01-01",
            "document_type": "banana",     # not a valid choice
            "doc_file": doc_file, "selfie_file": selfie_file,
        }, format="multipart")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "document_type" in response.data

    def test_all_valid_document_types_accepted(self, api_client):
        from verification.models import DocumentType
        for dtype, _ in DocumentType.choices:
            buf = io.BytesIO()
            Image.new("RGB", (10, 10)).save(buf, format="JPEG")
            doc_file = SimpleUploadedFile("doc.pdf", b"%PDF-1.4 x", content_type="application/pdf")
            selfie   = SimpleUploadedFile("s.jpg", buf.getvalue(), content_type="image/jpeg")
            with patch("verification.tasks.verify_document.delay"):
                response = api_client.post(reverse("verify"), {
                    "first_name": "A", "last_name": "B",
                    "birth_date": "1990-01-01", "document_type": dtype,
                    "doc_file": doc_file, "selfie_file": selfie,
                }, format="multipart")
            assert response.status_code == 202, f"Expected 202 for type '{dtype}', got {response.status_code}"


# =========================
# SERVICE LAYER TESTS
# =========================

class TestVerificationServices:
    """
    Unit tests for verification.services — each function tested in isolation
    with mocked dependencies, no DB required.
    """

    def test_check_ocr_name_match_label_level_pass(self):
        from verification.services import check_ocr_name_match
        from unittest.mock import MagicMock

        doc = MagicMock()
        doc.first_name = "Rodrigue"
        doc.last_name  = "Mbogning"

        ocr = {"first_name": "RODRIGUE", "last_name": "MBOGNING", "raw_text": "..."}
        match, detail = check_ocr_name_match(doc, ocr)

        assert match is True
        assert detail["method"] == "label"
        assert detail["first_name_match"] is True
        assert detail["last_name_match"] is True

    def test_check_ocr_name_match_label_level_fail(self):
        from verification.services import check_ocr_name_match
        from unittest.mock import MagicMock

        doc = MagicMock()
        doc.first_name = "Rodrigue"
        doc.last_name  = "Mbogning"

        ocr = {"first_name": "JEAN", "last_name": "DUPONT", "raw_text": "..."}
        match, detail = check_ocr_name_match(doc, ocr)

        assert match is False
        assert detail["method"] == "label"

    def test_check_ocr_name_match_fulltext_fallback(self):
        from verification.services import check_ocr_name_match
        from unittest.mock import MagicMock

        doc = MagicMock()
        doc.first_name = "Rodrigue"
        doc.last_name  = "Mbogning"

        ocr = {"first_name": None, "last_name": None,
               "raw_text": "Some text RODRIGUE MBOGNING more text"}
        match, detail = check_ocr_name_match(doc, ocr)

        assert match is True
        assert detail["method"] == "fulltext"

    def test_check_ocr_name_match_skipped_when_no_text(self):
        from verification.services import check_ocr_name_match
        from unittest.mock import MagicMock

        doc = MagicMock()
        doc.first_name = "Rodrigue"
        doc.last_name  = "Mbogning"

        match, detail = check_ocr_name_match(doc, {"raw_text": "   "})
        assert match is True
        assert detail["method"] == "skipped_no_text"

    def test_build_verification_result_both_pass(self):
        from verification.services import build_verification_result

        result = build_verification_result(
            ocr_data={"first_name": "A", "last_name": "B", "birth_date": "1990-01-01", "raw_text": ""},
            ocr_match=True,
            ocr_detail={"method": "label"},
            face={"verified": True, "score": 0.12, "error": None},
        )
        assert result["verified"] is True
        assert result["face_verified"] is True
        assert result["ocr_verified"] is True
        assert "face_error" not in result

    def test_build_verification_result_face_fails(self):
        from verification.services import build_verification_result

        result = build_verification_result(
            ocr_data={"first_name": "A", "last_name": "B", "birth_date": None, "raw_text": ""},
            ocr_match=True,
            ocr_detail={"method": "label"},
            face={"verified": False, "score": 0.9, "error": "no face detected"},
        )
        assert result["verified"] is False
        assert result["face_error"] == "no face detected"

    def test_build_verification_result_ocr_fails(self):
        from verification.services import build_verification_result

        result = build_verification_result(
            ocr_data={"first_name": None, "last_name": None, "birth_date": None, "raw_text": ""},
            ocr_match=False,
            ocr_detail={"method": "fulltext"},
            face={"verified": True, "score": 0.1, "error": None},
        )
        assert result["verified"] is False
        assert result["ocr_verified"] is False

    def test_run_face_verification_handles_exception(self):
        from verification.services import run_face_verification

        with patch("deepface.DeepFace.verify", side_effect=Exception("model load error")):
            result = run_face_verification("/fake/doc.jpg", "/fake/selfie.jpg")

        assert result["verified"] is False
        assert result["error"] == "model load error"
        assert result["score"] == 0.0


# =========================
# WEBHOOK DISPATCH TESTS
# =========================

@pytest.mark.django_db
class TestWebhookDispatch:
    """
    Tests for _dispatch_webhook and the full verify_document → webhook flow.
    All HTTP calls are mocked — no network access required.
    """

    @pytest.fixture
    def doc_with_callback(self, user, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        doc_dir    = tmp_path / "documents" / f"user_{user.id}"
        selfie_dir = tmp_path / "selfies"   / f"user_{user.id}"
        doc_dir.mkdir(parents=True)
        selfie_dir.mkdir(parents=True)
        (doc_dir    / "doc.pdf").write_bytes(b"%PDF-1.4 fake")
        (selfie_dir / "selfie.jpg").write_bytes(b"\xff\xd8\xff fake")

        return Document.objects.create(
            user=user,
            first_name="Rodrigue",
            last_name="Mbogning",
            birth_date=date(1990, 1, 1),
            document_type="passport",
            doc_file=f"documents/user_{user.id}/doc.pdf",
            selfie_file=f"selfies/user_{user.id}/selfie.jpg",
            callback_url="https://webhook.example.com/result",
        )

    def _mock_response(self, status_code=200):
        mock_resp = MagicMock()
        mock_resp.status = status_code
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_webhook_posts_correct_payload(self, doc_with_callback):
        from verification.tasks import _dispatch_webhook

        result = {"verified": True, "face_score": 0.05, "ocr_verified": True}

        with patch("urllib.request.urlopen", return_value=self._mock_response()) as mock_open:
            _dispatch_webhook(
                "https://webhook.example.com/result",
                doc_with_callback.id,
                doc_with_callback,
                result,
            )

        mock_open.assert_called_once()
        req = mock_open.call_args[0][0]

        body = json.loads(req.data.decode())
        assert body["document_id"] == doc_with_callback.id
        assert body["subject"]["first_name"] == "Rodrigue"
        assert body["subject"]["last_name"] == "Mbogning"
        assert body["subject"]["document_type"] == "passport"
        assert body["result"]["verified"] is True
        assert req.get_header("Content-type") == "application/json"

    def test_webhook_retries_on_network_error(self, doc_with_callback):
        from verification.tasks import _dispatch_webhook
        from urllib.error import URLError

        result = {"verified": False}

        with patch("urllib.request.urlopen", side_effect=URLError("timeout")) as mock_open:
            with patch("time.sleep"):
                _dispatch_webhook(
                    "https://webhook.example.com/result",
                    doc_with_callback.id,
                    doc_with_callback,
                    result,
                    max_retries=3,
                )

        assert mock_open.call_count == 3

    def test_webhook_stops_retrying_after_success(self, doc_with_callback):
        from verification.tasks import _dispatch_webhook
        from urllib.error import URLError

        result = {"verified": True}
        responses = [URLError("fail"), self._mock_response()]

        with patch("urllib.request.urlopen", side_effect=responses) as mock_open:
            with patch("time.sleep"):
                _dispatch_webhook(
                    "https://webhook.example.com/result",
                    doc_with_callback.id,
                    doc_with_callback,
                    result,
                )

        assert mock_open.call_count == 2

    def test_webhook_failure_does_not_raise(self, doc_with_callback):
        from verification.tasks import _dispatch_webhook
        from urllib.error import URLError

        with patch("urllib.request.urlopen", side_effect=URLError("down")):
            with patch("time.sleep"):
                # Must complete without raising any exception
                _dispatch_webhook(
                    "https://webhook.example.com/result",
                    doc_with_callback.id,
                    doc_with_callback,
                    {"verified": False},
                    max_retries=2,
                )

    def test_verify_document_fires_webhook_when_callback_url_set(self, doc_with_callback):
        from verification.tasks import verify_document

        mock_ocr  = {"first_name": "RODRIGUE", "last_name": "MBOGNING",
                     "birth_date": "01/01/1990", "raw_text": "RODRIGUE MBOGNING"}
        mock_face = {"verified": True, "score": 0.05, "error": None}

        with patch("verification.tasks.run_ocr", return_value=mock_ocr), \
             patch("verification.tasks.run_face_verification", return_value=mock_face), \
             patch("verification.tasks._dispatch_webhook") as mock_webhook:
            verify_document(doc_with_callback.id)

        mock_webhook.assert_called_once()
        call_args = mock_webhook.call_args[0]
        assert call_args[0] == "https://webhook.example.com/result"
        assert call_args[1] == doc_with_callback.id

    def test_verify_document_skips_webhook_when_no_callback_url(self, user, tmp_path, settings):
        from verification.tasks import verify_document

        settings.MEDIA_ROOT = str(tmp_path)
        doc_dir    = tmp_path / "documents" / f"user_{user.id}"
        selfie_dir = tmp_path / "selfies"   / f"user_{user.id}"
        doc_dir.mkdir(parents=True)
        selfie_dir.mkdir(parents=True)
        (doc_dir    / "doc.pdf").write_bytes(b"%PDF-1.4 fake")
        (selfie_dir / "selfie.jpg").write_bytes(b"\xff\xd8\xff fake")

        doc = Document.objects.create(
            user=user, first_name="A", last_name="B",
            birth_date=date(1990, 1, 1), document_type="id_card",
            doc_file=f"documents/user_{user.id}/doc.pdf",
            selfie_file=f"selfies/user_{user.id}/selfie.jpg",
            # no callback_url
        )

        mock_face = {"verified": True, "score": 0.1, "error": None}
        mock_ocr  = {"first_name": "A", "last_name": "B",
                     "birth_date": None, "raw_text": "A B"}

        with patch("verification.tasks.run_ocr", return_value=mock_ocr), \
             patch("verification.tasks.run_face_verification", return_value=mock_face), \
             patch("verification.tasks._dispatch_webhook") as mock_webhook:
            verify_document(doc.id)

        mock_webhook.assert_not_called()


# =========================
# LOGGING TESTS
# =========================

class TestStructuredLogging:
    """Verify key events are logged at the correct level."""

    def test_ocr_failure_logged_as_warning(self, caplog):
        import logging
        from verification.services import run_ocr

        with patch(
            "verification.ai_utils.ocr_extract_info",
            return_value={"first_name": None, "last_name": None,
                          "birth_date": None, "raw_text": "", "error": "tesseract not found"},
        ):
            with caplog.at_level(logging.WARNING, logger="verification"):
                run_ocr("/fake/doc.jpg")

        assert any("OCR failed" in r.message for r in caplog.records)

    @pytest.mark.django_db
    def test_task_not_found_logged_as_error(self, caplog):
        import logging
        from verification.tasks import verify_document

        with caplog.at_level(logging.ERROR, logger="verification"):
            verify_document(99999)

        assert any("not found" in r.message for r in caplog.records)
