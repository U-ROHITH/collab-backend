from django.urls import path

from apps.identity import views

urlpatterns = [
    path("login/", views.LoginView.as_view()),
    path("google/", views.GoogleAuthView.as_view()),
    path("otp/request/", views.OtpRequestView.as_view()),
    path("otp/verify/", views.OtpVerifyView.as_view()),
    path("email/request/", views.EmailVerificationRequestView.as_view()),
    path("email/verify/", views.EmailVerificationVerifyView.as_view()),
    path("token/refresh/", views.TokenRefreshEnvelopeView.as_view()),
    path("logout/", views.LogoutView.as_view()),
    path("logout-all/", views.LogoutAllView.as_view()),
    path("me/", views.MeView.as_view()),
    path("signup/complete/", views.SignupCompleteView.as_view()),
]
