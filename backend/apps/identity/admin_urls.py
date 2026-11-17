from django.urls import path

from apps.identity import views

urlpatterns = [
    path("auth/login/", views.AdminLoginView.as_view(), name="admin_login"),
    path("auth/mfa/verify/", views.AdminMfaVerifyView.as_view(), name="admin_mfa_verify"),
    path("auth/logout/", views.AdminLogoutView.as_view(), name="admin_logout"),
]
