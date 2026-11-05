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
