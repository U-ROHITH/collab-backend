from datetime import timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from apps.brands.models import Brand
from apps.core.exceptions import Conflict, DomainError, Forbidden, NotFound
from apps.creators.models import Creator
from apps.identity.models import Role, User
from apps.money.fees import FeeConfig, compute_order_amounts
from apps.notifications.services import notify
from apps.orders import state_machine
from apps.orders.models import (
    Deliverable,
    Dispute,
    DisputeKind,
    DisputeStatus,
    Order,
    OrderEvent,
    OrderStatus,
    Review,
)
from apps.orders.selectors import orders_visible_to


def create_order(
    *,
    actor: User,
    brand: Brand,
    creator: Creator,
    source_type: str,
    source_id: UUID,
    title: str,
    deliverables: str,
    brief: str,
    subtotal_paise: int,
    delivery_days: int,
    usage_rights: dict[str, Any],
    deadline: Any = None,
) -> Order:
    """Called by bids/offers/cart inside their transaction. Amounts are computed here, server-side."""
    from apps.kyc.services import require_order_parties_verified

    require_order_parties_verified(actor=actor, brand_owner=brand.owner, creator_user=creator.user)
    cfg = FeeConfig.from_dict(settings.FEE_CONFIG)
    amounts = compute_order_amounts(subtotal_paise, cfg)
    try:
        with transaction.atomic():
            order = Order.objects.create(
                brand=brand,
                creator=creator,
                source_type=source_type,
                source_id=source_id,
                title=title,
                deliverables=deliverables,
                brief=brief,
                usage_rights=usage_rights,
                delivery_days=delivery_days,
                deadline=deadline,
                max_revisions=settings.ORDER_POLICY["max_revisions"],
                fee_config_snapshot=cfg.to_dict(),
                subtotal_paise=amounts.subtotal,
                platform_fee_paise=amounts.platform_fee,
                gst_paise=amounts.gst,
                tds_paise=amounts.tds,
                creator_payout_paise=amounts.creator_payout,
                brand_total_paise=amounts.brand_total,
            )
    except IntegrityError:
        raise Conflict("An order already exists for this.", code="ORDER_ALREADY_EXISTS") from None
    OrderEvent.objects.create(
        order=order, event="create", from_status="", to_status=order.status, actor=actor, actor_role=actor.role or ""
    )
    for user_id in _parties(order):
        if user_id != actor.pk:
            notify(user_id=user_id, type="order_created", title=f"New order: {title}", data={"orderId": str(order.pk)})
    return order


def _parties(order: Order) -> list[Any]:
    return [uid for uid in (order.brand.owner_id, order.creator.user_id) if uid]


def _apply(
    order_id: UUID, *, actor: User | None, role: str, event: str, reason: str = "", visible_to: User | None = None
) -> Order:
    """Lock → transition → audit event. Runs inside the caller's atomic block."""
    qs = orders_visible_to(visible_to) if visible_to else Order.objects.all()
    order = qs.select_for_update(of=("self",)).filter(pk=order_id).first()
    if order is None:
        raise NotFound()
    frm, to = state_machine.transition(order, event, role)
    order.save()
    OrderEvent.objects.create(
        order=order, event=event, from_status=frm, to_status=to, actor=actor, actor_role=role, reason=reason
    )
    if event == "cancel":
        order.cancel_reason = reason  # the annotation was read before this event existed
    return order


def _notify_other(order: Order, actor: User, type_: str, title: str) -> None:
    for user_id in _parties(order):
        if user_id != actor.pk:
            notify(user_id=user_id, type=type_, title=title, data={"orderId": str(order.pk)})


def submit_deliverable(
    *, actor: User, order_id: UUID, content_url: str = "", file_upload_id: UUID | None = None, note: str
) -> Deliverable:
    from apps.uploads.models import UploadKind
    from apps.uploads.services import claim_ready_upload

    with transaction.atomic():
        order = _apply(order_id, actor=actor, role=Role.CREATOR, event="submit_draft", visible_to=actor)
        upload = None
        if file_upload_id:
            upload = claim_ready_upload(user=actor, upload_id=file_upload_id, kind=UploadKind.DELIVERABLE)
            if Deliverable.objects.filter(upload=upload).exists():
                raise Conflict("This file is already attached to a draft.", code="ATTACHMENT_IN_USE")
        next_version = (order.deliverable_versions.aggregate(v=Max("version"))["v"] or 0) + 1
        deliverable = Deliverable.objects.create(
            order=order, version=next_version, content_url=content_url, upload=upload, note=note
        )
        _notify_other(order, actor, "deliverable_submitted", f"Draft {next_version} submitted for {order.title}")
    return deliverable


def request_revision(*, actor: User, order_id: UUID, feedback: str) -> Order:
    with transaction.atomic():
        order = _apply(
            order_id, actor=actor, role=Role.BRAND, event="request_revision", reason=feedback, visible_to=actor
        )
        latest = order.deliverable_versions.order_by("-version").first()
        if latest:
            latest.feedback = feedback
            latest.save(update_fields=["feedback", "updated_at"])
        _notify_other(order, actor, "revision_requested", f"Revision requested on {order.title}")
    return order


def approve(*, actor: User, order_id: UUID) -> Order:
    with transaction.atomic():
        order = _apply(order_id, actor=actor, role=Role.BRAND, event="approve", visible_to=actor)
        _notify_other(order, actor, "order_approved", f"Your work on {order.title} was approved")
    # Phase 2: an outbox event triggers the Razorpay Route transfer release, then `complete`.
    return order


def cancel(*, actor: User, order_id: UUID, reason: str) -> Order:
    with transaction.atomic():
        order = _apply(order_id, actor=actor, role=actor.role or "", event="cancel", reason=reason, visible_to=actor)
        _notify_other(order, actor, "order_cancelled", f"Order cancelled: {order.title}")
    return order


def open_dispute(
    *,
    actor: User,
    order_id: UUID,
    reason: str,
    kind: str = DisputeKind.DISPUTE,
    evidence_upload_ids: list[UUID] | None = None,
) -> Dispute:
    with transaction.atomic():
        qs = orders_visible_to(actor)
        order = qs.select_for_update(of=("self",)).filter(pk=order_id).first()
        if order is None:
            raise NotFound()
        order.status_before_dispute = order.status
        order.save(update_fields=["status_before_dispute"])
        order = _apply(
            order_id, actor=actor, role=actor.role or "", event="open_dispute", reason=reason, visible_to=actor
        )
        sla_due_at = timezone.now() + timedelta(hours=48)
        upload_ids = [str(u) for u in (evidence_upload_ids or [])]
        dispute = Dispute.objects.create(
            order=order,
            opened_by=actor,
            reason=reason,
            kind=kind,
            evidence_upload_ids=upload_ids,
            sla_due_at=sla_due_at,
        )
        _notify_other(order, actor, "dispute_opened", f"A dispute was opened on {order.title}")
        from apps.notifications.services import notify_admins

        notify_admins(
            type="dispute_opened",
            title=f"Dispute Opened on Order: {order.title}",
            body=f"{actor.email or actor.phone} opened a {kind} on '{order.title}': {reason[:100]}",
            data={"orderId": str(order.id), "disputeId": str(dispute.id), "kind": kind},
        )
    return dispute


def respond_to_dispute(
    *,
    actor: User,
    order_id: UUID,
    dispute_id: UUID,
    statement: str,
    evidence_upload_ids: list[UUID] | None = None,
) -> Dispute:
    with transaction.atomic():
        dispute = Dispute.objects.select_for_update().filter(pk=dispute_id, order_id=order_id).first()
        if dispute is None:
            raise NotFound("Dispute not found.")
        if dispute.status != DisputeStatus.OPEN:
            raise Conflict("Dispute is already resolved.", code="DISPUTE_NOT_OPEN")
        # Check actor is the other party (counterparty)
        order = dispute.order
        parties = _parties(order)
        if actor.pk not in parties or actor.pk == dispute.opened_by_id:
            raise Forbidden("Only the counterparty can respond to this dispute.", code="NOT_COUNTERPARTY")
        if dispute.counterparty_statement:
            raise Conflict("A statement has already been submitted.", code="STATEMENT_ALREADY_SUBMITTED")

        dispute.counterparty_statement = statement
        dispute.counterparty_statement_at = timezone.now()
        dispute.counterparty_upload_ids = [str(u) for u in (evidence_upload_ids or [])]
        dispute.save(
            update_fields=["counterparty_statement", "counterparty_statement_at", "counterparty_upload_ids"]
        )

        # Notify dispute opener
        notify(
            user_id=dispute.opened_by_id,
            type="dispute_response",
            title=f"Counterparty Responded to Dispute: {order.title}",
            body=statement[:150],
            data={"orderId": str(order.id), "disputeId": str(dispute.id)},
        )
        # Notify admins
        from apps.notifications.services import notify_admins

        notify_admins(
            type="dispute_response",
            title=f"Counterparty Responded: {order.title}",
            body=statement[:150],
            data={"orderId": str(order.id), "disputeId": str(dispute.id)},
        )
    return dispute


def system_event(*, order_id: UUID, event: str, reason: str = "") -> Order:
    """For payments webhooks / cron jobs (fund, auto_approve, complete, expire_payment, refund_completed)."""
    with transaction.atomic():
        order = _apply(order_id, actor=None, role=state_machine.SYSTEM, event=event, reason=reason)
        if event == "fund":
            for uid in _parties(order):
                notify(
                    user_id=uid,
                    type="order_funded",
                    title=f"Payment received for {order.title}",
                    data={"orderId": str(order.pk)},
                )
    return order


def create_review(*, actor: User, order_id: UUID, stars: int, text: str) -> Review:
    from apps.creators.services import apply_review

    with transaction.atomic():
        order = orders_visible_to(actor).select_for_update(of=("self",)).filter(pk=order_id).first()
        if order is None or actor.role != Role.BRAND:
            raise NotFound()
        # Approval is when the brand accepts the work; "completed" needs a payout job that isn't built yet.
        if order.status not in (OrderStatus.APPROVED, OrderStatus.COMPLETED):
            raise DomainError("You can review once you've approved the work.", code="ORDER_NOT_COMPLETED")
        if Review.objects.filter(order=order).exists():
            raise Conflict("You already reviewed this order.", code="REVIEW_EXISTS")
        from apps.orders.models import ReviewStatus

        is_completed = order.status == OrderStatus.COMPLETED
        review_status = ReviewStatus.VISIBLE if is_completed else ReviewStatus.FLAGGED
        flag_reason = "" if is_completed else "No completed order between parties"

        review = Review.objects.create(
            order=order,
            brand=order.brand,
            creator=order.creator,
            stars=stars,
            text=text,
            status=review_status,
            flag_reason=flag_reason,
        )
        if review_status == ReviewStatus.VISIBLE:
            apply_review(creator_id=order.creator_id, stars=stars)
    return review
