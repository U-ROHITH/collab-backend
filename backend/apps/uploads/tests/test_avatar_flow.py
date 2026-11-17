from typing import Any

import pytest

from apps.uploads.tests.test_uploads import png_with_gps_exif, staging_key

pytestmark = pytest.mark.django_db


def test_uploads_flexible_payload_names(creator: Any, fake_storage: Any) -> None:
    # Test mimeType instead of contentType, sizeBytes instead of size, and extra filename
    r = creator.post(
        "/v1/uploads/",
        {
            "kind": "avatar",
            "mimeType": "image/jpeg",
            "sizeBytes": 2048,
            "filename": "my-photo.jpg",
        },
        format="json",
    )
    assert r.status_code == 201, r.json()
    data = r.json()["data"]
    assert data["upload"]["kind"] == "avatar"
    assert data["upload"]["status"] == "pending"

    # Test fileSize
    r2 = creator.post(
        "/v1/uploads/",
        {
            "kind": "avatar",
            "contentType": "image/png",
            "fileSize": 1024,
            "name": "profile.png",
        },
        format="json",
    )
    assert r2.status_code == 201, r2.json()


def test_creator_avatar_upload_and_auth_me(creator: Any, fake_storage: Any) -> None:
    # Initially avatarUrl is None
    me = creator.get("/v1/auth/me/").json()["data"]
    assert me["avatarUrl"] is None

    creator_me = creator.get("/v1/creators/me/").json()["data"]
    assert creator_me["avatarUrl"] is None

    # Upload avatar
    data = png_with_gps_exif()
    up_res = creator.post(
        "/v1/uploads/",
        {"kind": "avatar", "mimeType": "image/png", "size": len(data), "filename": "avatar.png"},
        format="json",
    ).json()["data"]

    fake_storage.put("private", staging_key(up_res["upload"]["id"]), data, "image/png")
    done = creator.post(f"/v1/uploads/{up_res['upload']['id']}/complete/")
    assert done.status_code == 200
    public_url = done.json()["data"]["url"]
    assert public_url

    # Check /v1/auth/me/ has avatarUrl
    me_after = creator.get("/v1/auth/me/").json()["data"]
    assert me_after["avatarUrl"] == public_url

    # Check /v1/creators/me/ has avatarUrl
    cr_after = creator.get("/v1/creators/me/").json()["data"]
    assert cr_after["avatarUrl"] == public_url


def test_brand_avatar_upload_and_auth_me(brand: Any, fake_storage: Any) -> None:
    # Brand can upload avatar kind or brand_logo kind
    data = png_with_gps_exif()
    up_res = brand.post(
        "/v1/uploads/",
        {"kind": "avatar", "contentType": "image/png", "size": len(data)},
        format="json",
    ).json()["data"]

    fake_storage.put("private", staging_key(up_res["upload"]["id"]), data, "image/png")
    done = brand.post(f"/v1/uploads/{up_res['upload']['id']}/complete/")
    assert done.status_code == 200
    public_url = done.json()["data"]["url"]

    # Check /v1/auth/me/ has avatarUrl
    me = brand.get("/v1/auth/me/").json()["data"]
    assert me["avatarUrl"] == public_url

    # Check /v1/brands/me/ has logoUrl and avatarUrl
    b_me = brand.get("/v1/brands/me/").json()["data"]
    assert b_me["logoUrl"] == public_url
    assert b_me["avatarUrl"] == public_url


def test_patch_creator_avatar_url_and_upload_id(creator: Any, fake_storage: Any) -> None:
    # Direct patch of avatarUrl
    r = creator.patch("/v1/creators/me/", {"avatarUrl": "https://cdn.example.com/pic.jpg"}, format="json")
    assert r.status_code == 200, r.json()
    assert r.json()["data"]["avatarUrl"] == "https://cdn.example.com/pic.jpg"

    # Patch with avatarUploadId
    data = png_with_gps_exif()
    up = creator.post(
        "/v1/uploads/",
        {"kind": "avatar", "size": len(data), "contentType": "image/png"},
        format="json",
    ).json()["data"]["upload"]
    fake_storage.put("private", staging_key(up["id"]), data, "image/png")
    done = creator.post(f"/v1/uploads/{up['id']}/complete/").json()["data"]

    r2 = creator.patch("/v1/creators/me/", {"avatarUploadId": up["id"]}, format="json")
    assert r2.status_code == 200
    assert r2.json()["data"]["avatarUrl"] == done["url"]


def test_patch_brand_logo_and_avatar(brand: Any, fake_storage: Any) -> None:
    # Direct patch of logoUrl / avatarUrl
    r = brand.patch("/v1/brands/me/", {"logoUrl": "https://cdn.example.com/logo.jpg"}, format="json")
    assert r.status_code == 200, r.json()
    assert r.json()["data"]["logoUrl"] == "https://cdn.example.com/logo.jpg"
    assert r.json()["data"]["avatarUrl"] == "https://cdn.example.com/logo.jpg"

    # Patch with avatarUrl alias
    r2 = brand.patch("/v1/brands/me/", {"avatarUrl": "https://cdn.example.com/brand-avatar.jpg"}, format="json")
    assert r2.status_code == 200
    assert r2.json()["data"]["logoUrl"] == "https://cdn.example.com/brand-avatar.jpg"
