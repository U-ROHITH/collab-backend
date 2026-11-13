from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel


class PaymentSession(BaseModel):
    brand = models.ForeignKey("brands.Brand", on_delete=models.CASCADE, related_name="payment_sessions")
    order = models.ForeignKey(
        "orders.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="payment_sessions"
    )
    gateway = models.CharField(max_length=32, default="razorpay")
    gateway_order_id = models.CharField(max_length=100, unique=True, db_index=True)
    amount_paise = models.BigIntegerField()
    currency = models.CharField(max_length=3, default="INR")
    status = models.CharField(max_length=24, default="created")  # created, paid, failed, expired
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(condition=Q(amount_paise__gt=0), name="payment_session_amount_positive"),
        ]

    def __str__(self) -> str:
        return f"PaymentSession({self.gateway_order_id}, {self.amount_paise} paise, {self.status})"


class PaymentReceipt(BaseModel):
    payment_session = models.ForeignKey(
        PaymentSession, null=True, blank=True, on_delete=models.SET_NULL, related_name="receipts"
    )
    order = models.ForeignKey("orders.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="receipts")
    brand = models.ForeignKey("brands.Brand", on_delete=models.PROTECT, related_name="receipts")
    gateway_payment_id = models.CharField(max_length=100, unique=True)
    gateway_signature = models.CharField(max_length=256, blank=True)
    amount_paise = models.BigIntegerField()
    currency = models.CharField(max_length=3, default="INR")
    status = models.CharField(max_length=24, default="succeeded")
    receipt_number = models.CharField(max_length=64, unique=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"PaymentReceipt({self.receipt_number}, {self.amount_paise} paise)"


class CreatorWallet(BaseModel):
    creator = models.OneToOneField("creators.Creator", on_delete=models.CASCADE, related_name="wallet")
    balance_paise = models.BigIntegerField(default=0)  # Available for withdrawal
    total_earned_paise = models.BigIntegerField(default=0)
    total_withdrawn_paise = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=3, default="INR")

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(balance_paise__gte=0), name="wallet_balance_non_negative"),
        ]

    def __str__(self) -> str:
        return f"CreatorWallet(creator={self.creator_id}, balance={self.balance_paise})"


class WithdrawalRequest(BaseModel):
    creator = models.ForeignKey("creators.Creator", on_delete=models.CASCADE, related_name="withdrawals")
    wallet = models.ForeignKey(CreatorWallet, on_delete=models.CASCADE, related_name="withdrawals")
    amount_paise = models.BigIntegerField()
    currency = models.CharField(max_length=3, default="INR")
    payout_method = models.CharField(max_length=16)  # "upi" | "bank_transfer"
    payout_details = models.JSONField(default=dict)
    status = models.CharField(max_length=24, default="pending")  # pending, processing, completed, rejected
    rejection_reason = models.CharField(max_length=255, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(condition=Q(amount_paise__gt=0), name="withdrawal_amount_positive"),
        ]

    def __str__(self) -> str:
        return f"WithdrawalRequest({self.id}, amount={self.amount_paise}, status={self.status})"


class WalletTransaction(BaseModel):
    wallet = models.ForeignKey(CreatorWallet, on_delete=models.CASCADE, related_name="transactions")
    order = models.ForeignKey(
        "orders.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="wallet_transactions"
    )
    withdrawal = models.ForeignKey(
        WithdrawalRequest, null=True, blank=True, on_delete=models.SET_NULL, related_name="wallet_transactions"
    )
    type = models.CharField(max_length=32)  # "credit_order", "debit_withdrawal", "credit_bonus", "debit_fee"
    amount_paise = models.BigIntegerField()
    balance_after_paise = models.BigIntegerField()
    description = models.CharField(max_length=255)

    class Meta:
        ordering = ("-created_at", "-id")

    def __str__(self) -> str:
        return f"WalletTransaction({self.type}, amount={self.amount_paise}, after={self.balance_after_paise})"
