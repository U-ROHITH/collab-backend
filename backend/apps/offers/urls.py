from django.urls import path

from apps.offers import views

urlpatterns = [
    path("offers/", views.BrandOffersView.as_view()),
    path("offers/<uuid:offer_id>/withdraw/", views.BrandOfferWithdrawView.as_view()),
    path("creators/me/offers/", views.CreatorOffersView.as_view()),
    path("creators/me/offers/<uuid:offer_id>/", views.CreatorOfferRespondView.as_view()),
]
