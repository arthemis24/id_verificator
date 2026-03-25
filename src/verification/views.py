from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import BasicAuthentication  # à adapter selon votre système d'authentification
from rest_framework import status
from .serializers import VerificationSerializer
from .tasks import verify_document


class VerificationView(APIView):
    permission_classes = [IsAuthenticated]  # à adapter selon les besoins
    parser_classes = [MultiPartParser, FormParser]
    authentication_classes = [BasicAuthentication]  # à configurer selon votre système d'authentification

    def post(self, request, *args, **kwargs):
        serializer = VerificationSerializer(
            data=request.data,
            context={"request": request}
        )
        if serializer.is_valid():
            doc = serializer.save()  # upload + stockage
            # vérification asynchrone
            verify_document.delay(doc.id)
            return Response(
                {"message": "Fichiers uploadés et vérification en cours"},
                status=status.HTTP_202_ACCEPTED
            )
        print(serializer.errors)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
