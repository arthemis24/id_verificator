from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
import os
import uuid

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


import uuid

def user_directory_path(instance, filename):
    ext = filename.split('.')[-1]
    filename = f"{uuid.uuid4().hex}.{ext}"
    return f'user_{instance.user.id}/{filename}'


def validate_file_extension(value):
    ext = os.path.splitext(value.name)[1].lower()
    if ext not in [".pdf", ".png", ".jpg", ".jpeg"]:
        raise ValidationError("Extension non supportée. PDF, PNG, JPG, JPEG acceptés.")


def validate_file_size(value):
    if value.size > MAX_FILE_SIZE:
        raise ValidationError(f"Taille maximale du fichier: {MAX_FILE_SIZE/(1024*1024)} MB")


def selfie_upload_path(instance, filename):
   return f"selfies/user_{instance.user.id}/{filename}"


def document_upload_path(instance, filename):
   return f"documents/user_{instance.user.id}/{filename}"

class Document(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    birth_date = models.DateField()
    document_type = models.CharField(max_length=50)
    doc_file = models.FileField(
        upload_to=document_upload_path,
        validators=[validate_file_extension, validate_file_size]
    )
    selfie_file = models.FileField(
        upload_to=selfie_upload_path,
        validators=[validate_file_extension, validate_file_size]
    )
    verified = models.BooleanField(default=False)
    verification_result = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
