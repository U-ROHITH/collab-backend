from django.urls import path

from apps.moderation.admin_moderation_views import (
    AdminAbuseReportDetailView,
    AdminAbuseReportDismissView,
    AdminAbuseReportListView,
    AdminAbuseReportRemoveView,
    AdminAbuseReportSuspendAccountView,
    AdminAbuseReportWarnView,
    AdminBidAlertDismissView,
    AdminBidAlertFreezeBidsView,
    AdminBidAlertListView,
    AdminBidAlertSuspendAccountsView,
    AdminBidAlertWarnView,
    AdminReviewDeleteView,
    AdminReviewKeepView,
    AdminReviewListView,
)
from apps.orders import admin_views as order_admin_views

urlpatterns = [
    # Abuse Reports
    path("abuse-reports/", AdminAbuseReportListView.as_view(), name="admin_abuse_reports_list"),
    path(
        "abuse-reports/<uuid:id>/",
        AdminAbuseReportDetailView.as_view(),
        name="admin_abuse_report_detail",
    ),
    path(
        "abuse-reports/<uuid:id>/remove/",
        AdminAbuseReportRemoveView.as_view(),
        name="admin_abuse_report_remove",
    ),
    path(
        "abuse-reports/<uuid:id>/warn/",
        AdminAbuseReportWarnView.as_view(),
        name="admin_abuse_report_warn",
    ),
    path(
        "abuse-reports/<uuid:id>/suspend-account/",
        AdminAbuseReportSuspendAccountView.as_view(),
        name="admin_abuse_report_suspend_account",
    ),
    path(
        "abuse-reports/<uuid:id>/dismiss/",
        AdminAbuseReportDismissView.as_view(),
        name="admin_abuse_report_dismiss",
    ),
    # Bid Alerts
    path("bid-alerts/", AdminBidAlertListView.as_view(), name="admin_bid_alerts_list"),
    path(
        "bid-alerts/<uuid:id>/freeze-bids/",
        AdminBidAlertFreezeBidsView.as_view(),
        name="admin_bid_alert_freeze_bids",
    ),
    path(
        "bid-alerts/<uuid:id>/suspend-accounts/",
        AdminBidAlertSuspendAccountsView.as_view(),
        name="admin_bid_alert_suspend_accounts",
    ),
    path(
        "bid-alerts/<uuid:id>/warn/",
        AdminBidAlertWarnView.as_view(),
        name="admin_bid_alert_warn",
    ),
    path(
        "bid-alerts/<uuid:id>/dismiss/",
        AdminBidAlertDismissView.as_view(),
        name="admin_bid_alert_dismiss",
    ),
    # Reviews
    path("reviews/", AdminReviewListView.as_view(), name="admin_reviews_list"),
    path(
        "reviews/<uuid:id>/delete/",
        AdminReviewDeleteView.as_view(),
        name="admin_review_delete",
    ),
    path(
        "reviews/<uuid:id>/keep/",
        AdminReviewKeepView.as_view(),
        name="admin_review_keep",
    ),
    # Disputes (Mediation)
    path("disputes/", order_admin_views.AdminDisputeListView.as_view(), name="admin_moderation_disputes_list"),
    path(
        "disputes/<uuid:id>/",
        order_admin_views.AdminDisputeDetailView.as_view(),
        name="admin_moderation_dispute_detail",
    ),
    path(
        "disputes/<uuid:id>/resolve/",
        order_admin_views.AdminDisputeResolveView.as_view(),
        name="admin_moderation_dispute_resolve",
    ),
]
