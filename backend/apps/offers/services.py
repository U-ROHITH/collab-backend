import math
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from apps.brands.models import Brand
from apps.core.exceptions import Conflict, DomainError, NotFound
from apps.creators.models import Creator
from apps.creators.selectors import public_creators
from apps.identity.models import User
from apps.notifications.services import notify
from apps.offers.models import Offer, OfferStatus


def send_offer(*, brand: Brand, data: dict[str, Any]) -> Offer:
    creator = public_creators().filter(pk=data["creatorId"]).first()
    if creator is None:
        raise NotFound("Creator not found.")
    if data["deadline"] <= timezone.now():
        raise DomainError("Deadline must be in the future.", code="INVALID_DEADLINE")
    with transaction.atomic():
        offer = Offer.objects.create(
            brand=brand,
            creator=creator,
            deliverable=data["deliverable"],
            budget_paise=data["budget"],
            deadline=data["deadline"],
            message=data["message"],
            usage_rights=data["usageRights"],
        )
        if creator.user_id:
            notify(
                user_id=creator.user_id,
                type="offer_received",
                title=f"New offer from {brand.name}",
                data={"offerId": str(offer.pk)},
            )
    return offer


def withdraw_offer(*, brand: Brand, offer_id: UUID) -> Offer:
    with transaction.atomic():
        offer = Offer.objects.select_for_update().filter(pk=offer_id, brand=brand).first()
        if offer is None:
            raise NotFound()
        if offer.status != OfferStatus.PENDING:
            raise Conflict("Only pending offers can be withdrawn.", code="OFFER_NOT_PENDING")
        offer.status = OfferStatus.WITHDRAWN
        offer.save(update_fields=["status", "updated_at"])
    return offer


def respond_to_offer(*, actor: User, creator: Creator, offer_id: UUID, accept: bool) -> tuple[Offer, Any]:
    from apps.orders.services import create_order

    with transaction.atomic():
        offer = (
            Offer.objects.select_for_update(of=("self",))
            .select_related("brand")
            .filter(pk=offer_id, creator=creator)
            .first()
        )
        if offer is None:
            raise NotFound()
        if offer.status != OfferStatus.PENDING:
            raise Conflict("This offer is no longer pending.", code="OFFER_NOT_PENDING")
        if offer.deadline <= timezone.now():
            offer.status = OfferStatus.EXPIRED
            offer.save(update_fields=["status", "updated_at"])
            return offer, None
        if accept and not creator.is_approved:
            raise Conflict("Your profile must be approved before accepting offers.", code="CREATOR_NOT_APPROVED")

        offer.status = OfferStatus.ACCEPTED if accept else OfferStatus.REJECTED
        offer.save(update_fields=["status", "updated_at"])
        order = None
        if accept:
            days_left = max(1, math.ceil((offer.deadline - timezone.now()).total_seconds() / 86400))
            order = create_order(
                actor=actor,
                brand=offer.brand,
                creator=creator,
                source_type="offer",
                source_id=offer.pk,
                title=offer.deliverable,
                deliverables=offer.deliverable,
                brief=offer.message,
                subtotal_paise=offer.budget_paise,
                delivery_days=min(days_left, 90),
                usage_rights=offer.usage_rights,
                deadline=offer.deadline,
            )
        notify(
            user_id=offer.brand.owner_id,
            type="offer_accepted" if accept else "offer_rejected",
            title=f"{creator.display_name} {'accepted' if accept else 'declined'} your offer",
            data={"offerId": str(offer.pk), **({"orderId": str(order.pk)} if order else {})},
        )
    return offer, order
