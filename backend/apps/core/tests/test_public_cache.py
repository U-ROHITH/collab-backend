"""Guests' discovery pages come from the cache (no database), and any change shows up on the next request."""

from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


def _queries(client: APIClient, url: str) -> tuple[int, Any]:
    with CaptureQueriesContext(connection) as q:
        resp = client.get(url)
    assert resp.status_code == 200
    return len(q), resp.json()


@pytest.mark.parametrize("url", ["/v1/creators/", "/v1/campaigns/"])
def test_second_guest_request_needs_no_database(
    anon: APIClient, creator: APIClient, brand: APIClient, campaign_payload: dict[str, Any], url: str
) -> None:
    brand.post("/v1/campaigns/", campaign_payload, format="json")
    _, first = _queries(anon, url)
    count, second = _queries(anon, url)
    assert count == 0 and second == first


def test_a_change_is_visible_on_the_next_guest_request(anon: APIClient, creator: APIClient) -> None:
    detail = f"/v1/creators/{creator.user.creator.id}/"
    assert anon.get(detail).json()["data"]["bio"] == ""
    creator.patch("/v1/creators/me/", {"bio": "Food creator"}, format="json")
    assert anon.get(detail).json()["data"]["bio"] == "Food creator"
    assert anon.get("/v1/creators/").json()["data"][0]["bio"] == "Food creator"


def test_signed_in_users_are_never_served_the_guest_copy(
    anon: APIClient, creator: APIClient, brand: APIClient, campaign_payload: dict[str, Any]
) -> None:
    brand.post("/v1/campaigns/", campaign_payload, format="json")
    guest = anon.get("/v1/campaigns/").json()["data"][0]
    assert "brief" not in guest or guest["brief"] in (None, "")
    signed_in = creator.get("/v1/campaigns/").json()["data"][0]
    assert signed_in["brief"]


def test_query_string_is_part_of_the_key(anon: APIClient, make_creator: Any) -> None:
    make_creator("Tech Person").patch("/v1/creators/me/", {"categories": ["Tech"]}, format="json")
    make_creator("Food Person").patch("/v1/creators/me/", {"categories": ["Food"]}, format="json")
    tech = [c["displayName"] for c in anon.get("/v1/creators/?category=tech").json()["data"]]
    food = [c["displayName"] for c in anon.get("/v1/creators/?category=food").json()["data"]]
    assert tech == ["Tech Person"] and food == ["Food Person"]
