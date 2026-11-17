from typing import Any

from django.db.models import Min, Prefetch, Q, QuerySet

from apps.creators.models import ApprovalStatus, Creator, CreatorPackage


def _creator_detail_queryset() -> QuerySet[Creator]:
    active_packages = CreatorPackage.objects.filter(active=True)
    return (
        Creator.objects.annotate(starting_price=Min("packages__price_paise", filter=Q(packages__active=True)))
        .prefetch_related(Prefetch("packages", queryset=active_packages, to_attr="active_packages"), "media")
    )


def public_creators() -> QuerySet[Creator]:
    """Only approved creators are ever visible in discovery."""
    return _creator_detail_queryset().filter(approval_status=ApprovalStatus.APPROVED)


def search_creators(params: dict[str, Any]) -> QuerySet[Creator]:
    qs = public_creators()
    if q := params.get("q"):
        qs = qs.filter(Q(display_name__icontains=q) | Q(bio__icontains=q) | Q(location__icontains=q))
    if category := params.get("category"):
        qs = qs.filter(categories__icontains=category)
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
    """An approved creator is visible to anyone (including anonymous guests).
    Pending or rejected creators are visible to:
      1. The creator themselves (to view their profile preview / public page).
      2. Admins / staff (for review and moderation).
      3. Brands that have interacted with the creator:
         - The creator applied to one of the brand's campaigns (bid).
         - An offer exists between the brand and the creator.
         - An order exists between the brand and the creator.
         - A conversation exists between the brand and the creator.
    """
    if creator.approval_status == ApprovalStatus.APPROVED:
        return True
    if viewer is None or not getattr(viewer, "is_authenticated", False):
        return False
    if getattr(viewer, "is_staff", False) or getattr(viewer, "is_superuser", False):
        return True
    from apps.identity.models import Role

    if getattr(viewer, "role", None) == Role.ADMIN:
        return True
    if getattr(viewer, "creator", None) and viewer.creator.pk == creator.pk:
        return True
    if creator.user_id == viewer.pk:
        return True
    if getattr(viewer, "role", None) == Role.BRAND:
        brand = getattr(viewer, "brand", None)
        if brand is None:
            return False
        from apps.campaigns.models import Bid
        from apps.messaging.models import Conversation
        from apps.offers.models import Offer
        from apps.orders.models import Order

        if Bid.objects.filter(campaign__brand=brand, creator=creator).exists():
            return True
        if Order.objects.filter(brand=brand, creator=creator).exists():
            return True
        if Offer.objects.filter(brand=brand, creator=creator).exists():
            return True
        if Conversation.objects.filter(brand=brand, creator=creator).exists():
            return True
    return False


def get_public_creator(creator_id: Any, viewer: Any = None) -> Creator | None:
    creator = _creator_detail_queryset().filter(pk=creator_id).first()
    if creator is None:
        return None
    if can_view_creator(viewer, creator):
        return creator
    return None
