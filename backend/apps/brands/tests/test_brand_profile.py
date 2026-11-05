from typing import Any

import pytest

from apps.brands.models import Brand

pytestmark = pytest.mark.django_db


def test_update_brand_industry(brand: Any) -> None:
    resp = brand.patch("/v1/brands/me/", {"industry": "Food & Beverage"}, format="json")
    assert resp.status_code == 200, resp.json()
    data = resp.json()["data"]
    assert data["industry"] == "Food & Beverage"
    assert data["category"] == "Food & Beverage"

    # Verify directly in DB
    db_brand = Brand.objects.get(pk=data["id"])
    assert db_brand.industry == "Food & Beverage"
    assert db_brand.category == "Food & Beverage"


def test_update_brand_category_alias(brand: Any) -> None:
    resp = brand.patch("/v1/brands/me/", {"category": "Health & Wellness"}, format="json")
    assert resp.status_code == 200, resp.json()
    data = resp.json()["data"]
    assert data["industry"] == "Health & Wellness"
    assert data["category"] == "Health & Wellness"

    db_brand = Brand.objects.get(pk=data["id"])
    assert db_brand.industry == "Health & Wellness"
    assert db_brand.category == "Health & Wellness"


def test_get_brand_returns_category_and_industry(brand: Any) -> None:
    brand.patch("/v1/brands/me/", {"industry": "Apparel & Fashion"}, format="json")
    resp = brand.get("/v1/brands/me/")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["industry"] == "Apparel & Fashion"
    assert data["category"] == "Apparel & Fashion"
