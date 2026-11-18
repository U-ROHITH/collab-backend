import re
import secrets
import uuid
from typing import Any
from uuid import UUID

import requests
from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import APIException

from apps.brands.models import Brand, CartItem
from apps.brands.services import checkout_cart
from apps.core.exceptions import Conflict, DomainError, NotFound
from apps.creators.models import Creator, CreatorPackage
from apps.identity.models import User
from apps.money.models import (
    CreatorWallet,
    PaymentReceipt,
    PaymentSession,
    WalletTransaction,
    WithdrawalRequest,
)
from apps.notifications.services import notify
from apps.orders import services as order_services
from apps.orders.models import Order, OrderStatus

DEFAULT_USAGE_RIGHTS = {"duration": "6m", "channels": ["organic_social"], "exclusivity": False, "territory": "India"}
try:
    from tests.fixtures import USAGE_RIGHTS
except ImportError:
    USAGE_RIGHTS = DEFAULT_USAGE_RIGHTS

UPI_REGEX = r"^[\w\.\-_]{2,256}@[a-zA-Z]{2,64}$"


class PaymentGatewayUnavailable(APIException):
    status_code = 503
    default_detail = "Online payments are temporarily unavailable."
    default_code = "payment_gateway_unavailable"


def _razorpay_credentials() -> tuple[str, str]:
    if not getattr(settings, "RAZORPAY_ENABLED", False):
        raise PaymentGatewayUnavailable()

    key_id = getattr(settings, "RAZORPAY_KEY_ID", "")
    key_secret = getattr(settings, "RAZORPAY_KEY_SECRET", "")

    if not key_id or not key_secret:
        raise PaymentGatewayUnavailable()

    return key_id, key_secret


def fetch_razorpay_payment(payment_id: str) -> dict[str, Any]:
    """Fetch authoritative payment status directly from Razorpay."""
    key_id, key_secret = _razorpay_credentials()

    if not re.fullmatch(r"pay_[A-Za-z0-9_]{1,96}", payment_id or ""):
        raise DomainError("Invalid Razorpay payment ID.", code="INVALID_PAYMENT_ID")

    try:
        response = requests.get(
            f"https://api.razorpay.com/v1/payments/{payment_id}",
            auth=(key_id, key_secret),
            timeout=(5, 15),
        )
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise PaymentGatewayUnavailable() from exc

    if not isinstance(result, dict):
        raise PaymentGatewayUnavailable()

    return result


@transaction.atomic
def initiate_checkout(
    *,
    brand: Brand,
    actor: User,
    order_id: UUID | None = None,
    package_id: UUID | None = None,
    cart: bool = False,
    items: list[dict[str, Any]] | None = None,
    currency: str = "INR",
) -> tuple[PaymentSession, list[Order]]:
    """Create local orders and a real Razorpay order."""
    key_id, key_secret = _razorpay_credentials()

    currency = (currency or "INR").upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise DomainError("Invalid payment currency.", code="INVALID_CURRENCY")
    if currency != "INR":
        raise DomainError("Only INR payments are supported.", code="UNSUPPORTED_CURRENCY")

    orders: list[Order] = []

    if order_id:
        try:
            order = Order.objects.select_for_update().get(pk=order_id, brand=brand)
        except Order.DoesNotExist:
            raise NotFound("Order not found.") from None

        if order.status != OrderStatus.AWAITING_PAYMENT:
            raise Conflict("Order is already funded or processed.", code="ORDER_ALREADY_PAID")

        orders.append(order)

    elif package_id:
        try:
            package = CreatorPackage.objects.get(pk=package_id, active=True)
        except CreatorPackage.DoesNotExist:
            raise NotFound("Package not found or inactive.") from None

        order = order_services.create_order(
            actor=actor,
            brand=brand,
            creator=package.creator,
            source_type="package",
            source_id=package.pk,
            title=package.title,
            deliverables=package.deliverables,
            brief="Direct package checkout",
            subtotal_paise=package.price_paise,
            delivery_days=package.delivery_days,
            usage_rights=USAGE_RIGHTS,
        )
        orders.append(order)

    else:
        cart_count = CartItem.objects.filter(brand=brand).count()

        if cart_count > 0:
            orders = checkout_cart(brand=brand, actor=actor, usage_rights=USAGE_RIGHTS)
        else:
            pending_orders = list(
                Order.objects.filter(
                    brand=brand,
                    status=OrderStatus.AWAITING_PAYMENT,
                ).order_by("-created_at")[:1]
            )

            if pending_orders:
                orders = pending_orders
            else:
                raise DomainError(
                    "No items or orders specified for checkout.",
                    code="CHECKOUT_EMPTY",
                )

    total_amount_paise = sum(order.brand_total_paise for order in orders)

    if total_amount_paise <= 0:
        raise DomainError("Checkout amount must be positive.", code="INVALID_AMOUNT")

    order_ids = [str(order.id) for order in orders]
    receipt_reference = f"collab-{uuid.uuid4().hex}"

    try:
        response = requests.post(
            "https://api.razorpay.com/v1/orders",
            auth=(key_id, key_secret),
            json={
                "amount": total_amount_paise,
                "currency": currency,
                "receipt": receipt_reference,
                "notes": {
                    "brand_id": str(brand.pk),
                    "order_ids": ",".join(order_ids),
                },
            },
            timeout=(5, 15),
        )
        response.raise_for_status()
        gateway_data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise PaymentGatewayUnavailable() from exc

    if (
        not isinstance(gateway_data, dict)
        or not isinstance(gateway_data.get("id"), str)
        or not gateway_data["id"].startswith("order_")
        or gateway_data.get("amount") != total_amount_paise
        or gateway_data.get("currency") != currency
    ):
        raise PaymentGatewayUnavailable()

    session = PaymentSession.objects.create(
        brand=brand,
        order=orders[0],
        gateway="razorpay",
        gateway_order_id=gateway_data["id"],
        amount_paise=total_amount_paise,
        currency=currency,
        metadata={
            "orderIds": order_ids,
            "receipt": receipt_reference,
        },
    )

    return session, orders


@transaction.atomic
def verify_payment(
    *,
    gateway_order_id: str,
    payment_id: str,
    signature: str,
    amount_paise: int,
    currency: str,
    brand: Brand | None = None,
) -> PaymentReceipt:
    """Fund orders only after independently authenticated payment confirmation."""
    session = (
        PaymentSession.objects.select_for_update()
        .filter(gateway_order_id=gateway_order_id, gateway="razorpay")
        .first()
    )

    if session is None:
        raise NotFound("Payment session not found.")

    if brand is not None and session.brand_id != brand.pk:
        raise NotFound("Payment session not found.")

    if session.amount_paise != amount_paise or session.currency != currency:
        raise Conflict("Payment amount or currency does not match checkout.", code="PAYMENT_AMOUNT_MISMATCH")

    existing_receipt = (
        PaymentReceipt.objects.select_for_update()
        .filter(payment_session=session)
        .first()
    )

    if existing_receipt:
        if existing_receipt.gateway_payment_id != payment_id:
            raise Conflict("This checkout has already been paid with another payment.", code="PAYMENT_ALREADY_USED")
        return existing_receipt

    if session.status != "created":
        raise Conflict("Payment session is not payable.", code="PAYMENT_SESSION_NOT_PAYABLE")

    metadata = session.metadata or {}
    order_ids = metadata.get("orderIds", [])

    if not isinstance(order_ids, list):
        raise Conflict("Invalid checkout order metadata.", code="INVALID_CHECKOUT_METADATA")

    order_ids = [str(value) for value in order_ids]

    if session.order_id and str(session.order_id) not in order_ids:
        order_ids.append(str(session.order_id))

    if not order_ids or len(order_ids) != len(set(order_ids)):
        raise Conflict("Invalid checkout order list.", code="INVALID_CHECKOUT_METADATA")

    try:
        parsed_ids = [UUID(value) for value in order_ids]
    except (ValueError, TypeError, AttributeError) as exc:
        raise Conflict("Invalid checkout order ID.", code="INVALID_CHECKOUT_METADATA") from exc

    order_objects = list(
        Order.objects.select_for_update().filter(pk__in=parsed_ids).order_by("id")
    )
    orders_by_id = {str(order.pk): order for order in order_objects}

    if len(orders_by_id) != len(order_ids):
        raise Conflict("One or more checkout orders no longer exist.", code="CHECKOUT_ORDER_MISSING")

    verified_orders = [orders_by_id[order_id] for order_id in order_ids]

    for order in verified_orders:
        if order.brand_id != session.brand_id:
            raise Conflict("Checkout contains an order belonging to another brand.", code="CHECKOUT_BRAND_MISMATCH")

        if order.status != OrderStatus.AWAITING_PAYMENT:
            raise Conflict(
                "One or more checkout orders are no longer awaiting payment.",
                code="CHECKOUT_ORDER_NOT_PAYABLE",
            )

    if sum(order.brand_total_paise for order in verified_orders) != session.amount_paise:
        raise Conflict("Checkout order totals no longer match the payment.", code="CHECKOUT_TOTAL_MISMATCH")

    for order in verified_orders:
        order_services.system_event(
            order_id=order.id,
            event="fund",
            reason=f"Payment verified via {payment_id}",
        )

    session.status = "paid"
    session.save(update_fields=["status", "updated_at"])

    receipt_number = f"REC-{timezone.now().strftime('%Y%m%d')}-{secrets.token_hex(4).upper()}"

    return PaymentReceipt.objects.create(
        payment_session=session,
        order=session.order,
        brand=session.brand,
        gateway_payment_id=payment_id,
        gateway_signature=signature,
        amount_paise=session.amount_paise,
        currency=session.currency,
        status="succeeded",
        receipt_number=receipt_number,
    )

@transaction.atomic
def get_or_sync_wallet(creator: Creator) -> tuple[CreatorWallet, int]:
    """Get creator wallet and synchronize balances from completed orders."""
    wallet, _ = CreatorWallet.objects.select_for_update().get_or_create(creator=creator)

    # 1. Credit any completed or approved orders not yet credited
    completed_orders = Order.objects.filter(creator=creator, status__in=[OrderStatus.COMPLETED, OrderStatus.APPROVED])
    for ord_obj in completed_orders:
        already_credited = WalletTransaction.objects.filter(wallet=wallet, order=ord_obj, type="credit_order").exists()
        if not already_credited:
            wallet.balance_paise += ord_obj.creator_payout_paise
            wallet.total_earned_paise += ord_obj.creator_payout_paise
            wallet.save(update_fields=["balance_paise", "total_earned_paise", "updated_at"])
            WalletTransaction.objects.create(
                wallet=wallet,
                order=ord_obj,
                type="credit_order",
                amount_paise=ord_obj.creator_payout_paise,
                balance_after_paise=wallet.balance_paise,
                description=f"Earnings from {ord_obj.title}",
            )

    # 2. Compute pending payout (in escrow from active funded orders)
    pending_paise = (
        Order.objects.filter(
            creator=creator,
            status__in=[OrderStatus.FUNDED, OrderStatus.DRAFT_SUBMITTED, OrderStatus.REVISION_REQUESTED],
        ).aggregate(total=Sum("creator_payout_paise"))["total"]
        or 0
    )

    return wallet, pending_paise


@transaction.atomic
def request_withdrawal(
    *,
    creator: Creator,
    amount_paise: int,
    payout_method: str,
    payout_details: dict[str, Any],
) -> WithdrawalRequest:
    """Request payout of available wallet funds."""
    if not getattr(settings, "PAYOUTS_ENABLED", False):
        raise Conflict(
            "Withdrawals are currently unavailable.",
            code="PAYOUTS_DISABLED",
        )

    min_paise = getattr(settings, "MIN_WITHDRAWAL_PAISE", 50_000)
    if amount_paise < min_paise:
        raise DomainError(
            f"Minimum withdrawal amount is ₹{min_paise // 100} ({min_paise} paise).",
            code="WITHDRAWAL_BELOW_MINIMUM",
        )

    wallet, _ = get_or_sync_wallet(creator)
    wallet = CreatorWallet.objects.select_for_update().get(pk=wallet.pk)

    if amount_paise > wallet.balance_paise:
        raise DomainError("Insufficient available balance.", code="INSUFFICIENT_FUNDS")

    payout_method = payout_method.lower().strip()
    if payout_method not in ("upi", "bank_transfer"):
        raise DomainError("Invalid payout method. Choose 'upi' or 'bank_transfer'.", code="INVALID_PAYOUT_METHOD")

    if payout_method == "upi":
        upi_id = payout_details.get("upiId") or payout_details.get("upi_id") or ""
        if not upi_id or not re.match(UPI_REGEX, upi_id.strip()):
            raise DomainError("Enter a valid UPI ID (e.g., name@bank).", code="INVALID_UPI_ID")
        payout_details["upiId"] = upi_id.strip()

    elif payout_method == "bank_transfer":
        acc = payout_details.get("accountNumber") or payout_details.get("account_number") or ""
        ifsc = payout_details.get("ifscCode") or payout_details.get("ifsc_code") or ""
        if not acc or not ifsc:
            raise DomainError("Bank transfer requires accountNumber and ifscCode.", code="INVALID_BANK_DETAILS")

    wallet.balance_paise -= amount_paise
    wallet.total_withdrawn_paise += amount_paise
    wallet.save(update_fields=["balance_paise", "total_withdrawn_paise", "updated_at"])

    withdrawal = WithdrawalRequest.objects.create(
        creator=creator,
        wallet=wallet,
        amount_paise=amount_paise,
        currency="INR",
        payout_method=payout_method,
        payout_details=payout_details,
        status="pending",
    )

    WalletTransaction.objects.create(
        wallet=wallet,
        withdrawal=withdrawal,
        type="debit_withdrawal",
        amount_paise=amount_paise,
        balance_after_paise=wallet.balance_paise,
        description=f"Withdrawal request via {payout_method.upper()}",
    )

    notify(
        user_id=creator.user.id,
        type="withdrawal_requested",
        title="Withdrawal Request Received",
        body=f"Your withdrawal request of ₹{amount_paise // 100} has been submitted.",
        data={"withdrawalId": str(withdrawal.id), "amountPaise": amount_paise},
    )

    return withdrawal
