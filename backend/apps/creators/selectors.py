from typing import Any

from django.db.models import Prefetch, Q, QuerySet

from apps.creators.models import ApprovalStatus, Creator, CreatorPackage


def public_creators() -> QuerySet[Creator]:
    """Only approved creators are ever visible in discovery."""
    active_packages = CreatorPackage.objects.filter(active=True)
    return Creator.objects.filter(approval_status=ApprovalStatus.APPROVED).prefetch_related(
        Prefetch("packages", queryset=active_packages, to_attr="active_packages")
    )


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
        qs = qs.filter(starting_price_paise__gte=min_price)
    if (max_price := params.get("maxPrice")) is not None:
        qs = qs.filter(starting_price_paise__lte=max_price)
    if (min_followers := params.get("minFollowers")) is not None:
        qs = qs.filter(follower_count__gte=min_followers)
    return qs


def get_public_creator(creator_id: Any) -> Creator | None:
    return public_creators().filter(pk=creator_id).first()
