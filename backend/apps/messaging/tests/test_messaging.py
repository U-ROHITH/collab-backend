from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from apps.notifications.models import Notification
from apps.uploads.models import Upload
from tests.fixtures import USAGE_RIGHTS

pytestmark = pytest.mark.django_db


def send_offer(brand: Any, creator: Any) -> None:
    r = brand.post(
        "/v1/offers/",
        {
            "creatorId": str(creator.user.creator.id),
            "deliverable": "1 Reel",
            "budget": 500_000,
            "deadline": (timezone.now() + timedelta(days=5)).isoformat(),
            "message": "hi",
            "usageRights": USAGE_RIGHTS,
        },
        format="json",
    )
    assert r.status_code == 201


def test_cannot_message_without_relationship(brand: Any, creator: Any) -> None:
    r = brand.post("/v1/conversations/", {"creatorId": str(creator.user.creator.id)}, format="json")
    assert r.status_code == 422 and r.json()["error"]["code"] == "CONVERSATION_NOT_ALLOWED"


def test_full_conversation_flow(brand: Any, creator: Any) -> None:
    send_offer(brand, creator)
    conv = brand.post("/v1/conversations/", {"creatorId": str(creator.user.creator.id)}, format="json")
    assert conv.status_code == 201
    cid = conv.json()["data"]["id"]
    assert conv.json()["data"]["withParty"]["type"] == "creator"
    # creator opening from its side gets the same thread
    same = creator.post("/v1/conversations/", {"brandId": str(brand.user.brand.id)}, format="json")
    assert same.json()["data"]["id"] == cid

    for text in ("Hi Ananya!", "Can you do a Reel?"):
        assert brand.post(f"/v1/conversations/{cid}/messages/", {"text": text}, format="json").status_code == 201

    listing = creator.get("/v1/conversations/").json()["data"]
    assert listing[0]["unread"] == 2 and listing[0]["lastMessage"]["text"] == "Can you do a Reel?"
    assert listing[0]["lastMessage"]["fromMe"] is False
    assert brand.get("/v1/conversations/").json()["data"][0]["unread"] == 0  # sender's own messages aren't unread
    # one notification per conversation, not per message
    assert Notification.objects.filter(user=creator.user, type="new_message").count() == 1

    msgs = creator.get(f"/v1/conversations/{cid}/messages/").json()["data"]
    assert [m["text"] for m in msgs] == ["Can you do a Reel?", "Hi Ananya!"]  # newest first

    assert creator.post(f"/v1/conversations/{cid}/read/").status_code == 204
    assert creator.get("/v1/conversations/").json()["data"][0]["unread"] == 0
    assert not Notification.objects.filter(user=creator.user, type="new_message", read_at__isnull=True).exists()


def test_outsiders_cannot_read_or_send(brand: Any, creator: Any, make_brand: Any, make_creator: Any) -> None:
    send_offer(brand, creator)
    cid = brand.post("/v1/conversations/", {"creatorId": str(creator.user.creator.id)}, format="json").json()["data"][
        "id"
    ]
    for outsider in (make_brand("Spy Brand"), make_creator("Spy Creator")):
        assert outsider.get(f"/v1/conversations/{cid}/").status_code == 404
        assert outsider.get(f"/v1/conversations/{cid}/messages/").status_code == 404
        assert outsider.post(f"/v1/conversations/{cid}/messages/", {"text": "x"}, format="json").status_code == 404


def test_message_validation(brand: Any, creator: Any) -> None:
    send_offer(brand, creator)
    cid = brand.post("/v1/conversations/", {"creatorId": str(creator.user.creator.id)}, format="json").json()["data"][
        "id"
    ]
    assert brand.post(f"/v1/conversations/{cid}/messages/", {"text": "   "}, format="json").status_code == 400
    assert brand.post(f"/v1/conversations/{cid}/messages/", {"text": "x" * 4001}, format="json").status_code == 400
    assert (
        brand.post(f"/v1/conversations/{cid}/messages/", {"text": "hi", "senderId": "x"}, format="json").status_code
        == 400
    )


def test_attachment_shared_only_with_conversation(brand: Any, creator: Any, make_brand: Any, fake_storage: Any) -> None:
    send_offer(brand, creator)
    cid = brand.post("/v1/conversations/", {"creatorId": str(creator.user.creator.id)}, format="json").json()["data"][
        "id"
    ]
    pdf = b"%PDF-1.4 brief"
    up = brand.post(
        "/v1/uploads/", {"kind": "chat_attachment", "contentType": "application/pdf", "size": len(pdf)}, format="json"
    ).json()["data"]["upload"]
    fake_storage.put("private", Upload.objects.get(pk=up["id"]).staging_key, pdf, "application/pdf")
    assert brand.post(f"/v1/uploads/{up['id']}/complete/").status_code == 200

    m = brand.post(f"/v1/conversations/{cid}/messages/", {"attachmentUploadId": up["id"]}, format="json")
    assert m.status_code == 201 and m.json()["data"]["attachment"]["uploadId"] == up["id"]
    # same file can't be re-sent elsewhere
    again = brand.post(f"/v1/conversations/{cid}/messages/", {"attachmentUploadId": up["id"]}, format="json")
    assert again.status_code == 409
    assert creator.get(f"/v1/uploads/{up['id']}/download-url/").status_code == 200
    assert make_brand("Nosy").get(f"/v1/uploads/{up['id']}/download-url/").status_code == 404
