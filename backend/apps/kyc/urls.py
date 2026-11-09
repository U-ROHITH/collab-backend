from django.urls import path

from apps.kyc import views

urlpatterns = [
    path("kyc/", views.KycView.as_view()),
    path("kyc/pan/", views.KycPanView.as_view()),
    path("kyc/id/", views.KycIdView.as_view()),
    path("kyc/bank/", views.KycBankView.as_view()),
    path("kyc/submit/", views.KycSubmitView.as_view()),
]
