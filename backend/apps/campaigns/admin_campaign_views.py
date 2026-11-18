from uuid import UUID

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.campaigns.admin_serializers import (
    AdminCampaignActionResult,
    AdminCampaignDetailOutput,
    AdminCampaignListOutput,
    AdminCampaignReasonInput,
    AdminCampaignRejectInput,
)
from apps.campaigns.models import Bid, BidStatus, Campaign, ModerationStatus
from apps.core.audit import record_audit
from apps.core.exceptions import Conflict, NotFound
from apps.identity.models import AdminRole
from apps.identity.permissions import RequireAdminRoles
from apps.notifications.services import notify


@extend_schema(tags=["Admin - Campaigns"])
class AdminCampaignListView(APIView):
    """List marketplace campaigns for moderation review with filter counts."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "filter",
                str,
                description="Filter status: pending (default), all, live, hidden, rejected, removed, flagged",
                required=False,
            ),
            OpenApiParameter("brandId", str, description="Filter by brand UUID", required=False),
            OpenApiParameter("limit", int, description="Page limit (default: 20, max: 100)", required=False),
            OpenApiParameter("cursor", str, description="Pagination cursor (optional ID)", required=False),
        ],
        responses={200: AdminCampaignListOutput(many=True)},
    )
    def get(self, request: Request) -> Response:
        filter_status = request.query_params.get("filter", "pending").lower()
        brand_id = request.query_params.get("brandId")

        base_qs = Campaign.objects.select_related("brand").annotate(
            bid_count=Count("bids", filter=~Q(bids__status=BidStatus.WITHDRAWN))
        )

        if brand_id:
            try:
                base_qs = base_qs.filter(brand_id=UUID(brand_id))
            except (ValueError, TypeError):
                base_qs = base_qs.none()

        # Compute status counts on base query
        counts = {
            "pending": base_qs.filter(moderation_status=ModerationStatus.PENDING_REVIEW).count(),
            "live": base_qs.filter(moderation_status=ModerationStatus.LIVE).count(),
            "hidden": base_qs.filter(moderation_status=ModerationStatus.HIDDEN).count(),
            "rejected": base_qs.filter(moderation_status=ModerationStatus.REJECTED).count(),
            "removed": base_qs.filter(moderation_status=ModerationStatus.REMOVED).count(),
            "total": base_qs.count(),
        }

        # Filter queryset based on param
        if filter_status == "pending":
            qs = base_qs.filter(moderation_status=ModerationStatus.PENDING_REVIEW).order_by(
                "submitted_at", "created_at"
            )
        elif filter_status == "live":
            qs = base_qs.filter(moderation_status=ModerationStatus.LIVE).order_by("-created_at")
        elif filter_status == "hidden":
            qs = base_qs.filter(moderation_status=ModerationStatus.HIDDEN).order_by("-created_at")
        elif filter_status == "rejected":
            qs = base_qs.filter(moderation_status=ModerationStatus.REJECTED).order_by("-created_at")
        elif filter_status == "removed":
            qs = base_qs.filter(moderation_status=ModerationStatus.REMOVED).order_by("-created_at")
        elif filter_status == "flagged":
            # Campaigns with changes or solicitation patterns
            qs = (
                base_qs.filter(moderation_status=ModerationStatus.PENDING_REVIEW)
                .exclude(changed_fields=[])
                .order_by("submitted_at", "created_at")
            )
        else:  # "all"
            qs = base_qs.order_by("-created_at")

        # Compute flagged count
        flagged_count = (
            base_qs.filter(moderation_status=ModerationStatus.PENDING_REVIEW).exclude(changed_fields=[]).count()
        )
        counts["flagged"] = flagged_count

        try:
            limit = min(max(int(request.query_params.get("limit", 20)), 1), 100)
        except ValueError:
            limit = 20

        cursor = request.query_params.get("cursor")
        if cursor:
            try:
                cursor_uuid = UUID(cursor)
                cursor_item = Campaign.objects.filter(pk=cursor_uuid).first()
                if cursor_item:
                    if filter_status in ("pending", "flagged"):
                        qs = qs.filter(created_at__gt=cursor_item.created_at)
                    else:
                        qs = qs.filter(created_at__lt=cursor_item.created_at)
            except (ValueError, TypeError):
                pass

        total_matching = qs.count()
        items = list(qs[:limit])
        serialized_data = AdminCampaignListOutput(items, many=True).data

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


@extend_schema(tags=["Admin - Campaigns"])
class AdminCampaignDetailView(APIView):
    """Retrieve full details, automated signals, and moderation state for a campaign."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(responses={200: AdminCampaignDetailOutput})
    def get(self, request: Request, campaign_id: UUID) -> Response:
        campaign = (
            Campaign.objects.select_related("brand", "brand__owner", "moderated_by")
            .prefetch_related("bids")
            .filter(pk=campaign_id)
            .first()
        )
        if campaign is None:
            raise NotFound("Campaign not found.")
        return Response(AdminCampaignDetailOutput(campaign).data)


@extend_schema(tags=["Admin - Campaigns"])
class AdminCampaignApproveView(APIView):
    """Approve a pending campaign, moving it to live and unfreezing held bids."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=None, responses={200: AdminCampaignActionResult})
    def post(self, request: Request, campaign_id: UUID) -> Response:
        with transaction.atomic():
            campaign = (
                Campaign.objects.select_for_update(of=("self",))
                .select_related("brand", "brand__owner")
                .filter(pk=campaign_id)
                .first()
            )
            if campaign is None:
                raise NotFound("Campaign not found.")

            if campaign.moderation_status != ModerationStatus.PENDING_REVIEW:
                raise Conflict(
                    f"Cannot approve campaign with status '{campaign.moderation_status}'."
                    " Only pending campaigns can be approved.",
                    code="INVALID_STATUS_TRANSITION",
                )

            campaign.moderation_status = ModerationStatus.LIVE
            campaign.moderated_at = timezone.now()
            campaign.moderated_by = request.user
            campaign.moderation_reason = ""
            campaign.rule_broken = ""
            campaign.changed_fields = []
            if not campaign.posted_at:
                campaign.posted_at = timezone.now()

            campaign.save(
                update_fields=[
                    "moderation_status",
                    "moderated_at",
                    "moderated_by",
                    "moderation_reason",
                    "rule_broken",
                    "changed_fields",
                    "posted_at",
                    "updated_at",
                ]
            )

            # Unfreeze held bids
            Bid.objects.filter(campaign=campaign, status=BidStatus.HELD).update(
                status=BidStatus.PENDING, updated_at=timezone.now()
            )

            audit = record_audit(
                admin=request.user,
                action="campaign.approve",
                target=campaign,
                detail={"brandId": str(campaign.brand_id), "title": campaign.title},
            )

            if campaign.brand.owner_id:
                notify(
                    user_id=campaign.brand.owner_id,
                    type="campaign_approved",
                    title="Campaign Approved",
                    body=f"Your campaign '{campaign.title}' has been approved and is now live.",
                    data={"campaignId": str(campaign.id)},
                )

            return Response(
                {
                    "campaign": AdminCampaignDetailOutput(campaign).data,
                    "audit": audit,
                }
            )


@extend_schema(tags=["Admin - Campaigns"])
class AdminCampaignRejectView(APIView):
    """Reject a pending campaign with a mandatory reason."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminCampaignRejectInput, responses={200: AdminCampaignActionResult})
    def post(self, request: Request, campaign_id: UUID) -> Response:
        input_serializer = AdminCampaignRejectInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        reason = input_serializer.validated_data["reason"]
        note = input_serializer.validated_data.get("note", "")
        rule_broken = input_serializer.validated_data.get("ruleBroken", "")

        with transaction.atomic():
            campaign = (
                Campaign.objects.select_for_update(of=("self",))
                .select_related("brand", "brand__owner")
                .filter(pk=campaign_id)
                .first()
            )
            if campaign is None:
                raise NotFound("Campaign not found.")

            if campaign.moderation_status != ModerationStatus.PENDING_REVIEW:
                raise Conflict(
                    f"Cannot reject campaign with status '{campaign.moderation_status}'."
                    " Only pending campaigns can be rejected.",
                    code="INVALID_STATUS_TRANSITION",
                )

            campaign.moderation_status = ModerationStatus.REJECTED
            campaign.moderated_at = timezone.now()
            campaign.moderated_by = request.user
            campaign.moderation_reason = reason
            campaign.rule_broken = rule_broken or ""
            campaign.save(
                update_fields=[
                    "moderation_status",
                    "moderated_at",
                    "moderated_by",
                    "moderation_reason",
                    "rule_broken",
                    "updated_at",
                ]
            )

            audit = record_audit(
                admin=request.user,
                action="campaign.reject",
                target=campaign,
                detail={"brandId": str(campaign.brand_id), "reason": reason, "note": note, "ruleBroken": rule_broken},
            )

            if campaign.brand.owner_id:
                notify(
                    user_id=campaign.brand.owner_id,
                    type="campaign_rejected",
                    title="Campaign Rejected",
                    body=f"Your campaign '{campaign.title}' was rejected: {reason}",
                    data={"campaignId": str(campaign.id), "reason": reason},
                )

            return Response(
                {
                    "campaign": AdminCampaignDetailOutput(campaign).data,
                    "audit": audit,
                }
            )


@extend_schema(tags=["Admin - Campaigns"])
class AdminCampaignHideView(APIView):
    """Hide a live campaign from public listings and freeze active bids."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminCampaignReasonInput, responses={200: AdminCampaignActionResult})
    def post(self, request: Request, campaign_id: UUID) -> Response:
        input_serializer = AdminCampaignReasonInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        reason = input_serializer.validated_data["reason"]

        with transaction.atomic():
            campaign = (
                Campaign.objects.select_for_update(of=("self",))
                .select_related("brand", "brand__owner")
                .filter(pk=campaign_id)
                .first()
            )
            if campaign is None:
                raise NotFound("Campaign not found.")

            if campaign.moderation_status != ModerationStatus.LIVE:
                raise Conflict(
                    f"Cannot hide campaign with status '{campaign.moderation_status}'."
                    " Only live campaigns can be hidden.",
                    code="INVALID_STATUS_TRANSITION",
                )

            campaign.moderation_status = ModerationStatus.HIDDEN
            campaign.moderated_at = timezone.now()
            campaign.moderated_by = request.user
            campaign.moderation_reason = reason
            campaign.save(
                update_fields=[
                    "moderation_status",
                    "moderated_at",
                    "moderated_by",
                    "moderation_reason",
                    "updated_at",
                ]
            )

            # Freeze open bids
            Bid.objects.filter(campaign=campaign, status=BidStatus.PENDING).update(
                status=BidStatus.HELD, updated_at=timezone.now()
            )

            audit = record_audit(
                admin=request.user,
                action="campaign.hide",
                target=campaign,
                detail={"brandId": str(campaign.brand_id), "reason": reason},
            )

            if campaign.brand.owner_id:
                notify(
                    user_id=campaign.brand.owner_id,
                    type="campaign_hidden",
                    title="Campaign Hidden",
                    body=f"Your campaign '{campaign.title}' was hidden by moderators: {reason}",
                    data={"campaignId": str(campaign.id), "reason": reason},
                )

            return Response(
                {
                    "campaign": AdminCampaignDetailOutput(campaign).data,
                    "audit": audit,
                }
            )


@extend_schema(tags=["Admin - Campaigns"])
class AdminCampaignRemoveView(APIView):
    """Remove a live or hidden campaign completely and decline active bids."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminCampaignReasonInput, responses={200: AdminCampaignActionResult})
    def post(self, request: Request, campaign_id: UUID) -> Response:
        input_serializer = AdminCampaignReasonInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        reason = input_serializer.validated_data["reason"]

        with transaction.atomic():
            campaign = (
                Campaign.objects.select_for_update(of=("self",))
                .select_related("brand", "brand__owner")
                .filter(pk=campaign_id)
                .first()
            )
            if campaign is None:
                raise NotFound("Campaign not found.")

            if campaign.moderation_status not in (ModerationStatus.LIVE, ModerationStatus.HIDDEN):
                raise Conflict(
                    f"Cannot remove campaign with status '{campaign.moderation_status}'."
                    " Only live or hidden campaigns can be removed.",
                    code="INVALID_STATUS_TRANSITION",
                )

            campaign.moderation_status = ModerationStatus.REMOVED
            campaign.moderated_at = timezone.now()
            campaign.moderated_by = request.user
            campaign.moderation_reason = reason
            campaign.save(
                update_fields=[
                    "moderation_status",
                    "moderated_at",
                    "moderated_by",
                    "moderation_reason",
                    "updated_at",
                ]
            )

            # Decline open / held bids
            open_bids = list(
                Bid.objects.filter(campaign=campaign, status__in=[BidStatus.PENDING, BidStatus.HELD]).select_related(
                    "creator__user"
                )
            )
            for b in open_bids:
                b.status = BidStatus.REJECTED
                b.save(update_fields=["status", "updated_at"])
                if b.creator.user_id:
                    notify(
                        user_id=b.creator.user_id,
                        type="bid_declined",
                        title="Bid Declined",
                        body=f"Campaign '{campaign.title}' was removed by moderators.",
                        data={"campaignId": str(campaign.id), "bidId": str(b.id)},
                    )

            audit = record_audit(
                admin=request.user,
                action="campaign.remove",
                target=campaign,
                detail={"brandId": str(campaign.brand_id), "reason": reason},
            )

            if campaign.brand.owner_id:
                notify(
                    user_id=campaign.brand.owner_id,
                    type="campaign_removed",
                    title="Campaign Removed",
                    body=f"Your campaign '{campaign.title}' was removed by moderators: {reason}",
                    data={"campaignId": str(campaign.id), "reason": reason},
                )

            return Response(
                {
                    "campaign": AdminCampaignDetailOutput(campaign).data,
                    "audit": audit,
                }
            )


@extend_schema(tags=["Admin - Campaigns"])
class AdminCampaignRestoreView(APIView):
    """Restore a hidden campaign to live status and restore frozen bids."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminCampaignReasonInput, responses={200: AdminCampaignActionResult})
    def post(self, request: Request, campaign_id: UUID) -> Response:
        input_serializer = AdminCampaignReasonInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        reason = input_serializer.validated_data["reason"]

        with transaction.atomic():
            campaign = (
                Campaign.objects.select_for_update(of=("self",))
                .select_related("brand", "brand__owner")
                .filter(pk=campaign_id)
                .first()
            )
            if campaign is None:
                raise NotFound("Campaign not found.")

            if campaign.moderation_status != ModerationStatus.HIDDEN:
                raise Conflict(
                    f"Cannot restore campaign with status '{campaign.moderation_status}'."
                    " Only hidden campaigns can be restored.",
                    code="INVALID_STATUS_TRANSITION",
                )

            campaign.moderation_status = ModerationStatus.LIVE
            campaign.moderated_at = timezone.now()
            campaign.moderated_by = request.user
            campaign.moderation_reason = ""
            campaign.save(
                update_fields=[
                    "moderation_status",
                    "moderated_at",
                    "moderated_by",
                    "moderation_reason",
                    "updated_at",
                ]
            )

            # Restore frozen bids (HELD -> PENDING)
            Bid.objects.filter(campaign=campaign, status=BidStatus.HELD).update(
                status=BidStatus.PENDING, updated_at=timezone.now()
            )

            audit = record_audit(
                admin=request.user,
                action="campaign.restore",
                target=campaign,
                detail={"brandId": str(campaign.brand_id), "reason": reason},
            )

            if campaign.brand.owner_id:
                notify(
                    user_id=campaign.brand.owner_id,
                    type="campaign_restored",
                    title="Campaign Restored",
                    body=f"Your campaign '{campaign.title}' has been restored and is now live.",
                    data={"campaignId": str(campaign.id)},
                )

            return Response(
                {
                    "campaign": AdminCampaignDetailOutput(campaign).data,
                    "audit": audit,
                }
            )
