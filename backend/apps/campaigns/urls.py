from django.urls import path

from apps.campaigns import views

urlpatterns = [
    path("campaigns/", views.CampaignListCreateView.as_view()),
    path("creators/me/campaigns/", views.CampaignListCreateView.as_view()),  # spec alias for creators
    path("campaigns/<uuid:campaign_id>/", views.CampaignDetailView.as_view()),
    path("campaigns/<uuid:campaign_id>/close/", views.CampaignCloseView.as_view()),
    path("campaigns/<uuid:campaign_id>/bids/", views.BidListCreateView.as_view()),
    path("campaigns/<uuid:campaign_id>/bids/<uuid:bid_id>/", views.BidDetailView.as_view()),
    path("campaigns/<uuid:campaign_id>/bids/<uuid:bid_id>/withdraw/", views.BidWithdrawView.as_view()),
    path("campaigns/<uuid:campaign_id>/bids/<uuid:bid_id>/select/", views.BidSelectView.as_view()),
]
