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
from apps.kyc.models import (
    KycStatus,
    VerificationKind,
    VerificationRequest,
    VerificationStatus,
)
from apps.notifications.services import notify


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


class AdminVerificationApproveView(APIView):
    """Approve a verification request if all checks pass."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=None, responses={200: AdminVerificationActionResult})
    def post(self, request: Request, verification_id: UUID) -> Response:
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

            # Check for blocking automated checks
            checks = vr.checks or []
            has_blocking = any(c.get("blocking") is True for c in checks)
            if has_blocking:
                raise Conflict("Verification has blocking checks that must be resolved.", code="VERIFICATION_BLOCKED")

            vr.status = VerificationStatus.APPROVED
            vr.decided_at = timezone.now()
            vr.decided_by = request.user
            vr.save(update_fields=["status", "decided_at", "decided_by", "updated_at"])

            kyc = vr.kyc_profile
            kyc.status = KycStatus.VERIFIED
            kyc.reviewed_at = timezone.now()
            kyc.reviewed_by = request.user
            kyc.rejection_reason = ""
            kyc.save(update_fields=["status", "reviewed_at", "reviewed_by", "rejection_reason", "updated_at"])

            if vr.kind == VerificationKind.CREATOR:
                from apps.creators.models import ApprovalStatus, Creator

                Creator.objects.filter(user=vr.user).update(approval_status=ApprovalStatus.APPROVED)
            elif vr.kind == VerificationKind.BRAND:
                from apps.brands.models import Brand

                Brand.objects.filter(owner=vr.user).update(reg_verified=True, gstin_status="active")

            audit = record_audit(
                admin=request.user,
                action="verification.approve",
                target=f"verification:{vr.id}",
                detail={"user_id": str(vr.user_id), "kind": vr.kind},
            )

        notify(
            user_id=vr.user_id,
            type="kyc_verified",
            title="KYC Verified",
            body="Your KYC verification has been approved.",
            data={"verificationId": str(vr.id)},
        )

        return Response(
            {
                "verification": AdminVerificationDetailOutput(vr).data,
                "audit": audit,
            }
        )


class AdminVerificationRejectView(APIView):
    """Reject a verification request with a mandatory reason."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminVerificationRejectInput, responses={200: AdminVerificationActionResult})
    def post(self, request: Request, verification_id: UUID) -> Response:
        input_serializer = AdminVerificationRejectInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        reason = input_serializer.validated_data["reason"]
        note = input_serializer.validated_data.get("note", "")

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

            vr.status = VerificationStatus.REJECTED
            vr.decision_reason = reason
            vr.decision_note = note
            vr.decided_at = timezone.now()
            vr.decided_by = request.user
            vr.save(
                update_fields=[
                    "status",
                    "decision_reason",
                    "decision_note",
                    "decided_at",
                    "decided_by",
                    "updated_at",
                ]
            )

            kyc = vr.kyc_profile
            kyc.status = KycStatus.REJECTED
            kyc.rejection_reason = reason
            kyc.review_message = reason
            kyc.reviewed_at = timezone.now()
            kyc.reviewed_by = request.user
            kyc.save(
                update_fields=[
                    "status",
                    "rejection_reason",
                    "review_message",
                    "reviewed_at",
                    "reviewed_by",
                    "updated_at",
                ]
            )

            audit = record_audit(
                admin=request.user,
                action="verification.reject",
                target=f"verification:{vr.id}",
                detail={"user_id": str(vr.user_id), "reason": reason, "note": note},
            )

        notify(
            user_id=vr.user_id,
            type="kyc_rejected",
            title="KYC Verification Rejected",
            body=reason,
            data={"verificationId": str(vr.id), "reason": reason},
        )

        return Response(
            {
                "verification": AdminVerificationDetailOutput(vr).data,
                "audit": audit,
            }
        )


class AdminVerificationRequestInfoView(APIView):
    """Request additional items/documents from applicant, setting KYC status to action_needed."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminVerificationRequestInfoInput, responses={200: AdminVerificationActionResult})
    def post(self, request: Request, verification_id: UUID) -> Response:
        input_serializer = AdminVerificationRequestInfoInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        items = input_serializer.validated_data["items"]
        message = input_serializer.validated_data["message"]

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

            vr.status = VerificationStatus.INFO_REQUESTED
            vr.requested_items = items
            vr.decision_reason = message
            vr.decision_note = message
            vr.decided_at = timezone.now()
            vr.decided_by = request.user
            vr.save(
                update_fields=[
                    "status",
                    "requested_items",
                    "decision_reason",
                    "decision_note",
                    "decided_at",
                    "decided_by",
                    "updated_at",
                ]
            )

            kyc = vr.kyc_profile
            kyc.status = KycStatus.ACTION_NEEDED
            kyc.requested_items = items
            kyc.review_message = message
            kyc.reviewed_at = timezone.now()
            kyc.reviewed_by = request.user
            kyc.save(
                update_fields=[
                    "status",
                    "requested_items",
                    "review_message",
                    "reviewed_at",
                    "reviewed_by",
                    "updated_at",
                ]
            )

            audit = record_audit(
                admin=request.user,
                action="verification.request_info",
                target=f"verification:{vr.id}",
                detail={"user_id": str(vr.user_id), "items": items, "message": message},
            )

        notify(
            user_id=vr.user_id,
            type="kyc_info_requested",
            title="Additional KYC Information Requested",
            body=message,
            data={"verificationId": str(vr.id), "items": items, "message": message},
        )

        return Response(
            {
                "verification": AdminVerificationDetailOutput(vr).data,
                "audit": audit,
            }
        )
