from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.core.models import BaseModel


class OrderStatus(models.TextChoices):
    AWAITING_PAYMENT = "awaiting_payment"
    FUNDED = "funded"
    DRAFT_SUBMITTED = "draft_submitted"
    REVISION_REQUESTED = "revision_requested"
    APPROVED = "approved"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REFUND_PENDING = "refund_pending"
    REFUNDED = "refunded"
    DISPUTED = "disputed"


class SourceType(models.TextChoices):
    BID = "bid"
    OFFER = "offer"
    PACKAGE = "package"


class Order(BaseModel):
    brand = models.ForeignKey("brands.Brand", on_delete=models.PROTECT, related_name="orders")
    creator = models.ForeignKey("creators.Creator", on_delete=models.PROTECT, related_name="orders")
    source_type = models.CharField(max_length=10, choices=SourceType.choices)
    source_id = models.UUIDField()
    # status is ONLY ever assigned inside apps/orders/state_machine.py
    status = models.CharField(max_length=24, choices=OrderStatus.choices, default=OrderStatus.AWAITING_PAYMENT)
    version = models.PositiveIntegerField(default=1)

    title = models.CharField(max_length=200)
    deliverables = models.CharField(max_length=200)
    brief = models.TextField(max_length=4000, blank=True)
    usage_rights = models.JSONField(default=dict)
    delivery_days = models.PositiveSmallIntegerField()
    deadline = models.DateTimeField(null=True, blank=True)
    revision_count = models.PositiveSmallIntegerField(default=0)
    max_revisions = models.PositiveSmallIntegerField(default=2)

    # Money snapshot (integer paise). Fee config is frozen at creation time.
    fee_config_snapshot = models.JSONField()
    subtotal_paise = models.BigIntegerField()
    platform_fee_paise = models.BigIntegerField()
    gst_paise = models.BigIntegerField()
    tds_paise = models.BigIntegerField()
    creator_payout_paise = models.BigIntegerField()
    brand_total_paise = models.BigIntegerField()

    funded_at = models.DateTimeField(null=True, blank=True)
    last_submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(fields=["source_type", "source_id"], name="uniq_order_per_source"),
            models.CheckConstraint(condition=Q(status__in=OrderStatus.values), name="order_status_valid"),
            models.CheckConstraint(condition=Q(subtotal_paise__gt=0), name="order_subtotal_positive"),
            models.CheckConstraint(condition=Q(creator_payout_paise__gt=0), name="order_payout_positive"),
            models.CheckConstraint(
                condition=Q(
                    brand_total_paise=F("creator_payout_paise")
                    + F("platform_fee_paise")
                    + F("gst_paise")
                    + F("tds_paise")
                ),
                name="order_amounts_balance",
            ),
        ]
        indexes = [
            models.Index(fields=["brand", "status"]),
            models.Index(fields=["creator", "status"]),
            models.Index(fields=["status", "updated_at"]),
        ]


class OrderEvent(BaseModel):
    """Append-only audit trail of every state transition."""

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="events")
    event = models.CharField(max_length=32)
    from_status = models.CharField(max_length=24)
    to_status = models.CharField(max_length=24)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    actor_role = models.CharField(max_length=16)
    reason = models.TextField(max_length=2000, blank=True)

    class Meta:
        ordering = ("created_at",)


class Deliverable(BaseModel):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="deliverable_versions")
    version = models.PositiveSmallIntegerField()
    content_url = models.URLField(max_length=1000, blank=True)  # external link, or…
    upload = models.OneToOneField(  # …a file uploaded to our private storage
        "uploads.Upload", null=True, blank=True, on_delete=models.PROTECT, related_name="deliverable"
    )
    note = models.TextField(max_length=2000, blank=True)
    feedback = models.TextField(max_length=4000, blank=True)  # brand's revision feedback on this version

    class Meta:
        ordering = ("version",)
        constraints = [models.UniqueConstraint(fields=["order", "version"], name="uniq_deliverable_version")]


class DisputeStatus(models.TextChoices):
    OPEN = "open"
    RESOLVED = "resolved"


class Dispute(BaseModel):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="disputes")
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    reason = models.TextField(max_length=4000)
    status = models.CharField(max_length=10, choices=DisputeStatus.choices, default=DisputeStatus.OPEN)
    resolution = models.TextField(max_length=4000, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["order"], condition=Q(status="open"), name="one_open_dispute_per_order")
        ]


class Review(BaseModel):
    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="review")
    brand = models.ForeignKey("brands.Brand", on_delete=models.PROTECT, related_name="+")
    creator = models.ForeignKey("creators.Creator", on_delete=models.PROTECT, related_name="reviews")
    stars = models.PositiveSmallIntegerField()
    text = models.TextField(max_length=2000, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [models.CheckConstraint(condition=Q(stars__gte=1, stars__lte=5), name="review_stars_range")]
