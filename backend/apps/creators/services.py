import secrets
from datetime import date
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
    "name": "display_name",
    "bio": "bio",
    "categories": "categories",
    "location": "location",
    "primaryPlatform": "primary_platform",
    "repliesWithin": "replies_within",
    "responseTimeHours": "response_time_hours",
    "response_time_hours": "response_time_hours",
    "profilePicture": "profile_picture",
    "profile_picture": "profile_picture",
    "avatar": "profile_picture",
    "avatarUrl": "avatar_url",
    "avatar_url": "avatar_url",
    "socialMediaUrls": "social_media_links",
    "social_media_urls": "social_media_links",
    "socialLinks": "social_media_links",
    "social_links": "social_media_links",
    "socialMediaLinks": "social_media_links",
    "socialAccounts": "social_accounts",
    "social_accounts": "social_accounts",
    "dob": "dob",
    "date_of_birth": "dob",
    "age": "age",
    "gender": "gender",
    "genderDescription": "gender_description",
    "gender_description": "gender_description",
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


def create_creator_for_user(*, user: User, display_name: str, **extra: Any) -> Creator:
    fields = {}
    for k, v in extra.items():
        if k in _PROFILE_FIELDS:
            fields[_PROFILE_FIELDS[k]] = v
    if "dob" in fields and fields["dob"] and "age" not in fields:
        today = date.today()
        d = fields["dob"]
        fields["age"] = today.year - d.year - ((today.month, today.day) < (d.month, d.day))
    creator = Creator.objects.create(user=user, display_name=display_name, slug=_unique_slug(display_name), **fields)
    if "profile_picture" in fields and bool(creator.profile_picture):
        creator.avatar_url = creator.profile_picture.url
        creator.save(update_fields=["avatar_url", "updated_at"])
    return creator


def create_catalogue_creator(*, actor: User, display_name: str, **fields: Any) -> Creator:
    """Admin-created profile for the initial catalogue (no user until claimed)."""
    creator = Creator.objects.create(display_name=display_name, slug=_unique_slug(display_name), **fields)
    AuditLog.objects.create(
        actor=actor, action="creator.catalogue_created", entity_type="creator", entity_id=str(creator.pk)
    )
    return creator


def update_creator_profile(*, creator: Creator, data: dict[str, Any]) -> Creator:
    changes = {}
    for k, v in data.items():
        if k in _PROFILE_FIELDS:
            model_attr = _PROFILE_FIELDS[k]
            changes[model_attr] = v
            setattr(creator, model_attr, v)
    if not changes:
        return creator

    fields_to_update = set(changes.keys())
    if "dob" in changes:
        if changes["dob"]:
            today = date.today()
            d = changes["dob"]
            calculated_age = today.year - d.year - ((today.month, today.day) < (d.month, d.day))
            if "age" not in changes:
                creator.age = calculated_age
                fields_to_update.add("age")
        elif "age" not in changes:
            creator.age = None
            fields_to_update.add("age")

    creator.save(update_fields=[*fields_to_update, "updated_at"])

    if "profile_picture" in changes and bool(creator.profile_picture):
        creator.avatar_url = creator.profile_picture.url
        creator.save(update_fields=["avatar_url", "updated_at"])

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


def sync_creator_starting_price(creator_id: UUID | Any) -> int | None:
    cheapest = (
        CreatorPackage.objects.filter(creator_id=creator_id, active=True)
        .order_by("price_paise")
        .values_list("price_paise", flat=True)
        .first()
    )
    Creator.objects.filter(pk=creator_id).update(starting_price_paise=cheapest)
    return cheapest


def create_package(*, creator: Creator, data: dict[str, Any]) -> CreatorPackage:
    pkg = CreatorPackage.objects.create(creator=creator, **{_PACKAGE_FIELDS[k]: v for k, v in data.items()})
    sync_creator_starting_price(creator.pk)
    return pkg


def update_package(*, creator: Creator, package_id: UUID, data: dict[str, Any]) -> CreatorPackage:
    package = CreatorPackage.objects.filter(pk=package_id, creator=creator).first()
    if package is None:
        raise NotFound()
    changes = {_PACKAGE_FIELDS[k]: v for k, v in data.items()}
    for field, value in changes.items():
        setattr(package, field, value)
    package.save(update_fields=[*changes.keys(), "updated_at"])
    sync_creator_starting_price(creator.pk)
    return package


def deactivate_package(*, creator: Creator, package_id: UUID) -> None:
    # Packages are referenced by orders, so they are deactivated, never deleted.
    updated = CreatorPackage.objects.filter(pk=package_id, creator=creator).update(active=False)
    if not updated:
        raise NotFound()
    sync_creator_starting_price(creator.pk)


def apply_review(*, creator_id: UUID, stars: int) -> None:
    Creator.objects.filter(pk=creator_id).update(rating_sum=F("rating_sum") + stars, rating_count=F("rating_count") + 1)
