from rest_framework import serializers

from apps.core.serializers import StrictSerializer
from apps.kyc.admin_serializers import AdminAuditOutput
from apps.moderation.models import (
    AbuseReport,
    AbuseTargetType,
    BidAlert,
)

ALLOWED_REPORT_TARGETS = (*AbuseTargetType.values, "profile", "chat")


class AbuseReportInput(StrictSerializer):
    targetType = serializers.ChoiceField(choices=ALLOWED_REPORT_TARGETS)  # noqa: N815
    targetId = serializers.UUIDField()  # noqa: N815
    reason = serializers.CharField(min_length=2, max_length=100)
    details = serializers.CharField(required=False, allow_blank=True, default="")


class AbuseReportOutput(serializers.ModelSerializer):
    targetType = serializers.CharField(source="target_type")  # noqa: N815
    targetId = serializers.UUIDField(source="target_id")  # noqa: N815
    actionTaken = serializers.CharField(source="action_taken", allow_blank=True)  # noqa: N815
    createdAt = serializers.DateTimeField(source="created_at")  # noqa: N815

    class Meta:
        model = AbuseReport
        fields = (
            "id",
            "targetType",
            "targetId",
            "reason",
            "details",
            "status",
            "actionTaken",
            "createdAt",
        )


class AbuseReportUserSummary(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.EmailField(allow_null=True)
    role = serializers.CharField()


class AdminAbuseReportDetailOutput(AbuseReportOutput):
    reporter = AbuseReportUserSummary()
    targetUser = AbuseReportUserSummary(source="target_user", allow_null=True)  # noqa: N815
    actionNotes = serializers.CharField(source="action_notes", allow_blank=True)  # noqa: N815
    resolvedAt = serializers.DateTimeField(source="resolved_at", allow_null=True)  # noqa: N815

    class Meta(AbuseReportOutput.Meta):
        fields = AbuseReportOutput.Meta.fields + (
            "reporter",
            "targetUser",
            "actionNotes",
            "resolvedAt",
        )


class AdminAbuseReasonInput(StrictSerializer):
    reason = serializers.CharField(min_length=4, max_length=500)


class AdminAbuseWarnInput(StrictSerializer):
    reason = serializers.CharField(min_length=4, max_length=500)
    warningMessage = serializers.CharField(min_length=4, max_length=1000)  # noqa: N815


class AdminAbuseActionResult(serializers.Serializer):
    report = AdminAbuseReportDetailOutput()
    audit = AdminAuditOutput()


class BidAlertOutput(serializers.ModelSerializer):
    campaignId = serializers.UUIDField(source="campaign_id")  # noqa: N815
    campaignTitle = serializers.CharField(source="campaign.title")  # noqa: N815
    alertType = serializers.CharField(source="alert_type")  # noqa: N815
    bidsCount = serializers.SerializerMethodField()  # noqa: N815
    createdAt = serializers.DateTimeField(source="created_at")  # noqa: N815

    class Meta:
        model = BidAlert
        fields = (
            "id",
            "campaignId",
            "campaignTitle",
            "alertType",
            "severity",
            "status",
            "details",
            "bidsCount",
            "createdAt",
        )

    def get_bidsCount(self, obj: BidAlert) -> int:  # noqa: N802
        return obj.bids.count()


class AdminBidAlertActionResult(serializers.Serializer):
    alert = BidAlertOutput()
    audit = AdminAuditOutput()
