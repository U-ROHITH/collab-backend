from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from tests.fixtures import USAGE_RIGHTS

pytestmark = pytest.mark.django_db


def test_filter_by_status(brand: APIClient, campaign_payload: dict[str, Any]) -> None:
    # 1. Create an open campaign
    resp1 = brand.post("/v1/campaigns/", campaign_payload, format="json")
    assert resp1.status_code == 201
    open_id = resp1.json()["data"]["id"]

    # 2. Create a draft campaign
    draft_payload = {**campaign_payload, "title": "Draft Diwali Campaign", "status": "draft"}
    resp2 = brand.post("/v1/campaigns/", draft_payload, format="json")
    assert resp2.status_code == 201
    draft_id = resp2.json()["data"]["id"]

    # Filter by draft
    draft_resp = brand.get("/v1/campaigns/?status=draft")
    assert draft_resp.status_code == 200
    draft_data = draft_resp.json()["data"]
    assert len(draft_data) == 1
    assert draft_data[0]["id"] == draft_id
    assert draft_data[0]["status"] == "draft"

    # Filter by open
    open_resp = brand.get("/v1/campaigns/?status=open")
    assert open_resp.status_code == 200
    open_data = open_resp.json()["data"]
    assert any(c["id"] == open_id for c in open_data)
    assert all(c["status"] == "open" for c in open_data)


def test_filter_by_platform(brand: APIClient, creator: Any, campaign_payload: dict[str, Any]) -> None:
    # 1. Instagram campaign
    brand.post(
        "/v1/campaigns/",
        {**campaign_payload, "title": "Insta Campaign", "platform": "instagram"},
        format="json",
    )

    # 2. YouTube campaign
    brand.post("/v1/campaigns/", {**campaign_payload, "title": "YT Video", "platform": "youtube"}, format="json")

    # Creator filters by platform=instagram
    insta_resp = creator.get("/v1/campaigns/?platform=instagram")
    assert insta_resp.status_code == 200
    insta_data = insta_resp.json()["data"]
    assert len(insta_data) >= 1
    assert all(c["platform"] == "instagram" for c in insta_data)

    # Creator filters by platform=youtube
    yt_resp = creator.get("/v1/campaigns/?platform=youtube")
    assert yt_resp.status_code == 200
    yt_data = yt_resp.json()["data"]
    assert len(yt_data) >= 1
    assert all(c["platform"] == "youtube" for c in yt_data)


def test_filter_by_category_and_industry(make_brand: Any, creator: Any, campaign_payload: dict[str, Any]) -> None:
    brand_food = make_brand(name="Haldiram Snacks")
    brand_food.user.brand.industry = "Food & Beverage"
    brand_food.user.brand.save()
    brand_food.post(
        "/v1/campaigns/",
        {**campaign_payload, "title": "Namkeen Campaign", "brief": "Showcase spicy namkeen"},
        format="json",
    )

    brand_fashion = make_brand(name="FabIndia Apparel")
    brand_fashion.user.brand.industry = "Apparel & Fashion"
    brand_fashion.user.brand.save()
    brand_fashion.post(
        "/v1/campaigns/",
        {**campaign_payload, "title": "Ethnic Kurtas", "brief": "Diwali ethnic clothing lookbook"},
        format="json",
    )

    # Filter by category=Fashion
    fashion_resp = creator.get("/v1/campaigns/?category=Fashion")
    assert fashion_resp.status_code == 200
    fashion_data = fashion_resp.json()["data"]
    assert len(fashion_data) == 1
    assert fashion_data[0]["title"] == "Ethnic Kurtas"

    # Filter by industry=Food
    food_resp = creator.get("/v1/campaigns/?industry=Food")
    assert food_resp.status_code == 200
    food_data = food_resp.json()["data"]
    assert len(food_data) == 1
    assert food_data[0]["title"] == "Namkeen Campaign"


def test_filter_by_city_and_territory(brand: APIClient, creator: Any, campaign_payload: dict[str, Any]) -> None:
    # Campaign in Warangal
    rights_warangal = {**USAGE_RIGHTS, "territory": "India", "city": "Warangal"}
    brand.post(
        "/v1/campaigns/",
        {
            **campaign_payload,
            "title": "Warangal Local Showcase",
            "brief": "Explore Warangal fort delicacies",
            "usageRights": rights_warangal,
        },
        format="json",
    )

    # Campaign in Mumbai
    rights_mumbai = {**USAGE_RIGHTS, "territory": "India", "city": "Mumbai"}
    brand.post(
        "/v1/campaigns/",
        {
            **campaign_payload,
            "title": "Mumbai Street Food",
            "brief": "Explore Mumbai street food delicacies",
            "usageRights": rights_mumbai,
        },
        format="json",
    )

    # Filter by city=Warangal
    resp_city = creator.get("/v1/campaigns/?city=Warangal")
    assert resp_city.status_code == 200
    city_data = resp_city.json()["data"]
    assert len(city_data) == 1
    assert city_data[0]["title"] == "Warangal Local Showcase"

    # Filter by territory=India
    resp_territory = creator.get("/v1/campaigns/?territory=India")
    assert resp_territory.status_code == 200
    assert len(resp_territory.json()["data"]) == 2

    # Filter by non-existent city
    resp_empty = creator.get("/v1/campaigns/?city=Hyderabad")
    assert resp_empty.status_code == 200
    assert resp_empty.json()["data"] == []


def test_filter_by_budget_range(brand: APIClient, creator: Any, campaign_payload: dict[str, Any]) -> None:
    # Campaign A: 500k to 1M paise (5k to 10k INR)
    brand.post(
        "/v1/campaigns/",
        {**campaign_payload, "title": "Budget Small", "budgetMin": 500_000, "budgetMax": 1_000_000},
        format="json",
    )

    # Campaign B: 2M to 5M paise (20k to 50k INR)
    brand.post(
        "/v1/campaigns/",
        {**campaign_payload, "title": "Budget High", "budgetMin": 2_000_000, "budgetMax": 5_000_000},
        format="json",
    )

    # Filter budgetMin__gte=1_500_000
    resp_gte = creator.get("/v1/campaigns/?budgetMin__gte=1500000")
    assert resp_gte.status_code == 200
    data_gte = resp_gte.json()["data"]
    assert len(data_gte) == 1
    assert data_gte[0]["title"] == "Budget High"

    # Filter budgetMax__lte=1_500_000
    resp_lte = creator.get("/v1/campaigns/?budgetMax__lte=1500000")
    assert resp_lte.status_code == 200
    data_lte = resp_lte.json()["data"]
    assert len(data_lte) == 1
    assert data_lte[0]["title"] == "Budget Small"

    # Filter budget range combined
    resp_range = creator.get("/v1/campaigns/?budgetMin__gte=500000&budgetMax__lte=1000000")
    assert resp_range.status_code == 200
    assert len(resp_range.json()["data"]) == 1
    assert resp_range.json()["data"][0]["title"] == "Budget Small"


def test_search_by_title_and_brief(brand: APIClient, creator: Any, campaign_payload: dict[str, Any]) -> None:
    brand.post(
        "/v1/campaigns/",
        {**campaign_payload, "title": "Organic Green Tea", "brief": "Highlight natural detox ingredients."},
        format="json",
    )
    brand.post(
        "/v1/campaigns/",
        {**campaign_payload, "title": "Wireless Earbuds Pro", "brief": "Active noise cancelling workout audio."},
        format="json",
    )

    # Search by keyword in title
    resp_search_title = creator.get("/v1/campaigns/?search=Earbuds")
    assert resp_search_title.status_code == 200
    data_title = resp_search_title.json()["data"]
    assert len(data_title) == 1
    assert data_title[0]["title"] == "Wireless Earbuds Pro"

    # Search by keyword in brief
    resp_search_brief = creator.get("/v1/campaigns/?search=detox")
    assert resp_search_brief.status_code == 200
    data_brief = resp_search_brief.json()["data"]
    assert len(data_brief) == 1
    assert data_brief[0]["title"] == "Organic Green Tea"

    # Query with alias q
    resp_q = creator.get("/v1/campaigns/?q=workout")
    assert resp_q.status_code == 200
    assert len(resp_q.json()["data"]) == 1


def test_multi_parameter_filtering_and_empty_results(
    brand: APIClient, creator: Any, campaign_payload: dict[str, Any]
) -> None:
    brand.post(
        "/v1/campaigns/",
        {
            **campaign_payload,
            "title": "Handcrafted Leather Boots",
            "brief": "Premium winter fashion boot showcase",
            "platform": "instagram",
            "budgetMin": 1_000_000,
            "budgetMax": 2_000_000,
        },
        format="json",
    )

    # Multi-parameter match
    resp_match = creator.get("/v1/campaigns/?platform=instagram&search=Boots&budgetMin__gte=500000")
    assert resp_match.status_code == 200
    assert len(resp_match.json()["data"]) == 1

    # Contradictory search returning empty list
    resp_empty = creator.get("/v1/campaigns/?platform=youtube&search=Boots")
    assert resp_empty.status_code == 200
    assert resp_empty.json()["data"] == []
    assert resp_empty.json()["success"] is True


def test_sorting_and_cursor_pagination(brand: APIClient, creator: Any, campaign_payload: dict[str, Any]) -> None:
    now = timezone.now()
    # Campaign 1 closes in 5 days, budgetMax 1M
    brand.post(
        "/v1/campaigns/",
        {
            **campaign_payload,
            "title": "Campaign Early",
            "closesAt": (now + timedelta(days=5)).isoformat(),
            "budgetMin": 500_000,
            "budgetMax": 1_000_000,
        },
        format="json",
    )
    # Campaign 2 closes in 15 days, budgetMax 4M
    brand.post(
        "/v1/campaigns/",
        {
            **campaign_payload,
            "title": "Campaign Later",
            "closesAt": (now + timedelta(days=15)).isoformat(),
            "budgetMin": 2_000_000,
            "budgetMax": 4_000_000,
        },
        format="json",
    )

    # Sort by closesAt ascending
    resp_sort_closes = creator.get("/v1/campaigns/?sort=closesAt")
    assert resp_sort_closes.status_code == 200
    data_closes = resp_sort_closes.json()["data"]
    assert len(data_closes) == 2
    assert data_closes[0]["title"] == "Campaign Early"
    assert data_closes[1]["title"] == "Campaign Later"

    # Sort by budgetMax descending
    resp_sort_budget = creator.get("/v1/campaigns/?sort=-budgetMax")
    assert resp_sort_budget.status_code == 200
    data_budget = resp_sort_budget.json()["data"]
    assert data_budget[0]["title"] == "Campaign Later"
    assert data_budget[1]["title"] == "Campaign Early"

    # Pagination test with limit=1
    page1 = creator.get("/v1/campaigns/?limit=1&sort=closesAt")
    assert page1.status_code == 200
    page1_json = page1.json()
    assert len(page1_json["data"]) == 1
    assert page1_json["data"][0]["title"] == "Campaign Early"
    assert page1_json["meta"]["hasMore"] is True
    next_cursor = page1_json["meta"]["nextCursor"]
    assert next_cursor is not None

    # Fetch page 2 using next cursor
    page2 = creator.get(f"/v1/campaigns/?limit=1&sort=closesAt&cursor={next_cursor}")
    assert page2.status_code == 200
    page2_json = page2.json()
    assert len(page2_json["data"]) == 1
    assert page2_json["data"][0]["title"] == "Campaign Later"
