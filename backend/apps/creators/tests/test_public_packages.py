"""
Tests: Public package data & pricing tiers on creator profile endpoints.

Covers:
  - GET /v1/creators/{id}/   — public profile includes active packages
  - GET /v1/creators/me/     — self profile includes all own packages
  - GET /v1/creators/me/packages/   — creator CRUD list
  - POST /v1/creators/me/packages/  — create package
  - PATCH /v1/creators/me/packages/{id}/ — update package
  - DELETE /v1/creators/me/packages/{id}/ — deactivate package (soft-delete)
  - Brand/anon access restrictions on management endpoints
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.creators.models import CreatorPackage

pytestmark = pytest.mark.django_db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_PACKAGE: dict[str, Any] = {
    "title": "1 Instagram Reel",
    "description": "One professional 30-second Reel with original audio and colour-grade.",
    "platform": "instagram",
    "deliverables": "1 Reel (30s), 3 story frames",
    "price": 1_500_000,  # ₹15,000 in paise
    "deliveryDays": 7,
}


def _create_package(creator_client: APIClient, **overrides: Any) -> dict[str, Any]:
    """POST a package and return the response data payload."""
    payload = {**_VALID_PACKAGE, **overrides}
    resp = creator_client.post("/v1/creators/me/packages/", payload, format="json")
    assert resp.status_code == 201, resp.json()
    return resp.json()["data"]


# ---------------------------------------------------------------------------
# 1. Public creator profile includes packages
# ---------------------------------------------------------------------------


def test_public_creator_profile_includes_packages(
    brand: APIClient,
    make_creator: Callable[..., APIClient],
) -> None:
    """Brand calling GET /v1/creators/{id}/ receives a 'packages' list."""
    creator_client = make_creator("Priya Sharma")
    pkg = _create_package(creator_client)

    creator_id = creator_client.user.creator.pk
    resp = brand.get(f"/v1/creators/{creator_id}/")
    assert resp.status_code == 200, resp.json()
    data = resp.json()["data"]

    assert "packages" in data, "Response must include 'packages' key"
    assert isinstance(data["packages"], list)
    assert len(data["packages"]) == 1

    p = data["packages"][0]
    assert p["id"] == pkg["id"]
    assert p["title"] == _VALID_PACKAGE["title"]


def test_public_packages_include_all_tier_fields(
    brand: APIClient,
    make_creator: Callable[..., APIClient],
) -> None:
    """Every package in the public response exposes all required tier fields."""
    creator_client = make_creator("Rohan Verma")
    _create_package(creator_client)

    creator_id = creator_client.user.creator.pk
    resp = brand.get(f"/v1/creators/{creator_id}/")
    p = resp.json()["data"]["packages"][0]

    # Required top-level fields
    for field in ("id", "title", "description", "platform", "deliverables", "price", "deliveryDays", "active"):
        assert field in p, f"Missing field: {field}"

    # Price must be money object
    assert isinstance(p["price"], dict), "price must be a money object"
    assert p["price"]["amount"] == _VALID_PACKAGE["price"]
    assert p["price"]["currency"] == "INR"

    # Delivery timeline
    assert p["deliveryDays"] == _VALID_PACKAGE["deliveryDays"]

    # Description & deliverables scope
    assert p["description"] == _VALID_PACKAGE["description"]
    assert p["deliverables"] == _VALID_PACKAGE["deliverables"]

    # Active flag
    assert p["active"] is True


def test_inactive_packages_not_exposed_on_public_profile(
    brand: APIClient,
    make_creator: Callable[..., APIClient],
) -> None:
    """Deactivated packages must not appear in GET /v1/creators/{id}/."""
    creator_client = make_creator("Aarav Patel")
    pkg_data = _create_package(creator_client)
    pkg_id = pkg_data["id"]

    # Deactivate it
    del_resp = creator_client.delete(f"/v1/creators/me/packages/{pkg_id}/")
    assert del_resp.status_code == 204

    creator_id = creator_client.user.creator.pk
    resp = brand.get(f"/v1/creators/{creator_id}/")
    assert resp.status_code == 200
    assert resp.json()["data"]["packages"] == [], "Deactivated package must not appear publicly"


def test_public_profile_shows_multiple_active_packages_sorted_by_price(
    brand: APIClient,
    make_creator: Callable[..., APIClient],
) -> None:
    """Multiple active packages appear sorted ascending by price."""
    creator_client = make_creator("Meera Krishnan")
    _create_package(creator_client, title="Premium", price=5_000_000)
    _create_package(creator_client, title="Starter", price=500_000)
    _create_package(creator_client, title="Standard", price=2_000_000)

    creator_id = creator_client.user.creator.pk
    resp = brand.get(f"/v1/creators/{creator_id}/")
    packages = resp.json()["data"]["packages"]

    assert len(packages) == 3
    prices = [p["price"]["amount"] for p in packages]
    assert prices == sorted(prices), "Packages must be sorted by price ascending"
    assert packages[0]["title"] == "Starter"


# ---------------------------------------------------------------------------
# 2. Creator self profile includes own packages
# ---------------------------------------------------------------------------


def test_creator_self_profile_includes_own_packages(creator: APIClient) -> None:
    """GET /v1/creators/me/ exposes the creator's own packages (all, not just active)."""
    _create_package(creator)
    resp = creator.get("/v1/creators/me/")
    assert resp.status_code == 200, resp.json()
    data = resp.json()["data"]
    assert "packages" in data
    assert len(data["packages"]) >= 1


def test_creator_self_profile_includes_inactive_packages(creator: APIClient) -> None:
    """GET /v1/creators/me/ shows both active and deactivated packages."""
    pkg_data = _create_package(creator)
    pkg_id = pkg_data["id"]

    # Deactivate
    creator.delete(f"/v1/creators/me/packages/{pkg_id}/")

    resp = creator.get("/v1/creators/me/")
    packages = resp.json()["data"]["packages"]
    deactivated = [p for p in packages if p["id"] == pkg_id]
    assert len(deactivated) == 1
    assert deactivated[0]["active"] is False


# ---------------------------------------------------------------------------
# 3. Package CRUD — creation
# ---------------------------------------------------------------------------


def test_creator_can_list_own_packages(creator: APIClient) -> None:
    """GET /v1/creators/me/packages/ lists all packages for the authenticated creator."""
    _create_package(creator, title="Package A")
    _create_package(creator, title="Package B")

    resp = creator.get("/v1/creators/me/packages/")
    assert resp.status_code == 200, resp.json()
    data = resp.json()["data"]
    assert isinstance(data, list)
    assert len(data) == 2


def test_creator_can_create_package(creator: APIClient) -> None:
    """POST /v1/creators/me/packages/ creates and returns the package."""
    pkg = _create_package(creator)

    assert pkg["title"] == _VALID_PACKAGE["title"]
    assert pkg["platform"] == "instagram"
    assert pkg["price"]["amount"] == _VALID_PACKAGE["price"]
    assert pkg["price"]["currency"] == "INR"
    assert pkg["deliveryDays"] == _VALID_PACKAGE["deliveryDays"]
    assert pkg["description"] == _VALID_PACKAGE["description"]
    assert pkg["active"] is True

    # Must be persisted in DB
    assert CreatorPackage.objects.filter(pk=pkg["id"]).exists()


def test_package_validation_requires_positive_price(creator: APIClient) -> None:
    """Package creation rejects zero or negative price_paise."""
    resp = creator.post(
        "/v1/creators/me/packages/",
        {**_VALID_PACKAGE, "price": 0},
        format="json",
    )
    assert resp.status_code in (400, 422), resp.json()


def test_package_validation_delivery_days_range(creator: APIClient) -> None:
    """Package creation rejects deliveryDays outside 1–90."""
    resp = creator.post(
        "/v1/creators/me/packages/",
        {**_VALID_PACKAGE, "deliveryDays": 91},
        format="json",
    )
    assert resp.status_code == 400, resp.json()
    assert resp.json()["success"] is False


# ---------------------------------------------------------------------------
# 4. Package CRUD — update
# ---------------------------------------------------------------------------


def test_creator_can_update_package(creator: APIClient) -> None:
    """PATCH /v1/creators/me/packages/{id}/ partially updates the package."""
    pkg = _create_package(creator)
    pkg_id = pkg["id"]

    resp = creator.patch(
        f"/v1/creators/me/packages/{pkg_id}/",
        {"title": "Updated Reel", "price": 2_000_000},
        format="json",
    )
    assert resp.status_code == 200, resp.json()
    updated = resp.json()["data"]
    assert updated["title"] == "Updated Reel"
    assert updated["price"]["amount"] == 2_000_000

    db_pkg = CreatorPackage.objects.get(pk=pkg_id)
    assert db_pkg.title == "Updated Reel"
    assert db_pkg.price_paise == 2_000_000


def test_creator_cannot_update_other_creators_package(
    make_creator: Callable[..., APIClient],
) -> None:
    """A creator can only update their own packages — foreign IDs return 404."""
    creator_a = make_creator("Creator A")
    creator_b = make_creator("Creator B")

    pkg = _create_package(creator_a)
    resp = creator_b.patch(
        f"/v1/creators/me/packages/{pkg['id']}/",
        {"title": "Hacked"},
        format="json",
    )
    assert resp.status_code == 404, resp.json()


# ---------------------------------------------------------------------------
# 5. Package CRUD — deactivate
# ---------------------------------------------------------------------------


def test_creator_can_deactivate_package(creator: APIClient) -> None:
    """DELETE /v1/creators/me/packages/{id}/ soft-deletes (sets active=False)."""
    pkg = _create_package(creator)
    pkg_id = pkg["id"]

    resp = creator.delete(f"/v1/creators/me/packages/{pkg_id}/")
    assert resp.status_code == 204

    db_pkg = CreatorPackage.objects.get(pk=pkg_id)
    assert db_pkg.active is False, "DELETE must set active=False, not hard-delete"
    assert CreatorPackage.objects.filter(pk=pkg_id).exists(), "Record must still exist in DB"


def test_creator_cannot_deactivate_other_creators_package(
    make_creator: Callable[..., APIClient],
) -> None:
    creator_a = make_creator("Creator A2")
    creator_b = make_creator("Creator B2")

    pkg = _create_package(creator_a)
    resp = creator_b.delete(f"/v1/creators/me/packages/{pkg['id']}/")
    assert resp.status_code == 404, resp.json()


# ---------------------------------------------------------------------------
# 6. Access control — brands & anonymous users cannot manage packages
# ---------------------------------------------------------------------------


def test_brand_cannot_create_package(brand: APIClient) -> None:
    """Brands must be rejected from POST /v1/creators/me/packages/."""
    resp = brand.post("/v1/creators/me/packages/", _VALID_PACKAGE, format="json")
    assert resp.status_code in (403, 404), resp.json()


def test_brand_cannot_update_package(
    brand: APIClient,
    make_creator: Callable[..., APIClient],
) -> None:
    creator_client = make_creator("Sneha Joshi")
    pkg = _create_package(creator_client)
    resp = brand.patch(f"/v1/creators/me/packages/{pkg['id']}/", {"title": "X"}, format="json")
    assert resp.status_code in (403, 404), resp.json()


def test_brand_cannot_delete_package(
    brand: APIClient,
    make_creator: Callable[..., APIClient],
) -> None:
    creator_client = make_creator("Kavya Nair")
    pkg = _create_package(creator_client)
    resp = brand.delete(f"/v1/creators/me/packages/{pkg['id']}/")
    assert resp.status_code in (403, 404), resp.json()


def test_unauthenticated_cannot_manage_packages() -> None:
    """Anonymous clients are rejected on all package management endpoints."""
    anon = APIClient()
    resp = anon.post("/v1/creators/me/packages/", _VALID_PACKAGE, format="json")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 7. startingPrice on card reflects cheapest active package
# ---------------------------------------------------------------------------


def test_starting_price_reflects_cheapest_active_package(
    brand: APIClient,
    make_creator: Callable[..., APIClient],
) -> None:
    """startingPrice on the card/detail output is the cheapest active package price."""
    creator_client = make_creator("Divya Menon")
    _create_package(creator_client, title="Big", price=5_000_000)
    cheap = _create_package(creator_client, title="Small", price=800_000)

    creator_id = creator_client.user.creator.pk
    resp = brand.get(f"/v1/creators/{creator_id}/")
    assert resp.status_code == 200
    data = resp.json()["data"]

    assert data["startingPrice"] is not None
    assert data["startingPrice"]["amount"] == cheap["price"]["amount"]
    assert data["startingPrice"]["currency"] == "INR"


def test_starting_price_ignores_inactive_packages(
    brand: APIClient,
    make_creator: Callable[..., APIClient],
) -> None:
    """startingPrice must not reflect deactivated packages."""
    creator_client = make_creator("Anjali Singh")
    cheap = _create_package(creator_client, title="Cheap", price=100_000)
    expensive = _create_package(creator_client, title="Expensive", price=5_000_000)

    # Deactivate the cheap one
    creator_client.delete(f"/v1/creators/me/packages/{cheap['id']}/")

    creator_id = creator_client.user.creator.pk
    resp = brand.get(f"/v1/creators/{creator_id}/")
    data = resp.json()["data"]
    assert data["startingPrice"]["amount"] == expensive["price"]["amount"]
