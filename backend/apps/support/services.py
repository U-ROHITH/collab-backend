import datetime
import logging
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.audit import record_audit
from apps.core.exceptions import BadRequest, Conflict, NotFound, TooManyRequests
from apps.identity.models import User
from apps.notifications.services import notify, notify_admins
from apps.orders.selectors import orders_visible_to
from apps.support.models import (
    MessageAuthorType,
    SupportMessage,
    SupportTicket,
    TicketPriority,
    TicketStatus,
)
from apps.uploads.models import Upload, UploadKind, UploadStatus

logger = logging.getLogger(__name__)


def send_support_email(to_email: str, subject: str, text: str) -> None:
    if not to_email:
        return
    try:
        from apps.identity.providers import _send_brevo, _send_resend

        if _send_resend.enabled() and _send_resend(to_email, subject, text):  # type: ignore[attr-defined]
            return
        if _send_brevo.enabled() and _send_brevo(to_email, subject, text):  # type: ignore[attr-defined]
            return
    except Exception as exc:  # noqa: BLE001
        logger.debug("HTTP email provider unavailable: %s", exc)

    try:
        from django.core import mail

        mail.send_mail(subject, text, settings.DEFAULT_FROM_EMAIL, [to_email], fail_silently=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to send support email to %s: %s", to_email, exc)


def validate_attachments(user: User, attachment_ids: list[UUID]) -> list[str]:
    if not attachment_ids:
        return []
    if len(attachment_ids) > 3:
        raise BadRequest("At most 3 attachments are allowed.", code="ATTACHMENT_INVALID")

    valid_ids: list[str] = []
    for upload_id in attachment_ids:
        upload = Upload.objects.filter(
            pk=upload_id,
            owner=user,
            kind=UploadKind.SUPPORT_ATTACHMENT,
            status=UploadStatus.READY,
        ).first()
        if not upload:
            raise BadRequest("Attachment not found or not finished uploading.", code="ATTACHMENT_INVALID")
        valid_ids.append(str(upload.id))
    return valid_ids


def create_ticket(
    *,
    user: User,
    topic: str,
    subject: str,
    message: str,
    order_id: UUID | None = None,
    attachment_upload_ids: list[UUID] | None = None,
) -> SupportTicket:
    # 1. Enforce rate limits
    open_count = SupportTicket.objects.filter(
        user=user,
        status__in=[TicketStatus.OPEN, TicketStatus.WAITING_ON_USER],
    ).count()
    if open_count >= 5:
        raise TooManyRequests("You have too many open support tickets.", code="TOO_MANY_OPEN")

    since_24h = timezone.now() - datetime.timedelta(hours=24)
    day_count = SupportTicket.objects.filter(user=user, created_at__gte=since_24h).count()
    if day_count >= 10:
        raise TooManyRequests("You have reached the ticket creation limit for today.", code="TOO_MANY_OPEN")

    # 2. Validate order ownership if order_id provided
    order = None
    if order_id:
        order = orders_visible_to(user).filter(pk=order_id).first()
        if not order:
            raise BadRequest("Order does not belong to you.", code="ORDER_NOT_YOURS")

    # 3. Validate attachments
    valid_attachments = validate_attachments(user, attachment_upload_ids or [])

    # 4. Atomic create with sequential numbering lock
    now = timezone.now()
    with transaction.atomic():
        last_ticket = SupportTicket.objects.select_for_update().order_by("-number").first()
        number = (last_ticket.number + 1) if last_ticket else 1042
        ticket = SupportTicket.objects.create(
            number=number,
            user=user,
            role=user.role or "",
            topic=topic,
            order=order,
            subject=subject,
            status=TicketStatus.OPEN,
            priority=TicketPriority.NORMAL,
            last_user_message_at=now,
            unread_for_user=0,
        )
        SupportMessage.objects.create(
            ticket=ticket,
            author_type=MessageAuthorType.USER,
            author_user=user,
            internal=False,
            text=message,
            attachment_ids=valid_attachments,
        )

    # 5. Notify admins
    notify_admins(
        type="support_new",
        title=f"New ticket #{ticket.formatted_number}: {ticket.subject}",
        body=message[:200],
        data={"ticketId": str(ticket.id), "ticketNumber": ticket.formatted_number},
    )

    return ticket


def add_user_message(
    *,
    user: User,
    ticket_id: UUID,
    message: str,
    attachment_upload_ids: list[UUID] | None = None,
) -> SupportMessage:
    ticket = SupportTicket.objects.filter(pk=ticket_id, user=user).first()
    if not ticket:
        raise NotFound("Ticket not found.")

    if ticket.status == TicketStatus.CLOSED:
        raise Conflict("This ticket is closed and cannot receive new messages.", code="TICKET_CLOSED")

    valid_attachments = validate_attachments(user, attachment_upload_ids or [])

    now = timezone.now()
    with transaction.atomic():
        msg = SupportMessage.objects.create(
            ticket=ticket,
            author_type=MessageAuthorType.USER,
            author_user=user,
            internal=False,
            text=message,
            attachment_ids=valid_attachments,
        )
        ticket.status = TicketStatus.OPEN
        ticket.last_user_message_at = now
        ticket.save(update_fields=["status", "last_user_message_at", "updated_at"])

    notify_admins(
        type="support_new",
        title=f"New message on ticket #{ticket.formatted_number}",
        body=message[:200],
        data={"ticketId": str(ticket.id), "ticketNumber": ticket.formatted_number},
    )

    return msg


def resolve_ticket(*, user: User, ticket_id: UUID) -> SupportTicket:
    ticket = SupportTicket.objects.filter(pk=ticket_id, user=user).first()
    if not ticket:
        raise NotFound("Ticket not found.")

    if ticket.status == TicketStatus.CLOSED:
        raise Conflict("This ticket is closed.", code="TICKET_CLOSED")

    if ticket.status == TicketStatus.RESOLVED:
        return ticket

    now = timezone.now()
    with transaction.atomic():
        ticket.status = TicketStatus.RESOLVED
        ticket.resolved_at = now
        ticket.save(update_fields=["status", "resolved_at", "updated_at"])
        SupportMessage.objects.create(
            ticket=ticket,
            author_type=MessageAuthorType.SYSTEM,
            internal=False,
            text="Marked resolved by user",
        )

    notify(
        user_id=ticket.user_id,
        type="support_resolved",
        title=f"Ticket #{ticket.formatted_number} marked resolved",
        body="",
        data={"ticketId": str(ticket.id), "ticketNumber": ticket.formatted_number},
    )

    return ticket


def reopen_ticket(*, user: User, ticket_id: UUID) -> SupportTicket:
    ticket = SupportTicket.objects.filter(pk=ticket_id, user=user).first()
    if not ticket:
        raise NotFound("Ticket not found.")

    if ticket.status == TicketStatus.CLOSED:
        raise Conflict("This ticket is closed and cannot be reopened.", code="TICKET_CLOSED")

    if ticket.status in (TicketStatus.OPEN, TicketStatus.WAITING_ON_USER):
        return ticket

    if ticket.status == TicketStatus.RESOLVED:
        now = timezone.now()
        if not ticket.resolved_at or (now - ticket.resolved_at) > datetime.timedelta(days=7):
            raise Conflict(
                "Tickets resolved more than 7 days ago cannot be reopened. Please open a new ticket.",
                code="REOPEN_EXPIRED",
            )
        with transaction.atomic():
            ticket.status = TicketStatus.OPEN
            ticket.resolved_at = None
            ticket.last_user_message_at = now
            ticket.save(update_fields=["status", "resolved_at", "last_user_message_at", "updated_at"])
            SupportMessage.objects.create(
                ticket=ticket,
                author_type=MessageAuthorType.SYSTEM,
                internal=False,
                text="Ticket reopened by user",
            )
        notify_admins(
            type="support_new",
            title=f"Ticket #{ticket.formatted_number} reopened",
            data={"ticketId": str(ticket.id), "ticketNumber": ticket.formatted_number},
        )

    return ticket


def admin_reply_ticket(
    *,
    admin_user: User,
    ticket_id: UUID,
    message: str,
    attachment_upload_ids: list[UUID] | None = None,
) -> SupportMessage:
    ticket = SupportTicket.objects.select_related("user").filter(pk=ticket_id).first()
    if not ticket:
        raise NotFound("Ticket not found.")

    valid_attachments = validate_attachments(admin_user, attachment_upload_ids or [])

    now = timezone.now()
    with transaction.atomic():
        msg = SupportMessage.objects.create(
            ticket=ticket,
            author_type=MessageAuthorType.ADMIN,
            author_user=admin_user,
            internal=False,
            text=message,
            attachment_ids=valid_attachments,
        )
        ticket.status = TicketStatus.WAITING_ON_USER
        ticket.last_admin_message_at = now
        ticket.unread_for_user = ticket.unread_for_user + 1
        ticket.save(update_fields=["status", "last_admin_message_at", "unread_for_user", "updated_at"])

    # 1. Notification
    notify(
        user_id=ticket.user_id,
        type="support_reply",
        title=f"Support replied to ticket #{ticket.formatted_number}",
        body=message[:200],
        data={"ticketId": str(ticket.id), "ticketNumber": ticket.formatted_number},
    )

    # 2. Email
    if ticket.user and ticket.user.email:
        send_support_email(
            ticket.user.email,
            f"[Collab Support] Update on ticket {ticket.formatted_number}: {ticket.subject}",
            message,
        )

    # 3. Audit
    record_audit(
        admin=admin_user,
        action="support.reply",
        target=ticket,
        detail={"ticket_id": str(ticket.id), "ticket_number": ticket.formatted_number},
    )

    return msg


def admin_add_note(*, admin_user: User, ticket_id: UUID, note: str) -> SupportMessage:
    ticket = SupportTicket.objects.filter(pk=ticket_id).first()
    if not ticket:
        raise NotFound("Ticket not found.")

    with transaction.atomic():
        msg = SupportMessage.objects.create(
            ticket=ticket,
            author_type=MessageAuthorType.ADMIN,
            author_user=admin_user,
            internal=True,
            text=note,
        )

    record_audit(
        admin=admin_user,
        action="support.note",
        target=ticket,
        detail={"ticket_id": str(ticket.id), "ticket_number": ticket.formatted_number},
    )

    return msg


def admin_assign_ticket(*, admin_user: User, ticket_id: UUID, assignee_raw: Any) -> SupportTicket:
    ticket = SupportTicket.objects.filter(pk=ticket_id).first()
    if not ticket:
        raise NotFound("Ticket not found.")

    assignee: User | None = None
    if assignee_raw == "me":
        assignee = admin_user
    elif not assignee_raw or assignee_raw in ("null", "none"):
        assignee = None
    else:
        assignee = User.objects.filter(pk=assignee_raw, is_staff=True).first()
        if not assignee:
            raise BadRequest("Assigned admin not found.", code="ADMIN_NOT_FOUND")

    ticket.assignee = assignee
    ticket.save(update_fields=["assignee", "updated_at"])

    record_audit(
        admin=admin_user,
        action="support.assign",
        target=ticket,
        detail={
            "ticket_id": str(ticket.id),
            "ticket_number": ticket.formatted_number,
            "assignee_id": str(assignee.id) if assignee else None,
        },
    )

    return ticket


def admin_patch_ticket(
    *,
    admin_user: User,
    ticket_id: UUID,
    priority: str | None = None,
    status: str | None = None,
) -> SupportTicket:
    ticket = SupportTicket.objects.filter(pk=ticket_id).first()
    if not ticket:
        raise NotFound("Ticket not found.")

    update_fields = ["updated_at"]

    if priority and priority != ticket.priority:
        ticket.priority = priority
        update_fields.append("priority")
        record_audit(
            admin=admin_user,
            action="support.priority",
            target=ticket,
            detail={"ticket_id": str(ticket.id), "priority": priority},
        )

    if status and status != ticket.status:
        now = timezone.now()
        if status == TicketStatus.RESOLVED:
            ticket.resolved_at = now
            update_fields.append("resolved_at")
        elif status == TicketStatus.CLOSED:
            ticket.closed_at = now
            update_fields.append("closed_at")
        ticket.status = status
        update_fields.append("status")
        record_audit(
            admin=admin_user,
            action="support.status",
            target=ticket,
            detail={"ticket_id": str(ticket.id), "status": status},
        )

    if len(update_fields) > 1:
        ticket.save(update_fields=update_fields)

    return ticket
