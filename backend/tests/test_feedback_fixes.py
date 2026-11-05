"""Frontend feedback (2026-11-05): blind bidding, date-only bid dates, both sides see profiles, message calendar."""

import re
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from apps.core.dates import IST
from apps.messaging.models import Message
from tests.fixtures import USAGE_RIGHTS

pytestmark = pytest.mark.django_db
DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PRIVATE_KEYS = {"phone", "email", "gstin", "billingAddress", "legalName"}


def today_ist() -> str:
    return timezone.localtime(timezone.now(), IST).date().isoformat()


def ok(resp: Any, code: int = 200) -> Any:
    assert resp.status_code == code, resp.json()
    return resp.json()["data"]


# --- 1. Blind bidding: creators don't see the brand on campaigns ------------------------------
def test_creator_does_not_see_brand_on_campaigns(brand: Any, creator: Any, campaign_payload: dict) -> None:
    c = ok(brand.post("/v1/campaigns/", campaign_payload, format="json"), 201)
    assert c["brand"]["name"] == "Nimbu Foods"  # the owner still sees it
    assert ok(creator.get("/v1/campaigns/"))[0]["brand"] is None
    assert ok(creator.get(f"/v1/campaigns/{c['id']}/"))["brand"] is None
    assert ok(creator.get("/v1/creators/me/campaigns/"))[0]["brand"] is None


def test_bid_alone_does_not_reveal_brand_profile(brand: Any, creator: Any, campaign_payload: dict) -> None:
    c = ok(brand.post("/v1/campaigns/", campaign_payload, format="json"), 201)
    ok(
        creator.post(
            f"/v1/campaigns/{c['id']}/bids/", {"price": 300_000, "message": "hi", "deliveryDays": 3}, format="json"
        ),
        201,
    )
    assert creator.get(f"/v1/brands/{brand.user.brand.id}/").status_code == 404


# --- 2. Date only (no time) on campaigns, bids and offers ----------------------------------
def test_campaign_and_bid_dates_are_date_only(brand: Any, creator: Any, campaign_payload: dict) -> None:
    closes = (timezone.localtime(timezone.now(), IST).date() + timedelta(days=5)).isoformat()
    c = ok(brand.post("/v1/campaigns/", {**campaign_payload, "closesAt": closes}, format="json"), 201)
    assert c["closesAt"] == closes and DATE_ONLY.match(c["createdAt"])
    b = ok(
        creator.post(
            f"/v1/campaigns/{c['id']}/bids/", {"price": 300_000, "message": "hi", "deliveryDays": 3}, format="json"
        ),
        201,
    )
    assert b["createdAt"] == today_ist()


def test_closing_today_is_allowed_past_and_garbage_are_not(brand: Any, campaign_payload: dict) -> None:
    assert brand.post("/v1/campaigns/", {**campaign_payload, "closesAt": today_ist()}, format="json").status_code == 201
    past = (timezone.localtime(timezone.now(), IST).date() - timedelta(days=1)).isoformat()
    r = brand.post("/v1/campaigns/", {**campaign_payload, "closesAt": past}, format="json")
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_CLOSES_AT"
    assert (
        brand.post("/v1/campaigns/", {**campaign_payload, "closesAt": "20-11-2026"}, format="json").status_code == 400
    )


def test_offer_deadline_is_date_only(brand: Any, creator: Any) -> None:
    deadline = (timezone.localtime(timezone.now(), IST).date() + timedelta(days=10)).isoformat()
    o = ok(
        brand.post(
            "/v1/offers/",
            {
                "creatorId": str(creator.user.creator.id),
                "deliverable": "1 Reel",
                "budget": 500_000,
                "deadline": deadline,
                "message": "hi",
                "usageRights": USAGE_RIGHTS,
            },
            format="json",
        ),
        201,
    )
    assert o["deadline"] == deadline and DATE_ONLY.match(o["createdAt"])


# --- 3. Both sides see each other's profile details -----------------------------------------
@pytest.fixture
def order_pair(brand: Any, creator: Any) -> dict[str, Any]:
    brand.patch(
        "/v1/brands/me/", {"industry": "Food & Beverage", "website": "https://nimbu.example.com"}, format="json"
    )
    creator.patch(
        "/v1/creators/me/",
        {"bio": "Beauty creator from Bengaluru", "categories": ["Beauty"], "location": "Bengaluru"},
        format="json",
    )
    offer = ok(
        brand.post(
            "/v1/offers/",
            {
                "creatorId": str(creator.user.creator.id),
                "deliverable": "1 Reel",
                "budget": 500_000,
                "deadline": (timezone.now() + timedelta(days=7)).date().isoformat(),
                "message": "hi",
                "usageRights": USAGE_RIGHTS,
            },
            format="json",
        ),
        201,
    )
    result = ok(creator.patch(f"/v1/creators/me/offers/{offer['id']}/", {"status": "accepted"}, format="json"))
    return result["order"]


def test_brand_sees_creator_details_and_creator_sees_brand_details(brand: Any, creator: Any, order_pair: dict) -> None:
    as_brand = ok(brand.get(f"/v1/orders/{order_pair['id']}/"))
    assert as_brand["creator"]["bio"] == "Beauty creator from Bengaluru"
    assert as_brand["creator"]["categories"] == ["Beauty"] and as_brand["creator"]["location"] == "Bengaluru"
    as_creator = ok(creator.get(f"/v1/orders/{order_pair['id']}/"))
    assert as_creator["brand"]["industry"] == "Food & Beverage"
    assert as_creator["brand"]["website"] == "https://nimbu.example.com"
    for party in (as_brand["creator"], as_creator["brand"]):
        assert not PRIVATE_KEYS & set(party)  # contact/billing details never exposed


def test_brand_profile_endpoint(brand: Any, creator: Any, make_creator: Any, make_brand: Any, order_pair: dict) -> None:
    bid = brand.user.brand.id
    prof = ok(creator.get(f"/v1/brands/{bid}/"))
    assert prof["name"] == "Nimbu Foods" and prof["industry"] == "Food & Beverage"
    assert not PRIVATE_KEYS & set(prof)
    assert make_creator("Stranger").get(f"/v1/brands/{bid}/").status_code == 404
    assert ok(brand.get(f"/v1/brands/{bid}/"))["id"] == str(bid)
    assert make_brand("Rival").get(f"/v1/brands/{bid}/").status_code == 404


def test_bids_and_offers_show_full_creator_card(brand: Any, creator: Any, campaign_payload: dict) -> None:
    creator.patch("/v1/creators/me/", {"bio": "Hello", "location": "Pune"}, format="json")
    c = ok(brand.post("/v1/campaigns/", campaign_payload, format="json"), 201)
    ok(
        creator.post(
            f"/v1/campaigns/{c['id']}/bids/", {"price": 300_000, "message": "hi", "deliveryDays": 3}, format="json"
        ),
        201,
    )
    card = ok(brand.get(f"/v1/campaigns/{c['id']}/bids/"))[0]["creator"]
    assert card["bio"] == "Hello" and card["location"] == "Pune" and "reviewCount" in card


# --- 4. Message dates for the calendar ------------------------------------------------------
def test_message_dates_and_calendar(brand: Any, creator: Any, order_pair: dict) -> None:
    cid = ok(brand.post("/v1/conversations/", {"creatorId": str(creator.user.creator.id)}, format="json"), 201)["id"]
    conv = ok(creator.get(f"/v1/conversations/{cid}/"))
    assert conv["withParty"]["profile"]["industry"] == "Food & Beverage"

    ok(brand.post(f"/v1/conversations/{cid}/messages/", {"text": "today"}, format="json"), 201)
    old = ok(brand.post(f"/v1/conversations/{cid}/messages/", {"text": "older"}, format="json"), 201)
    three_days_ago = timezone.now() - timedelta(days=3)
    Message.objects.filter(pk=old["id"]).update(created_at=three_days_ago)
    old_day = timezone.localtime(three_days_ago, IST).date().isoformat()

    msgs = ok(creator.get(f"/v1/conversations/{cid}/messages/"))
    assert DATE_ONLY.match(msgs[0]["sentDate"]) and re.match(r"^\d{2}:\d{2}$", msgs[0]["sentTime"])

    only_old = ok(creator.get(f"/v1/conversations/{cid}/messages/?date={old_day}"))
    assert [m["text"] for m in only_old] == ["older"]

    days = ok(creator.get(f"/v1/conversations/{cid}/dates/"))
    assert days == [{"date": old_day, "count": 1}, {"date": today_ist(), "count": 1}]
    month = old_day[:7]
    assert {"date": old_day, "count": 1} in ok(creator.get(f"/v1/conversations/{cid}/dates/?month={month}"))

    bad = creator.get(f"/v1/conversations/{cid}/messages/?date=yesterday")
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_DATE"
    assert brand.get(f"/v1/conversations/{cid}/dates/?month=2026-13").status_code == 422
