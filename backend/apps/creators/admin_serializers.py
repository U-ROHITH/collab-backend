from typing import Any

from rest_framework import serializers

from apps.core.serializers import StrictSerializer
from apps.creators.models import Category, Creator
from apps.identity.models import UserStatus
from apps.kyc.admin_serializers import AdminAuditOutput


class AdminCategoryOutput(serializers.ModelSerializer):
    creatorCount = serializers.SerializerMethodField()  # noqa: N815
    isActive = serializers.BooleanField(source="is_active")  # noqa: N815

    class Meta:
        model = Category
        fields = ("id", "name", "slug", "description", "icon", "isActive", "creatorCount")

    def get_creatorCount(self, obj: Category) -> int:  # noqa: N802
        count = getattr(obj, "creator_count", None)
        if count is not None:
            return count
        return Creator.objects.filter(categories__contains=[obj.name]).count()


class AdminCategoryInput(StrictSerializer):
    name = serializers.CharField(min_length=2, max_length=60)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    icon = serializers.CharField(required=False, allow_blank=True, default="")


class AdminCategoryUpdateInput(StrictSerializer):
    name = serializers.CharField(required=False, min_length=2, max_length=60)
    description = serializers.CharField(required=False, allow_blank=True)
    icon = serializers.CharField(required=False, allow_blank=True)
    isActive = serializers.BooleanField(required=False)  # noqa: N815


class AdminCategoryMergeInput(StrictSerializer):
    targetCategoryId = serializers.UUIDField()  # noqa: N815


class AdminCreatorListOutput(serializers.ModelSerializer):
    userId = serializers.UUIDField(source="user_id", read_only=True)  # noqa: N815
    displayName = serializers.CharField(source="display_name")  # noqa: N815
    avatarUrl = serializers.CharField(source="avatar_url", allow_null=True)  # noqa: N815
    primaryPlatform = serializers.CharField(source="primary_platform", allow_null=True)  # noqa: N815
    approvalStatus = serializers.CharField(source="approval_status")  # noqa: N815
    featuredOrder = serializers.IntegerField(source="featured_order")  # noqa: N815
    rating = serializers.FloatField(read_only=True)
    status = serializers.SerializerMethodField()
    userStatus = serializers.SerializerMethodField()  # noqa: N815
    suspension = serializers.SerializerMethodField()
    kycStatus = serializers.SerializerMethodField()  # noqa: N815
    reportsCount = serializers.SerializerMethodField()  # noqa: N815
    createdAt = serializers.DateTimeField(source="created_at")  # noqa: N815

    class Meta:
        model = Creator
        fields = (
            "id",
            "userId",
            "slug",
            "displayName",
            "avatarUrl",
            "primaryPlatform",
            "approvalStatus",
            "badges",
            "featured",
            "featuredOrder",
            "tags",
            "rating",
            "status",
            "userStatus",
            "suspension",
            "kycStatus",
            "reportsCount",
            "createdAt",
        )

    def get_status(self, obj: Creator) -> str:
        return obj.user.status if obj.user else "active"

    def get_userStatus(self, obj: Creator) -> str:  # noqa: N802
        return obj.user.status if obj.user else "active"

    def get_suspension(self, obj: Creator) -> dict[str, Any] | None:
        if not obj.user or obj.user.status != UserStatus.SUSPENDED:
            return None
        from apps.identity.models import AuditLog

        audit = (
            AuditLog.objects.filter(entity_id=str(obj.user_id), action="account.suspend")
            .select_related("actor")
            .order_by("-created_at")
            .first()
        )
        if not audit:
            return {"at": None, "by": "system", "reason": "Account suspended"}
        return {
            "at": audit.created_at.isoformat() if audit.created_at else None,
            "by": audit.actor.email or audit.actor.phone if audit.actor else "system",
            "reason": audit.metadata.get("reason", "") if isinstance(audit.metadata, dict) else "",
        }

    def get_kycStatus(self, obj: Creator) -> str:  # noqa: N802
        if not obj.user:
            return "not_started"
        kyc = getattr(obj.user, "kyc", None)
        return kyc.status if kyc else "not_started"

    def get_reportsCount(self, obj: Creator) -> int:  # noqa: N802
        count = getattr(obj, "reports_count", None)
        if count is not None:
            return count
        from apps.moderation.models import AbuseReport

        return AbuseReport.objects.filter(target_type="creator", target_id=obj.id).count()


class AdminCreatorDetailOutput(AdminCreatorListOutput):
    repliesWithin = serializers.IntegerField(source="replies_within", allow_null=True)  # noqa: N815
    followerCount = serializers.IntegerField(source="follower_count", allow_null=True)  # noqa: N815
    platformStats = serializers.JSONField(source="platform_stats")  # noqa: N815
    socialMediaLinks = serializers.JSONField(source="social_media_links")  # noqa: N815
    metrics = serializers.SerializerMethodField()
    recentOrders = serializers.SerializerMethodField()  # noqa: N815
    reports = serializers.SerializerMethodField()
    adminHistory = serializers.SerializerMethodField()  # noqa: N815

    class Meta(AdminCreatorListOutput.Meta):
        fields = AdminCreatorListOutput.Meta.fields + (
            "bio",
            "categories",
            "location",
            "repliesWithin",
            "followerCount",
            "platformStats",
            "socialMediaLinks",
            "metrics",
            "recentOrders",
            "reports",
            "adminHistory",
        )

    def get_metrics(self, obj: Creator) -> dict[str, Any]:
        from apps.orders.models import Dispute, Order, OrderStatus

        orders = list(Order.objects.filter(creator=obj))
        completed = [o for o in orders if o.status == OrderStatus.COMPLETED]
        in_progress = [
            o
            for o in orders
            if o.status
            in (
                OrderStatus.FUNDED,
                OrderStatus.DRAFT_SUBMITTED,
                OrderStatus.REVISION_REQUESTED,
                OrderStatus.APPROVED,
            )
        ]
        disputes = Dispute.objects.filter(order__creator=obj).count()
        gmv_paise = sum(o.subtotal_paise for o in completed)
        return {
            "totalOrders": len(orders),
            "completedOrders": len(completed),
            "inProgressOrders": len(in_progress),
            "disputedOrders": disputes,
            "completionRate": round((len(completed) / len(orders)) * 100, 1) if orders else 100.0,
            "gmvPaise": gmv_paise,
        }

    def get_recentOrders(self, obj: Creator) -> list[dict[str, Any]]:  # noqa: N802
        from apps.orders.models import Order

        orders = Order.objects.filter(creator=obj).select_related("brand").order_by("-created_at")[:10]
        return [
            {
                "id": str(o.id),
                "title": o.title,
                "brandName": o.brand.name if o.brand else "",
                "status": o.status,
                "amountPaise": o.subtotal_paise,
                "createdAt": o.created_at,
            }
            for o in orders
        ]

    def get_reports(self, obj: Creator) -> list[dict[str, Any]]:
        from apps.moderation.models import AbuseReport

        reports = AbuseReport.objects.filter(target_type="creator", target_id=obj.id).order_by("-created_at")[:10]
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

    def get_adminHistory(self, obj: Creator) -> list[dict[str, Any]]:  # noqa: N802
        from apps.identity.models import AuditLog

        target_ids = [str(obj.id)]
        if obj.user_id:
            target_ids.append(str(obj.user_id))
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


class AdminCreatorCurateInput(StrictSerializer):
    badges = serializers.ListField(child=serializers.CharField(), required=False)
    tags = serializers.ListField(child=serializers.CharField(), required=False)
    featured = serializers.BooleanField(required=False)


class AdminCreatorCurateResult(serializers.Serializer):
    creator = AdminCreatorDetailOutput()
    audit = AdminAuditOutput()


class AdminFeaturedCreatorsOutput(serializers.Serializer):
    featured = AdminCreatorListOutput(many=True)
    eligible = AdminCreatorListOutput(many=True)


class AdminFeaturedOrderInput(StrictSerializer):
    creatorIds = serializers.ListField(child=serializers.UUIDField(), min_length=1)  # noqa: N815
