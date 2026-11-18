from django.urls import path

from apps.campaigns.admin_campaign_views import (
    AdminCampaignApproveView,
    AdminCampaignDetailView,
    AdminCampaignHideView,
    AdminCampaignListView,
    AdminCampaignRejectView,
    AdminCampaignRemoveView,
    AdminCampaignRestoreView,
)

urlpatterns = [
    path("", AdminCampaignListView.as_view(), name="admin_campaigns_list"),
    path(
        "<uuid:campaign_id>/",
        AdminCampaignDetailView.as_view(),
        name="admin_campaign_detail",
    ),
    path(
        "<uuid:campaign_id>/approve/",
        AdminCampaignApproveView.as_view(),
        name="admin_campaign_approve",
    ),
    path(
        "<uuid:campaign_id>/reject/",
        AdminCampaignRejectView.as_view(),
        name="admin_campaign_reject",
    ),
    path(
        "<uuid:campaign_id>/hide/",
        AdminCampaignHideView.as_view(),
        name="admin_campaign_hide",
    ),
    path(
        "<uuid:campaign_id>/remove/",
        AdminCampaignRemoveView.as_view(),
        name="admin_campaign_remove",
    ),
    path(
        "<uuid:campaign_id>/restore/",
        AdminCampaignRestoreView.as_view(),
        name="admin_campaign_restore",
    ),
]
