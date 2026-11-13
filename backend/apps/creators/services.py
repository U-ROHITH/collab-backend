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


def _unique_slug(name: str) -> str:
    base = slugify(name)[:70] or "creator"
    return f"{base}-{secrets.token_hex(3)}"


def create_creator_for_user(*, user: User, display_name: str) -> Creator:
    return Creator.objects.create(user=user, display_name=display_name, slug=_unique_slug(display_name))


def create_catalogue_creator(*, actor: User, display_name: str, **fields: Any) -> Creator:
    """Admin-created profile for the initial catalogue (no user until claimed)."""
    creator = Creator.objects.create(display_name=display_name, slug=_unique_slug(display_name), **fields)
    AuditLog.objects.create(
        actor=actor, action="creator.catalogue_created", entity_type="creator", entity_id=str(creator.pk)
    )
    return creator


def update_creator_profile(*, creator: Creator, data: dict[str, Any]) -> Creator:
    data = dict(data)
    city = data.pop("city", None)
    region = data.pop("region", None)
    if city or region:
        parts = [p.strip() for p in (city, region) if p and p.strip()]
        data["location"] = ", ".join(parts)

    instagram = data.pop("instagram", None)
    youtube = data.pop("youtube", None)
    languages = data.pop("languages", None)

    social_links = dict(creator.social_media_links or {})
    if instagram is not None:
        social_links["instagram"] = instagram
    if youtube is not None:
        social_links["youtube"] = youtube
    if languages is not None:
        social_links["languages"] = languages
    creator.social_media_links = social_links

    changes = {_PROFILE_FIELDS[k]: v for k, v in data.items() if k in _PROFILE_FIELDS}
    for field, value in changes.items():
        setattr(creator, field, value)

    update_fields = list(changes.keys())
    if "social_media_links" not in update_fields:
        update_fields.append("social_media_links")
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
