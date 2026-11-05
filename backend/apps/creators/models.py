from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel


class Platform(models.TextChoices):
    INSTAGRAM = "instagram", "Instagram"
    YOUTUBE = "youtube", "YouTube"
    OTHER = "other", "Other"


class ApprovalStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class RepliesWithin(models.TextChoices):
    H1 = "1h", "1 hour"
    H6 = "6h", "6 hours"
    H24 = "24h", "24 hours"
    D3 = "3d", "3 days"


class Creator(BaseModel):
    # user is NULL for admin-created catalogue profiles until claimed.
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="creator"
    )
    slug = models.SlugField(max_length=90, unique=True)
    display_name = models.CharField(max_length=80)
    bio = models.TextField(max_length=2000, blank=True)
    categories = ArrayField(models.CharField(max_length=40), default=list, blank=True, size=5)
    location = models.CharField(max_length=80, blank=True)
    primary_platform = models.CharField(max_length=16, choices=Platform.choices, default=Platform.INSTAGRAM)
    replies_within = models.CharField(max_length=4, choices=RepliesWithin.choices, default=RepliesWithin.H24)
    approval_status = models.CharField(
        max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.PENDING, db_index=True
    )
    verified = models.BooleanField(default=False)  # set only from verified social data
    follower_count = models.PositiveIntegerField(null=True, blank=True)  # from social sync, not self-reported
    avatar_url = models.URLField(max_length=500, blank=True)
    rating_sum = models.PositiveIntegerField(default=0)
    rating_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=Q(approval_status__in=ApprovalStatus.values), name="creator_approval_valid"
            ),
            models.CheckConstraint(condition=Q(primary_platform__in=Platform.values), name="creator_platform_valid"),
        ]
        indexes = [models.Index(fields=["approval_status", "created_at"])]

    @property
    def is_approved(self) -> bool:
        return self.approval_status == ApprovalStatus.APPROVED

    @property
    def rating(self) -> float | None:
        return round(self.rating_sum / self.rating_count, 2) if self.rating_count else None

    def __str__(self) -> str:
        return self.display_name


class CreatorPackage(BaseModel):
    creator = models.ForeignKey(Creator, on_delete=models.PROTECT, related_name="packages")
    title = models.CharField(max_length=120)
    description = models.TextField(max_length=2000, blank=True)
    platform = models.CharField(max_length=16, choices=Platform.choices)
    deliverables = models.CharField(max_length=200)
    price_paise = models.BigIntegerField()
    delivery_days = models.PositiveSmallIntegerField()
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("price_paise", "id")
        constraints = [
            models.CheckConstraint(condition=Q(price_paise__gt=0), name="package_price_positive"),
            models.CheckConstraint(
                condition=Q(delivery_days__gte=1, delivery_days__lte=90), name="package_delivery_days_range"
            ),
        ]
        indexes = [models.Index(fields=["creator", "active"])]

    def __str__(self) -> str:
        return f"{self.creator} — {self.title}"


class CreatorMedia(BaseModel):
    """Portfolio gallery image (public, re-encoded WebP)."""

    creator = models.ForeignKey(Creator, on_delete=models.CASCADE, related_name="media")
    upload = models.OneToOneField("uploads.Upload", on_delete=models.CASCADE, related_name="gallery_item")
    url = models.URLField(max_length=600)
    sort = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort", "created_at")
