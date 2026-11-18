
from django.db.models import Sum
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.brands.models import Brand
from apps.campaigns.models import Campaign, ModerationStatus
from apps.core.audit import verify_audit_chain
from apps.creators.models import Creator
from apps.identity.models import AdminRole, AuditLog, User, UserStatus
from apps.identity.permissions import RequireAdminRoles
from apps.kyc.models import VerificationRequest, VerificationStatus
from apps.moderation.models import AbuseReport, AbuseReportStatus, BidAlert, BidAlertStatus
from apps.notifications.models import Notification
from apps.orders.admin_views import ESCROW_HELD_STATUSES
from apps.orders.models import Dispute, DisputeStatus, Order, OrderStatus


class AdminDashboardAccountsOutput(serializers.Serializer):
    totalUsers = serializers.IntegerField()  # noqa: N815
    creators = serializers.IntegerField()
    brands = serializers.IntegerField()
    suspended = serializers.IntegerField()


class AdminDashboardOrdersOutput(serializers.Serializer):
    live = serializers.IntegerField()
    byStatus = serializers.DictField(child=serializers.IntegerField())  # noqa: N815
    escrowHeld = serializers.IntegerField()  # noqa: N815


class AdminDashboardVerificationsOutput(serializers.Serializer):
    pending = serializers.IntegerField()
    overSla = serializers.IntegerField()  # noqa: N815
    top = serializers.ListField(child=serializers.DictField())


class AdminDashboardCampaignsOutput(serializers.Serializer):
    pending = serializers.IntegerField()
    oldestSubmittedAt = serializers.DateTimeField(allow_null=True)  # noqa: N815


class AdminDashboardReportsOutput(serializers.Serializer):
    open = serializers.IntegerField()
    totalFlags = serializers.IntegerField()  # noqa: N815
    top = serializers.ListField(child=serializers.DictField())


class AdminDashboardDisputesOutput(serializers.Serializer):
    open = serializers.IntegerField()
    next = serializers.DictField(allow_null=True)


class AdminDashboardAlertsOutput(serializers.Serializer):
    active = serializers.IntegerField()
    recent = serializers.ListField(child=serializers.DictField())


class AdminDashboardAuditChainOutput(serializers.Serializer):
    ok = serializers.BooleanField()
    count = serializers.IntegerField()


class AdminDashboardRecentAuditItem(serializers.Serializer):
    id = serializers.CharField()
    action = serializers.CharField()
    actorEmail = serializers.CharField()  # noqa: N815
    target = serializers.CharField()
    timestamp = serializers.DateTimeField()


class AdminDashboardOutput(serializers.Serializer):
    accounts = AdminDashboardAccountsOutput()
    orders = AdminDashboardOrdersOutput()
    verifications = AdminDashboardVerificationsOutput()
    campaigns = AdminDashboardCampaignsOutput()
    reports = AdminDashboardReportsOutput()
    disputes = AdminDashboardDisputesOutput()
    alerts = AdminDashboardAlertsOutput()
    recentAudit = AdminDashboardRecentAuditItem(many=True)  # noqa: N815
    auditChain = AdminDashboardAuditChainOutput()  # noqa: N815


class AdminCountsOutput(serializers.Serializer):
    verifications = serializers.IntegerField()
    campaigns = serializers.IntegerField()
    disputes = serializers.IntegerField()
    reports = serializers.IntegerField()
    alerts = serializers.IntegerField()
    notifications = serializers.IntegerField()


@extend_schema(tags=["Admin - System"])
class AdminDashboardView(APIView):
    """Aggregate command dashboard metrics across accounts, orders, escrow, verifications,
    campaigns, reports, disputes, and audit integrity.
    """

    permission_classes = [RequireAdminRoles(AdminRole.SUPER_ADMIN, AdminRole.TRUST_SAFETY, AdminRole.FINANCE)]

    @extend_schema(responses={200: AdminDashboardOutput})
    def get(self, request: Request) -> Response:
        now = timezone.now()

        # 1. Accounts
        accounts_data = {
            "totalUsers": User.objects.count(),
            "creators": Creator.objects.count(),
            "brands": Brand.objects.count(),
            "suspended": User.objects.filter(status=UserStatus.SUSPENDED).count(),
        }

        # 2. Orders
        by_status = {}
        for st in OrderStatus.values:
            by_status[st] = Order.objects.filter(status=st).count()

        live_orders_count = Order.objects.filter(
            status__in=(
                OrderStatus.FUNDED,
                OrderStatus.DRAFT_SUBMITTED,
                OrderStatus.REVISION_REQUESTED,
                OrderStatus.APPROVED,
            )
        ).count()

        escrow_held = (
            Order.objects.filter(status__in=ESCROW_HELD_STATUSES).aggregate(s=Sum("subtotal_paise"))["s"] or 0
        )

        orders_data = {
            "live": live_orders_count,
            "byStatus": by_status,
            "escrowHeld": escrow_held,
        }

        # 3. Verifications
        pending_vr = VerificationRequest.objects.filter(status=VerificationStatus.PENDING).select_related("user")
        pending_v_count = pending_vr.count()
        over_sla_v = pending_vr.filter(sla_due_at__lt=now).count()
        top_v = [
            {
                "id": str(v.id),
                "userId": str(v.user_id),
                "email": v.user.email or v.user.phone if v.user else "",
                "kind": v.kind,
                "slaDueAt": v.sla_due_at.isoformat() if v.sla_due_at else None,
                "isOverSla": (v.sla_due_at < now) if v.sla_due_at else False,
            }
            for v in pending_vr.order_by("sla_due_at")[:5]
        ]
        verifications_data = {
            "pending": pending_v_count,
            "overSla": over_sla_v,
            "top": top_v,
        }

        # 4. Campaigns
        pending_c = Campaign.objects.filter(moderation_status=ModerationStatus.PENDING_REVIEW)
        pending_c_count = pending_c.count()
        oldest_c = pending_c.order_by("submitted_at").first()
        campaigns_data = {
            "pending": pending_c_count,
            "oldestSubmittedAt": oldest_c.submitted_at if oldest_c else None,
        }

        # 5. Reports
        open_reports = AbuseReport.objects.filter(
            status__in=[AbuseReportStatus.PENDING, AbuseReportStatus.INVESTIGATING]
        )
        reports_data = {
            "open": open_reports.count(),
            "totalFlags": AbuseReport.objects.count(),
            "top": [
                {
                    "id": str(r.id),
                    "targetType": r.target_type,
                    "targetId": str(r.target_id),
                    "reason": r.reason,
                    "createdAt": r.created_at.isoformat() if r.created_at else None,
                }
                for r in open_reports.order_by("-created_at")[:5]
            ],
        }

        # 6. Disputes
        open_disputes = Dispute.objects.filter(status=DisputeStatus.OPEN).select_related("order")
        next_d = open_disputes.order_by("sla_due_at").first()
        disputes_data = {
            "open": open_disputes.count(),
            "next": {
                "id": str(next_d.id),
                "orderId": str(next_d.order_id),
                "orderTitle": next_d.order.title if next_d.order else "",
                "slaDueAt": next_d.sla_due_at.isoformat() if next_d.sla_due_at else None,
            }
            if next_d
            else None,
        }

        # 7. Alerts
        active_alerts = BidAlert.objects.filter(
            status__in=[BidAlertStatus.PENDING, BidAlertStatus.FROZEN]
        )
        alerts_data = {
            "active": active_alerts.count(),
            "recent": [
                {
                    "id": str(a.id),
                    "type": a.alert_type,
                    "severity": a.severity,
                    "description": (
                        a.details.get("reason", a.alert_type) if isinstance(a.details, dict) else str(a.details)
                    ),
                    "createdAt": a.created_at.isoformat() if a.created_at else None,
                }
                for a in active_alerts.order_by("-created_at")[:5]
            ],
        }

        # 8. Recent Audit & Hash Chain
        recent_logs = AuditLog.objects.select_related("actor").order_by("-created_at")[:5]
        recent_audit_data = [
            {
                "id": str(log.id),
                "action": log.action,
                "actorEmail": log.actor.email or log.actor.phone if log.actor else "system",
                "target": log.target,
                "timestamp": log.created_at,
            }
            for log in recent_logs
        ]

        ok, count, _ = verify_audit_chain()
        chain_data = {"ok": ok, "count": count}

        return Response({
            "accounts": accounts_data,
            "orders": orders_data,
            "verifications": verifications_data,
            "campaigns": campaigns_data,
            "reports": reports_data,
            "disputes": disputes_data,
            "alerts": alerts_data,
            "recentAudit": recent_audit_data,
            "auditChain": chain_data,
        })


@extend_schema(tags=["Admin - System"])
class AdminCountsView(APIView):
    """Badge counters for the admin navigation header."""

    permission_classes = [RequireAdminRoles(AdminRole.SUPER_ADMIN, AdminRole.TRUST_SAFETY, AdminRole.FINANCE)]

    @extend_schema(responses={200: AdminCountsOutput})
    def get(self, request: Request) -> Response:
        return Response({
            "verifications": VerificationRequest.objects.filter(status=VerificationStatus.PENDING).count(),
            "campaigns": Campaign.objects.filter(moderation_status=ModerationStatus.PENDING_REVIEW).count(),
            "disputes": Dispute.objects.filter(status=DisputeStatus.OPEN).count(),
            "reports": AbuseReport.objects.filter(
                status__in=[AbuseReportStatus.PENDING, AbuseReportStatus.INVESTIGATING]
            ).count(),
            "alerts": BidAlert.objects.filter(
                status__in=[BidAlertStatus.PENDING, BidAlertStatus.FROZEN]
            ).count(),
            "notifications": Notification.objects.filter(user=request.user, read_at__isnull=True).count(),
        })
