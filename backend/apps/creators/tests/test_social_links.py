import pytest
from rest_framework.test import APIClient

from apps.creators.serializers import extract_social_handle, normalize_social_url

pytestmark = pytest.mark.django_db


def test_extract_social_handle_instagram() -> None:
    # Full URLs
    assert extract_social_handle("instagram", "https://www.instagram.com/srikarnagavelli/") == "srikarnagavelli"
    assert extract_social_handle("instagram", "https://instagram.com/srikarnagavelli") == "srikarnagavelli"
    assert extract_social_handle("instagram", "http://instagram.com/srikarnagavelli/") == "srikarnagavelli"
    # Domain without protocol
    assert extract_social_handle("instagram", "instagram.com/srikarnagavelli") == "srikarnagavelli"
    assert extract_social_handle("instagram", "www.instagram.com/srikarnagavelli/") == "srikarnagavelli"
    # Handles with @ and plain usernames
    assert extract_social_handle("instagram", "@srikarnagavelli") == "srikarnagavelli"
    assert extract_social_handle("instagram", "srikarnagavelli") == "srikarnagavelli"
    # Query parameters & trailing slashes
    query_url = "https://www.instagram.com/srikarnagavelli/?igsh=abc123#profile"
    assert extract_social_handle("instagram", query_url) == "srikarnagavelli"


def test_normalize_social_url_instagram() -> None:
    expected = "https://www.instagram.com/srikarnagavelli/"
    assert normalize_social_url("instagram", "srikarnagavelli") == expected
    assert normalize_social_url("instagram", "@srikarnagavelli") == expected
    assert normalize_social_url("instagram", "instagram.com/srikarnagavelli") == expected
    assert normalize_social_url("instagram", "https://instagram.com/srikarnagavelli") == expected
    assert normalize_social_url("instagram", "https://www.instagram.com/srikarnagavelli/") == expected
    assert normalize_social_url("instagram", "https://www.instagram.com/srikarnagavelli?igsh=123") == expected


def test_creator_profile_instagram_normalization(creator: APIClient) -> None:
    # Patch profile with plain Instagram handle
    resp = creator.patch(
        "/v1/creators/me/",
        {
            "socialMediaUrls": {
                "instagram": "srikarnagavelli",
            },
            "socialAccounts": [
                {
                    "platform": "instagram",
                    "handle": "https://www.instagram.com/srikarnagavelli/?hl=en",
                }
            ],
        },
        format="json",
    )
    assert resp.status_code == 200, resp.json()
    data = resp.json()["data"]

    # 1. socialMediaUrls.instagram must be fully-qualified external URL
    assert data["socialMediaUrls"]["instagram"] == "https://www.instagram.com/srikarnagavelli/"

    # 2. socialAccounts[].handle must be cleanly extracted username handle
    assert len(data["socialAccounts"]) == 1
    assert data["socialAccounts"][0]["platform"] == "instagram"
    assert data["socialAccounts"][0]["handle"] == "srikarnagavelli"

    # 3. Check public card and detail endpoint output
    creator_id = data["id"]
    public_client = APIClient()
    detail_resp = public_client.get(f"/v1/creators/{creator_id}/")
    assert detail_resp.status_code == 200
    detail_data = detail_resp.json()["data"]
    assert detail_data["socialMediaUrls"]["instagram"] == "https://www.instagram.com/srikarnagavelli/"
    assert detail_data["socialAccounts"][0]["handle"] == "srikarnagavelli"


def test_creator_card_synthesizes_urls_from_social_accounts(creator: APIClient) -> None:
    # Set only socialAccounts with @handle
    resp = creator.patch(
        "/v1/creators/me/",
        {
            "socialAccounts": [
                {
                    "platform": "instagram",
                    "handle": "@coolcreator",
                }
            ]
        },
        format="json",
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["socialAccounts"][0]["handle"] == "coolcreator"
    # socialMediaUrls should synthesize the full URL even if socialMediaUrls wasn't explicitly passed
    assert data["socialMediaUrls"]["instagram"] == "https://www.instagram.com/coolcreator/"
