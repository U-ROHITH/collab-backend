from typing import Any

from django.utils import timezone
from rest_framework import serializers

from apps.core.serializers import StrictSerializer
from apps.kyc.admin_serializers import AdminAuditOutput
from apps.orders.models import Dispute, Order, Review


class AdminReviewOutput(serializers.ModelSerializer):
    orderId = serializers.UUIDField(source="order_id")  # noqa: N815
    brandName = serializers.CharField(source="brand.name")  # noqa: N815
    creatorName = serializers.CharField(source="creator.display_name")  # noqa: N815
    brandId = serializers.UUIDField(source="brand_id")  # noqa: N815
    creatorId = serializers.UUIDField(source="creator_id")  # noqa: N815
    flagReason = serializers.CharField(source="flag_reason", allow_blank=True)  # noqa: N815
    createdAt = serializers.DateTimeField(source="created_at")  # noqa: N815

    class Meta:
        model = Review
        fields = (
            "id",
            "orderId",
            "brandId",
            "brandName",
            "creatorId",
            "creatorName",
            "stars",
            "text",
            "status",
            "flagReason",
            "createdAt",
        )


class AdminReviewReasonInput(StrictSerializer):
    reason = serializers.CharField(min_length=4, max_length=500)


class AdminReviewKeepInput(StrictSerializer):
    note = serializers.CharField(required=False, allow_blank=True, max_length=500, default="")


class AdminReviewActionResult(serializers.Serializer):
    review = AdminReviewOutput()
    audit = AdminAuditOutput()


class AdminOrderListOutput(serializers.ModelSerializer):
    brand = serializers.SerializerMethodField()
    creator = serializers.SerializerMethodField()
    subtotalPaise = serializers.IntegerField(source="subtotal_paise")  # noqa: N815
    creatorPayoutPaise = serializers.IntegerField(source="creator_payout_paise")  # noqa: N815
    brandTotalPaise = serializers.IntegerField(source="brand_total_paise")  # noqa: N815
    sourceType = serializers.CharField(source="source_type")  # noqa: N815
    deliveryDays = serializers.IntegerField(source="delivery_days")  # noqa: N815
    revisionCount = serializers.IntegerField(source="revision_count")  # noqa: N815
    maxRevisions = serializers.IntegerField(source="max_revisions")  # noqa: N815
    createdAt = serializers.DateTimeField(source="created_at")  # noqa: N815
    deadline = serializers.DateTimeField(allow_null=True)
    startedAt = serializers.DateTimeField(source="started_at", allow_null=True)  # noqa: N815
    fundedAt = serializers.DateTimeField(source="funded_at", allow_null=True)  # noqa: N815
    completedAt = serializers.DateTimeField(source="completed_at", allow_null=True)  # noqa: N815
    releasedAt = serializers.DateTimeField(source="released_at", allow_null=True)  # noqa: N815
    refundedAt = serializers.DateTimeField(source="refunded_at", allow_null=True)  # noqa: N815

    class Meta:
        model = Order
        fields = (
            "id",
            "title",
            "deliverables",
            "brief",
            "status",
            "brand",
            "creator",
            "subtotalPaise",
            "creatorPayoutPaise",
            "brandTotalPaise",
            "sourceType",
            "deliveryDays",
            "revisionCount",
            "maxRevisions",
            "deadline",
            "startedAt",
            "fundedAt",
            "completedAt",
            "releasedAt",
            "refundedAt",
            "createdAt",
        )

    def get_brand(self, obj: Order) -> dict[str, Any]:
        return {
            "id": str(obj.brand_id),
            "name": obj.brand.name if obj.brand else "",
            "email": obj.brand.owner.email if (obj.brand and obj.brand.owner) else "",
            "avatarUrl": getattr(obj.brand, "logo_url", None),
        }

    def get_creator(self, obj: Order) -> dict[str, Any]:
        return {
            "id": str(obj.creator_id),
            "name": obj.creator.display_name if obj.creator else "",
            "email": obj.creator.user.email if (obj.creator and obj.creator.user) else "",
            "avatarUrl": getattr(obj.creator, "avatar_url", None),
        }


class AdminOrderPipelineItem(serializers.Serializer):
    status = serializers.CharField()
    count = serializers.IntegerField()
    value = serializers.IntegerField()


class AdminOrderPipelineOutput(serializers.Serializer):
    pipeline = AdminOrderPipelineItem(many=True)
    heldTotal = serializers.IntegerField()  # noqa: N815
    disputedTotal = serializers.IntegerField()  # noqa: N815


class AdminOrderDetailOutput(AdminOrderListOutput):
    breakdown = serializers.SerializerMethodField()
    payout = serializers.SerializerMethodField()
    timeline = serializers.SerializerMethodField()

    class Meta(AdminOrderListOutput.Meta):
        fields = AdminOrderListOutput.Meta.fields + (
            "breakdown",
            "payout",
            "timeline",
        )

    def get_breakdown(self, obj: Order) -> dict[str, int]:
        from apps.money.models import PlatformRule

        rule = PlatformRule.objects.filter(key="default").first()
        gw_pct = float(rule.gateway_pct) if rule else 2.0
        gateway_paise = int(obj.brand_total_paise * (gw_pct / 100.0))
        commission_paise = obj.platform_fee_paise
        creator_gets = obj.creator_payout_paise
        linkloom_net = commission_paise - gateway_paise

        return {
            "price": obj.subtotal_paise,
            "commission": commission_paise,
            "gateway": gateway_paise,
            "creatorGets": creator_gets,
            "linkloomNet": linkloom_net,
        }

    def get_payout(self, obj: Order) -> dict[str, Any]:
        from apps.money.models import WithdrawalRequest

        w = (
            WithdrawalRequest.objects.filter(creator=obj.creator)
            .order_by("-created_at")
            .first()
        )
        return {
            "status": w.status if w else ("processing" if obj.released_at else "pending"),
            "amountPaise": obj.creator_payout_paise,
            "payoutMethod": w.payout_method if w else "bank_transfer",
            "processedAt": w.processed_at if w else obj.released_at,
        }

    def get_timeline(self, obj: Order) -> list[dict[str, Any]]:
        from apps.identity.models import AuditLog

        logs = AuditLog.objects.filter(entity_id=str(obj.id)).select_related("actor").order_by("-created_at")
        events = obj.events.select_related("actor").order_by("-created_at")
        timeline = []
        for log in logs:
            timeline.append({
                "id": str(log.id),
                "action": log.action,
                "actorEmail": log.actor.email if log.actor else "system",
                "timestamp": log.created_at,
                "detail": log.detail,
                "hash": log.hash,
            })
        for ev in events:
            timeline.append({
                "id": str(ev.id),
                "action": f"order.{ev.event}",
                "actorEmail": ev.actor.email if ev.actor else "system",
                "timestamp": ev.created_at,
                "detail": {"fromStatus": ev.from_status, "toStatus": ev.to_status, "reason": ev.reason},
                "hash": "",
            })
        timeline.sort(key=lambda x: x["timestamp"], reverse=True)
        return timeline


class AdminEscrowActionInput(StrictSerializer):
    reason = serializers.CharField(min_length=12, max_length=1000)
    disputeId = serializers.UUIDField(required=False, allow_null=True)  # noqa: N815


class AdminEscrowActionResult(serializers.Serializer):
    order = AdminOrderListOutput()
    audit = AdminAuditOutput()
    disputeResolved = serializers.BooleanField()  # noqa: N815


class AdminDisputeListOutput(serializers.ModelSerializer):
    orderId = serializers.UUIDField(source="order_id")  # noqa: N815
    orderTitle = serializers.CharField(source="order.title")  # noqa: N815
    orderStatus = serializers.CharField(source="order.status")  # noqa: N815
    orderAmountPaise = serializers.IntegerField(source="order.subtotal_paise")  # noqa: N815
    openedByRole = serializers.SerializerMethodField()  # noqa: N815
    openedByEmail = serializers.SerializerMethodField()  # noqa: N815
    slaDueAt = serializers.DateTimeField(source="sla_due_at")  # noqa: N815
    isOverSla = serializers.SerializerMethodField()  # noqa: N815
    createdAt = serializers.DateTimeField(source="created_at")  # noqa: N815

    class Meta:
        model = Dispute
        fields = (
            "id",
            "orderId",
            "orderTitle",
            "orderStatus",
            "orderAmountPaise",
            "openedByRole",
            "openedByEmail",
            "kind",
            "reason",
            "slaDueAt",
            "isOverSla",
            "status",
            "createdAt",
        )

    def get_openedByRole(self, obj: Dispute) -> str:  # noqa: N802
        if obj.order.brand and obj.order.brand.owner_id == obj.opened_by_id:
            return "brand"
        if obj.order.creator and obj.order.creator.user_id == obj.opened_by_id:
            return "creator"
        return "user"

    def get_openedByEmail(self, obj: Dispute) -> str:  # noqa: N802
        return obj.opened_by.email or obj.opened_by.phone if obj.opened_by else ""

    def get_isOverSla(self, obj: Dispute) -> bool:  # noqa: N802
        if obj.status == "resolved" or not obj.sla_due_at:
            return False
        return timezone.now() > obj.sla_due_at


class AdminDisputeDetailOutput(AdminDisputeListOutput):
    evidenceUploadIds = serializers.JSONField(source="evidence_upload_ids")  # noqa: N815
    counterpartyStatement = serializers.CharField(source="counterparty_statement")  # noqa: N815
    counterpartyStatementAt = serializers.DateTimeField(source="counterparty_statement_at")  # noqa: N815
    counterpartyUploadIds = serializers.JSONField(source="counterparty_upload_ids")  # noqa: N815
    decisionMetadata = serializers.JSONField(source="decision_metadata")  # noqa: N815
    resolution = serializers.CharField()
    resolvedByEmail = serializers.SerializerMethodField()  # noqa: N815
    resolvedAt = serializers.DateTimeField(source="resolved_at", allow_null=True)  # noqa: N815
    adminHistory = serializers.SerializerMethodField()  # noqa: N815

    class Meta(AdminDisputeListOutput.Meta):
        fields = AdminDisputeListOutput.Meta.fields + (
            "evidenceUploadIds",
            "counterpartyStatement",
            "counterpartyStatementAt",
            "counterpartyUploadIds",
            "decisionMetadata",
            "resolution",
            "resolvedByEmail",
            "resolvedAt",
            "adminHistory",
        )

    def get_resolvedByEmail(self, obj: Dispute) -> str:  # noqa: N802
        return obj.resolved_by.email if obj.resolved_by else ""

    def get_adminHistory(self, obj: Dispute) -> list[dict[str, Any]]:  # noqa: N802
        from apps.identity.models import AuditLog

        target_ids = [str(obj.id), str(obj.order_id)]
        logs = AuditLog.objects.filter(entity_id__in=target_ids).select_related("actor").order_by("-created_at")[:20]
        return [
            {
                "id": str(log.id),
                "action": log.action,
                "actorEmail": log.actor.email if log.actor else "",
                "detail": log.detail,
                "timestamp": log.created_at,
                "hash": log.hash,
            }
            for log in logs
        ]


class AdminDisputeResolveInput(StrictSerializer):
    reason = serializers.CharField(min_length=4, max_length=1000)


class AdminDisputeResolveResult(serializers.Serializer):
    dispute = AdminDisputeDetailOutput()
    order = AdminOrderListOutput()
    audit = AdminAuditOutput()


