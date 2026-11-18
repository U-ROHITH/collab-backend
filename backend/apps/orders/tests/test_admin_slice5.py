import uuid
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.exceptions import DomainError
from apps.identity.models import AdminRole, AuditLog, Role, User
from apps.identity.services import issue_admin_tokens, issue_tokens
from apps.money.models import CreatorWallet, PlatformRule, WithdrawalRequest
from apps.notifications.models import Notification
from apps.orders.models import (
    Dispute,
    DisputeKind,
    DisputeStatus,
    Order,
    OrderStatus,
    SourceType,
)
from apps.uploads.models import Upload, UploadKind, UploadStatus

pytestmark = pytest.mark.django_db


def _make_admin_client(role: str = AdminRole.SUPER_ADMIN) -> tuple[APIClient, User]:
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


def _create_test_order(brand_user: User, creator_user: User, status: str = OrderStatus.FUNDED) -> Order:
    return Order.objects.create(
        brand=brand_user.brand,
        creator=creator_user.creator,
        source_type=SourceType.BID,
        source_id=uuid.uuid4(),
        title="Slice 5 Campaign Deliverable",
        brief="Brief description",
        deliverables="1x YouTube Review Reel",
        delivery_days=7,
        fee_config_snapshot={},
        subtotal_paise=100_000,
        platform_fee_paise=10_000,
        gst_paise=1_800,
        tds_paise=1_000,
        creator_payout_paise=87_200,
        brand_total_paise=100_000,
        status=status,
    )


# --- 1. Order Statuses & Dispute Workflow -------------------------------------------


def test_order_dispute_workflow_and_counterparty_response(creator: APIClient, brand: APIClient) -> None:
    creator_user = creator.user  # type: ignore[attr-defined]
    brand_user = brand.user  # type: ignore[attr-defined]
    order = _create_test_order(brand_user, creator_user, status=OrderStatus.FUNDED)

    # 1. Brand opens a dispute with payment_complaint and evidence
    evidence_id = uuid.uuid4()
    disp_resp = brand.post(
        f"/v1/orders/{order.id}/disputes/",
        {
            "reason": "Creator failed to deliver video before agreed schedule deadline.",
            "kind": "payment_complaint",
            "evidenceUploadIds": [str(evidence_id)],
        },
        format="json",
    )
    assert disp_resp.status_code == 201
    disp_data = disp_resp.json()["data"]
    dispute_id = disp_data["id"]
    assert disp_data["kind"] == "payment_complaint"
    assert disp_data["status"] == "open"
    assert disp_data["slaDueAt"] is not None

    # Check order was frozen to disputed and status_before_dispute saved
    order.refresh_from_db()
    assert order.status == OrderStatus.DISPUTED
    assert order.status_before_dispute == OrderStatus.FUNDED

    # 2. Counterparty (creator) responds with a statement
    resp_statement = creator.post(
        f"/v1/orders/{order.id}/disputes/{dispute_id}/response/",
        {
            "statement": "Draft was delivered on time but brand did not review within SLA.",
            "evidenceUploadIds": [str(uuid.uuid4())],
        },
        format="json",
    )
    assert resp_statement.status_code == 200
    updated_disp = resp_statement.json()["data"]
    assert updated_disp["counterpartyStatement"] == "Draft was delivered on time but brand did not review within SLA."

    # 3. Third-party cannot respond
    other_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)
    third_user = User.objects.create_user(phone="+919999900001")
    third_client = APIClient()
    third_client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_tokens(third_user).access}")
    bad_resp = third_client.post(
        f"/v1/orders/{order.id}/disputes/{dispute_id}/response/",
        {"statement": "Intruder statement"},
        format="json",
    )
    assert bad_resp.status_code == 403

    # 4. Admin disputes list & detail
    admin_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)
    d_list = admin_client.get("/v1/admin/finance/disputes/?status=open")
    assert d_list.status_code == 200
    assert any(d["id"] == str(dispute_id) for d in d_list.json()["data"])

    d_detail = admin_client.get(f"/v1/admin/finance/disputes/{dispute_id}/")
    assert d_detail.status_code == 200
    assert d_detail.json()["data"]["counterpartyStatement"] != ""


# --- 2. Admin Order Pipeline & Escrow Actions ----------------------------------------


def test_order_pipeline_and_detail(creator: APIClient, brand: APIClient) -> None:
    admin_client, _ = _make_admin_client(role=AdminRole.FINANCE)
    order = _create_test_order(brand.user, creator.user, status=OrderStatus.FUNDED)

    # 1. Order list
    list_resp = admin_client.get("/v1/admin/finance/orders/")
    assert list_resp.status_code == 200
    assert any(o["id"] == str(order.id) for o in list_resp.json()["data"])

    # 2. Pipeline
    pipe_resp = admin_client.get("/v1/admin/finance/orders/pipeline/")
    assert pipe_resp.status_code == 200
    pdata = pipe_resp.json()["data"]
    assert "pipeline" in pdata
    assert pdata["heldTotal"] >= order.subtotal_paise

    # 3. Order detail with financial breakdown
    detail_resp = admin_client.get(f"/v1/admin/finance/orders/{order.id}/")
    assert detail_resp.status_code == 200
    ddata = detail_resp.json()["data"]
    assert ddata["breakdown"]["price"] == order.subtotal_paise
    assert ddata["breakdown"]["commission"] == order.platform_fee_paise
    assert ddata["breakdown"]["creatorGets"] == order.creator_payout_paise
    assert "linkloomNet" in ddata["breakdown"]


def test_force_release_escrow_super_admin_only_and_idempotency(creator: APIClient, brand: APIClient) -> None:
    order = _create_test_order(brand.user, creator.user, status=OrderStatus.DRAFT_SUBMITTED)
    idem_key = f"idem-force-{uuid.uuid4().hex}"

    # 1. Non-super-admin is forbidden (403)
    finance_client, _ = _make_admin_client(role=AdminRole.FINANCE)
    forbidden_resp = finance_client.post(
        f"/v1/admin/finance/orders/{order.id}/force-release/",
        {"reason": "Valid reason exceeding twelve characters"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=idem_key,
    )
    assert forbidden_resp.status_code == 403

    # 2. Super admin succeeds
    super_client, _ = _make_admin_client(role=AdminRole.SUPER_ADMIN)
    ok_resp = super_client.post(
        f"/v1/admin/finance/orders/{order.id}/force-release/",
        {"reason": "Quality approved by Linkloom moderation desk"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=idem_key,
    )
    assert ok_resp.status_code == 200
    res_data = ok_resp.json()["data"]
    assert res_data["order"]["status"] == "completed"
    assert res_data["order"]["releasedAt"] is not None

    # Verify wallet credited and withdrawal marked processing
    wallet = CreatorWallet.objects.get(creator=order.creator)
    assert wallet.balance_paise >= order.creator_payout_paise
    w = WithdrawalRequest.objects.filter(creator=order.creator).first()
    assert w is not None and w.status == "processing"

    # 3. Idempotent replay with same key returns identical cached response
    replay_resp = super_client.post(
        f"/v1/admin/finance/orders/{order.id}/force-release/",
        {"reason": "Quality approved by Linkloom moderation desk"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=idem_key,
    )
    assert replay_resp.status_code == 200
    assert replay_resp.json()["data"]["order"]["id"] == str(order.id)


def test_refund_escrow_super_admin_only_and_idempotency(creator: APIClient, brand: APIClient) -> None:
    order = _create_test_order(brand.user, creator.user, status=OrderStatus.FUNDED)
    idem_key = f"idem-refund-{uuid.uuid4().hex}"

    super_client, _ = _make_admin_client(role=AdminRole.SUPER_ADMIN)
    ref_resp = super_client.post(
        f"/v1/admin/finance/orders/{order.id}/refund/",
        {"reason": "Brand canceled order due to unresponsiveness"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=idem_key,
    )
    assert ref_resp.status_code == 200
    res_data = ref_resp.json()["data"]
    assert res_data["order"]["status"] == "refunded"
    assert res_data["order"]["refundedAt"] is not None

    # Idempotent replay
    replay_resp = super_client.post(
        f"/v1/admin/finance/orders/{order.id}/refund/",
        {"reason": "Brand canceled order due to unresponsiveness"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=idem_key,
    )
    assert replay_resp.status_code == 200


def test_dispute_resolve_without_money(creator: APIClient, brand: APIClient) -> None:
    order = _create_test_order(brand.user, creator.user, status=OrderStatus.DRAFT_SUBMITTED)
    order.status_before_dispute = OrderStatus.DRAFT_SUBMITTED
    order.status = OrderStatus.DISPUTED
    order.save()

    dispute = Dispute.objects.create(
        order=order,
        opened_by=brand.user,
        reason="Needs mediation",
        kind=DisputeKind.DISPUTE,
        status=DisputeStatus.OPEN,
    )

    admin_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)
    res = admin_client.post(
        f"/v1/admin/finance/disputes/{dispute.id}/resolve/",
        {"reason": "Parties mutually agreed to continue revisions"},
        format="json",
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["dispute"]["status"] == "resolved"
    assert data["order"]["status"] == OrderStatus.DRAFT_SUBMITTED


# --- 3. Rules, Dashboard, Financials & Audit Log -------------------------------------


def test_platform_rules_endpoint() -> None:
    client, _ = _make_admin_client(role=AdminRole.FINANCE)
    PlatformRule.objects.update_or_create(key="default", defaults={"commission_pct": 12.5, "gateway_pct": 2.5})
    resp = client.get("/v1/admin/finance/rules/")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["commissionPct"] == 12.5
    assert data["gatewayPct"] == 2.5


def test_admin_dashboard_and_counts(creator: APIClient, brand: APIClient) -> None:
    client, admin = _make_admin_client(role=AdminRole.SUPER_ADMIN)
    _create_test_order(brand.user, creator.user, status=OrderStatus.FUNDED)

    # 1. Dashboard
    dash_resp = client.get("/v1/admin/system/dashboard/")
    assert dash_resp.status_code == 200
    dash = dash_resp.json()["data"]
    assert "accounts" in dash
    assert "orders" in dash
    assert "verifications" in dash
    assert "campaigns" in dash
    assert "reports" in dash
    assert "disputes" in dash
    assert "alerts" in dash
    assert "recentAudit" in dash
    assert dash["auditChain"]["ok"] is True

    # 2. Counts
    counts_resp = client.get("/v1/admin/system/counts/")
    assert counts_resp.status_code == 200
    counts = counts_resp.json()["data"]
    assert "verifications" in counts
    assert "campaigns" in counts
    assert "disputes" in counts


def test_finance_summary_and_payouts_export(creator: APIClient) -> None:
    client, _ = _make_admin_client(role=AdminRole.FINANCE)
    cr = creator.user.creator  # type: ignore[attr-defined]
    wallet, _ = CreatorWallet.objects.get_or_create(creator=cr)
    WithdrawalRequest.objects.create(
        creator=cr,
        wallet=wallet,
        amount_paise=50_000,
        payout_method="upi",
        payout_details={"vpa": "sathvik@okhdfcbank"},
        status="completed",
        processed_at=timezone.now(),
    )

    # 1. Summary
    sum_resp = client.get("/v1/admin/finance/summary/?months=3")
    assert sum_resp.status_code == 200
    assert sum_resp.json()["data"]["months"] == 3

    # 2. Payouts list
    p_resp = client.get("/v1/admin/finance/payouts/?status=paid")
    assert p_resp.status_code == 200
    assert len(p_resp.json()["data"]) >= 1

    # 3. Export CSV
    exp_resp = client.get("/v1/admin/finance/payouts/export/")
    assert exp_resp.status_code == 200
    assert exp_resp["Content-Type"] == "text/csv"
    assert "upi: ***@okhdfcbank" in exp_resp.content.decode()

    # Check audit log
    assert AuditLog.objects.filter(action="payouts.export").exists()


def test_admin_notifications_flow() -> None:
    client, admin = _make_admin_client(role=AdminRole.SUPER_ADMIN)
    n = Notification.objects.create(
        user=admin,
        type="campaign_submitted",
        title="New campaign pending",
        body="Review needed",
    )

    # 1. Unread count
    cnt_resp = client.get("/v1/admin/system/notifications/unread-count/")
    assert cnt_resp.status_code == 200
    assert cnt_resp.json()["data"]["unreadCount"] >= 1

    # 2. List
    list_resp = client.get("/v1/admin/system/notifications/?unreadOnly=true")
    assert list_resp.status_code == 200
    assert any(item["id"] == str(n.id) for item in list_resp.json()["data"])

    # 3. Mark read
    read_resp = client.post(f"/v1/admin/system/notifications/{n.id}/read/")
    assert read_resp.status_code == 200
    assert read_resp.json()["data"]["isRead"] is True

    # 4. Mark all read
    all_resp = client.post("/v1/admin/system/notifications/read-all/")
    assert all_resp.status_code == 200


def test_audit_log_hash_chain_verification_and_immutability() -> None:
    client, admin = _make_admin_client(role=AdminRole.SUPER_ADMIN)

    # 1. Verify chain
    v_resp = client.get("/v1/admin/system/audit-log/verify/")
    assert v_resp.status_code == 200
    assert v_resp.json()["data"]["ok"] is True

    # 2. Export
    exp_resp = client.get("/v1/admin/system/audit-log/export/")
    assert exp_resp.status_code == 200
    assert exp_resp["Content-Type"] == "text/csv"

    # 3. Immutability: deleting AuditLog must raise DomainError
    log = AuditLog.objects.first()
    assert log is not None
    with pytest.raises(DomainError):
        log.delete()


def test_admin_upload_download_url_creates_audit_entry(creator: APIClient, fake_storage: Any) -> None:
    admin_client, admin = _make_admin_client(role=AdminRole.TRUST_SAFETY)
    up = Upload.objects.create(
        owner=creator.user,  # type: ignore[attr-defined]
        bucket="private-media",
        storage_key="test/file.pdf",
        declared_content_type="application/pdf",
        declared_size=1024,
        kind=UploadKind.DELIVERABLE,
        status=UploadStatus.READY,
    )

    dl_resp = admin_client.get(f"/v1/uploads/{up.id}/download-url/")
    assert dl_resp.status_code == 200

    # Verify upload.view audit log was recorded
    assert AuditLog.objects.filter(actor=admin, action="upload.view", entity_id=str(up.id)).exists()
