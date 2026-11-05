from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.orders import state_machine as sm
from apps.orders.models import OrderStatus as S

ROLES = (sm.BRAND, sm.CREATOR, sm.ADMIN, sm.SYSTEM)
EVENTS = sorted({event for (_, event) in sm.TRANSITIONS})


def _order(status: str, **kw: object) -> SimpleNamespace:
    defaults = dict(
        status=status,
        version=1,
        revision_count=0,
        max_revisions=2,
        delivery_days=5,
        deadline=timezone.now() - timedelta(days=1),
        funded_at=None,
        last_submitted_at=None,
        approved_at=None,
        completed_at=None,
        cancelled_at=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


@pytest.mark.parametrize("status", S.values)
@pytest.mark.parametrize("event", EVENTS)
@pytest.mark.parametrize("role", ROLES)
def test_every_status_event_role_combination(status: str, event: str, role: str) -> None:
    """Exhaustive: only combinations in the table succeed; everything else raises."""
    rule = sm.TRANSITIONS.get((status, event))
    order = _order(status)
    if rule and role in rule.roles:
        frm, to = sm.transition(order, event, role)  # type: ignore[arg-type]
        assert (frm, to) == (status, rule.to) and order.version == 2
    else:
        with pytest.raises(sm.InvalidTransition):
            sm.transition(order, event, role)  # type: ignore[arg-type]


def test_revision_limit() -> None:
    order = _order(S.DRAFT_SUBMITTED, revision_count=2)
    with pytest.raises(sm.InvalidTransition):
        sm.transition(order, "request_revision", sm.BRAND)  # type: ignore[arg-type]


def test_brand_cannot_cancel_funded_before_deadline() -> None:
    order = _order(S.FUNDED, deadline=timezone.now() + timedelta(days=2))
    with pytest.raises(sm.InvalidTransition):
        sm.transition(order, "cancel", sm.BRAND)  # type: ignore[arg-type]
    sm.transition(order, "cancel", sm.CREATOR)  # type: ignore[arg-type]
    assert order.status == S.REFUND_PENDING


def test_funding_sets_deadline_from_delivery_days() -> None:
    order = _order(S.AWAITING_PAYMENT, deadline=None, delivery_days=5)
    sm.transition(order, "fund", sm.SYSTEM)  # type: ignore[arg-type]
    assert order.funded_at and order.deadline - order.funded_at == timedelta(days=5)


def test_status_assigned_only_in_state_machine() -> None:
    """Guardrail: no other module may write `.status =` on an order."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    offenders = [
        str(p)
        for p in root.rglob("*.py")
        if "tests" not in p.parts
        and p.name != "state_machine.py"
        and re.search(r"\border\.status\s*=[^=]", p.read_text())
    ]
    assert offenders == []
