from rest_framework import serializers
from .models import Document


class VerificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = [
            "first_name", "last_name", "birth_date",
            "document_type", "doc_file", "selfie_file"
        ]
