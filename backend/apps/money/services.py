import re
import secrets
import uuid
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

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
from tests.fixtures import USAGE_RIGHTS

UPI_REGEX = r"^[\w\.\-_]{2,256}@[a-zA-Z]{2,64}$"


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
    """Initialize a payment/checkout session for an order, package, or cart items."""
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
        # Check out cart items
        cart_count = CartItem.objects.filter(brand=brand).count()
        if cart_count > 0:
            orders = checkout_cart(brand=brand, actor=actor, usage_rights=USAGE_RIGHTS)
        else:
            # Check if there is already an order awaiting payment
            pending_orders = list(
                Order.objects.filter(brand=brand, status=OrderStatus.AWAITING_PAYMENT).order_by("-created_at")[:1]
            )
            if pending_orders:
                orders = pending_orders
            else:
                raise DomainError("No items or orders specified for checkout.", code="CHECKOUT_EMPTY")

    total_amount_paise = sum(o.brand_total_paise for o in orders)
    gateway_order_id = f"order_rzp_{uuid.uuid4().hex[:16]}"

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
    session = PaymentSession.objects.select_for_update().filter(gateway_order_id=gateway_order_id).first()

    # If not found by gateway_order_id, check if caller passed an order ID directly
    if session is None:
        try:
            order_uuid = UUID(gateway_order_id)
            order = Order.objects.filter(pk=order_uuid).first()
            if order:
                session = PaymentSession.objects.filter(order=order).first()
                if session is None:
                    session = PaymentSession.objects.create(
                        brand=order.brand,
                        order=order,
                        gateway="razorpay",
                        gateway_order_id=f"order_rzp_{uuid.uuid4().hex[:16]}",
                        amount_paise=order.brand_total_paise,
                        currency="INR",
                    )
        except (ValueError, TypeError):
            pass

    if session is None:
        raise NotFound("Payment session not found.")

    # If already paid, return existing receipt
    existing_receipt = session.receipts.first()
    if existing_receipt:
        return existing_receipt

    # Transition order(s) to FUNDED
    order_ids = session.metadata.get("orderIds", [])
    if session.order_id and str(session.order_id) not in order_ids:
        order_ids.append(str(session.order_id))

    for oid in order_ids:
        try:
            ord_obj = Order.objects.select_for_update().get(pk=UUID(oid))
            if ord_obj.status == OrderStatus.AWAITING_PAYMENT:
                order_services.system_event(
                    order_id=ord_obj.id,
                    event="fund",
                    reason=f"Payment verified via {payment_id}",
                )
        except Order.DoesNotExist:
            continue

    session.status = "paid"
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
        status="succeeded",
        receipt_number=receipt_number,
    )

    return receipt


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
