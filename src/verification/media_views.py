import os

from django.conf import settings
from django.db.models import Q
from django.http import FileResponse, Http404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status

from .models import Document


class ProtectedMediaView(APIView):
    """
    Authenticated file-serving endpoint for uploaded media.

    Why this exists:
        Django's default /media/ serving has no access control — any URL that
        can be guessed or intercepted exposes raw identity documents (ID scans,
        selfies) to unauthenticated third parties.

    Access rules:
        - Request must carry a valid JWT Bearer token.
        - The file must belong to a Document owned by the authenticated user.
        - Path traversal sequences (../../) are rejected before any lookup.

    Production note (nginx X-Accel-Redirect):
        Replace the FileResponse below with:
            response = HttpResponse()
            response["X-Accel-Redirect"] = f"/protected-media/{safe_path}"
            response["Content-Type"] = ""
            return response

        And configure nginx:
            location /protected-media/ {
                internal;
                alias /app/media/;
            }

        This lets Django authorise the request while nginx streams the file,
        avoiding buffering large files in the Python process.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, path: str):
        # ── 1. Sanitise path (prevent directory traversal) ────────────────────
        safe_path = os.path.normpath(path).lstrip(os.sep)
        if safe_path.startswith(".."):
            raise Http404

        media_root = os.path.realpath(settings.MEDIA_ROOT)
        full_path = os.path.realpath(os.path.join(media_root, safe_path))

        # Ensure the resolved path is still inside MEDIA_ROOT
        if not full_path.startswith(media_root + os.sep):
            raise Http404

        # ── 2. Ownership check ────────────────────────────────────────────────
        owns = Document.objects.filter(
            user=request.user
        ).filter(
            Q(doc_file=safe_path) | Q(selfie_file=safe_path)
        ).exists()

        if not owns:
            return Response({"error": "Access denied."}, status=status.HTTP_403_FORBIDDEN)

        # ── 3. Serve ──────────────────────────────────────────────────────────
        if not os.path.isfile(full_path):
            raise Http404

        return FileResponse(open(full_path, "rb"))  # noqa: WPS515
