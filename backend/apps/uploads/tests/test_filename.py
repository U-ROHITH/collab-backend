"""Tests for original_filename persistence and fileName exposure on uploads & deliverables."""

from typing import Any

import pytest

from apps.orders.services import system_event
from apps.uploads.models import Upload
from apps.uploads.tests.test_uploads import staging_key, start

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ready_pdf(creator: Any, fake_storage: Any, filename: str = "") -> dict[str, Any]:
    """Upload a valid PDF deliverable and complete it. Returns the UploadOutput dict."""
    pdf = b"%PDF-1.7\n%fake well-formed\n"
    kwargs: dict[str, Any] = {"kind": "deliverable", "contentType": "application/pdf", "size": len(pdf)}
    if filename:
        kwargs["fileName"] = filename
    up_res = creator.post("/v1/uploads/", kwargs, format="json")
    assert up_res.status_code == 201, up_res.json()
    up = up_res.json()["data"]["upload"]
    fake_storage.put("private", staging_key(up["id"]), pdf, "application/pdf")
    done = creator.post(f"/v1/uploads/{up['id']}/complete/").json()["data"]
    assert done["status"] == "ready"
    return done


@pytest.fixture
def funded_order_fixture(brand: Any, creator: Any, campaign_payload: dict[str, Any]) -> dict[str, Any]:
    c = brand.post("/v1/campaigns/", campaign_payload, format="json").json()["data"]
    b = creator.post(
        f"/v1/campaigns/{c['id']}/bids/", {"price": 500_000, "message": "hi", "deliveryDays": 3}, format="json"
    ).json()["data"]
    o = brand.post(f"/v1/campaigns/{c['id']}/bids/{b['id']}/select/").json()["data"]
    system_event(order_id=o["id"], event="fund")
    return o


# ---------------------------------------------------------------------------
# fileName persisted on POST /v1/uploads/
# ---------------------------------------------------------------------------

def test_filename_persisted_via_fileName_alias(creator: Any, fake_storage: Any) -> None:
    """fileName alias in the upload request is sanitised and stored as original_filename."""
    r = creator.post(
        "/v1/uploads/",
        {"kind": "deliverable", "contentType": "application/pdf", "size": 100, "fileName": "my-draft.pdf"},
        format="json",
    )
    assert r.status_code == 201, r.json()
    up_data = r.json()["data"]["upload"]
    assert up_data["fileName"] == "my-draft.pdf"
    # Verify it's also in the DB
    assert Upload.objects.get(pk=up_data["id"]).original_filename == "my-draft.pdf"


def test_filename_persisted_via_filename_alias(creator: Any, fake_storage: Any) -> None:
    """lowercase 'filename' alias is also captured."""
    r = creator.post(
        "/v1/uploads/",
        {"kind": "deliverable", "contentType": "video/mp4", "size": 200, "filename": "clip.mp4"},
        format="json",
    )
    assert r.status_code == 201, r.json()
    up_data = r.json()["data"]["upload"]
    assert up_data["fileName"] == "clip.mp4"


def test_filename_persisted_via_name_alias(creator: Any, fake_storage: Any) -> None:
    """'name' alias is captured when no other alias is present."""
    r = creator.post(
        "/v1/uploads/",
        {"kind": "avatar", "contentType": "image/png", "size": 500, "name": "profile.png"},
        format="json",
    )
    assert r.status_code == 201, r.json()
    assert r.json()["data"]["upload"]["fileName"] == "profile.png"


def test_filename_absent_returns_null(creator: Any, fake_storage: Any) -> None:
    """When no filename alias is sent, fileName is null in the response."""
    r = start(creator, "deliverable", "application/pdf", 100)
    assert r.status_code == 201, r.json()
    assert r.json()["data"]["upload"]["fileName"] is None


def test_filename_path_separators_stripped(creator: Any, fake_storage: Any) -> None:
    """Path separators and control characters are stripped before storing."""
    r = creator.post(
        "/v1/uploads/",
        {
            "kind": "deliverable",
            "contentType": "application/pdf",
            "size": 100,
            # DRF CharField strips null bytes itself; test path traversal + other unsafe chars instead.
            "fileName": "../../../etc/passwd<evil>.pdf",
        },
        format="json",
    )
    assert r.status_code == 201, r.json()
    stored = r.json()["data"]["upload"]["fileName"]
    # All path separators and angle brackets removed
    assert stored is not None
    assert "/" not in stored and "\\" not in stored
    assert "<" not in stored and ">" not in stored
    # Safe portion of the name survives
    assert "etcpasswdevil.pdf" in stored or stored.endswith("evil.pdf")


def test_filename_survives_complete(creator: Any, fake_storage: Any) -> None:
    """original_filename is preserved after /complete/ and reflected in UploadOutput."""
    done = _ready_pdf(creator, fake_storage, filename="final-draft.pdf")
    assert done["fileName"] == "final-draft.pdf"
    # Also confirm DB state
    assert Upload.objects.get(pk=done["id"]).original_filename == "final-draft.pdf"


# ---------------------------------------------------------------------------
# fileName exposed on GET /v1/orders/{id}/deliverables/
# ---------------------------------------------------------------------------

def test_deliverable_file_includes_fileName(
    brand: Any, creator: Any, funded_order_fixture: dict[str, Any], fake_storage: Any
) -> None:
    """POST deliverable with a named upload → GET /deliverables/ returns fileName in file dict."""
    done = _ready_pdf(creator, fake_storage, filename="campaign-video.pdf")

    post_r = creator.post(
        f"/v1/orders/{funded_order_fixture['id']}/deliverables/",
        {"fileUploadId": done["id"]},
        format="json",
    )
    assert post_r.status_code == 201, post_r.json()
    file_field = post_r.json()["data"]["file"]
    assert file_field["fileName"] == "campaign-video.pdf"
    assert "uploadId" in file_field
    assert "contentType" in file_field
    assert "size" in file_field

    # Also verify via the GET list endpoint (brand's perspective)
    list_r = brand.get(f"/v1/orders/{funded_order_fixture['id']}/deliverables/")
    assert list_r.status_code == 200
    deliverables = list_r.json()["data"]
    assert len(deliverables) == 1
    assert deliverables[0]["file"]["fileName"] == "campaign-video.pdf"


def test_deliverable_file_fileName_null_when_no_filename_given(
    brand: Any, creator: Any, funded_order_fixture: dict[str, Any], fake_storage: Any
) -> None:
    """Upload without a filename → fileName is null in the deliverable file dict."""
    done = _ready_pdf(creator, fake_storage)  # no filename

    post_r = creator.post(
        f"/v1/orders/{funded_order_fixture['id']}/deliverables/",
        {"fileUploadId": done["id"]},
        format="json",
    )
    assert post_r.status_code == 201, post_r.json()
    file_field = post_r.json()["data"]["file"]
    assert file_field["fileName"] is None


def test_deliverable_content_url_has_no_file_field(
    creator: Any, funded_order_fixture: dict[str, Any]
) -> None:
    """content_url deliverable → file field is None."""
    post_r = creator.post(
        f"/v1/orders/{funded_order_fixture['id']}/deliverables/",
        {"contentUrl": "https://drive.google.com/file/xyz"},
        format="json",
    )
    assert post_r.status_code == 201, post_r.json()
    data = post_r.json()["data"]
    assert data["file"] is None
    assert data["contentUrl"] == "https://drive.google.com/file/xyz"


# ---------------------------------------------------------------------------
# Brand download-url access is still intact (regression guard)
# ---------------------------------------------------------------------------

def test_brand_can_access_deliverable_download_url(
    brand: Any, creator: Any, make_brand: Any, funded_order_fixture: dict[str, Any], fake_storage: Any
) -> None:
    done = _ready_pdf(creator, fake_storage, filename="proof.pdf")
    creator.post(
        f"/v1/orders/{funded_order_fixture['id']}/deliverables/",
        {"fileUploadId": done["id"]},
        format="json",
    )

    link = brand.get(f"/v1/uploads/{done['id']}/download-url/")
    assert link.status_code == 200
    assert link.json()["data"]["expiresIn"] == 300

    # A brand on a *different* order cannot access it
    other_brand = make_brand("Outsider Co")
    assert other_brand.get(f"/v1/uploads/{done['id']}/download-url/").status_code == 404


# ---------------------------------------------------------------------------
# deliverable_submitted notification is still sent (regression guard)
# ---------------------------------------------------------------------------

def test_deliverable_submitted_notification_sent(
    brand: Any, creator: Any, funded_order_fixture: dict[str, Any], fake_storage: Any
) -> None:
    from apps.notifications.models import Notification

    done = _ready_pdf(creator, fake_storage, filename="notify-test.pdf")
    post_r = creator.post(
        f"/v1/orders/{funded_order_fixture['id']}/deliverables/",
        {"fileUploadId": done["id"]},
        format="json",
    )
    assert post_r.status_code == 201
    # Brand (not creator) should have received the notification
    assert Notification.objects.filter(
        user=brand.user, type="deliverable_submitted"
    ).exists()
