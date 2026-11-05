"""End-to-end API flows: campaign → bid → order → deliver → revise → approve; offers; cart checkout."""

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from apps.orders.services import system_event
from tests.fixtures import USAGE_RIGHTS

pytestmark = pytest.mark.django_db


def ok(resp: Any, code: int = 200) -> dict[str, Any]:
    assert resp.status_code == code, resp.json()
    return resp.json()["data"]


def test_campaign_bid_order_lifecycle(brand: Any, creator: Any, campaign_payload: dict[str, Any]) -> None:
    campaign = ok(brand.post("/v1/campaigns/", campaign_payload, format="json"), 201)

    # approved creator sees it and bids
    open_campaigns = ok(creator.get("/v1/campaigns/"))
    assert [c["id"] for c in open_campaigns] == [campaign["id"]]
    bid = ok(
        creator.post(
            f"/v1/campaigns/{campaign['id']}/bids/",
            {"price": 850_000, "message": "Love it", "expectedViews": 52000, "deliveryDays": 5},
            format="json",
        ),
        201,
    )
    assert bid["status"] == "pending"
    # duplicate bid -> 409; edit instead
    dup = creator.post(
        f"/v1/campaigns/{campaign['id']}/bids/", {"price": 1, "message": "x", "deliveryDays": 5}, format="json"
    )
    assert dup.status_code in (400, 409)
    bid = ok(creator.patch(f"/v1/campaigns/{campaign['id']}/bids/{bid['id']}/", {"price": 900_000}, format="json"))
    assert bid["price"]["amount"] == 900_000 and bid["version"] == 2

    # brand sees new_bid notification + the bid, then selects it
    assert ok(brand.get("/v1/notifications/"))[0]["type"] == "new_bid"
    assert len(ok(brand.get(f"/v1/campaigns/{campaign['id']}/bids/"))) == 1
    order = ok(brand.post(f"/v1/campaigns/{campaign['id']}/bids/{bid['id']}/select/"), 201)
    assert order["status"] == "awaiting_payment"
    assert order["amounts"]["subtotal"]["amount"] == 900_000
    assert order["amounts"]["brandTotal"]["amount"] == 900_000 + 90_000 + 16_200  # default config: brand pays fee+GST
    assert order["usageRights"] == {**USAGE_RIGHTS}
    assert "cancel" in order["allowedActions"]

    # creator can't submit before funding
    early = creator.post(
        f"/v1/orders/{order['id']}/deliverables/", {"contentUrl": "https://drive.google.com/x"}, format="json"
    )
    assert early.status_code == 409 and early.json()["error"]["code"] == "ORDER_INVALID_TRANSITION"

    system_event(order_id=order["id"], event="fund")  # Phase 2: Razorpay webhook does this

    ok(
        creator.post(
            f"/v1/orders/{order['id']}/deliverables/",
            {"contentUrl": "https://drive.google.com/d1", "note": "Draft 1"},
            format="json",
        ),
        201,
    )
    revised = ok(brand.post(f"/v1/orders/{order['id']}/revisions/", {"feedback": "Brighter lighting"}, format="json"))
    assert revised["status"] == "revision_requested" and revised["revisionCount"] == 1
    ok(
        creator.post(
            f"/v1/orders/{order['id']}/deliverables/", {"contentUrl": "https://drive.google.com/d2"}, format="json"
        ),
        201,
    )

    versions = ok(brand.get(f"/v1/orders/{order['id']}/deliverables/"))
    assert [v["version"] for v in versions] == [1, 2] and versions[0]["feedback"] == "Brighter lighting"

    approved = ok(brand.post(f"/v1/orders/{order['id']}/approve/"))
    assert approved["status"] == "approved"
    # second approve is a state conflict, not a double effect
    assert brand.post(f"/v1/orders/{order['id']}/approve/").status_code == 409

    # review only after completion
    assert brand.post(f"/v1/orders/{order['id']}/review/", {"stars": 5}, format="json").status_code == 422
    system_event(order_id=order["id"], event="complete")
    ok(brand.post(f"/v1/orders/{order['id']}/review/", {"stars": 5, "text": "Great"}, format="json"), 201)
    assert brand.post(f"/v1/orders/{order['id']}/review/", {"stars": 4}, format="json").status_code == 409
    detail = ok(brand.get(f"/v1/creators/{order['creator']['id']}/"))
    assert detail["rating"] == 5.0 and detail["reviewCount"] == 1
    assert len(ok(brand.get(f"/v1/creators/{order['creator']['id']}/reviews/"))) == 1

    # creator sees the order in its own list
    assert [o["id"] for o in ok(creator.get("/v1/creators/me/orders/"))] == [order["id"]]


def test_offer_accept_creates_order(brand: Any, creator: Any) -> None:
    creator_id = creator.user.creator.id
    payload = {
        "creatorId": str(creator_id),
        "deliverable": "1 Recipe Reel",
        "budget": 900_000,
        "deadline": (timezone.now() + timedelta(days=10)).isoformat(),
        "message": "Hi!",
        "usageRights": USAGE_RIGHTS,
    }
    offer = ok(brand.post("/v1/offers/", payload, format="json"), 201)
    assert ok(creator.get("/v1/creators/me/offers/"))[0]["id"] == offer["id"]

    result = ok(creator.patch(f"/v1/creators/me/offers/{offer['id']}/", {"status": "accepted"}, format="json"))
    assert result["offer"]["status"] == "accepted"
    assert result["order"]["amounts"]["subtotal"]["amount"] == 900_000
    # can't respond twice
    again = creator.patch(f"/v1/creators/me/offers/{offer['id']}/", {"status": "rejected"}, format="json")
    assert again.status_code == 409
    types = [n["type"] for n in ok(brand.get("/v1/notifications/"))]
    assert "offer_accepted" in types


def test_cart_checkout_uses_server_side_prices(brand: Any, package: Any) -> None:
    ok(brand.post("/v1/cart/", {"packageId": str(package.id), "brief": "Diwali"}, format="json"), 201)
    # client cannot inject a price
    bad = brand.post("/v1/cart/", {"packageId": str(package.id), "price": 1}, format="json")
    assert bad.status_code == 400
    orders = ok(brand.post("/v1/cart/checkout/", {"usageRights": USAGE_RIGHTS}, format="json"), 201)
    assert len(orders) == 1 and orders[0]["amounts"]["subtotal"]["amount"] == package.price_paise
    assert ok(brand.get("/v1/cart/")) == []
    empty = brand.post("/v1/cart/checkout/", {"usageRights": USAGE_RIGHTS}, format="json")
    assert empty.status_code == 422 and empty.json()["error"]["code"] == "CART_EMPTY"


def test_discovery_lists_and_creator_profile(brand: Any, creator: Any, make_creator: Any, package: Any) -> None:
    make_creator("Pending Person", approved=False)
    found = ok(brand.get("/v1/creators/?q=ananya"))
    assert [c["displayName"] for c in found] == ["Ananya Rao"]
    assert found[0]["startingPrice"]["amount"] == package.price_paise
    assert "phone" not in found[0] and "approvalStatus" not in found[0]
    # unapproved creators never appear
    assert all(c["displayName"] != "Pending Person" for c in ok(brand.get("/v1/creators/")))
    # bad filter value -> 400
    assert brand.get("/v1/creators/?platform=myspace").status_code == 400

    lst = ok(brand.post("/v1/lists/", {"name": "Diwali"}, format="json"), 201)
    assert brand.post("/v1/lists/", {"name": "Diwali"}, format="json").status_code == 409
    assert (
        brand.post(f"/v1/lists/{lst['id']}/creators/", {"creatorId": found[0]["id"]}, format="json").status_code == 204
    )
    assert len(ok(brand.get(f"/v1/lists/{lst['id']}/"))["creators"]) == 1

    me = ok(
        creator.patch(
            "/v1/creators/me/",
            {"bio": "Beauty creator from Bengaluru", "categories": ["Beauty", "Skincare"]},
            format="json",
        )
    )
    assert me["bio"].startswith("Beauty") and me["approvalStatus"] == "approved"
    pkg = ok(
        creator.post(
            "/v1/creators/me/packages/",
            {
                "title": "Story",
                "platform": "instagram",
                "deliverables": "3 Stories",
                "price": 300_000,
                "deliveryDays": 3,
            },
            format="json",
        ),
        201,
    )
    assert creator.delete(f"/v1/creators/me/packages/{pkg['id']}/").status_code == 204
