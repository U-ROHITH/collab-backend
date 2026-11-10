from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models import F, Q

from apps.core.models import BaseModel
from apps.creators.models import Platform


class CampaignStatus(models.TextChoices):
    DRAFT = "draft"
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class Campaign(BaseModel):
    brand = models.ForeignKey("brands.Brand", on_delete=models.PROTECT, related_name="campaigns")
    title = models.CharField(max_length=200)
    brief = models.TextField(max_length=5000)
    platform = models.CharField(max_length=16, choices=Platform.choices)
    deliverables = models.CharField(max_length=200)
    budget_min_paise = models.BigIntegerField()
    budget_max_paise = models.BigIntegerField()
    closes_at = models.DateTimeField()
    status = models.CharField(max_length=12, choices=CampaignStatus.choices, default=CampaignStatus.OPEN)
    usage_rights = models.JSONField(default=dict)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(condition=Q(budget_min_paise__gt=0), name="campaign_budget_positive"),
            models.CheckConstraint(
                condition=Q(budget_min_paise__lte=F("budget_max_paise")), name="campaign_budget_range"
            ),
            models.CheckConstraint(condition=Q(status__in=CampaignStatus.values), name="campaign_status_valid"),
        ]
        indexes = [
            models.Index(fields=["status", "closes_at"]),
            GinIndex(
                fields=["title", "brief"],
                name="campaign_search_trgm_idx",
                opclasses=["gin_trgm_ops", "gin_trgm_ops"],
            ),
        ]


class BidStatus(models.TextChoices):
    PENDING = "pending"
    SELECTED = "selected"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class Bid(BaseModel):
    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT, related_name="bids")
    creator = models.ForeignKey("creators.Creator", on_delete=models.PROTECT, related_name="bids")
    price_paise = models.BigIntegerField()
    message = models.TextField(max_length=2000)
    expected_views = models.PositiveIntegerField(null=True, blank=True)
    delivery_days = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=12, choices=BidStatus.choices, default=BidStatus.PENDING)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(fields=["campaign", "creator"], name="one_bid_per_creator_per_campaign"),
            models.CheckConstraint(condition=Q(price_paise__gt=0), name="bid_price_positive"),
            models.CheckConstraint(condition=Q(status__in=BidStatus.values), name="bid_status_valid"),
        ]
