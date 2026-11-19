import datetime
import uuid
from typing import Any

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.audit import verify_audit_chain
from apps.identity.models import AdminRole, AuditLog, Role, User
from apps.identity.services import issue_admin_tokens
from apps.notifications.models import Notification
from apps.support.models import (
    SupportMessage,
    SupportTicket,
    TicketStatus,
    TicketTopic,
)
from apps.uploads.models import Upload, UploadKind, UploadStatus
from apps.uploads.services import can_access

pytestmark = pytest.mark.django_db


def _make_admin_client(role: str = AdminRole.TRUST_SAFETY) -> tuple[APIClient, User]:
    admin = User.objects.create_user(
        email=f"admin_{role}_{uuid.uuid4().hex[:6]}@linkloom.test",
        password="AdminPass123!",
        role=Role.ADMIN,
        admin_role=role,
        is_staff=True,
    )
    client = APIClient()
    tokens = issue_admin_tokens(admin)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens.access}")
    client.user = admin  # type: ignore[attr-defined]
    return client, admin


def _make_upload(
    user: User,
    kind: str = UploadKind.SUPPORT_ATTACHMENT,
    status: str = UploadStatus.READY,
) -> Upload:
    return Upload.objects.create(
        owner=user,
        kind=kind,
        status=status,
        declared_content_type="image/png",
        declared_size=1024,
        staging_key=f"uploads/{uuid.uuid4()}.png",
        storage_key=f"storage/{uuid.uuid4()}.png",
        content_type="image/png",
        size_bytes=1024,
        original_filename="screenshot.png",
    )


# ---------------------------------------------------------------------------
# 1. Sequential Numbering & Creation
# ---------------------------------------------------------------------------


def test_sequential_ticket_numbering(brand: APIClient) -> None:
    t1 = SupportTicket.objects.create(
        user=brand.user,
        topic=TicketTopic.ACCOUNT,
        subject="Ticket One",
    )
    assert t1.number >= 1042
    assert t1.formatted_number == f"LL-{t1.number}"

    resp = brand.post(
        "/v1/support/tickets/",
        {"topic": "bug", "subject": "Ticket Two Subject", "message": "Here is a bug details text"},
        format="json",
    )
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["number"] == t1.number + 1
    assert data["ticketNumber"] == f"LL-{t1.number + 1}"


def test_create_ticket_success_and_admin_notification(brand: APIClient) -> None:
    _, admin = _make_admin_client(role=AdminRole.SUPER_ADMIN)

    payload = {
        "topic": "payments",
        "subject": "Payout delayed",
        "message": "My recent payout has not reached my account yet.",
    }
    resp = brand.post("/v1/support/tickets/", payload, format="json")
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["topic"] == "payments"
    assert data["subject"] == "Payout delayed"
    assert data["status"] == "open"
    assert len(data["messages"]) == 1
    assert data["messages"][0]["authorType"] == "user"
    assert data["messages"][0]["text"] == payload["message"]

    # Verify admin received in-app notification
    notif = Notification.objects.filter(user=admin, type="support_new").first()
    assert notif is not None
    assert data["ticketNumber"] in notif.title


# ---------------------------------------------------------------------------
# 2. Rate Limits & Open Ticket Cap
# ---------------------------------------------------------------------------


def test_rate_limit_open_tickets(brand: APIClient) -> None:
    for i in range(5):
        SupportTicket.objects.create(
            user=brand.user,
            topic=TicketTopic.OTHER,
            subject=f"Open Ticket {i}",
            status=TicketStatus.OPEN,
        )

    resp = brand.post(
        "/v1/support/tickets/",
        {"topic": "other", "subject": "Sixth Ticket", "message": "Cannot open more than 5 open tickets"},
        format="json",
    )
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "TOO_MANY_OPEN"


def test_rate_limit_daily_creation_cap(brand: APIClient) -> None:
    # 10 tickets created in last 24h (even if resolved)
    for i in range(10):
        SupportTicket.objects.create(
            user=brand.user,
            topic=TicketTopic.OTHER,
            subject=f"Resolved Ticket {i}",
            status=TicketStatus.RESOLVED,
        )

    resp = brand.post(
        "/v1/support/tickets/",
        {"topic": "other", "subject": "Eleventh Ticket", "message": "Cannot create more than 10 in 24h"},
        format="json",
    )
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "TOO_MANY_OPEN"


# ---------------------------------------------------------------------------
# 3. Order & Attachment Validations
# ---------------------------------------------------------------------------


def test_order_validation(brand: APIClient, creator: APIClient, campaign_payload: dict[str, Any]) -> None:
    # Create order belonging to brand and creator
    c = brand.post("/v1/campaigns/", campaign_payload, format="json").json()["data"]
    bid = creator.post(
        f"/v1/campaigns/{c['id']}/bids/",
        {"price": 800_000, "message": "hi", "deliveryDays": 5},
        format="json",
    ).json()["data"]
    order_data = brand.post(f"/v1/campaigns/{c['id']}/bids/{bid['id']}/select/").json()["data"]
    order_id = order_data["id"]

    # 1. Valid order from brand
    resp = brand.post(
        "/v1/support/tickets/",
        {
            "topic": "deal",
            "orderId": order_id,
            "subject": "Help with order",
            "message": "Order discussion message",
        },
        format="json",
    )
    assert resp.status_code == 201
    assert resp.json()["data"]["orderId"] == order_id

    # 2. Random unowned order
    unowned_id = str(uuid.uuid4())
    resp = brand.post(
        "/v1/support/tickets/",
        {
            "topic": "deal",
            "orderId": unowned_id,
            "subject": "Help with unowned order",
            "message": "Order discussion message",
        },
        format="json",
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "ORDER_NOT_YOURS"


def test_attachment_validation(brand: APIClient, creator: APIClient) -> None:
    # Too many attachments (> 3)
    resp = brand.post(
        "/v1/support/tickets/",
        {
            "topic": "bug",
            "subject": "Too many attachments",
            "message": "Testing max 3 uploads",
            "attachmentUploadIds": [str(uuid.uuid4()) for _ in range(4)],
        },
        format="json",
    )
    assert resp.status_code == 400

    # Attachment owned by someone else
    other_upload = _make_upload(user=creator.user)
    resp = brand.post(
        "/v1/support/tickets/",
        {
            "topic": "bug",
            "subject": "Wrong owner upload",
            "message": "Testing ownership",
            "attachmentUploadIds": [str(other_upload.id)],
        },
        format="json",
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "ATTACHMENT_INVALID"

    # Attachment not ready
    pending_upload = _make_upload(user=brand.user, status=UploadStatus.PENDING)
    resp = brand.post(
        "/v1/support/tickets/",
        {
            "topic": "bug",
            "subject": "Pending upload test",
            "message": "Testing readiness",
            "attachmentUploadIds": [str(pending_upload.id)],
        },
        format="json",
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "ATTACHMENT_INVALID"

    # Valid ready attachment
    valid_upload = _make_upload(user=brand.user)
    resp = brand.post(
        "/v1/support/tickets/",
        {
            "topic": "bug",
            "subject": "Valid ready upload",
            "message": "Testing valid ready upload",
            "attachmentUploadIds": [str(valid_upload.id)],
        },
        format="json",
    )
    assert resp.status_code == 201
    assert resp.json()["data"]["messages"][0]["attachmentIds"] == [str(valid_upload.id)]


# ---------------------------------------------------------------------------
# 4. User Ticket Views, Messages, Internal Note Isolation
# ---------------------------------------------------------------------------


def test_user_ticket_detail_and_internal_notes_hidden(brand: APIClient) -> None:
    admin_client, admin = _make_admin_client(role=AdminRole.TRUST_SAFETY)

    # Brand creates ticket
    resp = brand.post(
        "/v1/support/tickets/",
        {"topic": "account", "subject": "Account query", "message": "Initial user question"},
        format="json",
    )
    ticket_id = resp.json()["data"]["id"]

    # Admin adds internal note
    admin_client.post(
        f"/v1/admin/support/tickets/{ticket_id}/notes/",
        {"note": "Confidential staff observation: check KYC flags."},
        format="json",
    )

    # Admin replies to user
    admin_client.post(
        f"/v1/admin/support/tickets/{ticket_id}/reply/",
        {"message": "Hello! We are looking into this."},
        format="json",
    )

    # Admin GET detail shows all 3 messages (initial + note + reply)
    admin_detail = admin_client.get(f"/v1/admin/support/tickets/{ticket_id}/").json()["data"]
    assert len(admin_detail["messages"]) == 3
    assert any(m["internal"] for m in admin_detail["messages"])

    # User GET detail strictly HIDES internal note (only 2 messages)
    user_detail = brand.get(f"/v1/support/tickets/{ticket_id}/").json()["data"]
    assert len(user_detail["messages"]) == 2
    assert not any(m["internal"] for m in user_detail["messages"])
    # unread count cleared on GET
    assert user_detail["unreadForUser"] == 0


def test_user_add_message_and_ticket_closed_conflict(brand: APIClient) -> None:
    resp = brand.post(
        "/v1/support/tickets/",
        {"topic": "account", "subject": "Question", "message": "Initial text"},
        format="json",
    )
    ticket_id = resp.json()["data"]["id"]

    # User appends message
    resp_msg = brand.post(
        f"/v1/support/tickets/{ticket_id}/messages/",
        {"message": "Follow-up question from brand"},
        format="json",
    )
    assert resp_msg.status_code == 201
    assert resp_msg.json()["data"]["text"] == "Follow-up question from brand"

    # Close ticket
    ticket = SupportTicket.objects.get(pk=ticket_id)
    ticket.status = TicketStatus.CLOSED
    ticket.save()

    # Appending message to closed ticket -> 409 TICKET_CLOSED
    resp_closed = brand.post(
        f"/v1/support/tickets/{ticket_id}/messages/",
        {"message": "Attempting message on closed ticket"},
        format="json",
    )
    assert resp_closed.status_code == 409
    assert resp_closed.json()["error"]["code"] == "TICKET_CLOSED"


# ---------------------------------------------------------------------------
# 5. User Resolve and Reopen Lifecycles
# ---------------------------------------------------------------------------


def test_user_resolve_and_reopen_lifecycle(brand: APIClient) -> None:
    resp = brand.post(
        "/v1/support/tickets/",
        {"topic": "other", "subject": "Resolve test", "message": "Testing resolve"},
        format="json",
    )
    ticket_id = resp.json()["data"]["id"]

    # Resolve
    resp_res = brand.post(f"/v1/support/tickets/{ticket_id}/resolve/")
    assert resp_res.status_code == 200
    assert resp_res.json()["data"]["status"] == "resolved"

    # Reopen within 7 days -> succeeds
    resp_reopen = brand.post(f"/v1/support/tickets/{ticket_id}/reopen/")
    assert resp_reopen.status_code == 200
    assert resp_reopen.json()["data"]["status"] == "open"

    # Resolve again, but backdate resolved_at > 7 days ago
    ticket = SupportTicket.objects.get(pk=ticket_id)
    ticket.status = TicketStatus.RESOLVED
    ticket.resolved_at = timezone.now() - datetime.timedelta(days=8)
    ticket.save()

    # Reopen after 8 days -> 409 REOPEN_EXPIRED
    resp_expired = brand.post(f"/v1/support/tickets/{ticket_id}/reopen/")
    assert resp_expired.status_code == 409
    assert resp_expired.json()["error"]["code"] == "REOPEN_EXPIRED"


# ---------------------------------------------------------------------------
# 6. Admin Role Matrix & Finance Permissions
# ---------------------------------------------------------------------------


def test_admin_role_matrix_and_finance_restrictions(brand: APIClient) -> None:
    # Create ticket
    resp = brand.post(
        "/v1/support/tickets/",
        {"topic": "payments", "subject": "Payment query", "message": "Where is my invoice?"},
        format="json",
    )
    ticket_id = resp.json()["data"]["id"]

    finance_client, _ = _make_admin_client(role=AdminRole.FINANCE)
    ts_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)

    # 1. Non-admin user gets 403 on admin endpoint
    assert brand.get("/v1/admin/support/tickets/").status_code == 403

    # 2. Finance can read list and detail
    assert finance_client.get("/v1/admin/support/tickets/").status_code == 200
    assert finance_client.get(f"/v1/admin/support/tickets/{ticket_id}/").status_code == 200

    # 3. Finance can reply
    finance_reply = finance_client.post(
        f"/v1/admin/support/tickets/{ticket_id}/reply/",
        {"message": "Finance team looking into the invoice."},
        format="json",
    )
    assert finance_reply.status_code == 201

    # 4. Finance is forbidden from adding internal notes, assigning, and patching
    finance_note = finance_client.post(
        f"/v1/admin/support/tickets/{ticket_id}/notes/",
        {"note": "Finance note attempt"},
        format="json",
    )
    assert finance_note.status_code == 403

    finance_assign = finance_client.post(
        f"/v1/admin/support/tickets/{ticket_id}/assign/",
        {"adminId": "me"},
        format="json",
    )
    assert finance_assign.status_code == 403

    finance_patch = finance_client.patch(
        f"/v1/admin/support/tickets/{ticket_id}/",
        {"priority": "urgent"},
        format="json",
    )
    assert finance_patch.status_code == 403

    # 5. Trust & Safety can perform notes, assign, and patch
    assert (
        ts_client.post(
            f"/v1/admin/support/tickets/{ticket_id}/notes/",
            {"note": "T&S internal note"},
            format="json",
        ).status_code
        == 201
    )

    assign_resp = ts_client.post(
        f"/v1/admin/support/tickets/{ticket_id}/assign/",
        {"adminId": "me"},
        format="json",
    )
    assert assign_resp.status_code == 200
    assert assign_resp.json()["data"]["assigneeId"] == str(ts_client.user.id)

    patch_resp = ts_client.patch(
        f"/v1/admin/support/tickets/{ticket_id}/",
        {"priority": "urgent", "status": "closed"},
        format="json",
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["data"]["priority"] == "urgent"
    assert patch_resp.json()["data"]["status"] == "closed"


# ---------------------------------------------------------------------------
# 7. Audit Logging, Outbound Email & System Counts
# ---------------------------------------------------------------------------


def test_admin_reply_sends_email_and_records_audit(brand: APIClient) -> None:
    admin_client, admin = _make_admin_client(role=AdminRole.SUPER_ADMIN)
    brand.user.email = "brand_contact@example.com"
    brand.user.save()

    resp = brand.post(
        "/v1/support/tickets/",
        {"topic": "marketplace", "subject": "Listing question", "message": "Need help"},
        format="json",
    )
    ticket_id = resp.json()["data"]["id"]

    mail.outbox.clear()

    reply_resp = admin_client.post(
        f"/v1/admin/support/tickets/{ticket_id}/reply/",
        {"message": "Official resolution reply from admin"},
        format="json",
    )
    assert reply_resp.status_code == 201

    # Verify email was dispatched
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["brand_contact@example.com"]
    assert "Official resolution reply" in mail.outbox[0].body

    # Verify audit log entry
    audit = AuditLog.objects.filter(action="support.reply").first()
    assert audit is not None
    assert audit.actor == admin

    # Verify audit chain integrity
    ok, count, _ = verify_audit_chain()
    assert ok is True
    assert count >= 1


def test_admin_system_counts_includes_support_badge(brand: APIClient) -> None:
    admin_client, _ = _make_admin_client(role=AdminRole.SUPER_ADMIN)

    # Initially count open tickets
    resp = admin_client.get("/v1/admin/system/counts/")
    assert resp.status_code == 200
    initial_support = resp.json()["data"]["support"]

    # Create new ticket
    brand.post(
        "/v1/support/tickets/",
        {"topic": "other", "subject": "Count test", "message": "Counting support tickets"},
        format="json",
    )

    resp_after = admin_client.get("/v1/admin/system/counts/")
    assert resp_after.json()["data"]["support"] == initial_support + 1


# ---------------------------------------------------------------------------
# 8. Upload Access Security (`can_access`)
# ---------------------------------------------------------------------------


def test_support_attachment_can_access_rules(brand: APIClient, creator: APIClient) -> None:
    admin_client, admin = _make_admin_client(role=AdminRole.TRUST_SAFETY)

    # 1. Admin creates upload for ticket reply
    upload_admin = _make_upload(user=admin)

    # Ticket created by brand
    t_resp = brand.post(
        "/v1/support/tickets/",
        {"topic": "account", "subject": "Access check", "message": "Checking upload access"},
        format="json",
    )
    ticket_id = t_resp.json()["data"]["id"]

    # Admin attaches upload to public reply
    admin_client.post(
        f"/v1/admin/support/tickets/{ticket_id}/reply/",
        {"message": "Here is the attachment", "attachmentUploadIds": [str(upload_admin.id)]},
        format="json",
    )

    # Brand can access attachment
    assert can_access(brand.user, upload_admin) is True
    # Admin can access
    assert can_access(admin, upload_admin) is True
    # Creator (unrelated third party) CANNOT access
    assert can_access(creator.user, upload_admin) is False

    # 2. Admin creates upload and attaches to INTERNAL note
    upload_internal = _make_upload(user=admin)
    admin_client.post(
        f"/v1/admin/support/tickets/{ticket_id}/notes/",
        {"note": "Confidential attachment"},
        format="json",
    )
    # Manually link upload to the internal message
    msg = SupportMessage.objects.filter(ticket_id=ticket_id, internal=True).first()
    assert msg is not None
    msg.attachment_ids = [str(upload_internal.id)]
    msg.save()

    # Brand user CANNOT access upload on internal note
    assert can_access(brand.user, upload_internal) is False
    # Admin CAN access
    assert can_access(admin, upload_internal) is True


# ---------------------------------------------------------------------------
# 9. Abuse Reports Normalization & Duplicate Handling
# ---------------------------------------------------------------------------


def test_abuse_report_chat_and_profile_normalization(brand: APIClient, creator: APIClient) -> None:
    # 1. Report creator with targetType="profile"
    creator_id = creator.user.creator.id
    resp = brand.post(
        "/v1/abuse-reports/",
        {
            "targetType": "profile",
            "targetId": str(creator_id),
            "reason": "Inappropriate bio content",
        },
        format="json",
    )
    assert resp.status_code == 201
    assert resp.json()["data"]["targetType"] == "creator"

    # Duplicate report against same target returns 409 ALREADY_REPORTED
    dup_resp = brand.post(
        "/v1/abuse-reports/",
        {
            "targetType": "profile",
            "targetId": str(creator_id),
            "reason": "Duplicate report attempt",
        },
        format="json",
    )
    assert dup_resp.status_code == 409
    assert dup_resp.json()["error"]["code"] == "ALREADY_REPORTED"
