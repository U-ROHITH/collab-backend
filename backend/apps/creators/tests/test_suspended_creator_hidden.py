from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.identity.models import AdminRole, Role, User
from apps.identity.services import issue_admin_tokens

pytestmark = pytest.mark.django_db


def _make_admin_client() -> APIClient:
    admin = User.objects.create_user(
        email="admin_safety@linkloom.test",
        password="AdminPass123!",
        role=Role.ADMIN,
        admin_role=AdminRole.SUPER_ADMIN,
        is_staff=True,
    )
    client = APIClient()
    tokens = issue_admin_tokens(admin)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens.access}")
    return client


def test_suspended_creator_is_hidden_from_discovery_and_detail(
    anon: APIClient, brand: APIClient, creator: APIClient, package: Any
) -> None:
    admin_client = _make_admin_client()
    creator_user = creator.user
    cr_id = str(creator_user.creator.id)

    # 1. Before suspension: visible in discovery and detail
    search_resp = brand.get("/v1/creators/")
    assert search_resp.status_code == 200
    assert any(c["id"] == cr_id for c in search_resp.json()["data"])

    detail_resp = brand.get(f"/v1/creators/{cr_id}/")
    assert detail_resp.status_code == 200

    # 2. Suspend creator via admin endpoint
    suspend_resp = admin_client.post(
        f"/v1/admin/users/{creator_user.id}/suspend/",
        {"reason": "Policy violation"},
        format="json",
    )
    assert suspend_resp.status_code == 200

    # 3. After suspension: hidden from brand discovery list
    search_after = brand.get("/v1/creators/")
    assert search_after.status_code == 200
    assert not any(c["id"] == cr_id for c in search_after.json()["data"])

    # 4. Hidden from guest discovery list
    guest_search = anon.get("/v1/creators/")
    assert guest_search.status_code == 200
    assert not any(c["id"] == cr_id for c in guest_search.json()["data"])

    # 5. Detail view returns 404 to brands and guests
    assert brand.get(f"/v1/creators/{cr_id}/").status_code == 404
    assert anon.get(f"/v1/creators/{cr_id}/").status_code == 404

    # 6. Detail view still accessible to admin
    admin_detail = admin_client.get(f"/v1/admin/users/creators/{cr_id}/")
    assert admin_detail.status_code == 200
    assert admin_detail.json()["data"]["userStatus"] == "suspended"

    # 7. Reactivate creator
    reactivate_resp = admin_client.post(
        f"/v1/admin/users/{creator_user.id}/reactivate/",
        {"reason": "Appeal accepted"},
        format="json",
    )
    assert reactivate_resp.status_code == 200

    # 8. Creator is visible again
    search_reactivated = brand.get("/v1/creators/")
    assert search_reactivated.status_code == 200
    assert any(c["id"] == cr_id for c in search_reactivated.json()["data"])
    assert brand.get(f"/v1/creators/{cr_id}/").status_code == 200
