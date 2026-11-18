from django.urls import path

from apps.kyc.admin_views import (
    AdminVerificationApproveView,
    AdminVerificationDetailView,
    AdminVerificationListView,
    AdminVerificationRejectView,
    AdminVerificationRequestInfoView,
)

urlpatterns = [
    path("", AdminVerificationListView.as_view(), name="admin_verifications_list"),
    path(
        "<uuid:verification_id>/",
        AdminVerificationDetailView.as_view(),
        name="admin_verification_detail",
    ),
    path(
        "<uuid:verification_id>/approve/",
        AdminVerificationApproveView.as_view(),
        name="admin_verification_approve",
    ),
    path(
        "<uuid:verification_id>/reject/",
        AdminVerificationRejectView.as_view(),
        name="admin_verification_reject",
    ),
    path(
        "<uuid:verification_id>/request-info/",
        AdminVerificationRequestInfoView.as_view(),
        name="admin_verification_request_info",
    ),
]
