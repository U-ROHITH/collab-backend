import logging
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

from apps.brands.models import Brand, CartItem
from apps.brands.services import checkout_cart
from apps.core.exceptions import Conflict, DomainError, NotFound
from apps.creators.models import Creator, CreatorPackage
from apps.identity.models import User, UserStatus
from apps.money.models import (
    CreatorWallet,
    PaymentReceipt,
    PaymentSession,
    WalletTransaction,
    WithdrawalRequest,
)
from apps.money.signatures import are_payments_configured
from apps.notifications.services import notify
from apps.orders import services as order_services
from apps.orders.models import Order, OrderStatus

logger = logging.getLogger(__name__)

UPI_REGEX = r"^[\w\.\-_]{2,256}@[a-zA-Z]{2,64}$"


def create_razorpay_order(*, amount_paise: int, receipt_id: str, order_id: str) -> str:
    """Create order in Razorpay if live keys are configured, otherwise return mock ID."""
    if not are_payments_configured():
        return f"order_rzp_{uuid.uuid4().hex[:16]}"

    key_id = getattr(settings, "RAZORPAY_KEY_ID", "")
    key_secret = getattr(settings, "RAZORPAY_KEY_SECRET", "")
    payload = {
        "amount": amount_paise,
        "currency": "INR",
        "receipt": receipt_id[:40],
        "notes": {"linkloomOrderId": order_id},
    }
    try:
        resp = requests.post(
            "https://api.razorpay.com/v1/orders",
            auth=(key_id, key_secret),
            json=payload,
            timeout=10,
        )
    except Exception as exc:
        logger.error("Failed to connect to Razorpay: %s", exc)
        raise DomainError("Unable to reach payment gateway.", code="PAYMENT_GATEWAY_ERROR") from exc

    if resp.status_code not in (200, 201):
        logger.error("Razorpay order creation failed: %s %s", resp.status_code, resp.text)
        raise DomainError("Payment gateway order creation failed.", code="PAYMENT_GATEWAY_ERROR")

    data = resp.json()
    rzp_id = data.get("id")
    if not rzp_id:
        raise DomainError("Payment gateway did not return an order ID.", code="PAYMENT_GATEWAY_ERROR")
    return rzp_id


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
    usage_rights: dict[str, Any] | None = None,
) -> tuple[PaymentSession, list[Order]]:
    """Initialize a payment/checkout session for an order, package, or cart items."""
    orders: list[Order] = []

    if order_id:
        try:
            order = Order.objects.select_for_update().get(pk=order_id, brand=brand)
        except Order.DoesNotExist:
            raise NotFound("Order not found.") from None
        if order.status != OrderStatus.AWAITING_PAYMENT:
            raise Conflict("Order is already funded or processed.", code="ORDER_NOT_PAYABLE")

        # Guard: check creator status
        creator = order.creator
        creator_user = getattr(creator, "user", None)
        is_active_creator = creator_user is None or creator_user.status == UserStatus.ACTIVE
        if not creator.is_approved or not is_active_creator:
            raise Conflict("This creator is no longer available.", code="PACKAGE_UNAVAILABLE")

        # Paying the same order twice must not be possible: reuse its open session.
        open_session = PaymentSession.objects.filter(order=order, status="created").first()
        if open_session is not None:
            return open_session, [order]
        orders.append(order)

    elif package_id:
        from apps.creators.selectors import public_creators

        package = (
            CreatorPackage.objects.filter(pk=package_id, active=True, creator__in=public_creators())
            .select_related("creator")
            .first()
        )
        if package is None:
            raise NotFound("Package not found or inactive.")

        order = order_services.create_order(
            actor=actor,
            brand=brand,
            creator=package.creator,
            source_type="package",
            source_id=uuid.uuid4(),  # one order per purchase (orders are unique per source)
            title=package.title,
            deliverables=package.deliverables,
            brief="Direct package checkout",
            subtotal_paise=package.price_paise,
            delivery_days=package.delivery_days,
            usage_rights=usage_rights,
        )
        orders.append(order)

    else:
        # Check out cart items
        cart_count = CartItem.objects.filter(brand=brand).count()
        if cart_count > 0:
            orders = checkout_cart(brand=brand, actor=actor, usage_rights=usage_rights or {})
        else:
            # Check if there is already an order awaiting payment
            pending_orders = list(
                Order.objects.filter(brand=brand, status=OrderStatus.AWAITING_PAYMENT).order_by("-created_at")[:1]
            )
            if pending_orders:
                orders = pending_orders
                creator = orders[0].creator
                creator_user = getattr(creator, "user", None)
                is_active_creator = creator_user is None or creator_user.status == UserStatus.ACTIVE
                if not creator.is_approved or not is_active_creator:
                    raise Conflict("This creator is no longer available.", code="PACKAGE_UNAVAILABLE")
                open_session = PaymentSession.objects.filter(order=orders[0], status="created").first()
                if open_session is not None:
                    return open_session, orders
            else:
                raise DomainError("No items or orders specified for checkout.", code="CHECKOUT_EMPTY")

    total_amount_paise = sum(o.brand_total_paise for o in orders)
    primary_order_id = str(orders[0].id) if orders else str(uuid.uuid4())
    gateway_order_id = create_razorpay_order(
        amount_paise=total_amount_paise,
        receipt_id=primary_order_id,
        order_id=primary_order_id,
    )

    session = PaymentSession.objects.create(
        brand=brand,
        order=orders[0] if orders else None,
        gateway="razorpay",
        gateway_order_id=gateway_order_id,
        amount_paise=total_amount_paise,
        currency=currency,
        metadata={"orderIds": [str(o.id) for o in orders]},
    )
    return session, orders


@transaction.atomic
def verify_payment(
    *,
    gateway_order_id: str,
    payment_id: str,
    signature: str = "",
    brand: Brand | None = None,
    metadata: dict[str, Any] | None = None,
) -> PaymentReceipt:
    """Confirm payment success, fund the order(s), and generate payment receipt."""
    """The caller must already have checked Razorpay's signature (apps.money.signatures). `brand` is the paying
    brand for browser confirmations (None for the signed webhook): it must own the session."""
    session = PaymentSession.objects.select_for_update().filter(gateway_order_id=gateway_order_id).first()
    if session is None or (brand is not None and session.brand_id != brand.pk):
        raise NotFound("Payment session not found.")

    # If already paid, return existing receipt
    existing_receipt = session.receipts.first()
    if existing_receipt:
        return existing_receipt

    # Transition order(s) to FUNDED
    order_ids = session.metadata.get("orderIds", [])
    if session.order_id and str(session.order_id) not in order_ids:
        order_ids.append(str(session.order_id))

    not_fundable: list[str] = []
    for oid in order_ids:
        ord_obj = Order.objects.select_for_update().filter(pk=UUID(oid)).first()
        if ord_obj is not None and ord_obj.status == OrderStatus.AWAITING_PAYMENT:
            order_services.system_event(order_id=ord_obj.id, event="fund", reason=f"Payment verified via {payment_id}")
        else:
            not_fundable.append(oid)

    # Money arrived for orders that can't be funded any more (e.g. cancelled while the brand was paying):
    # record it as owed back, never as a plain success.
    outcome = "needs_refund" if not_fundable else "paid"
    if not_fundable:
        logger.error(
            "Payment %s captured for %d order(s) that can't be funded (%s); needs refund",
            payment_id,
            len(not_fundable),
            ", ".join(not_fundable),
        )
    session.status = outcome
    session.save(update_fields=["status", "updated_at"])

    receipt_number = f"REC-{timezone.now().strftime('%Y%m%d')}-{secrets.token_hex(4).upper()}"
    receipt = PaymentReceipt.objects.create(
        payment_session=session,
        order=session.order,
        brand=session.brand,
        gateway_payment_id=payment_id,
        gateway_signature=signature,
        amount_paise=session.amount_paise,
        currency=session.currency,
        status="succeeded" if outcome == "paid" else "needs_refund",
        receipt_number=receipt_number,
    )

    return receipt


def handle_payment_failed(*, gateway_order_id: str, payment_id: str, payload: dict[str, Any]) -> None:
    """Log and track payment failures from Razorpay webhooks."""
    logger.warning("Razorpay payment %s failed for gateway order %s: %s", payment_id, gateway_order_id, payload)
    if gateway_order_id:
        session = PaymentSession.objects.filter(gateway_order_id=gateway_order_id).first()
        if session and session.status == "created":
            metadata = session.metadata or {}
            metadata["last_payment_failed"] = {
                "payment_id": payment_id,
                "error_code": payload.get("error_code"),
                "error_description": payload.get("error_description"),
                "failed_at": timezone.now().isoformat(),
            }
            session.metadata = metadata
            session.save(update_fields=["metadata", "updated_at"])


@transaction.atomic
def handle_refund_processed(*, refund_id: str, payment_id: str, payload: dict[str, Any] | None = None) -> None:
    """Process a refund event from Razorpay webhook, updating receipt and order status."""
    logger.info("Razorpay refund %s processed for payment %s", refund_id, payment_id)
    receipt = PaymentReceipt.objects.select_for_update().filter(gateway_payment_id=payment_id).first()
    if receipt:
        receipt.status = "refunded"
        receipt.save(update_fields=["status", "updated_at"])
        session = receipt.payment_session
        if session:
            session.status = "refunded"
            session.save(update_fields=["status", "updated_at"])

        order_ids = session.metadata.get("orderIds", []) if session else []
        if receipt.order_id and str(receipt.order_id) not in order_ids:
            order_ids.append(str(receipt.order_id))
        for oid in order_ids:
            ord_obj = Order.objects.select_for_update().filter(pk=UUID(oid)).first()
            if ord_obj and ord_obj.status == OrderStatus.REFUND_PENDING:
                order_services.system_event(
                    order_id=ord_obj.id,
                    event="refund_completed",
                    reason=f"Refund completed via Razorpay {refund_id}",
                )


@transaction.atomic
def get_or_sync_wallet(creator: Creator) -> tuple[CreatorWallet, int]:
    """Get creator wallet and synchronize balances from completed orders."""
    wallet, _ = CreatorWallet.objects.select_for_update().get_or_create(creator=creator)

    # 1. Credit approved/completed orders not yet credited: one query to find them, one insert for all.
    credited = WalletTransaction.objects.filter(wallet=wallet, type="credit_order").values("order_id")
    new_earnings = list(
        Order.objects.filter(creator=creator, status__in=[OrderStatus.COMPLETED, OrderStatus.APPROVED])
        .exclude(pk__in=credited)
        .order_by("created_at")
    )
    if new_earnings:
        credits = []
        for order in new_earnings:
            wallet.balance_paise += order.creator_payout_paise
            wallet.total_earned_paise += order.creator_payout_paise
            credits.append(
                WalletTransaction(
                    wallet=wallet,
                    order=order,
                    type="credit_order",
                    amount_paise=order.creator_payout_paise,
                    balance_after_paise=wallet.balance_paise,
                    description=f"Earnings from {order.title}",
                )
            )
        WalletTransaction.objects.bulk_create(credits)
        wallet.save(update_fields=["balance_paise", "total_earned_paise", "updated_at"])

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
