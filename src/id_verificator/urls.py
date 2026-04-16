from django.contrib import admin
from django.urls import path, include
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView
from drf_spectacular.utils import extend_schema, OpenApiExample, OpenApiResponse
from rest_framework_simplejwt.serializers import (
    TokenObtainPairSerializer,
    TokenRefreshSerializer,
)


# Annotate simplejwt views (they live in a third-party lib, so we wrap here).

@extend_schema(
    tags=["Authentication"],
    summary="Obtain a JWT token pair",
    description=(
        "Authenticate with username and password. "
        "Returns an **access** token (short-lived) and a **refresh** token (long-lived).\n\n"
        "Pass the access token as `Authorization: Bearer <access>` on every protected request."
    ),
    responses={
        200: OpenApiResponse(
            response=TokenObtainPairSerializer,
            description="Token pair issued successfully.",
            examples=[
                OpenApiExample(
                    "Success",
                    value={
                        "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                        "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                    },
                    response_only=True,
                )
            ],
        ),
        401: OpenApiResponse(
            description="Invalid credentials.",
            examples=[
                OpenApiExample(
                    "Wrong password",
                    value={"detail": "No active account found with the given credentials"},
                    response_only=True,
                )
            ],
        ),
    },
)
class AnnotatedTokenObtainPairView(TokenObtainPairView):
    pass


@extend_schema(
    tags=["Authentication"],
    summary="Refresh an access token",
    description=(
        "Exchange a valid **refresh** token for a new **access** token. "
        "Use this when the access token has expired (typically after 5 minutes)."
    ),
    responses={
        200: OpenApiResponse(
            response=TokenRefreshSerializer,
            description="New access token issued.",
            examples=[
                OpenApiExample(
                    "Success",
                    value={"access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."},
                    response_only=True,
                )
            ],
        ),
        401: OpenApiResponse(
            description="Refresh token is invalid or expired.",
            examples=[
                OpenApiExample(
                    "Expired",
                    value={"detail": "Token is invalid or expired", "code": "token_not_valid"},
                    response_only=True,
                )
            ],
        ),
    },
)
class AnnotatedTokenRefreshView(TokenRefreshView):
    pass


urlpatterns = [
    path('admin/', admin.site.urls),

    # Verification endpoints
    path('api/', include('verification.urls')),

    # JWT auth
    path('api/token/', AnnotatedTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', AnnotatedTokenRefreshView.as_view(), name='token_refresh'),

    # OpenAPI schema + UIs
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
]
