"""The ONLY place an order's status may change. See docs/02-system-design.md §3.4.

Callers must hold a row lock: Order.objects.select_for_update() inside transaction.atomic().
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from apps.core.exceptions import Conflict
from apps.orders.models import Order
from apps.orders.models import OrderStatus as S

BRAND, CREATOR, ADMIN, SYSTEM = "brand", "creator", "admin", "system"


class InvalidTransition(Conflict):
    code = "ORDER_INVALID_TRANSITION"
    message = "This action isn't allowed for the order in its current state."


Guard = Callable[[Order, str], str | None]  # returns an error message, or None if allowed


def _revisions_left(order: Order, _role: str) -> str | None:
    if order.revision_count >= order.max_revisions:
        return "No revisions left. Approve the work or open a dispute."
    return None


def _funded_cancel_rule(order: Order, role: str) -> str | None:
    # Creator may back out before submitting. Brand may cancel only once the deadline is missed.
    if role == BRAND and (order.deadline is None or order.deadline > timezone.now()):
        return "You can cancel a funded order only after the creator misses the deadline. Otherwise open a dispute."
    return None


@dataclass(frozen=True)
class Rule:
    to: str
    roles: frozenset[str]
    guard: Guard | None = None


def _r(to: str, *roles: str, guard: Guard | None = None) -> Rule:
    return Rule(to, frozenset(roles), guard)


TRANSITIONS: dict[tuple[str, str], Rule] = {
    (S.AWAITING_PAYMENT, "fund"): _r(S.FUNDED, SYSTEM),
    (S.AWAITING_PAYMENT, "cancel"): _r(S.CANCELLED, BRAND, CREATOR),
    (S.AWAITING_PAYMENT, "expire_payment"): _r(S.CANCELLED, SYSTEM),
    (S.FUNDED, "submit_draft"): _r(S.DRAFT_SUBMITTED, CREATOR),
    (S.FUNDED, "cancel"): _r(S.REFUND_PENDING, BRAND, CREATOR, guard=_funded_cancel_rule),
    (S.FUNDED, "open_dispute"): _r(S.DISPUTED, BRAND, CREATOR),
    (S.DRAFT_SUBMITTED, "request_revision"): _r(S.REVISION_REQUESTED, BRAND, guard=_revisions_left),
    (S.DRAFT_SUBMITTED, "approve"): _r(S.APPROVED, BRAND),
    (S.DRAFT_SUBMITTED, "auto_approve"): _r(S.APPROVED, SYSTEM),
    (S.DRAFT_SUBMITTED, "open_dispute"): _r(S.DISPUTED, BRAND, CREATOR),
    (S.REVISION_REQUESTED, "submit_draft"): _r(S.DRAFT_SUBMITTED, CREATOR),
    (S.REVISION_REQUESTED, "open_dispute"): _r(S.DISPUTED, BRAND, CREATOR),
    (S.APPROVED, "complete"): _r(S.COMPLETED, SYSTEM),
    (S.REFUND_PENDING, "refund_completed"): _r(S.REFUNDED, SYSTEM),
    (S.DISPUTED, "resolve_for_creator"): _r(S.APPROVED, ADMIN),
    (S.DISPUTED, "resolve_for_brand"): _r(S.REFUND_PENDING, ADMIN),
}


def allowed_events(order: Order, role: str) -> list[str]:
    return sorted(
        event
        for (frm, event), rule in TRANSITIONS.items()
        if frm == order.status and role in rule.roles and not (rule.guard and rule.guard(order, role))
    )


def transition(order: Order, event: str, role: str) -> tuple[str, str]:
    """Validate and apply a transition in memory. Returns (from_status, to_status). Caller saves."""
    rule = TRANSITIONS.get((order.status, event))
    if rule is None or role not in rule.roles:
        raise InvalidTransition()
    if rule.guard and (error := rule.guard(order, role)):
        raise InvalidTransition(error)

    now = timezone.now()
    from_status = order.status
    order.status = rule.to
    order.version += 1
    if rule.to == S.FUNDED:
        order.funded_at = now
        if order.deadline is None:
            order.deadline = now + timedelta(days=order.delivery_days)
    elif rule.to == S.DRAFT_SUBMITTED:
        order.last_submitted_at = now
    elif rule.to == S.REVISION_REQUESTED:
        order.revision_count += 1
    elif rule.to == S.APPROVED:
        order.approved_at = now
    elif rule.to == S.COMPLETED:
        order.completed_at = now
    elif rule.to in (S.CANCELLED, S.REFUND_PENDING):
        order.cancelled_at = now
    return from_status, rule.to
