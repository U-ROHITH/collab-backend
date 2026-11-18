from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, F, OuterRef, Q, QuerySet, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.brands.models import Brand
from apps.campaigns.models import Bid, BidStatus, Campaign, CampaignStatus, ModerationStatus
from apps.core.exceptions import Conflict, DomainError, NotFound
from apps.creators.models import Creator
from apps.identity.models import Role, User, UserStatus
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

REVIEWED_FIELDS = {"title", "brief", "deliverables", "budgetMin", "budgetMax", "usageRights", "platform"}


def public_open_campaigns() -> QuerySet[Campaign]:
    return Campaign.objects.filter(
        status=CampaignStatus.OPEN,
        moderation_status=ModerationStatus.LIVE,
        closes_at__gt=timezone.now(),
    )


def sees_full_campaigns(user: User | None) -> bool:
    """Brands (own campaigns) and approved creators get full details; everyone else gets the guest view."""
    role = getattr(user, "role", None)
    if role == Role.BRAND:
        return True
    creator = getattr(user, "creator", None) if role == Role.CREATOR else None
    return bool(creator and creator.is_approved)


def campaigns_visible_to(user: User | None) -> QuerySet[Campaign]:
    if getattr(user, "role", None) == Role.BRAND:
        return Campaign.objects.filter(brand__owner=user)
    if sees_full_campaigns(user):
        return public_open_campaigns()
    return Campaign.objects.none()


def campaigns_listed_for(user: User | None) -> QuerySet[Campaign]:
    """GET /campaigns/: guests, unapproved creators and users mid-signup get the public open list."""
    if getattr(user, "role", None) == Role.BRAND or sees_full_campaigns(user):
        return campaigns_visible_to(user)
    return public_open_campaigns()


# --- Read helpers (one query per list: counts are annotated, never fetched per row) --------------
def _counted_bids(prefix: str = "") -> Q:
    """Bids shown in `bidCount`: everything except withdrawn ones."""
    return ~Q(**{f"{prefix}status": BidStatus.WITHDRAWN})


def with_bid_count(qs: QuerySet[Campaign]) -> QuerySet[Campaign]:
    return qs.annotate(bid_count=Count("bids", filter=_counted_bids("bids__")))


def filter_campaigns(qs: QuerySet[Campaign], filters: dict[str, Any]) -> QuerySet[Campaign]:
    """Apply validated list filters (see CampaignListQuery)."""
    if platform := filters.get("platform"):
        qs = qs.filter(platform=platform)
    if (min_gte := filters.get("budgetMin__gte")) is not None:
        qs = qs.filter(budget_min_paise__gte=min_gte)
    if (max_lte := filters.get("budgetMax__lte")) is not None:
        qs = qs.filter(budget_max_paise__lte=max_lte)
    return qs


def bids_of(user: User) -> QuerySet[Bid]:
    """A creator's own bids with their campaign, newest first, `campaign_bid_count` annotated."""
    counts = (
        Bid.objects.filter(_counted_bids(), campaign=OuterRef("campaign"))
        .order_by()
        .values("campaign")
        .annotate(n=Count("pk"))
        .values("n")
    )
    return (
        Bid.objects.filter(creator__user=user)
        .select_related("campaign", "creator")
        .annotate(campaign_bid_count=Coalesce(Subquery(counts), 0))
    )


def _check_budget(min_p: int, max_p: int) -> None:
    if min_p > max_p:
        raise DomainError("Minimum budget can't exceed maximum budget.", code="INVALID_BUDGET")


def create_campaign(*, brand: Brand, data: dict[str, Any]) -> Campaign:
    _check_budget(data["budgetMin"], data["budgetMax"])
    if data["closesAt"] <= timezone.now():
        raise DomainError("Closing date must be in the future.", code="INVALID_CLOSES_AT")
    fields = {_CAMPAIGN_FIELDS[k]: v for k, v in data.items()}
    if getattr(settings, "CAMPAIGN_MODERATION_ENFORCED", True):
        fields["moderation_status"] = ModerationStatus.PENDING_REVIEW
        fields["submitted_at"] = timezone.now()
        fields["changed_fields"] = []
    else:
        fields["moderation_status"] = ModerationStatus.LIVE
        fields["submitted_at"] = timezone.now()
        fields["posted_at"] = timezone.now()
        fields["changed_fields"] = []
    campaign = Campaign.objects.create(brand=brand, **fields)

    # Notify admins if pending review
    if campaign.moderation_status == ModerationStatus.PENDING_REVIEW:
        admins = User.objects.filter(role=Role.ADMIN, is_staff=True, status=UserStatus.ACTIVE)
        for adm in admins:
            notify(
                user_id=adm.id,
                type="campaign_submitted",
                title="New Campaign Submitted for Review",
                body=f"Campaign '{campaign.title}' by {brand.name} requires moderation",
                data={"campaignId": str(campaign.id), "brandId": str(brand.id)},
            )

    return campaign


def update_campaign(*, brand: Brand, campaign_id: UUID, data: dict[str, Any]) -> Campaign:
    with transaction.atomic():
        campaign = Campaign.objects.select_for_update().filter(pk=campaign_id, brand=brand).first()
        if campaign is None:
            raise NotFound()
        if campaign.status in (CampaignStatus.CLOSED, CampaignStatus.CANCELLED):
            raise Conflict("Closed campaigns can't be edited.", code="CAMPAIGN_CLOSED")

        actually_changed: list[str] = []
        for key in REVIEWED_FIELDS:
            if key in data:
                db_field = _CAMPAIGN_FIELDS[key]
                current_val = getattr(campaign, db_field)
                new_val = data[key]
                if current_val != new_val:
                    actually_changed.append(key)

        changes = {_CAMPAIGN_FIELDS[k]: v for k, v in data.items()}
        if changes.get("status") not in (None, CampaignStatus.DRAFT, CampaignStatus.OPEN):
            raise DomainError("Use the close endpoint to close a campaign.", code="INVALID_STATUS")
        for field, value in changes.items():
            setattr(campaign, field, value)
        _check_budget(campaign.budget_min_paise, campaign.budget_max_paise)

        re_review = False
        if getattr(settings, "CAMPAIGN_MODERATION_ENFORCED", True):
            if campaign.moderation_status == ModerationStatus.LIVE and actually_changed:
                campaign.moderation_status = ModerationStatus.PENDING_REVIEW
                campaign.submitted_at = timezone.now()
                campaign.changed_fields = sorted(actually_changed)
                # Freeze existing open bids
                Bid.objects.filter(campaign=campaign, status=BidStatus.PENDING).update(status=BidStatus.HELD)
                re_review = True
            elif campaign.moderation_status == ModerationStatus.REJECTED:
                campaign.moderation_status = ModerationStatus.PENDING_REVIEW
                campaign.submitted_at = timezone.now()
                campaign.changed_fields = sorted(actually_changed or list(data.keys()))
                campaign.moderation_reason = ""
                campaign.rule_broken = ""
                re_review = True

        update_fields = list(changes.keys())
        update_fields.append("updated_at")
        if re_review:
            update_fields.extend(
                ["moderation_status", "submitted_at", "changed_fields", "moderation_reason", "rule_broken"]
            )
        campaign.save(update_fields=list(set(update_fields)))

        if re_review:
            admins = User.objects.filter(role=Role.ADMIN, is_staff=True, status=UserStatus.ACTIVE)
            for adm in admins:
                notify(
                    user_id=adm.id,
                    type="campaign_submitted",
                    title="Campaign Updated and Resubmitted for Review",
                    body=f"Campaign '{campaign.title}' by {brand.name} requires re-moderation",
                    data={"campaignId": str(campaign.id), "brandId": str(brand.id)},
                )

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
    campaign = Campaign.objects.select_related("brand").filter(pk=campaign_id).first()
    if campaign is None or campaign.status != CampaignStatus.OPEN or campaign.closes_at <= timezone.now():
        raise NotFound("Campaign not found or no longer open.")
    if campaign.moderation_status != ModerationStatus.LIVE:
        raise Conflict("Campaign is not live for bidding.", code="CAMPAIGN_NOT_LIVE")
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
        if bid.campaign.moderation_status != ModerationStatus.LIVE:
            raise Conflict("Campaign is not live.", code="CAMPAIGN_NOT_LIVE")
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
