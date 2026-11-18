from django.urls import path

from apps.identity import admin_audit_views, admin_dashboard_views
from apps.notifications import admin_views as notification_admin_views

urlpatterns = [
    # Command Dashboard & Counts
    path("dashboard/", admin_dashboard_views.AdminDashboardView.as_view(), name="admin_dashboard"),
    path("counts/", admin_dashboard_views.AdminCountsView.as_view(), name="admin_counts"),
    # Audit Log & Cryptographic Verification
    path("audit-log/", admin_audit_views.AdminAuditLogListView.as_view(), name="admin_audit_log_list"),
    path("audit-log/verify/", admin_audit_views.AdminAuditLogVerifyView.as_view(), name="admin_audit_log_verify"),
    path("audit-log/export/", admin_audit_views.AdminAuditLogExportView.as_view(), name="admin_audit_log_export"),
    # Admin Notifications
    path(
        "notifications/",
        notification_admin_views.AdminNotificationListView.as_view(),
        name="admin_notifications_list",
    ),
    path(
        "notifications/unread-count/",
        notification_admin_views.AdminNotificationUnreadCountView.as_view(),
        name="admin_notifications_unread_count",
    ),
    path(
        "notifications/<uuid:id>/read/",
        notification_admin_views.AdminNotificationMarkReadView.as_view(),
        name="admin_notification_mark_read",
    ),
    path(
        "notifications/read-all/",
        notification_admin_views.AdminNotificationMarkAllReadView.as_view(),
        name="admin_notifications_read_all",
    ),
]
