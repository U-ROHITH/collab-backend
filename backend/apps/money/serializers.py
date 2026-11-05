from typing import Any

from rest_framework import serializers

from apps.core.serializers import MoneyField, StrictSerializer
from apps.creators.serializers import money
from apps.money.models import PaymentReceipt, WalletTransaction, WithdrawalRequest


class PaymentCheckoutInput(StrictSerializer):
    orderId = serializers.UUIDField(required=False)  # noqa: N815
    packageId = serializers.UUIDField(required=False)  # noqa: N815
    cart = serializers.BooleanField(required=False, default=False)
    currency = serializers.CharField(max_length=3, required=False, default="INR")


class CheckoutSessionOutput(serializers.Serializer):
    sessionId = serializers.UUIDField()  # noqa: N815
    gateway = serializers.CharField()
    orderId = serializers.CharField()  # noqa: N815
    amount = serializers.DictField()
    amountPaise = serializers.IntegerField()  # noqa: N815
    currency = serializers.CharField()
    key = serializers.CharField()
    brand = serializers.DictField()
    orders = serializers.ListField(child=serializers.CharField())


class PaymentVerifyInput(StrictSerializer):
    orderId = serializers.CharField(required=False)  # noqa: N815
    paymentId = serializers.CharField(required=False)  # noqa: N815
    signature = serializers.CharField(required=False, allow_blank=True)
    razorpayOrderId = serializers.CharField(required=False)  # noqa: N815
    razorpayPaymentId = serializers.CharField(required=False)  # noqa: N815
    razorpaySignature = serializers.CharField(required=False, allow_blank=True)  # noqa: N815
    event = serializers.CharField(required=False)
    payload = serializers.JSONField(required=False)


class PaymentReceiptOutput(serializers.ModelSerializer):
    receiptId = serializers.UUIDField(source="id")  # noqa: N815
    receiptNumber = serializers.CharField(source="receipt_number")  # noqa: N815
    orderId = serializers.UUIDField(source="order_id")  # noqa: N815
    amount = serializers.SerializerMethodField()
    paymentId = serializers.CharField(source="gateway_payment_id")  # noqa: N815
    paidAt = serializers.DateTimeField(source="created_at")  # noqa: N815

    class Meta:
        model = PaymentReceipt
        fields = (
            "receiptId",
            "receiptNumber",
            "orderId",
            "amount",
            "status",
            "paymentId",
            "paidAt",
        )

    def get_amount(self, obj: PaymentReceipt) -> dict[str, Any] | None:
        return money(obj.amount_paise)


class WalletTransactionOutput(serializers.ModelSerializer):
    amount = serializers.SerializerMethodField()
    balanceAfter = serializers.SerializerMethodField()  # noqa: N815
    createdAt = serializers.DateTimeField(source="created_at")  # noqa: N815

    class Meta:
        model = WalletTransaction
        fields = ("id", "type", "amount", "balanceAfter", "description", "createdAt")

    def get_amount(self, obj: WalletTransaction) -> dict[str, Any] | None:
        return money(obj.amount_paise)

    def get_balanceAfter(self, obj: WalletTransaction) -> dict[str, Any] | None:  # noqa: N802
        return money(obj.balance_after_paise)


class CreatorWalletOutput(serializers.Serializer):
    balance = serializers.SerializerMethodField()
    availableBalance = serializers.SerializerMethodField()  # noqa: N815
    pendingBalance = serializers.SerializerMethodField()  # noqa: N815
    totalEarned = serializers.SerializerMethodField()  # noqa: N815
    totalWithdrawn = serializers.SerializerMethodField()  # noqa: N815
    transactions = WalletTransactionOutput(many=True)

    def get_balance(self, obj: dict[str, Any]) -> dict[str, Any] | None:
        return money(obj["wallet"].balance_paise)

    def get_availableBalance(self, obj: dict[str, Any]) -> dict[str, Any] | None:  # noqa: N802
        return money(obj["wallet"].balance_paise)

    def get_pendingBalance(self, obj: dict[str, Any]) -> dict[str, Any] | None:  # noqa: N802
        return money(obj["pending_paise"])

    def get_totalEarned(self, obj: dict[str, Any]) -> dict[str, Any] | None:  # noqa: N802
        return money(obj["wallet"].total_earned_paise)

    def get_totalWithdrawn(self, obj: dict[str, Any]) -> dict[str, Any] | None:  # noqa: N802
        return money(obj["wallet"].total_withdrawn_paise)


class WithdrawalInput(StrictSerializer):
    amount = MoneyField(required=False)
    amountPaise = serializers.IntegerField(min_value=1, required=False)  # noqa: N815
    payoutMethod = serializers.ChoiceField(choices=["upi", "bank_transfer"])  # noqa: N815
    payoutDetails = serializers.JSONField()  # noqa: N815


class WithdrawalOutput(serializers.ModelSerializer):
    amount = serializers.SerializerMethodField()
    payoutMethod = serializers.CharField(source="payout_method")  # noqa: N815
    payoutDetails = serializers.JSONField(source="payout_details")  # noqa: N815
    createdAt = serializers.DateTimeField(source="created_at")  # noqa: N815

    class Meta:
        model = WithdrawalRequest
        fields = ("id", "amount", "payoutMethod", "payoutDetails", "status", "createdAt")

    def get_amount(self, obj: WithdrawalRequest) -> dict[str, Any] | None:
        return money(obj.amount_paise)
