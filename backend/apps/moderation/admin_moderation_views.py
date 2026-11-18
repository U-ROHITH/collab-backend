from uuid import UUID

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from apps.campaigns.models import Bid, BidStatus, Campaign, ModerationStatus
from apps.core.audit import record_audit
from apps.core.exceptions import Conflict, NotFound
from apps.identity.models import AdminRole, User, UserStatus
from apps.identity.permissions import RequireAdminRoles
from apps.moderation.models import (
    AbuseReport,
    AbuseReportStatus,
    AbuseTargetType,
    BidAlert,
    BidAlertSeverity,
    BidAlertStatus,
)
from apps.moderation.serializers import (
    AdminAbuseActionResult,
    AdminAbuseReasonInput,
    AdminAbuseReportDetailOutput,
    AdminAbuseWarnInput,
    AdminBidAlertActionResult,
    BidAlertOutput,
)
from apps.notifications.services import notify
from apps.orders.admin_serializers import (
    AdminReviewActionResult,
    AdminReviewKeepInput,
    AdminReviewOutput,
    AdminReviewReasonInput,
)
from apps.orders.models import Review, ReviewStatus


@extend_schema(tags=["Admin - Moderation"])
class AdminAbuseReportListView(APIView):
    """List abuse reports with status and target filters and counts."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "status", str, description="Status: pending, investigating, actioned, dismissed", required=False
            ),
            OpenApiParameter(
                "targetType", str, description="Target type: creator, brand, campaign, review, message", required=False
            ),
            OpenApiParameter("limit", int, description="Page limit (default: 20, max: 100)", required=False),
            OpenApiParameter("cursor", str, description="Pagination cursor (optional ID)", required=False),
        ],
        responses={200: AdminAbuseReportDetailOutput(many=True)},
    )
    def get(self, request: Request) -> Response:
        base_qs = AbuseReport.objects.select_related("reporter", "target_user")

        counts = {
            "pending": base_qs.filter(status=AbuseReportStatus.PENDING).count(),
            "investigating": base_qs.filter(status=AbuseReportStatus.INVESTIGATING).count(),
            "actioned": base_qs.filter(status=AbuseReportStatus.ACTIONED).count(),
            "dismissed": base_qs.filter(status=AbuseReportStatus.DISMISSED).count(),
            "total": base_qs.count(),
        }

        qs = base_qs
        status_param = request.query_params.get("status")
        if status_param and status_param in AbuseReportStatus.values:
            qs = qs.filter(status=status_param)

        target_type = request.query_params.get("targetType")
        if target_type and target_type in AbuseTargetType.values:
            qs = qs.filter(target_type=target_type)

        try:
            limit = min(max(int(request.query_params.get("limit", 20)), 1), 100)
        except ValueError:
            limit = 20

        cursor = request.query_params.get("cursor")
        if cursor:
            try:
                cursor_item = AbuseReport.objects.filter(pk=UUID(cursor)).first()
                if cursor_item:
                    qs = qs.filter(created_at__lt=cursor_item.created_at)
            except (ValueError, TypeError):
                pass

        total_matching = qs.count()
        items = list(qs.order_by("-created_at")[:limit])
        serialized_data = AdminAbuseReportDetailOutput(items, many=True).data

        return Response(
            {
                "success": True,
                "data": serialized_data,
                "error": None,
                "meta": {
                    "counts": counts,
                    "total": total_matching,
                    "limit": limit,
                },
            }
        )


@extend_schema(tags=["Admin - Moderation"])
class AdminAbuseReportDetailView(APIView):
    """Retrieve full detail for an abuse report."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(responses={200: AdminAbuseReportDetailOutput})
    def get(self, request: Request, id: UUID) -> Response:
        report = AbuseReport.objects.select_related("reporter", "target_user").filter(pk=id).first()
        if report is None:
            raise NotFound("Abuse report not found.")
        return Response(AdminAbuseReportDetailOutput(report).data)


@extend_schema(tags=["Admin - Moderation"])
class AdminAbuseReportRemoveView(APIView):
    """Remove the reported content (e.g. campaign or review) and resolve the report."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminAbuseReasonInput, responses={200: AdminAbuseActionResult})
    def post(self, request: Request, id: UUID) -> Response:
        input_serializer = AdminAbuseReasonInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        reason = input_serializer.validated_data["reason"]

        with transaction.atomic():
            report = (
                AbuseReport.objects.select_for_update(of=("self",))
                .select_related("reporter", "target_user")
                .filter(pk=id)
                .first()
            )
            if report is None:
                raise NotFound("Abuse report not found.")

            # Remove target content based on type
            if report.target_type == AbuseTargetType.CAMPAIGN:
                camp = Campaign.objects.filter(pk=report.target_id).first()
                if camp:
                    camp.moderation_status = ModerationStatus.REMOVED
                    camp.moderation_reason = f"Removed via abuse report: {reason}"
                    camp.save(update_fields=["moderation_status", "moderation_reason", "updated_at"])
                    Bid.objects.filter(campaign=camp, status__in=[BidStatus.PENDING, BidStatus.HELD]).update(
                        status=BidStatus.REJECTED, updated_at=timezone.now()
                    )
            elif report.target_type == AbuseTargetType.REVIEW:
                rev = Review.objects.filter(pk=report.target_id).first()
                if rev:
                    rev.status = ReviewStatus.REMOVED
                    rev.flag_reason = f"Removed via abuse report: {reason}"
                    rev.save(update_fields=["status", "flag_reason", "updated_at"])
                    # Recalculate rating
                    c = rev.creator
                    visible_reviews = Review.objects.filter(creator=c, status=ReviewStatus.VISIBLE)
                    c.rating_count = visible_reviews.count()
                    c.rating_sum = sum(r.stars for r in visible_reviews)
                    c.save(update_fields=["rating_count", "rating_sum", "updated_at"])

            report.status = AbuseReportStatus.ACTIONED
            report.action_taken = "content_removed"
            report.action_notes = reason
            report.resolved_by = request.user
            report.resolved_at = timezone.now()
            report.save(
                update_fields=["status", "action_taken", "action_notes", "resolved_by", "resolved_at", "updated_at"]
            )

            audit = record_audit(
                admin=request.user,
                action="abuse_report.remove_content",
                target=report,
                detail={"reason": reason, "targetType": report.target_type, "targetId": str(report.target_id)},
            )

            if report.reporter_id:
                notify(
                    user_id=report.reporter_id,
                    type="report_actioned",
                    title="Report Update",
                    body="Action was taken on your report: the reported content has been removed.",
                    data={"reportId": str(report.id)},
                )

            return Response(
                {
                    "report": AdminAbuseReportDetailOutput(report).data,
                    "audit": audit,
                }
            )


@extend_schema(tags=["Admin - Moderation"])
class AdminAbuseReportWarnView(APIView):
    """Issue a formal warning to the reported user."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminAbuseWarnInput, responses={200: AdminAbuseActionResult})
    def post(self, request: Request, id: UUID) -> Response:
        input_serializer = AdminAbuseWarnInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        reason = input_serializer.validated_data["reason"]
        warning_msg = input_serializer.validated_data["warningMessage"]

        with transaction.atomic():
            report = (
                AbuseReport.objects.select_for_update(of=("self",))
                .select_related("reporter", "target_user")
                .filter(pk=id)
                .first()
            )
            if report is None:
                raise NotFound("Abuse report not found.")

            if not report.target_user_id:
                raise Conflict("No target user associated with this report.", code="NO_TARGET_USER")

            # Notify target user
            notify(
                user_id=report.target_user_id,
                type="admin_warning",
                title="Account Warning",
                body=warning_msg,
                data={"reason": reason},
            )

            report.status = AbuseReportStatus.ACTIONED
            report.action_taken = "user_warned"
            report.action_notes = f"{reason}: {warning_msg}"
            report.resolved_by = request.user
            report.resolved_at = timezone.now()
            report.save(
                update_fields=["status", "action_taken", "action_notes", "resolved_by", "resolved_at", "updated_at"]
            )

            audit = record_audit(
                admin=request.user,
                action="abuse_report.warn",
                target=report,
                detail={"reason": reason, "targetUserId": str(report.target_user_id)},
            )

            if report.reporter_id:
                notify(
                    user_id=report.reporter_id,
                    type="report_actioned",
                    title="Report Update",
                    body="Action was taken on your report: a formal warning was issued to the user.",
                    data={"reportId": str(report.id)},
                )

            return Response(
                {
                    "report": AdminAbuseReportDetailOutput(report).data,
                    "audit": audit,
                }
            )


@extend_schema(tags=["Admin - Moderation"])
class AdminAbuseReportSuspendAccountView(APIView):
    """Suspend the reported user's account and resolve the report."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminAbuseReasonInput, responses={200: AdminAbuseActionResult})
    def post(self, request: Request, id: UUID) -> Response:
        input_serializer = AdminAbuseReasonInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        reason = input_serializer.validated_data["reason"]

        with transaction.atomic():
            report = (
                AbuseReport.objects.select_for_update(of=("self",))
                .select_related("reporter", "target_user")
                .filter(pk=id)
                .first()
            )
            if report is None:
                raise NotFound("Abuse report not found.")

            if not report.target_user_id:
                raise Conflict("No target user associated with this report.", code="NO_TARGET_USER")

            target_user = User.objects.select_for_update(of=("self",)).filter(pk=report.target_user_id).first()
            if target_user:
                target_user.status = UserStatus.SUSPENDED
                target_user.save(update_fields=["status", "updated_at"])
                tokens = OutstandingToken.objects.filter(user=target_user).exclude(blacklistedtoken__isnull=False)
                for t in tokens:
                    BlacklistedToken.objects.get_or_create(token=t)

                notify(
                    user_id=target_user.id,
                    type="account_suspended",
                    title="Account Suspended",
                    body=f"Your account has been suspended due to abuse reports: {reason}",
                    data={"reason": reason},
                )

            report.status = AbuseReportStatus.ACTIONED
            report.action_taken = "account_suspended"
            report.action_notes = reason
            report.resolved_by = request.user
            report.resolved_at = timezone.now()
            report.save(
                update_fields=["status", "action_taken", "action_notes", "resolved_by", "resolved_at", "updated_at"]
            )

            audit = record_audit(
                admin=request.user,
                action="abuse_report.suspend_account",
                target=report,
                detail={"reason": reason, "targetUserId": str(report.target_user_id)},
            )

            if report.reporter_id:
                notify(
                    user_id=report.reporter_id,
                    type="report_actioned",
                    title="Report Update",
                    body="Action was taken on your report: the user account has been suspended.",
                    data={"reportId": str(report.id)},
                )

            return Response(
                {
                    "report": AdminAbuseReportDetailOutput(report).data,
                    "audit": audit,
                }
            )


@extend_schema(tags=["Admin - Moderation"])
class AdminAbuseReportDismissView(APIView):
    """Dismiss an abuse report with an explanation note."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminAbuseReasonInput, responses={200: AdminAbuseActionResult})
    def post(self, request: Request, id: UUID) -> Response:
        input_serializer = AdminAbuseReasonInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        reason = input_serializer.validated_data["reason"]

        with transaction.atomic():
            report = (
                AbuseReport.objects.select_for_update(of=("self",))
                .select_related("reporter", "target_user")
                .filter(pk=id)
                .first()
            )
            if report is None:
                raise NotFound("Abuse report not found.")

            report.status = AbuseReportStatus.DISMISSED
            report.action_taken = "dismissed"
            report.action_notes = reason
            report.resolved_by = request.user
            report.resolved_at = timezone.now()
            report.save(
                update_fields=["status", "action_taken", "action_notes", "resolved_by", "resolved_at", "updated_at"]
            )

            audit = record_audit(
                admin=request.user,
                action="abuse_report.dismiss",
                target=report,
                detail={"reason": reason},
            )

            return Response(
                {
                    "report": AdminAbuseReportDetailOutput(report).data,
                    "audit": audit,
                }
            )


@extend_schema(tags=["Admin - Moderation"])
class AdminBidAlertListView(APIView):
    """List automated bid-pattern alerts with status and severity filters."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "status", str, description="Filter by status: pending, frozen, dismissed, actioned", required=False
            ),
            OpenApiParameter(
                "severity", str, description="Filter by severity: low, medium, high, critical", required=False
            ),
            OpenApiParameter("limit", int, description="Page limit (default: 20, max: 100)", required=False),
            OpenApiParameter("cursor", str, description="Pagination cursor (optional ID)", required=False),
        ],
        responses={200: BidAlertOutput(many=True)},
    )
    def get(self, request: Request) -> Response:
        base_qs = BidAlert.objects.select_related("campaign").prefetch_related("bids")

        counts = {
            "pending": base_qs.filter(status=BidAlertStatus.PENDING).count(),
            "frozen": base_qs.filter(status=BidAlertStatus.FROZEN).count(),
            "dismissed": base_qs.filter(status=BidAlertStatus.DISMISSED).count(),
            "actioned": base_qs.filter(status=BidAlertStatus.ACTIONED).count(),
            "total": base_qs.count(),
        }

        qs = base_qs
        status_param = request.query_params.get("status")
        if status_param and status_param in BidAlertStatus.values:
            qs = qs.filter(status=status_param)

        severity_param = request.query_params.get("severity")
        if severity_param and severity_param in BidAlertSeverity.values:
            qs = qs.filter(severity=severity_param)

        try:
            limit = min(max(int(request.query_params.get("limit", 20)), 1), 100)
        except ValueError:
            limit = 20

        cursor = request.query_params.get("cursor")
        if cursor:
            try:
                cursor_item = BidAlert.objects.filter(pk=UUID(cursor)).first()
                if cursor_item:
                    qs = qs.filter(created_at__lt=cursor_item.created_at)
            except (ValueError, TypeError):
                pass

        total_matching = qs.count()
        items = list(qs.order_by("-created_at")[:limit])
        serialized_data = BidAlertOutput(items, many=True).data

        return Response(
            {
                "success": True,
                "data": serialized_data,
                "error": None,
                "meta": {
                    "counts": counts,
                    "total": total_matching,
                    "limit": limit,
                },
            }
        )


@extend_schema(tags=["Admin - Moderation"])
class AdminBidAlertFreezeBidsView(APIView):
    """Freeze all bids linked to a bid-pattern alert."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=None, responses={200: AdminBidAlertActionResult})
    def post(self, request: Request, id: UUID) -> Response:
        with transaction.atomic():
            alert = (
                BidAlert.objects.select_for_update(of=("self",))
                .select_related("campaign")
                .prefetch_related("bids")
                .filter(pk=id)
                .first()
            )
            if alert is None:
                raise NotFound("Bid alert not found.")

            bids = alert.bids.filter(status=BidStatus.PENDING)
            bids.update(status=BidStatus.HELD, updated_at=timezone.now())

            alert.status = BidAlertStatus.FROZEN
            alert.resolved_by = request.user
            alert.resolved_at = timezone.now()
            alert.save(update_fields=["status", "resolved_by", "resolved_at", "updated_at"])

            audit = record_audit(
                admin=request.user,
                action="bid_alert.freeze_bids",
                target=alert,
                detail={"campaignId": str(alert.campaign_id), "frozenBidsCount": bids.count()},
            )

            return Response(
                {
                    "alert": BidAlertOutput(alert).data,
                    "audit": audit,
                }
            )


@extend_schema(tags=["Admin - Moderation"])
class AdminBidAlertSuspendAccountsView(APIView):
    """Suspend accounts of all creators linked to bids in this alert."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminAbuseReasonInput, responses={200: AdminBidAlertActionResult})
    def post(self, request: Request, id: UUID) -> Response:
        input_serializer = AdminAbuseReasonInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        reason = input_serializer.validated_data["reason"]

        with transaction.atomic():
            alert = (
                BidAlert.objects.select_for_update(of=("self",))
                .select_related("campaign")
                .prefetch_related("bids__creator__user")
                .filter(pk=id)
                .first()
            )
            if alert is None:
                raise NotFound("Bid alert not found.")

            users_to_suspend = set()
            for b in alert.bids.all():
                if b.creator and b.creator.user:
                    users_to_suspend.add(b.creator.user)

            for u in users_to_suspend:
                u.status = UserStatus.SUSPENDED
                u.save(update_fields=["status", "updated_at"])
                tokens = OutstandingToken.objects.filter(user=u).exclude(blacklistedtoken__isnull=False)
                for t in tokens:
                    BlacklistedToken.objects.get_or_create(token=t)
                notify(
                    user_id=u.id,
                    type="account_suspended",
                    title="Account Suspended",
                    body=f"Your account has been suspended due to bid manipulation alerts: {reason}",
                    data={"reason": reason},
                )

            alert.status = BidAlertStatus.ACTIONED
            alert.resolved_by = request.user
            alert.resolved_at = timezone.now()
            alert.save(update_fields=["status", "resolved_by", "resolved_at", "updated_at"])

            audit = record_audit(
                admin=request.user,
                action="bid_alert.suspend_accounts",
                target=alert,
                detail={"reason": reason, "suspendedCount": len(users_to_suspend)},
            )

            return Response(
                {
                    "alert": BidAlertOutput(alert).data,
                    "audit": audit,
                }
            )


@extend_schema(tags=["Admin - Moderation"])
class AdminBidAlertWarnView(APIView):
    """Send warnings to all creators linked to bids in this alert."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminAbuseReasonInput, responses={200: AdminBidAlertActionResult})
    def post(self, request: Request, id: UUID) -> Response:
        input_serializer = AdminAbuseReasonInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        reason = input_serializer.validated_data["reason"]

        with transaction.atomic():
            alert = (
                BidAlert.objects.select_for_update(of=("self",))
                .select_related("campaign")
                .prefetch_related("bids__creator__user")
                .filter(pk=id)
                .first()
            )
            if alert is None:
                raise NotFound("Bid alert not found.")

            warned_users = set()
            for b in alert.bids.all():
                if b.creator and b.creator.user:
                    warned_users.add(b.creator.user)

            for u in warned_users:
                notify(
                    user_id=u.id,
                    type="admin_warning",
                    title="Bidding Activity Warning",
                    body=f"Anomalous bidding activity detected: {reason}",
                    data={"reason": reason},
                )

            alert.status = BidAlertStatus.ACTIONED
            alert.resolved_by = request.user
            alert.resolved_at = timezone.now()
            alert.save(update_fields=["status", "resolved_by", "resolved_at", "updated_at"])

            audit = record_audit(
                admin=request.user,
                action="bid_alert.warn",
                target=alert,
                detail={"reason": reason, "warnedCount": len(warned_users)},
            )

            return Response(
                {
                    "alert": BidAlertOutput(alert).data,
                    "audit": audit,
                }
            )


@extend_schema(tags=["Admin - Moderation"])
class AdminBidAlertDismissView(APIView):
    """Dismiss a bid-pattern alert."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminAbuseReasonInput, responses={200: AdminBidAlertActionResult})
    def post(self, request: Request, id: UUID) -> Response:
        input_serializer = AdminAbuseReasonInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        reason = input_serializer.validated_data["reason"]

        with transaction.atomic():
            alert = (
                BidAlert.objects.select_for_update(of=("self",))
                .select_related("campaign")
                .prefetch_related("bids")
                .filter(pk=id)
                .first()
            )
            if alert is None:
                raise NotFound("Bid alert not found.")

            alert.status = BidAlertStatus.DISMISSED
            alert.resolved_by = request.user
            alert.resolved_at = timezone.now()
            alert.save(update_fields=["status", "resolved_by", "resolved_at", "updated_at"])

            audit = record_audit(
                admin=request.user,
                action="bid_alert.dismiss",
                target=alert,
                detail={"reason": reason},
            )

            return Response(
                {
                    "alert": BidAlertOutput(alert).data,
                    "audit": audit,
                }
            )


# --- Reviews Moderation -------------------------------------------------------------


@extend_schema(tags=["Admin - Moderation"])
class AdminReviewListView(APIView):
    """List customer reviews with status filters and moderation counts."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(
        parameters=[
            OpenApiParameter("status", str, description="Review status: visible, flagged, removed", required=False),
            OpenApiParameter("creatorId", str, description="Filter by creator UUID", required=False),
            OpenApiParameter("brandId", str, description="Filter by brand UUID", required=False),
            OpenApiParameter("limit", int, description="Page limit (default: 20, max: 100)", required=False),
            OpenApiParameter("cursor", str, description="Pagination cursor (optional ID)", required=False),
        ],
        responses={200: AdminReviewOutput(many=True)},
    )
    def get(self, request: Request) -> Response:
        base_qs = Review.objects.select_related("brand", "creator")

        creator_id = request.query_params.get("creatorId")
        if creator_id:
            try:
                base_qs = base_qs.filter(creator_id=UUID(creator_id))
            except (ValueError, TypeError):
                base_qs = base_qs.none()

        brand_id = request.query_params.get("brandId")
        if brand_id:
            try:
                base_qs = base_qs.filter(brand_id=UUID(brand_id))
            except (ValueError, TypeError):
                base_qs = base_qs.none()

        counts = {
            "visible": base_qs.filter(status=ReviewStatus.VISIBLE).count(),
            "flagged": base_qs.filter(status=ReviewStatus.FLAGGED).count(),
            "removed": base_qs.filter(status=ReviewStatus.REMOVED).count(),
            "total": base_qs.count(),
        }

        qs = base_qs
        status_param = request.query_params.get("status")
        if status_param and status_param in ReviewStatus.values:
            qs = qs.filter(status=status_param)

        try:
            limit = min(max(int(request.query_params.get("limit", 20)), 1), 100)
        except ValueError:
            limit = 20

        cursor = request.query_params.get("cursor")
        if cursor:
            try:
                cursor_item = Review.objects.filter(pk=UUID(cursor)).first()
                if cursor_item:
                    qs = qs.filter(created_at__lt=cursor_item.created_at)
            except (ValueError, TypeError):
                pass

        total_matching = qs.count()
        items = list(qs.order_by("-created_at")[:limit])
        serialized_data = AdminReviewOutput(items, many=True).data

        return Response(
            {
                "success": True,
                "data": serialized_data,
                "error": None,
                "meta": {
                    "counts": counts,
                    "total": total_matching,
                    "limit": limit,
                },
            }
        )


@extend_schema(tags=["Admin - Moderation"])
class AdminReviewDeleteView(APIView):
    """Mark a review as removed and recalculate creator rating."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminReviewReasonInput, responses={200: AdminReviewActionResult})
    def post(self, request: Request, id: UUID) -> Response:
        input_serializer = AdminReviewReasonInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        reason = input_serializer.validated_data["reason"]

        with transaction.atomic():
            review = (
                Review.objects.select_for_update(of=("self",))
                .select_related("creator", "brand")
                .filter(pk=id)
                .first()
            )
            if review is None:
                raise NotFound("Review not found.")

            review.status = ReviewStatus.REMOVED
            review.flag_reason = reason
            review.save(update_fields=["status", "flag_reason", "updated_at"])

            # Recalculate creator rating from visible reviews
            creator = review.creator
            visible_reviews = Review.objects.filter(creator=creator, status=ReviewStatus.VISIBLE)
            creator.rating_count = visible_reviews.count()
            creator.rating_sum = sum(r.stars for r in visible_reviews)
            creator.save(update_fields=["rating_count", "rating_sum", "updated_at"])

            audit = record_audit(
                admin=request.user,
                action="review.delete",
                target=review,
                detail={"reason": reason, "creatorId": str(creator.id)},
            )

            return Response(
                {
                    "review": AdminReviewOutput(review).data,
                    "audit": audit,
                }
            )


@extend_schema(tags=["Admin - Moderation"])
class AdminReviewKeepView(APIView):
    """Keep a flagged or removed review as visible and ensure rating is counted."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminReviewKeepInput, responses={200: AdminReviewActionResult})
    def post(self, request: Request, id: UUID) -> Response:
        input_serializer = AdminReviewKeepInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        note = input_serializer.validated_data.get("note", "")

        with transaction.atomic():
            review = (
                Review.objects.select_for_update(of=("self",))
                .select_related("creator", "brand")
                .filter(pk=id)
                .first()
            )
            if review is None:
                raise NotFound("Review not found.")

            review.status = ReviewStatus.VISIBLE
            review.flag_reason = ""
            review.save(update_fields=["status", "flag_reason", "updated_at"])

            # Recalculate creator rating
            creator = review.creator
            visible_reviews = Review.objects.filter(creator=creator, status=ReviewStatus.VISIBLE)
            creator.rating_count = visible_reviews.count()
            creator.rating_sum = sum(r.stars for r in visible_reviews)
            creator.save(update_fields=["rating_count", "rating_sum", "updated_at"])

            audit = record_audit(
                admin=request.user,
                action="review.keep",
                target=review,
                detail={"note": note, "creatorId": str(creator.id)},
            )

            return Response(
                {
                    "review": AdminReviewOutput(review).data,
                    "audit": audit,
                }
            )

