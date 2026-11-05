import io
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

from apps.creators.models import Creator
from apps.creators.serializers import MAX_PROFILE_PICTURE_SIZE_BYTES

pytestmark = pytest.mark.django_db


def _generate_test_image(name: str = "test.png", size: tuple[int, int] = (100, 100)) -> SimpleUploadedFile:
    file = io.BytesIO()
    image = Image.new("RGB", size, color="blue")
    image.save(file, format="PNG")
    file.seek(0)
    return SimpleUploadedFile(name, file.read(), content_type="image/png")


def test_update_profile_text_fields(creator: Any) -> None:
    payload = {
        "displayName": "Priya Sharma",
        "bio": "Lifestyle and fashion creator based in Bengaluru.",
        "location": "Bengaluru",
        "socialMediaUrls": {
            "instagram": "https://instagram.com/priyasharma",
            "youtube": "https://youtube.com/@priyasharma",
        },
    }
    resp = creator.patch("/v1/creators/me/", payload, format="json")
    assert resp.status_code == 200, resp.json()
    data = resp.json()["data"]
    assert data["displayName"] == "Priya Sharma"
    assert data["socialMediaUrls"] == payload["socialMediaUrls"]

    db_creator = Creator.objects.get(pk=data["id"])
    assert db_creator.display_name == "Priya Sharma"
    assert db_creator.social_media_links == payload["socialMediaUrls"]


def test_update_profile_with_name_alias(creator: Any) -> None:
    payload = {
        "name": "Priya S.",
        "socialLinks": {
            "instagram": "https://instagram.com/priyas",
        },
    }
    resp = creator.patch("/v1/creators/me/", payload, format="json")
    assert resp.status_code == 200, resp.json()
    data = resp.json()["data"]
    assert data["displayName"] == "Priya S."
    assert data["socialMediaUrls"] == {"instagram": "https://instagram.com/priyas"}


def test_update_profile_multipart_with_valid_image(creator: Any) -> None:
    image_file = _generate_test_image("avatar.png")
    payload = {
        "displayName": "Aarav Patel",
        "profilePicture": image_file,
        "socialMediaUrls": '{"instagram": "https://instagram.com/aarav"}',
    }
    resp = creator.patch("/v1/creators/me/", payload, format="multipart")
    assert resp.status_code == 200, resp.json()
    data = resp.json()["data"]
    assert data["displayName"] == "Aarav Patel"
    assert data["profilePicture"] is not None
    assert "avatar" in data["profilePicture"]
    assert data["profilePicture"].endswith(".png")
    assert data["socialMediaUrls"] == {"instagram": "https://instagram.com/aarav"}

    # Verify directly in DB
    db_creator = Creator.objects.get(pk=data["id"])
    assert db_creator.display_name == "Aarav Patel"
    assert bool(db_creator.profile_picture) is True
    assert "avatar" in db_creator.profile_picture.name
    assert db_creator.profile_picture.name.endswith(".png")
    assert db_creator.social_media_links == {"instagram": "https://instagram.com/aarav"}


def test_profile_picture_exceeding_5mb_rejected(creator: Any) -> None:
    # Generate a dummy file that exceeds 5MB
    large_content = b"x" * (MAX_PROFILE_PICTURE_SIZE_BYTES + 1024)
    oversized_file = SimpleUploadedFile("too_large.png", large_content, content_type="image/png")

    payload = {
        "displayName": "Aarav Patel",
        "profilePicture": oversized_file,
    }
    resp = creator.patch("/v1/creators/me/", payload, format="multipart")
    assert resp.status_code == 400
    res_json = resp.json()
    assert res_json["success"] is False
    assert res_json["error"]["code"] == "VALIDATION_ERROR"
    assert "profilePicture" in res_json["error"]["details"]
    error_msg = res_json["error"]["details"]["profilePicture"][0]
    assert "5MB" in error_msg


def test_non_image_file_rejected(creator: Any) -> None:
    txt_file = SimpleUploadedFile("fake.png", b"This is plain text, not an image", content_type="image/png")
    payload = {
        "profilePicture": txt_file,
    }
    resp = creator.patch("/v1/creators/me/", payload, format="multipart")
    assert resp.status_code == 400
    res_json = resp.json()
    assert res_json["success"] is False
    assert "profilePicture" in res_json["error"]["details"]


def test_get_profile_returns_uploaded_picture_and_social_links(creator: Any) -> None:
    image_file = _generate_test_image("my_pic.png")
    creator.patch(
        "/v1/creators/me/",
        {
            "displayName": "Rohan Verma",
            "profilePicture": image_file,
            "socialMediaUrls": '{"youtube": "https://youtube.com/@rohan"}',
        },
        format="multipart",
    )

    resp = creator.get("/v1/creators/me/")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["displayName"] == "Rohan Verma"
    assert "my_pic" in data["profilePicture"]
    assert data["profilePicture"].endswith(".png")
    assert data["socialMediaUrls"] == {"youtube": "https://youtube.com/@rohan"}


def test_upload_avatar_pipeline_success(creator: Any) -> None:
    image_file = _generate_test_image("avatar_test.png")
    resp = creator.post(
        "/v1/uploads/",
        {"file": image_file, "kind": "avatar"},
        format="multipart",
    )
    assert resp.status_code == 201, resp.json()
    res_json = resp.json()
    assert res_json["success"] is True
    data = res_json["data"]
    assert data["url"] is not None
    assert data["avatarUrl"] == data["url"]
    assert data["kind"] == "avatar"
    assert data["filename"] == "avatar_test.png"
    assert data["contentType"] == "image/png"

    # Profile persistence: verify bound to creator model instance
    creator_instance = creator.user.creator
    creator_instance.refresh_from_db()
    assert creator_instance.avatar_url == data["url"]
    assert bool(creator_instance.profile_picture) is True

    # Creator self profile view returns updated avatarUrl
    me_resp = creator.get("/v1/creators/me/")
    assert me_resp.status_code == 200
    me_data = me_resp.json()["data"]
    assert me_data["avatarUrl"] == data["url"]
    assert me_data["profilePicture"] == data["url"]


def test_upload_avatar_dedicated_endpoint(creator: Any) -> None:
    image_file = _generate_test_image("avatar_direct.png")
    resp = creator.post(
        "/v1/creators/me/avatar/",
        {"avatar": image_file},
        format="multipart",
    )
    assert resp.status_code == 201, resp.json()
    data = resp.json()["data"]
    assert data["url"] is not None
    assert data["avatarUrl"] == data["url"]
    assert data["kind"] == "avatar"

    creator_instance = creator.user.creator
    creator_instance.refresh_from_db()
    assert creator_instance.avatar_url == data["url"]


def test_upload_oversized_file_rejected(creator: Any) -> None:
    oversized = SimpleUploadedFile(
        "large_avatar.png",
        b"x" * (MAX_PROFILE_PICTURE_SIZE_BYTES + 1024),
        content_type="image/png",
    )
    resp = creator.post(
        "/v1/uploads/",
        {"file": oversized, "kind": "avatar"},
        format="multipart",
    )
    assert resp.status_code == 400
    res_json = resp.json()
    assert res_json["success"] is False
    assert res_json["error"]["code"] == "VALIDATION_ERROR"
    assert "file" in res_json["error"]["details"]
    assert "5MB" in res_json["error"]["details"]["file"][0]


def test_upload_invalid_format_rejected(creator: Any) -> None:
    fake_img = SimpleUploadedFile("fake.png", b"Not a real image file content", content_type="image/png")
    resp = creator.post(
        "/v1/uploads/",
        {"file": fake_img, "kind": "avatar"},
        format="multipart",
    )
    assert resp.status_code == 400
    res_json = resp.json()
    assert res_json["success"] is False
    assert res_json["error"]["code"] == "VALIDATION_ERROR"
    assert "file" in res_json["error"]["details"]


def test_upload_unauthenticated_rejected() -> None:
    client = APIClient()
    image_file = _generate_test_image("anon.png")
    resp = client.post(
        "/v1/uploads/",
        {"file": image_file, "kind": "avatar"},
        format="multipart",
    )
    assert resp.status_code == 401
