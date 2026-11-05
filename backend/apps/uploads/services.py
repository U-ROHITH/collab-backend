"""Upload lifecycle: presign -> browser PUTs to storage -> complete (verify, sanitise, attach)."""

import logging
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction
from uuid6 import uuid7

from apps.core.exceptions import DomainError, NotFound, TooManyRequests
from apps.identity.models import Role, User
from apps.uploads import images, storage
from apps.uploads.models import EXTENSIONS, RULES, Upload, UploadKind, UploadStatus

logger = logging.getLogger(__name__)
SIGNED_DOWNLOAD_TTL = 300
MAX_PENDING_PER_HOUR = 60

_MAGIC: dict[str, tuple[int, bytes]] = {  # content type -> (offset, signature)
    "image/jpeg": (0, b"\xff\xd8\xff"),
    "image/png": (0, b"\x89PNG\r\n\x1a\n"),
    "image/webp": (8, b"WEBP"),
    "application/pdf": (0, b"%PDF-"),
    "video/mp4": (4, b"ftyp"),
    "video/quicktime": (4, b"ftyp"),
}


class UploadRejected(DomainError):
    code = "UPLOAD_REJECTED"
    message = "The file was rejected. Upload a supported file within the size limit."


class StorageUnavailable(DomainError):
    status_code = 503
    code = "STORAGE_UNAVAILABLE"
    message = "File storage is unavailable right now. Please try again."


def _private_bucket() -> str:
    return settings.STORAGE_PRIVATE_BUCKET


def _public_bucket() -> str:
    return settings.STORAGE_PUBLIC_BUCKET


def create_upload(*, user: User, kind: str, content_type: str, size: int) -> tuple[Upload, str]:
    rule = RULES[kind]
    if rule.role and user.role != rule.role:
        raise DomainError("This upload type isn't available for your account.", code="UPLOAD_KIND_NOT_ALLOWED")
    if user.role not in (Role.BRAND, Role.CREATOR):
        raise DomainError("Complete signup first.", code="SIGNUP_REQUIRED")
    if content_type not in rule.content_types:
        raise DomainError(f"Allowed types: {', '.join(rule.content_types)}.", code="UPLOAD_TYPE_NOT_ALLOWED")
    if not 0 < size <= rule.max_bytes:
        raise DomainError(f"Maximum size is {rule.max_bytes // (1024 * 1024)} MB.", code="UPLOAD_TOO_LARGE")

    from datetime import timedelta

    from django.utils import timezone

    recent = Upload.objects.filter(owner=user, created_at__gte=timezone.now() - timedelta(hours=1)).count()
    if recent >= MAX_PENDING_PER_HOUR:
        raise TooManyRequests("Too many uploads. Try again later.")
    if kind == UploadKind.GALLERY:
        from apps.creators.models import CreatorMedia

        if CreatorMedia.objects.filter(creator__user=user).count() >= settings.GALLERY_MAX_ITEMS:
            raise DomainError(f"Your gallery can hold up to {settings.GALLERY_MAX_ITEMS} images.", code="GALLERY_FULL")

    # The path is generated server-side; the client's filename is never used.
    upload_id = uuid7()
    staging_key = f"staging/{kind}/{user.pk}/{upload_id}.{EXTENSIONS[content_type]}"
    try:
        url = storage.create_signed_upload_url(_private_bucket(), staging_key)
    except storage.StorageError:
        raise StorageUnavailable() from None
    upload = Upload.objects.create(
        id=upload_id,
        owner=user,
        kind=kind,
        declared_content_type=content_type,
        declared_size=size,
        staging_key=staging_key,
    )
    return upload, url


def _reject(upload: Upload, reason: str) -> None:
    logger.info("Upload %s rejected: %s", upload.pk, reason)
    upload.status = UploadStatus.REJECTED
    upload.save(update_fields=["status", "updated_at"])
    try:
        storage.delete(_private_bucket(), [upload.staging_key])
    except storage.StorageError:
        logger.warning("Could not delete rejected upload %s", upload.pk)
    raise UploadRejected()


def complete_upload(*, user: User, upload_id: UUID) -> Upload:
    upload = Upload.objects.filter(pk=upload_id, owner=user).first()
    if upload is None:
        raise NotFound()
    if upload.status == UploadStatus.READY:
        return upload
    if upload.status == UploadStatus.REJECTED:
        raise UploadRejected()

    rule = RULES[upload.kind]
    try:
        meta = storage.head(_private_bucket(), upload.staging_key)
        if meta is None:
            raise DomainError("The file hasn't been uploaded yet.", code="UPLOAD_NOT_FOUND")
        if meta["size"] > rule.max_bytes or meta["content_type"] != upload.declared_content_type:
            _reject(upload, "size/type mismatch")

        if rule.public:
            original = storage.download(_private_bucket(), upload.staging_key, rule.max_bytes)
            try:
                webp = images.sanitise_to_webp(original)
            except images.InvalidImage:
                _reject(upload, "not a decodable image")
            final_key = f"{upload.kind}/{user.pk}/{upload.pk}.webp"
            storage.upload(_public_bucket(), final_key, webp, "image/webp")
            storage.delete(_private_bucket(), [upload.staging_key])
            upload.bucket, upload.storage_key = _public_bucket(), final_key
            upload.content_type, upload.size_bytes = "image/webp", len(webp)
            upload.public_url = storage.public_url(_public_bucket(), final_key)
        else:
            offset, signature = _MAGIC[upload.declared_content_type]
            head_bytes = storage.read_prefix(_private_bucket(), upload.staging_key, 64)
            if head_bytes[offset : offset + len(signature)] != signature:
                _reject(upload, "magic bytes mismatch")
            upload.bucket, upload.storage_key = _private_bucket(), upload.staging_key
            upload.content_type, upload.size_bytes = upload.declared_content_type, int(meta["size"])
    except storage.StorageError:
        raise StorageUnavailable() from None

    upload.status = UploadStatus.READY
    upload.save()
    _attach_profile_image(user, upload)
    return upload


def _attach_profile_image(user: User, upload: Upload) -> None:
    """Public image kinds attach themselves as soon as they're verified."""
    from apps.brands.models import Brand
    from apps.creators.models import Creator, CreatorMedia

    with transaction.atomic():
        if upload.kind == UploadKind.AVATAR:
            Creator.objects.filter(user=user).update(avatar_url=upload.public_url)
        elif upload.kind == UploadKind.BRAND_LOGO:
            Brand.objects.filter(owner=user).update(logo_url=upload.public_url)
        elif upload.kind == UploadKind.GALLERY:
            creator = Creator.objects.select_for_update().get(user=user)
            if creator.media.count() >= settings.GALLERY_MAX_ITEMS:
                raise DomainError(
                    f"Your gallery can hold up to {settings.GALLERY_MAX_ITEMS} images.", code="GALLERY_FULL"
                )
            CreatorMedia.objects.get_or_create(
                creator=creator, upload=upload, defaults={"url": upload.public_url, "sort": creator.media.count()}
            )


def claim_ready_upload(*, user: User, upload_id: UUID, kind: str) -> Upload:
    """For private kinds: verify ownership/kind/readiness before attaching to a deliverable or message."""
    upload = Upload.objects.filter(pk=upload_id, owner=user, kind=kind, status=UploadStatus.READY).first()
    if upload is None:
        raise DomainError("Attachment not found or not finished uploading.", code="ATTACHMENT_INVALID")
    return upload


def can_access(user: User, upload: Upload) -> bool:
    if upload.owner_id == user.pk:
        return True
    from apps.messaging.selectors import conversations_for
    from apps.orders.models import Deliverable
    from apps.orders.selectors import orders_visible_to

    if upload.kind == UploadKind.DELIVERABLE:
        return Deliverable.objects.filter(upload=upload, order__in=orders_visible_to(user)).exists()
    if upload.kind == UploadKind.CHAT_ATTACHMENT:
        from apps.messaging.models import Message

        return Message.objects.filter(attachment=upload, conversation__in=conversations_for(user)).exists()
    return bool(upload.public_url)


def download_url(*, user: User, upload_id: UUID) -> dict[str, Any]:
    upload = Upload.objects.filter(pk=upload_id, status=UploadStatus.READY).first()
    if upload is None or not can_access(user, upload):
        raise NotFound()
    if upload.public_url:
        return {"url": upload.public_url, "expiresIn": None}
    try:
        url = storage.signed_download_url(upload.bucket, upload.storage_key, SIGNED_DOWNLOAD_TTL)
    except storage.StorageError:
        raise StorageUnavailable() from None
    return {"url": url, "expiresIn": SIGNED_DOWNLOAD_TTL}


def delete_gallery_item(*, user: User, media_id: UUID) -> None:
    from apps.creators.models import CreatorMedia

    item = CreatorMedia.objects.select_related("upload").filter(pk=media_id, creator__user=user).first()
    if item is None:
        raise NotFound()
    upload = item.upload
    item.delete()
    try:
        storage.delete(upload.bucket, [upload.storage_key])
    except storage.StorageError:
        logger.warning("Could not delete gallery object for upload %s", upload.pk)
