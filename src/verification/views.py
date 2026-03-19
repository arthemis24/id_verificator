from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework import status
from .serializers import VerificationSerializer
from .tasks import verify_document


class VerificationView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, *args, **kwargs):
        serializer = VerificationSerializer(data=request.data)
        if serializer.is_valid():
            doc = serializer.save(user=request.user)  # upload + stockage
            # vérification asynchrone
            verify_document.delay(doc.id)
            return Response(
                {"message": "Fichiers uploadés et vérification en cours"},
                status=status.HTTP_202_ACCEPTED
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
