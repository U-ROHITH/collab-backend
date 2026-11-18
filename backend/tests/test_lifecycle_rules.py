"""Campaign terms are fixed once creators bid on them; brands can review once they've approved the work."""

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.orders.services import system_event

pytestmark = pytest.mark.django_db

BID = {"price": 800_000, "message": "hi", "deliveryDays": 5}


def _campaign_with_bid(brand: APIClient, creator: APIClient, payload: dict[str, Any]) -> tuple[str, str]:
    campaign = brand.post("/v1/campaigns/", payload, format="json").json()["data"]
    bid = creator.post(f"/v1/campaigns/{campaign['id']}/bids/", BID, format="json")
    assert bid.status_code == 201, bid.json()
    return campaign["id"], bid.json()["data"]["id"]


@pytest.mark.parametrize(
    "change",
    [
        {"brief": "Now it's three Reels and a Story."},
        {"deliverables": "3 Reels + 1 Story"},
        {"platform": "youtube"},
        {"usageRights": {"duration": "perpetual", "channels": ["paid_ads"], "exclusivity": True, "territory": "World"}},
    ],
)
def test_terms_are_locked_once_there_are_bids(
    brand: APIClient, creator: APIClient, campaign_payload: dict[str, Any], change: dict[str, Any]
) -> None:
    cid, _ = _campaign_with_bid(brand, creator, campaign_payload)
    resp = brand.patch(f"/v1/campaigns/{cid}/", change, format="json")
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "CAMPAIGN_TERMS_LOCKED"


def test_title_budget_and_closing_date_can_still_change(
    brand: APIClient, creator: APIClient, campaign_payload: dict[str, Any]
) -> None:
    cid, _ = _campaign_with_bid(brand, creator, campaign_payload)
    later = (timezone.now() + timedelta(days=20)).date().isoformat()
    resp = brand.patch(f"/v1/campaigns/{cid}/", {"title": "New title", "closesAt": later}, format="json")
    assert resp.status_code == 200, resp.json()


def test_terms_can_change_before_any_bid(brand: APIClient, campaign_payload: dict[str, Any]) -> None:
    cid = brand.post("/v1/campaigns/", campaign_payload, format="json").json()["data"]["id"]
    assert brand.patch(f"/v1/campaigns/{cid}/", {"brief": "Updated"}, format="json").status_code == 200


def test_closing_date_cant_be_moved_into_the_past(brand: APIClient, campaign_payload: dict[str, Any]) -> None:
    cid = brand.post("/v1/campaigns/", campaign_payload, format="json").json()["data"]["id"]
    past = (timezone.now() - timedelta(days=2)).date().isoformat()
    resp = brand.patch(f"/v1/campaigns/{cid}/", {"closesAt": past}, format="json")
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "INVALID_CLOSES_AT"


def test_brand_can_review_once_it_has_approved(
    brand: APIClient, creator: APIClient, campaign_payload: dict[str, Any]
) -> None:
    cid, bid_id = _campaign_with_bid(brand, creator, campaign_payload)
    order = brand.post(f"/v1/campaigns/{cid}/bids/{bid_id}/select/").json()["data"]
    system_event(order_id=order["id"], event="fund")
    creator.post(f"/v1/orders/{order['id']}/deliverables/", {"contentUrl": "https://drive.google.com/x"}, format="json")
    assert brand.post(f"/v1/orders/{order['id']}/review/", {"stars": 5}, format="json").status_code == 422
    assert brand.post(f"/v1/orders/{order['id']}/approve/").status_code == 200
    resp = brand.post(f"/v1/orders/{order['id']}/review/", {"stars": 5, "text": "Great"}, format="json")
    assert resp.status_code == 201, resp.json()


@pytest.mark.parametrize("kind", ["approved", "pending", "no_kyc"])
def test_every_creator_can_browse_open_campaigns(
    make_creator: Any, brand: APIClient, campaign_payload: dict[str, Any], kind: str
) -> None:
    brand.post("/v1/campaigns/", campaign_payload, format="json")
    creator = {
        "approved": lambda: make_creator("A"),
        "pending": lambda: make_creator("P", approved=False),
        "no_kyc": lambda: make_creator("K", kyc=False),
    }[kind]()
    resp = creator.get("/v1/campaigns/")
    assert resp.status_code == 200, resp.json()
    assert len(resp.json()["data"]) == 1


def test_suspended_account_gets_a_clear_403(creator: APIClient) -> None:
    from apps.identity.models import UserStatus

    creator.user.status = UserStatus.SUSPENDED
    creator.user.save(update_fields=["status"])
    resp = creator.get("/v1/campaigns/")
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "ACCOUNT_SUSPENDED"
