"""Checkout and payment rules: no invented terms, every purchase its own order, payments never lost silently."""

import hashlib
import hmac
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.creators.models import ApprovalStatus, CreatorPackage
from apps.money.models import PaymentReceipt, PaymentSession
from apps.orders import services as order_services
from apps.orders.models import Order, OrderStatus
from tests.fixtures import USAGE_RIGHTS

pytestmark = pytest.mark.django_db

KEY_SECRET = "unit-test-key-secret"
CHECKOUT = "/v1/checkout/"


@pytest.fixture(autouse=True)
def razorpay_secret(settings: Any) -> None:
    settings.RAZORPAY_KEY_SECRET = KEY_SECRET


def _pay(brand: APIClient, rzp_order: str, payment_id: str = "pay_1") -> Any:
    sig = hmac.new(KEY_SECRET.encode(), f"{rzp_order}|{payment_id}".encode(), hashlib.sha256).hexdigest()
    body = {"razorpayOrderId": rzp_order, "razorpayPaymentId": payment_id, "razorpaySignature": sig}
    return brand.post("/v1/payments/verify/", body, format="json")


def test_package_checkout_needs_the_brands_usage_rights(brand: APIClient, package: CreatorPackage) -> None:
    resp = brand.post(CHECKOUT, {"packageId": str(package.pk)}, format="json")
    assert resp.status_code == 400 and "usageRights" in resp.json()["error"]["details"]
    ok = brand.post(CHECKOUT, {"packageId": str(package.pk), "usageRights": USAGE_RIGHTS}, format="json")
    assert ok.status_code == 201, ok.json()
    order = Order.objects.get(pk=ok.json()["data"]["orders"][0])
    assert order.usage_rights["duration"] == USAGE_RIGHTS["duration"]


def test_same_package_can_be_bought_again_and_by_other_brands(
    brand: APIClient, make_brand: Any, package: CreatorPackage
) -> None:
    body = {"packageId": str(package.pk), "usageRights": USAGE_RIGHTS}
    assert brand.post(CHECKOUT, body, format="json").status_code == 201
    assert brand.post(CHECKOUT, body, format="json").status_code == 201
    assert make_brand("Other Co").post(CHECKOUT, body, format="json").status_code == 201
    assert Order.objects.filter(source_type="package").count() == 3


def test_unapproved_creators_package_cant_be_bought(brand: APIClient, package: CreatorPackage) -> None:
    package.creator.approval_status = ApprovalStatus.PENDING
    package.creator.save(update_fields=["approval_status"])
    resp = brand.post(CHECKOUT, {"packageId": str(package.pk), "usageRights": USAGE_RIGHTS}, format="json")
    assert resp.status_code == 404


def test_currency_is_inr_only(brand: APIClient, package: CreatorPackage) -> None:
    body = {"packageId": str(package.pk), "usageRights": USAGE_RIGHTS, "currency": "KWD"}
    assert brand.post(CHECKOUT, body, format="json").status_code == 400


def _awaiting_order(brand: APIClient, creator: Any, package: CreatorPackage) -> Order:
    return order_services.create_order(
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


def test_checking_out_the_same_order_twice_reuses_the_open_session(
    brand: APIClient, creator: Any, package: CreatorPackage
) -> None:
    order = _awaiting_order(brand, creator, package)
    first = brand.post(CHECKOUT, {"orderId": str(order.pk)}, format="json").json()["data"]
    second = brand.post(CHECKOUT, {"orderId": str(order.pk)}, format="json").json()["data"]
    assert first["orderId"] == second["orderId"]
    assert PaymentSession.objects.filter(order=order).count() == 1


def test_payment_for_an_order_cancelled_meanwhile_is_flagged_for_refund(
    brand: APIClient, creator: Any, package: CreatorPackage
) -> None:
    order = _awaiting_order(brand, creator, package)
    rzp_order = brand.post(CHECKOUT, {"orderId": str(order.pk)}, format="json").json()["data"]["orderId"]
    assert brand.post(f"/v1/orders/{order.pk}/cancel/", {"reason": "Changed plans"}, format="json").status_code == 200

    resp = _pay(brand, rzp_order)
    assert resp.status_code == 200, resp.json()
    assert resp.json()["data"]["status"] == "needs_refund"
    assert PaymentReceipt.objects.get().status == "needs_refund"
    assert PaymentSession.objects.get().status == "needs_refund"
    order.refresh_from_db()
    assert order.status == OrderStatus.CANCELLED


def test_order_funded_after_its_deadline_gives_the_creator_the_full_window(
    brand: APIClient, creator: Any, package: CreatorPackage
) -> None:
    order = _awaiting_order(brand, creator, package)
    Order.objects.filter(pk=order.pk).update(deadline=timezone.now() - timedelta(days=2))
    order_services.system_event(order_id=order.pk, event="fund")
    order.refresh_from_db()
    assert order.deadline >= timezone.now() + timedelta(days=package.delivery_days) - timedelta(minutes=1)
    # so the brand can't immediately cancel a just-funded order as "late"
    assert brand.post(f"/v1/orders/{order.pk}/cancel/", {"reason": "late"}, format="json").status_code != 200


def test_withdrawals_are_closed_until_payouts_launch(creator: APIClient) -> None:
    body = {"amount": 50000, "payoutMethod": "upi", "payoutDetails": {"upiId": "a@okicici"}}
    resp = creator.post("/v1/creators/me/withdrawals/", body, format="json")
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "PAYOUTS_NOT_LIVE"


def test_malformed_payout_details_are_a_400_not_a_500(settings: Any, creator: APIClient) -> None:
    settings.PAYOUTS_ENABLED = True
    body = {"amount": 50000, "payoutMethod": "upi", "payoutDetails": "x"}
    assert creator.post("/v1/creators/me/withdrawals/", body, format="json").status_code == 400
