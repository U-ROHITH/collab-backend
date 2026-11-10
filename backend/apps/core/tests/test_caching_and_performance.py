import pytest
from django.db import connection
from rest_framework.test import APIClient

from apps.core import cache as cache_utils
from apps.creators.models import Creator

pytestmark = pytest.mark.django_db


def test_starting_price_paise_sync_on_package_lifecycle(creator: APIClient) -> None:
    creator_obj: Creator = creator.user.creator

    # Initially no packages -> starting_price_paise is None
    creator_obj.refresh_from_db()
    assert creator_obj.starting_price_paise is None

    # 1. Create first package (₹15,000 = 1,500,000 paise)
    pkg1_resp = creator.post(
        "/v1/creators/me/packages/",
        {
            "title": "Standard Reel",
            "platform": "instagram",
            "deliverables": "1 Reel",
            "price": 1_500_000,
            "deliveryDays": 5,
        },
        format="json",
    )
    assert pkg1_resp.status_code == 201
    pkg1_id = pkg1_resp.json()["data"]["id"]

    creator_obj.refresh_from_db()
    assert creator_obj.starting_price_paise == 1_500_000
    assert creator_obj.starting_price == 1_500_000

    # 2. Create cheaper package (₹5,000 = 500,000 paise)
    pkg2_resp = creator.post(
        "/v1/creators/me/packages/",
        {
            "title": "Story Promo",
            "platform": "instagram",
            "deliverables": "1 Story",
            "price": 500_000,
            "deliveryDays": 2,
        },
        format="json",
    )
    assert pkg2_resp.status_code == 201
    pkg2_id = pkg2_resp.json()["data"]["id"]

    creator_obj.refresh_from_db()
    assert creator_obj.starting_price_paise == 500_000

    # 3. Update cheap package price to ₹20,000 (2,000,000 paise)
    creator.patch(f"/v1/creators/me/packages/{pkg2_id}/", {"price": 2_000_000}, format="json")
    creator_obj.refresh_from_db()
    # Now pkg1 (1,500,000) is cheapest again
    assert creator_obj.starting_price_paise == 1_500_000

    # 4. Deactivate pkg1
    creator.delete(f"/v1/creators/me/packages/{pkg1_id}/")
    creator_obj.refresh_from_db()
    # Now pkg2 (2,000,000) is the only active package
    assert creator_obj.starting_price_paise == 2_000_000

    # 5. Deactivate pkg2 -> no active packages
    creator.delete(f"/v1/creators/me/packages/{pkg2_id}/")
    creator_obj.refresh_from_db()
    assert creator_obj.starting_price_paise is None


def test_redis_cache_hit_and_miss_for_creator_detail(anon: APIClient, creator: APIClient) -> None:
    creator_id = creator.user.creator.id
    cache_utils.invalidate_creator_cache(creator_id)

    # First request: Cache MISS
    resp1 = anon.get(f"/v1/creators/{creator_id}/")
    assert resp1.status_code == 200
    assert resp1.headers.get("X-Cache") == "MISS"

    # Second request: Cache HIT
    resp2 = anon.get(f"/v1/creators/{creator_id}/")
    assert resp2.status_code == 200
    assert resp2.headers.get("X-Cache") == "HIT"
    assert resp2.json()["data"]["id"] == str(creator_id)

    # Invalidate via profile update
    creator.patch("/v1/creators/me/", {"bio": "Updated bio via cache test"}, format="json")

    # Third request after mutation: Cache MISS
    resp3 = anon.get(f"/v1/creators/{creator_id}/")
    assert resp3.status_code == 200
    assert resp3.headers.get("X-Cache") == "MISS"
    assert resp3.json()["data"]["bio"] == "Updated bio via cache test"


def test_redis_cache_hit_and_miss_for_open_campaigns(anon: APIClient, brand: APIClient, campaign_payload: dict) -> None:
    cache_utils.invalidate_campaigns_cache()

    # Create campaign
    brand.post("/v1/campaigns/", campaign_payload, format="json")

    # First anon request: Cache MISS
    r1 = anon.get("/v1/campaigns/")
    assert r1.status_code == 200
    assert r1.headers.get("X-Cache") == "MISS"

    # Second anon request: Cache HIT
    r2 = anon.get("/v1/campaigns/")
    assert r2.status_code == 200
    assert r2.headers.get("X-Cache") == "HIT"

    # Invalidate by creating another campaign
    payload2 = {**campaign_payload, "title": "Second Festive Hamper"}
    brand.post("/v1/campaigns/", payload2, format="json")

    # Next request: Cache MISS
    r3 = anon.get("/v1/campaigns/")
    assert r3.status_code == 200
    assert r3.headers.get("X-Cache") == "MISS"


def test_gin_trigram_indexes_exist() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT indexname, tablename
            FROM pg_indexes
            WHERE indexname IN ('creator_search_trgm_idx', 'campaign_search_trgm_idx')
            """
        )
        indexes = {row[0]: row[1] for row in cursor.fetchall()}

    assert "creator_search_trgm_idx" in indexes
    assert "campaign_search_trgm_idx" in indexes
