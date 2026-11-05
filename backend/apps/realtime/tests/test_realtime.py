from typing import Any

import pytest
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator

from apps.identity.models import User
from apps.identity.services import complete_signup
from apps.realtime.push import push_to_user
from apps.realtime.tickets import issue_ticket
from config.asgi import application

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]
ORIGIN = [(b"origin", b"http://localhost:5173")]


@database_sync_to_async
def make_user() -> User:
    user = User.objects.create_user(phone="+919999900001")
    complete_signup(user=user, role="creator", full_name="Live Tester")
    return User.objects.get(pk=user.pk)


async def connect(query: str) -> tuple[WebsocketCommunicator, bool, Any]:
    comm = WebsocketCommunicator(application, f"/ws/{query}", headers=ORIGIN)
    connected, code = await comm.connect()
    return comm, connected, code


async def test_rejects_missing_or_bad_ticket() -> None:
    for query in ("", "?ticket=nope"):
        comm, connected, code = await connect(query)
        assert not connected and code == 4401
        await comm.disconnect()


async def test_ticket_is_single_use_and_events_are_delivered() -> None:
    user = await make_user()
    ticket = await database_sync_to_async(issue_ticket)(user.pk)

    comm, connected, _ = await connect(f"?ticket={ticket}")
    assert connected
    assert (await comm.receive_json_from())["event"] == "connected"

    await database_sync_to_async(push_to_user)(user.pk, {"event": "new_message", "conversationId": "c1"})
    assert (await comm.receive_json_from(timeout=2)) == {"event": "new_message", "conversationId": "c1"}

    await comm.send_json_to({"type": "ping"})
    assert (await comm.receive_json_from()) == {"type": "pong"}

    reuse, ok, code = await connect(f"?ticket={ticket}")
    assert not ok and code == 4401
    await reuse.disconnect()
    await comm.disconnect()


async def test_ticket_endpoint_requires_auth(client: Any) -> None:
    resp = await database_sync_to_async(client.post)("/v1/realtime/ticket/")
    assert resp.status_code == 401
