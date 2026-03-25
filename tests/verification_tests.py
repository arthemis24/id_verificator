from django.urls import reverse
import pytest
import io
from datetime import date
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from rest_framework import status
from unittest.mock import patch
from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile

from verification.models import Document, validate_file_extension, validate_file_size, document_upload_path, selfie_upload_path, MAX_FILE_SIZE


# =========================
# 🔧 FIXTURES
# =========================

@pytest.fixture
def user():
    return User.objects.create_user(username="testuser", password="password")

def  generate_test_image():
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
        content_type="application/pdf"
    )

    selfie_file = SimpleUploadedFile(
        "selfie.jpg",
        generate_test_image().read(),
        content_type="image/jpeg"
    )

    return doc_file, selfie_file


# =========================
# 📦 MODEL TESTS
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
        selfie_file=selfie_file
    )

    assert doc.id is not None
    assert doc.verified is False
    assert doc.user.username == "testuser"


# =========================
#  VALIDATION TESTS
# =========================

def test_invalid_file_extension():
    file = SimpleUploadedFile("test.exe", b"data")

    with pytest.raises(Exception):
        validate_file_extension(file)


def test_file_too_large():
    file = SimpleUploadedFile(
        "big.jpg",
        b"a" * (MAX_FILE_SIZE + 1)
    )

    with pytest.raises(Exception):
        validate_file_size(file)


# =========================
# 🌐 API TESTS
# =========================

@pytest.mark.django_db
class TestVerificationAPI:

    def test_upload_valid_files(self, api_client, valid_files):
        doc_file, selfie_file = valid_files

        data = {
            "first_name": "Rodrigue",
            "last_name": "Mbogning",
            "birth_date": "1980-01-24",
            "document_type": "passport",
            "doc_file": doc_file,
            "selfie_file": selfie_file,
        }

        url = reverse("verify")
        response = api_client.post(url, data, format="multipart")

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert Document.objects.count() == 1

        doc = Document.objects.first()
        assert doc.verified is False
        assert doc.doc_file.name.endswith(".pdf")
        assert doc.selfie_file.name.endswith(".jpg")


    def test_upload_invalid_file_extension(self, api_client):
        doc_file = SimpleUploadedFile("test.txt", b"data")
        selfie_file = SimpleUploadedFile("selfie.bmp", b"data")

        data = {
            "first_name": "Rodrigue",
            "last_name": "Mbogning",
            "birth_date": "1980-01-24",
            "document_type": "passport",
            "doc_file": doc_file,
            "selfie_file": selfie_file,
        }

        response = api_client.post("/api/verify/", data, format="multipart")
        print(response.data)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Document.objects.count() == 0


    def test_upload_missing_field(self, api_client, valid_files):
        doc_file, _ = valid_files

        data = {
            "first_name": "Rodrigue",
            "last_name": "Mbogning",
            "document_type": "passport",
            "doc_file": doc_file,
        }

        response = api_client.post("/api/verify/", data, format="multipart")
        print(response.data)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "birth_date" in response.data


    def test_upload_file_too_large(self, api_client):
        big_file = SimpleUploadedFile(
            "big.pdf",
            b"a" * (6 * 1024 * 1024),
            content_type="application/pdf"
        )

        selfie_file = SimpleUploadedFile(
            "selfie.jpg",
            b"content",
            content_type="image/jpeg"
        )

        data = {
            "first_name": "Rodrigue",
            "last_name": "Mbogning",
            "birth_date": "1980-01-24",
            "document_type": "passport",
            "doc_file": big_file,
            "selfie_file": selfie_file,
        }

        response = api_client.post("/api/verify/", data, format="multipart")
        print(response.data)
        assert response.status_code == 400


    def test_upload_unauthenticated(self):
        client = APIClient()

        response = client.post("/api/verify/", {}, format="multipart")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


    @patch("verification.tasks.verify_document.delay")
    def test_async_task_called(self, mock_task, api_client, valid_files):
        doc_file, selfie_file = valid_files

        data = {
            "first_name": "Rodrigue",
            "last_name": "Mbogning",
            "birth_date": "1980-01-24",
            "document_type": "passport",
            "doc_file": doc_file,
            "selfie_file": selfie_file,
        }

        api_client.post("/api/verify/", data, format="multipart")

        assert mock_task.called