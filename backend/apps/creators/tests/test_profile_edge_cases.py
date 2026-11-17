"""Creator profile edits: bad-but-plausible input is a 400 or a clean no-op, never a 500; nothing private leaks."""

from typing import Any

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db

ME = "/v1/creators/me/"


def _me(client: APIClient) -> dict[str, Any]:
    return client.get(ME).json()["data"]


def test_date_of_birth_is_never_public(creator: APIClient, anon: APIClient) -> None:
    assert creator.patch(ME, {"dob": "1999-04-01"}, format="json").status_code == 200
    card = anon.get("/v1/creators/").json()["data"][0]
    assert "dob" not in card
    assert "dob" not in anon.get(f"/v1/creators/{card['id']}/").json()["data"]
    assert _me(creator)["dob"] == "1999-04-01"  # the creator still sees their own


def test_removing_the_photo_with_null_clears_it(creator: APIClient) -> None:
    creator.patch(ME, {"avatarUrl": "https://cdn.example.com/a.webp"}, format="json")
    resp = creator.patch(ME, {"avatarUrl": None}, format="json")
    assert resp.status_code == 200, resp.json()
    assert not _me(creator)["avatarUrl"]


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "not a url", "http://insecure.example.com/a.png"])
def test_avatar_must_be_an_https_url(creator: APIClient, bad: str) -> None:
    assert creator.patch(ME, {"avatarUrl": bad}, format="json").status_code == 400


def test_too_long_values_are_400_not_500(creator: APIClient) -> None:
    long_url = "https://cdn.example.com/" + "a" * 490
    assert creator.patch(ME, {"avatarUrl": long_url}, format="json").status_code == 400
    assert creator.patch(ME, {"location": "x" * 100}, format="json").status_code == 400
    resp = creator.patch(ME, {"city": "c" * 60, "region": "r" * 60}, format="json")
    assert resp.status_code == 400 and "location" in str(resp.json()["error"]["details"]).lower()


def test_changing_city_keeps_region_and_blank_clears(creator: APIClient) -> None:
    creator.patch(ME, {"city": "Mumbai", "region": "Maharashtra"}, format="json")
    creator.patch(ME, {"city": "Pune"}, format="json")
    me = _me(creator)
    assert (me["city"], me["region"]) == ("Pune", "Maharashtra")
    creator.patch(ME, {"city": "", "region": ""}, format="json")
    me = _me(creator)
    assert (me["city"], me["region"], me["location"]) == ("", "", "")


def test_removing_follower_stats_clears_the_follower_count(creator: APIClient, anon: APIClient) -> None:
    creator.patch(ME, {"platforms": {"instagram": {"handle": "a", "followersCount": 30000}}}, format="json")
    assert _me(creator)["followerCount"] == 30000
    creator.patch(ME, {"platforms": {"instagram": None}}, format="json")
    assert _me(creator)["followerCount"] is None
    assert anon.get("/v1/creators/?minFollowers=1000").json()["data"] == []


def test_category_filter_matches_whole_categories(make_creator: Any, anon: APIClient) -> None:
    a = make_creator("Smart Home Guy")
    b = make_creator("Art Person")
    a.patch(ME, {"categories": ["Smart Home"]}, format="json")
    b.patch(ME, {"categories": ["Art"]}, format="json")
    names = [c["displayName"] for c in anon.get("/v1/creators/?category=art").json()["data"]]
    assert names == ["Art Person"]
