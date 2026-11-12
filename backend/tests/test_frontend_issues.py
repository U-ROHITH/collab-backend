"""Fixes for the frontend's backend-issues report of 2026-11-12 (numbers match the report)."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.identity.models import User
from apps.identity.services import issue_tokens

pytestmark = pytest.mark.django_db

BID = {"price": 300_000, "message": "Happy to do this", "deliveryDays": 5}
GOOD = "Monsoon-Tea-42"


def ok(resp: Any, status: int = 200) -> Any:
    assert resp.status_code == status, resp.content
    return resp.json()["data"]


def _campaign(brand: APIClient, payload: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return ok(brand.post("/v1/campaigns/", {**payload, **changes}, format="json"), 201)


# --- #1 My bids ----------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/v1/creators/me/bids/", "/v1/creators/me/campaigns/"])
def test_my_bids_requires_sign_in(anon: APIClient, path: str) -> None:
    resp = anon.get(path)
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.parametrize("path", ["/v1/creators/me/bids/", "/v1/creators/me/campaigns/"])
def test_my_bids_lists_only_this_creators_bids_with_campaign(
    brand: APIClient, make_creator: Any, campaign_payload: dict[str, Any], path: str
) -> None:
    mine, other = make_creator("Ananya Rao"), make_creator("Ravi Kumar")
    bid_on = _campaign(brand, campaign_payload, title="Bid on this")
    _campaign(brand, campaign_payload, title="Not bid on")
    ok(mine.post(f"/v1/campaigns/{bid_on['id']}/bids/", BID, format="json"), 201)
    ok(other.post(f"/v1/campaigns/{bid_on['id']}/bids/", BID, format="json"), 201)

    rows = ok(mine.get(path))
    assert len(rows) == 1
    row = rows[0]
    assert row["campaign"]["id"] == bid_on["id"] and row["campaign"]["title"] == "Bid on this"
    assert row["campaign"]["brand"] is None  # blind bidding still applies
    assert row["campaign"]["bidCount"] == 2
    assert row["price"]["amount"] == 300_000 and row["status"] == "pending"


def test_my_bids_is_creator_only(brand: APIClient) -> None:
    assert brand.get("/v1/creators/me/bids/").status_code == 403


def test_my_bids_query_count_is_flat(
    brand: APIClient, creator: APIClient, campaign_payload: dict[str, Any], django_assert_max_num_queries: Any
) -> None:
    for i in range(5):
        c = _campaign(brand, campaign_payload, title=f"C{i}")
        ok(creator.post(f"/v1/campaigns/{c['id']}/bids/", BID, format="json"), 201)
    with django_assert_max_num_queries(8):
        assert len(ok(creator.get("/v1/creators/me/bids/"))) == 5


# --- #5 bidCount ----------------------------------------------------------------------------------


def test_campaigns_carry_bid_count_excluding_withdrawn(
    anon: APIClient, brand: APIClient, make_creator: Any, campaign_payload: dict[str, Any]
) -> None:
    c = _campaign(brand, campaign_payload)
    a, b = make_creator("Ananya Rao"), make_creator("Ravi Kumar")
    ok(a.post(f"/v1/campaigns/{c['id']}/bids/", BID, format="json"), 201)
    withdrawn = ok(b.post(f"/v1/campaigns/{c['id']}/bids/", BID, format="json"), 201)
    ok(b.post(f"/v1/campaigns/{c['id']}/bids/{withdrawn['id']}/withdraw/"))

    assert ok(anon.get("/v1/campaigns/"))[0]["bidCount"] == 1
    assert ok(anon.get(f"/v1/campaigns/{c['id']}/"))["bidCount"] == 1
    assert ok(brand.get("/v1/campaigns/"))[0]["bidCount"] == 1


def test_campaign_list_query_count_is_flat(
    anon: APIClient, brand: APIClient, campaign_payload: dict[str, Any], django_assert_max_num_queries: Any
) -> None:
    for i in range(6):
        _campaign(brand, campaign_payload, title=f"C{i}")
    with django_assert_max_num_queries(2):
        assert len(ok(anon.get("/v1/campaigns/"))) == 6


# --- #6 Public campaign detail -------------------------------------------------------------------


def test_guest_can_open_an_open_campaign_with_guest_view(
    anon: APIClient, brand: APIClient, campaign_payload: dict[str, Any]
) -> None:
    c = _campaign(brand, campaign_payload)
    data = ok(anon.get(f"/v1/campaigns/{c['id']}/"))
    assert data["id"] == c["id"] and data["title"] == campaign_payload["title"]
    assert data["brief"] is None and data["usageRights"] is None and data["brand"] is None


def test_guest_cannot_open_a_draft(anon: APIClient, brand: APIClient, campaign_payload: dict[str, Any]) -> None:
    c = _campaign(brand, campaign_payload, status="draft")
    assert anon.get(f"/v1/campaigns/{c['id']}/").status_code == 404


def test_approved_creator_gets_full_detail(
    brand: APIClient, creator: APIClient, campaign_payload: dict[str, Any]
) -> None:
    c = _campaign(brand, campaign_payload)
    assert ok(creator.get(f"/v1/campaigns/{c['id']}/"))["brief"] == campaign_payload["brief"]


# --- #7 Filters -----------------------------------------------------------------------------------


def test_platform_and_budget_filters(anon: APIClient, brand: APIClient, campaign_payload: dict[str, Any]) -> None:
    yt = _campaign(brand, campaign_payload, title="YT", platform="youtube", budgetMin=1_000_000, budgetMax=2_000_000)
    ig = _campaign(brand, campaign_payload, title="IG", platform="instagram", budgetMin=100_000, budgetMax=400_000)

    def ids(query: str) -> set[str]:
        return {c["id"] for c in ok(anon.get(f"/v1/campaigns/?{query}"))}

    assert ids("platform=youtube") == {yt["id"]}
    assert ids("budgetMin__gte=500000") == {yt["id"]}
    assert ids("budgetMax__lte=500000") == {ig["id"]}
    assert ids("platform=instagram&budgetMax__lte=500000") == {ig["id"]}
    assert ids("limit=500") == {yt["id"], ig["id"]}  # accepted, capped at 50


@pytest.mark.parametrize("query", ["category=food", "platform=myspace", "budgetMin__gte=abc", "sort=new"])
def test_unknown_or_bad_filters_are_rejected(anon: APIClient, query: str) -> None:
    resp = anon.get(f"/v1/campaigns/?{query}")
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "VALIDATION_ERROR"


# --- #10 JSON for unknown routes, trailing slash optional ----------------------------------------


def test_unknown_api_route_returns_json_envelope(anon: APIClient) -> None:
    resp = anon.get("/v1/nope/")
    assert resp.status_code == 404 and resp["Content-Type"].startswith("application/json")
    body = resp.json()
    assert body["success"] is False and body["error"]["code"] == "NOT_FOUND" and body["data"] is None


def test_missing_trailing_slash_is_served_not_redirected(anon: APIClient) -> None:
    resp = anon.get("/v1/campaigns")
    assert resp.status_code == 200 and resp.json()["success"] is True
    resp = anon.post("/v1/auth/login", {"identifier": "x@example.com", "password": GOOD}, format="json")
    assert resp.status_code == 401


# --- #3 hasPassword and password change ----------------------------------------------------------


def _client(user: User, method: str | None = "otp") -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_tokens(user, method=method).access}")
    return client


def test_me_reports_has_password() -> None:
    without = User.objects.create_user(email="a@example.com")
    with_pw = User.objects.create_user(email="b@example.com", password=GOOD)
    assert ok(_client(without).get("/v1/auth/me/"))["hasPassword"] is False
    assert ok(_client(with_pw).get("/v1/auth/me/"))["hasPassword"] is True


def test_no_password_login_hint_does_not_reveal_the_account(anon: APIClient) -> None:
    User.objects.create_user(email="code-only@example.com")
    a = anon.post("/v1/auth/login/", {"identifier": "code-only@example.com", "password": GOOD}, format="json")
    b = anon.post("/v1/auth/login/", {"identifier": "nobody@example.com", "password": GOOD}, format="json")
    assert a.json()["error"] == {**b.json()["error"], "requestId": a.json()["error"]["requestId"]}
    assert "sign in with a code" in a.json()["error"]["message"]


def test_change_password_right_after_code_sign_in(anon: APIClient) -> None:
    user = User.objects.create_user(email="a@example.com")
    resp = _client(user, "otp").post(
        "/v1/auth/password/change/", {"password": GOOD, "confirmPassword": GOOD}, format="json"
    )
    data = ok(resp)
    assert data["access"] and data["user"]["hasPassword"] is True
    assert (
        anon.post("/v1/auth/login/", {"identifier": "a@example.com", "password": GOOD}, format="json").status_code
        == 200
    )


def test_change_password_with_current_password(anon: APIClient) -> None:
    user = User.objects.create_user(email="a@example.com", password="Old-Password-99")
    body = {"currentPassword": "Old-Password-99", "password": GOOD, "confirmPassword": GOOD}
    ok(_client(user, "password").post("/v1/auth/password/change/", body, format="json"))
    assert (
        anon.post("/v1/auth/login/", {"identifier": "a@example.com", "password": GOOD}, format="json").status_code
        == 200
    )


def test_change_password_wrong_current_password_is_a_field_error() -> None:
    user = User.objects.create_user(email="a@example.com", password="Old-Password-99")
    body = {"currentPassword": "nope-nope-nope", "password": GOOD, "confirmPassword": GOOD}
    resp = _client(user, "password").post("/v1/auth/password/change/", body, format="json")
    assert resp.status_code == 400 and "currentPassword" in resp.json()["error"]["details"]


def test_change_password_needs_current_password_or_fresh_code_sign_in() -> None:
    user = User.objects.create_user(email="a@example.com", password="Old-Password-99")
    body = {"password": GOOD, "confirmPassword": GOOD}
    resp = _client(user, "password").post("/v1/auth/password/change/", body, format="json")
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "REAUTH_REQUIRED"


def test_code_sign_in_is_only_fresh_for_a_few_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.identity import services

    user = User.objects.create_user(email="a@example.com")
    client = _client(user, "otp")
    monkeypatch.setattr(services, "PASSWORD_CHANGE_FRESH_SECONDS", -1)
    resp = client.post("/v1/auth/password/change/", {"password": GOOD, "confirmPassword": GOOD}, format="json")
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "REAUTH_REQUIRED"


def test_change_password_signs_out_other_sessions() -> None:
    user = User.objects.create_user(email="a@example.com")
    other = issue_tokens(user, method="otp").refresh
    ok(_client(user).post("/v1/auth/password/change/", {"password": GOOD, "confirmPassword": GOOD}, format="json"))
    assert APIClient().post("/v1/auth/token/refresh/", {"refresh": other}, format="json").status_code == 401


def test_refreshing_does_not_make_a_session_fresh_again() -> None:
    user = User.objects.create_user(email="a@example.com", password="Old-Password-99")
    refresh = issue_tokens(user, method="password").refresh
    new = ok(APIClient().post("/v1/auth/token/refresh/", {"refresh": refresh}, format="json"))
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {new['access']}")
    resp = client.post("/v1/auth/password/change/", {"password": GOOD, "confirmPassword": GOOD}, format="json")
    assert resp.status_code == 403


# --- #4 New numbers must go through onboarding --------------------------------------------------


def test_otp_for_a_brand_new_number_needs_onboarding(anon: APIClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.identity import providers

    codes: list[str] = []
    monkeypatch.setattr(providers, "send_sms_otp", lambda _d, code: codes.append(code))
    otp = ok(anon.post("/v1/auth/otp/request/", {"channel": "sms", "destination": "9123456780"}, format="json"), 202)
    user = ok(anon.post("/v1/auth/otp/verify/", {"challengeId": otp["challengeId"], "code": codes[-1]}, format="json"))[
        "user"
    ]
    assert user["needsOnboarding"] is True and user["role"] is None and user["hasPassword"] is False


def test_token_from_before_a_password_change_cannot_skip_current_password() -> None:
    user = User.objects.create_user(email="a@example.com")
    old_client = _client(user, "otp")  # fresh code sign-in
    body = {"password": GOOD, "confirmPassword": GOOD}
    ok(_client(user, "otp").post("/v1/auth/password/change/", body, format="json"))
    again = {"password": "Another-Pass-77", "confirmPassword": "Another-Pass-77"}
    resp = old_client.post("/v1/auth/password/change/", again, format="json")
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "REAUTH_REQUIRED"
