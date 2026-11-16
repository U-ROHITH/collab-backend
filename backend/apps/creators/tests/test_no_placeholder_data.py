"""No made-up data: fields stay empty until the user fills them, and blank form inputs save as "not set"."""

import importlib
from collections.abc import Callable

import pytest
from django.db import connection
from rest_framework.test import APIClient

from apps.creators.models import Creator
from apps.identity.models import User
from apps.identity.services import complete_signup

pytestmark = pytest.mark.django_db

ME = "/v1/creators/me/"
clear_migration = importlib.import_module("apps.creators.migrations.0008_no_placeholder_defaults")


def test_new_creator_has_no_made_up_values(make_creator: Callable[..., APIClient], anon: APIClient) -> None:
    make_creator("Ananya Rao")
    card = anon.get("/v1/creators/").json()["data"][0]
    assert card["repliesWithin"] is None
    assert card["primaryPlatform"] is None
    assert card["followerCount"] is None
    assert card["platforms"] == {}
    assert card["bio"] == "" and card["categories"] == [] and card["languages"] == []
    assert card["rating"] is None and card["reviewCount"] == 0


def test_creator_can_set_replies_within_and_primary_platform(creator: APIClient) -> None:
    resp = creator.patch(ME, {"repliesWithin": "6h", "primaryPlatform": "youtube"}, format="json")
    assert resp.status_code == 200, resp.json()
    me = creator.get(ME).json()["data"]
    assert me["repliesWithin"] == "6h" and me["primaryPlatform"] == "youtube"


def test_brand_without_a_brand_name_is_not_named_after_its_owner() -> None:
    user = User.objects.create_user(email="owner@example.com")
    complete_signup(user=user, role="brand", full_name="Priya Sharma")
    assert User.objects.get(pk=user.pk).brand.name == ""


def test_blank_number_inputs_save_as_not_set_and_dont_block_the_rest_of_the_save(creator: APIClient) -> None:
    body = {
        "bio": "Food creator",
        "platforms": {"instagram": {"handle": "ananya", "followersCount": "", "engagementRate": "", "avgViews": " "}},
    }
    resp = creator.patch(ME, body, format="json")
    assert resp.status_code == 200, resp.json()
    me = creator.get(ME).json()["data"]
    assert me["bio"] == "Food creator"
    ig = me["platforms"]["instagram"]
    assert ig["handle"] == "ananya"
    assert ig["followersCount"] is None and ig["engagementRate"] is None and ig["avgViews"] is None


def test_common_number_formats_are_understood(creator: APIClient) -> None:
    body = {"platforms": {"instagram": {"followersCount": "30,000", "engagementRate": "4.2%", "avgViews": "8 000"}}}
    resp = creator.patch(ME, body, format="json")
    assert resp.status_code == 200, resp.json()
    ig = creator.get(ME).json()["data"]["platforms"]["instagram"]
    assert (ig["followersCount"], ig["engagementRate"], ig["avgViews"]) == (30000, 4.2, 8000)


def test_ambiguous_numbers_are_still_rejected_on_the_field(creator: APIClient) -> None:
    resp = creator.patch(ME, {"platforms": {"instagram": {"followersCount": "30K"}}}, format="json")
    assert resp.status_code == 400
    assert "followersCount" in str(resp.json()["error"]["details"])


def _seed(slug: str, stats: dict, follower_count: int | None) -> Creator:  # type: ignore[type-arg]
    return Creator.objects.create(
        display_name=slug,
        slug=slug,
        platform_stats={"instagram": stats},
        follower_count=follower_count,
        social_media_links={"instagram": "handle"},
    )


def test_known_dummy_stats_are_cleared_but_real_values_are_kept() -> None:
    thalla = _seed("thalla-aravind-67c664", {"followersCount": 12000, "avgViews": 3000, "engagementRate": 4.2}, 12000)
    demo = _seed("demo-creator", {"followersCount": 25000, "avgViews": None, "engagementRate": None}, 25000)
    real = _seed("someone-real", {"followersCount": 12000, "avgViews": 3000, "engagementRate": 4.2}, 12000)

    clear_migration.clear_known_dummy_stats(_django_apps(), connection.schema_editor())

    for c in (thalla, demo):
        c.refresh_from_db()
        assert c.follower_count is None
        assert c.platform_stats["instagram"] == {"followersCount": None, "avgViews": None, "engagementRate": None}
        assert c.social_media_links["instagram"] == "handle"  # handles are real input, kept
    real.refresh_from_db()
    assert real.follower_count == 12000 and real.platform_stats["instagram"]["followersCount"] == 12000


def test_dummy_clearing_leaves_values_the_creator_has_since_changed() -> None:
    thalla = _seed("thalla-aravind-67c664", {"followersCount": 15400, "avgViews": 3000, "engagementRate": 5.1}, 15400)
    clear_migration.clear_known_dummy_stats(_django_apps(), connection.schema_editor())
    thalla.refresh_from_db()
    assert thalla.platform_stats["instagram"]["followersCount"] == 15400
    assert thalla.platform_stats["instagram"]["engagementRate"] == 5.1
    assert thalla.follower_count == 15400


def _django_apps():  # type: ignore[no-untyped-def]
    from django.apps import apps

    return apps
