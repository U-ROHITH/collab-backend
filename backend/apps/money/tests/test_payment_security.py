"""POST /v1/payments/verify/ must never mark an order funded without proof of payment from Razorpay."""

import hashlib
import hmac
import json
from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.creators.models import CreatorPackage
from apps.orders import services as order_services
from apps.orders.models import Order, OrderStatus
from tests.fixtures import USAGE_RIGHTS

pytestmark = pytest.mark.django_db

KEY_SECRET = "unit-test-key-secret"
WEBHOOK_SECRET = "unit-test-webhook-secret"
VERIFY = "/v1/payments/verify/"


@pytest.fixture(autouse=True)
def razorpay_secrets(settings: Any) -> None:
    settings.RAZORPAY_KEY_SECRET = KEY_SECRET
    settings.RAZORPAY_WEBHOOK_SECRET = WEBHOOK_SECRET


def _sign(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _order_with_session(brand: APIClient, creator: Any, package: CreatorPackage) -> tuple[Order, str]:
    order = order_services.create_order(
        actor=brand.user,
        brand=brand.user.brand,
        creator=creator.user.creator,
        source_type="package",
        source_id=package.pk,
        title=package.title,
        deliverables=package.deliverables,
        brief="Brief",
        subtotal_paise=package.price_paise,
        delivery_days=package.delivery_days,
        usage_rights=USAGE_RIGHTS,
    )
    resp = brand.post("/v1/checkout/", {"orderId": str(order.id)}, format="json")
    assert resp.status_code == 201, resp.json()
    return order, resp.json()["data"]["orderId"]


def _status(order: Order) -> str:
    order.refresh_from_db()
    return order.status


def test_anonymous_caller_cannot_fund_an_order_by_its_id(brand: APIClient, creator: Any, package: Any) -> None:
    order, _ = _order_with_session(brand, creator, package)
    resp = APIClient().post(VERIFY, {"orderId": str(order.id), "paymentId": "pay_fake"}, format="json")
    assert resp.status_code in (400, 401, 403, 404)
    assert _status(order) == OrderStatus.AWAITING_PAYMENT


def test_brand_cannot_fund_with_a_wrong_signature(brand: APIClient, creator: Any, package: Any) -> None:
    order, rzp_order = _order_with_session(brand, creator, package)
    body = {"razorpayOrderId": rzp_order, "razorpayPaymentId": "pay_1", "razorpaySignature": "deadbeef"}
    resp = brand.post(VERIFY, body, format="json")
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "INVALID_SIGNATURE"
    assert _status(order) == OrderStatus.AWAITING_PAYMENT


def test_brand_cannot_fund_with_a_blank_signature(brand: APIClient, creator: Any, package: Any) -> None:
    order, rzp_order = _order_with_session(brand, creator, package)
    resp = brand.post(VERIFY, {"orderId": rzp_order, "paymentId": "pay_1", "signature": ""}, format="json")
    assert resp.status_code == 400
    assert _status(order) == OrderStatus.AWAITING_PAYMENT


def test_valid_razorpay_signature_funds_the_order(brand: APIClient, creator: Any, package: Any) -> None:
    order, rzp_order = _order_with_session(brand, creator, package)
    sig = _sign(KEY_SECRET, f"{rzp_order}|pay_1".encode())
    body = {"razorpayOrderId": rzp_order, "razorpayPaymentId": "pay_1", "razorpaySignature": sig}
    resp = brand.post(VERIFY, body, format="json")
    assert resp.status_code == 200, resp.json()
    assert _status(order) == OrderStatus.FUNDED


def test_another_brand_cannot_confirm_someone_elses_payment(
    brand: APIClient, make_brand: Any, creator: Any, package: Any
) -> None:
    order, rzp_order = _order_with_session(brand, creator, package)
    sig = _sign(KEY_SECRET, f"{rzp_order}|pay_1".encode())
    other = make_brand("Other Co")
    body = {"razorpayOrderId": rzp_order, "razorpayPaymentId": "pay_1", "razorpaySignature": sig}
    resp = other.post(VERIFY, body, format="json")
    assert resp.status_code in (403, 404)
    assert _status(order) == OrderStatus.AWAITING_PAYMENT


def _webhook(rzp_order: str) -> bytes:
    payload = {
        "event": "payment.captured",
        "payload": {"payment": {"entity": {"id": "pay_wh", "order_id": rzp_order}}},
    }
    return json.dumps(payload).encode()


def test_unsigned_webhook_is_rejected(brand: APIClient, creator: Any, package: Any) -> None:
    order, rzp_order = _order_with_session(brand, creator, package)
    resp = APIClient().post(VERIFY, _webhook(rzp_order), content_type="application/json")
    assert resp.status_code == 400
    assert _status(order) == OrderStatus.AWAITING_PAYMENT


def test_webhook_signed_by_razorpay_funds_the_order(brand: APIClient, creator: Any, package: Any) -> None:
    order, rzp_order = _order_with_session(brand, creator, package)
    raw = _webhook(rzp_order)
    resp = APIClient().post(
        VERIFY, raw, content_type="application/json", HTTP_X_RAZORPAY_SIGNATURE=_sign(WEBHOOK_SECRET, raw)
    )
    assert resp.status_code == 200, resp.json()
    assert _status(order) == OrderStatus.FUNDED


def test_payments_refused_when_secrets_are_the_built_in_placeholders(
    settings: Any, brand: APIClient, creator: Any, package: Any
) -> None:
    order, rzp_order = _order_with_session(brand, creator, package)
    settings.RAZORPAY_KEY_SECRET = "test_secret_collab"  # the settings default: public, so forgeable
    sig = _sign("test_secret_collab", f"{rzp_order}|pay_1".encode())
    body = {"razorpayOrderId": rzp_order, "razorpayPaymentId": "pay_1", "razorpaySignature": sig}
    resp = brand.post(VERIFY, body, format="json")
    assert resp.status_code == 503 and resp.json()["error"]["code"] == "PAYMENTS_NOT_CONFIGURED"
    assert _status(order) == OrderStatus.AWAITING_PAYMENT
