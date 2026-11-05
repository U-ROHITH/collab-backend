from django.urls import path

from apps.money import views

urlpatterns = [
    path("checkout/", views.CheckoutView.as_view(), name="checkout"),
    path("payments/verify/", views.PaymentVerifyView.as_view(), name="payment-verify"),
    path("creators/me/wallet/", views.CreatorWalletView.as_view(), name="creator-wallet"),
    path("payouts/", views.CreatorWalletView.as_view(), name="creator-payouts"),
    path("creators/me/withdrawals/", views.WithdrawalRequestView.as_view(), name="creator-withdrawals"),
]
