from rest_framework import serializers
from verification.models import Document, DocumentType
from verification.validators import validate_no_ssrf


class VerificationSerializer(serializers.ModelSerializer):
    document_type = serializers.ChoiceField(
        choices=DocumentType.choices,
        help_text="Type of identity document. Accepted values: "
                  + ", ".join(f"`{v}`" for v, _ in DocumentType.choices),
    )
    callback_url = serializers.URLField(
        required=False,
        allow_null=True,
        validators=[validate_no_ssrf],
        help_text="Optional URL. When verification completes, a POST with the result will be sent here.",
    )

    class Meta:
        model = Document
        fields = [
            "first_name", "last_name", "birth_date",
            "document_type", "doc_file", "selfie_file", "callback_url",
        ]

    def create(self, validated_data):
        user = self.context["request"].user
        return Document.objects.create(user=user, **validated_data)


class DocumentStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ["id", "verified", "verification_result", "created_at", "updated_at"]
        read_only_fields = fields


# ── Inline response shapes used only by @extend_schema ───────────────────────

class VerificationAcceptedSerializer(serializers.Serializer):
    message = serializers.CharField(
        default="Fichiers uploadés et vérification en cours",
        help_text="Human-readable confirmation message.",
    )
    document_id = serializers.IntegerField(
        help_text="ID of the created Document record. Use it to poll the status endpoint.",
    )
    callback_url = serializers.URLField(
        required=False,
        allow_null=True,
        help_text="Echoed back when a callback_url was provided. The result will be POSTed there when ready.",
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
