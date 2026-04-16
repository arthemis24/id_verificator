from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiParameter,
    OpenApiExample,
    OpenApiResponse,
)
from drf_spectacular.types import OpenApiTypes
from .models import Document
from .serializers import (
    VerificationSerializer,
    DocumentStatusSerializer,
    VerificationAcceptedSerializer,
    ErrorDetailSerializer,
    ValidationErrorSerializer,
)
from verification.tasks import verify_document


@extend_schema(tags=["Verification"])
class VerificationView(APIView):
    """
    Submit an identity verification request.

    Upload an ID document (PDF, PNG, JPG or JPEG) together with a selfie.
    The API saves the files, queues an async Celery task that runs:

    1. **OCR** — extracts the name and birth date from the document and
       cross-checks them against the values you submitted.
    2. **Face comparison** — compares the photo on the document against the
       selfie using DeepFace.

    Both checks must pass for `verified` to be `true`.
    Poll `GET /api/verify/{id}/status/` to retrieve the result.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        summary="Submit an identity verification",
        request={
            "multipart/form-data": VerificationSerializer,
        },
        responses={
            202: OpenApiResponse(
                response=VerificationAcceptedSerializer,
                description=(
                    "Files accepted. Verification is running asynchronously. "
                    "Use `document_id` to poll the status endpoint."
                ),
                examples=[
                    OpenApiExample(
                        "Accepted",
                        value={
                            "message": "Fichiers uploadés et vérification en cours",
                            "document_id": 42,
                        },
                        response_only=True,
                    )
                ],
            ),
            400: OpenApiResponse(
                response=ValidationErrorSerializer,
                description="Validation error — missing field, bad file type, or file too large.",
                examples=[
                    OpenApiExample(
                        "Missing selfie",
                        value={"selfie_file": ["This field is required."]},
                        response_only=True,
                    ),
                    OpenApiExample(
                        "Bad file extension",
                        value={
                            "doc_file": [
                                "Extension non supportée. PDF, PNG, JPG, JPEG acceptés."
                            ]
                        },
                        response_only=True,
                    ),
                    OpenApiExample(
                        "File too large",
                        value={"doc_file": ["Taille maximale du fichier: 5.0 MB"]},
                        response_only=True,
                    ),
                ],
            ),
            401: OpenApiResponse(description="Authentication credentials were not provided or are invalid."),
        },
    )
    def post(self, request, *args, **kwargs):
        serializer = VerificationSerializer(
            data=request.data,
            context={"request": request},
        )
        if serializer.is_valid():
            doc = serializer.save()
            verify_document.delay(doc.id)
            return Response(
                {
                    "message": "Fichiers uploadés et vérification en cours",
                    "document_id": doc.id,
                },
                status=status.HTTP_202_ACCEPTED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(tags=["Verification"])
class VerificationStatusView(APIView):
    """
    Retrieve the verification result for a previously submitted document.

    A user can only access their own documents — querying another user's
    `document_id` returns **404** to avoid information leakage.

    **Result states**

    | `verified` | `verification_result` | Meaning |
    |---|---|---|
    | `false` | `null` | Task still running |
    | `true` | object | Both OCR and face checks passed |
    | `false` | object | At least one check failed |
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get the verification status of a document",
        parameters=[
            OpenApiParameter(
                name="doc_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="ID returned by `POST /api/verify/`.",
                required=True,
            )
        ],
        responses={
            200: OpenApiResponse(
                response=DocumentStatusSerializer,
                description="Verification record — check `verified` and `verification_result`.",
                examples=[
                    OpenApiExample(
                        "Still pending",
                        value={
                            "id": 42,
                            "verified": False,
                            "verification_result": None,
                            "created_at": "2026-04-15T10:00:00Z",
                        },
                        response_only=True,
                    ),
                    OpenApiExample(
                        "Verified — both checks passed",
                        value={
                            "id": 42,
                            "verified": True,
                            "verification_result": {
                                "verified": True,
                                "face_verified": True,
                                "face_score": 0.21,
                                "ocr_verified": True,
                                "ocr_data": {
                                    "first_name": "RODRIGUE",
                                    "last_name": "MBOG",
                                    "birth_date": "12/01/1990",
                                },
                            },
                            "created_at": "2026-04-15T10:00:00Z",
                        },
                        response_only=True,
                    ),
                    OpenApiExample(
                        "Failed — face mismatch",
                        value={
                            "id": 43,
                            "verified": False,
                            "verification_result": {
                                "verified": False,
                                "face_verified": False,
                                "face_score": 0.74,
                                "ocr_verified": True,
                                "ocr_data": {
                                    "first_name": "JEAN",
                                    "last_name": "DUPONT",
                                    "birth_date": "01/01/1985",
                                },
                            },
                            "created_at": "2026-04-15T10:05:00Z",
                        },
                        response_only=True,
                    ),
                    OpenApiExample(
                        "Failed — OCR name mismatch",
                        value={
                            "id": 44,
                            "verified": False,
                            "verification_result": {
                                "verified": False,
                                "face_verified": True,
                                "face_score": 0.18,
                                "ocr_verified": False,
                                "ocr_data": {
                                    "first_name": "JEAN",
                                    "last_name": "DUPONT",
                                    "birth_date": "01/01/1985",
                                },
                            },
                            "created_at": "2026-04-15T10:10:00Z",
                        },
                        response_only=True,
                    ),
                ],
            ),
            401: OpenApiResponse(description="Authentication credentials were not provided or are invalid."),
            404: OpenApiResponse(
                response=ErrorDetailSerializer,
                description="Document not found or belongs to a different user.",
                examples=[
                    OpenApiExample(
                        "Not found",
                        value={"error": "Document introuvable"},
                        response_only=True,
                    )
                ],
            ),
        },
    )
    def get(self, request, doc_id, *args, **kwargs):
        try:
            doc = Document.objects.get(id=doc_id, user=request.user)
        except Document.DoesNotExist:
            return Response(
                {"error": "Document introuvable"},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = DocumentStatusSerializer(doc)
        return Response(serializer.data, status=status.HTTP_200_OK)
