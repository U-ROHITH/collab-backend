from django.urls import path

from apps.kyc import views

urlpatterns = [
    path("kyc/", views.KycView.as_view()),
    path("kyc/pan/", views.KycPanView.as_view()),
    path("kyc/id/", views.KycIdView.as_view()),
    path("kyc/bank/", views.KycBankView.as_view()),
    path("kyc/submit/", views.KycSubmitView.as_view()),
    # Brand KYC: voluntary company PAN + bank (apps.kyc.brand_kyc)
    path("brands/me/kyc/", views.BrandKycView.as_view()),
    path("brands/me/kyc/pan/", views.BrandKycPanView.as_view()),
    path("brands/me/kyc/bank/", views.BrandKycBankView.as_view()),
]
