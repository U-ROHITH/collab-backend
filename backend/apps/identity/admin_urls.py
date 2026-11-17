from django.urls import path

from apps.identity import views
from apps.kyc import admin_views as kyc_admin_views

urlpatterns = [
    path("auth/login/", views.AdminLoginView.as_view(), name="admin_login"),
    path("auth/mfa/verify/", views.AdminMfaVerifyView.as_view(), name="admin_mfa_verify"),
    path("auth/logout/", views.AdminLogoutView.as_view(), name="admin_logout"),
    path("verifications/", kyc_admin_views.AdminVerificationListView.as_view(), name="admin_verifications_list"),
    path(
        "verifications/<uuid:verification_id>/",
        kyc_admin_views.AdminVerificationDetailView.as_view(),
        name="admin_verification_detail",
    ),
    path(
        "verifications/<uuid:verification_id>/approve/",
        kyc_admin_views.AdminVerificationApproveView.as_view(),
        name="admin_verification_approve",
    ),
    path(
        "verifications/<uuid:verification_id>/reject/",
        kyc_admin_views.AdminVerificationRejectView.as_view(),
        name="admin_verification_reject",
    ),
    path(
        "verifications/<uuid:verification_id>/request-info/",
        kyc_admin_views.AdminVerificationRequestInfoView.as_view(),
        name="admin_verification_request_info",
    ),
]
