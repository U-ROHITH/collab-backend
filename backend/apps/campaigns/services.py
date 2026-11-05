from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import F, QuerySet
from django.utils import timezone

from apps.brands.models import Brand
from apps.campaigns.models import Bid, BidStatus, Campaign, CampaignStatus
from apps.core.exceptions import Conflict, DomainError, NotFound
from apps.creators.models import Creator
from apps.identity.models import Role, User
from apps.notifications.services import notify

_CAMPAIGN_FIELDS = {
    "title": "title",
    "brief": "brief",
    "platform": "platform",
    "deliverables": "deliverables",
    "budgetMin": "budget_min_paise",
    "budgetMax": "budget_max_paise",
    "closesAt": "closes_at",
    "status": "status",
    "usageRights": "usage_rights",
}
_BID_FIELDS = {
    "price": "price_paise",
    "message": "message",
    "expectedViews": "expected_views",
    "deliveryDays": "delivery_days",
}


def campaigns_visible_to(user: User) -> QuerySet[Campaign]:
    if user.role == Role.BRAND:
        return Campaign.objects.filter(brand__owner=user)
    if user.role == Role.CREATOR:
        creator = getattr(user, "creator", None)
        if creator is None or not creator.is_approved:
            return Campaign.objects.none()
        return Campaign.objects.filter(status=CampaignStatus.OPEN, closes_at__gt=timezone.now())
    return Campaign.objects.none()


def _check_budget(min_p: int, max_p: int) -> None:
    if min_p > max_p:
        raise DomainError("Minimum budget can't exceed maximum budget.", code="INVALID_BUDGET")


def create_campaign(*, brand: Brand, data: dict[str, Any]) -> Campaign:
    _check_budget(data["budgetMin"], data["budgetMax"])
    if data["closesAt"] <= timezone.now():
        raise DomainError("Closing date must be in the future.", code="INVALID_CLOSES_AT")
    return Campaign.objects.create(brand=brand, **{_CAMPAIGN_FIELDS[k]: v for k, v in data.items()})


def update_campaign(*, brand: Brand, campaign_id: UUID, data: dict[str, Any]) -> Campaign:
    with transaction.atomic():
        campaign = Campaign.objects.select_for_update().filter(pk=campaign_id, brand=brand).first()
        if campaign is None:
            raise NotFound()
        if campaign.status in (CampaignStatus.CLOSED, CampaignStatus.CANCELLED):
            raise Conflict("Closed campaigns can't be edited.", code="CAMPAIGN_CLOSED")
        changes = {_CAMPAIGN_FIELDS[k]: v for k, v in data.items()}
        if changes.get("status") not in (None, CampaignStatus.DRAFT, CampaignStatus.OPEN):
            raise DomainError("Use the close endpoint to close a campaign.", code="INVALID_STATUS")
        for field, value in changes.items():
            setattr(campaign, field, value)
        _check_budget(campaign.budget_min_paise, campaign.budget_max_paise)
        campaign.save(update_fields=[*changes.keys(), "updated_at"])
    return campaign


def close_campaign(*, brand: Brand, campaign_id: UUID) -> Campaign:
    with transaction.atomic():
        campaign = Campaign.objects.select_for_update().filter(pk=campaign_id, brand=brand).first()
        if campaign is None:
            raise NotFound()
        campaign.status = CampaignStatus.CLOSED
        campaign.save(update_fields=["status", "updated_at"])
        Bid.objects.filter(campaign=campaign, status=BidStatus.PENDING).update(status=BidStatus.REJECTED)
    return campaign


def _open_campaign_for_bidding(campaign_id: UUID) -> Campaign:
    campaign = (
        Campaign.objects.select_related("brand")
        .filter(pk=campaign_id, status=CampaignStatus.OPEN, closes_at__gt=timezone.now())
        .first()
    )
    if campaign is None:
        raise NotFound("Campaign not found or no longer open.")
    return campaign


def create_bid(*, creator: Creator, campaign_id: UUID, data: dict[str, Any]) -> Bid:
    campaign = _open_campaign_for_bidding(campaign_id)
    try:
        with transaction.atomic():
            bid = Bid.objects.create(campaign=campaign, creator=creator, **{_BID_FIELDS[k]: v for k, v in data.items()})
            notify(
                user_id=campaign.brand.owner_id,
                type="new_bid",
                title=f"New bid on {campaign.title}",
                data={"campaignId": str(campaign.pk), "bidId": str(bid.pk)},
            )
    except IntegrityError:
        raise Conflict("You already bid on this campaign. Edit your bid instead.", code="BID_EXISTS") from None
    return bid


def update_bid(*, creator: Creator, campaign_id: UUID, bid_id: UUID, data: dict[str, Any]) -> Bid:
    _open_campaign_for_bidding(campaign_id)
    with transaction.atomic():
        bid = Bid.objects.select_for_update().filter(pk=bid_id, campaign_id=campaign_id, creator=creator).first()
        if bid is None:
            raise NotFound()
        if bid.status != BidStatus.PENDING:
            raise Conflict("Only pending bids can be changed.", code="BID_NOT_PENDING")
        changes = {_BID_FIELDS[k]: v for k, v in data.items()}
        for field, value in changes.items():
            setattr(bid, field, value)
        bid.version = F("version") + 1
        bid.save(update_fields=[*changes.keys(), "version", "updated_at"])
        bid.refresh_from_db()
    return bid


def withdraw_bid(*, creator: Creator, campaign_id: UUID, bid_id: UUID) -> Bid:
    with transaction.atomic():
        bid = Bid.objects.select_for_update().filter(pk=bid_id, campaign_id=campaign_id, creator=creator).first()
        if bid is None:
            raise NotFound()
        if bid.status != BidStatus.PENDING:
            raise Conflict("Only pending bids can be withdrawn.", code="BID_NOT_PENDING")
        bid.status = BidStatus.WITHDRAWN
        bid.save(update_fields=["status", "updated_at"])
    return bid


def select_bid(*, actor: User, brand: Brand, campaign_id: UUID, bid_id: UUID) -> Any:
    """Brand picks a bid -> an order is created at the bid's price (server-side)."""
    from apps.orders.services import create_order

    with transaction.atomic():
        bid = (
            Bid.objects.select_for_update(of=("self",))
            .select_related("campaign", "creator")
            .filter(pk=bid_id, campaign_id=campaign_id, campaign__brand=brand)
            .first()
        )
        if bid is None:
            raise NotFound()
        if bid.status != BidStatus.PENDING:
            raise Conflict("This bid can't be selected.", code="BID_NOT_PENDING")
        if not bid.creator.is_approved:
            raise Conflict("This creator is not available.", code="CREATOR_UNAVAILABLE")
        bid.status = BidStatus.SELECTED
        bid.save(update_fields=["status", "updated_at"])
        campaign = bid.campaign
        order = create_order(
            actor=actor,
            brand=brand,
            creator=bid.creator,
            source_type="bid",
            source_id=bid.pk,
            title=campaign.title,
            deliverables=campaign.deliverables,
            brief=campaign.brief,
            subtotal_paise=bid.price_paise,
            delivery_days=bid.delivery_days,
            usage_rights=campaign.usage_rights,
        )
        if bid.creator.user_id:
            notify(
                user_id=bid.creator.user_id,
                type="bid_selected",
                title=f"Your bid on {campaign.title} was selected",
                data={"orderId": str(order.pk)},
            )
    return order
