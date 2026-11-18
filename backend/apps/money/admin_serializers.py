
from rest_framework import serializers

from apps.money.models import WithdrawalRequest


class AdminPlatformRulesOutput(serializers.Serializer):
    commissionPct = serializers.FloatField()  # noqa: N815
    gatewayPct = serializers.FloatField()  # noqa: N815


class AdminFinanceMonthlyBreakdown(serializers.Serializer):
    month = serializers.CharField()
    gmvPaise = serializers.IntegerField()  # noqa: N815
    revenuePaise = serializers.IntegerField()  # noqa: N815
    gstPaise = serializers.IntegerField()  # noqa: N815
    tdsPaise = serializers.IntegerField()  # noqa: N815
    payoutsPaise = serializers.IntegerField()  # noqa: N815
    ordersCount = serializers.IntegerField()  # noqa: N815


class AdminFinanceSummaryOutput(serializers.Serializer):
    totalGmvPaise = serializers.IntegerField()  # noqa: N815
    totalRevenuePaise = serializers.IntegerField()  # noqa: N815
    totalGstPaise = serializers.IntegerField()  # noqa: N815
    totalTdsPaise = serializers.IntegerField()  # noqa: N815
    escrowHeldPaise = serializers.IntegerField()  # noqa: N815
    payoutsProcessedPaise = serializers.IntegerField()  # noqa: N815
    months = serializers.IntegerField()
    monthly = AdminFinanceMonthlyBreakdown(many=True)


class AdminPayoutOutput(serializers.ModelSerializer):
    creatorId = serializers.UUIDField(source="creator_id")  # noqa: N815
    creatorName = serializers.CharField(source="creator.display_name")  # noqa: N815
    creatorHandle = serializers.CharField(source="creator.slug")  # noqa: N815
    amountPaise = serializers.IntegerField(source="amount_paise")  # noqa: N815
    rejectionReason = serializers.CharField(source="rejection_reason", allow_blank=True)  # noqa: N815
    payoutMethod = serializers.SerializerMethodField()  # noqa: N815
    createdAt = serializers.DateTimeField(source="created_at")  # noqa: N815
    processedAt = serializers.DateTimeField(source="processed_at", allow_null=True)  # noqa: N815

    class Meta:
        model = WithdrawalRequest
        fields = (
            "id",
            "creatorId",
            "creatorName",
            "creatorHandle",
            "amountPaise",
            "currency",
            "payoutMethod",
            "status",
            "rejectionReason",
            "createdAt",
            "processedAt",
        )

    def get_payoutMethod(self, obj: WithdrawalRequest) -> str:  # noqa: N802
        details = obj.payout_details or {}
        method = obj.payout_method or "bank_transfer"
        if method == "upi":
            vpa = details.get("vpa", "")
            if "@" in vpa:
                handle, domain = vpa.split("@", 1)
                return f"upi: ***@{domain}"
            return "upi: ***"
        account = details.get("account_number", "")
        if account and len(account) >= 4:
            return f"bank: ***{account[-4:]}"
        return "bank: ***"
