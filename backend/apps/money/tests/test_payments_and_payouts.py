from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.brands.models import CartItem
from apps.creators.models import CreatorPackage
from apps.money.models import CreatorWallet, PaymentReceipt, PaymentSession, WithdrawalRequest
from apps.orders import services as order_services
from apps.orders.models import OrderStatus
from tests.fixtures import USAGE_RIGHTS

pytestmark = pytest.mark.django_db


def test_checkout_initialization_with_order(brand: APIClient, creator: Any, package: CreatorPackage) -> None:
    # 1. Create an order awaiting payment
    order = order_services.create_order(
        actor=brand.user,
        brand=brand.user.brand,
        creator=creator.user.creator,
        source_type="package",
        source_id=package.pk,
        title=package.title,
        deliverables=package.deliverables,
        brief="Test brief for checkout",
        subtotal_paise=package.price_paise,
        delivery_days=package.delivery_days,
        usage_rights=USAGE_RIGHTS,
    )
    assert order.status == OrderStatus.AWAITING_PAYMENT

    # 2. Brand initializes checkout for this order
    resp = brand.post(
        "/v1/checkout/",
        {"orderId": str(order.id)},
        format="json",
    )
    assert resp.status_code == 201, resp.json()
    data = resp.json()["data"]
    assert data["sessionId"] is not None
    assert data["gateway"] == "razorpay"
    assert data["orderId"].startswith("order_rzp_")
    assert data["amountPaise"] == order.brand_total_paise
    assert data["currency"] == "INR"
    assert data["key"] is not None
    assert data["brand"]["name"] == brand.user.brand.name
    assert str(order.id) in data["orders"]

    # Verify session persisted
    session = PaymentSession.objects.get(gateway_order_id=data["orderId"])
    assert session.amount_paise == order.brand_total_paise
    assert session.status == "created"


def test_checkout_initialization_from_cart(brand: APIClient, creator: Any, package: CreatorPackage) -> None:
    # Add package to cart
    CartItem.objects.create(
        brand=brand.user.brand,
        package=package,
        brief="Cart item brief",
    )

    resp = brand.post(
        "/v1/checkout/",
        {"cart": True},
        format="json",
    )
    assert resp.status_code == 201, resp.json()
    data = resp.json()["data"]
    assert data["gateway"] == "razorpay"
    assert data["amountPaise"] > 0

    # Cart should now be empty (converted to order)
    assert CartItem.objects.filter(brand=brand.user.brand).count() == 0


def test_payment_verify_transitions_order_and_creates_receipt(
    brand: APIClient, creator: Any, package: CreatorPackage
) -> None:
    # 1. Create order and payment session
    order = order_services.create_order(
        actor=brand.user,
        brand=brand.user.brand,
        creator=creator.user.creator,
        source_type="package",
        source_id=package.pk,
        title=package.title,
        deliverables=package.deliverables,
        brief="Test brief",
        subtotal_paise=package.price_paise,
        delivery_days=package.delivery_days,
        usage_rights=USAGE_RIGHTS,
    )
    chk_resp = brand.post("/v1/checkout/", {"orderId": str(order.id)}, format="json")
    gateway_order_id = chk_resp.json()["data"]["orderId"]

    # 2. Verify payment success
    verify_resp = brand.post(
        "/v1/payments/verify/",
        {
            "orderId": gateway_order_id,
            "paymentId": "pay_test_12345678",
            "signature": "test_signature_hex",
        },
        format="json",
    )
    assert verify_resp.status_code == 200, verify_resp.json()
    receipt_data = verify_resp.json()["data"]
    assert receipt_data["receiptNumber"].startswith("REC-")
    assert receipt_data["status"] == "succeeded"
    assert receipt_data["paymentId"] == "pay_test_12345678"

    # 3. Verify order transitioned to FUNDED
    order.refresh_from_db()
    assert order.status == OrderStatus.FUNDED
    assert order.funded_at is not None

    # Verify session marked as paid
    session = PaymentSession.objects.get(gateway_order_id=gateway_order_id)
    assert session.status == "paid"

    # Verify receipt persisted
    receipt = PaymentReceipt.objects.get(gateway_payment_id="pay_test_12345678")
    assert receipt.order_id == order.id


def test_payment_webhook_handler(brand: APIClient, creator: Any, package: CreatorPackage) -> None:
    order = order_services.create_order(
        actor=brand.user,
        brand=brand.user.brand,
        creator=creator.user.creator,
        source_type="package",
        source_id=package.pk,
        title=package.title,
        deliverables=package.deliverables,
        brief="Webhook brief",
        subtotal_paise=package.price_paise,
        delivery_days=package.delivery_days,
        usage_rights=USAGE_RIGHTS,
    )
    chk_resp = brand.post("/v1/checkout/", {"orderId": str(order.id)}, format="json")
    gateway_order_id = chk_resp.json()["data"]["orderId"]

    client = APIClient()  # Webhook is unauthenticated / gateway-called
    webhook_payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_webhook_99999",
                    "order_id": gateway_order_id,
                    "amount": order.brand_total_paise,
                }
            }
        },
    }
    resp = client.post("/v1/payments/verify/", webhook_payload, format="json")
    assert resp.status_code == 200, resp.json()
    order.refresh_from_db()
    assert order.status == OrderStatus.FUNDED


def test_creator_wallet_balance_and_earnings(brand: APIClient, creator: Any, package: CreatorPackage) -> None:
    # 1. Initially wallet is empty
    wallet_resp = creator.get("/v1/creators/me/wallet/")
    assert wallet_resp.status_code == 200
    wallet_data = wallet_resp.json()["data"]
    assert wallet_data["balance"]["amount"] == 0
    assert wallet_data["pendingBalance"]["amount"] == 0

    # 2. An order is funded -> pending balance increases
    order = order_services.create_order(
        actor=brand.user,
        brand=brand.user.brand,
        creator=creator.user.creator,
        source_type="package",
        source_id=package.pk,
        title=package.title,
        deliverables=package.deliverables,
        brief="Test wallet brief",
        subtotal_paise=package.price_paise,
        delivery_days=package.delivery_days,
        usage_rights=USAGE_RIGHTS,
    )
    order_services.system_event(order_id=order.id, event="fund")

    wallet_resp2 = creator.get("/v1/creators/me/wallet/")
    wallet_data2 = wallet_resp2.json()["data"]
    assert wallet_data2["balance"]["amount"] == 0
    assert wallet_data2["pendingBalance"]["amount"] == order.creator_payout_paise

    # 3. Order is submitted, approved, and completed -> earnings credited to available balance
    order_services.submit_deliverable(
        actor=creator.user, order_id=order.id, content_url="https://example.com/draft", note="Done!"
    )
    order_services.approve(actor=brand.user, order_id=order.id)
    order_services.system_event(order_id=order.id, event="complete")

    wallet_resp3 = creator.get("/v1/creators/me/wallet/")
    wallet_data3 = wallet_resp3.json()["data"]
    assert wallet_data3["balance"]["amount"] == order.creator_payout_paise
    assert wallet_data3["availableBalance"]["amount"] == order.creator_payout_paise
    assert wallet_data3["pendingBalance"]["amount"] == 0
    assert wallet_data3["totalEarned"]["amount"] == order.creator_payout_paise
    assert len(wallet_data3["transactions"]) >= 1
    assert wallet_data3["transactions"][0]["type"] == "credit_order"


def test_creator_withdrawal_validations_and_processing(creator: Any) -> None:
    # Setup wallet with available balance of ₹2,000 (200,000 paise)
    wallet, _ = CreatorWallet.objects.get_or_create(creator=creator.user.creator)
    wallet.balance_paise = 200_000
    wallet.total_earned_paise = 200_000
    wallet.save()

    # 1. Withdrawal below minimum threshold (₹100 = 10,000 paise < ₹500 = 50,000 paise)
    low_resp = creator.post(
        "/v1/creators/me/withdrawals/",
        {
            "amountPaise": 10_000,
            "payoutMethod": "upi",
            "payoutDetails": {"upiId": "creator@okaxis"},
        },
        format="json",
    )
    assert low_resp.status_code == 422
    assert low_resp.json()["error"]["code"] == "WITHDRAWAL_BELOW_MINIMUM"

    # 2. Withdrawal exceeding balance (₹5,000 = 500,000 paise > 200,000)
    high_resp = creator.post(
        "/v1/creators/me/withdrawals/",
        {
            "amountPaise": 500_000,
            "payoutMethod": "upi",
            "payoutDetails": {"upiId": "creator@okaxis"},
        },
        format="json",
    )
    assert high_resp.status_code == 422
    assert high_resp.json()["error"]["code"] == "INSUFFICIENT_FUNDS"

    # 3. Invalid UPI ID
    invalid_upi = creator.post(
        "/v1/creators/me/withdrawals/",
        {
            "amountPaise": 50_000,
            "payoutMethod": "upi",
            "payoutDetails": {"upiId": "invalid_upi_format"},
        },
        format="json",
    )
    assert invalid_upi.status_code == 422
    assert invalid_upi.json()["error"]["code"] == "INVALID_UPI_ID"

    # 4. Successful UPI withdrawal of ₹1,000 (100,000 paise)
    ok_resp = creator.post(
        "/v1/creators/me/withdrawals/",
        {
            "amountPaise": 100_000,
            "payoutMethod": "upi",
            "payoutDetails": {"upiId": "creator@okaxis"},
        },
        format="json",
    )
    assert ok_resp.status_code == 201, ok_resp.json()
    w_data = ok_resp.json()["data"]
    assert w_data["amount"]["amount"] == 100_000
    assert w_data["status"] == "pending"

    # Verify wallet balance deducted
    wallet.refresh_from_db()
    assert wallet.balance_paise == 100_000
    assert wallet.total_withdrawn_paise == 100_000

    # Verify withdrawal record in DB
    withdrawal = WithdrawalRequest.objects.get(pk=w_data["id"])
    assert withdrawal.payout_method == "upi"
    assert withdrawal.payout_details["upiId"] == "creator@okaxis"


def test_unauthorized_access(brand: APIClient, creator: Any) -> None:
    # Creator cannot initiate brand checkout
    assert creator.post("/v1/checkout/", {"cart": True}).status_code == 403

    # Brand cannot access creator wallet
    assert brand.get("/v1/creators/me/wallet/").status_code == 403

    # Brand cannot request creator withdrawal
    assert brand.post("/v1/creators/me/withdrawals/", {"amountPaise": 50000}).status_code == 403

    # Anonymous access denied
    anon = APIClient()
    assert anon.post("/v1/checkout/", {}).status_code == 401
    assert anon.get("/v1/creators/me/wallet/").status_code == 401
    assert anon.post("/v1/creators/me/withdrawals/", {}).status_code == 401
