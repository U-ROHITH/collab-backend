import io
from typing import Any

import pytest
from PIL import Image

from apps.orders.services import system_event
from apps.uploads.models import Upload

pytestmark = pytest.mark.django_db


def png_with_gps_exif() -> bytes:
    img = Image.new("RGB", (64, 48), (10, 120, 200))
    exif = Image.Exif()
    exif[0x8825] = {1: "N", 2: (12.0, 58.0, 0.0)}  # GPS IFD
    exif[0x010F] = "SpyCam"  # Make
    buf = io.BytesIO()
    img.save(buf, "PNG", exif=exif)
    return buf.getvalue()


def start(client: Any, kind: str, content_type: str, size: int) -> Any:
    return client.post("/v1/uploads/", {"kind": kind, "contentType": content_type, "size": size}, format="json")


def staging_key(upload_id: str) -> str:
    return Upload.objects.get(pk=upload_id).staging_key


def test_avatar_upload_is_sanitised_and_attached(creator: Any, fake_storage: Any) -> None:
    data = png_with_gps_exif()
    r = start(creator, "avatar", "image/png", len(data))
    assert r.status_code == 201, r.json()
    body = r.json()["data"]
    assert body["method"] == "PUT" and body["uploadUrl"].startswith("https://")
    key = staging_key(body["upload"]["id"])
    assert key.startswith("staging/avatar/") and key.endswith(".png")  # server-chosen path, no client filename

    fake_storage.put("private", key, data, "image/png")
    done = creator.post(f"/v1/uploads/{body['upload']['id']}/complete/")
    assert done.status_code == 200, done.json()
    out = done.json()["data"]
    assert out["status"] == "ready" and out["contentType"] == "image/webp" and out["url"]

    stored, ctype = next(v for (b, _), v in fake_storage.objects.items() if b == "public-media")
    assert ctype == "image/webp"
    with Image.open(io.BytesIO(stored)) as img:
        assert img.format == "WEBP" and not img.getexif()  # EXIF/GPS stripped
    assert ("private", key) not in fake_storage.objects  # staging copy removed
    assert creator.get("/v1/creators/me/").json()["data"]["avatarUrl"] == out["url"]


def test_fake_image_is_rejected(creator: Any, fake_storage: Any) -> None:
    payload = b"<html><script>alert(1)</script></html>"
    up = start(creator, "avatar", "image/png", len(payload)).json()["data"]["upload"]
    key = staging_key(up["id"])
    fake_storage.put("private", key, payload, "image/png")
    r = creator.post(f"/v1/uploads/{up['id']}/complete/")
    assert r.status_code == 422 and r.json()["error"]["code"] == "UPLOAD_REJECTED"
    assert ("private", key) not in fake_storage.objects


@pytest.mark.parametrize(
    ("kind", "ctype", "size", "code"),
    [
        ("avatar", "image/gif", 100, "UPLOAD_TYPE_NOT_ALLOWED"),
        ("avatar", "image/png", 50 * 1024 * 1024, "UPLOAD_TOO_LARGE"),
        ("brand_logo", "image/png", 100, "UPLOAD_KIND_NOT_ALLOWED"),
        ("deliverable", "text/html", 100, "UPLOAD_TYPE_NOT_ALLOWED"),
    ],
)
def test_presign_rules(creator: Any, fake_storage: Any, kind: str, ctype: str, size: int, code: str) -> None:
    r = start(creator, kind, ctype, size)
    assert r.status_code == 422 and r.json()["error"]["code"] == code


def test_brand_cannot_upload_creator_kinds(brand: Any, fake_storage: Any) -> None:
    assert start(brand, "avatar", "image/png", 10).json()["error"]["code"] == "UPLOAD_KIND_NOT_ALLOWED"
    assert start(brand, "brand_logo", "image/png", 10).status_code == 201


def test_complete_requires_file_and_ownership(creator: Any, make_creator: Any, fake_storage: Any) -> None:
    up = start(creator, "avatar", "image/png", 10).json()["data"]["upload"]
    r = creator.post(f"/v1/uploads/{up['id']}/complete/")
    assert r.status_code == 422 and r.json()["error"]["code"] == "UPLOAD_NOT_FOUND"
    other = make_creator("Someone Else")
    assert other.post(f"/v1/uploads/{up['id']}/complete/").status_code == 404


def test_gallery_add_show_delete(creator: Any, brand: Any, fake_storage: Any) -> None:
    data = png_with_gps_exif()
    up = start(creator, "gallery", "image/png", len(data)).json()["data"]["upload"]
    fake_storage.put("private", staging_key(up["id"]), data, "image/png")
    assert creator.post(f"/v1/uploads/{up['id']}/complete/").status_code == 200
    gallery = brand.get(f"/v1/creators/{creator.user.creator.id}/").json()["data"]["gallery"]
    assert len(gallery) == 1
    assert brand.delete(f"/v1/creators/me/media/{gallery[0]['id']}/").status_code == 403
    assert creator.delete(f"/v1/creators/me/media/{gallery[0]['id']}/").status_code == 204
    assert brand.get(f"/v1/creators/{creator.user.creator.id}/").json()["data"]["gallery"] == []


@pytest.fixture
def funded_order(brand: Any, creator: Any, campaign_payload: dict[str, Any]) -> dict[str, Any]:
    c = brand.post("/v1/campaigns/", campaign_payload, format="json").json()["data"]
    b = creator.post(
        f"/v1/campaigns/{c['id']}/bids/", {"price": 500_000, "message": "hi", "deliveryDays": 3}, format="json"
    ).json()["data"]
    o = brand.post(f"/v1/campaigns/{c['id']}/bids/{b['id']}/select/").json()["data"]
    system_event(order_id=o["id"], event="fund")
    return o


def test_private_deliverable_file_access(
    brand: Any, creator: Any, make_brand: Any, funded_order: dict[str, Any], fake_storage: Any
) -> None:
    pdf = b"%PDF-1.7\n%fake but well-formed header\n"
    up = start(creator, "deliverable", "application/pdf", len(pdf)).json()["data"]["upload"]
    fake_storage.put("private", staging_key(up["id"]), pdf, "application/pdf")
    done = creator.post(f"/v1/uploads/{up['id']}/complete/").json()["data"]
    assert done["status"] == "ready" and done["url"] is None  # private: no public URL

    both = creator.post(
        f"/v1/orders/{funded_order['id']}/deliverables/",
        {"contentUrl": "https://x.com/a", "fileUploadId": up["id"]},
        format="json",
    )
    assert both.status_code == 400
    d = creator.post(f"/v1/orders/{funded_order['id']}/deliverables/", {"fileUploadId": up["id"]}, format="json")
    assert d.status_code == 201 and d.json()["data"]["file"]["uploadId"] == up["id"]

    link = brand.get(f"/v1/uploads/{up['id']}/download-url/")
    assert link.status_code == 200 and link.json()["data"]["expiresIn"] == 300
    assert make_brand("Snoop Co").get(f"/v1/uploads/{up['id']}/download-url/").status_code == 404


def test_pdf_with_wrong_magic_rejected(creator: Any, fake_storage: Any) -> None:
    up = start(creator, "deliverable", "application/pdf", 20).json()["data"]["upload"]
    fake_storage.put("private", staging_key(up["id"]), b"MZ\x90\x00 windows exe!!", "application/pdf")
    assert creator.post(f"/v1/uploads/{up['id']}/complete/").json()["error"]["code"] == "UPLOAD_REJECTED"
