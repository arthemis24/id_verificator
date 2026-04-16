from django.urls import path
from .views import VerificationView, VerificationStatusView

urlpatterns = [
    path("verify/", VerificationView.as_view(), name="verify"),
    path("verify/<int:doc_id>/status/", VerificationStatusView.as_view(), name="verify-status"),
]
