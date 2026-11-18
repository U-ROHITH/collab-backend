from datetime import timedelta
from typing import Any
from uuid import UUID

from django.utils import timezone

from apps.brands.models import Brand
from apps.campaigns.models import Bid, Campaign
from apps.core.exceptions import NotFound
from apps.creators.models import Creator
from apps.identity.models import User
from apps.kyc.models import KycStatus
from apps.moderation.models import (
    AbuseTargetType,
    BidAlert,
    BidAlertSeverity,
    BidAlertStatus,
    BidAlertType,
)
from apps.orders.models import Review


def resolve_target(target_type: str, target_id: UUID) -> tuple[Any, User | None]:
    """Find target instance and target user (if applicable)."""
    if target_type == AbuseTargetType.CREATOR:
        c = Creator.objects.select_related("user").filter(pk=target_id).first()
        if not c:
            raise NotFound("Target creator not found.")
        return c, c.user

    if target_type == AbuseTargetType.BRAND:
        b = Brand.objects.select_related("owner").filter(pk=target_id).first()
        if not b:
            raise NotFound("Target brand not found.")
        return b, b.owner

    if target_type == AbuseTargetType.CAMPAIGN:
        camp = Campaign.objects.select_related("brand", "brand__owner").filter(pk=target_id).first()
        if not camp:
            raise NotFound("Target campaign not found.")
        return camp, camp.brand.owner if camp.brand else None

    if target_type == AbuseTargetType.REVIEW:
        rev = Review.objects.select_related("brand", "brand__owner").filter(pk=target_id).first()
        if not rev:
            raise NotFound("Target review not found.")
        return rev, rev.brand.owner if rev.brand else None

    # Default lookup in User
    u = User.objects.filter(pk=target_id).first()
    if not u:
        raise NotFound("Target not found.")
    return u, u


def detect_bid_patterns(campaign_id: UUID) -> list[BidAlert]:
    """Analyze bids on a campaign for suspicious patterns: spikes, extreme under-bidding, unverified clusters."""
    campaign = Campaign.objects.filter(pk=campaign_id).first()
    if not campaign:
        return []

    alerts: list[BidAlert] = []
    bids = list(
        Bid.objects.filter(campaign=campaign)
        .select_related("creator", "creator__user", "creator__user__kyc")
        .order_by("created_at")
    )
    if not bids:
        return []

    # 1. Bid Spike: >= 5 bids within a 60-minute sliding window
    for i in range(len(bids)):
        window_bids = [b for b in bids[i:] if (b.created_at - bids[i].created_at).total_seconds() <= 3600]
        if len(window_bids) >= 5:
            alert, created = BidAlert.objects.get_or_create(
                campaign=campaign,
                alert_type=BidAlertType.BID_SPIKE,
                defaults={
                    "severity": BidAlertSeverity.HIGH,
                    "status": BidAlertStatus.PENDING,
                    "details": {
                        "count": len(window_bids),
                        "windowMinutes": 60,
                        "firstBidAt": str(window_bids[0].created_at),
                        "lastBidAt": str(window_bids[-1].created_at),
                    },
                },
            )
            if created:
                alert.bids.set(window_bids)
                alerts.append(alert)
            break

    # 2. Suspicious Low Bid: Bid price < 20% of min budget
    low_threshold = int(campaign.budget_min_paise * 0.20)
    low_bids = [b for b in bids if b.price_paise < low_threshold]
    if low_bids:
        alert, created = BidAlert.objects.get_or_create(
            campaign=campaign,
            alert_type=BidAlertType.SUSPICIOUS_LOW_BID,
            defaults={
                "severity": BidAlertSeverity.MEDIUM,
                "status": BidAlertStatus.PENDING,
                "details": {
                    "count": len(low_bids),
                    "minBudgetPaise": campaign.budget_min_paise,
                    "lowestBidPaise": min(b.price_paise for b in low_bids),
                },
            },
        )
        if created:
            alert.bids.set(low_bids)
            alerts.append(alert)

    # 3. Unverified Bidder Cluster: 3+ bids from accounts without verified KYC or created in last 24h
    now = timezone.now()
    suspicious_bids = []
    for b in bids:
        creator_user = b.creator.user if b.creator else None
        if not creator_user:
            continue
        kyc = getattr(creator_user, "kyc", None)
        is_kyc_unverified = not kyc or kyc.status != KycStatus.VERIFIED
        is_new_account = (now - creator_user.date_joined) <= timedelta(hours=24)
        if is_kyc_unverified and is_new_account:
            suspicious_bids.append(b)

    if len(suspicious_bids) >= 3:
        alert, created = BidAlert.objects.get_or_create(
            campaign=campaign,
            alert_type=BidAlertType.UNVERIFIED_BIDDER_CLUSTER,
            defaults={
                "severity": BidAlertSeverity.HIGH,
                "status": BidAlertStatus.PENDING,
                "details": {
                    "count": len(suspicious_bids),
                    "reason": "3+ bids from new unverified creator accounts",
                },
            },
        )
        if created:
            alert.bids.set(suspicious_bids)
            alerts.append(alert)

    return alerts
