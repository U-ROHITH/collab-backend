from datetime import timedelta
from typing import Any

from django.utils import timezone
from rest_framework import serializers

from apps.brands.models import Brand
from apps.campaigns.models import Campaign, ModerationStatus


class AdminBrandListOutput(serializers.ModelSerializer):
    legalName = serializers.CharField(source="legal_name", allow_blank=True)  # noqa: N815
    logoUrl = serializers.CharField(source="logo_url", allow_null=True)  # noqa: N815
    regVerified = serializers.BooleanField(source="reg_verified")  # noqa: N815
    userStatus = serializers.SerializerMethodField()  # noqa: N815
    campaignsCount = serializers.SerializerMethodField()  # noqa: N815
    postingSpike = serializers.SerializerMethodField()  # noqa: N815
    reportsCount = serializers.SerializerMethodField()  # noqa: N815
    createdAt = serializers.DateTimeField(source="created_at")  # noqa: N815

    class Meta:
        model = Brand
        fields = (
            "id",
            "name",
            "legalName",
            "logoUrl",
            "website",
            "industry",
            "gstin",
            "regVerified",
            "userStatus",
            "campaignsCount",
            "postingSpike",
            "reportsCount",
            "createdAt",
        )

    def get_userStatus(self, obj: Brand) -> str:  # noqa: N802
        return obj.owner.status if obj.owner else "active"

    def get_campaignsCount(self, obj: Brand) -> int:  # noqa: N802
        count = getattr(obj, "campaign_count", None)
        if count is not None:
            return count
        return obj.campaigns.count()

    def get_postingSpike(self, obj: Brand) -> bool:  # noqa: N802
        spike = getattr(obj, "has_posting_spike", None)
        if spike is not None:
            return spike
        now = timezone.now()
        thirty_days_ago = now - timedelta(days=30)
        recent_count = Campaign.objects.filter(brand=obj, created_at__gte=thirty_days_ago).count()
        if recent_count < 5:
            return False
        total_campaigns = Campaign.objects.filter(brand=obj).count()
        days_active = max(1, (now - obj.created_at).days)
        months_active = max(1.0, days_active / 30.0)
        monthly_average = total_campaigns / months_active
        return recent_count > 2.0 * monthly_average

    def get_reportsCount(self, obj: Brand) -> int:  # noqa: N802
        count = getattr(obj, "reports_count", None)
        if count is not None:
            return count
        from apps.moderation.models import AbuseReport

        return AbuseReport.objects.filter(target_type="brand", target_id=obj.id).count()


class AdminBrandDetailOutput(AdminBrandListOutput):
    companyPan = serializers.CharField(source="company_pan", allow_blank=True)  # noqa: N815
    signatoryName = serializers.CharField(source="signatory_name", allow_blank=True)  # noqa: N815
    signatoryDesignation = serializers.CharField(source="signatory_designation", allow_blank=True)  # noqa: N815
    billingAddress = serializers.JSONField(source="billing_address")  # noqa: N815
    metrics = serializers.SerializerMethodField()
    recentOrders = serializers.SerializerMethodField()  # noqa: N815
    reports = serializers.SerializerMethodField()
    adminHistory = serializers.SerializerMethodField()  # noqa: N815

    class Meta(AdminBrandListOutput.Meta):
        fields = AdminBrandListOutput.Meta.fields + (
            "companyPan",
            "cin",
            "signatoryName",
            "signatoryDesignation",
            "billingAddress",
            "metrics",
            "recentOrders",
            "reports",
            "adminHistory",
        )

    def get_metrics(self, obj: Brand) -> dict[str, Any]:
        from apps.orders.models import Order, OrderStatus

        campaigns = Campaign.objects.filter(brand=obj)
        orders = list(Order.objects.filter(brand=obj))
        completed = [o for o in orders if o.status == OrderStatus.COMPLETED]
        total_spent = sum(o.subtotal_paise for o in completed)
        return {
            "totalCampaigns": campaigns.count(),
            "liveCampaigns": campaigns.filter(moderation_status=ModerationStatus.LIVE).count(),
            "pendingCampaigns": campaigns.filter(moderation_status=ModerationStatus.PENDING_REVIEW).count(),
            "totalOrders": len(orders),
            "completedOrders": len(completed),
            "totalSpentPaise": total_spent,
        }

    def get_recentOrders(self, obj: Brand) -> list[dict[str, Any]]:  # noqa: N802
        from apps.orders.models import Order

        orders = Order.objects.filter(brand=obj).select_related("creator").order_by("-created_at")[:10]
        return [
            {
                "id": str(o.id),
                "title": o.title,
                "creatorName": o.creator.display_name if o.creator else "",
                "status": o.status,
                "amountPaise": o.subtotal_paise,
                "createdAt": o.created_at,
            }
            for o in orders
        ]

    def get_reports(self, obj: Brand) -> list[dict[str, Any]]:
        from apps.moderation.models import AbuseReport

        reports = AbuseReport.objects.filter(target_type="brand", target_id=obj.id).order_by("-created_at")[:10]
        return [
            {
                "id": str(r.id),
                "reason": r.reason,
                "status": r.status,
                "actionTaken": r.action_taken,
                "createdAt": r.created_at,
            }
            for r in reports
        ]

    def get_adminHistory(self, obj: Brand) -> list[dict[str, Any]]:  # noqa: N802
        from apps.identity.models import AuditLog

        target_ids = [str(obj.id)]
        if obj.owner_id:
            target_ids.append(str(obj.owner_id))
        logs = (
            AuditLog.objects.filter(entity_id__in=target_ids)
            .select_related("actor")
            .order_by("-created_at")[:20]
        )
        return [
            {
                "id": str(log.id),
                "action": log.action,
                "actorEmail": log.actor.email if log.actor else "",
                "detail": log.detail,
                "timestamp": log.created_at,
            }
            for log in logs
        ]
