"""Frontend launch requests: guest browsing, plain refresh errors, creator earnings."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.orders.models import Order
from apps.orders.services import system_event

pytestmark = pytest.mark.django_db

PRIVATE_KEYS = {"phone", "email", "approvalStatus", "user", "payout", "pan"}


def test_guest_can_browse_creators(anon: APIClient, creator: APIClient, make_creator: Any, package: Any) -> None:
    make_creator("Pending Person", approved=False)
    listing = anon.get("/v1/creators/")
    assert listing.status_code == 200
    cards = listing.json()["data"]
    assert [c["displayName"] for c in cards] == ["Ananya Rao"]
    assert not PRIVATE_KEYS & set(cards[0])

    detail = anon.get(f"/v1/creators/{cards[0]['id']}/")
    assert detail.status_code == 200
    body = detail.json()["data"]
    assert body["packages"][0]["id"] == str(package.id)
    assert not PRIVATE_KEYS & set(body)

    pending_id = make_creator("Hidden", approved=False).user.creator.id
    assert anon.get(f"/v1/creators/{pending_id}/").status_code == 404
    assert anon.get("/v1/creators/?platform=myspace").status_code == 400


def test_guest_campaign_list_hides_brief(
    anon: APIClient, brand: APIClient, creator: APIClient, campaign_payload: dict[str, Any]
) -> None:
    campaign = brand.post("/v1/campaigns/", campaign_payload, format="json").json()["data"]
    brand.post("/v1/campaigns/", {**campaign_payload, "status": "draft", "title": "Secret draft"}, format="json")

    guest = anon.get("/v1/campaigns/")
    assert guest.status_code == 200
    items = guest.json()["data"]
    assert [c["id"] for c in items] == [campaign["id"]]  # drafts never public
    assert items[0]["brief"] is None and items[0]["usageRights"] is None and items[0]["brand"] is None
    assert items[0]["title"] == campaign_payload["title"] and items[0]["budgetMax"]["amount"] == 1_500_000

    # approved creators still get the full brief; brands their own campaigns with brand info
    full = creator.get("/v1/campaigns/").json()["data"][0]
    assert full["brief"] == campaign_payload["brief"] and full["brand"] is None
    assert brand.get("/v1/campaigns/").json()["data"][0]["brand"]["name"]

    # detail, writes and bids stay signed-in only
    assert anon.get(f"/v1/campaigns/{campaign['id']}/").status_code == 401
    assert anon.post("/v1/campaigns/", campaign_payload, format="json").status_code == 401
    assert anon.get(f"/v1/campaigns/{campaign['id']}/bids/").status_code == 401


def test_status_filter_ignored_for_guests(anon: APIClient, brand: APIClient, campaign_payload: dict) -> None:
    brand.post("/v1/campaigns/", {**campaign_payload, "status": "draft"}, format="json")
    assert anon.get("/v1/campaigns/?status=draft").json()["data"] == []


@pytest.mark.parametrize("token", ["garbage", "a.b.c", ""])
def test_bad_refresh_token_plain_message(anon: APIClient, token: str) -> None:
    resp = anon.post("/v1/auth/token/refresh/", {"refresh": token}, format="json")
    assert resp.status_code in (400, 401)
    message = resp.json()["error"]["message"]
    assert "ErrorDetail" not in message and "{" not in message
    if resp.status_code == 401:
        assert message == "Your session has expired or is invalid. Please sign in again."


def test_reused_refresh_token_plain_message(anon: APIClient, creator: APIClient) -> None:
    from apps.identity.services import issue_tokens

    refresh = issue_tokens(creator.user).refresh
    assert anon.post("/v1/auth/token/refresh/", {"refresh": refresh}, format="json").status_code == 200
    reused = anon.post("/v1/auth/token/refresh/", {"refresh": refresh}, format="json")  # blacklisted now
    assert reused.status_code == 401
    assert reused.json()["error"]["code"] == "AUTH_INVALID"
    assert "ErrorDetail" not in reused.json()["error"]["message"]


def test_creator_earnings(brand: APIClient, creator: APIClient, campaign_payload: dict[str, Any]) -> None:
    empty = creator.get("/v1/creators/me/earnings/").json()["data"]
    assert empty["earned"] == {"amount": 0, "currency": "INR"} and empty["payoutsEnabled"] is False

    def order_for(price: int) -> str:
        campaign = brand.post("/v1/campaigns/", campaign_payload, format="json").json()["data"]
        bid = creator.post(
            f"/v1/campaigns/{campaign['id']}/bids/", {"price": price, "message": "hi", "deliveryDays": 5}, format="json"
        ).json()["data"]
        oid = brand.post(f"/v1/campaigns/{campaign['id']}/bids/{bid['id']}/select/").json()["data"]["id"]
        return oid

    funded, approved, unpaid = order_for(800_000), order_for(1_000_000), order_for(600_000)
    system_event(order_id=funded, event="fund")
    system_event(order_id=approved, event="fund")
    Order.objects.filter(pk=approved).update(status="approved")

    body = creator.get("/v1/creators/me/earnings/").json()["data"]
    funded_o, approved_o = Order.objects.get(pk=funded), Order.objects.get(pk=approved)
    assert body["inEscrow"]["amount"] == funded_o.creator_payout_paise
    assert body["earned"]["amount"] == approved_o.creator_payout_paise
    assert body["tdsDeducted"]["amount"] == approved_o.tds_paise
    assert body["ordersInEscrow"] == 1 and body["ordersEarned"] == 1
    assert Order.objects.get(pk=unpaid).status == "awaiting_payment"

    assert brand.get("/v1/creators/me/earnings/").status_code == 403
