from datetime import date, datetime
from rest_framework import serializers
from verification.models import Document, DocumentType
from verification.validators import validate_no_ssrf


class FlexibleDateField(serializers.DateField):
    """
    DateField that accepts DD-MM-YYYY and DD/MM/YYYY in addition to the
    default YYYY-MM-DD, normalising all inputs to a date object.
    """
    _EXTRA_FORMATS = ("%d-%m-%Y", "%d/%m/%Y")

    def to_internal_value(self, value):
        if isinstance(value, date):
            return value
        for fmt in self._EXTRA_FORMATS:
            try:
                return datetime.strptime(str(value), fmt).date()
            except ValueError:
                pass
        return super().to_internal_value(value)


class VerificationSerializer(serializers.ModelSerializer):
    birth_date = FlexibleDateField(
        help_text="Date of birth. Accepted formats: YYYY-MM-DD, DD-MM-YYYY, DD/MM/YYYY.",
    )
    expiry_date = FlexibleDateField(
        required=False,
        allow_null=True,
        help_text="Document expiry date. Accepted formats: YYYY-MM-DD, DD-MM-YYYY, DD/MM/YYYY.",
    )
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
            "first_name", "last_name", "birth_date", "expiry_date",
            "document_type", "doc_file", "selfie_file", "callback_url",
        ]

    def create(self, validated_data):
        user = self.context["request"].user
        return Document.objects.create(user=user, **validated_data)


class DocumentStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = [
            "id", "verified", "verification_result",
            "expiry_date", "created_at", "updated_at",
        ]
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
