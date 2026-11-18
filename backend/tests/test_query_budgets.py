"""Every query is a round trip from Singapore to the Mumbai database (~40 ms). These budgets catch N+1s and
needless lookups before they reach production."""

from typing import Any

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

pytestmark = pytest.mark.django_db

PACKAGE = {"title": "Reel", "platform": "instagram", "deliverables": "1", "price": 100000, "deliveryDays": 3}
BID = {"price": 800000, "message": "hi", "deliveryDays": 5}


@pytest.fixture
def market(make_brand: Any, make_creator: Any, campaign_payload: dict[str, Any]) -> dict[str, Any]:
    brand = make_brand()
    creators = [make_creator(f"Creator {i}") for i in range(6)]
    campaign = brand.post("/v1/campaigns/", campaign_payload, format="json").json()["data"]
    for c in creators:
        c.patch("/v1/creators/me/", {"bio": "x", "platforms": {"instagram": {"followersCount": 10}}}, format="json")
        c.post("/v1/creators/me/packages/", PACKAGE, format="json")
        c.post(f"/v1/campaigns/{campaign['id']}/bids/", BID, format="json")
    return {"brand": brand, "creator": creators[0], "campaign": campaign}


@pytest.mark.parametrize(
    ("who", "url", "budget"),
    [
        ("anon", "/v1/creators/", 1),
        ("anon", "/v1/creators/{creator_id}/", 3),
        ("anon", "/v1/campaigns/", 1),
        ("brand", "/v1/campaigns/", 2),
        ("brand", "/v1/campaigns/{campaign_id}/bids/", 2),
        ("brand", "/v1/auth/me/", 1),
        ("brand", "/v1/brands/me/", 1),
        ("creator", "/v1/creators/me/", 3),
        ("brand", "/v1/orders/", 2),
        ("brand", "/v1/conversations/", 2),
        ("brand", "/v1/notifications/", 2),
    ],
)
def test_query_budget(market: dict[str, Any], anon: Any, who: str, url: str, budget: int) -> None:
    client = {"anon": anon, "brand": market["brand"], "creator": market["creator"]}[who]
    url = url.format(creator_id=market["creator"].user.creator.id, campaign_id=market["campaign"]["id"])
    cache.clear()  # measure the uncached path
    with CaptureQueriesContext(connection) as queries:
        assert client.get(url).status_code == 200
    assert len(queries) <= budget, [q["sql"][:120] for q in queries]
