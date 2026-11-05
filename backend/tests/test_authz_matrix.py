"""Authorization matrix: every protected resource checked against anon / wrong party / right party.

Rule: a user who isn't a party gets 404 (not 403) so resource existence isn't leaked.
"""

from typing import Any

import pytest

from apps.orders.services import system_event

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(make_brand: Any, make_creator: Any, campaign_payload: dict[str, Any]) -> dict[str, Any]:
    brand, other_brand = make_brand("Nimbu"), make_brand("Other Co")
    creator, other_creator = make_creator("Ananya"), make_creator("Ravi")
    pending_creator = make_creator("Pending", approved=False)
    campaign = brand.post("/v1/campaigns/", campaign_payload, format="json").json()["data"]
    bid = creator.post(
        f"/v1/campaigns/{campaign['id']}/bids/", {"price": 800_000, "message": "hi", "deliveryDays": 5}, format="json"
    ).json()["data"]
    order = brand.post(f"/v1/campaigns/{campaign['id']}/bids/{bid['id']}/select/").json()["data"]
    system_event(order_id=order["id"], event="fund")
    return dict(
        brand=brand,
        other_brand=other_brand,
        creator=creator,
        other_creator=other_creator,
        pending=pending_creator,
        campaign=campaign,
        bid=bid,
        order=order,
    )


def test_order_visibility(world: dict[str, Any], anon: Any) -> None:
    url = f"/v1/orders/{world['order']['id']}/"
    assert anon.get(url).status_code == 401
    assert world["other_brand"].get(url).status_code == 404
    assert world["other_creator"].get(url).status_code == 404
    assert world["brand"].get(url).status_code == 200
    assert world["creator"].get(url).status_code == 200
    assert world["other_brand"].get("/v1/orders/").json()["data"] == []


@pytest.mark.parametrize(
    ("actor", "path", "body", "expected"),
    [
        # only the order's creator can deliver
        ("brand", "deliverables/", {"contentUrl": "https://x.com/a"}, 404),
        ("other_creator", "deliverables/", {"contentUrl": "https://x.com/a"}, 404),
        # only the order's brand can approve / request revisions
        ("creator", "approve/", None, 403),
        ("other_brand", "approve/", None, 404),
        ("other_brand", "revisions/", {"feedback": "x"}, 404),
        ("other_creator", "cancel/", {"reason": "not mine"}, 404),
        ("other_brand", "disputes/", {"reason": "not mine"}, 404),
    ],
)
def test_order_actions_matrix(world: dict[str, Any], actor: str, path: str, body: Any, expected: int) -> None:
    resp = world[actor].post(f"/v1/orders/{world['order']['id']}/{path}", body, format="json")
    assert resp.status_code == expected, resp.json()


def test_campaign_matrix(world: dict[str, Any], anon: Any) -> None:
    cid = world["campaign"]["id"]
    assert anon.get(f"/v1/campaigns/{cid}/").status_code == 401
    assert world["other_brand"].get(f"/v1/campaigns/{cid}/").status_code == 404
    assert world["other_brand"].patch(f"/v1/campaigns/{cid}/", {"title": "hacked"}, format="json").status_code == 404
    assert world["creator"].patch(f"/v1/campaigns/{cid}/", {"title": "hacked"}, format="json").status_code == 404
    # unapproved creators can neither see nor bid
    assert world["pending"].get("/v1/campaigns/").json()["data"] == []
    r = world["pending"].post(
        f"/v1/campaigns/{cid}/bids/", {"price": 100_00, "message": "x", "deliveryDays": 1}, format="json"
    )
    assert r.status_code == 404
    # a creator only sees its own bid; other brand sees none
    assert world["other_creator"].get(f"/v1/campaigns/{cid}/bids/").json()["data"] == []
    assert world["other_brand"].get(f"/v1/campaigns/{cid}/bids/").json()["data"] == []
    # another brand can't select the bid
    sel = world["other_brand"].post(f"/v1/campaigns/{cid}/bids/{world['bid']['id']}/select/")
    assert sel.status_code == 404


def test_role_gates(world: dict[str, Any]) -> None:
    assert world["creator"].get("/v1/brands/me/").status_code == 403
    assert world["brand"].get("/v1/creators/me/").status_code == 403
    assert world["creator"].post("/v1/offers/", {}, format="json").status_code == 403
    assert world["brand"].get("/v1/creators/me/offers/").status_code == 403


def test_mass_assignment_blocked(world: dict[str, Any]) -> None:
    r = world["pending"].patch("/v1/creators/me/", {"approvalStatus": "approved"}, format="json")
    assert r.status_code == 400
    r = world["pending"].patch("/v1/creators/me/", {"approval_status": "approved", "verified": True}, format="json")
    assert r.status_code == 400
    assert world["pending"].get("/v1/creators/me/").json()["data"]["approvalStatus"] == "pending"


def test_notifications_are_private(world: dict[str, Any]) -> None:
    notes = world["brand"].get("/v1/notifications/").json()["data"]
    assert notes
    r = world["other_brand"].patch(f"/v1/notifications/{notes[0]['id']}/read/")
    assert r.status_code == 404


def test_dev_endpoint_disabled_outside_debug(world: dict[str, Any]) -> None:
    r = world["brand"].post(f"/v1/dev/orders/{world['order']['id']}/simulate-payment/")
    assert r.status_code == 403


def test_https_only_deliverable_urls(world: dict[str, Any]) -> None:
    for url in ("http://x.com/a", "javascript:alert(1)", "file:///etc/passwd"):
        r = world["creator"].post(
            f"/v1/orders/{world['order']['id']}/deliverables/", {"contentUrl": url}, format="json"
        )
        assert r.status_code == 400
