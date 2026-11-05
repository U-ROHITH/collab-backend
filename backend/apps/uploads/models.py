from dataclasses import dataclass

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel

MB = 1024 * 1024
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")


class UploadKind(models.TextChoices):
    AVATAR = "avatar"  # creator profile photo  -> public, re-encoded
    BRAND_LOGO = "brand_logo"  # brand logo         -> public, re-encoded
    GALLERY = "gallery"  # creator portfolio image  -> public, re-encoded
    DELIVERABLE = "deliverable"  # draft files       -> private
    CHAT_ATTACHMENT = "chat_attachment"  # message files -> private


@dataclass(frozen=True)
class KindRule:
    content_types: tuple[str, ...]
    max_bytes: int
    public: bool
    role: str | None  # who may upload this kind (None = brand or creator)


RULES: dict[str, KindRule] = {
    UploadKind.AVATAR: KindRule(IMAGE_TYPES, 10 * MB, public=True, role="creator"),
    UploadKind.BRAND_LOGO: KindRule(IMAGE_TYPES, 10 * MB, public=True, role="brand"),
    UploadKind.GALLERY: KindRule(IMAGE_TYPES, 10 * MB, public=True, role="creator"),
    UploadKind.DELIVERABLE: KindRule(
        (*IMAGE_TYPES, "application/pdf", "video/mp4", "video/quicktime"), 50 * MB, public=False, role="creator"
    ),
    UploadKind.CHAT_ATTACHMENT: KindRule((*IMAGE_TYPES, "application/pdf"), 10 * MB, public=False, role=None),
}

EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
    "video/mp4": "mp4",
    "video/quicktime": "mov",
}


class UploadStatus(models.TextChoices):
    PENDING = "pending"  # signed URL issued, file not verified yet
    READY = "ready"  # verified (and re-encoded if an image)
    REJECTED = "rejected"


class Upload(BaseModel):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="uploads")
    kind = models.CharField(max_length=20, choices=UploadKind.choices)
    status = models.CharField(max_length=10, choices=UploadStatus.choices, default=UploadStatus.PENDING)
    declared_content_type = models.CharField(max_length=60)
    declared_size = models.PositiveIntegerField()
    staging_key = models.CharField(max_length=300, unique=True)  # in the private bucket
    bucket = models.CharField(max_length=40, blank=True)  # final location (set when READY)
    storage_key = models.CharField(max_length=300, blank=True)
    content_type = models.CharField(max_length=60, blank=True)
    size_bytes = models.PositiveIntegerField(null=True, blank=True)
    public_url = models.URLField(max_length=600, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(kind__in=UploadKind.values), name="upload_kind_valid"),
            models.CheckConstraint(condition=Q(status__in=UploadStatus.values), name="upload_status_valid"),
        ]
        indexes = [models.Index(fields=["owner", "status", "created_at"])]
