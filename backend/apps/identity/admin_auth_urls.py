from django.urls import path

from apps.identity.admin_auth_views import AdminLoginView, AdminLogoutView, AdminMfaVerifyView

urlpatterns = [
    path("login/", AdminLoginView.as_view(), name="admin_login"),
    path("mfa/verify/", AdminMfaVerifyView.as_view(), name="admin_mfa_verify"),
    path("logout/", AdminLogoutView.as_view(), name="admin_logout"),
]
