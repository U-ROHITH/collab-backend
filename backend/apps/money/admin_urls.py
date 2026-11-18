from django.urls import path

from apps.money.admin_finance_views import (
    AdminFinanceSummaryView,
    AdminPayoutExportView,
    AdminPayoutListView,
    AdminPlatformRulesView,
)
from apps.orders import admin_views as order_admin_views

urlpatterns = [
    # Rules & Financial Reporting
    path("rules/", AdminPlatformRulesView.as_view(), name="admin_platform_rules"),
    path("summary/", AdminFinanceSummaryView.as_view(), name="admin_finance_summary"),
    path("payouts/", AdminPayoutListView.as_view(), name="admin_payouts_list"),
    path("payouts/export/", AdminPayoutExportView.as_view(), name="admin_payouts_export"),
    # Orders & Escrow Overrides
    path("orders/", order_admin_views.AdminOrderListView.as_view(), name="admin_orders_list"),
    path("orders/pipeline/", order_admin_views.AdminOrderPipelineView.as_view(), name="admin_orders_pipeline"),
    path("orders/<uuid:id>/", order_admin_views.AdminOrderDetailView.as_view(), name="admin_order_detail"),
    path(
        "orders/<uuid:id>/force-release/",
        order_admin_views.AdminOrderForceReleaseView.as_view(),
        name="admin_order_force_release",
    ),
    path(
        "orders/<uuid:id>/refund/",
        order_admin_views.AdminOrderRefundView.as_view(),
        name="admin_order_refund",
    ),
    # Disputes
    path("disputes/", order_admin_views.AdminDisputeListView.as_view(), name="admin_finance_disputes_list"),
    path(
        "disputes/<uuid:id>/",
        order_admin_views.AdminDisputeDetailView.as_view(),
        name="admin_finance_dispute_detail",
    ),
    path(
        "disputes/<uuid:id>/resolve/",
        order_admin_views.AdminDisputeResolveView.as_view(),
        name="admin_finance_dispute_resolve",
    ),
]
