from django.urls import path

from apps.identity import views

urlpatterns = [
    path("otp/request/", views.OtpRequestView.as_view()),
    path("otp/verify/", views.OtpVerifyView.as_view()),
    path("google/", views.GoogleSignInView.as_view()),
    path("login/", views.LoginView.as_view()),
    path("password/reset/", views.PasswordResetView.as_view()),
    path("password/change/", views.PasswordChangeView.as_view()),
    path("token/refresh/", views.TokenRefreshEnvelopeView.as_view()),
    path("logout/", views.LogoutView.as_view()),
    path("logout-all/", views.LogoutAllView.as_view()),
    path("me/", views.MeView.as_view()),
    path("signup/complete/", views.SignupCompleteView.as_view()),
]
