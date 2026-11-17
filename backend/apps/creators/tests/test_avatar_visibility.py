from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.core.parties import brand_public, creator_public
from apps.identity.models import Role, User
from apps.uploads.tests.test_uploads import png_with_gps_exif, staging_key

pytestmark = pytest.mark.django_db


def _upload_avatar(client: APIClient, fake_storage: Any) -> str:
    data = png_with_gps_exif()
    res = client.post(
        "/v1/uploads/",
        {"kind": "avatar", "mimeType": "image/png", "size": len(data), "filename": "avatar.png"},
        format="json",
    )
    assert res.status_code == 201, res.json()
    up = res.json()["data"]["upload"]
    fake_storage.put("private", staging_key(up["id"]), data, "image/png")
    done = client.post(f"/v1/uploads/{up['id']}/complete/").json()["data"]
    return done["url"]


def test_owner_avatar_upload_and_visibility(
    creator: APIClient,
    fake_storage: Any,
) -> None:
    avatar_url = _upload_avatar(creator, fake_storage)
    assert avatar_url

    # Check /v1/creators/me/
    me_resp = creator.get("/v1/creators/me/")
    assert me_resp.status_code == 200
    data = me_resp.json()["data"]
    assert data["avatarUrl"] == avatar_url
    assert data["avatar_url"] == avatar_url
    assert data["avatar"] == avatar_url
    assert data["image"] == avatar_url

    # Check /v1/auth/me/
    auth_resp = creator.get("/v1/auth/me/")
    assert auth_resp.status_code == 200
    auth_data = auth_resp.json()["data"]
    assert auth_data["avatarUrl"] == avatar_url
    assert auth_data["avatar_url"] == avatar_url
    assert auth_data["avatar"] == avatar_url
    assert auth_data["image"] == avatar_url


def test_approved_creator_search_and_detail_visibility(
    creator: APIClient,
    anon: APIClient,
    brand: APIClient,
    fake_storage: Any,
) -> None:
    avatar_url = _upload_avatar(creator, fake_storage)
    creator_id = str(creator.user.creator.id)

    # 1. Anonymous search
    search_resp = anon.get("/v1/creators/")
    assert search_resp.status_code == 200
    results = search_resp.json()["data"]
    matching = [c for c in results if c["id"] == creator_id]
    assert len(matching) == 1
    found = matching[0]
    assert found["avatarUrl"] == avatar_url
    assert found["avatar_url"] == avatar_url
    assert found["avatar"] == avatar_url
    assert found["image"] == avatar_url

    # 2. Anonymous detail
    detail_resp = anon.get(f"/v1/creators/{creator_id}/")
    assert detail_resp.status_code == 200
    detail_data = detail_resp.json()["data"]
    assert detail_data["avatarUrl"] == avatar_url
    assert detail_data["avatar_url"] == avatar_url
    assert detail_data["avatar"] == avatar_url
    assert detail_data["image"] == avatar_url

    # 3. Brand detail
    brand_detail = brand.get(f"/v1/creators/{creator_id}/")
    assert brand_detail.status_code == 200
    assert brand_detail.json()["data"]["avatarUrl"] == avatar_url


def test_pending_creator_visibility_rules(
    make_creator: Any,
    anon: APIClient,
    brand: APIClient,
    fake_storage: Any,
) -> None:
    pending_creator = make_creator(name="Pending Creator", approved=False)
    avatar_url = _upload_avatar(pending_creator, fake_storage)
    creator_id = str(pending_creator.user.creator.id)

    # 1. Excluded from public search
    search_resp = anon.get("/v1/creators/")
    assert search_resp.status_code == 200
    results = search_resp.json()["data"]
    assert not any(c["id"] == creator_id for c in results)

    # 2. 404 for anonymous guest
    anon_resp = anon.get(f"/v1/creators/{creator_id}/")
    assert anon_resp.status_code == 404

    # 3. 404 for unrelated brand
    unrelated_brand_resp = brand.get(f"/v1/creators/{creator_id}/")
    assert unrelated_brand_resp.status_code == 404

    # 4. 200 for the creator themselves (previewing their profile page)
    self_resp = pending_creator.get(f"/v1/creators/{creator_id}/")
    assert self_resp.status_code == 200
    self_data = self_resp.json()["data"]
    assert self_data["avatarUrl"] == avatar_url
    assert self_data["avatar_url"] == avatar_url
    assert self_data["avatar"] == avatar_url
    assert self_data["image"] == avatar_url

    # 5. 200 for admin
    admin_user = User.objects.create_user(
        email="admin@example.com",
        role=Role.ADMIN,
        is_staff=True,
    )
    admin_client = APIClient()
    from apps.identity.services import issue_admin_tokens

    admin_client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_admin_tokens(admin_user).access}")
    admin_resp = admin_client.get(f"/v1/creators/{creator_id}/")
    assert admin_resp.status_code == 200
    assert admin_resp.json()["data"]["avatarUrl"] == avatar_url


def test_brand_sees_creator_who_applied_to_campaign(
    creator: APIClient,
    brand: APIClient,
    campaign_payload: dict[str, Any],
    fake_storage: Any,
) -> None:
    avatar_url = _upload_avatar(creator, fake_storage)
    creator_obj = creator.user.creator
    creator_id = str(creator_obj.id)

    # Brand creates a campaign
    camp_resp = brand.post("/v1/campaigns/", campaign_payload, format="json")
    assert camp_resp.status_code == 201
    camp_id = camp_resp.json()["data"]["id"]

    # Creator applies with a bid
    bid_resp = creator.post(
        f"/v1/campaigns/{camp_id}/bids/",
        {"price": 800_000, "message": "I'd love to make this Reel!", "deliveryDays": 5},
        format="json",
    )
    assert bid_resp.status_code == 201, bid_resp.json()

    # Brand views bids
    bids_list = brand.get(f"/v1/campaigns/{camp_id}/bids/")
    assert bids_list.status_code == 200
    bids_data = bids_list.json()["data"]
    assert len(bids_data) == 1
    bid_creator = bids_data[0]["creator"]
    assert bid_creator["id"] == creator_id
    assert bid_creator["avatarUrl"] == avatar_url
    assert bid_creator["avatar_url"] == avatar_url
    assert bid_creator["avatar"] == avatar_url
    assert bid_creator["image"] == avatar_url

    # Brand views applicant's public profile page directly
    applicant_detail = brand.get(f"/v1/creators/{creator_id}/")
    assert applicant_detail.status_code == 200
    detail_data = applicant_detail.json()["data"]
    assert detail_data["avatarUrl"] == avatar_url
    assert detail_data["avatar_url"] == avatar_url
    assert detail_data["avatar"] == avatar_url
    assert detail_data["image"] == avatar_url


def test_brand_sees_pending_creator_with_conversation(
    make_creator: Any,
    brand: APIClient,
    fake_storage: Any,
) -> None:
    from apps.messaging.models import Conversation

    pending_creator = make_creator(name="Pending With Convo", approved=False)
    avatar_url = _upload_avatar(pending_creator, fake_storage)
    creator_obj = pending_creator.user.creator
    creator_id = str(creator_obj.id)
    brand_obj = brand.user.brand

    # Unrelated brand gets 404
    assert brand.get(f"/v1/creators/{creator_id}/").status_code == 404

    # Now a conversation is created between them
    Conversation.objects.create(creator=creator_obj, brand=brand_obj)

    # Now brand can view the pending creator's profile page and avatar
    resp = brand.get(f"/v1/creators/{creator_id}/")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["avatarUrl"] == avatar_url
    assert data["avatar_url"] == avatar_url
    assert data["avatar"] == avatar_url
    assert data["image"] == avatar_url


def test_party_helpers_include_all_avatar_aliases(creator: APIClient, brand: APIClient) -> None:
    c = creator.user.creator
    c.avatar_url = "https://cdn.example.com/creator-pic.webp"
    c.save()

    cp = creator_public(c)
    assert cp["avatarUrl"] == "https://cdn.example.com/creator-pic.webp"
    assert cp["avatar_url"] == "https://cdn.example.com/creator-pic.webp"
    assert cp["avatar"] == "https://cdn.example.com/creator-pic.webp"
    assert cp["image"] == "https://cdn.example.com/creator-pic.webp"

    b = brand.user.brand
    b.logo_url = "https://cdn.example.com/brand-logo.webp"
    b.save()

    bp = brand_public(b)
    assert bp["logoUrl"] == "https://cdn.example.com/brand-logo.webp"
    assert bp["logo_url"] == "https://cdn.example.com/brand-logo.webp"
    assert bp["avatarUrl"] == "https://cdn.example.com/brand-logo.webp"
    assert bp["avatar_url"] == "https://cdn.example.com/brand-logo.webp"
    assert bp["avatar"] == "https://cdn.example.com/brand-logo.webp"
    assert bp["image"] == "https://cdn.example.com/brand-logo.webp"
