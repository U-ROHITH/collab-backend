from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.campaigns.models import Campaign, CampaignStatus, ModerationStatus
from apps.identity.models import UserStatus
from apps.notifications.models import Notification
from apps.pitches.models import Pitch

pytestmark = pytest.mark.django_db


def _make_campaign(
    brand: APIClient, campaign_payload: dict[str, Any], status: str = "open", moderation: str = "live"
) -> dict[str, Any]:
    c = brand.post("/v1/campaigns/", campaign_payload, format="json").json()["data"]
    if status != "open" or moderation != "live":
        camp = Campaign.objects.get(pk=c["id"])
        camp.status = status
        camp.moderation_status = moderation
        camp.save()
    return c


# ---------------------------------------------------------------------------
# 1. Brand Profile Endpoint (GET /v1/brands/{id}/)
# ---------------------------------------------------------------------------

def test_brand_profile_fields_and_suspended_brand_404(
    brand: Any, creator: Any, make_brand: Any, campaign_payload: dict[str, Any]
) -> None:
    bid = brand.user.brand.id
    # Create an open campaign for this brand
    _make_campaign(brand, campaign_payload)

    # Creator views brand profile
    resp = creator.get(f"/v1/brands/{bid}/")
    assert resp.status_code == 200, resp.json()
    data = resp.json()["data"]

    # Verify expected fields
    assert data["id"] == str(bid)
    assert data["name"] == "Nimbu Foods"
    assert "openBriefs" in data
    assert isinstance(data["openBriefs"], list)
    assert len(data["openBriefs"]) == 1
    assert data["openBriefs"][0]["title"] == "Festive snack hamper"
    assert "canPitch" in data
    assert data["canPitch"] is True
    assert data["pitchBlockedUntil"] is None
    assert data["myPitch"] is None
    assert "location" in data
    assert "about" in data
    private_keys = {
        "dealCount", "dealsCount", "spend", "rating", "ratingSum",
        "phone", "email", "gstin", "billingAddress",
    }
    assert not private_keys & set(data)

    # Suspended brand returns 404
    susp_brand = make_brand("Suspended Brand Co")
    susp_brand.user.status = UserStatus.SUSPENDED
    susp_brand.user.save()
    assert creator.get(f"/v1/brands/{susp_brand.user.brand.id}/").status_code == 404


# ---------------------------------------------------------------------------
# 2. General Pitch Creation & Duplicate 409 ALREADY_PITCHED
# ---------------------------------------------------------------------------

def test_pitch_create_general_and_duplicate_conflict(brand: Any, creator: Any) -> None:
    bid = brand.user.brand.id
    payload = {
        "message": "Hi, I would love to collaborate with your brand on our upcoming channel video!",
        "links": ["https://youtube.com/@creator", "https://instagram.com/creator"],
    }
    r = creator.post(f"/v1/brands/{bid}/pitches/", payload, format="json")
    assert r.status_code == 201, r.json()
    data = r.json()["data"]
    assert data["brandId"] == str(bid)
    assert data["creatorId"] == str(creator.user.creator.id)
    assert data["campaignId"] is None
    assert data["status"] == "sent"
    assert data["editCount"] == 0
    assert len(data["links"]) == 2

    # Brand profile now reflects active pitch
    prof = creator.get(f"/v1/brands/{bid}/").json()["data"]
    assert prof["canPitch"] is False
    assert prof["myPitch"]["id"] == data["id"]

    # Second active pitch to same brand fails with 409 ALREADY_PITCHED
    r2 = creator.post(f"/v1/brands/{bid}/pitches/", payload, format="json")
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "ALREADY_PITCHED"


# ---------------------------------------------------------------------------
# 3. Campaign Validation: foreign, closed, unapproved -> 400 BRIEF_NOT_THIS_BRAND
# ---------------------------------------------------------------------------

def test_pitch_create_campaign_validation(
    brand: Any, creator: Any, make_brand: Any, campaign_payload: dict[str, Any]
) -> None:
    bid = brand.user.brand.id
    valid_c = _make_campaign(brand, campaign_payload)

    other_brand = make_brand("Other Co")
    other_c = _make_campaign(other_brand, campaign_payload)

    # 1. Foreign campaign (belongs to another brand)
    r1 = creator.post(
        f"/v1/brands/{bid}/pitches/",
        {"message": "Hello I want to pitch you for your brief here!", "campaignId": other_c["id"]},
        format="json",
    )
    assert r1.status_code == 400
    assert r1.json()["error"]["code"] == "BRIEF_NOT_THIS_BRAND"

    # 2. Closed campaign
    closed_c = _make_campaign(brand, campaign_payload, status=CampaignStatus.CLOSED)
    r2 = creator.post(
        f"/v1/brands/{bid}/pitches/",
        {"message": "Hello I want to pitch you for your brief here!", "campaignId": closed_c["id"]},
        format="json",
    )
    assert r2.status_code == 400
    assert r2.json()["error"]["code"] == "BRIEF_NOT_THIS_BRAND"

    # 3. Unapproved campaign
    unapproved_c = _make_campaign(brand, campaign_payload, moderation=ModerationStatus.REJECTED)
    r3 = creator.post(
        f"/v1/brands/{bid}/pitches/",
        {"message": "Hello I want to pitch you for your brief here!", "campaignId": unapproved_c["id"]},
        format="json",
    )
    assert r3.status_code == 400
    assert r3.json()["error"]["code"] == "BRIEF_NOT_THIS_BRAND"

    # 4. Valid campaign succeeds
    r_valid = creator.post(
        f"/v1/brands/{bid}/pitches/",
        {"message": "Hello I want to pitch you for your brief here!", "campaignId": valid_c["id"]},
        format="json",
    )
    assert r_valid.status_code == 201
    assert r_valid.json()["data"]["campaignId"] == valid_c["id"]


# ---------------------------------------------------------------------------
# 4. Input Validation: trimmed message length & links -> 400 VALIDATION_ERROR
# ---------------------------------------------------------------------------

def test_pitch_input_validation(brand: Any, creator: Any) -> None:
    bid = brand.user.brand.id

    # Too short after strip
    r1 = creator.post(
        f"/v1/brands/{bid}/pitches/",
        {"message": "   short   "},
        format="json",
    )
    assert r1.status_code == 400
    assert r1.json()["error"]["code"] == "VALIDATION_ERROR"

    # Too long (> 1500 chars)
    r2 = creator.post(
        f"/v1/brands/{bid}/pitches/",
        {"message": "x" * 1501},
        format="json",
    )
    assert r2.status_code == 400
    assert r2.json()["error"]["code"] == "VALIDATION_ERROR"

    # More than 5 links
    r3 = creator.post(
        f"/v1/brands/{bid}/pitches/",
        {"message": "Valid message with at least 20 chars.", "links": [f"https://example.com/{i}" for i in range(6)]},
        format="json",
    )
    assert r3.status_code == 400
    assert r3.json()["error"]["code"] == "VALIDATION_ERROR"

    # Invalid link protocol (not http or https)
    r4 = creator.post(
        f"/v1/brands/{bid}/pitches/",
        {"message": "Valid message with at least 20 chars.", "links": ["javascript:alert(1)"]},
        format="json",
    )
    assert r4.status_code == 400
    assert r4.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# 5. Pitch Editing & Edit Limits
# ---------------------------------------------------------------------------

def test_pitch_edit_lifecycle(brand: Any, creator: Any, make_creator: Any) -> None:
    bid = brand.user.brand.id
    p = creator.post(
        f"/v1/brands/{bid}/pitches/",
        {"message": "Initial proposal message that is long enough."},
        format="json",
    ).json()["data"]

    # 1. Other creator cannot edit (403 FORBIDDEN)
    other = make_creator("Other Creator")
    r_forbid = other.patch(
        f"/v1/pitches/{p['id']}/",
        {"message": "Hijacking pitch with another message."},
        format="json",
    )
    assert r_forbid.status_code == 403

    # 2. First edit succeeds (200)
    r_edit = creator.patch(
        f"/v1/pitches/{p['id']}/",
        {"message": "Updated proposal message that is also quite detailed."},
        format="json",
    )
    assert r_edit.status_code == 200
    data = r_edit.json()["data"]
    assert data["status"] == "edited"
    assert data["editCount"] == 1
    assert data["editedAt"] is not None
    assert data["message"] == "Updated proposal message that is also quite detailed."

    # 3. Second edit fails (409 EDIT_USED)
    r_edit2 = creator.patch(
        f"/v1/pitches/{p['id']}/",
        {"message": "Attempting a second edit on this pitch."},
        format="json",
    )
    assert r_edit2.status_code == 409
    assert r_edit2.json()["error"]["code"] == "EDIT_USED"


def test_pitch_edit_allowed_after_read(brand: Any, creator: Any) -> None:
    bid = brand.user.brand.id
    p = creator.post(
        f"/v1/brands/{bid}/pitches/",
        {"message": "Pitch message that will be read by brand."},
        format="json",
    ).json()["data"]

    # Brand reads the pitch
    brand.post(f"/v1/pitches/{p['id']}/read/")

    # Creator can still edit after read
    r_edit = creator.patch(
        f"/v1/pitches/{p['id']}/",
        {"message": "Post-read update with more details on scope."},
        format="json",
    )
    assert r_edit.status_code == 200
    assert r_edit.json()["data"]["status"] == "edited"


# ---------------------------------------------------------------------------
# 6. Mark Read & Decline Flow
# ---------------------------------------------------------------------------

def test_pitch_read_and_decline_permissions_and_idempotency(brand: Any, creator: Any, make_brand: Any) -> None:
    bid = brand.user.brand.id
    p = creator.post(
        f"/v1/brands/{bid}/pitches/",
        {"message": "Pitch to be read and then declined by brand."},
        format="json",
    ).json()["data"]

    # Creator cannot call read or decline (403)
    assert creator.post(f"/v1/pitches/{p['id']}/read/").status_code == 403
    assert creator.post(f"/v1/pitches/{p['id']}/decline/").status_code == 403

    # Other brand cannot call read or decline (403)
    rival = make_brand("Rival Co")
    assert rival.post(f"/v1/pitches/{p['id']}/read/").status_code == 403
    assert rival.post(f"/v1/pitches/{p['id']}/decline/").status_code == 403

    # Receiving brand marks read
    r_read = brand.post(f"/v1/pitches/{p['id']}/read/")
    assert r_read.status_code == 200
    read_at_first = r_read.json()["data"]["readAt"]
    assert read_at_first is not None

    # Receiving brand declines pitch
    r_dec = brand.post(f"/v1/pitches/{p['id']}/decline/")
    assert r_dec.status_code == 200
    dec_data = r_dec.json()["data"]
    assert dec_data["status"] == "declined"
    declined_at_first = dec_data["declinedAt"]
    assert declined_at_first is not None

    # Idempotent decline does not reset declinedAt timestamp
    r_dec2 = brand.post(f"/v1/pitches/{p['id']}/decline/")
    assert r_dec2.status_code == 200
    assert r_dec2.json()["data"]["declinedAt"] == declined_at_first

    # Editing a declined pitch fails (409 PITCH_DECLINED)
    r_edit = creator.patch(
        f"/v1/pitches/{p['id']}/",
        {"message": "Trying to edit after it was already declined."},
        format="json",
    )
    assert r_edit.status_code == 409
    assert r_edit.json()["error"]["code"] == "PITCH_DECLINED"


# ---------------------------------------------------------------------------
# 7. 15-Day Cooldown & Re-pitching
# ---------------------------------------------------------------------------

def test_pitch_15_day_cooldown_and_retry(brand: Any, creator: Any) -> None:
    bid = brand.user.brand.id
    p = creator.post(
        f"/v1/brands/{bid}/pitches/",
        {"message": "Pitch message that will be declined today."},
        format="json",
    ).json()["data"]
    brand.post(f"/v1/pitches/{p['id']}/decline/")

    # Brand profile shows pitchBlockedUntil timestamp
    prof = creator.get(f"/v1/brands/{bid}/").json()["data"]
    assert prof["canPitch"] is False
    assert prof["pitchBlockedUntil"] is not None

    # Immediate re-pitch fails with 409 PITCH_COOLDOWN + retryAt in details
    r = creator.post(
        f"/v1/brands/{bid}/pitches/",
        {"message": "Trying to re-pitch brand immediately after decline."},
        format="json",
    )
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "PITCH_COOLDOWN"
    assert "retryAt" in err["details"]

    # Simulate passing of 16 days
    pitch_record = Pitch.objects.get(pk=p["id"])
    pitch_record.declined_at = timezone.now() - timedelta(days=16)
    pitch_record.save(update_fields=["declined_at"])

    # Brand profile now shows canPitch = True
    prof_after = creator.get(f"/v1/brands/{bid}/").json()["data"]
    assert prof_after["canPitch"] is True
    assert prof_after["pitchBlockedUntil"] is None

    # Re-pitch now succeeds
    r_new = creator.post(
        f"/v1/brands/{bid}/pitches/",
        {"message": "New fresh proposal now that 15-day cooldown has passed."},
        format="json",
    )
    assert r_new.status_code == 201
    assert r_new.json()["data"]["status"] == "sent"


# ---------------------------------------------------------------------------
# 8. Listing Endpoints & Filters
# ---------------------------------------------------------------------------

def test_pitch_list_filters_and_suspended_exclusion(
    brand: Any, creator: Any, make_creator: Any, campaign_payload: dict[str, Any]
) -> None:
    bid = brand.user.brand.id
    c1 = _make_campaign(brand, campaign_payload)

    # Creator 1 pitches on c1
    creator.post(
        f"/v1/brands/{bid}/pitches/",
        {"message": "Pitch 1 on campaign 1 from creator 1.", "campaignId": c1["id"]},
        format="json",
    )

    # Creator 2 pitches general (no campaign)
    creator2 = make_creator("Creator Two")
    p2 = creator2.post(
        f"/v1/brands/{bid}/pitches/",
        {"message": "Pitch 2 general pitch from creator 2."},
        format="json",
    ).json()["data"]

    # Creator list (creators/me/pitches/)
    c_list = creator.get("/v1/creators/me/pitches/")
    assert c_list.status_code == 200
    assert len(c_list.json()["data"]) == 1

    # Brand list (brands/me/pitches/)
    b_list = brand.get("/v1/brands/me/pitches/")
    assert b_list.status_code == 200
    assert len(b_list.json()["data"]) == 2

    # Filter by campaignId
    filt_camp = brand.get(f"/v1/brands/me/pitches/?campaignId={c1['id']}")
    assert len(filt_camp.json()["data"]) == 1

    # Brand marks p2 as read
    brand.post(f"/v1/pitches/{p2['id']}/read/")

    # Filter by unread=1
    filt_unread = brand.get("/v1/brands/me/pitches/?unread=1")
    assert len(filt_unread.json()["data"]) == 1

    # Filter by status
    filt_status = brand.get("/v1/brands/me/pitches/?status=sent")
    assert len(filt_status.json()["data"]) == 2

    # Suspend creator2 -> excluded from brand list
    creator2.user.status = UserStatus.SUSPENDED
    creator2.user.save()

    b_list_after_susp = brand.get("/v1/brands/me/pitches/")
    assert len(b_list_after_susp.json()["data"]) == 1


# ---------------------------------------------------------------------------
# 9. Notifications Verification
# ---------------------------------------------------------------------------

def test_pitch_notifications_flow(brand: Any, creator: Any, campaign_payload: dict[str, Any]) -> None:
    bid = brand.user.brand.id
    c = _make_campaign(brand, campaign_payload)

    # 1. pitch_received notification to brand
    p = creator.post(
        f"/v1/brands/{bid}/pitches/",
        {"message": "Hello Brand, here is my initial pitch idea.", "campaignId": c["id"]},
        format="json",
    ).json()["data"]

    notif_recv = Notification.objects.filter(user=brand.user, type="pitch_received").first()
    assert notif_recv is not None
    assert f"{creator.user.creator.display_name} pitched you about {c['title']}" in notif_recv.title
    assert notif_recv.data["pitchId"] == p["id"]
    assert notif_recv.data["creatorId"] == str(creator.user.creator.id)
    assert notif_recv.data["campaignId"] == c["id"]

    # 2. pitch_edited notification to brand
    creator.patch(f"/v1/pitches/{p['id']}/", {"message": "Updated pitch message with new scope."}, format="json")
    notif_edit = Notification.objects.filter(user=brand.user, type="pitch_edited").first()
    assert notif_edit is not None
    assert f"{creator.user.creator.display_name} edited their pitch" in notif_edit.title
    assert notif_edit.data["pitchId"] == p["id"]

    # 3. pitch_declined notification to creator
    brand.post(f"/v1/pitches/{p['id']}/decline/")
    notif_dec = Notification.objects.filter(user=creator.user, type="pitch_declined").first()
    assert notif_dec is not None
    assert "Nimbu Foods passed on your pitch" in notif_dec.title
    assert notif_dec.data["pitchId"] == p["id"]
    assert notif_dec.data["brandId"] == str(bid)
    assert "retryAt" in notif_dec.data


# ---------------------------------------------------------------------------
# 10. No DELETE endpoint
# ---------------------------------------------------------------------------

def test_pitch_no_delete_endpoint(creator: Any, brand: Any) -> None:
    bid = brand.user.brand.id
    p = creator.post(
        f"/v1/brands/{bid}/pitches/",
        {"message": "This pitch will test that deletion is disallowed."},
        format="json",
    ).json()["data"]

    r = creator.delete(f"/v1/pitches/{p['id']}/")
    assert r.status_code in (404, 405)
