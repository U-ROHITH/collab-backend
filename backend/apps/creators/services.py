import secrets
from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import F
from django.utils.text import slugify

from apps.core.exceptions import NotFound
from apps.creators.models import ApprovalStatus, Creator, CreatorPackage
from apps.identity.models import AuditLog, User

_PROFILE_FIELDS = {
    "displayName": "display_name",
    "bio": "bio",
    "categories": "categories",
    "location": "location",
    "primaryPlatform": "primary_platform",
    "repliesWithin": "replies_within",
    "dob": "dob",
    "gender": "gender",
    "avatarUrl": "avatar_url",
    "avatar": "avatar_url",
}
_PACKAGE_FIELDS = {
    "title": "title",
    "description": "description",
    "platform": "platform",
    "deliverables": "deliverables",
    "price": "price_paise",
    "deliveryDays": "delivery_days",
    "active": "active",
}
_SENTINEL = object()


def _unique_slug(name: str) -> str:
    base = slugify(name)[:70] or "creator"
    return f"{base}-{secrets.token_hex(3)}"


def create_creator_for_user(*, user: User, display_name: str) -> Creator:
    return Creator.objects.create(user=user, display_name=display_name, slug=_unique_slug(display_name))


def _merge_platforms(creator: Creator, social_links: dict[str, Any], platforms: dict[str, Any]) -> dict[str, Any]:
    """Merge per platform: given fields overwrite, others are kept; None removes the platform (handle too)."""
    stats = {k: dict(v) for k, v in (creator.platform_stats or {}).items() if isinstance(v, dict)}
    for platform, entry in platforms.items():
        if entry is None:
            stats.pop(platform, None)
            social_links[platform] = ""
            continue
        entry = dict(entry)
        if "handle" in entry:
            social_links[platform] = entry.pop("handle")
        stats[platform] = stats.get(platform, {}) | entry
    return stats


def update_creator_profile(*, creator: Creator, data: dict[str, Any]) -> Creator:
    data = dict(data)
    avatar_upload_id = data.pop("avatarUploadId", None)
    if avatar_upload_id:
        from apps.uploads.models import Upload

        upload = Upload.objects.filter(pk=avatar_upload_id, owner=creator.user).first()
        if upload and upload.public_url:
            data["avatarUrl"] = upload.public_url

    city = data.pop("city", None)
    region = data.pop("region", None)
    if city is not None or region is not None:
        # Changing one half keeps the other; sending "" clears that half.
        old_city, _, old_region = (creator.location or "").partition(",")
        new_city = (city if city is not None else old_city).strip()
        new_region = (region if region is not None else old_region).strip()
        location = ", ".join(p for p in (new_city, new_region) if p)
        if len(location) > 80:
            from rest_framework.exceptions import ValidationError

            raise ValidationError({"location": ["City and region together must be under 80 characters."]})
        data["location"] = location

    instagram = data.pop("instagram", _SENTINEL)
    youtube = data.pop("youtube", _SENTINEL)
    languages = data.pop("languages", _SENTINEL)
    platforms = data.pop("platforms", _SENTINEL)
    explicit_follower_count = data.pop("followerCount", _SENTINEL)

    social_links = dict(creator.social_media_links or {})
    if languages is not _SENTINEL:
        social_links["languages"] = languages

    incoming_platforms: dict[str, Any] = {}
    if instagram is not _SENTINEL:
        incoming_platforms["instagram"] = instagram
    if youtube is not _SENTINEL:
        incoming_platforms["youtube"] = youtube
    if platforms is not _SENTINEL and isinstance(platforms, dict):
        for p, p_data in platforms.items():
            if p in incoming_platforms and incoming_platforms[p] is not None and p_data is not None:
                incoming_platforms[p] = {**incoming_platforms[p], **p_data}
            else:
                incoming_platforms[p] = p_data

    stats_changed = False
    if incoming_platforms:
        creator.platform_stats = _merge_platforms(creator, social_links, incoming_platforms)
        stats_changed = True

    creator.social_media_links = social_links

    # Sync follower_count with platform stats or explicit followerCount
    follower_count_changed = False
    primary = data.get("primaryPlatform") or creator.primary_platform
    derived_follower_count = None
    if isinstance(creator.platform_stats, dict):
        primary_stats = creator.platform_stats.get(primary)
        if isinstance(primary_stats, dict) and primary_stats.get("followersCount") is not None:
            derived_follower_count = primary_stats["followersCount"]
        else:
            for p in ("instagram", "youtube"):
                st = creator.platform_stats.get(p)
                if isinstance(st, dict) and st.get("followersCount") is not None:
                    derived_follower_count = st["followersCount"]
                    break

    if derived_follower_count is not None:
        if creator.follower_count != derived_follower_count:
            creator.follower_count = derived_follower_count
            follower_count_changed = True
    elif explicit_follower_count is not _SENTINEL:
        if creator.follower_count != explicit_follower_count:
            creator.follower_count = explicit_follower_count
            follower_count_changed = True
    elif stats_changed and creator.follower_count is not None:
        # The stats it came from were removed: don't keep showing (and filtering on) the old number.
        creator.follower_count = None
        follower_count_changed = True

    changes = {_PROFILE_FIELDS[k]: v for k, v in data.items() if k in _PROFILE_FIELDS}
    for field, value in changes.items():
        setattr(creator, field, value)

    update_fields = list(changes.keys())
    if "social_media_links" not in update_fields:
        update_fields.append("social_media_links")
    if stats_changed and "platform_stats" not in update_fields:
        update_fields.append("platform_stats")
    if follower_count_changed and "follower_count" not in update_fields:
        update_fields.append("follower_count")
    update_fields.append("updated_at")

    creator.save(update_fields=update_fields)
    return creator


def set_approval(*, actor: User, creator_id: UUID, status: str) -> Creator:
    with transaction.atomic():
        creator = Creator.objects.select_for_update().get(pk=creator_id)
        creator.approval_status = status
        creator.save(update_fields=["approval_status", "updated_at"])
        AuditLog.objects.create(
            actor=actor, action=f"creator.{status}", entity_type="creator", entity_id=str(creator.pk)
        )
    if creator.user_id:
        from apps.notifications.services import notify

        notify(
            user_id=creator.user_id,
            type="creator_" + status,
            title="Your profile was approved" if status == ApprovalStatus.APPROVED else "Profile review update",
            data={"creatorId": str(creator.pk)},
        )
    return creator


def create_package(*, creator: Creator, data: dict[str, Any]) -> CreatorPackage:
    return CreatorPackage.objects.create(creator=creator, **{_PACKAGE_FIELDS[k]: v for k, v in data.items()})


def update_package(*, creator: Creator, package_id: UUID, data: dict[str, Any]) -> CreatorPackage:
    package = CreatorPackage.objects.filter(pk=package_id, creator=creator).first()
    if package is None:
        raise NotFound()
    changes = {_PACKAGE_FIELDS[k]: v for k, v in data.items()}
    for field, value in changes.items():
        setattr(package, field, value)
    package.save(update_fields=[*changes.keys(), "updated_at"])
    return package


def deactivate_package(*, creator: Creator, package_id: UUID) -> None:
    # Packages are referenced by orders, so they are deactivated, never deleted.
    updated = CreatorPackage.objects.filter(pk=package_id, creator=creator).update(active=False)
    if not updated:
        raise NotFound()


def apply_review(*, creator_id: UUID, stars: int) -> None:
    Creator.objects.filter(pk=creator_id).update(rating_sum=F("rating_sum") + stars, rating_count=F("rating_count") + 1)
