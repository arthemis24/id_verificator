import io
import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient
from rest_framework import status
from verification.models import Document

@pytest.mark.django_db
class TestVerificationAPI:

    @pytest.fixture
    def user(self):
        return User.objects.create_user(username="testuser", password="password")

    @pytest.fixture
    def api_client(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_upload_valid_files(self, api_client):
        doc_file = io.BytesIO(b"fake pdf content")
        doc_file.name = "test.pdf"
        selfie_file = io.BytesIO(b"fake image content")
        selfie_file.name = "selfie.jpg"

        data = {
            "first_name": "Rodrigue",
            "last_name": "Mbogning",
            "birth_date": "1980-01-24",
            "document_type": "passport",
            "doc_file": doc_file,
            "selfie_file": selfie_file,
        }

        response = api_client.post("/api/verify/", data, format="multipart")
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert Document.objects.count() == 1
        doc = Document.objects.first()
        assert doc.user.username == "testuser"
        assert doc.verified is False  # pas encore vérifié
        assert doc.doc_file.name.endswith(".pdf")
        assert doc.selfie_file.name.endswith(".jpg")

    def test_upload_invalid_file_extension(self, api_client):
        doc_file = io.BytesIO(b"fake content")
        doc_file.name = "test.txt"
        selfie_file = io.BytesIO(b"fake content")
        selfie_file.name = "selfie.bmp"

        data = {
            "first_name": "Rodrigue",
            "last_name": "Mbogning",
            "birth_date": "1980-01-24",
            "document_type": "passport",
            "doc_file": doc_file,
            "selfie_file": selfie_file,
        }

        response = api_client.post("/api/verify/", data, format="multipart")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "doc_file" in response.data
        assert "selfie_file" in response.data
        assert Document.objects.count() == 0

    def test_upload_missing_field(self, api_client):
        doc_file = io.BytesIO(b"fake pdf content")
        doc_file.name = "test.pdf"

        data = {
            "first_name": "Rodrigue",
            "last_name": "Mbogning",
            # "birth_date" manquant
            "document_type": "passport",
            "doc_file": doc_file,
        }

        response = api_client.post("/api/verify/", data, format="multipart")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "birth_date" in response.data
