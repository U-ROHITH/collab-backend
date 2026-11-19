from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel


class PitchStatus(models.TextChoices):
    SENT = "sent", "Sent"
    EDITED = "edited", "Edited"
    DECLINED = "declined", "Declined"


class Pitch(BaseModel):
    creator = models.ForeignKey("creators.Creator", on_delete=models.PROTECT, related_name="pitches")
    brand = models.ForeignKey("brands.Brand", on_delete=models.PROTECT, related_name="pitches")
    campaign = models.ForeignKey(
        "campaigns.Campaign",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pitches",
    )
    message = models.TextField()
    links = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=16, choices=PitchStatus.choices, default=PitchStatus.SENT)
    edit_count = models.PositiveSmallIntegerField(default=0)
    edited_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    declined_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["creator", "brand"],
                condition=Q(status__in=["sent", "edited"]),
                name="uniq_active_pitch_creator_brand",
            ),
            models.CheckConstraint(condition=Q(status__in=PitchStatus.values), name="pitch_status_valid"),
        ]
        indexes = [
            models.Index(fields=["brand", "status", "-created_at"]),
            models.Index(fields=["creator", "-created_at"]),
        ]
