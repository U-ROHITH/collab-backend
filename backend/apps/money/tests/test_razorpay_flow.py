"""Unit and integration tests for Razorpay escrow checkout, verification, webhooks, and funding side effects."""

import hashlib
import hmac
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIClient

from apps.identity.models import UserStatus
from apps.messaging.models import Conversation
from apps.money.models import PaymentReceipt, PaymentSession
from apps.orders import services as order_services
from apps.orders.models import Order, OrderStatus
from tests.fixtures import USAGE_RIGHTS

pytestmark = pytest.mark.django_db

KEY_SECRET = "unit-test-key-secret"
WEBHOOK_SECRET = "unit-test-webhook-secret"
CHECKOUT_URL = "/v1/checkout/"
VERIFY_URL = "/v1/payments/verify/"
WEBHOOK_URL = "/v1/payments/webhook/"


@pytest.fixture(autouse=True)
def setup_razorpay_settings(settings: Any) -> None:
    settings.RAZORPAY_KEY_ID = "rzp_test_collabmarketplace"
    settings.RAZORPAY_KEY_SECRET = KEY_SECRET
    settings.RAZORPAY_WEBHOOK_SECRET = WEBHOOK_SECRET


def _sign(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _create_order(brand: APIClient, creator: Any, package: Any) -> Order:
    return order_services.create_order(
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


def test_checkout_response_includes_contract_aliases(brand: APIClient, creator: Any, package: Any) -> None:
    order = _create_order(brand, creator, package)
    resp = brand.post(CHECKOUT_URL, {"orderId": str(order.pk)}, format="json")
    assert resp.status_code == 201, resp.json()
    data = resp.json()["data"]

    # Verify both standard and Razorpay frontend aliases are present
    assert data["orderId"] == data["razorpayOrderId"]
    assert data["orderId"].startswith("order_rzp_")
    assert data["key"] == data["keyId"]
    assert data["amountPaise"] == order.brand_total_paise
    assert data["sessionId"] is not None
    assert str(order.pk) in data["orders"]


def test_checkout_rejects_suspended_creator(brand: APIClient, creator: Any, package: Any) -> None:
    order = _create_order(brand, creator, package)

    # Suspend the creator's user account
    creator.user.status = UserStatus.SUSPENDED
    creator.user.save(update_fields=["status"])

    resp = brand.post(CHECKOUT_URL, {"orderId": str(order.pk)}, format="json")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "PACKAGE_UNAVAILABLE"


def test_checkout_rejects_already_paid_order(brand: APIClient, creator: Any, package: Any) -> None:
    order = _create_order(brand, creator, package)
    order_services.system_event(order_id=order.pk, event="fund")

    resp = brand.post(CHECKOUT_URL, {"orderId": str(order.pk)}, format="json")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "ORDER_NOT_PAYABLE"


def test_checkout_calls_live_razorpay_api_when_configured(
    settings: Any, brand: APIClient, creator: Any, package: Any
) -> None:
    settings.RAZORPAY_KEY_ID = "rzp_live_abc123"
    settings.RAZORPAY_KEY_SECRET = "live_secret_xyz789"

    order = _create_order(brand, creator, package)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "order_live_rzp_424242"}

    with patch("requests.post", return_value=mock_response) as mock_post:
        resp = brand.post(CHECKOUT_URL, {"orderId": str(order.pk)}, format="json")
        assert resp.status_code == 201, resp.json()
        data = resp.json()["data"]
        assert data["orderId"] == "order_live_rzp_424242"
        assert data["razorpayOrderId"] == "order_live_rzp_424242"

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "https://api.razorpay.com/v1/orders"
        assert kwargs["auth"] == ("rzp_live_abc123", "live_secret_xyz789")
        assert kwargs["json"]["amount"] == order.brand_total_paise
        assert kwargs["json"]["currency"] == "INR"
        assert kwargs["json"]["notes"]["linkloomOrderId"] == str(order.pk)


def test_funding_order_auto_creates_conversation(brand: APIClient, creator: Any, package: Any) -> None:
    order = _create_order(brand, creator, package)

    # Initially no conversation exists
    assert not Conversation.objects.filter(brand=brand.user.brand, creator=creator.user.creator).exists()

    # Trigger funding
    order_services.system_event(order_id=order.pk, event="fund")

    # Conversation must now be created
    convo = Conversation.objects.filter(brand=brand.user.brand, creator=creator.user.creator).first()
    assert convo is not None


def test_webhook_payment_captured_funds_order_and_creates_receipt(brand: APIClient, creator: Any, package: Any) -> None:
    order = _create_order(brand, creator, package)
    chk_resp = brand.post(CHECKOUT_URL, {"orderId": str(order.pk)}, format="json")
    rzp_order_id = chk_resp.json()["data"]["orderId"]

    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_captured_111",
                    "order_id": rzp_order_id,
                    "amount": order.brand_total_paise,
                }
            }
        },
    }
    raw = json.dumps(payload).encode()
    sig = _sign(WEBHOOK_SECRET, raw)

    resp = APIClient().post(
        WEBHOOK_URL,
        raw,
        content_type="application/json",
        HTTP_X_RAZORPAY_SIGNATURE=sig,
    )
    assert resp.status_code == 200, resp.json()
    assert resp.json()["data"]["paymentId"] == "pay_captured_111"
    assert resp.json()["data"]["status"] == "succeeded"

    order.refresh_from_db()
    assert order.status == OrderStatus.FUNDED
    assert order.funded_at is not None

    session = PaymentSession.objects.get(gateway_order_id=rzp_order_id)
    assert session.status == "paid"


def test_webhook_payment_failed_records_failure_and_leaves_order_awaiting(
    brand: APIClient, creator: Any, package: Any
) -> None:
    order = _create_order(brand, creator, package)
    chk_resp = brand.post(CHECKOUT_URL, {"orderId": str(order.pk)}, format="json")
    rzp_order_id = chk_resp.json()["data"]["orderId"]

    payload = {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_failed_222",
                    "order_id": rzp_order_id,
                    "error_code": "BAD_REQUEST_ERROR",
                    "error_description": "Payment was declined by bank",
                }
            }
        },
    }
    raw = json.dumps(payload).encode()
    sig = _sign(WEBHOOK_SECRET, raw)

    resp = APIClient().post(
        WEBHOOK_URL,
        raw,
        content_type="application/json",
        HTTP_X_RAZORPAY_SIGNATURE=sig,
    )
    assert resp.status_code == 200, resp.json()
    assert resp.json()["data"]["event"] == "payment.failed"

    order.refresh_from_db()
    assert order.status == OrderStatus.AWAITING_PAYMENT

    session = PaymentSession.objects.get(gateway_order_id=rzp_order_id)
    assert session.status == "created"
    assert session.metadata["last_payment_failed"]["payment_id"] == "pay_failed_222"


def test_webhook_refund_processed_transitions_order_and_receipt(brand: APIClient, creator: Any, package: Any) -> None:
    order = _create_order(brand, creator, package)
    chk_resp = brand.post(CHECKOUT_URL, {"orderId": str(order.pk)}, format="json")
    rzp_order_id = chk_resp.json()["data"]["orderId"]

    # First fund the order
    sig = _sign(KEY_SECRET, f"{rzp_order_id}|pay_to_refund".encode())
    brand.post(
        VERIFY_URL,
        {"razorpayOrderId": rzp_order_id, "razorpayPaymentId": "pay_to_refund", "razorpaySignature": sig},
        format="json",
    )
    order.refresh_from_db()
    assert order.status == OrderStatus.FUNDED

    # Creator cancels the funded order, moving it to refund_pending
    order_services.cancel(actor=creator.user, order_id=order.pk, reason="Creator emergency")
    order.refresh_from_db()
    assert order.status == OrderStatus.REFUND_PENDING

    # Now Razorpay sends refund.processed
    payload = {
        "event": "refund.processed",
        "payload": {
            "refund": {
                "entity": {
                    "id": "rfnd_999",
                    "payment_id": "pay_to_refund",
                    "amount": order.brand_total_paise,
                    "status": "processed",
                }
            },
            "payment": {
                "entity": {
                    "id": "pay_to_refund",
                    "order_id": rzp_order_id,
                }
            },
        },
    }
    raw = json.dumps(payload).encode()
    webhook_sig = _sign(WEBHOOK_SECRET, raw)

    resp = APIClient().post(
        WEBHOOK_URL,
        raw,
        content_type="application/json",
        HTTP_X_RAZORPAY_SIGNATURE=webhook_sig,
    )
    assert resp.status_code == 200, resp.json()
    assert resp.json()["data"]["event"] == "refund.processed"

    order.refresh_from_db()
    assert order.status == OrderStatus.REFUNDED

    receipt = PaymentReceipt.objects.get(gateway_payment_id="pay_to_refund")
    assert receipt.status == "refunded"

    session = PaymentSession.objects.get(gateway_order_id=rzp_order_id)
    assert session.status == "refunded"


def test_webhook_rejects_invalid_signature(brand: APIClient) -> None:
    payload = {"event": "payment.captured", "payload": {}}
    raw = json.dumps(payload).encode()

    resp = APIClient().post(
        WEBHOOK_URL,
        raw,
        content_type="application/json",
        HTTP_X_RAZORPAY_SIGNATURE="invalid_signature_hex",
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_SIGNATURE"
