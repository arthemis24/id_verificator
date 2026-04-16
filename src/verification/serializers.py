from rest_framework import serializers
from verification.models import Document


class VerificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = [
            "first_name", "last_name", "birth_date",
            "document_type", "doc_file", "selfie_file",
        ]

    def create(self, validated_data):
        user = self.context["request"].user
        return Document.objects.create(user=user, **validated_data)


class DocumentStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ["id", "verified", "verification_result", "created_at"]
        read_only_fields = ["id", "verified", "verification_result", "created_at"]


# ── Inline response shapes used only by @extend_schema ───────────────────────

class VerificationAcceptedSerializer(serializers.Serializer):
    message = serializers.CharField(
        default="Fichiers uploadés et vérification en cours",
        help_text="Human-readable confirmation message.",
    )
    document_id = serializers.IntegerField(
        help_text="ID of the created Document record. Use it to poll the status endpoint.",
    )


class ErrorDetailSerializer(serializers.Serializer):
    error = serializers.CharField(help_text="Error description.")


class ValidationErrorSerializer(serializers.Serializer):
    """
    Generic shape for DRF field-level validation errors.
    Each key is a field name; each value is a list of error strings.
    Shown on 400 responses.
    """
    detail = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        help_text="Mapping of field name → list of error messages.",
    )
