import json
from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.core.parties import creator_public
from apps.creators.models import ApprovalStatus, Creator
from apps.identity.models import User
from apps.identity.services import complete_signup, issue_tokens

pytestmark = pytest.mark.django_db

ME = "/v1/creators/me/"


def _client_for(user: User) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_tokens(user).access}")
    client.user = user  # type: ignore[attr-defined]
    return client


def _create_creator(
    phone: str = "+919876500001", name: str = "Ananya Rao", approved: bool = True
) -> tuple[APIClient, Creator]:
    user = User.objects.create_user(phone=phone)
    complete_signup(user=user, role="creator", full_name=name, display_name=name)
    creator = Creator.objects.get(user=user)
    if approved:
        creator.approval_status = ApprovalStatus.APPROVED
        creator.save(update_fields=["approval_status"])
    return _client_for(user), creator


def test_verification_suite(capsys: Any) -> None:
    client, creator = _create_creator()
    results: dict[str, Any] = {}

    # 1. Full Nested PATCH
    payload_1 = {
        "primaryPlatform": "instagram",
        "instagram": {
            "handle": "@ananya.eats",
            "followersCount": 30000,
            "avgViews": 8000,
            "engagementRate": 4.2,
        },
        "youtube": {
            "handle": "https://www.youtube.com/@AnanyaCooks",
            "followersCount": 20000,
            "avgViews": 15000,
            "engagementRate": 2.8,
        },
    }
    r1 = client.patch(ME, payload_1, format="json")
    assert r1.status_code == 200
    res_1 = r1.json()
    assert res_1["data"]["platforms"]["instagram"] == {
        "handle": "ananya.eats",
        "url": "https://www.instagram.com/ananya.eats/",
        "followersCount": 30000,
        "avgViews": 8000,
        "engagementRate": 4.2,
        "verified": False,
    }
    assert res_1["data"]["platforms"]["youtube"] == {
        "handle": "AnanyaCooks",
        "url": "https://www.youtube.com/@AnanyaCooks",
        "followersCount": 20000,
        "avgViews": 15000,
        "engagementRate": 2.8,
        "verified": False,
    }
    assert res_1["data"]["instagram"] == "ananya.eats"
    assert res_1["data"]["youtube"] == "AnanyaCooks"
    assert res_1["data"]["followerCount"] == 30000

    results["1_full_patch"] = {"request": payload_1, "response": res_1["data"]}

    # 2. Partial Update (merging stats)
    payload_2 = {
        "instagram": {
            "avgViews": 9500,
        }
    }
    r2 = client.patch(ME, payload_2, format="json")
    assert r2.status_code == 200
    res_2 = r2.json()
    # Check that followersCount, engagementRate, handle were merged and preserved
    assert res_2["data"]["platforms"]["instagram"]["avgViews"] == 9500
    assert res_2["data"]["platforms"]["instagram"]["followersCount"] == 30000
    assert res_2["data"]["platforms"]["instagram"]["engagementRate"] == 4.2
    assert res_2["data"]["platforms"]["instagram"]["handle"] == "ananya.eats"
    assert res_2["data"]["platforms"]["youtube"]["followersCount"] == 20000

    results["2_partial_merge"] = {"request": payload_2, "response": res_2["data"]["platforms"]}

    # 3. Sending null removes platform cleanly
    payload_3 = {
        "youtube": None
    }
    r3 = client.patch(ME, payload_3, format="json")
    assert r3.status_code == 200
    res_3 = r3.json()
    assert "youtube" not in res_3["data"]["platforms"]
    assert res_3["data"]["youtube"] == ""
    assert "instagram" in res_3["data"]["platforms"]

    results["3_null_clears_platform"] = {"request": payload_3, "response": res_3["data"]["platforms"]}

    # 4. Plain string handle update
    payload_4 = {
        "youtube": "AnanyaCooksOfficial"
    }
    r4 = client.patch(ME, payload_4, format="json")
    assert r4.status_code == 200
    res_4 = r4.json()
    assert res_4["data"]["youtube"] == "AnanyaCooksOfficial"
    assert res_4["data"]["platforms"]["youtube"] == {
        "handle": "AnanyaCooksOfficial",
        "url": "https://www.youtube.com/@AnanyaCooksOfficial",
        "followersCount": None,
        "avgViews": None,
        "engagementRate": None,
        "verified": False,
    }

    results["4_string_handle"] = {"request": payload_4, "response": res_4["data"]["platforms"]["youtube"]}

    # 5. Handle & URL Normalization
    payload_5 = {
        "instagram": {
            "handle": "https://www.instagram.com/democreator/?utm_source=ig#profile",
            "followersCount": 25000,
        }
    }
    r5 = client.patch(ME, payload_5, format="json")
    assert r5.status_code == 200
    res_5 = r5.json()
    assert res_5["data"]["instagram"] == "democreator"
    assert res_5["data"]["platforms"]["instagram"]["handle"] == "democreator"
    assert res_5["data"]["platforms"]["instagram"]["url"] == "https://www.instagram.com/democreator/"

    results["5_normalization"] = {"request": payload_5, "response": res_5["data"]["platforms"]["instagram"]}

    # 6. Strict validation: unknown nested keys
    payload_6 = {
        "instagram": {
            "handle": "democreator",
            "followers": 12000,
            "er": 4.2,
        }
    }
    r6 = client.patch(ME, payload_6, format="json")
    assert r6.status_code == 400
    res_6 = r6.json()
    assert res_6["error"]["code"] == "VALIDATION_ERROR"
    assert "followers" in res_6["error"]["details"]["instagram"]
    assert "er" in res_6["error"]["details"]["instagram"]

    results["6_strict_validation"] = {"request": payload_6, "response": res_6}

    # 7. Derived followerCount & minFollowers filtering & compact cards
    # Creator 1 (Ananya) has primaryPlatform="instagram" with 25000 followers.
    # Set explicit top-level followerCount: 99999 vs primary followersCount: 25000
    payload_7 = {
        "primaryPlatform": "instagram",
        "followerCount": 99999,
        "instagram": {
            "followersCount": 25000,
        }
    }
    r7 = client.patch(ME, payload_7, format="json")
    assert r7.status_code == 200
    res_7 = r7.json()
    # Primary platform followersCount (25000) takes precedence over explicit (99999)
    assert res_7["data"]["followerCount"] == 25000
    creator.refresh_from_db()
    assert creator.follower_count == 25000

    # Create Creator 2 with 5000 followers
    client_2, creator_2 = _create_creator(phone="+919876500002", name="Rohan Small")
    client_2.patch(ME, {
        "primaryPlatform": "instagram",
        "instagram": {"handle": "rohan.small", "followersCount": 5000}
    }, format="json")
    creator_2.refresh_from_db()
    assert creator_2.follower_count == 5000

    # Test GET /v1/creators/?minFollowers=10000
    anon = APIClient()
    r_discovery = anon.get("/v1/creators/?minFollowers=10000")
    assert r_discovery.status_code == 200
    discovery_data = r_discovery.json()["data"]
    returned_ids = [c["id"] for c in discovery_data]
    assert str(creator.id) in returned_ids
    assert str(creator_2.id) not in returned_ids

    # Test compact creator card in creator_public (bids)
    compact = creator_public(creator)
    assert compact["followerCount"] == 25000
    assert "avgViews" not in compact
    assert "engagementRate" not in compact
    assert "platforms" not in compact
    assert "packages" not in compact

    # Test full detail GET /v1/creators/<id>/
    r_detail = anon.get(f"/v1/creators/{creator.id}/")
    assert r_detail.status_code == 200
    detail_data = r_detail.json()["data"]
    assert detail_data["followerCount"] == 25000
    assert "platforms" in detail_data
    assert "instagram" in detail_data["platforms"]
    assert detail_data["platforms"]["instagram"]["avgViews"] == 9500
    assert detail_data["platforms"]["instagram"]["engagementRate"] == 4.2
    assert "packages" in detail_data

    results["7_derived_and_discovery"] = {
        "precedence_followerCount": res_7["data"]["followerCount"],
        "discovery_filtered_count": len(discovery_data),
        "compact_keys": list(compact.keys()),
        "detail_has_platforms": "platforms" in detail_data,
    }

    # Print results as JSON for report inspection
    print("===VERIFICATION_RESULTS_JSON_START===")
    print(json.dumps(results, indent=2))
    print("===VERIFICATION_RESULTS_JSON_END===")
