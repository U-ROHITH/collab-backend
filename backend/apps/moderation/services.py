from typing import Any
from uuid import UUID

from apps.brands.models import Brand
from apps.campaigns.models import Campaign
from apps.core.exceptions import NotFound
from apps.creators.models import Creator
from apps.identity.models import User
from apps.moderation.models import AbuseTargetType
from apps.orders.models import Review


def resolve_target(target_type: str, target_id: UUID) -> tuple[Any, User | None]:
    """Find target instance and target user (if applicable)."""
    if target_type == AbuseTargetType.CREATOR:
        c = Creator.objects.select_related("user").filter(pk=target_id).first()
        if not c:
            raise NotFound("Target creator not found.")
        return c, c.user

    if target_type == AbuseTargetType.BRAND:
        b = Brand.objects.select_related("owner").filter(pk=target_id).first()
        if not b:
            raise NotFound("Target brand not found.")
        return b, b.owner

    if target_type == AbuseTargetType.CAMPAIGN:
        camp = Campaign.objects.select_related("brand", "brand__owner").filter(pk=target_id).first()
        if not camp:
            raise NotFound("Target campaign not found.")
        return camp, camp.brand.owner if camp.brand else None

    if target_type == AbuseTargetType.REVIEW:
        rev = Review.objects.select_related("brand", "brand__owner").filter(pk=target_id).first()
        if not rev:
            raise NotFound("Target review not found.")
        return rev, rev.brand.owner if rev.brand else None

    if target_type in (AbuseTargetType.MESSAGE, "chat"):
        from apps.messaging.models import Message

        msg = Message.objects.select_related("sender").filter(pk=target_id).first()
        if not msg:
            raise NotFound("Target message not found.")
        return msg, msg.sender

    if target_type == "profile":
        c = Creator.objects.select_related("user").filter(pk=target_id).first()
        if c:
            return c, c.user
        b = Brand.objects.select_related("owner").filter(pk=target_id).first()
        if b:
            return b, b.owner
        u = User.objects.filter(pk=target_id).first()
        if u:
            return u, u
        raise NotFound("Target profile not found.")

    # Default lookup in User
    u = User.objects.filter(pk=target_id).first()
    if not u:
        raise NotFound("Target not found.")
    return u, u
