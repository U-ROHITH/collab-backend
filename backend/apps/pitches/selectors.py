from typing import Any
from uuid import UUID

from django.db.models import QuerySet

from apps.identity.models import UserStatus
from apps.pitches.models import Pitch


def pitches_for_creator(creator_id: UUID) -> QuerySet[Pitch]:
    return (
        Pitch.objects.filter(creator_id=creator_id)
        .select_related("brand", "campaign")
        .order_by("-created_at")
    )


def pitches_for_brand(brand_id: UUID, params: dict[str, Any]) -> QuerySet[Pitch]:
    qs = (
        Pitch.objects.filter(brand_id=brand_id)
        .select_related("creator", "creator__user", "campaign")
        .exclude(creator__user__status=UserStatus.SUSPENDED)
        .order_by("-created_at")
    )
    if campaign_id := params.get("campaignId"):
        qs = qs.filter(campaign_id=campaign_id)
    if status := params.get("status"):
        qs = qs.filter(status=status)
    if params.get("unread") == 1:
        qs = qs.filter(read_at__isnull=True)
    return qs
