from datetime import timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.campaigns.models import Campaign, CampaignStatus, ModerationStatus
from apps.core.exceptions import BadRequest, Conflict, Forbidden, NotFound
from apps.notifications.services import notify
from apps.pitches.models import Pitch, PitchStatus


def _cooldown_days() -> int:
    return getattr(settings, "PITCH_DECLINE_COOLDOWN_DAYS", 15)


def _validate_campaign(brand_id: UUID, campaign_id: UUID | None) -> Campaign | None:
    if campaign_id is None:
        return None
    campaign = Campaign.objects.filter(pk=campaign_id).first()
    now = timezone.now()
    if (
        campaign is None
        or str(campaign.brand_id) != str(brand_id)
        or campaign.status != CampaignStatus.OPEN
        or campaign.moderation_status != ModerationStatus.LIVE
        or campaign.closes_at <= now
    ):
        raise BadRequest(
            "Campaign not found or not an open approved brief for this brand.",
            code="BRIEF_NOT_THIS_BRAND",
        )
    return campaign


def create_pitch(
    *,
    actor_user: Any,
    brand_id: UUID,
    message: str,
    links: list[str],
    campaign_id: UUID | None,
) -> Pitch:
    from apps.brands.models import Brand
    from apps.identity.models import UserStatus

    creator = getattr(actor_user, "creator", None)
    if creator is None:
        raise NotFound("Creator profile not found.")

    brand = Brand.objects.select_related("owner").filter(pk=brand_id).first()
    if brand is None or brand.owner.status == UserStatus.SUSPENDED:
        raise NotFound("Brand not found.")

    campaign = _validate_campaign(brand_id, campaign_id)

    cooldown_days = _cooldown_days()
    latest_declined = (
        Pitch.objects.filter(creator=creator, brand=brand, status=PitchStatus.DECLINED)
        .order_by("-declined_at")
        .first()
    )
    if latest_declined and latest_declined.declined_at:
        retry_at = latest_declined.declined_at + timedelta(days=cooldown_days)
        if timezone.now() < retry_at:
            raise Conflict(
                "You must wait before pitching this brand again.",
                code="PITCH_COOLDOWN",
                details={"retryAt": retry_at.isoformat()},
            )

    try:
        with transaction.atomic():
            existing = (
                Pitch.objects.select_for_update()
                .filter(creator=creator, brand=brand, status__in=[PitchStatus.SENT, PitchStatus.EDITED])
                .first()
            )
            if existing is not None:
                raise Conflict("You already have an active pitch with this brand.", code="ALREADY_PITCHED")
            pitch = Pitch.objects.create(
                creator=creator,
                brand=brand,
                campaign=campaign,
                message=message,
                links=links,
                status=PitchStatus.SENT,
            )
    except IntegrityError:
        raise Conflict("You already have an active pitch with this brand.", code="ALREADY_PITCHED") from None

    # Notify brand owner
    if campaign:
        title = f"{creator.display_name} pitched you about {campaign.title}"
    else:
        title = f"{creator.display_name} sent you a pitch"
    notify(
        user_id=brand.owner_id,
        type="pitch_received",
        title=title,
        data={
            "pitchId": str(pitch.id),
            "creatorId": str(creator.id),
            "campaignId": str(campaign.id) if campaign else None,
        },
    )
    return pitch


def edit_pitch(
    *,
    actor_user: Any,
    pitch_id: UUID,
    message: str | None,
    links: list[str] | None,
    campaign_id: UUID | None,
    campaign_id_provided: bool,
) -> Pitch:
    creator = getattr(actor_user, "creator", None)
    if creator is None:
        raise NotFound()

    with transaction.atomic():
        pitch = Pitch.objects.select_for_update().filter(pk=pitch_id).first()
        if pitch is None:
            raise NotFound()
        if pitch.creator_id != creator.id:
            raise Forbidden("You are not allowed to edit this pitch.", code="FORBIDDEN")
        if pitch.status == PitchStatus.DECLINED:
            raise Conflict("Cannot edit a declined pitch.", code="PITCH_DECLINED")
        if pitch.edit_count >= 1:
            raise Conflict("You have already used your one edit.", code="EDIT_USED")

        if campaign_id_provided:
            campaign = _validate_campaign(pitch.brand_id, campaign_id)
            pitch.campaign = campaign

        if message is not None:
            pitch.message = message
        if links is not None:
            pitch.links = links
        pitch.edit_count = 1
        pitch.status = PitchStatus.EDITED
        pitch.edited_at = timezone.now()
        pitch.save()

    # Notify brand owner
    notify(
        user_id=pitch.brand.owner_id,
        type="pitch_edited",
        title=f"{creator.display_name} edited their pitch",
        data={
            "pitchId": str(pitch.id),
            "creatorId": str(creator.id),
            "campaignId": str(pitch.campaign_id) if pitch.campaign_id else None,
        },
    )
    return pitch


def mark_read(*, actor_user: Any, pitch_id: UUID) -> Pitch:
    brand = getattr(actor_user, "brand", None)
    with transaction.atomic():
        pitch = Pitch.objects.select_for_update().filter(pk=pitch_id).first()
        if pitch is None:
            raise NotFound()
        if brand is None or pitch.brand_id != brand.id:
            raise Forbidden("You are not allowed to read this pitch.", code="FORBIDDEN")
        if pitch.read_at is None:
            pitch.read_at = timezone.now()
            pitch.save(update_fields=["read_at", "updated_at"])
    return pitch


def decline_pitch(*, actor_user: Any, pitch_id: UUID) -> Pitch:
    brand = getattr(actor_user, "brand", None)
    cooldown_days = _cooldown_days()

    with transaction.atomic():
        pitch = Pitch.objects.select_related("creator", "brand").select_for_update().filter(pk=pitch_id).first()
        if pitch is None:
            raise NotFound()
        if brand is None or pitch.brand_id != brand.id:
            raise Forbidden("You are not allowed to decline this pitch.", code="FORBIDDEN")
        if pitch.status == PitchStatus.DECLINED:
            return pitch  # idempotent: does not reset cooldown
        pitch.status = PitchStatus.DECLINED
        pitch.declined_at = timezone.now()
        pitch.save(update_fields=["status", "declined_at", "updated_at"])

    retry_at = pitch.declined_at + timedelta(days=cooldown_days)
    notify(
        user_id=pitch.creator.user_id,
        type="pitch_declined",
        title=f"{brand.name} passed on your pitch",
        data={
            "pitchId": str(pitch.id),
            "brandId": str(brand.id),
            "retryAt": retry_at.isoformat(),
        },
    )
    return pitch


def pitch_status_for_creator(creator_id: UUID, brand_id: UUID) -> dict[str, Any]:
    """Return canPitch, pitchBlockedUntil, and myPitch for the brand profile response."""
    cooldown_days = _cooldown_days()
    active = (
        Pitch.objects.filter(
            creator_id=creator_id,
            brand_id=brand_id,
            status__in=[PitchStatus.SENT, PitchStatus.EDITED],
        )
        .order_by("-created_at")
        .first()
    )
    if active:
        return {"canPitch": False, "pitchBlockedUntil": None, "myPitch": active}

    declined = (
        Pitch.objects.filter(creator_id=creator_id, brand_id=brand_id, status=PitchStatus.DECLINED)
        .order_by("-declined_at")
        .first()
    )
    if declined and declined.declined_at:
        retry_at = declined.declined_at + timedelta(days=cooldown_days)
        if timezone.now() < retry_at:
            return {"canPitch": False, "pitchBlockedUntil": retry_at.isoformat(), "myPitch": None}

    return {"canPitch": True, "pitchBlockedUntil": None, "myPitch": None}
