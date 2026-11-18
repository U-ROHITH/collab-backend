from typing import Any

from rest_framework import serializers

from apps.campaigns.models import BidStatus, Campaign
from apps.campaigns.serializers import money
from apps.core.serializers import StrictSerializer
from apps.kyc.admin_serializers import AdminAuditOutput


class AdminCampaignListBrandOutput(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    logoUrl = serializers.CharField(source="logo_url", allow_null=True)  # noqa: N815


class AdminCampaignBrandOutput(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    legalName = serializers.CharField(source="legal_name", allow_blank=True, default="")  # noqa: N815
    gstin = serializers.CharField(allow_blank=True, default="")
    regVerified = serializers.BooleanField(source="reg_verified", default=False)  # noqa: N815
    logoUrl = serializers.CharField(source="logo_url", allow_null=True)  # noqa: N815


class AdminCampaignSignalOutput(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    result = serializers.ChoiceField(choices=["pass", "warn", "fail"])
    text = serializers.CharField()
    blocking = serializers.BooleanField(default=False)


class AdminCampaignBidsSummaryOutput(serializers.Serializer):
    total = serializers.IntegerField()
    pending = serializers.IntegerField()
    held = serializers.IntegerField()
    selected = serializers.IntegerField()
    rejected = serializers.IntegerField()


class AdminCampaignModerationDetailOutput(serializers.Serializer):
    status = serializers.CharField()
    reason = serializers.CharField(allow_null=True)
    ruleBroken = serializers.CharField(allow_null=True)  # noqa: N815
    changedFields = serializers.ListField(child=serializers.CharField())  # noqa: N815
    moderatedAt = serializers.DateTimeField(allow_null=True)  # noqa: N815
    moderatedBy = serializers.CharField(allow_null=True)  # noqa: N815


class AdminCampaignListOutput(serializers.ModelSerializer):
    brand = AdminCampaignListBrandOutput()
    budgetMin = serializers.SerializerMethodField()  # noqa: N815
    budgetMax = serializers.SerializerMethodField()  # noqa: N815
    moderationStatus = serializers.CharField(source="moderation_status")  # noqa: N815
    changedFields = serializers.ListField(child=serializers.CharField(), source="changed_fields")  # noqa: N815
    bidCount = serializers.SerializerMethodField()  # noqa: N815
    submittedAt = serializers.DateTimeField(source="submitted_at", allow_null=True)  # noqa: N815
    postedAt = serializers.DateTimeField(source="posted_at", allow_null=True)  # noqa: N815
    createdAt = serializers.DateTimeField(source="created_at")  # noqa: N815

    class Meta:
        model = Campaign
        fields = (
            "id",
            "title",
            "platform",
            "brand",
            "budgetMin",
            "budgetMax",
            "moderationStatus",
            "changedFields",
            "bidCount",
            "submittedAt",
            "postedAt",
            "createdAt",
        )

    def get_budgetMin(self, obj: Campaign) -> dict[str, Any] | None:  # noqa: N802
        return money(obj.budget_min_paise)

    def get_budgetMax(self, obj: Campaign) -> dict[str, Any] | None:  # noqa: N802
        return money(obj.budget_max_paise)

    def get_bidCount(self, obj: Campaign) -> int:  # noqa: N802
        count = getattr(obj, "bid_count", None)
        if count is None:
            count = obj.bids.exclude(status=BidStatus.WITHDRAWN).count()
        return count


class AdminCampaignDetailOutput(AdminCampaignListOutput):
    brand = AdminCampaignBrandOutput()
    signals = serializers.SerializerMethodField()
    bidsSummary = serializers.SerializerMethodField()  # noqa: N815
    moderation = serializers.SerializerMethodField()
    usageRights = serializers.JSONField(source="usage_rights")  # noqa: N815
    closesAt = serializers.DateTimeField(source="closes_at")  # noqa: N815

    class Meta(AdminCampaignListOutput.Meta):
        fields = AdminCampaignListOutput.Meta.fields + (
            "brief",
            "deliverables",
            "closesAt",
            "status",
            "usageRights",
            "signals",
            "bidsSummary",
            "moderation",
        )

    def get_signals(self, obj: Campaign) -> list[dict[str, Any]]:
        from apps.campaigns.signals_check import compute_campaign_signals

        return compute_campaign_signals(obj)

    def get_bidsSummary(self, obj: Campaign) -> dict[str, int]:  # noqa: N802
        bids = list(obj.bids.all())
        return {
            "total": len(bids),
            "pending": sum(1 for b in bids if b.status == BidStatus.PENDING),
            "held": sum(1 for b in bids if b.status == BidStatus.HELD),
            "selected": sum(1 for b in bids if b.status == BidStatus.SELECTED),
            "rejected": sum(1 for b in bids if b.status in (BidStatus.REJECTED, BidStatus.WITHDRAWN)),
        }

    def get_moderation(self, obj: Campaign) -> dict[str, Any]:
        mod_by = None
        if obj.moderated_by:
            mod_by = obj.moderated_by.email or str(obj.moderated_by.id)
        return {
            "status": obj.moderation_status,
            "reason": obj.moderation_reason or None,
            "ruleBroken": obj.rule_broken or None,
            "changedFields": list(obj.changed_fields or []),
            "moderatedAt": obj.moderated_at,
            "moderatedBy": mod_by,
        }


class AdminCampaignRejectInput(StrictSerializer):
    reason = serializers.CharField(min_length=4, max_length=500)
    note = serializers.CharField(required=False, allow_blank=True, max_length=1000, default="")
    ruleBroken = serializers.CharField(required=False, allow_blank=True, max_length=100, default="")  # noqa: N815


class AdminCampaignReasonInput(StrictSerializer):
    reason = serializers.CharField(min_length=4, max_length=500)


class AdminCampaignActionResult(serializers.Serializer):
    campaign = AdminCampaignDetailOutput()
    audit = AdminAuditOutput()
