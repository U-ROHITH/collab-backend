"""Per-platform handle + stats: saved via PATCH /v1/creators/me/, shown to brands on list and detail.

Until Instagram/YouTube connect exists these numbers are creator-entered, so each platform says verified=false,
and the top-level `followerCount` is kept consistent with the primary platform's stats.
"""

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db

ME = "/v1/creators/me/"

STATS = {
    "instagram": {"handle": "@ananya.eats", "followersCount": 30000, "engagementRate": 4.2, "avgViews": 8000},
    "youtube": {"handle": "AnanyaCooks", "followersCount": 20000, "engagementRate": 2.8, "avgViews": 15000},
}


def _patch(client: APIClient, body: dict[str, Any]) -> Any:
    return client.patch(ME, body, format="json")


def test_creator_saves_platforms_and_brands_see_them_on_list_and_detail(creator: APIClient, anon: APIClient) -> None:
    resp = _patch(creator, {"platforms": STATS})
    assert resp.status_code == 200, resp.json()

    expected = {
        "instagram": {
            "handle": "ananya.eats",
            "url": "https://www.instagram.com/ananya.eats/",
            "followersCount": 30000,
            "engagementRate": 4.2,
            "avgViews": 8000,
            "verified": False,
        },
        "youtube": {
            "handle": "AnanyaCooks",
            "url": "https://www.youtube.com/@AnanyaCooks",
            "followersCount": 20000,
            "engagementRate": 2.8,
            "avgViews": 15000,
            "verified": False,
        },
    }
    assert resp.json()["data"]["platforms"] == expected

    listed = anon.get("/v1/creators/").json()["data"]
    assert listed[0]["platforms"] == expected
    detail = anon.get(f"/v1/creators/{listed[0]['id']}/").json()["data"]
    assert detail["platforms"] == expected
    # flat handles stay in sync with bare handles
    assert detail["instagram"] == "ananya.eats" and detail["youtube"] == "AnanyaCooks"
    # followerCount is kept consistent with primary platform's followersCount
    assert detail["verified"] is False and detail["followerCount"] == 30000


def test_partial_update_merges_per_platform_and_null_removes_one(creator: APIClient) -> None:
    _patch(creator, {"platforms": STATS})
    resp = _patch(creator, {"platforms": {"instagram": {"followersCount": 31000}, "youtube": None}})
    assert resp.status_code == 200, resp.json()
    platforms = resp.json()["data"]["platforms"]
    assert set(platforms) == {"instagram"}
    assert platforms["instagram"]["followersCount"] == 31000
    assert platforms["instagram"]["engagementRate"] == 4.2  # untouched fields kept
    assert resp.json()["data"]["youtube"] == ""
    assert resp.json()["data"]["followerCount"] == 31000


def test_flat_handle_alone_shows_as_a_platform(creator: APIClient) -> None:
    resp = _patch(creator, {"instagram": "ananya.eats"})
    assert resp.json()["data"]["platforms"] == {
        "instagram": {
            "handle": "ananya.eats",
            "url": "https://www.instagram.com/ananya.eats/",
            "followersCount": None,
            "engagementRate": None,
            "avgViews": None,
            "verified": False,
        }
    }


def test_top_level_instagram_with_stats_and_pasted_url(creator: APIClient) -> None:
    resp = _patch(
        creator,
        {
            "instagram": {
                "handle": "https://www.instagram.com/democreator/",
                "followersCount": 25000,
                "avgViews": 5000,
                "engagementRate": 3.5,
            }
        },
    )
    assert resp.status_code == 200, resp.json()
    data = resp.json()["data"]
    assert data["instagram"] == "democreator"
    assert data["followerCount"] == 25000
    assert data["platforms"]["instagram"] == {
        "handle": "democreator",
        "url": "https://www.instagram.com/democreator/",
        "followersCount": 25000,
        "avgViews": 5000,
        "engagementRate": 3.5,
        "verified": False,
    }


def test_new_creator_has_empty_platforms(make_creator: Callable[..., APIClient], anon: APIClient) -> None:
    make_creator()
    assert anon.get("/v1/creators/").json()["data"][0]["platforms"] == {}


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"tiktok": {"followersCount": 5}}, "platforms"),
        ({"instagram": {"followersCount": -1}}, "followersCount"),
        ({"instagram": {"engagementRate": 101}}, "engagementRate"),
        ({"instagram": {"avgViews": "lots"}}, "avgViews"),
        ({"instagram": {"likes": 5}}, "likes"),
        ({"instagram": {"handle": "x" * 121}}, "handle"),
    ],
)
def test_bad_platform_input_is_a_400_on_the_right_field(creator: APIClient, body: dict[str, Any], field: str) -> None:
    resp = _patch(creator, {"platforms": body})
    assert resp.status_code == 400, resp.json()
    assert field in str(resp.json()["error"]["details"])


def test_top_level_unknown_nested_fields_rejected_400(creator: APIClient) -> None:
    resp = _patch(creator, {"instagram": {"handle": "@test", "followers": 12000, "er": 4.2}})
    assert resp.status_code == 400, resp.json()
    details = resp.json()["error"]["details"]["instagram"]
    assert "followers" in details
    assert "er" in details
