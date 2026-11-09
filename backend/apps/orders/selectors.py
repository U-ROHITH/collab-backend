from django.db.models import QuerySet

from apps.identity.models import Role, User
from apps.orders.models import Order


def orders_visible_to(user: User) -> QuerySet[Order]:
    """Every order query starts here. A user only ever sees orders they are a party to."""
    base = Order.objects.select_related("brand", "creator")
    if user.role == Role.BRAND:
        return base.filter(brand__owner=user)
    if user.role == Role.CREATOR:
        return base.filter(creator__user=user)
    return base.none()


_IN_ESCROW = ("funded", "draft_submitted", "revision_requested", "disputed")
_EARNED = ("approved", "completed")


def creator_earnings(creator_id: object) -> dict[str, int]:
    """Totals in paise from the order money snapshots. Payouts (Razorpay Route) aren't built yet,
    so "earned" means the brand approved the work, not that money reached the bank."""
    from django.db.models import Count, Q, Sum
    from django.db.models.functions import Coalesce

    def total(field: str, statuses: tuple[str, ...]) -> Coalesce:
        return Coalesce(Sum(field, filter=Q(status__in=statuses)), 0)

    return Order.objects.filter(creator_id=creator_id).aggregate(
        in_escrow=total("creator_payout_paise", _IN_ESCROW),
        earned=total("creator_payout_paise", _EARNED),
        tds_deducted=total("tds_paise", _EARNED),
        orders_in_escrow=Count("id", filter=Q(status__in=_IN_ESCROW)),
        orders_earned=Count("id", filter=Q(status__in=_EARNED)),
    )
