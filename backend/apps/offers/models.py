from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel


class OfferStatus(models.TextChoices):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"


class Offer(BaseModel):
    """A brand's direct offer to one creator."""

    brand = models.ForeignKey("brands.Brand", on_delete=models.PROTECT, related_name="offers")
    creator = models.ForeignKey("creators.Creator", on_delete=models.PROTECT, related_name="offers")
    deliverable = models.CharField(max_length=200)
    budget_paise = models.BigIntegerField()
    deadline = models.DateTimeField()
    message = models.TextField(max_length=2000)
    usage_rights = models.JSONField(default=dict)
    status = models.CharField(max_length=12, choices=OfferStatus.choices, default=OfferStatus.PENDING)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(condition=Q(budget_paise__gt=0), name="offer_budget_positive"),
            models.CheckConstraint(condition=Q(status__in=OfferStatus.values), name="offer_status_valid"),
        ]
        indexes = [models.Index(fields=["creator", "status"]), models.Index(fields=["brand", "status"])]
