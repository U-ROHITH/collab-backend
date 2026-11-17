"""Orders carry the cancel reason and the brand's review, from stored data only (null when there's none)."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.orders import services as order_services
from apps.orders.models import Order
from tests.fixtures import USAGE_RIGHTS

pytestmark = pytest.mark.django_db


def _order(brand: APIClient, creator: Any, package: Any) -> Order:
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


def test_new_order_has_null_cancel_reason_and_review(brand: APIClient, creator: Any, package: Any) -> None:
    order = _order(brand, creator, package)
    data = brand.get(f"/v1/orders/{order.pk}/").json()["data"]
    assert data["cancelReason"] is None and data["review"] is None


def test_cancel_reason_is_shown_to_both_parties_on_detail_and_list(
    brand: APIClient, creator: Any, package: Any
) -> None:
    order = _order(brand, creator, package)
    resp = brand.post(f"/v1/orders/{order.pk}/cancel/", {"reason": "Budget moved to next quarter"}, format="json")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["data"]["cancelReason"] == "Budget moved to next quarter"
    assert creator.get(f"/v1/orders/{order.pk}/").json()["data"]["cancelReason"] == "Budget moved to next quarter"
    listed = brand.get("/v1/orders/").json()["data"]
    assert listed[0]["cancelReason"] == "Budget moved to next quarter"


def test_review_appears_on_the_order_after_the_brand_leaves_one(brand: APIClient, creator: Any, package: Any) -> None:
    order = _order(brand, creator, package)
    for event in ("fund",):
        order_services.system_event(order_id=order.pk, event=event)
    ok = creator.post(
        f"/v1/orders/{order.pk}/deliverables/", {"contentUrl": "https://drive.google.com/x"}, format="json"
    )
    assert ok.status_code == 201, ok.json()
    assert brand.post(f"/v1/orders/{order.pk}/approve/").status_code == 200
    order_services.system_event(order_id=order.pk, event="complete")
    assert brand.post(f"/v1/orders/{order.pk}/review/", {"stars": 4, "text": "Solid"}, format="json").status_code == 201

    review = creator.get(f"/v1/orders/{order.pk}/").json()["data"]["review"]
    assert review["stars"] == 4 and review["text"] == "Solid" and review["createdAt"]
    assert brand.get("/v1/orders/").json()["data"][0]["review"]["stars"] == 4
