from __future__ import annotations

import csv
import io
from typing import Any

from django.db.models import Sum
from django.utils import timezone

from apps.campaigns.models import Campaign, CampaignStatus
from apps.identity.models import Role, User
from apps.money.models import WalletTransaction
from apps.orders.models import Order, OrderStatus


def get_user_overview_metrics(user: User) -> dict[str, Any]:
    if user.role == Role.BRAND:
        brand = getattr(user, "brand", None)
        if brand is None:
            return {"role": "brand", "metrics": {}}

        orders = Order.objects.filter(brand=brand)
        total_spend = (
            orders.exclude(status__in=[OrderStatus.CANCELLED, OrderStatus.REFUNDED]).aggregate(
                total=Sum("brand_total_paise")
            )["total"]
            or 0
        )
        total_orders = orders.count()
        completed_orders = orders.filter(status=OrderStatus.COMPLETED).count()
        active_orders = orders.filter(
            status__in=[
                OrderStatus.FUNDED,
                OrderStatus.DRAFT_SUBMITTED,
                OrderStatus.REVISION_REQUESTED,
                OrderStatus.APPROVED,
            ]
        ).count()

        campaigns = Campaign.objects.filter(brand=brand)
        total_campaigns = campaigns.count()
        open_campaigns = campaigns.filter(status=CampaignStatus.OPEN, closes_at__gt=timezone.now()).count()

        return {
            "role": "brand",
            "metrics": {
                "totalSpendPaise": total_spend,
                "totalSpendInr": round(total_spend / 100, 2),
                "totalCampaigns": total_campaigns,
                "activeCampaigns": open_campaigns,
                "totalOrders": total_orders,
                "completedOrders": completed_orders,
                "inProgressOrders": active_orders,
            },
        }

    if user.role == Role.CREATOR:
        creator = getattr(user, "creator", None)
        if creator is None:
            return {"role": "creator", "metrics": {}}

        orders = Order.objects.filter(creator=creator)
        total_earned = (
            orders.filter(status=OrderStatus.COMPLETED).aggregate(total=Sum("creator_payout_paise"))["total"] or 0
        )
        pending_earnings = (
            orders.filter(
                status__in=[
                    OrderStatus.FUNDED,
                    OrderStatus.DRAFT_SUBMITTED,
                    OrderStatus.REVISION_REQUESTED,
                    OrderStatus.APPROVED,
                ]
            ).aggregate(total=Sum("creator_payout_paise"))["total"]
            or 0
        )
        total_orders = orders.count()
        completed_orders = orders.filter(status=OrderStatus.COMPLETED).count()
        active_orders = orders.filter(
            status__in=[
                OrderStatus.FUNDED,
                OrderStatus.DRAFT_SUBMITTED,
                OrderStatus.REVISION_REQUESTED,
                OrderStatus.APPROVED,
            ]
        ).count()

        return {
            "role": "creator",
            "metrics": {
                "totalEarningsPaise": total_earned,
                "totalEarningsInr": round(total_earned / 100, 2),
                "pendingEarningsPaise": pending_earnings,
                "pendingEarningsInr": round(pending_earnings / 100, 2),
                "totalOrders": total_orders,
                "completedOrders": completed_orders,
                "activeOrders": active_orders,
                "reviewCount": creator.rating_count,
                "rating": creator.rating,
            },
        }

    return {"role": user.role or "user", "metrics": {}}


def generate_orders_csv(user: User) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Order ID",
            "Status",
            "Brand",
            "Creator",
            "Subtotal (Paise)",
            "Brand Total (Paise)",
            "Creator Payout (Paise)",
            "Brand Total (INR)",
            "Creator Payout (INR)",
            "Created At",
        ]
    )

    if user.role == Role.BRAND and hasattr(user, "brand"):
        orders = Order.objects.filter(brand=user.brand).select_related("brand", "creator")
    elif user.role == Role.CREATOR and hasattr(user, "creator"):
        orders = Order.objects.filter(creator=user.creator).select_related("brand", "creator")
    else:
        orders = Order.objects.none()

    for o in orders.order_by("-created_at"):
        writer.writerow(
            [
                str(o.id),
                o.status,
                o.brand.name if o.brand else "",
                o.creator.display_name if o.creator else "",
                o.subtotal_paise,
                o.brand_total_paise,
                o.creator_payout_paise,
                round(o.brand_total_paise / 100, 2),
                round(o.creator_payout_paise / 100, 2),
                o.created_at.isoformat(),
            ]
        )

    return output.getvalue()


def generate_campaigns_csv(user: User) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Campaign ID",
            "Title",
            "Platform",
            "Deliverables",
            "Status",
            "Budget Min (Paise)",
            "Budget Max (Paise)",
            "Budget Min (INR)",
            "Budget Max (INR)",
            "Closes At",
            "Created At",
        ]
    )

    if user.role == Role.BRAND and hasattr(user, "brand"):
        campaigns = Campaign.objects.filter(brand=user.brand)
    else:
        campaigns = Campaign.objects.filter(status=CampaignStatus.OPEN)

    for c in campaigns.order_by("-created_at"):
        writer.writerow(
            [
                str(c.id),
                c.title,
                c.platform,
                c.deliverables,
                c.status,
                c.budget_min_paise,
                c.budget_max_paise,
                round(c.budget_min_paise / 100, 2),
                round(c.budget_max_paise / 100, 2),
                c.closes_at.isoformat(),
                c.created_at.isoformat(),
            ]
        )

    return output.getvalue()


def generate_transactions_csv(user: User) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Transaction ID", "Type", "Amount (Paise)", "Amount (INR)", "Reference ID", "Created At"])

    if user.role == Role.CREATOR and hasattr(user, "creator"):
        wallet = getattr(user.creator, "wallet", None)
        txs = WalletTransaction.objects.filter(wallet=wallet) if wallet else WalletTransaction.objects.none()
    else:
        txs = WalletTransaction.objects.none()

    for t in txs.order_by("-created_at"):
        writer.writerow(
            [
                str(t.id),
                t.type,
                t.amount_paise,
                round(t.amount_paise / 100, 2),
                t.reference_id or "",
                t.created_at.isoformat(),
            ]
        )

    return output.getvalue()
