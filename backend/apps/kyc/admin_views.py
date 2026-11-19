import logging
from collections.abc import Callable
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import record_audit
from apps.core.exceptions import Conflict, NotFound
from apps.identity.models import AdminRole
from apps.identity.permissions import RequireAdminRoles
from apps.kyc.admin_serializers import (
    AdminVerificationActionResult,
    AdminVerificationDetailOutput,
    AdminVerificationListOutput,
    AdminVerificationRejectInput,
    AdminVerificationRequestInfoInput,
)
from apps.kyc.brand_kyc import mark_reviewed
from apps.kyc.models import (
    KycStatus,
    VerificationKind,
    VerificationRequest,
    VerificationStatus,
)
from apps.kyc.services import approval_side_effects
from apps.notifications.services import notify

logger = logging.getLogger(__name__)


def _send_verification_email(to_email: str, subject: str, text: str) -> None:
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
        from django.conf import settings
        from django.core import mail

        mail.send_mail(subject, text, settings.DEFAULT_FROM_EMAIL, [to_email], fail_silently=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to send verification email to %s: %s", to_email, exc)


def _send_verification_sms(phone: str, text: str) -> None:
    if not phone:
        return
    try:
        from apps.identity.providers import _console_mode

        if _console_mode():
            logger.warning("[DEV SMS] to %s: %s", phone, text)
            return
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to send verification SMS to %s: %s", phone, exc)


def _send_verification_outbound(user: Any, title: str, body: str) -> None:
    if not user:
        return
    email = getattr(user, "email", None)
    phone = getattr(user, "phone", None)
    if email:
        _send_verification_email(email, title, body)
    if phone:
        _send_verification_sms(phone, f"{title}: {body}")


@extend_schema(tags=["Admin - Verifications"])
class AdminVerificationListView(APIView):
    """List verification requests sorted by SLA urgency with status/kind counts."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(
        parameters=[
            OpenApiParameter("kind", str, description="Filter by kind: creator or brand", required=False),
            OpenApiParameter(
                "status",
                str,
                description="Filter by status: pending, approved, rejected, info_requested",
                required=False,
            ),
            OpenApiParameter("limit", int, description="Page limit (default: 20, max: 100)", required=False),
            OpenApiParameter("cursor", str, description="Pagination cursor (optional ID or offset)", required=False),
        ],
        responses={200: AdminVerificationListOutput(many=True)},
    )
    def get(self, request: Request) -> Response:
        qs = VerificationRequest.objects.select_related("user", "user__creator", "user__brand")

        # Filters
        kind = request.query_params.get("kind")
        if kind and kind in VerificationKind.values:
            qs = qs.filter(kind=kind)

        status_param = request.query_params.get("status")
        if status_param and status_param in VerificationStatus.values:
            qs = qs.filter(status=status_param)

        # Urgency sorting: pending requests ordered by sla_due_at asc; others by -submitted_at
        qs = qs.order_by("sla_due_at", "-submitted_at")

        # Meta counts
        all_qs = VerificationRequest.objects.all()
        counts = {
            "total": all_qs.count(),
            "pending": all_qs.filter(status=VerificationStatus.PENDING).count(),
            "approved": all_qs.filter(status=VerificationStatus.APPROVED).count(),
            "rejected": all_qs.filter(status=VerificationStatus.REJECTED).count(),
            "info_requested": all_qs.filter(status=VerificationStatus.INFO_REQUESTED).count(),
            "creator": all_qs.filter(kind=VerificationKind.CREATOR).count(),
            "brand": all_qs.filter(kind=VerificationKind.BRAND).count(),
        }

        # Pagination limit
        try:
            limit = min(max(int(request.query_params.get("limit", 20)), 1), 100)
        except ValueError:
            limit = 20

        items = list(qs[:limit])
        serialized_data = AdminVerificationListOutput(items, many=True).data

        return Response(
            {
                "success": True,
                "data": serialized_data,
                "error": None,
                "meta": {
                    "counts": counts,
                    "total": qs.count(),
                    "limit": limit,
                },
            }
        )


@extend_schema(tags=["Admin - Verifications"])
class AdminVerificationDetailView(APIView):
    """Retrieve full details of a specific verification request."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(responses={200: AdminVerificationDetailOutput})
    def get(self, request: Request, verification_id: UUID) -> Response:
        vr = (
            VerificationRequest.objects.select_related("user", "kyc_profile", "decided_by")
            .filter(pk=verification_id)
            .first()
        )
        if vr is None:
            raise NotFound("Verification request not found.")
        return Response(AdminVerificationDetailOutput(vr).data)


def _decide(
    request: Request,
    verification_id: UUID,
    *,
    vr_status: str,
    kyc_changes: dict[str, Any],
    audit_action: str,
    audit_detail: dict[str, Any],
    notification: dict[str, Any],
    vr_changes: dict[str, Any] | None = None,
    before_save: Callable[[VerificationRequest], None] | None = None,
    after_save: Callable[[VerificationRequest], None] | None = None,
) -> Response:
    """One decision on an open verification request: lock it, update it and the KYC profile, audit, notify."""
    now = timezone.now()
    with transaction.atomic():
        vr = (
            VerificationRequest.objects.select_for_update()
            .select_related("user", "kyc_profile")
            .filter(pk=verification_id)
            .first()
        )
        if vr is None:
            raise NotFound("Verification request not found.")
        if vr.status not in (VerificationStatus.PENDING, VerificationStatus.INFO_REQUESTED):
            raise Conflict("Verification request has already been decided.", code="ALREADY_DECIDED")
        if before_save:
            before_save(vr)
        for field, value in {
            "status": vr_status,
            "decided_at": now,
            "decided_by": request.user,
            **(vr_changes or {}),
        }.items():
            setattr(vr, field, value)
        vr.save()
        kyc = vr.kyc_profile
        for field, value in {"reviewed_at": now, "reviewed_by": request.user, **kyc_changes}.items():
            setattr(kyc, field, value)
        kyc.save()
        if after_save:
            after_save(vr)
        audit = record_audit(
            admin=request.user,
            action=audit_action,
            target=f"verification:{vr.id}",
            detail={"user_id": str(vr.user_id), **audit_detail},
        )
    title = notification.get("title", "KYC Verification Update")
    body = notification.get("body", "")
    notif_data = {"verificationId": str(vr.id), **notification.pop("data", {})}
    notify(user_id=vr.user_id, data=notif_data, **notification)
    _send_verification_outbound(vr.user, title, body)
    return Response({"verification": AdminVerificationDetailOutput(vr).data, "audit": audit})


def _no_blocking_checks(vr: VerificationRequest) -> None:
    if any(c.get("blocking") is True for c in vr.checks or []):
        raise Conflict("Verification has blocking checks that must be resolved.", code="VERIFICATION_BLOCKED")


def _on_approved(vr: VerificationRequest) -> None:
    mark_reviewed(vr.kyc_profile, approved=True)
    approval_side_effects(kind=vr.kind, user_id=vr.user_id, checks=vr.checks)


@extend_schema(tags=["Admin - Verifications"])
class AdminVerificationApproveView(APIView):
    """Approve a verification request if no automated check blocks it."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=None, responses={200: AdminVerificationActionResult})
    def post(self, request: Request, verification_id: UUID) -> Response:
        return _decide(
            request,
            verification_id,
            vr_status=VerificationStatus.APPROVED,
            kyc_changes={"status": KycStatus.VERIFIED, "rejection_reason": ""},
            before_save=_no_blocking_checks,
            after_save=_on_approved,
            audit_action="verification.approve",
            audit_detail={},
            notification={
                "type": "kyc_verified",
                "title": "KYC Verified",
                "body": "Your KYC verification has been approved.",
            },
        )


@extend_schema(tags=["Admin - Verifications"])
class AdminVerificationRejectView(APIView):
    """Reject a verification request with a mandatory reason."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminVerificationRejectInput, responses={200: AdminVerificationActionResult})
    def post(self, request: Request, verification_id: UUID) -> Response:
        data = AdminVerificationRejectInput(data=request.data)
        data.is_valid(raise_exception=True)
        reason, note = data.validated_data["reason"], data.validated_data.get("note", "")
        return _decide(
            request,
            verification_id,
            vr_status=VerificationStatus.REJECTED,
            vr_changes={"decision_reason": reason, "decision_note": note},
            kyc_changes={
                "status": KycStatus.REJECTED,
                "rejection_reason": reason,
                "review_message": reason,
                "pan_verified": False,
                "bank_verified": False,
            },
            audit_action="verification.reject",
            audit_detail={"reason": reason, "note": note},
            notification={
                "type": "kyc_rejected",
                "title": "KYC Verification Rejected",
                "body": reason,
                "data": {"reason": reason},
            },
        )


@extend_schema(tags=["Admin - Verifications"])
class AdminVerificationRequestInfoView(APIView):
    """Ask the applicant for more items; their KYC becomes action_needed."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminVerificationRequestInfoInput, responses={200: AdminVerificationActionResult})
    def post(self, request: Request, verification_id: UUID) -> Response:
        data = AdminVerificationRequestInfoInput(data=request.data)
        data.is_valid(raise_exception=True)
        items, message = data.validated_data["items"], data.validated_data["message"]
        return _decide(
            request,
            verification_id,
            vr_status=VerificationStatus.INFO_REQUESTED,
            vr_changes={"requested_items": items, "decision_reason": message, "decision_note": message},
            kyc_changes={"status": KycStatus.ACTION_NEEDED, "requested_items": items, "review_message": message},
            audit_action="verification.request_info",
            audit_detail={"items": items, "message": message},
            notification={
                "type": "kyc_info_requested",
                "title": "Additional KYC Information Requested",
                "body": message,
                "data": {"items": items, "message": message},
            },
        )
