from datetime import date

import pytest
from rest_framework.test import APIClient

from apps.creators.models import Creator

pytestmark = pytest.mark.django_db


def test_creator_profile_fields_persistence_and_privacy(
    creator: APIClient,
    brand: APIClient,
    anon: APIClient,
) -> None:
    # 1. Update creator profile with socialAccounts, age, gender, genderDescription
    payload = {
        "displayName": "Ananya Social",
        "primaryPlatform": "instagram",
        "socialAccounts": [
            {"platform": "instagram", "handle": "ananya_official"},
            {"platform": "youtube", "handle": "ananya_vlogs"},
            {"platform": "tiktok", "handle": "ananya_tok"},
        ],
        "age": 25,
        "gender": "female",
        "genderDescription": None,
    }
    resp = creator.patch("/v1/creators/me/", payload, format="json")
    assert resp.status_code == 200, resp.json()
    data = resp.json()["data"]

    # Verify fields on GET /v1/creators/me/
    assert data["displayName"] == "Ananya Social"
    assert data["age"] == 25
    assert data["gender"] == "female"
    assert data["genderDescription"] is None
    assert len(data["socialAccounts"]) == 3
    assert data["socialAccounts"][0]["platform"] == "instagram"
    assert data["socialAccounts"][0]["handle"] == "ananya_official"

    # Verify directly in DB
    db_creator = Creator.objects.get(pk=data["id"])
    assert db_creator.age == 25
    assert db_creator.gender == "female"
    assert len(db_creator.social_accounts) == 3

    # 2. Strict Privacy check: Public profile GET /v1/creators/{id}/ must EXCLUDE age, gender, genderDescription, dob
    public_resp = brand.get(f"/v1/creators/{db_creator.id}/")
    assert public_resp.status_code == 200
    public_data = public_resp.json()["data"]
    assert "dob" not in public_data, "dob must be strictly private"
    assert "age" not in public_data, "age must be strictly private"
    assert "gender" not in public_data, "gender must be strictly private"
    assert "genderDescription" not in public_data, "genderDescription must be strictly private"
    assert "socialAccounts" in public_data

    # 3. Anonymous public discovery: GET /v1/creators/{id}/ without auth
    anon_resp = anon.get(f"/v1/creators/{db_creator.id}/")
    assert anon_resp.status_code == 200
    anon_data = anon_resp.json()["data"]
    assert "dob" not in anon_data, "dob must be strictly private"
    assert "age" not in anon_data
    assert "gender" not in anon_data
    assert "genderDescription" not in anon_data
    assert anon_data["displayName"] == "Ananya Social"


def test_primary_platform_must_match_social_accounts(creator: APIClient) -> None:
    # Attempt to set primaryPlatform to "youtube" while socialAccounts only contains "instagram"
    payload = {
        "primaryPlatform": "youtube",
        "socialAccounts": [
            {"platform": "instagram", "handle": "insta_only"},
        ],
    }
    resp = creator.patch("/v1/creators/me/", payload, format="json")
    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert "primaryPlatform" in error["details"]


def test_unsupported_platform_rejected(creator: APIClient) -> None:
    payload = {
        "socialAccounts": [
            {"platform": "myspace_invalid_platform_xyz", "handle": "user"},
        ],
    }
    resp = creator.patch("/v1/creators/me/", payload, format="json")
    assert resp.status_code == 400
    assert "socialAccounts" in resp.json()["error"]["details"]


def test_anonymous_discovery_endpoints(
    anon: APIClient, creator: APIClient, campaign_payload: dict, brand: APIClient
) -> None:
    # 1. Creator search list is accessible anonymously
    resp = anon.get("/v1/creators/")
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    # 2. Creator detail is accessible anonymously
    creator_id = creator.user.creator.id
    resp = anon.get(f"/v1/creators/{creator_id}/")
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == str(creator_id)

    # 3. Creator reviews list is accessible anonymously
    resp = anon.get(f"/v1/creators/{creator_id}/reviews/")
    assert resp.status_code == 200
    assert isinstance(resp.json()["data"], list)

    # 4. Open campaigns list is accessible anonymously
    brand.post("/v1/campaigns/", campaign_payload, format="json")
    resp = anon.get("/v1/campaigns/")
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert len(resp.json()["data"]) >= 1


def test_creator_dob_and_response_time_hours(creator: APIClient, brand: APIClient) -> None:
    # Update creator profile with DOB and responseTimeHours
    payload = {
        "dob": "1998-05-20",
        "responseTimeHours": 12,
    }
    resp = creator.patch("/v1/creators/me/", payload, format="json")
    assert resp.status_code == 200, resp.json()
    data = resp.json()["data"]

    # Verify self output
    assert data["dob"] == "1998-05-20"
    assert data["responseTimeHours"] == 12
    today = date.today()
    expected_age = today.year - 1998 - ((today.month, today.day) < (5, 20))
    assert data["age"] == expected_age

    # Verify directly in DB
    creator_id = data["id"]
    db_creator = Creator.objects.get(pk=creator_id)
    assert db_creator.dob == date(1998, 5, 20)
    assert db_creator.response_time_hours == 12
    assert db_creator.age == expected_age

    # Verify public profile: responseTimeHours is visible, dob and age are strictly private
    public_resp = brand.get(f"/v1/creators/{creator_id}/")
    assert public_resp.status_code == 200
    public_data = public_resp.json()["data"]
    assert public_data["responseTimeHours"] == 12
    assert "dob" not in public_data, "dob must be excluded from public profiles"
    assert "age" not in public_data, "age must be excluded from public profiles"


def test_creator_future_dob_rejected(creator: APIClient) -> None:
    payload = {
        "dob": "2099-01-01",
    }
    resp = creator.patch("/v1/creators/me/", payload, format="json")
    assert resp.status_code == 400
    res_json = resp.json()
    assert res_json["success"] is False
    assert res_json["error"]["code"] == "VALIDATION_ERROR"
    assert "dob" in res_json["error"]["details"]


def test_creator_response_time_hours_validation(creator: APIClient) -> None:
    # Less than minimum 1 hour
    resp_zero = creator.patch("/v1/creators/me/", {"responseTimeHours": 0}, format="json")
    assert resp_zero.status_code == 400
    assert "responseTimeHours" in resp_zero.json()["error"]["details"]

    # More than maximum 720 hours (30 days)
    resp_excess = creator.patch("/v1/creators/me/", {"responseTimeHours": 800}, format="json")
    assert resp_excess.status_code == 400
    assert "responseTimeHours" in resp_excess.json()["error"]["details"]
