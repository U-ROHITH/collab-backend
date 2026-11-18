from typing import Any

from django.db.models import Min, Prefetch, Q, QuerySet

from apps.creators.models import ApprovalStatus, Creator, CreatorPackage


def _with_starting_price(qs: QuerySet[Creator]) -> QuerySet[Creator]:
    return qs.annotate(starting_price=Min("packages__price_paise", filter=Q(packages__active=True)))


def public_creators() -> QuerySet[Creator]:
    """Only approved creators with active accounts are visible in discovery, featured creators first."""
    from apps.identity.models import UserStatus

    return (
        _with_starting_price(Creator.objects.filter(approval_status=ApprovalStatus.APPROVED))
        .filter(Q(user__isnull=True) | Q(user__status=UserStatus.ACTIVE))
        .order_by("-featured", "featured_order", "-rating_sum", "-created_at")
    )


def search_creators(params: dict[str, Any]) -> QuerySet[Creator]:
    qs = public_creators()
    if q := params.get("q"):
        qs = qs.filter(Q(display_name__icontains=q) | Q(bio__icontains=q) | Q(location__icontains=q))
    if category := params.get("category"):
        # Whole-category, case-insensitive match ("art" must not match "Smart Home").
        qs = qs.extra(  # noqa: S610 - parameterised
            where=["lower(%s) IN (SELECT lower(c) FROM unnest(creators_creator.categories) AS c)"],
            params=[category],
        )
    if location := params.get("location"):
        qs = qs.filter(location__icontains=location)
    if platform := params.get("platform"):
        qs = qs.filter(primary_platform=platform)
    if params.get("verified"):
        qs = qs.filter(verified=True)
    if (min_price := params.get("minPrice")) is not None:
        qs = qs.filter(starting_price__gte=min_price)
    if (max_price := params.get("maxPrice")) is not None:
        qs = qs.filter(starting_price__lte=max_price)
    if (min_followers := params.get("minFollowers")) is not None:
        qs = qs.filter(follower_count__gte=min_followers)
    return qs


def can_view_creator(viewer: Any, creator: Creator) -> bool:
    """Approved creators are public. Others are visible to themselves, staff/admins, and brands that already
    work with them (bid, offer, order or conversation)."""
    from apps.identity.models import Role

    if creator.approval_status == ApprovalStatus.APPROVED:
        return True
    if not getattr(viewer, "is_authenticated", False):
        return False
    if viewer.is_staff or viewer.role == Role.ADMIN or creator.user_id == viewer.pk:
        return True
    if viewer.role != Role.BRAND or not hasattr(viewer, "brand"):
        return False
    from apps.messaging.models import Conversation
    from apps.messaging.services import has_relationship

    brand = viewer.brand
    return has_relationship(brand, creator) or Conversation.objects.filter(brand=brand, creator=creator).exists()


def get_public_creator(creator_id: Any, viewer: Any = None) -> Creator | None:
    active = CreatorPackage.objects.filter(active=True)
    creator = (
        _with_starting_price(Creator.objects.filter(pk=creator_id))
        .prefetch_related(Prefetch("packages", queryset=active, to_attr="active_packages"), "media")
        .first()
    )
    return creator if creator is not None and can_view_creator(viewer, creator) else None
