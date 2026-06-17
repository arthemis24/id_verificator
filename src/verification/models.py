import os
import uuid

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models

from .validators import validate_file_content, validate_no_ssrf

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


# ── File validators ───────────────────────────────────────────────────────────

def validate_file_extension(value):
    ext = os.path.splitext(value.name)[1].lower()
    if ext not in [".pdf", ".png", ".jpg", ".jpeg"]:
        raise ValidationError("Extension non supportée. PDF, PNG, JPG, JPEG acceptés.")


def validate_file_size(value):
    if value.size > MAX_FILE_SIZE:
        raise ValidationError(f"Taille maximale du fichier: {MAX_FILE_SIZE / (1024 * 1024):.0f} MB")


# ── Upload paths ──────────────────────────────────────────────────────────────

def _make_upload_path(folder: str):
    def upload_path(instance, filename):
        ext = os.path.splitext(filename)[1].lower()
        return f"{folder}/user_{instance.user.id}/{uuid.uuid4().hex}{ext}"
    return upload_path


document_upload_path = _make_upload_path("documents")
selfie_upload_path   = _make_upload_path("selfies")


# ── Document model ────────────────────────────────────────────────────────────

class DocumentType(models.TextChoices):
    PASSPORT      = "passport",       "Passport"
    ID_CARD       = "id_card",        "National ID card"
    DRIVER_LICENSE = "driver_license", "Driver's license"
    RESIDENCE_PERMIT = "residence_permit", "Residence permit"


_FILE_VALIDATORS = [validate_file_extension, validate_file_size, validate_file_content]


class Document(models.Model):
    user          = models.ForeignKey(User, on_delete=models.CASCADE)
    first_name    = models.CharField(max_length=100)
    last_name     = models.CharField(max_length=100)
    birth_date    = models.DateField()
    document_type = models.CharField(
        max_length=50,
        choices=DocumentType.choices,
    )
    doc_file = models.FileField(
        upload_to=document_upload_path,
        validators=_FILE_VALIDATORS,
    )
    selfie_file = models.FileField(
        upload_to=selfie_upload_path,
        validators=_FILE_VALIDATORS,
    )
    expiry_date          = models.DateField(null=True, blank=True)
    verified             = models.BooleanField(default=False)
    verification_result  = models.JSONField(null=True, blank=True)
    callback_url         = models.URLField(null=True, blank=True, validators=[validate_no_ssrf])
    created_at           = models.DateTimeField(auto_now_add=True)
    updated_at           = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        status = "✓" if self.verified else "pending"
        return f"Document #{self.id} — {self.first_name} {self.last_name} [{status}]"
