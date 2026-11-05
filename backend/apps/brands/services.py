from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction

from apps.brands.models import Brand, BrandList, BrandListItem, CartItem
from apps.core.exceptions import Conflict, DomainError, NotFound
from apps.creators.models import CreatorPackage
from apps.creators.selectors import public_creators
from apps.identity.models import User

_BRAND_FIELDS = {
    "name": "name",
    "website": "website",
    "industry": "industry",
    "category": "industry",
    "legalName": "legal_name",
    "gstin": "gstin",
    "stateCode": "state_code",
    "billingAddress": "billing_address",
}


def create_brand_for_user(*, user: User, name: str) -> Brand:
    return Brand.objects.create(owner=user, name=name)


def update_brand(*, brand: Brand, data: dict[str, Any]) -> Brand:
    changes = {_BRAND_FIELDS[k]: v for k, v in data.items()}
    for field, value in changes.items():
        setattr(brand, field, value)
    brand.save(update_fields=[*changes.keys(), "updated_at"])
    return brand


# --- Lists ------------------------------------------------------------------------------------
def _get_list(brand: Brand, list_id: UUID) -> BrandList:
    obj = BrandList.objects.filter(pk=list_id, brand=brand).first()
    if obj is None:
        raise NotFound()
    return obj


def create_list(*, brand: Brand, name: str) -> BrandList:
    try:
        with transaction.atomic():
            return BrandList.objects.create(brand=brand, name=name)
    except IntegrityError:
        raise Conflict("A list with this name already exists.", code="LIST_NAME_TAKEN") from None


def rename_list(*, brand: Brand, list_id: UUID, name: str) -> BrandList:
    obj = _get_list(brand, list_id)
    obj.name = name
    try:
        with transaction.atomic():
            obj.save(update_fields=["name", "updated_at"])
    except IntegrityError:
        raise Conflict("A list with this name already exists.", code="LIST_NAME_TAKEN") from None
    return obj


def delete_list(*, brand: Brand, list_id: UUID) -> None:
    _get_list(brand, list_id).delete()


def add_creator_to_list(*, brand: Brand, list_id: UUID, creator_id: UUID) -> None:
    obj = _get_list(brand, list_id)
    if not public_creators().filter(pk=creator_id).exists():
        raise NotFound("Creator not found.")
    BrandListItem.objects.get_or_create(list=obj, creator_id=creator_id)


def remove_creator_from_list(*, brand: Brand, list_id: UUID, creator_id: UUID) -> None:
    obj = _get_list(brand, list_id)
    BrandListItem.objects.filter(list=obj, creator_id=creator_id).delete()


# --- Cart -------------------------------------------------------------------------------------
def add_to_cart(*, brand: Brand, package_id: UUID, brief: str) -> CartItem:
    package = CreatorPackage.objects.filter(pk=package_id, active=True, creator__in=public_creators()).first()
    if package is None:
        raise NotFound("Package not found.")
    item, _ = CartItem.objects.update_or_create(brand=brand, package=package, defaults={"brief": brief})
    return item


def remove_from_cart(*, brand: Brand, item_id: UUID) -> None:
    if not CartItem.objects.filter(pk=item_id, brand=brand).delete()[0]:
        raise NotFound()


def checkout_cart(*, brand: Brand, actor: User, usage_rights: dict[str, Any]) -> list[Any]:
    """Create one order per cart item. Prices are read from the package, never from the client."""
    from apps.orders.services import create_order

    with transaction.atomic():
        items = list(CartItem.objects.select_for_update().filter(brand=brand).select_related("package__creator"))
        if not items:
            raise DomainError("Your cart is empty.", code="CART_EMPTY")
        orders = []
        for item in items:
            package = item.package
            if not package.active or not package.creator.is_approved:
                raise Conflict(f"'{package.title}' is no longer available.", code="PACKAGE_UNAVAILABLE")
            orders.append(
                create_order(
                    actor=actor,
                    brand=brand,
                    creator=package.creator,
                    source_type="package",
                    source_id=item.pk,
                    title=package.title,
                    deliverables=package.deliverables,
                    brief=item.brief,
                    subtotal_paise=package.price_paise,
                    delivery_days=package.delivery_days,
                    usage_rights=usage_rights,
                )
            )
        CartItem.objects.filter(pk__in=[i.pk for i in items]).delete()
    return orders
